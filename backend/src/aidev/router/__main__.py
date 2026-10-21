"""Router CLI.

uv run python -m aidev.router route "how do stripe webhooks work?"
uv run python -m aidev.router tune      # nearest-neighbour thresholds, leave-one-out
uv run python -m aidev.router eval      # every configuration on the golden set (~$0.01)
"""

import argparse
import asyncio
import sys

import httpx2

from aidev.config import Settings, get_settings
from aidev.infra import postgres
from aidev.llm.factory import create_llm, create_meter
from aidev.llm.models import model_for
from aidev.llm.types import Role
from aidev.rag.voyage import create_voyage
from aidev.router.eval import (
    CONFIGS,
    apply,
    format_confusion,
    format_report,
    load_cases,
    misroutes,
    overlap,
    run_stages,
    score,
    slice_accuracy,
    systems_accuracy,
    systems_misses,
    tune,
)
from aidev.router.intents import RouteContext
from aidev.router.nearest import DEFAULT_THRESHOLDS, ExampleIndex, load_examples
from aidev.router.router import Router


def _examples_path(settings: Settings):
    return settings.corpus_dir / "router" / "examples.toml"


async def _route(settings: Settings, message: str, active_run: bool) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        async with httpx2.AsyncClient() as http:
            meter = create_meter(settings, engine)
            embedder, _ = create_voyage(settings, http, meter)
            router = Router(
                create_llm(settings, meter),
                model_for(Role.ROUTER, settings.llm_profile),
                ExampleIndex(embedder, load_examples(_examples_path(settings))),
            )
            d = await router.route(message, RouteContext(active_run=active_run))
    finally:
        await engine.dispose()
    systems = ", ".join(d.systems) or "-"
    print(
        f"{d.intent}  (tier {d.tier}, confidence {d.confidence:.2f}, systems: {systems})\n"
        f"  {d.reason}\n  {d.latency_ms:.0f} ms, ${d.cost_usd:.6f}"
    )


async def _tune(settings: Settings) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        async with httpx2.AsyncClient() as http:
            embedder, _ = create_voyage(settings, http, create_meter(settings, engine))
            examples = ExampleIndex(embedder, load_examples(_examples_path(settings)))
            best, grid = await tune(examples)
    finally:
        await engine.dispose()
    t = best.thresholds
    print(
        f"best: min_similarity={t.min_similarity:.2f} min_margin={t.min_margin:.2f} "
        f"-> {best.coverage:.0%} of chit-chat examples at tier 0, 0 false positives"
    )
    for margin in (0.0, 0.05, 0.10):
        row = [g for g in grid if g.thresholds.min_margin == margin]
        cells = [
            f"{g.thresholds.min_similarity:.2f}:{g.coverage:.0%}/{g.false_positives}fp"
            for g in row
            if round(g.thresholds.min_similarity * 100) % 5 == 0
        ]
        print(f"  margin {margin:.2f}  " + " ".join(cells))
    current = DEFAULT_THRESHOLDS
    if current != t:
        print(f"note: DEFAULT_THRESHOLDS in router/nearest.py is {current}")


async def _eval(settings: Settings, concurrency: int, show_misses: bool) -> None:
    examples_list = load_examples(_examples_path(settings))
    cases = load_cases(settings.corpus_dir / "eval" / "router.toml")
    if leaked := overlap(cases, examples_list):
        sys.exit(f"golden messages also in the examples (tier 0 would see them): {leaked}")
    engine = postgres.create_engine(settings.database_url)
    try:
        async with httpx2.AsyncClient() as http:
            meter = create_meter(settings, engine)
            embedder, _ = create_voyage(settings, http, meter)
            results = await run_stages(
                cases,
                create_llm(settings, meter),
                model_for(Role.ROUTER, settings.llm_profile),
                ExampleIndex(embedder, examples_list),
                concurrency,
            )
    finally:
        await engine.dispose()
    outcomes = {name: apply(c, results, DEFAULT_THRESHOLDS) for name, c in CONFIGS.items()}
    reports = [score(name, o) for name, o in outcomes.items()]
    print(f"{len(cases)} cases, model {model_for(Role.ROUTER, settings.llm_profile)}\n")
    print(format_report(reports))
    print("\naccuracy per slice:")
    for name, outs in outcomes.items():
        cells = "  ".join(f"{s} {a:.2f} (n={n})" for s, (a, n) in slice_accuracy(outs).items())
        print(f"  {name:28} {cells}")
    acc, n = systems_accuracy(results)
    print(f"\nclassifier systems exact match: {acc:.2f} on {n} labeled cases")
    print(f"\nconfusion, {reports[-1].name}:\n{format_confusion(reports[-1])}")
    if show_misses:
        print("\nmisroutes:")
        for name, outs in outcomes.items():
            for o in misroutes(outs):
                print(f"  {name}: [{o.case.intent} -> {o.intent}, tier {o.tier}] {o.case.message}")
        print("\nsystems misses (gold -> classifier):")
        for r in systems_misses(results):
            gold = ",".join(r.case.systems or ())
            print(f"  [{gold} -> {','.join(r.classified.systems)}] {r.case.message}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m aidev.router")
    commands = parser.add_subparsers(dest="command", required=True)
    route = commands.add_parser("route", help="route one message")
    route.add_argument("message")
    route.add_argument("--active-run", action="store_true")
    commands.add_parser("tune", help="tune tier 0 thresholds on the examples")
    ev = commands.add_parser("eval", help="evaluate every configuration on the golden set")
    ev.add_argument("--concurrency", type=int, default=4)
    ev.add_argument("--misses", action="store_true", help="list misrouted messages")
    args = parser.parse_args()

    settings = get_settings()
    if args.command == "route":
        asyncio.run(_route(settings, args.message, args.active_run))
    elif args.command == "tune":
        asyncio.run(_tune(settings))
    else:
        asyncio.run(_eval(settings, args.concurrency, args.misses))


if __name__ == "__main__":
    main()
