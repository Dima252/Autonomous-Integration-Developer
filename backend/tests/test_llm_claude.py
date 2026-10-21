"""The Claude adapter against the real SDK, with HTTP mocked: catches SDK shape changes
(like a streamed message without `_request_id`) without network access."""

import json
from typing import Any, Literal

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from aidev.llm.claude import AnthropicLLM
from aidev.llm.models import HAIKU, OPUS
from aidev.llm.types import ChatMessage, Completion, LLMError, LLMRequest, Role, TextDelta

USAGE = {
    "input_tokens": 12,
    "output_tokens": 5,
    "cache_read_input_tokens": 3,
    "cache_creation_input_tokens": 4,
    "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 0},
}


def _message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": HAIKU,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": USAGE,
    }


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _stream_body(words: list[str]) -> bytes:
    start = {**_message(""), "content": [], "stop_reason": None}
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": start},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ]
    events += [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w}}
        for w in words
    ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    return _sse(events)


class Recorder:
    def __init__(self, response: httpx2.Response) -> None:
        self.response = response
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return self.response


def _llm(recorder: Recorder) -> AnthropicLLM:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    return AnthropicLLM(anthropic.AsyncAnthropic(api_key="k", http_client=http, max_retries=0))


def _request(model: str = HAIKU, effort: Literal["low", "xhigh"] = "low") -> LLMRequest:
    return LLMRequest(Role.CHAT, model, [ChatMessage("user", "hi")], system="sys", effort=effort)


HEADERS = {"request-id": "req_123"}


async def test_complete_maps_text_usage_and_request_id() -> None:
    rec = Recorder(httpx2.Response(200, json=_message("hello"), headers=HEADERS))

    c = await _llm(rec).complete(_request())

    assert (c.text, c.model, c.stop_reason, c.request_id) == ("hello", HAIKU, "end_turn", "req_123")
    assert (c.usage.input_tokens, c.usage.cache_read_tokens, c.usage.cache_write_tokens) == (
        12,
        3,
        4,
    )
    body = rec.bodies[0]
    assert body["system"] == "sys" and body["output_config"] == {"effort": "low"}
    assert body["thinking"] == {"type": "disabled"}  # Haiku at low effort


async def test_thinking_is_never_disabled_where_the_model_rejects_it() -> None:
    rec = Recorder(httpx2.Response(200, json=_message("x"), headers=HEADERS))

    await _llm(rec).complete(_request(model=OPUS))
    await _llm(rec).complete(_request(effort="xhigh"))

    assert all("thinking" not in b for b in rec.bodies)


async def test_stream_yields_deltas_then_a_completion_with_the_request_id() -> None:
    rec = Recorder(
        httpx2.Response(
            200,
            content=_stream_body(["Hel", "lo"]),
            headers={**HEADERS, "content-type": "text/event-stream"},
        )
    )

    chunks = [c async for c in _llm(rec).stream(_request())]

    assert [c.text for c in chunks if isinstance(c, TextDelta)] == ["Hel", "lo"]
    final = chunks[-1]
    assert isinstance(final, Completion)
    assert (final.text, final.request_id, final.usage.output_tokens) == ("Hello", "req_123", 5)


class Answer(BaseModel):
    intent: str


async def test_parse_validates_structured_output() -> None:
    rec = Recorder(httpx2.Response(200, json=_message('{"intent": "chitchat"}'), headers=HEADERS))

    parsed, completion = await _llm(rec).parse(_request(), Answer)

    assert parsed == Answer(intent="chitchat")
    assert completion.request_id == "req_123"
    assert rec.bodies[0]["output_config"]["format"]["type"] == "json_schema"


async def test_api_errors_become_llm_errors() -> None:
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    rec = Recorder(httpx2.Response(400, json=error))

    with pytest.raises(LLMError, match="anthropic 400"):
        await _llm(rec).complete(_request())
