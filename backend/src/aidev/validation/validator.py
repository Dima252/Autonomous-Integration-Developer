import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace

from aidev.validation.checks import CHECKS, CheckFn, Timeouts
from aidev.validation.models import Check, CheckResult, Report, Status
from aidev.validation.sandbox import Sandbox, Session
from aidev.validation.workspace import Workspace

# A stage runs only when every check before it passed. The checks inside a stage run
# together, so one iteration reports every static problem at once (ADR 019).
STAGES: tuple[tuple[Check, ...], ...] = (
    (Check.INSTALL,),
    (Check.RUFF, Check.PYRIGHT),
)

OnResult = Callable[[CheckResult], Awaitable[None]]


class Validator:
    def __init__(
        self,
        sandbox: Sandbox,
        timeouts: Timeouts | None = None,
        checks: Mapping[Check, CheckFn] | None = None,
        stages: Sequence[Sequence[Check]] = STAGES,
    ) -> None:
        self._sandbox = sandbox
        self._timeouts = timeouts or Timeouts()
        self._checks = checks or CHECKS
        self._stages = stages

    async def validate(self, workspace: Workspace, on_result: OnResult | None = None) -> Report:
        """Run every stage, reporting each result as it lands (`check.result` events)."""
        results: list[CheckResult] = []

        async def emit(result: CheckResult) -> None:
            results.append(result)
            if on_result:
                await on_result(result)

        async with self._sandbox.session(workspace) as session:
            blocked_by: Check | None = None
            for stage in self._stages:
                if blocked_by:
                    for check in stage:
                        await emit(CheckResult(check, Status.SKIP, f"skipped: {blocked_by} failed"))
                    continue
                done = await asyncio.gather(*(self._run(c, session, workspace) for c in stage))
                for result in done:
                    await emit(result)
                blocked_by = next((r.check for r in done if r.status is Status.FAIL), None)
        return Report(tuple(results))

    async def _run(self, check: Check, session: Session, workspace: Workspace) -> CheckResult:
        start = time.perf_counter()
        result = await self._checks[check](session, workspace, self._timeouts)
        return replace(result, duration_ms=(time.perf_counter() - start) * 1000)
