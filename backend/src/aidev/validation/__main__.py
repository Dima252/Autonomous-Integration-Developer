"""Validation CLI (needs Docker).

uv run python -m aidev.validation build-image   # build the sandbox image (infra/sandbox/)
uv run python -m aidev.validation run <dir>     # validate a folder of code
uv run python -m aidev.validation eval          # the seeded-bug cases (corpus/validation/)
uv run python -m aidev.validation prune         # remove what an interrupted run left behind
"""

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

from aidev.config import Settings, get_settings
from aidev.validation.cases import CaseResult, apply, format_results, load_cases
from aidev.validation.models import CheckResult, Status
from aidev.validation.sandbox import DockerSandbox, prune
from aidev.validation.specs import SpecLibrary
from aidev.validation.validator import Validator
from aidev.validation.workspace import Workspace

MARK = {Status.PASS: "pass", Status.FAIL: "FAIL", Status.SKIP: "skip"}


async def _print(result: CheckResult) -> None:
    seconds = result.duration_ms / 1000
    print(f"  {MARK[result.status]}  {result.check:<9} {seconds:5.1f}s  {result.summary}")


def _build(settings: Settings) -> int:
    cmd = ["docker", "build", "-t", settings.sandbox_image, str(settings.sandbox_dir)]
    return subprocess.run(cmd, check=False).returncode


def _validator(settings: Settings) -> Validator:
    return Validator(
        DockerSandbox(settings.sandbox_image), SpecLibrary(settings.data_dir / "specs")
    )


async def _run(settings: Settings, path: Path, verbose: bool) -> int:
    validator = _validator(settings)
    report = await validator.validate(Workspace.from_dir(path), _print)
    if verbose:
        for r in report.results:
            if r.status is Status.FAIL and r.output:
                print(f"\n--- {r.check} ---\n{r.output}")
    print(
        f"\n{'passed' if report.passed else 'failed'}: {report.score}/{len(report.results)} checks"
    )
    return 0 if report.passed else 1


async def _eval(settings: Settings, only: str | None) -> int:
    root = settings.corpus_dir / "validation"
    base = Workspace.from_dir(root / "base").files
    cases = [c for c in load_cases(root / "cases.toml") if only in (None, c.name)]
    validator = _validator(settings)
    results = []
    for case in cases:
        print(f"{case.name}: {case.description}")
        start = time.perf_counter()
        report = await validator.validate(Workspace(apply(dict(base), case)), _print)
        results.append(CaseResult(case, report, time.perf_counter() - start))
    print("\n" + format_results(results))
    return 0 if all(r.ok for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m aidev.validation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-image", help="build the sandbox image")
    run = sub.add_parser("run", help="validate a folder of code")
    run.add_argument("path", type=Path)
    run.add_argument("-v", "--verbose", action="store_true", help="print failing checks' output")
    ev = sub.add_parser("eval", help="run the seeded-bug cases")
    ev.add_argument("--case", help="only this case")
    sub.add_parser("prune", help="remove leftover sandbox containers and volumes")
    args = parser.parse_args()

    settings = get_settings()
    if args.cmd == "build-image":
        sys.exit(_build(settings))
    if args.cmd == "run":
        sys.exit(asyncio.run(_run(settings, args.path, args.verbose)))
    if args.cmd == "eval":
        sys.exit(asyncio.run(_eval(settings, args.case)))
    if args.cmd == "prune":
        print(f"removed {asyncio.run(prune())} containers and volumes")


if __name__ == "__main__":
    main()
