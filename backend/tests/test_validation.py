import io
import json
import re
import tarfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import pytest

from aidev.config import get_settings
from aidev.validation.cases import apply, load_cases
from aidev.validation.checks import (
    CheckContext,
    ground,
    install,
    parse_junit,
    parse_pyright,
    parse_ruff,
    parse_uv,
    requirement_problems,
)
from aidev.validation.models import Check, CheckResult, Failure, Status, trim
from aidev.validation.sandbox import ExecResult, Session
from aidev.validation.specs import MOCKS, ProviderApi, mock_spec, providers_for
from aidev.validation.validator import Validator
from aidev.validation.workspace import Workspace, WorkspaceError

CORPUS = get_settings().corpus_dir / "validation"


class FakeSession:
    def __init__(self, install: ExecResult | None = None) -> None:
        self._install = install or ExecResult(0, "", " + stripe==16.1.0\n", 10)
        self.installs = 0

    async def install(self, timeout_s: float) -> ExecResult:
        self.installs += 1
        return self._install

    async def start_mocks(self, specs: Mapping[str, bytes]) -> dict[str, str]:
        return {}

    async def wait_mocks(self, timeout_s: float) -> None:
        pass

    async def exec(
        self,
        argv: Sequence[str],
        timeout_s: float,
        *,
        mocks: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        return ExecResult(0, "", "", 1)


class FakeSandbox:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.session_obj = session or FakeSession()

    @asynccontextmanager
    async def session(self, workspace: Workspace) -> AsyncIterator[Session]:
        yield self.session_obj


def fixed(check: Check, status: Status):
    async def run(ctx: CheckContext) -> CheckResult:
        return CheckResult(check, status, f"{check} {status}")

    return run


# --- models --------------------------------------------------------------------------


def test_fingerprint_ignores_line_numbers_and_quoted_names() -> None:
    a = Failure(
        Check.PYRIGHT, "reportAttributeAccessIssue", "app/main.py", 'No "foo" on Webhook', 10
    )
    b = Failure(
        Check.PYRIGHT, "reportAttributeAccessIssue", "app/main.py", 'No "bar" on Webhook', 42
    )
    c = Failure(Check.PYRIGHT, "reportAttributeAccessIssue", "app/slack.py", 'No "foo" on Webhook')
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint


def test_trim_keeps_head_and_tail() -> None:
    text = "first error\n" + "x" * 20_000 + "\n1 failed"
    out = trim(text, limit=100)
    assert out.startswith("first error") and out.endswith("1 failed")
    assert "characters trimmed" in out and len(out) < 200


def test_event_data_matches_the_ui_contract() -> None:
    result = CheckResult(Check.PYTEST, Status.FAIL, "4 failed", "trace")
    assert result.event_data(2) == {
        "iteration": 2,
        "check": "pytest",
        "status": "fail",
        "summary": "4 failed",
        "output": "trace",
    }


# --- workspace -----------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape.py", "app/../../x", "", "\\abs.py"])
def test_workspace_rejects_paths_outside_itself(path: str) -> None:
    with pytest.raises(WorkspaceError):
        Workspace({path: "x"})


def test_workspace_normalizes_windows_paths_and_tars_plain_files() -> None:
    ws = Workspace({"app\\main.py": "print(1)\n", "requirements.txt": "stripe\n"})
    assert list(ws.files) == ["app/main.py", "requirements.txt"]
    with tarfile.open(fileobj=io.BytesIO(ws.to_tar())) as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == ["app/main.py", "requirements.txt"]
        assert all(m.isfile() and m.uid == 10001 for m in members)


def test_workspace_has_size_limits() -> None:
    with pytest.raises(WorkspaceError, match="files"):
        Workspace({f"f{i}.py": "" for i in range(201)})


# --- install -------------------------------------------------------------------------


def test_requirements_allow_only_named_pypi_packages() -> None:
    text = (
        "# deps\nstripe==16.1.0\nslack-sdk[optional]>=3 ; python_version >= '3.12'\n\n"
        "--extra-index-url https://evil.example/simple\n"
        "evil @ https://evil.example/evil-1.0-py3-none-any.whl\n"
        "-e .\nnot a requirement!!\n"
    )
    problems = requirement_problems(text)
    assert [p.line for p in problems] == [5, 6, 7, 8]
    assert all(p.kind == "disallowed" for p in problems)


# uv 0.13's messages, captured from the sandbox.
UV_ERROR = (
    "error: No solution found when resolving dependencies\n  cause: Because {}, we can conclude"
)
UV_NOT_FOUND = UV_ERROR.format(
    "stripe-billing-webhooks-helper was not found in the package registry and you require "
    "stripe-billing-webhooks-helper==2.1.0"
)
UV_NO_VERSION = UV_ERROR.format(
    "there is no version of stripe==99.0.0 and you require stripe==99.0.0"
)
UV_NO_WHEEL = UV_ERROR.format("pyyaml==5.3 has no usable wheels and you require pyyaml==5.3")


@pytest.mark.parametrize(
    ("stderr", "kind", "message"),
    [
        (UV_NOT_FOUND, "not_found", "stripe-billing-webhooks-helper doesn't exist on PyPI"),
        (UV_NO_VERSION, "no_version", "no release matches stripe==99.0.0"),
        (UV_NO_WHEEL, "no_wheel", "pyyaml==5.3 has no wheel for Linux"),
        ("error: network unreachable\n", "install_error", "network unreachable"),
    ],
)
def test_parse_uv_names_the_package(stderr: str, kind: str, message: str) -> None:
    [failure] = parse_uv(stderr)
    assert failure.kind == kind
    assert failure.message.startswith(message)


async def test_install_rejects_bad_requirements_without_running_anything() -> None:
    session = FakeSession()
    ws = Workspace({"requirements.txt": "--index-url https://evil.example/simple\n"})
    result = await install(CheckContext(session, ws))
    assert result.status is Status.FAIL
    assert session.installs == 0


async def test_install_needs_a_requirements_file() -> None:
    result = await install(CheckContext(FakeSession(), Workspace({"app.py": ""})))
    assert result.status is Status.FAIL and result.summary == "no requirements.txt"


async def test_install_reports_a_timeout() -> None:
    session = FakeSession(ExecResult(-1, "", "", 180_000, timed_out=True))
    result = await install(CheckContext(session, Workspace({"requirements.txt": "stripe\n"})))
    assert result.status is Status.FAIL
    assert result.failures[0].kind == "timeout"


# --- ruff and pyright ----------------------------------------------------------------


def test_parse_ruff() -> None:
    out = json.dumps(
        [
            {
                "code": "F821",
                "message": "Undefined name `seen`",
                "filename": "/work/app/main.py",
                "location": {"row": 34, "column": 9},
            },
            {
                "code": None,
                "message": "SyntaxError: Expected ':'",
                "filename": "/work/app/slack.py",
                "location": {"row": 3, "column": 1},
            },
        ]
    )
    a, b = parse_ruff(out)
    assert (a.kind, a.location, a.line) == ("F821", "app/main.py", 34)
    assert b.kind == "syntax"


def test_parse_pyright_counts_errors_only() -> None:
    out = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": "/work/app/main.py",
                    "severity": "error",
                    "message": 'Cannot access attribute "verify"\n  Attribute is unknown',
                    "rule": "reportAttributeAccessIssue",
                    "range": {"start": {"line": 32, "character": 4}},
                },
                {
                    "file": "/work/app/main.py",
                    "severity": "warning",
                    "message": "Unused",
                    "range": {"start": {"line": 1, "character": 0}},
                },
            ]
        }
    )
    [f] = parse_pyright(out)
    assert (f.kind, f.location, f.line, f.message) == (
        "reportAttributeAccessIssue",
        "app/main.py",
        33,
        'Cannot access attribute "verify"',
    )


# --- validator -----------------------------------------------------------------------


async def test_a_failed_stage_skips_the_later_ones_but_not_its_siblings() -> None:
    checks = {
        Check.INSTALL: fixed(Check.INSTALL, Status.PASS),
        Check.RUFF: fixed(Check.RUFF, Status.FAIL),
        Check.PYRIGHT: fixed(Check.PYRIGHT, Status.PASS),
        Check.PYTEST: fixed(Check.PYTEST, Status.PASS),
    }
    stages = [(Check.INSTALL,), (Check.RUFF, Check.PYRIGHT), (Check.PYTEST,)]
    seen: list[CheckResult] = []

    async def on_result(r: CheckResult) -> None:
        seen.append(r)

    report = await Validator(FakeSandbox(), checks=checks, stages=stages).validate(
        Workspace({"requirements.txt": ""}), on_result
    )
    assert [(r.check, r.status) for r in report.results] == [
        (Check.INSTALL, Status.PASS),
        (Check.RUFF, Status.FAIL),
        (Check.PYRIGHT, Status.PASS),
        (Check.PYTEST, Status.SKIP),
    ]
    assert report.results[-1].summary == "skipped: ruff failed"
    assert seen == list(report.results)
    assert (report.passed, report.score, report.failed) == (False, 2, (Check.RUFF,))


# --- the seeded-bug cases ------------------------------------------------------------


def test_every_case_edit_applies_to_the_reference_solution() -> None:
    base = Workspace.from_dir(CORPUS / "base").files
    cases = load_cases(CORPUS / "cases.toml")
    assert len({c.name for c in cases}) == len(cases)
    assert [c.name for c in cases if not c.expect] == ["good"]
    for case in cases:
        edited = apply(dict(base), case)
        assert (edited == base) == (not case.edits), case.name


# --- grounding -----------------------------------------------------------------------

STRIPE = ProviderApi(
    "stripe",
    ("https://api.stripe.com",),
    (("GET", re.compile(r"^/v1/customers/[^/]+/?$")),),
    frozenset({"invoice.paid", "invoice.payment_failed", "customer.created"}),
)
SLACK = ProviderApi(
    "slack", ("https://slack.com/api",), (("POST", re.compile(r"^/chat\.postMessage/?$")),)
)
APP = """
import os
import httpx

SLACK_API_URL = os.environ.get("SLACK_API_URL", "https://slack.com/api/")
BASE: str = os.getenv("STRIPE_API_BASE") or "https://api.stripe.com"
EVENTS = {"invoice.paid", "invoice.payment_faild"}

def run(customer: str, kind: str) -> None:
    httpx.post(f"{SLACK_API_URL}chat.postMessage", data={})
    httpx.post(SLACK_API_URL + "chat.sendAlert")
    httpx.get(f"{BASE}/v1/customers/{customer}?expand=x")
    httpx.request("DELETE", f"{BASE}/v1/customers/{customer}")
    httpx.get(f"{os.environ['HOST']}/anything")  # unresolvable: not checked
    open("invoice.pdf")  # a dotted argument isn't an event
    if kind == "customer.created" or kind == "customer.deleted_soon":
        pass
"""


def test_grounding_resolves_urls_and_flags_what_the_catalog_lacks() -> None:
    ws = Workspace({"app/main.py": APP, "tests/test_x.py": 'x = {"invoice.nope"}'})
    failures, checked = ground(ws, {"stripe": STRIPE, "slack": SLACK})
    assert checked == 8
    assert sorted((f.kind, f.message, f.line) for f in failures) == [
        ("unknown_event", "customer.deleted_soon isn't a Stripe event", 16),
        ("unknown_event", "invoice.payment_faild isn't a Stripe event", 7),
        ("unknown_operation", "DELETE /v1/customers/x isn't in the Stripe API", 13),
        ("unknown_operation", "POST /chat.sendAlert isn't in the Slack API", 11),
    ]


def test_grounding_skips_code_that_does_not_parse() -> None:
    failures, checked = ground(Workspace({"app.py": "def broken(:"}), {"stripe": STRIPE})
    assert (failures, checked) == ([], 0)


# --- pytest --------------------------------------------------------------------------

VIOLATION = "Failed: the slack API spec doesn't allow POST /chat.postMessage: body: needs channel"
JUNIT = f"""<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
<testcase classname="tests.test_webhook" name="test_ok" />
<testcase classname="tests.test_webhook" name="test_alert">
  <failure message="httpx.HTTPStatusError: 422 Unprocessable Entity">trace</failure>
</testcase>
<testcase classname="tests.test_webhook" name="test_alert">
  <error message="failed on teardown with &quot;{VIOLATION}&quot;">x</error>
</testcase>
<testcase classname="tests.test_webhook" name="test_dedupe">
  <failure message="assert 2 == 1">trace</failure>
</testcase>
</testsuite></testsuites>"""


def test_junit_counts_each_test_once_and_prefers_the_contract_message() -> None:
    total, failures = parse_junit(JUNIT)
    assert total == 3
    assert [(f.kind, f.location, f.message) for f in failures] == [
        (
            "api_contract",
            "tests/test_webhook.py",
            "test_alert: Failed: the slack API spec doesn't allow POST /chat.postMessage: "
            "body: needs channel",
        ),
        ("test_failure", "tests/test_webhook.py", "test_dedupe: assert 2 == 1"),
    ]


# --- specs and mocks -----------------------------------------------------------------


def test_providers_come_from_requirements_and_api_hosts() -> None:
    ws = Workspace(
        {
            "requirements.txt": "Stripe==16.1.0\nslack_sdk>=3\n",
            "app.py": 'URL = "https://api.github.com/repos"',
        }
    )
    assert providers_for(ws) == ["stripe", "slack", "github"]
    assert providers_for(Workspace({"requirements.txt": "fastapi\n"})) == []


def test_mock_spec_keeps_requests_and_drops_response_schemas() -> None:
    oas3 = {
        "openapi": "3.0.0",
        "paths": {
            "/v1/customers": {
                "post": {
                    "requestBody": {"content": {"application/x-www-form-urlencoded": {}}},
                    "responses": {"200": {"$ref": "#/components/responses/Customer"}},
                }
            }
        },
        "components": {"schemas": {"customer": {}}, "securitySchemes": {"bearer": {}}},
        "webhooks": {"x": {}},
    }
    out = mock_spec(oas3, MOCKS["stripe"])
    op = out["paths"]["/v1/customers"]["post"]
    assert "requestBody" in op
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {"type": "object"}
    assert out["components"] == {"securitySchemes": {"bearer": {}}}
    assert "webhooks" not in out and "schemas" in oas3["components"]  # the input is untouched


def test_slack_mock_spec_drops_the_token_header_and_answers_ok() -> None:
    swagger = {
        "swagger": "2.0",
        "paths": {
            "/chat.postMessage": {
                "post": {
                    "parameters": [
                        {"name": "token", "in": "header", "required": True},
                        {"name": "channel", "in": "formData", "required": True},
                    ],
                    "responses": {"200": {"schema": {"$ref": "#/definitions/x"}}},
                }
            }
        },
        "definitions": {"x": {}},
    }
    op = mock_spec(swagger, MOCKS["slack"])["paths"]["/chat.postMessage"]["post"]
    assert [p["name"] for p in op["parameters"]] == ["channel"]
    assert op["responses"]["200"]["examples"] == {"application/json": {"ok": True}}
