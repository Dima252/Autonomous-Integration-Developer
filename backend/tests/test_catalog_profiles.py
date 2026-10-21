from typing import Any

from aidev.catalog.openapi import parse_spec
from aidev.catalog.profiles import (
    GitHubProfile,
    HubSpotWebhooksProfile,
    SlackProfile,
    StripeProfile,
)


def test_stripe_events_come_from_the_enabled_events_enum() -> None:
    doc: dict[str, Any] = {
        "openapi": "3.0.0",
        "paths": {
            "/v1/webhook_endpoints": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "properties": {
                                        "enabled_events": {
                                            "items": {
                                                "enum": [
                                                    "*",
                                                    "invoice.paid",
                                                    "customer.subscription.deleted",
                                                    "issuing_card.created",
                                                    "mystery.thing.happened",
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {},
                }
            }
        },
        "components": {
            "schemas": {
                "event": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "data": {"$ref": "#/components/schemas/notification_event_data"},
                    },
                },
                "invoice": {"x-resourceId": "invoice", "type": "object"},
                "subscription": {"x-resourceId": "subscription", "type": "object"},
                "issuing.card": {"x-resourceId": "issuing.card", "type": "object"},
            }
        },
    }

    events = {e.name: e for e in parse_spec(doc, StripeProfile()).webhook_events}

    assert set(events) == {
        "invoice.paid",
        "customer.subscription.deleted",
        "issuing_card.created",
        "mystery.thing.happened",
    }
    paid = events["invoice.paid"].payload_schema
    assert paid is not None
    assert paid["properties"]["type"] == {"type": "string", "enum": ["invoice.paid"]}
    assert paid["properties"]["data"]["properties"]["object"] == {
        "$ref": "#/components/schemas/invoice"
    }
    assert paid["properties"]["id"] == {"type": "string"}  # rest of the envelope kept

    sub = events["customer.subscription.deleted"].payload_schema
    assert sub is not None
    assert sub["properties"]["data"]["properties"]["object"]["$ref"].endswith("/subscription")
    card = events["issuing_card.created"].payload_schema
    assert card is not None
    assert card["properties"]["data"]["properties"]["object"]["$ref"].endswith("/issuing.card")
    # Unknown resource: the event still exists, but the payload isn't guessed.
    assert events["mystery.thing.happened"].payload_schema is None


def test_github_webhook_names_match_what_github_delivers() -> None:
    doc = {
        "openapi": "3.0.3",
        "x-webhooks": {
            "check-run-requested-action": {"post": {"operationId": "check-run/requested-action"}},
            "push": {"post": {"operationId": "push"}},
        },
    }

    names = [e.name for e in parse_spec(doc, GitHubProfile()).webhook_events]

    assert names == ["check_run.requested_action", "push"]


def test_slack_scopes_come_from_the_token_description() -> None:
    def op(token_description: str) -> dict[str, Any]:
        return {
            "security": [{"slackAuth": ["chat:write:bot", "chat:write:user"]}],
            "parameters": [{"name": "token", "in": "header", "description": token_description}],
            "responses": {},
        }

    doc = {
        "swagger": "2.0",
        "paths": {
            "/chat.postMessage": {"post": op("Requires scope: `chat:write`")},
            "/legacy.method": {"post": op("Authentication token.")},
        },
    }

    scopes = {o.path: o.scopes for o in parse_spec(doc, SlackProfile()).operations}

    assert scopes["/chat.postMessage"] == ["chat:write"]
    assert scopes["/legacy.method"] == ["chat:write:bot", "chat:write:user"]


def test_hubspot_events_come_from_the_subscription_enum() -> None:
    doc = {
        "openapi": "3.0.1",
        "components": {
            "schemas": {
                "SubscriptionCreateRequest": {
                    "properties": {"eventType": {"enum": ["contact.creation", "deal.deletion"]}}
                }
            }
        },
    }

    events = parse_spec(doc, HubSpotWebhooksProfile()).webhook_events

    assert [e.name for e in events] == ["contact.creation", "deal.deletion"]
    assert all(e.payload_schema is None for e in events)
