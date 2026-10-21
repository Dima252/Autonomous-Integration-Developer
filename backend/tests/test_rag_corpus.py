from pathlib import Path

import httpx2

from aidev.config import get_settings
from aidev.rag.corpus import GUIDES, GuideSource, fetch_guide, guide_document, playbook_documents

PAGE = """---
title: "Verifying requests"
---

Source: https://docs.example.test/verify

# Verifying requests from Example

Example signs every request.

## Validating a request {#validate}

Compute the HMAC.
"""


def test_guide_document_cleans_chunks_and_titles_the_page() -> None:
    guide = GuideSource("slack", "verify", "https://docs.example.test/verify", "unused")

    doc = guide_document(guide, PAGE)

    assert doc.key == "slack/guide/verify"
    assert doc.title == "Verifying requests from Example"
    assert doc.url == "https://docs.example.test/verify"
    assert doc.chunks[0].breadcrumb.startswith("Slack › Verifying requests from Example")
    assert "Source:" not in doc.chunks[0].text
    assert "{#validate}" not in doc.chunks[0].text


async def test_fetch_guide_caches_until_refresh(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, text=f"# Version {calls}")

    guide = GuideSource("stripe", "page", "https://x.test/page", "https://x.test/page.md")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        first = await fetch_guide(guide, tmp_path, http)
        cached = await fetch_guide(guide, tmp_path, http)
        refreshed = await fetch_guide(guide, tmp_path, http, refresh=True)

    assert (first, cached, refreshed) == ("# Version 1", "# Version 1", "# Version 2")


def test_playbook_documents(tmp_path: Path) -> None:
    (tmp_path / "timeouts.md").write_text("# Set timeouts\n\nAlways.", encoding="utf-8")

    (doc,) = playbook_documents(tmp_path)

    assert (doc.key, doc.provider, doc.doc_type, doc.title) == (
        "general/playbook/timeouts",
        "general",
        "playbook",
        "Set timeouts",
    )


def test_real_playbook_entries_all_load() -> None:
    docs = playbook_documents(get_settings().corpus_dir / "playbook")

    assert {d.key.rsplit("/", 1)[1] for d in docs} >= {
        "idempotency",
        "webhook-signature-verification",
        "fast-ack-async-processing",
        "retries-and-backoff",
        "rate-limits",
        "secrets-and-configuration",
        "timeouts",
        "structured-logging",
    }
    assert all(d.chunks for d in docs)


def test_guide_sources_are_unique_https_pages() -> None:
    keys = [(g.provider, g.slug) for g in GUIDES]

    assert len(keys) == len(set(keys))
    assert all(g.url.startswith("https://") and g.fetch_url.startswith("https://") for g in GUIDES)
