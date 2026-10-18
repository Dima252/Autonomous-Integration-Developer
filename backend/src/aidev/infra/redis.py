from redis.asyncio import Redis


def create_client(url: str) -> Redis:
    return Redis.from_url(url)


async def ping(client: Redis) -> None:
    await client.ping()
