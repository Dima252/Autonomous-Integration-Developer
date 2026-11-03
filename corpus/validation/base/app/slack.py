import os
from typing import Any

import httpx
import stripe

SLACK_API_URL = os.environ.get("SLACK_API_URL", "https://slack.com/api/")
TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "")


def post_alert(event_type: str, customer: stripe.Customer, invoice: dict[str, Any]) -> None:
    amount = invoice.get("amount_due", 0) / 100
    currency = str(invoice.get("currency", "")).upper()
    who = customer.name or customer.id
    resp = httpx.post(
        f"{SLACK_API_URL}chat.postMessage",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={
            "channel": CHANNEL,
            "text": f":rotating_light: VIP {who}: {event_type} ({amount:.2f} {currency})",
        },
        timeout=10,
    )
    resp.raise_for_status()
    # Slack answers 200 even when it refuses the message.
    if not resp.json().get("ok"):
        raise RuntimeError(f"Slack refused the alert: {resp.json().get('error')}")
