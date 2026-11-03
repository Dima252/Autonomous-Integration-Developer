"""The checks: each runs one tool in the sandbox and turns its output into a
`CheckResult` with one `Failure` per problem. The tools' configuration lives in the
image (infra/sandbox/), outside the workspace, so the code under validation can't
loosen its own rules."""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement

from aidev.validation.models import Check, CheckResult, Failure, Status, trim
from aidev.validation.sandbox import ExecResult, Session
from aidev.validation.workspace import Workspace

WORK = "/work/"


@dataclass(frozen=True)
class Timeouts:
    install: float = 180
    ruff: float = 60
    pyright: float = 180


CheckFn = Callable[[Session, Workspace, Timeouts], Awaitable[CheckResult]]


def _rel(path: str) -> str:
    return path.removeprefix(WORK)


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


async def install(session: Session, workspace: Workspace, timeouts: Timeouts) -> CheckResult:
    if workspace.requirements is None:
        failure = Failure(Check.INSTALL, "missing", "requirements.txt", "no requirements.txt")
        return CheckResult(Check.INSTALL, Status.FAIL, failure.message, failures=(failure,))
    if problems := requirement_problems(workspace.requirements):
        return _result(Check.INSTALL, problems, "", "")
    r = await session.install(timeouts.install)
    if r.timed_out:
        return _timed_out(Check.INSTALL, r, timeouts.install)
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


async def ruff(session: Session, workspace: Workspace, timeouts: Timeouts) -> CheckResult:
    argv = [
        "ruff", "check", "--no-cache", "--config", "/etc/aidev/ruff.toml",
        "--output-format", "json", ".",
    ]  # fmt: skip
    r = await session.exec(argv, timeouts.ruff)
    if r.timed_out:
        return _timed_out(Check.RUFF, r, timeouts.ruff)
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


async def pyright(session: Session, workspace: Workspace, timeouts: Timeouts) -> CheckResult:
    argv = ["pyright", "--outputjson", "-p", "/etc/aidev/pyrightconfig.json", "/work"]
    r = await session.exec(argv, timeouts.pyright)
    if r.timed_out:
        return _timed_out(Check.PYRIGHT, r, timeouts.pyright)
    try:
        failures = parse_pyright(r.stdout)
    except (json.JSONDecodeError, KeyError, TypeError):
        failures = [Failure(Check.PYRIGHT, "tool_error", "", "pyright failed to run")]
        return _result(Check.PYRIGHT, failures, r.stdout + r.stderr, "")
    output = "\n".join(f"{f.location}:{f.line} error: {f.message} [{f.kind}]" for f in failures)
    return _result(Check.PYRIGHT, failures, output, "no type errors")


CHECKS: dict[Check, CheckFn] = {
    Check.INSTALL: install,
    Check.RUFF: ruff,
    Check.PYRIGHT: pyright,
}
