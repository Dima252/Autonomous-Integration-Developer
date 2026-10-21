"""The chat path: route a message, answer it on the cheapest path that can, and stream
the answer as typed events (docs/architecture.md §2).

Events, in order:
- `conversation`: ids of the conversation and the stored user message
- `route`: the router's decision (intent, tier, systems, reason)
- `citations`: docs questions only, the excerpts the answer may cite as [n]
- `run`: integration tasks only, the run id and its event stream
- `delta`: reply text, any number of times
- `error`: the reply failed part-way; carries the notice shown to the user
- `done`: id of the stored reply, its total cost and latency
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from aidev.chat.prompts import (
    BUDGET_REPLY,
    CHAT_SYSTEM,
    CLARIFY_SYSTEM,
    DEGRADED_REPLY,
    DOCS_SYSTEM,
    EDIT_REPLY,
    RUN_REPLY,
    TEMPLATES,
)
from aidev.chat.store import ChatStore, RunStatus, StoredMessage
from aidev.events.bus import EventBus, EventType, RunEvent
from aidev.llm.client import LLMClient
from aidev.llm.pricing import cost_usd
from aidev.llm.types import BudgetExceededError, ChatMessage, LLMError, LLMRequest, Role, TextDelta
from aidev.rag.search import SearchHit, SearchTrace
from aidev.router.intents import Intent, RouteContext, RouteDecision
from aidev.router.router import Router

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 12  # sent to the router and the reply model
HISTORY_CHARS = 2000  # per message
DOCS_K = 6
MAX_REPLY_TOKENS = 1500
DOC_PROVIDERS = ("stripe", "slack", "github", "hubspot")
PLAYBOOK_PROVIDER = "general"  # the engineering playbook is filed under it
_NAMES = {
    "stripe": "Stripe",
    "slack": "Slack",
    "github": "GitHub",
    "hubspot": "HubSpot",
    "other": "another service",
}


class Search(Protocol):
    """The slice of `aidev.rag.search.DocSearch` the chat path uses."""

    async def search(
        self,
        query: str,
        *,
        k: int,
        providers: Sequence[str] | None,
        trace: SearchTrace | None,
    ) -> list[SearchHit]: ...


@dataclass(frozen=True)
class ChatEvent:
    type: str
    data: dict[str, Any]


@dataclass
class _Reply:
    text: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class ChatService:
    def __init__(
        self,
        store: ChatStore,
        router: Router,
        llm: LLMClient,
        bus: EventBus,
        search: Search,
        model_for: Callable[[Role], str],
    ) -> None:
        self.store = store
        self.router = router
        self.llm = llm
        self.bus = bus
        self.search = search
        self.model_for = model_for
        self._background: set[asyncio.Task[Any]] = set()

    async def reply(self, conversation_id: UUID, text: str) -> AsyncGenerator[ChatEvent]:
        started = time.perf_counter()
        history = await self.store.recent_messages(conversation_id, HISTORY_MESSAGES)
        context = RouteContext(
            history=tuple(ChatMessage(m.role, m.content[:HISTORY_CHARS]) for m in history),
            active_run=await self.store.has_editable_run(conversation_id),
        )
        decision = await self.router.route(text, context)
        route = _route_meta(decision)
        message_id = await self.store.add_message(
            conversation_id, "user", text, intent=decision.intent, meta={"route": route}
        )
        yield ChatEvent(
            "conversation", {"conversation_id": str(conversation_id), "message_id": message_id}
        )
        yield ChatEvent("route", route)

        reply = _Reply(cost_usd=decision.cost_usd)
        try:
            async for event in self._answer(decision, text, history, conversation_id, reply):
                if event.type == "delta":
                    reply.text.append(event.data["text"])
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # The client went away mid-answer. Keep what was said, without awaiting:
            # this coroutine is being torn down.
            reply.meta["interrupted"] = True
            self._spawn(self._save(conversation_id, reply, started))
            raise
        except Exception as exc:
            if isinstance(exc, BudgetExceededError):
                notice = BUDGET_REPLY
            elif isinstance(exc, LLMError):
                notice = DEGRADED_REPLY.format(why="Please try again in a moment.")
            else:
                log.exception("chat reply failed")
                notice = "Something went wrong while answering. Please try again."
            reply.meta["error"] = str(exc) or type(exc).__name__
            reply.text.append(("\n\n" if reply.text else "") + notice)
            yield ChatEvent("error", {"message": notice})

        reply_id = await self._save(conversation_id, reply, started)
        yield ChatEvent(
            "done",
            {
                "message_id": reply_id,
                "cost_usd": round(reply.cost_usd, 6),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            },
        )

    async def drain(self) -> None:
        """Waits for messages still being saved in the background (tests, shutdown)."""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    # --- paths ------------------------------------------------------------------------

    async def _answer(
        self,
        decision: RouteDecision,
        text: str,
        history: Sequence[StoredMessage],
        conversation_id: UUID,
        reply: _Reply,
    ) -> AsyncGenerator[ChatEvent]:
        if decision.degraded:
            notice = (
                BUDGET_REPLY
                if decision.budget_exceeded
                else DEGRADED_REPLY.format(why="Please try again in a moment.")
            )
            yield _delta(notice)
            return

        match decision.intent:
            case Intent.CHITCHAT if decision.rule in TEMPLATES:
                yield _delta(TEMPLATES[decision.rule or ""])
            case Intent.CHITCHAT:
                async for e in self._converse(Role.CHAT, CHAT_SYSTEM, history, text, reply):
                    yield e
            case Intent.CLARIFY:
                async for e in self._converse(Role.CHAT, CLARIFY_SYSTEM, history, text, reply):
                    yield e
            case Intent.DOCS_QUESTION:
                async for e in self._docs(decision, history, text, reply):
                    yield e
            case Intent.INTEGRATION_TASK:
                async for e in self._run(decision, conversation_id, text, reply):
                    yield e
            case Intent.FOLLOWUP_EDIT:
                yield _delta(EDIT_REPLY.format(systems=_systems_text(decision.systems)))

    async def _converse(
        self,
        role: Role,
        system: str,
        history: Sequence[StoredMessage],
        text: str,
        reply: _Reply,
        content: str | None = None,
    ) -> AsyncGenerator[ChatEvent]:
        request = LLMRequest(
            role=role,
            model=self.model_for(role),
            system=system,
            messages=[*_turns(history), ChatMessage("user", content or text)],
            max_tokens=MAX_REPLY_TOKENS,
            effort="low",
        )
        async for chunk in self.llm.stream(request):
            if isinstance(chunk, TextDelta):
                yield _delta(chunk.text)
            else:
                reply.cost_usd += cost_usd(chunk.model, chunk.usage)
                reply.meta["model"] = chunk.model

    async def _docs(
        self,
        decision: RouteDecision,
        history: Sequence[StoredMessage],
        text: str,
        reply: _Reply,
    ) -> AsyncGenerator[ChatEvent]:
        query = decision.search_query or text
        providers = [s for s in decision.systems if s in DOC_PROVIDERS]
        trace = SearchTrace()
        hits = await self.search.search(
            query,
            k=DOCS_K,
            providers=[*providers, PLAYBOOK_PROVIDER] if providers else None,
            trace=trace,
        )
        citations = [_citation(n, h) for n, h in enumerate(hits, start=1)]
        reply.meta["citations"] = citations
        yield ChatEvent(
            "citations", {"query": query, "hits": citations, "degraded": trace.degraded}
        )
        if not hits:
            yield _delta(
                "I couldn't find anything about that in the documentation I have indexed, "
                "so I'd rather not guess."
            )
            return
        docs = "\n".join(
            f'<doc n="{n}" title="{h.title}" source="{h.breadcrumb}">\n{h.content}\n</doc>'
            for n, h in enumerate(hits, start=1)
        )
        content = f"<docs>\n{docs}\n</docs>\n\n<question>\n{text}\n</question>"
        async for e in self._converse(Role.DOCS_QA, DOCS_SYSTEM, history, text, reply, content):
            yield e

    async def _run(
        self, decision: RouteDecision, conversation_id: UUID, text: str, reply: _Reply
    ) -> AsyncGenerator[ChatEvent]:
        run_id = await self.store.create_run(conversation_id, text, decision.systems)
        systems = list(decision.systems)
        await self.bus.publish(
            run_id,
            RunEvent(
                EventType.RUN_CREATED,
                f"Run created{_systems_text(decision.systems)}",
                data={"request": text, "systems": systems},
            ),
        )
        # Phase 2 has no worker: the agent graph that executes runs is Phase 3.
        await self.bus.publish(
            run_id,
            RunEvent(
                EventType.RUN_FAILED,
                "The agent graph isn't built yet (Phase 3)",
                data={"reason": "not_implemented"},
            ),
        )
        await self.store.set_run_status(run_id, RunStatus.FAILED)
        reply.meta["run_id"] = str(run_id)
        yield ChatEvent("run", {"run_id": str(run_id), "events_url": f"/runs/{run_id}/events"})
        yield _delta(RUN_REPLY.format(systems=_systems_text(decision.systems), run=str(run_id)[:8]))

    # --- helpers ----------------------------------------------------------------------

    async def _save(self, conversation_id: UUID, reply: _Reply, started: float) -> int:
        meta = {
            **reply.meta,
            "cost_usd": round(reply.cost_usd, 6),
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
        return await self.store.add_message(
            conversation_id, "assistant", "".join(reply.text), meta=meta
        )

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def _delta(text: str) -> ChatEvent:
    return ChatEvent("delta", {"text": text})


def _turns(history: Sequence[StoredMessage]) -> list[ChatMessage]:
    turns = [ChatMessage(m.role, m.content[:HISTORY_CHARS]) for m in history if m.content]
    while turns and turns[0].role == "assistant":  # the API wants a user turn first
        turns.pop(0)
    return turns


def _route_meta(d: RouteDecision) -> dict[str, Any]:
    return {
        "intent": d.intent.value,
        "tier": d.tier,
        "confidence": round(d.confidence, 3),
        "systems": list(d.systems),
        "reason": d.reason,
        "latency_ms": round(d.latency_ms),
        "degraded": d.degraded,
    }


def _citation(n: int, h: SearchHit) -> dict[str, Any]:
    return {
        "n": n,
        "title": h.title,
        "url": h.url,
        "breadcrumb": h.breadcrumb,
        "provider": h.provider,
        "doc_type": h.doc_type,
    }


def _systems_text(systems: Sequence[str]) -> str:
    names = [_NAMES.get(s, s) for s in systems]
    return f" ({', '.join(names)})" if names else ""
