import os
from typing import Any

import stripe
from slack_sdk import WebClient

client = WebClient(
    token=os.environ.get("SLACK_BOT_TOKEN", ""),
    base_url=os.environ.get("SLACK_API_URL", WebClient.BASE_URL),
)
CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "")


def post_alert(event_type: str, customer: stripe.Customer, invoice: dict[str, Any]) -> None:
    amount = invoice.get("amount_due", 0) / 100
    currency = str(invoice.get("currency", "")).upper()
    who = customer.name or customer.id
    client.chat_postMessage(
        channel=CHANNEL,
        text=f":rotating_light: VIP {who}: {event_type} ({amount:.2f} {currency})",
    )
