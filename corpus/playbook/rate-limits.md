# Respect provider rate limits

Every API limits request rates, and exceeding the limit returns `429`. Repeated
violations can get an app throttled harder or flagged. Design for the limit from the
start instead of discovering it in production.

## Provider specifics

- **GitHub:**
  - A primary limit per token per hour, reported in the `x-ratelimit-limit`,
    `x-ratelimit-remaining` and `x-ratelimit-reset` headers.
  - Separate **secondary** limits on bursts, concurrency and expensive endpoints.
  - On `403`/`429`, wait for `retry-after` if present. Otherwise wait until
    `x-ratelimit-reset`, or at least a minute.
- **Slack:**
  - Web API methods belong to rate-limit tiers (requests per minute per workspace).
  - `chat.postMessage` has a special limit of about one message per second per channel.
  - A `429` includes `Retry-After` in seconds.
- **HubSpot:**
  - Limits are per app, with a burst limit per 10 seconds and a daily limit that depend
    on the account tier.
  - Errors should stay under 5% of daily requests.
- **Stripe:**
  - Returns `429` when rate limited.
  - Use the SDK's built-in retries and avoid polling; listen to webhooks instead.

## Practices

- **Read the headers.** Track remaining quota and slow down before hitting zero.
- **Throttle on the client side** with a token bucket or semaphore when you fan out,
  rather than firing everything at once and letting `429`s sort it out.
- **Batch.** Use batch endpoints, such as HubSpot `batch/read` and `batch/update`, and
  page sizes at the maximum, instead of one call per record.
- **Cache reads** that don't change often (users, channels, properties).
- **Prefer webhooks to polling.**
- **Queue bursts.** When a webhook burst triggers many outbound calls, a worker with
  limited concurrency drains the queue at an allowed rate.

```python
import asyncio

slack_semaphore = asyncio.Semaphore(1)  # chat.postMessage: ~1 message/second/channel


async def post_alert(client, channel: str, text: str) -> None:
    async with slack_semaphore:
        await client.chat_postMessage(channel=channel, text=text)
        await asyncio.sleep(1)
```

## Tests to write

- A `429` with `Retry-After: 2` waits about two seconds and then retries.
- A burst of N events results in at most the allowed number of concurrent outbound calls.
