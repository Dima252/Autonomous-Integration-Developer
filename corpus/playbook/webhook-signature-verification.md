# Verify webhook signatures before parsing

Anyone who learns your webhook URL can send it requests. A signature proves a delivery
came from the provider and wasn't modified in transit. Verify it first, before parsing
the body or acting on anything in it, and reject failures with `400` (or `401`).

## Rules

- **Use the raw request body bytes.** Every provider signs the exact bytes it sent.
  Parsing JSON and re-serializing it changes whitespace and key order, and the signature
  no longer matches. In FastAPI, read `await request.body()` instead of declaring a
  Pydantic body parameter for the webhook route.
- **Compare in constant time.** Use `hmac.compare_digest`, never `==`, so response
  timing can't leak how much of a forged signature was correct.
- **Check the timestamp to stop replays** where the provider signs one. Reject deliveries
  older than the provider's tolerance (5 minutes for Stripe, Slack and HubSpot). Keep the
  server clock synced with NTP.
- **Prefer the provider SDK's verifier** where one exists. It handles header parsing,
  multiple signatures during secret rotation, and tolerance.
- **Keep the signing secret out of code.** Load it from an environment variable. Each
  endpoint has its own secret; test and live secrets differ.
- **Never log the secret or the full signature header.**

## Provider schemes

| Provider | Header(s) | What is signed | Encoding |
|---|---|---|---|
| Stripe | `Stripe-Signature` (`t=<ts>,v1=<sig>`) | `"{t}.{raw_body}"`, HMAC-SHA256 with the endpoint secret (`whsec_...`) | hex |
| GitHub | `X-Hub-Signature-256` (`sha256=<sig>`) | raw body, HMAC-SHA256 with the webhook secret | hex |
| Slack | `X-Slack-Signature` (`v0=<sig>`), `X-Slack-Request-Timestamp` | `"v0:{timestamp}:{raw_body}"`, HMAC-SHA256 with the signing secret | hex |
| HubSpot (v3) | `X-HubSpot-Signature-v3`, `X-HubSpot-Request-Timestamp` | `method + uri + raw_body + timestamp`, HMAC-SHA256 with the app's client secret | base64 |

GitHub doesn't sign a timestamp. Use the unique `X-GitHub-Delivery` header to reject a
delivery you've already processed (see the idempotency entry).

## Stripe (SDK)

```python
import os

import stripe
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
ENDPOINT_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> dict[str, str]:
    payload = await request.body()  # raw bytes, not parsed JSON
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, ENDPOINT_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="invalid signature") from None
    ...  # enqueue event["id"] / event["type"] for processing, then return
    return {"status": "ok"}
```

## GitHub (no SDK helper)

```python
import hashlib
import hmac


def verify_github(raw_body: bytes, signature_header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

## Slack (SDK)

```python
from slack_sdk.signature import SignatureVerifier

verifier = SignatureVerifier(signing_secret=os.environ["SLACK_SIGNING_SECRET"])

if not verifier.is_valid_request(raw_body, dict(request.headers)):
    raise HTTPException(status_code=401, detail="invalid signature")
```

## HubSpot v3

```python
import base64
import hashlib
import hmac
import time

MAX_AGE_MS = 5 * 60 * 1000


def verify_hubspot_v3(
    method: str, uri: str, raw_body: bytes, timestamp: str, signature: str, client_secret: str
) -> bool:
    if abs(time.time() * 1000 - int(timestamp)) > MAX_AGE_MS:
        return False
    message = f"{method}{uri}".encode() + raw_body + timestamp.encode()
    digest = hmac.new(client_secret.encode(), message, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
```

`uri` is the full URL HubSpot called, including scheme, host and query string, with the
URL-encoded characters HubSpot lists decoded.

## Tests to write

- A correctly signed payload is accepted.
- A tampered body with the original signature returns 400.
- A missing signature header returns 400.
- An expired timestamp (Stripe, Slack, HubSpot) is rejected.
