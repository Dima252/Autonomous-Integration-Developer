# Set a timeout on every outbound call

An HTTP call without a timeout can hang forever: on a stalled TCP connection, a slow
upstream, or a load balancer that never answers. In a webhook handler that means missing
the provider's response deadline. In a worker it means a stuck job and a growing queue.

## Rules

- **Always pass a timeout.** `requests` has **no default timeout**: a bare
  `requests.post(url)` can block indefinitely. `httpx` defaults to 5 seconds; set it
  explicitly anyway, so the choice is visible.
- **Separate connect and read timeouts.** Connecting should be fast (a few seconds);
  reads may legitimately take longer for heavy endpoints.
- **Budget the whole operation.** A handler that makes several calls needs an overall
  deadline (`asyncio.timeout(...)`) as well as per-call timeouts. Five calls with 10-second
  timeouts can take 50 seconds.
- **Stay inside the caller's deadline.** Work done synchronously in a webhook handler
  must finish well within the provider's limit (3 seconds for Slack, 10 for GitHub), or
  move to a background worker.
- **Treat a timeout as a transient error**, retried with backoff (see the retries entry).
  For non-idempotent writes, the request may have succeeded, so use idempotency keys.
- **Configure SDK clients too:**
  - Stripe Python: `stripe.StripeClient(api_key, http_client=...)`, or the global
    `stripe.default_http_client`. It defaults to 80 seconds, which is long for a request
    path.
  - Slack `WebClient(timeout=...)`.

```python
import asyncio

import httpx

client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))


async def enrich_and_notify(event: dict) -> None:
    async with asyncio.timeout(20):  # overall budget for this job
        customer = await fetch_customer(client, event["customer"])
        await post_alert(client, customer)
```

## Tests to write

- A mocked upstream that never responds raises a timeout within the configured limit.
- The timeout is handled (retried or reported), not left to crash the worker silently.
