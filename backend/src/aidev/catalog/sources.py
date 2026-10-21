"""Where each provider's spec comes from. URLs are pinned to commits so ingestion is
reproducible; bumping a SHA is how the catalog picks up a new API version."""

from dataclasses import dataclass, field
from urllib.parse import quote

from aidev.catalog.profiles import (
    GitHubProfile,
    HubSpotWebhooksProfile,
    SlackProfile,
    SpecProfile,
    StripeProfile,
)


@dataclass(frozen=True)
class SpecSource:
    provider: str
    name: str  # unique within the provider
    url: str
    profile: SpecProfile = field(default_factory=SpecProfile, compare=False)


def _github_raw(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{quote(path)}"


_STRIPE_SHA = "f3799ad599a6cf7c76fd94ce72274c0367cc2ba7"
_GITHUB_SHA = "755ee33b4a81d51f6f07a902cce26cbec3b822f2"
# The Slack spec repo was archived in 2021; this is its final commit. Newer Web API
# methods are missing from it and must come from the docs index.
_SLACK_SHA = "bc08db49625630e3585bf2f1322128ea04f2a7f3"
_HUBSPOT_SHA = "e8578a318010f28c28eb16ef7746b57818a2806c"

# HubSpot publishes ~700 spec files. These cover what CRM integrations need: the core
# objects, generic objects, properties, associations, owners and webhook subscriptions.
_HUBSPOT_SPECS = {
    "crm-contacts": "CRM/Contacts/Rollouts/424/v3/contacts.json",
    "crm-companies": "CRM/Companies/Rollouts/424/v3/companies.json",
    "crm-deals": "CRM/Deals/Rollouts/424/v3/deals.json",
    "crm-objects": "CRM/Objects/Rollouts/424/v3/objects.json",
    "crm-properties": "CRM/Properties/Rollouts/145899/v3/properties.json",
    "crm-associations": "CRM/Associations/Rollouts/130902/v4/associations.json",
    "crm-owners": "CRM/Crm Owners/Rollouts/146888/v3/crmOwners.json",
    "webhooks": "Webhooks/Webhooks/Rollouts/147891/v3/webhooks.json",
}

SOURCES: list[SpecSource] = [
    SpecSource(
        "stripe",
        "api",
        _github_raw("stripe/openapi", _STRIPE_SHA, "openapi/spec3.json"),
        StripeProfile(),
    ),
    SpecSource(
        "github",
        "rest",
        _github_raw(
            "github/rest-api-description",
            _GITHUB_SHA,
            "descriptions/api.github.com/api.github.com.json",
        ),
        GitHubProfile(),
    ),
    SpecSource(
        "slack",
        "web",
        _github_raw("slackapi/slack-api-specs", _SLACK_SHA, "web-api/slack_web_openapi_v2.json"),
        SlackProfile(),
    ),
    *(
        SpecSource(
            "hubspot",
            name,
            _github_raw(
                "HubSpot/HubSpot-public-api-spec-collection", _HUBSPOT_SHA, f"PublicApiSpecs/{path}"
            ),
            HubSpotWebhooksProfile() if name == "webhooks" else SpecProfile(),
        )
        for name, path in _HUBSPOT_SPECS.items()
    ),
]

PROVIDERS = sorted({s.provider for s in SOURCES})
