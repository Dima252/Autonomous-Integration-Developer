"""Router evaluation (ADR 012).

Every stage runs once per golden case (rules, nearest neighbour, classifier) and each
configuration is then scored from those results, so comparing configurations costs no
extra API calls. The configuration's latency is the sum of the stages it would have run.
"""

import asyncio
import statistics
import time
import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from aidev.llm.client import LLMClient
from aidev.llm.types import ChatMessage
from aidev.router.classifier import classify
from aidev.router.intents import HEAVY, Intent, Provider, RouteContext, RouteDecision
from aidev.router.nearest import Example, ExampleIndex, Match, Thresholds, is_chitchat
from aidev.router.rules import match_smalltalk, normalize


@dataclass(frozen=True)
class Case:
    message: str
    intent: Intent
    systems: tuple[Provider, ...] | None  # None: not labeled
    context: RouteContext
    slice: str = "core"


def load_cases(path: Path) -> list[Case]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        Case(
            message=c["message"],
            intent=Intent(c["intent"]),
            systems=tuple(c["systems"]) if "systems" in c else None,
            context=RouteContext(
                history=tuple(ChatMessage(h["role"], h["content"]) for h in c.get("history", [])),
                active_run=c.get("active_run", False),
            ),
            slice=c.get("slice", "core"),
        )
        for c in data["case"]
    ]


def overlap(cases: Sequence[Case], examples: Sequence[Example]) -> list[str]:
    """Golden messages that also appear among the examples (must be none)."""
    known = {normalize(e.text) for e in examples}
    return [c.message for c in cases if normalize(c.message) in known]


@dataclass
class StageResults:
    case: Case
    rule: str | None
    match: Match | None  # None when tier 0 matching is skipped (awaiting an answer)
    nearest_ms: float
    classified: RouteDecision
    classify_ms: float


async def run_stages(
    cases: Sequence[Case],
    llm: LLMClient,
    model: str,
    examples: ExampleIndex | None,
    concurrency: int = 4,
) -> list[StageResults]:
    if examples is not None:
        await examples.vectors()  # embed the examples before any timing
    gate = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> StageResults:
        async with gate:
            rule = match_smalltalk(case.message, case.context)
            match, nearest_ms = None, 0.0
            if examples is not None and not case.context.awaiting_answer:
                start = time.perf_counter()
                match = await examples.match(await examples.embed(case.message))
                nearest_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            decision = await classify(llm, model, case.message, case.context)
            classify_ms = (time.perf_counter() - start) * 1000
            return StageResults(case, rule, match, nearest_ms, decision, classify_ms)

    return list(await asyncio.gather(*(one(c) for c in cases)))


@dataclass(frozen=True)
class Config:
    name: str
    rules: bool
    nearest: bool
    # Start the classifier alongside the nearest-neighbour lookup instead of after it,
    # and drop its answer when tier 0 matches. Its cost is counted either way, since a
    # request cancelled mid-flight may still be billed.
    parallel: bool = False


CONFIGS = {
    c.name: c
    for c in (
        Config("classifier", rules=False, nearest=False),
        Config("rules+classifier", rules=True, nearest=False),
        Config("rules+nearest+classifier", rules=True, nearest=True),
        Config("rules+(nearest||classifier)", rules=True, nearest=True, parallel=True),
    )
}


@dataclass(frozen=True)
class Outcome:
    case: Case
    intent: Intent
    tier: int
    latency_ms: float
    cost_usd: float


def apply(config: Config, results: Sequence[StageResults], thresholds: Thresholds) -> list[Outcome]:
    outcomes = []
    for r in results:
        if config.rules and r.rule is not None:
            outcomes.append(Outcome(r.case, Intent.CHITCHAT, 0, 0.0, 0.0))
            continue
        d = r.classified
        nearest = config.nearest and r.match is not None
        if nearest and is_chitchat(r.match, thresholds):
            cost = d.cost_usd if config.parallel else 0.0
            outcomes.append(Outcome(r.case, Intent.CHITCHAT, 0, r.nearest_ms, cost))
            continue
        if not nearest:
            latency = r.classify_ms
        elif config.parallel:
            latency = max(r.nearest_ms, r.classify_ms)
        else:
            latency = r.nearest_ms + r.classify_ms
        outcomes.append(Outcome(r.case, d.intent, 1, latency, d.cost_usd))
    return outcomes


@dataclass
class Report:
    name: str
    n: int
    accuracy: float
    tier0_share: float
    tier0_errors: int
    missed_heavy: int  # a task or edit routed to a cheap path
    false_heavy: int  # a cheap message routed into the agent graph
    p50_ms: float
    p95_ms: float
    chitchat_p50_ms: float  # what small talk waits before its reply can start
    other_p50_ms: float
    cost_per_1k: float  # USD per 1,000 messages
    recall: dict[Intent, float] = field(default_factory=dict)
    confusion: Counter[tuple[Intent, Intent]] = field(default_factory=Counter)


def score(name: str, outcomes: Sequence[Outcome]) -> Report:
    n = len(outcomes)
    correct = [o.intent is o.case.intent for o in outcomes]
    tier0 = [o for o in outcomes if o.tier == 0]
    latencies = sorted(o.latency_ms for o in outcomes)
    chat = [o.latency_ms for o in outcomes if o.case.intent is Intent.CHITCHAT]
    other = [o.latency_ms for o in outcomes if o.case.intent is not Intent.CHITCHAT]
    recall = {}
    for intent in Intent:
        gold = [o for o in outcomes if o.case.intent is intent]
        if gold:
            recall[intent] = sum(o.intent is intent for o in gold) / len(gold)
    return Report(
        name=name,
        n=n,
        accuracy=sum(correct) / n,
        tier0_share=len(tier0) / n,
        tier0_errors=sum(o.intent is not o.case.intent for o in tier0),
        missed_heavy=sum(o.case.intent in HEAVY and o.intent not in HEAVY for o in outcomes),
        false_heavy=sum(o.case.intent not in HEAVY and o.intent in HEAVY for o in outcomes),
        p50_ms=statistics.median(latencies),
        p95_ms=latencies[min(n - 1, round(0.95 * (n - 1)))],
        chitchat_p50_ms=statistics.median(chat) if chat else 0.0,
        other_p50_ms=statistics.median(other) if other else 0.0,
        cost_per_1k=1000 * sum(o.cost_usd for o in outcomes) / n,
        recall=recall,
        confusion=Counter((o.case.intent, o.intent) for o in outcomes),
    )


def _systems_labeled(results: Sequence[StageResults]) -> list[StageResults]:
    return [
        r for r in results if r.case.systems is not None and r.case.intent is not Intent.CHITCHAT
    ]


def systems_accuracy(results: Sequence[StageResults]) -> tuple[float, int]:
    """Exact-set match of the classifier's systems, on labeled non-chitchat cases."""
    labeled = _systems_labeled(results)
    hits = sum(set(r.classified.systems) == set(r.case.systems or ()) for r in labeled)
    return (hits / len(labeled) if labeled else 0.0), len(labeled)


def systems_misses(results: Sequence[StageResults]) -> list[StageResults]:
    return [
        r
        for r in _systems_labeled(results)
        if set(r.classified.systems) != set(r.case.systems or ())
    ]


def slice_accuracy(outcomes: Sequence[Outcome]) -> dict[str, tuple[float, int]]:
    slices: dict[str, list[bool]] = {}
    for o in outcomes:
        slices.setdefault(o.case.slice, []).append(o.intent is o.case.intent)
    return {name: (sum(v) / len(v), len(v)) for name, v in slices.items()}


def misroutes(outcomes: Sequence[Outcome]) -> list[Outcome]:
    return [o for o in outcomes if o.intent is not o.case.intent]


def format_report(reports: Sequence[Report]) -> str:
    lines = [
        f"{'config':28} {'acc':>5} {'tier0':>6} {'t0 err':>6} {'miss':>5} {'false':>6} "
        f"{'p50':>6} {'p95':>6} {'chat':>6} {'other':>6} {'$/1k':>7}",
        "-" * 99,
    ]
    for r in reports:
        lines.append(
            f"{r.name:28} {r.accuracy:5.2f} {r.tier0_share:6.2f} {r.tier0_errors:6d} "
            f"{r.missed_heavy:5d} {r.false_heavy:6d} {r.p50_ms:6.0f} {r.p95_ms:6.0f} "
            f"{r.chitchat_p50_ms:6.0f} {r.other_p50_ms:6.0f} {r.cost_per_1k:7.4f}"
        )
    lines.append("ms: p50/p95 over all messages, then p50 for chit-chat and for the rest")
    lines.append("")
    lines.append("recall per intent: " + "  ".join(f"{i.value}" for i in reports[0].recall))
    for r in reports:
        lines.append(f"  {r.name:26} " + "  ".join(f"{v:.2f}" for v in r.recall.values()))
    return "\n".join(lines)


def format_confusion(report: Report) -> str:
    intents = list(Intent)
    short = {i: i.value[:9] for i in intents}
    lines = ["gold \\ predicted  " + " ".join(f"{short[i]:>9}" for i in intents)]
    for gold in intents:
        row = [report.confusion.get((gold, p), 0) for p in intents]
        lines.append(f"{short[gold]:17} " + " ".join(f"{v:9d}" for v in row))
    return "\n".join(lines)


# --- Tuning the nearest-neighbour thresholds ----------------------------------------


@dataclass(frozen=True)
class TuneResult:
    thresholds: Thresholds
    coverage: float  # share of chit-chat examples answered at tier 0
    false_positives: int


async def tune(examples: ExampleIndex) -> tuple[TuneResult, list[TuneResult]]:
    """Leave-one-out over the examples: each one is matched against all the others.
    Picks the thresholds with the most coverage and zero false positives, preferring
    the strictest such pair (the golden set is never consulted)."""
    vectors = await examples.vectors()
    matches = [await examples.match(v, skip=i) for i, v in enumerate(vectors)]
    is_chat = [e.intent is Intent.CHITCHAT for e in examples.examples]
    total_chat = sum(is_chat)
    grid = []
    for sim_step in range(50, 100):
        for margin_step in range(0, 21):
            t = Thresholds(sim_step / 100, margin_step / 100)
            hits = [is_chitchat(m, t) for m in matches]
            tp = sum(h and c for h, c in zip(hits, is_chat, strict=True))
            fp = sum(h and not c for h, c in zip(hits, is_chat, strict=True))
            grid.append(TuneResult(t, tp / total_chat, fp))
    safe = [g for g in grid if g.false_positives == 0]
    best = max(
        safe,
        key=lambda g: (g.coverage, g.thresholds.min_margin, g.thresholds.min_similarity),
    )
    return best, grid
