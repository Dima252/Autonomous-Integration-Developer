"""The validator's own eval: seeded bugs it must catch (corpus/validation/, ADR 019).

Each case is the reference solution plus a few exact edits, and names the checks that
must fail. A case passes when the failed checks are exactly those: a missed bug and a
false alarm both count against the validator."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from aidev.validation.models import Check, Report


@dataclass(frozen=True)
class Edit:
    path: str
    old: str | None = None  # replaced by `new`; must occur exactly once
    new: str = ""
    append: str | None = None


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    expect: frozenset[Check]
    edits: tuple[Edit, ...] = ()


@dataclass(frozen=True)
class CaseResult:
    case: Case
    report: Report
    duration_s: float

    @property
    def failed(self) -> frozenset[Check]:
        return frozenset(self.report.failed)

    @property
    def ok(self) -> bool:
        return self.failed == self.case.expect


def load_cases(path: Path) -> list[Case]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        Case(
            name=c["name"],
            description=c["description"],
            expect=frozenset(Check(x) for x in c["expect"]),
            edits=tuple(Edit(**e) for e in c.get("edits", [])),
        )
        for c in raw["case"]
    ]


def apply(files: dict[str, str], case: Case) -> dict[str, str]:
    out = dict(files)
    for edit in case.edits:
        content = out.get(edit.path, "")
        if edit.old is not None:
            if content.count(edit.old) != 1:
                raise ValueError(f"{case.name}: {edit.old!r} must occur once in {edit.path}")
            content = content.replace(edit.old, edit.new)
        if edit.append is not None:
            content += edit.append
        out[edit.path] = content
    return out


def format_results(results: list[CaseResult]) -> str:
    def checks(cs: frozenset[Check]) -> str:
        return ", ".join(sorted(cs)) or "-"

    rows = [f"{'case':<24} {'expected':<16} {'failed':<16} {'time':>6}"]
    for r in results:
        mark = "ok " if r.ok else "MISS"
        rows.append(
            f"{r.case.name:<24} {checks(r.case.expect):<16} {checks(r.failed):<16} "
            f"{r.duration_s:5.1f}s  {mark}"
        )
    caught = sum(r.ok for r in results)
    times = sorted(r.duration_s for r in results)
    median = times[len(times) // 2] if times else 0.0
    rows.append(
        f"\n{caught}/{len(results)} cases as expected, median {median:.1f} s per validation"
    )
    return "\n".join(rows)
