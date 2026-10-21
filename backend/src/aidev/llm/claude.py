"""Claude adapter for `LLMClient`, on the official Anthropic SDK (ADR 002)."""

from collections.abc import AsyncGenerator
from typing import Any

import anthropic
from anthropic.types import Message
from pydantic import BaseModel

from aidev.config import Settings
from aidev.llm.types import (
    Completion,
    LLMError,
    LLMRequest,
    StreamChunk,
    TextDelta,
    Usage,
)

# Models that accept `thinking: disabled` (Haiku 5.5 does at effort high or below).
# Opus 5.5 and Sonnet 5.5 reject it, so they run adaptive thinking at the requested
# effort instead.
_CAN_DISABLE_THINKING = {"claude-haiku-5-5"}


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def complete(self, request: LLMRequest) -> Completion:
        try:
            message = await self._client.messages.create(**_params(request))
        except anthropic.APIError as exc:
            raise _error(exc) from exc
        return _completion(message)

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk]:
        try:
            async with self._client.messages.stream(**_params(request)) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
                message = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise _error(exc) from exc
        yield _completion(message)

    async def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, Completion]:
        try:
            message = await self._client.messages.parse(**_params(request), output_format=schema)
        except anthropic.APIError as exc:
            raise _error(exc) from exc
        if message.parsed_output is None:
            # A refusal, or max_tokens hit before the JSON closed.
            raise LLMError(f"no structured output (stop_reason={message.stop_reason})")
        return message.parsed_output, _completion(message)


def create_anthropic(settings: Settings) -> AnthropicLLM:
    if settings.anthropic_api_key is None:
        raise LLMError("ANTHROPIC_API_KEY is not set (see .env.example)")
    return AnthropicLLM(
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    )


def _params(request: LLMRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        "output_config": {"effort": request.effort},
    }
    if request.system:
        params["system"] = request.system
    if request.thinking:
        params["thinking"] = {"type": "adaptive"}
    elif request.model in _CAN_DISABLE_THINKING and request.effort in ("low", "medium", "high"):
        params["thinking"] = {"type": "disabled"}
    return params


def _completion(message: Message) -> Completion:
    u = message.usage
    cache_writes = u.cache_creation
    write_1h = cache_writes.ephemeral_1h_input_tokens if cache_writes else 0
    write_total = u.cache_creation_input_tokens or 0
    return Completion(
        text="".join(b.text for b in message.content if b.type == "text"),
        model=message.model,
        usage=Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=write_total - write_1h,
            cache_write_1h_tokens=write_1h,
        ),
        stop_reason=message.stop_reason,
        request_id=message._request_id,
    )


def _error(exc: anthropic.APIError) -> LLMError:
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"anthropic {exc.status_code}: {exc.message}")
    return LLMError(f"anthropic: {exc!r}")
