"""Small key/value cache: Valkey when VALKEY_URL is set, in-process dict otherwise."""
import json
import time
from typing import Any

from ..config import settings

_mem: dict[str, tuple[float, str]] = {}
_client = None


async def _valkey():
    global _client
    if _client is None and settings.valkey_url:
        import redis.asyncio as redis  # Valkey speaks the Redis protocol

        _client = redis.from_url(settings.valkey_url, decode_responses=True)
    return _client


async def get(key: str) -> Any | None:
    try:
        if c := await _valkey():
            raw = await c.get(key)
            return json.loads(raw) if raw else None
    except Exception:
        pass  # the cache is an optimisation; never fail a hunt because of it
    hit = _mem.get(key)
    if hit and hit[0] > time.time():
        return json.loads(hit[1])
    return None


async def set(key: str, value: Any, ttl_s: int = 86400) -> None:
    raw = json.dumps(value)
    try:
        if c := await _valkey():
            await c.set(key, raw, ex=ttl_s)
            return
    except Exception:
        pass
    _mem[key] = (time.time() + ttl_s, raw)
