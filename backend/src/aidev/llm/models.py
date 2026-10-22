"""Model per role (docs/architecture.md §7). Switching a role is a one-line change here,
and the whole system moves between profiles with `LLM_PROFILE`."""

from typing import Literal

from aidev.llm.types import Role

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

Profile = Literal["dev", "final"]

# Phase 2 roles are cheap and latency-bound, so they stay on Haiku in both profiles.
# The reasoning roles and the coder (Phase 4) are where the profiles differ.
_MODELS: dict[Profile, dict[Role, str]] = {
    "dev": {Role.ROUTER: HAIKU, Role.CHAT: HAIKU, Role.DOCS_QA: HAIKU},
    "final": {Role.ROUTER: HAIKU, Role.CHAT: HAIKU, Role.DOCS_QA: HAIKU},
}


def model_for(role: Role, profile: Profile) -> str:
    return _MODELS[profile][role]
