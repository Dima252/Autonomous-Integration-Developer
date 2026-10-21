# Make webhook handling and API writes idempotent

Webhook delivery is **at least once**. The same event arrives twice when your 2xx was
lost, your server timed out, or the provider retried. Stripe retries for up to three
days and Slack retries three times. Separate events can also describe the same change.
Processing a duplicate must not double-charge, double-alert or double-insert.

## Deduplicate incoming events

- **Key on the provider's unique ID:**
  - Stripe: `event.id` (`evt_...`)
  - GitHub: the `X-GitHub-Delivery` header
  - Slack Events API: `event_id`
  - HubSpot: `eventId` on each event in the batch
- **Record the ID atomically with the work.** Insert it into a table with a unique
  constraint in the same transaction as the side effect. If the insert conflicts, the
  event was already handled: return 2xx and stop.
- **Keep processed IDs longer than the provider's retry window.** For Stripe that means
  more than three days. A week is a safe default; then expire them.
- **Don't rely on in-memory sets.** They're lost on restart and not shared between
  workers.

```python
from sqlalchemy import text


async def handle_once(conn, event_id: str, process) -> bool:
    """Run `process` only for the first delivery of `event_id`."""
    inserted = await conn.scalar(
        text(
            "INSERT INTO processed_events (event_id) VALUES (:id) "
            "ON CONFLICT (event_id) DO NOTHING RETURNING event_id"
        ),
        {"id": event_id},
    )
    if inserted is None:
        return False  # duplicate delivery: already processed
    await process()
    return True
```

Call it inside one transaction, so a failure in `process` rolls back the ID too and the
retry runs again.

## Handle out-of-order delivery

Providers don't guarantee ordering. Before overwriting state, compare timestamps or
versions. Examples:
- Stripe `event.created`, or the object's current status fetched from the API.
- HubSpot `occurredAt`.

When in doubt, treat the event as a notification and fetch the current object from the
API.

## Idempotent outgoing writes

- **Send an idempotency key on POSTs that create things**, so a retried request after a
  timeout doesn't create a second object. Stripe accepts an `Idempotency-Key` header;
  the Python SDK takes `idempotency_key=` on create calls. Derive the key from your own
  stable identifier, such as `f"invoice-alert-{event_id}"`, not a random UUID per attempt.
- **For APIs without idempotency keys** (Slack `chat.postMessage`, most CRM writes),
  dedupe on your side: record that the action for this event ID was done before
  retrying it.

## Tests to write

- The same event delivered twice causes exactly one side effect.
- Two concurrent deliveries of the same event cause exactly one side effect.
- A failure during processing leaves the event unrecorded, so a retry processes it.
