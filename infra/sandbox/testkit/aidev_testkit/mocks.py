"""A local proxy in front of each provider's Prism mock.

Every request is forwarded to Prism, which checks it against the provider's spec. When
Prism rejects it, the rejection goes back to the code and is recorded as a violation,
which fails the test. When Prism accepts it, the proxy answers with the response the
test set for that call, or with Prism's default. Every call is recorded."""

import fnmatch
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PRISM_ERROR = "https://stoplight.io/prism/errors#"
_HOP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    status: int = 0

    @property
    def json(self) -> Any:
        return json.loads(self.body or b"null")

    @property
    def form(self) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(self.body.decode(), keep_blank_values=True))

    @property
    def data(self) -> dict[str, Any]:
        """The body as a dict, whether it was sent as JSON or as a form."""
        if "json" in self.headers.get("content-type", ""):
            body = self.json
            return body if isinstance(body, dict) else {}
        return self.form


@dataclass
class MockApi:
    name: str
    upstream: str  # the Prism mock
    form_only: bool = False
    calls: list[Call] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    _responses: dict[tuple[str, str], tuple[int, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def respond(self, method: str, path: str, body: Any = None, status: int = 200) -> None:
        """Answer `method path` (a glob: `/v1/customers/*`) with `body` as JSON."""
        self._responses[(method.upper(), path)] = (status, {} if body is None else body)

    def calls_to(self, method: str, path: str) -> list[Call]:
        return [
            c for c in self.calls if c.method == method.upper() and fnmatch.fnmatch(c.path, path)
        ]

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.violations.clear()
            self._responses.clear()

    def _canned(self, method: str, path: str) -> tuple[int, Any] | None:
        exact = self._responses.get((method, path))
        if exact is not None:
            return exact
        for (m, pattern), answer in self._responses.items():
            if m == method and fnmatch.fnmatch(path, pattern):
                return answer
        return None

    def _validate(self, method: str, target: str, headers: dict[str, str], body: bytes):
        """Forward to Prism: (status, headers, body)."""
        if self.form_only and body and "json" in headers.get("content-type", ""):
            # The spec only describes form bodies; the provider accepts both.
            fields = {
                k: v if isinstance(v, str) else json.dumps(v)
                for k, v in json.loads(body).items()
            }
            body = urllib.parse.urlencode(fields).encode()
            headers = {**headers, "content-type": "application/x-www-form-urlencoded"}
        req = urllib.request.Request(
            self.upstream + target, data=body or None, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def handle(self, method: str, target: str, headers: dict[str, str], body: bytes):
        parsed = urllib.parse.urlsplit(target)
        forward = {k: v for k, v in headers.items() if k.lower() not in _HOP_HEADERS}
        status, resp_headers, resp_body = self._validate(method, target, forward, body)
        rejection = _prism_rejection(resp_body)
        if rejection:
            with self._lock:
                self.violations.append(
                    f"the {self.name} API spec doesn't allow {method} {parsed.path}: {rejection}"
                )
        elif canned := self._canned(method, parsed.path):
            status, answer = canned
            resp_body = json.dumps(answer).encode()
            resp_headers = {"content-type": "application/json"}
        with self._lock:
            self.calls.append(
                Call(
                    method,
                    parsed.path,
                    urllib.parse.parse_qs(parsed.query),
                    {k.lower(): v for k, v in headers.items()},
                    body,
                    status,
                )
            )
        return status, resp_headers, resp_body


def _prism_rejection(body: bytes) -> str | None:
    try:
        err = json.loads(body)
    except ValueError:
        return None
    if not isinstance(err, dict) or not str(err.get("type", "")).startswith(PRISM_ERROR):
        return None
    problems = [
        f"{'.'.join(map(str, v.get('location', [])))}: {v.get('message', '')}"
        for v in err.get("validation", [])
    ]
    return "; ".join(problems) or err.get("detail") or err.get("title") or "rejected"


class MockApis:
    """`mock_api.stripe`, `mock_api.slack`: the providers this validation mocks."""

    def __init__(self, apis: dict[str, MockApi]) -> None:
        self._apis = apis

    def __getattr__(self, name: str) -> MockApi:
        try:
            return self._apis[name]
        except KeyError:
            raise AttributeError(
                f"no {name} mock in this validation (mocked: {', '.join(self._apis) or 'none'})"
            ) from None

    def all(self) -> list[MockApi]:
        return list(self._apis.values())


def serve(api: MockApi) -> ThreadingHTTPServer:
    """Start the proxy on a free local port."""

    class Handler(BaseHTTPRequestHandler):
        def _any(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            status, headers, out = api.handle(self.command, self.path, dict(self.headers), body)
            self.send_response(status)
            self.send_header("content-type", headers.get("content-type", "application/json"))
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _any

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
