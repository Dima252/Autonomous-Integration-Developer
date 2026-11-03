import hashlib
import hmac
import json
import time
from typing import Any


def _hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_stripe(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """The `Stripe-Signature` header for `payload`, signed with a webhook secret."""
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={_hex(secret, f'{ts}.'.encode() + payload)}"


def sign_slack(body: bytes, secret: str, timestamp: int | None = None) -> dict[str, str]:
    """The headers Slack signs a request with, from the app's signing secret."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": "v0=" + _hex(secret, f"v0:{ts}:".encode() + body),
    }


def sign_github(body: bytes, secret: str) -> dict[str, str]:
    """The `X-Hub-Signature-256` header GitHub sends with a webhook delivery."""
    return {"X-Hub-Signature-256": "sha256=" + _hex(secret, body)}


def stripe_event(
    kind: str, obj: dict[str, Any], event_id: str = "evt_test", created: int | None = None
) -> bytes:
    """A Stripe event as Stripe delivers it: compact JSON, the object under data.object."""
    event = {
        "id": event_id,
        "object": "event",
        "type": kind,
        "created": int(time.time()) if created is None else created,
        "livemode": False,
        "data": {"object": obj},
    }
    return json.dumps(event, separators=(",", ":")).encode()
