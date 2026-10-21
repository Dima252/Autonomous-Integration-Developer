from dataclasses import dataclass
from typing import Literal

from aidev.rag.chunking import Chunk

DocType = Literal["operation", "webhook_event", "schema", "guide", "playbook"]
DOC_TYPES: tuple[DocType, ...] = ("operation", "webhook_event", "schema", "guide", "playbook")

# Provider label for content that applies to every integration (the playbook).
GENERAL = "general"

PROVIDER_NAMES = {
    "stripe": "Stripe",
    "github": "GitHub",
    "slack": "Slack",
    "hubspot": "HubSpot",
    GENERAL: "Engineering playbook",
}


@dataclass(frozen=True)
class SourceDoc:
    """One logical document (a guide page, an operation, a playbook entry) and the
    chunks it was split into. `key` is stable across re-indexing, so citations and eval
    labels can refer to it."""

    key: str  # "stripe/guide/webhooks", "slack/operation/chat_postMessage"
    provider: str
    doc_type: DocType
    title: str
    url: str | None
    chunks: list[Chunk]


def doc_key(provider: str, doc_type: DocType, name: str) -> str:
    return f"{provider}/{doc_type}/{name}"
