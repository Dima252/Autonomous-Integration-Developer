"""Provider knowledge the generic OpenAPI parser can't infer from the spec format alone.

Each provider publishes its spec differently: Stripe lists webhook events only as an
enum, GitHub's webhook names differ from the names it actually sends, and Slack's legacy
spec declares outdated OAuth scopes. A profile corrects for that, so the parser itself
stays provider-agnostic.
"""

import copy
import re
from collections.abc import Mapping
from typing import Any

from aidev.catalog.models import SCHEMA_REF_PREFIX, WebhookEvent


class SpecProfile:
    """Defaults: trust the spec as written."""

    def scopes(self, raw_op: Mapping[str, Any], declared: list[str]) -> list[str]:
        return declared

    def webhook_name(self, key: str, raw_op: Mapping[str, Any]) -> str:
        return str(raw_op.get("operationId") or key)

    def webhook_events(self, doc: Mapping[str, Any]) -> list[WebhookEvent]:
        """Events the spec defines outside a `webhooks` / `x-webhooks` section."""
        return []


class GitHubProfile(SpecProfile):
    def webhook_name(self, key: str, raw_op: Mapping[str, Any]) -> str:
        # operationId "check-run/requested-action" is delivered as event `check_run`
        # with action `requested_action`.
        return super().webhook_name(key, raw_op).replace("-", "_").replace("/", ".")


class StripeProfile(SpecProfile):
    """Stripe's spec has no webhooks section. Event types appear only as the enum of
    `enabled_events` on webhook endpoints, and every event shares the `event` envelope
    whose `data.object` is the resource the type is named after."""

    DOCS_URL = "https://docs.stripe.com/api/events/types"

    def webhook_events(self, doc: Mapping[str, Any]) -> list[WebhookEvent]:
        schemas: Mapping[str, Any] = doc.get("components", {}).get("schemas", {})
        envelope = schemas.get("event")
        try:
            body = doc["paths"]["/v1/webhook_endpoints"]["post"]["requestBody"]
            form = body["content"]["application/x-www-form-urlencoded"]["schema"]
            names: list[str] = form["properties"]["enabled_events"]["items"]["enum"]
        except KeyError:
            return []
        resources = {s["x-resourceId"]: n for n, s in schemas.items() if "x-resourceId" in s}

        events = []
        for name in names:
            if name == "*":
                continue
            resource = _stripe_resource(name, resources)
            payload = None
            if envelope is not None and resource is not None:
                payload = copy.deepcopy(envelope)
                props = payload.setdefault("properties", {})
                props["type"] = {"type": "string", "enum": [name]}
                props["data"] = {
                    "type": "object",
                    "required": ["object"],
                    "properties": {
                        "object": {"$ref": SCHEMA_REF_PREFIX + resource},
                        "previous_attributes": {
                            "type": "object",
                            "description": "Changed attributes' previous values "
                            "(only on `*.updated` events).",
                        },
                    },
                }
            description = f"`data.object` holds the `{resource}` object." if resource else None
            events.append(
                WebhookEvent(
                    name=name,
                    description=description,
                    payload_schema=payload,
                    docs_url=self.DOCS_URL,
                )
            )
        return events


def _stripe_resource(event: str, resources: Mapping[str, str]) -> str | None:
    """`customer.subscription.deleted` -> schema of resource `subscription`."""
    prefix = event.rsplit(".", 1)[0]
    candidates = (
        prefix,  # checkout.session
        prefix.split(".")[-1],  # customer.subscription -> subscription
        prefix.replace("_", ".", 1),  # issuing_card -> issuing.card
    )
    return next((resources[c] for c in candidates if c in resources), None)


class SlackProfile(SpecProfile):
    """Slack's archived spec declares legacy scopes (`chat:write:bot`) in `security`, but
    each method's token parameter names the current one ("Requires scope: `chat:write`")."""

    _REQUIRES = re.compile(r"Requires scope: `([^`]+)`")

    def scopes(self, raw_op: Mapping[str, Any], declared: list[str]) -> list[str]:
        for param in raw_op.get("parameters", []):
            if isinstance(param, dict) and param.get("name") == "token":
                found = self._REQUIRES.findall(param.get("description") or "")
                if found:
                    return sorted(set(found))
        return declared


class HubSpotWebhooksProfile(SpecProfile):
    """HubSpot lists subscribable event types as an enum on subscription requests. The
    payload format is documented only in prose, so payloads are left to the docs index."""

    DOCS_URL = "https://developers.hubspot.com/docs/api/webhooks"

    def webhook_events(self, doc: Mapping[str, Any]) -> list[WebhookEvent]:
        schemas = doc.get("components", {}).get("schemas", {})
        try:
            names = schemas["SubscriptionCreateRequest"]["properties"]["eventType"]["enum"]
        except KeyError:
            return []
        return [WebhookEvent(name=n, docs_url=self.DOCS_URL) for n in names]
