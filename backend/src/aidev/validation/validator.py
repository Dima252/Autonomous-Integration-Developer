import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from aidev.validation.checks import CHECKS, CheckContext, CheckFn, Timeouts
from aidev.validation.models import Check, CheckResult, Report, Status
from aidev.validation.sandbox import Sandbox
from aidev.validation.specs import MOCKS, ProviderApi, providers_for
from aidev.validation.workspace import Workspace

# A stage runs only when every check before it passed. The checks inside a stage run
# together, so one iteration reports every static problem at once (ADR 019).
STAGES: tuple[tuple[Check, ...], ...] = (
    (Check.INSTALL,),
    (Check.RUFF, Check.PYRIGHT, Check.GROUNDING),
    (Check.PYTEST,),
)

OnResult = Callable[[CheckResult], Awaitable[None]]


class Specs(Protocol):
    async def api(self, provider: str) -> ProviderApi: ...

    async def mock_spec(self, provider: str) -> bytes: ...


class Validator:
    def __init__(
        self,
        sandbox: Sandbox,
        specs: Specs | None = None,
        timeouts: Timeouts | None = None,
        checks: Mapping[Check, CheckFn] | None = None,
        stages: Sequence[Sequence[Check]] = STAGES,
    ) -> None:
        self._sandbox = sandbox
        self._specs = specs
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

        providers = providers_for(workspace) if self._specs else []
        apis = {p: await self._specs.api(p) for p in providers} if self._specs else {}
        async with self._sandbox.session(workspace) as session:
            # The mocks start first and get ready while the code installs.
            mocks: dict[str, dict[str, Any]] = {}
            if self._specs and providers and Check.PYTEST in self._checks:
                specs = {p: await self._specs.mock_spec(p) for p in providers}
                urls = await session.start_mocks(specs)
                mocks = {
                    p: {
                        "upstream": url,
                        "env": MOCKS[p].env,
                        "suffix": MOCKS[p].suffix,
                        "form_only": MOCKS[p].form_only,
                    }
                    for p, url in urls.items()
                }
            ctx = CheckContext(session, workspace, self._timeouts, apis, mocks)
            blocked_by: Check | None = None
            for stage in self._stages:
                if blocked_by:
                    for check in stage:
                        await emit(CheckResult(check, Status.SKIP, f"skipped: {blocked_by} failed"))
                    continue
                done = await asyncio.gather(*(self._run(c, ctx) for c in stage))
                for result in done:
                    await emit(result)
                blocked_by = next((r.check for r in done if r.status is Status.FAIL), None)
        return Report(tuple(results))

    async def _run(self, check: Check, ctx: CheckContext) -> CheckResult:
        start = time.perf_counter()
        result = await self._checks[check](ctx)
        return replace(result, duration_ms=(time.perf_counter() - start) * 1000)
