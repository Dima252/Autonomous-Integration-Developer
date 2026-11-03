import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Check(StrEnum):
    """The validator's checks, in order. The values are the `check` field of
    `check.result` events (docs/ui-events.md)."""

    INSTALL = "install"
    RUFF = "ruff"
    PYRIGHT = "pyright"
    GROUNDING = "grounding"
    PYTEST = "pytest"
    SECURITY = "security"
    REVIEW = "review"


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


_VOLATILE = re.compile(r"\d+|'[^']*'|\"[^\"]*\"")


@dataclass(frozen=True)
class Failure:
    """One problem a check found. `location` is a path (no line number: an edit
    elsewhere in the file mustn't change the fingerprint)."""

    check: Check
    kind: str  # rule or error class: F821, reportAttributeAccessIssue, unresolvable
    location: str
    message: str
    line: int | None = None

    @property
    def fingerprint(self) -> str:
        """Stable across iterations for the same underlying failure, so the fix loop
        can tell "this failed again" from "this is new" (agent-architecture.md §3.7).
        Numbers and quoted names are masked: they drift between attempts."""
        message = _VOLATILE.sub("_", self.message)
        key = f"{self.check}|{self.kind}|{self.location}|{message}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class CheckResult:
    check: Check
    status: Status
    summary: str  # one line for the checks matrix
    output: str = ""  # the tool's output, trimmed (see trim())
    failures: tuple[Failure, ...] = ()
    duration_ms: float = 0.0

    def event_data(self, iteration: int) -> dict[str, Any]:
        """The payload of a `check.result` event."""
        data: dict[str, Any] = {
            "iteration": iteration,
            "check": self.check.value,
            "status": self.status.value,
            "summary": self.summary,
        }
        if self.output:
            data["output"] = self.output
        return data


@dataclass(frozen=True)
class Report:
    results: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(r.status is Status.PASS for r in self.results)

    @property
    def score(self) -> int:
        """Checks passed: the fix loop keeps the best-scoring iteration."""
        return sum(r.status is Status.PASS for r in self.results)

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(r.check for r in self.results if r.status is Status.FAIL)

    @property
    def failures(self) -> tuple[Failure, ...]:
        return tuple(f for r in self.results for f in r.failures)

    def get(self, check: Check) -> CheckResult | None:
        return next((r for r in self.results if r.check is check), None)


OUTPUT_LIMIT = 8_000


def trim(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """Keep the head and the tail: tools put the first error first and the summary last."""
    text = text.strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n… {dropped} characters trimmed …\n{text[-half:]}"
