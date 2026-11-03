"""The provider specs the validator uses: trimmed for the Prism mock servers, and parsed
for the grounding check (ADR 009, 019). Both come from the catalog's pinned sources,
cached under data/specs/, so a validation needs no database."""

import asyncio
import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

from aidev.catalog.ingest import fetch_spec
from aidev.catalog.openapi import parse_spec, path_regex
from aidev.catalog.sources import SOURCES, SpecSource
from aidev.validation.workspace import Workspace


@dataclass(frozen=True)
class MockProvider:
    """How code under validation reaches a provider, and how its mock behaves."""

    provider: str
    env: str  # the variable generated code reads its API base URL from
    suffix: str  # appended to the mock's URL: Slack clients expect a trailing slash
    default_body: Mapping[str, Any]  # the mock's answer when a test set none
    packages: frozenset[str]  # requirements that mean the code talks to this provider
    hosts: frozenset[str]  # hosts that mean the same in a URL
    drop_params: frozenset[str] = frozenset()
    form_only: bool = False  # the spec describes form bodies only; JSON is re-encoded


MOCKS: dict[str, MockProvider] = {
    "stripe": MockProvider(
        "stripe",
        env="STRIPE_API_BASE",
        suffix="",
        default_body={},
        packages=frozenset({"stripe"}),
        hosts=frozenset({"api.stripe.com"}),
    ),
    "slack": MockProvider(
        "slack",
        env="SLACK_API_URL",
        suffix="/",
        # Slack clients treat any answer without "ok": true as an error.
        default_body={"ok": True},
        packages=frozenset({"slack-sdk", "slack-bolt"}),
        hosts=frozenset({"slack.com"}),
        # The 2021 spec models the bot token as a required header named `token`; real
        # clients send `Authorization: Bearer`, which the security scheme covers.
        drop_params=frozenset({"token"}),
        form_only=True,
    ),
    "github": MockProvider(
        "github",
        env="GITHUB_API_URL",
        suffix="",
        default_body={},
        packages=frozenset({"pygithub", "githubkit"}),
        hosts=frozenset({"api.github.com"}),
    ),
}

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def providers_for(workspace: Workspace) -> list[str]:
    """The providers a workspace talks to: from its requirements, or from API hosts
    named anywhere in its code."""
    names = set()
    for line in (workspace.requirements or "").splitlines():
        if m := _NAME.match(line):
            names.add(re.sub(r"[._]+", "-", m.group(1)).lower())
    code = "\n".join(c for p, c in workspace.files.items() if p.endswith(".py"))
    return [
        m.provider for m in MOCKS.values() if names & m.packages or any(h in code for h in m.hosts)
    ]


def mock_spec(doc: Mapping[str, Any], mock: MockProvider) -> dict[str, Any]:
    """Keep every path's parameters and request body, which is what the mock checks
    calls against, and replace responses with one plain object. Responses carry nearly
    all of a large spec's references (all 1,243 of Stripe's), and Prism never finishes
    resolving Stripe's full spec. Tests set the responses they depend on themselves."""
    out = copy.deepcopy(dict(doc))
    swagger2 = str(out.get("swagger", "")).startswith("2")
    if swagger2:
        ok: dict[str, Any] = {
            "description": "OK",
            "schema": {"type": "object"},
            "examples": {"application/json": dict(mock.default_body)},
        }
    else:
        ok = {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": dict(mock.default_body),
                }
            },
        }
    for item in out.get("paths", {}).values():
        for op in item.values():
            if not isinstance(op, dict) or "responses" not in op:
                continue
            op["responses"] = {"200": ok}
            if mock.drop_params:
                op["parameters"] = [
                    p for p in op.get("parameters", []) if p.get("name") not in mock.drop_params
                ]
    if swagger2:
        out.pop("definitions", None)
        out.pop("responses", None)
    else:
        components = out.get("components", {})
        out["components"] = {k: v for k, v in components.items() if k == "securitySchemes"}
        out.pop("webhooks", None)
    return out


@dataclass(frozen=True)
class ProviderApi:
    """What the grounding check matches code against."""

    provider: str
    base_urls: tuple[str, ...]
    operations: tuple[tuple[str, re.Pattern[str]], ...]
    events: frozenset[str] = field(default_factory=frozenset)

    def has_operation(self, method: str, path: str) -> bool:
        return any(m == method.upper() and rx.match(path) for m, rx in self.operations)

    @property
    def event_prefixes(self) -> frozenset[str]:
        return frozenset(e.split(".", 1)[0] for e in self.events if "." in e)


class SpecLibrary:
    """Loads each provider's spec once: downloaded on first use, then from the cache."""

    def __init__(self, cache_dir: Path, sources: list[SpecSource] = SOURCES) -> None:
        self._cache_dir = cache_dir
        self._sources = {s.provider: s for s in sources if s.provider in MOCKS}
        self._docs: dict[str, dict[str, Any]] = {}
        self._apis: dict[str, ProviderApi] = {}
        self._lock = asyncio.Lock()

    async def _doc(self, provider: str) -> dict[str, Any]:
        async with self._lock:
            if provider not in self._docs:
                async with httpx2.AsyncClient() as client:
                    raw = await fetch_spec(self._sources[provider], self._cache_dir, client)
                self._docs[provider] = json.loads(raw)
            return self._docs[provider]

    async def mock_spec(self, provider: str) -> bytes:
        return json.dumps(mock_spec(await self._doc(provider), MOCKS[provider])).encode()

    async def api(self, provider: str) -> ProviderApi:
        if provider not in self._apis:
            source = self._sources[provider]
            spec = parse_spec(await self._doc(provider), source.profile)
            self._apis[provider] = ProviderApi(
                provider,
                tuple(spec.base_urls),
                tuple((op.method, re.compile(path_regex(op.path))) for op in spec.operations),
                frozenset(e.name for e in spec.webhook_events),
            )
        return self._apis[provider]
