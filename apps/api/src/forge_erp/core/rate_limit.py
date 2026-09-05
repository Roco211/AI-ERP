from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from forge_erp.core.config import settings
from forge_erp.core.errors import Problem
from forge_erp.core.security import fingerprint


async def check_login_rate(client_ip: str) -> None:
    # No trust of attacker-controlled Forwarded/X-Forwarded-For headers.
    try:
        async with Redis.from_url(settings().redis_url) as redis:
            pending = redis.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1,
                "forge:login:" + fingerprint(client_ip),
            )
            count = await cast(Awaitable[int], pending)
    except RedisError as exc:
        raise Problem(503, "AUTH_UNAVAILABLE", "Sign-in temporarily unavailable") from exc
    if int(count) > 30:
        raise Problem(429, "RATE_LIMITED", "Too many login attempts; retry in one minute")
