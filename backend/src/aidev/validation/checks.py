"""The checks: each runs one tool in the sandbox (or, for grounding, reads the code) and
turns the outcome into a `CheckResult` with one `Failure` per problem. The tools'
configuration lives in the image (infra/sandbox/), outside the workspace, so the code
under validation can't loosen its own rules."""

import ast
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from aidev.validation import grounding as g
from aidev.validation.models import Check, CheckResult, Failure, Status, trim
from aidev.validation.sandbox import ExecResult, Session
from aidev.validation.specs import ProviderApi
from aidev.validation.workspace import Workspace

WORK = "/work/"
PROVIDER_NAMES = {"github": "GitHub"}


@dataclass(frozen=True)
class Timeouts:
    install: float = 180
    ruff: float = 60
    pyright: float = 180
    mocks: float = 90  # Prism starting, counted from the start of the validation
    pytest: float = 180


@dataclass(frozen=True)
class CheckContext:
    session: Session
    workspace: Workspace
    timeouts: Timeouts = field(default_factory=Timeouts)
    apis: Mapping[str, ProviderApi] = field(default_factory=dict)  # for grounding
    mocks: Mapping[str, dict[str, Any]] = field(default_factory=dict)  # AIDEV_MOCKS


CheckFn = Callable[[CheckContext], Awaitable[CheckResult]]


def _rel(path: str) -> str:
    return path.removeprefix(WORK)


def _name(provider: str) -> str:
    return PROVIDER_NAMES.get(provider, provider.capitalize())


def _result(check: Check, failures: list[Failure], output: str, ok: str) -> CheckResult:
    if not failures:
        return CheckResult(check, Status.PASS, ok, trim(output))
    first = failures[0]
    where = f" ({first.location}{f':{first.line}' if first.line else ''})"
    more = f" (+{len(failures) - 1} more)" if len(failures) > 1 else ""
    return CheckResult(
        check, Status.FAIL, f"{first.message}{where}{more}", trim(output), tuple(failures)
    )


def _timed_out(check: Check, r: ExecResult, timeout_s: float) -> CheckResult:
    failure = Failure(check, "timeout", "", f"timed out after {timeout_s:.0f} s")
    return CheckResult(check, Status.FAIL, failure.message, trim(r.stdout + r.stderr), (failure,))


# --- install -------------------------------------------------------------------------


def requirement_problems(text: str) -> list[Failure]:
    """Only named packages from PyPI: no pip options (an `--index-url` line would
    change where packages come from) and no direct URLs or paths."""
    problems: list[Failure] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        message = None
        if line.startswith("-"):
            message = f"pip options aren't allowed: {line}"
        else:
            try:
                if Requirement(line).url:
                    message = f"only packages from PyPI are allowed: {line}"
            except InvalidRequirement:
                message = f"not a valid requirement: {line}"
        if message:
            problems.append(Failure(Check.INSTALL, "disallowed", "requirements.txt", message, n))
    return problems


_UV_CAUSES = [
    (
        re.compile(r"Because (\S+) was not found in the package registry"),
        "not_found",
        "{} doesn't exist on PyPI",
    ),
    (re.compile(r"there is no version of (\S+?) and"), "no_version", "no release matches {}"),
    (
        re.compile(r"Because (\S+) has no usable wheels"),
        "no_wheel",
        "{} has no wheel for Linux, and source builds are disabled",
    ),
]


def parse_uv(stderr: str) -> list[Failure]:
    failures = []
    for pattern, kind, template in _UV_CAUSES:
        for m in pattern.finditer(stderr):
            failures.append(
                Failure(Check.INSTALL, kind, "requirements.txt", template.format(m.group(1)))
            )
    if not failures:
        errors = [ln for ln in stderr.splitlines() if ln.startswith("error:")]
        message = errors[0].removeprefix("error:").strip() if errors else "install failed"
        failures.append(Failure(Check.INSTALL, "install_error", "requirements.txt", message))
    return failures


async def install(ctx: CheckContext) -> CheckResult:
    if ctx.workspace.requirements is None:
        failure = Failure(Check.INSTALL, "missing", "requirements.txt", "no requirements.txt")
        return CheckResult(Check.INSTALL, Status.FAIL, failure.message, failures=(failure,))
    if problems := requirement_problems(ctx.workspace.requirements):
        return _result(Check.INSTALL, problems, "", "")
    r = await ctx.session.install(ctx.timeouts.install)
    if r.timed_out:
        return _timed_out(Check.INSTALL, r, ctx.timeouts.install)
    if r.exit_code != 0:
        return _result(Check.INSTALL, parse_uv(r.stderr), r.stderr, "")
    installed = sum(ln.startswith(" + ") for ln in r.stderr.splitlines())
    return _result(Check.INSTALL, [], r.stderr, f"{installed} packages installed")


# --- ruff ----------------------------------------------------------------------------


def parse_ruff(stdout: str) -> list[Failure]:
    return [
        Failure(
            Check.RUFF,
            item.get("code") or "syntax",
            _rel(item["filename"]),
            item["message"],
            item["location"]["row"],
        )
        for item in json.loads(stdout or "[]")
    ]


async def ruff(ctx: CheckContext) -> CheckResult:
    argv = [
        "ruff", "check", "--no-cache", "--config", "/etc/aidev/ruff.toml",
        "--output-format", "json", ".",
    ]  # fmt: skip
    r = await ctx.session.exec(argv, ctx.timeouts.ruff)
    if r.timed_out:
        return _timed_out(Check.RUFF, r, ctx.timeouts.ruff)
    try:
        failures = parse_ruff(r.stdout)
    except (json.JSONDecodeError, KeyError, TypeError):
        failures = [Failure(Check.RUFF, "tool_error", "", "ruff failed to run")]
    output = "\n".join(f"{f.location}:{f.line} {f.kind} {f.message}" for f in failures)
    return _result(Check.RUFF, failures, output or r.stderr, "no problems")


# --- pyright -------------------------------------------------------------------------


def parse_pyright(stdout: str) -> list[Failure]:
    """Errors only: warnings don't fail a validation."""
    report = json.loads(stdout)
    return [
        Failure(
            Check.PYRIGHT,
            d.get("rule") or "error",
            _rel(d.get("file", "")),
            d["message"].splitlines()[0],
            d["range"]["start"]["line"] + 1 if "range" in d else None,
        )
        for d in report.get("generalDiagnostics", [])
        if d.get("severity") == "error"
    ]


async def pyright(ctx: CheckContext) -> CheckResult:
    argv = ["pyright", "--outputjson", "-p", "/etc/aidev/pyrightconfig.json", "/work"]
    r = await ctx.session.exec(argv, ctx.timeouts.pyright)
    if r.timed_out:
        return _timed_out(Check.PYRIGHT, r, ctx.timeouts.pyright)
    try:
        failures = parse_pyright(r.stdout)
    except (json.JSONDecodeError, KeyError, TypeError):
        failures = [Failure(Check.PYRIGHT, "tool_error", "", "pyright failed to run")]
        return _result(Check.PYRIGHT, failures, r.stdout + r.stderr, "")
    output = "\n".join(f"{f.location}:{f.line} error: {f.message} [{f.kind}]" for f in failures)
    return _result(Check.PYRIGHT, failures, output, "no type errors")


# --- grounding -----------------------------------------------------------------------


def ground(workspace: Workspace, apis: Mapping[str, ProviderApi]) -> tuple[list[Failure], int]:
    """Failures, and how many API calls and event names were checked."""
    failures: list[Failure] = []
    checked = 0
    for path, source in workspace.files.items():
        if not path.endswith(".py") or g.is_test_file(path):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # ruff reports it
        for call in g.http_calls(tree):
            for api in apis.values():
                if (api_path := g.api_path(call.url, api.base_urls)) is None:
                    continue
                checked += 1
                if not api.has_operation(call.method, api_path):
                    failures.append(
                        Failure(
                            Check.GROUNDING,
                            "unknown_operation",
                            path,
                            f"{call.method} {api_path} isn't in the {_name(api.provider)} API",
                            call.line,
                        )
                    )
        for event in g.event_names(tree):
            for api in apis.values():
                if event.name.split(".", 1)[0] not in api.event_prefixes:
                    continue
                checked += 1
                if event.name not in api.events:
                    failures.append(
                        Failure(
                            Check.GROUNDING,
                            "unknown_event",
                            path,
                            f"{event.name} isn't a {_name(api.provider)} event",
                            event.line,
                        )
                    )
    return failures, checked


async def grounding(ctx: CheckContext) -> CheckResult:
    failures, checked = ground(ctx.workspace, ctx.apis)
    output = "\n".join(f"{f.location}:{f.line} {f.message}" for f in failures)
    ok = (
        f"{checked} API calls and event names match the catalog"
        if checked
        else "no raw API calls or event names to check"
    )
    return _result(Check.GROUNDING, failures, output, ok)


# --- pytest --------------------------------------------------------------------------


CONTRACT_VIOLATION = "API spec doesn't allow"  # aidev_testkit's message


def parse_junit(xml: str) -> tuple[int, list[Failure]]:
    """Tests run, and one failure per failed or errored test. A test that fails and
    then breaks the API contract at teardown appears twice in the report: it counts
    once, under the contract message, which names the cause rather than its effect."""
    root = ET.fromstring(xml)
    tests: dict[tuple[str, str], list[ET.Element]] = {}
    for case in root.iter("testcase"):
        key = (case.get("classname", ""), case.get("name", ""))
        problems = tests.setdefault(key, [])
        problems += [p for p in case if p.tag in ("failure", "error")]
    failures = []
    for (classname, name), problems in tests.items():
        if not problems:
            continue
        texts = [(p.get("message") or p.text or "failed").strip() for p in problems]
        pick = next((i for i, t in enumerate(texts) if CONTRACT_VIOLATION in t), 0)
        text = texts[pick]
        if text.startswith(teardown := 'failed on teardown with "'):
            text = text.removeprefix(teardown).removesuffix('"')
        line = text.splitlines()[0]
        kind = "api_contract" if CONTRACT_VIOLATION in line else f"test_{problems[pick].tag}"
        path = classname.replace(".", "/") + ".py" if classname else ""
        failures.append(Failure(Check.PYTEST, kind, path, f"{name}: {line}"))
    return len(tests), failures


async def pytest(ctx: CheckContext) -> CheckResult:
    if not any(g.is_test_file(p) and p.endswith(".py") for p in ctx.workspace.files):
        failure = Failure(Check.PYTEST, "missing", "tests/", "no tests")
        return CheckResult(Check.PYTEST, Status.FAIL, failure.message, failures=(failure,))
    if ctx.mocks:
        await ctx.session.wait_mocks(ctx.timeouts.mocks)
    # The JUnit report goes to stderr, apart from the terminal output; a run that
    # exits early (os._exit) writes none, so it can't pass.
    argv = [
        "pytest", "-c", "/etc/aidev/pytest.ini", "--rootdir", "/work",
        "-q", "--tb=short", "-o", "junit_family=xunit2", "--junitxml=/dev/stderr",
    ]  # fmt: skip
    env = {"AIDEV_MOCKS": json.dumps(ctx.mocks)} if ctx.mocks else None
    r = await ctx.session.exec(argv, ctx.timeouts.pytest, mocks=True, env=env)
    if r.timed_out:
        return _timed_out(Check.PYTEST, r, ctx.timeouts.pytest)
    start = r.stderr.find("<?xml")
    try:
        total, failures = parse_junit(r.stderr[start:]) if start >= 0 else (0, [])
    except ET.ParseError:
        total, failures = 0, []
    if total == 0 or (r.exit_code != 0 and not failures):
        message = "no tests ran" if r.exit_code in (0, 5) else "pytest didn't finish"
        failure = Failure(Check.PYTEST, "no_report", "tests/", message)
        stderr = r.stderr if start < 0 else r.stderr[:start]
        return CheckResult(Check.PYTEST, Status.FAIL, message, trim(r.stdout + stderr), (failure,))
    if not failures:
        return CheckResult(Check.PYTEST, Status.PASS, f"{total} tests passed", trim(r.stdout))
    summary = f"{len(failures)} of {total} tests failed: {failures[0].message}"
    return CheckResult(Check.PYTEST, Status.FAIL, summary, trim(r.stdout), tuple(failures))


CHECKS: dict[Check, CheckFn] = {
    Check.INSTALL: install,
    Check.RUFF: ruff,
    Check.PYRIGHT: pyright,
    Check.GROUNDING: grounding,
    Check.PYTEST: pytest,
}
