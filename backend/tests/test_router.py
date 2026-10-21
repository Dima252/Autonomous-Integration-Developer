from pathlib import Path

import pytest

from aidev.config import get_settings
from aidev.llm.models import HAIKU
from aidev.llm.types import ChatMessage, LLMRequest
from aidev.router.eval import CONFIGS, apply, load_cases, overlap, run_stages, score
from aidev.router.intents import Intent, RouteContext
from aidev.router.nearest import ExampleIndex, Thresholds, load_examples
from aidev.router.router import Router
from aidev.router.rules import match_smalltalk
from tests.llm_fakes import FakeLLM
from tests.rag_fakes import FailingService, FakeEmbedder

CORPUS = get_settings().corpus_dir
ASKED = RouteContext(history=(ChatMessage("assistant", "Want me to add retries?"),))


@pytest.mark.parametrize(
    "text",
    ["hi", "Hello there!", "thanks a lot!", "ok cool, bye", "Thank you so much 🙏", "👍"],
)
def test_rules_match_pure_small_talk(text: str) -> None:
    assert match_smalltalk(text, RouteContext()) is not None


@pytest.mark.parametrize(
    "text",
    [
        "hi, build me a stripe webhook",
        "thanks! now send it to discord",
        "hey how do slack scopes work",
        "ok " * 30,
        "",
    ],
)
def test_rules_never_match_a_request(text: str) -> None:
    assert match_smalltalk(text, RouteContext()) is None


def test_acknowledgements_after_a_question_are_answers() -> None:
    assert match_smalltalk("ok", ASKED) is None
    assert match_smalltalk("👍", ASKED) is None
    assert match_smalltalk("thanks!", ASKED) is not None  # still not an answer


def _classifier(intent: str, confidence: float = 0.9, systems: list[str] | None = None):
    def parsed(_: LLMRequest) -> dict[str, object]:
        return {"intent": intent, "confidence": confidence, "systems": systems or []}

    return FakeLLM(parsed=parsed)


async def test_rule_hits_skip_every_model() -> None:
    llm = _classifier("integration_task")
    embedder = FakeEmbedder()
    router = Router(llm, HAIKU, ExampleIndex(embedder, []))

    decision = await router.route("hey!")

    assert (decision.intent, decision.tier) == (Intent.CHITCHAT, 0)
    assert llm.requests == [] and embedder.document_calls == []


def _nearest_router(llm: FakeLLM) -> Router:
    examples = load_examples(CORPUS / "router" / "examples.toml")
    # Bag-of-words vectors: an exact example text has similarity 1.0.
    return Router(llm, HAIKU, ExampleIndex(FakeEmbedder(), examples), Thresholds(0.9, 0.05))


async def test_nearest_example_answers_without_waiting_for_the_classifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = _classifier("chitchat")
    router = _nearest_router(llm)

    decision = await router.route("tell me a joke")
    await router.drain()

    assert (decision.intent, decision.tier, decision.cost_usd) == (Intent.CHITCHAT, 0, 0.0)
    assert len(llm.requests) == 1  # the shadow check still ran, and was billed
    assert "disagreement" not in caplog.text


async def test_shadow_classifier_logs_tier_0_disagreements(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router = _nearest_router(_classifier("integration_task"))

    decision = await router.route("tell me a joke")
    await router.drain()

    assert decision.intent is Intent.CHITCHAT
    assert "tier 0 disagreement" in caplog.text


async def test_no_nearest_match_waits_for_the_classifier() -> None:
    router = _nearest_router(_classifier("docs_question", systems=["stripe"]))

    decision = await router.route("how are stripe webhook signatures computed")

    assert (decision.intent, decision.tier) == (Intent.DOCS_QUESTION, 1)


async def test_classifier_handles_the_rest_and_names_systems() -> None:
    llm = _classifier("integration_task", systems=["stripe", "slack", "stripe"])
    router = Router(llm, HAIKU)

    decision = await router.route("alert slack on failed stripe payments")

    assert (decision.intent, decision.tier) == (Intent.INTEGRATION_TASK, 1)
    assert decision.systems == ("stripe", "slack")
    assert decision.cost_usd > 0
    assert "<state>no active run</state>" in llm.requests[0].messages[0].content


async def test_unsure_heavy_route_becomes_a_question() -> None:
    router = Router(_classifier("integration_task", confidence=0.3), HAIKU)

    decision = await router.route("do the thing")

    assert decision.intent is Intent.CLARIFY


async def test_followup_without_a_run_is_a_new_task() -> None:
    router = Router(_classifier("followup_edit", systems=["slack"]), HAIKU)

    decision = await router.route("send it to slack instead")

    assert decision.intent is Intent.INTEGRATION_TASK


async def test_embedding_outage_falls_through_to_the_classifier() -> None:
    router = Router(_classifier("docs_question"), HAIKU, ExampleIndex(FailingService(), []))

    decision = await router.route("what's a payment intent?")

    assert (decision.intent, decision.tier) == (Intent.DOCS_QUESTION, 1)


async def test_classifier_outage_is_marked_degraded() -> None:
    router = Router(FakeLLM(fail=True), HAIKU)

    decision = await router.route("build a stripe webhook")

    assert decision.degraded and decision.intent is Intent.CLARIFY


def test_golden_set_is_held_out_from_the_examples() -> None:
    cases = load_cases(CORPUS / "eval" / "router.toml")
    examples = load_examples(CORPUS / "router" / "examples.toml")

    assert overlap(cases, examples) == []
    assert {c.intent for c in cases} == set(Intent)


async def test_eval_scores_configurations_from_one_run(tmp_path: Path) -> None:
    cases = load_cases(CORPUS / "eval" / "router.toml")[:20]
    results = await run_stages(cases, _classifier("docs_question"), HAIKU, None)

    llm_only = score("classifier", apply(CONFIGS["classifier"], results, Thresholds(1, 1)))
    with_rules = score("rules", apply(CONFIGS["rules+classifier"], results, Thresholds(1, 1)))

    assert llm_only.tier0_share == 0
    assert with_rules.tier0_share > 0 and with_rules.tier0_errors == 0
    assert with_rules.accuracy > llm_only.accuracy
