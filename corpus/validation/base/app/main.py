import os

import stripe
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.slack import post_alert

app = FastAPI()
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
stripe.api_base = os.environ.get("STRIPE_API_BASE", "https://api.stripe.com")

BILLING_EVENTS = {"invoice.payment_failed", "invoice.paid", "customer.subscription.deleted"}
# Stripe delivers at least once; remember what we've handled (Redis in production).
seen_events: set[str] = set()


def handle(event: stripe.Event) -> None:
    # Stripe objects aren't dicts (stripe-python 15+): convert before reading fields.
    invoice = event.data.object.to_dict()
    customer = stripe.Customer.retrieve(invoice["customer"])
    metadata = customer.metadata.to_dict() if customer.metadata else {}
    if metadata.get("vip") == "true":
        post_alert(event.type, customer, invoice)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
    # Stripe signs the exact bytes it sent: verify before parsing anything.
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="invalid signature") from exc

    if event.type in BILLING_EVENTS and event.id not in seen_events:
        seen_events.add(event.id)
        background.add_task(handle, event)
    return {"status": "ok"}
