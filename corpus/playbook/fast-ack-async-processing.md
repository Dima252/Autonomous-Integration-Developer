# Acknowledge webhooks fast, process asynchronously

Providers wait only briefly for a response:
- Slack: 3 seconds.
- GitHub: 10 seconds, then the connection is closed.
- Stripe: requires a 2xx before any complex logic that might time out.

A slow handler is recorded as failed and triggers retries, duplicates and eventually
disabled endpoints, even if the work later succeeded.

## Pattern

1. Verify the signature (fast, local).
2. Parse just enough to route: event ID and type.
3. Persist or enqueue the event, and record its ID for deduplication.
4. Return `200` immediately.
5. A background worker does the slow part: API calls to other systems, database
   updates, notifications.

```python
@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, queue: Queue = Depends(get_queue)):
    event = verify_stripe(await request.body(), request.headers)  # 400 on failure
    await queue.enqueue("process_stripe_event", event_id=event["id"], payload=event)
    return {"received": True}
```

## Choosing the async mechanism

- **A real queue** (Redis + Arq, RQ or Celery; SQS; a Postgres outbox table) for anything
  that matters. Work survives restarts and can be retried with backoff.
- **FastAPI `BackgroundTasks`** only for best-effort work. Tasks run in the same process
  after the response and are lost if it crashes.
- Never block the event loop with synchronous HTTP clients inside an `async def` handler.

## Retries happen in the worker, not by failing the webhook

- If processing fails, the worker retries with backoff (see the retries entry).
- Return a non-2xx from the webhook endpoint only when you can't accept the event at all
  (bad signature, malformed payload, storage down), so the provider redelivers it later.
- **GitHub doesn't redeliver failed deliveries automatically.** A GitHub integration that
  can't afford to lose events should list failed deliveries through the API and request
  redelivery on a schedule.

## Batches

HubSpot sends up to 100 events per request. Acknowledge the whole batch once each event
is stored, then process them individually so one bad event doesn't block the others.

## Tests to write

- The webhook endpoint returns 2xx without waiting for downstream calls.
- A downstream failure doesn't turn the webhook response into an error.
- An enqueued event is processed by the worker.
