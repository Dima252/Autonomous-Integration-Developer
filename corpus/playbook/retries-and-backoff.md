# Retry transient failures with exponential backoff and jitter

Networks drop connections and APIs return `429` and `5xx` under load. A client that
never retries is fragile. A client that retries instantly and forever amplifies outages.

## What to retry

| Retry | Don't retry |
|---|---|
| Connection errors, timeouts | `400`, `401`, `403`, `404`, `422`: the request itself is wrong |
| `429 Too Many Requests` | Signature or validation failures |
| `500`, `502`, `503`, `504` | Business errors (a declined card is an answer, not a fault) |

Only retry non-idempotent requests (POST) when they carry an idempotency key or are
otherwise safe to repeat (see the idempotency entry).

## How

- **Exponential backoff with full jitter:** wait a random time between 0 and
  `min(cap, base * 2**attempt)`. Jitter stops many clients from retrying in sync.
- **Honor `Retry-After`** when the response includes it (Slack and GitHub send it with
  rate limits). It overrides the computed delay.
- **Cap attempts and total time.** For example, 5 attempts or 60 seconds. Then fail loudly:
  log, alert, or move the job to a dead-letter queue. Never retry forever.
- **Retry at one layer only.** If the SDK already retries (Stripe's
  `stripe.max_network_retries`), don't wrap it in another retry loop. The retry counts
  multiply.

```python
import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):  # connect/read errors and timeouts
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in RETRYABLE_STATUS


@retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_random_exponential(multiplier=0.5, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def post_json(client: httpx.AsyncClient, url: str, body: dict) -> dict:
    resp = await client.post(url, json=body)
    resp.raise_for_status()
    return resp.json()
```

## SDK settings

- Stripe Python: set `stripe.max_network_retries = 2` (or per client). The SDK adds
  idempotency keys to its own retries.
- Slack `slack_sdk`: add `RateLimitErrorRetryHandler` to the `WebClient` to retry 429s
  using `Retry-After`.

## Tests to write

- A `503` followed by `200` succeeds after one retry.
- A `400` is raised immediately, with no retry.
- After the attempt limit, the last error propagates.
