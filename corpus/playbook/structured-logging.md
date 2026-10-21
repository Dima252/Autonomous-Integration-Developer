# Log structured events with correlation IDs

When an integration misbehaves at 3 a.m., logs are the only record of what happened.
Free-text `print` statements can't be searched or aggregated. Structured logs with the
right identifiers can.

## Rules

- **Log in JSON (key-value pairs)**, one event per line, with a stable `event` name:
  `{"event": "webhook.received", "provider": "stripe", "event_id": "evt_...", "type": "invoice.paid"}`.
- **Include correlation IDs** that let you join your logs with the provider's dashboard:
  - Stripe: `event.id`, plus the `Request-Id` response header on API calls
  - GitHub: `X-GitHub-Delivery`
  - Slack: `event_id`
  - HubSpot: `eventId`
  - your own job or request ID
- **Log every outcome:**
  - received
  - duplicate (skipped)
  - processed
  - failed, with the error class and whether it will be retried
- **Never log secrets or sensitive data:**
  - no `Authorization` headers, tokens or signing secrets
  - minimize personal data such as emails and names; log IDs instead
- **Use levels deliberately:**
  - `INFO` for normal milestones
  - `WARNING` for retried or degraded paths
  - `ERROR` for failures that need a human
- **Log the exception with its traceback** (`logger.exception`) at the point where it's
  handled, once. Not at every layer it passes through.

```python
import logging

import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
log = structlog.get_logger()


async def process(event: dict) -> None:
    structlog.contextvars.bind_contextvars(provider="stripe", event_id=event["id"])
    log.info("webhook.processing", type=event["type"])
    try:
        ...
    except Exception:
        log.exception("webhook.failed", will_retry=True)
        raise
    log.info("webhook.processed")
```

## Tests to write

- Processing an event emits `webhook.processed` with the event ID (capture logs).
- No secret value appears in captured log output.
