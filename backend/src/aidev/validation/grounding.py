"""Finds what code claims about provider APIs, by reading its syntax tree (nothing runs):
raw HTTP calls to a provider's API, and webhook event names (docs/architecture.md §4).

SDK calls aren't covered here: pyright already checks them against the real SDKs.

    httpx.post(f"{SLACK_API_URL}chat.postMessage", ...)   -> POST /chat.postMessage
    BILLING_EVENTS = {"invoice.paid", "invoice.payment_failed"}   -> two Stripe events

URLs are resolved through module-level string constants, including the default of
`os.environ.get("X", "https://...")`, which is how generated code is told to configure
its base URLs. A part that can't be resolved becomes a path parameter placeholder.
"""

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_PLACEHOLDER = "\x00"
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


@dataclass(frozen=True)
class HttpCall:
    method: str
    url: str  # unresolved parts are "\x00"
    line: int


@dataclass(frozen=True)
class EventName:
    name: str
    line: int


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or name.startswith("test_") or name == "conftest.py"


def _env_default(node: ast.Call) -> ast.expr | None:
    """`os.environ.get("X", default)`, `os.getenv("X", default)`."""
    func = node.func
    name = ast.unparse(func) if isinstance(func, ast.Attribute) else ""
    if name in ("os.environ.get", "os.getenv", "environ.get", "getenv") and len(node.args) > 1:
        return node.args[1]
    return None


def _string(node: ast.expr | None, consts: Mapping[str, str]) -> str | None:
    match node:
        case ast.Constant(value=str() as value):
            return value
        case ast.Name(id=name):
            return consts.get(name)
        case ast.JoinedStr(values=parts):
            out = []
            for part in parts:
                if isinstance(part, ast.FormattedValue):
                    out.append(_string(part.value, consts) or _PLACEHOLDER)
                else:
                    out.append(_string(part, consts) or "")
            return "".join(out)
        case ast.BinOp(op=ast.Add(), left=left, right=right):
            a, b = _string(left, consts), _string(right, consts)
            return None if a is None or b is None else a + b
        case ast.BoolOp(op=ast.Or(), values=values):
            return _string(values[-1], consts)
        case ast.Call():
            return _string(_env_default(node), consts)
    return None


def _module_strings(tree: ast.Module) -> dict[str, str]:
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if isinstance(target, ast.Name) and (text := _string(value, consts)) is not None:
            consts[target.id] = text
    return consts


def http_calls(tree: ast.Module) -> list[HttpCall]:
    consts = _module_strings(tree)
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        if attr in HTTP_METHODS:
            method, url_node = attr.upper(), node.args[0] if node.args else kwargs.get("url")
        elif attr == "request" and node.args:
            method = (_string(node.args[0], consts) or "").upper()
            url_node = node.args[1] if len(node.args) > 1 else kwargs.get("url")
        else:
            continue
        url = _string(url_node, consts)
        if method and url and url.startswith("http"):
            calls.append(HttpCall(method, url, node.lineno))
    return calls


def event_names(tree: ast.Module) -> list[EventName]:
    """Dotted lowercase string literals where code names an event: compared with
    something, listed in a collection, used as a dict key or a `case` value. A dotted
    string passed as an argument (a filename, a setting) isn't treated as an event."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            found += [node.left, *node.comparators]
        elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            found += node.elts
        elif isinstance(node, ast.Dict):
            found += [k for k in node.keys if k is not None]
        elif isinstance(node, ast.MatchValue):
            found.append(node.value)
    return [
        EventName(n.value, n.lineno)
        for n in found
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and EVENT_NAME.match(n.value)
    ]


def api_path(url: str, base_urls: tuple[str, ...]) -> str | None:
    """The request path relative to one of the API's base URLs, or None if the URL
    isn't for this API. Placeholders become a single path segment."""
    for base in base_urls:
        if url == base or url.startswith(base.rstrip("/") + "/"):
            path = url[len(base.rstrip("/")) :].split("?", 1)[0].split("#", 1)[0]
            return (path or "/").replace(_PLACEHOLDER, "x")
    return None
