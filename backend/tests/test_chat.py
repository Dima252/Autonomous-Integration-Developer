from collections.abc import AsyncGenerator
from uuid import UUID

from aidev.chat.prompts import BUDGET_REPLY, TEMPLATES
from aidev.chat.service import ChatEvent, ChatService
from aidev.chat.store import RunStatus
from aidev.events.bus import EventType
from aidev.llm.models import HAIKU
from aidev.llm.types import BudgetExceededError, LLMError, LLMRequest, StreamChunk, TextDelta
from aidev.router.router import Router
from tests.chat_fakes import FakeSearch, MemoryBus, MemoryChatStore, hit
from tests.llm_fakes import FakeLLM


def _llm(intent: str, systems: list[str] | None = None, query: str = "", text: str = "Sure thing"):
    def parsed(_: LLMRequest) -> dict[str, object]:
        return {
            "intent": intent,
            "confidence": 0.9,
            "systems": systems or [],
            "search_query": query,
        }

    return FakeLLM(text=text, parsed=parsed)


async def _chat(
    llm: FakeLLM, text: str, search: FakeSearch | None = None
) -> tuple[list[ChatEvent], MemoryChatStore, MemoryBus, UUID]:
    store, bus = MemoryChatStore(), MemoryBus()
    service = ChatService(
        store, Router(llm, HAIKU), llm, bus, search or FakeSearch(), lambda _: HAIKU
    )
    cid = await store.create_conversation()
    events = [e async for e in service.reply(cid, text)]
    return events, store, bus, cid


def _text(events: list[ChatEvent]) -> str:
    return "".join(e.data["text"] for e in events if e.type == "delta")


async def test_greeting_is_answered_from_a_template_without_any_model() -> None:
    llm = _llm("integration_task")

    events, store, _, cid = await _chat(llm, "hey there!")

    assert [e.type for e in events] == ["conversation", "route", "delta", "done"]
    assert _text(events) == TEMPLATES["greeting"]
    assert llm.requests == []
    user, assistant = store.messages[cid]
    assert (user.role, user.intent, assistant.content) == (
        "user",
        "chitchat",
        TEMPLATES["greeting"],
    )
    assert events[-1].data["cost_usd"] == 0


async def test_classified_small_talk_streams_a_model_reply() -> None:
    llm = _llm("chitchat", text="I'm an integration builder")

    events, store, _, cid = await _chat(llm, "what's your favourite movie?")

    assert _text(events) == "I'm an integration builder "
    assert events[-1].data["cost_usd"] > 0
    assert store.messages[cid][-1].meta["model"] == HAIKU


async def test_docs_question_cites_the_retrieved_excerpts() -> None:
    llm = _llm("docs_question", systems=["stripe"], query="how are stripe webhooks signed")
    search = FakeSearch([hit(1), hit(2)])

    events, store, _, cid = await _chat(llm, "how is it signed?", search)

    types = [e.type for e in events]
    assert types.index("citations") < types.index("delta")
    citations = next(e for e in events if e.type == "citations").data
    assert [c["n"] for c in citations["hits"]] == [1, 2]
    # The standalone query from the router, filtered to the provider plus the playbook.
    assert search.calls == [
        {"query": "how are stripe webhooks signed", "k": 6, "providers": ["stripe", "general"]}
    ]
    prompt = llm.requests[-1].messages[-1].content
    assert '<doc n="1"' in prompt and "Fact number 2." in prompt
    assert store.messages[cid][-1].meta["citations"][0]["url"] == "https://docs.example/1"


async def test_docs_question_without_hits_does_not_guess() -> None:
    llm = _llm("docs_question")

    events, _, _, _ = await _chat(llm, "what is the frobnicate endpoint?", FakeSearch([]))

    assert "rather not guess" in _text(events)
    assert len(llm.requests) == 1  # the classifier only, no answer model


async def test_integration_task_opens_a_run_and_publishes_its_events() -> None:
    llm = _llm("integration_task", systems=["stripe", "slack"])

    events, store, bus, _ = await _chat(llm, "alert slack on failed stripe payments")

    run = next(e for e in events if e.type == "run").data
    run_id = UUID(run["run_id"])
    assert run["events_url"] == f"/runs/{run_id}/events"
    assert store.runs[run_id]["status"] == RunStatus.FAILED
    assert [e.type for e in bus.events[run_id]] == [EventType.RUN_CREATED, EventType.RUN_FAILED]
    assert "(Stripe, Slack)" in _text(events)


async def test_classifier_over_budget_says_so() -> None:
    class OverBudget(FakeLLM):
        async def parse(self, request, schema):  # type: ignore[override]
            raise BudgetExceededError("cap reached")

    events, _, _, _ = await _chat(OverBudget(), "build a stripe webhook")

    assert _text(events) == BUDGET_REPLY


class FailsMidStream(FakeLLM):
    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk]:
        yield TextDelta("Half an ")
        raise LLMError("connection reset")


async def test_failure_mid_reply_keeps_the_partial_text_and_reports_it() -> None:
    llm = FailsMidStream(
        parsed=lambda _: {
            "intent": "chitchat",
            "confidence": 0.9,
            "systems": [],
            "search_query": "",
        }
    )

    events, store, _, cid = await _chat(llm, "tell me about yourself please")

    assert [e.type for e in events][-2:] == ["error", "done"]
    saved = store.messages[cid][-1]
    assert saved.content.startswith("Half an ")
    assert saved.meta["error"] == "connection reset"


async def test_disconnect_mid_reply_saves_what_was_said() -> None:
    llm = _llm("chitchat", text="one two three four")
    store, bus = MemoryChatStore(), MemoryBus()
    service = ChatService(store, Router(llm, HAIKU), llm, bus, FakeSearch(), lambda _: HAIKU)
    cid = await store.create_conversation()

    stream = service.reply(cid, "tell me something")
    async for event in stream:
        if event.type == "delta":
            break  # the browser tab closed
    await stream.aclose()
    await service.drain()

    saved = store.messages[cid][-1]
    assert (saved.role, saved.content, saved.meta["interrupted"]) == ("assistant", "one ", True)


async def test_history_reaches_the_router_and_the_reply_model() -> None:
    llm = _llm("chitchat", text="ok")
    store, bus = MemoryChatStore(), MemoryBus()
    service = ChatService(store, Router(llm, HAIKU), llm, bus, FakeSearch(), lambda _: HAIKU)
    cid = await store.create_conversation()

    _ = [e async for e in service.reply(cid, "my name is Sam, what's yours?")]
    _ = [e async for e in service.reply(cid, "do you remember my name?")]

    classify, reply = llm.requests[-2:]
    assert "my name is Sam" in classify.messages[0].content
    assert [m.role for m in reply.messages] == ["user", "assistant", "user"]
