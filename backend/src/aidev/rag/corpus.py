"""Everything the docs index contains: catalog entries, provider guide pages and the
engineering playbook."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx2

from aidev.catalog.store import CatalogStore
from aidev.rag.catalog_docs import catalog_documents
from aidev.rag.chunking import chunk_markdown, clean_markdown
from aidev.rag.documents import GENERAL, PROVIDER_NAMES, SourceDoc, doc_key

log = logging.getLogger(__name__)

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class GuideSource:
    provider: str
    slug: str  # stable name used in the doc key: "stripe/guide/<slug>"
    url: str  # human-readable page, used in citations
    fetch_url: str  # where its Markdown version lives


def _md(provider: str, slug: str, url: str) -> GuideSource:
    """Stripe, Slack and HubSpot serve any docs page as Markdown at `<url>.md`."""
    return GuideSource(provider, slug, url, f"{url}.md")


def _github(slug: str, path: str) -> GuideSource:
    return GuideSource(
        "github",
        slug,
        f"https://docs.github.com/en/{path}",
        f"https://docs.github.com/api/article/body?pathname={quote('/en/' + path)}",
    )


_STRIPE = "https://docs.stripe.com"
_SLACK = "https://docs.slack.dev"
_HUBSPOT = "https://developers.hubspot.com/docs"

# Pages an integration engineer reads: webhooks and their verification, auth, errors,
# retries and rate limits, pagination, and the objects billing/CRM/alerting tasks use.
GUIDES: list[GuideSource] = [
    _md("stripe", "webhooks", f"{_STRIPE}/webhooks"),
    _md("stripe", "webhooks-quickstart", f"{_STRIPE}/webhooks/quickstart"),
    _md("stripe", "webhook-signature-errors", f"{_STRIPE}/webhooks/signature"),
    _md("stripe", "event-types", f"{_STRIPE}/api/events/types"),
    _md("stripe", "errors", f"{_STRIPE}/api/errors"),
    _md("stripe", "idempotent-requests", f"{_STRIPE}/api/idempotent_requests"),
    _md("stripe", "pagination", f"{_STRIPE}/api/pagination"),
    _md("stripe", "rate-limits", f"{_STRIPE}/rate-limits"),
    _md("stripe", "authentication", f"{_STRIPE}/api/authentication"),
    _md("stripe", "api-keys", f"{_STRIPE}/keys"),
    _md("stripe", "versioning", f"{_STRIPE}/api/versioning"),
    _md("stripe", "metadata", f"{_STRIPE}/metadata"),
    _md("stripe", "expanding-objects", f"{_STRIPE}/api/expanding_objects"),
    _md("stripe", "subscription-webhooks", f"{_STRIPE}/billing/subscriptions/webhooks"),
    _md("stripe", "subscriptions-overview", f"{_STRIPE}/billing/subscriptions/overview"),
    _md("stripe", "invoice-status", f"{_STRIPE}/invoicing/integration/workflow-transitions"),
    _md("slack", "events-api", f"{_SLACK}/apis/events-api"),
    _md("slack", "events-api-http", f"{_SLACK}/apis/events-api/using-http-request-urls"),
    _md("slack", "web-api", f"{_SLACK}/apis/web-api"),
    _md("slack", "pagination", f"{_SLACK}/apis/web-api/pagination"),
    _md("slack", "rate-limits", f"{_SLACK}/apis/web-api/rate-limits"),
    _md("slack", "oauth", f"{_SLACK}/authentication/installing-with-oauth"),
    _md("slack", "tokens", f"{_SLACK}/authentication/tokens"),
    _md("slack", "verifying-requests", f"{_SLACK}/authentication/verifying-requests-from-slack"),
    _md("slack", "formatting-text", f"{_SLACK}/messaging/formatting-message-text"),
    _md("slack", "sending-messages", f"{_SLACK}/messaging/sending-and-scheduling-messages"),
    _md(
        "slack", "incoming-webhooks", f"{_SLACK}/messaging/sending-messages-using-incoming-webhooks"
    ),
    _md("slack", "chat-postmessage", f"{_SLACK}/reference/methods/chat.postMessage"),
    _md("slack", "block-kit", f"{_SLACK}/block-kit"),
    _md("slack", "scope-chat-write", f"{_SLACK}/reference/scopes/chat.write"),
    _github("about-webhooks", "webhooks/about-webhooks"),
    _github("validating-webhooks", "webhooks/using-webhooks/validating-webhook-deliveries"),
    _github("webhook-best-practices", "webhooks/using-webhooks/best-practices-for-using-webhooks"),
    _github("handling-webhooks", "webhooks/using-webhooks/handling-webhook-deliveries"),
    _github("failed-webhooks", "webhooks/using-webhooks/handling-failed-webhook-deliveries"),
    _github("rate-limits", "rest/using-the-rest-api/rate-limits-for-the-rest-api"),
    _github("pagination", "rest/using-the-rest-api/using-pagination-in-the-rest-api"),
    _github("authentication", "rest/authentication/authenticating-to-the-rest-api"),
    _github("rest-best-practices", "rest/using-the-rest-api/best-practices-for-using-the-rest-api"),
    _github("troubleshooting", "rest/using-the-rest-api/troubleshooting-the-rest-api"),
    _github("api-versions", "rest/about-the-rest-api/api-versions"),
    _github("credential-security", "rest/authentication/keeping-your-api-credentials-secure"),
    _github(
        "github-app-auth",
        "apps/creating-github-apps/authenticating-with-a-github-app/about-authentication-with-a-github-app",
    ),
    _md("hubspot", "usage-guidelines", f"{_HUBSPOT}/developer-tooling/platform/usage-guidelines"),
    _md("hubspot", "contacts", f"{_HUBSPOT}/api-reference/2026-03/crm/objects/contacts/guide"),
    _md("hubspot", "companies", f"{_HUBSPOT}/api-reference/2026-03/crm/objects/companies/guide"),
    _md("hubspot", "deals", f"{_HUBSPOT}/api-reference/2026-03/crm/objects/deals/guide"),
    _md("hubspot", "properties", f"{_HUBSPOT}/api-reference/2026-03/crm/properties/guide"),
    _md("hubspot", "search", f"{_HUBSPOT}/api-reference/2026-03/crm/search-the-crm"),
    _md(
        "hubspot",
        "associations",
        f"{_HUBSPOT}/api-reference/2026-03/crm/associations/associate-records/guide",
    ),
    _md(
        "hubspot", "webhooks", f"{_HUBSPOT}/apps/developer-platform/add-features/configure-webhooks"
    ),
    _md(
        "hubspot",
        "private-app-webhooks",
        f"{_HUBSPOT}/apps/legacy-apps/private-apps/create-and-edit-webhook-subscriptions-in-private-apps",
    ),
    _md(
        "hubspot",
        "request-validation",
        f"{_HUBSPOT}/apps/developer-platform/build-apps/authentication/request-validation",
    ),
    _md("hubspot", "private-apps", f"{_HUBSPOT}/apps/legacy-apps/private-apps/overview"),
    _md(
        "hubspot",
        "auth-overview",
        f"{_HUBSPOT}/apps/developer-platform/build-apps/authentication/overview",
    ),
    _md(
        "hubspot", "scopes", f"{_HUBSPOT}/apps/developer-platform/build-apps/authentication/scopes"
    ),
]


async def build_corpus(
    catalog: CatalogStore,
    data_dir: Path,
    corpus_dir: Path,
    http: httpx2.AsyncClient,
    refresh: bool = False,
) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    seen: set[str] = set()
    for stored in await catalog.load_specs():
        for doc in catalog_documents(stored.provider, stored.spec):
            # HubSpot's per-product specs repeat shared schemas (`Error`,
            # `SimplePublicObject`); keep the first, as catalog lookups do.
            if doc.key not in seen:
                seen.add(doc.key)
                docs.append(doc)
    for guide in GUIDES:
        md = await fetch_guide(guide, data_dir / "docs", http, refresh)
        docs.append(guide_document(guide, md))
    docs += playbook_documents(corpus_dir / "playbook")
    return docs


async def fetch_guide(
    guide: GuideSource, cache_dir: Path, http: httpx2.AsyncClient, refresh: bool = False
) -> str:
    """Guides are live pages, so unlike specs they can't be pinned. The cached copy is
    used until `refresh`; the index's content hashes then pick up what changed."""
    path = cache_dir / guide.provider / f"{guide.slug}.md"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8")
    log.info("fetching %s", guide.url)
    resp = await http.get(guide.fetch_url, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def guide_document(guide: GuideSource, md: str) -> SourceDoc:
    md = clean_markdown(md)
    return SourceDoc(
        key=doc_key(guide.provider, "guide", guide.slug),
        provider=guide.provider,
        doc_type="guide",
        title=_title(md, guide.slug),
        url=guide.url,
        chunks=chunk_markdown(md, PROVIDER_NAMES[guide.provider]),
    )


def playbook_documents(playbook_dir: Path) -> list[SourceDoc]:
    docs = []
    for path in sorted(playbook_dir.glob("*.md")):
        md = clean_markdown(path.read_text(encoding="utf-8"))
        docs.append(
            SourceDoc(
                key=doc_key(GENERAL, "playbook", path.stem),
                provider=GENERAL,
                doc_type="playbook",
                title=_title(md, path.stem),
                url=None,
                chunks=chunk_markdown(md, PROVIDER_NAMES[GENERAL]),
            )
        )
    return docs


def _title(md: str, fallback: str) -> str:
    match = _H1.search(md)
    return match.group(1) if match else fallback
