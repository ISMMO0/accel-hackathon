"""Small key/value cache: Valkey when VALKEY_URL is set, in-process dict otherwise.

Same get/set either way. Valkey (open-source Redis, on Aiven) is shared by every process and survives a
restart; the dict is enough for one process. What is cached, with its TTL:
  search results (6 h) · fetched pages (6 h) · geocoding (30 days) · LLM responses (24 h, llm.py)
  · chat answers by meaning (24 h, agents/chat_graph.py)
"""
import json
import time
from typing import Any

from ..config import settings

_mem: dict[str, tuple[float, str]] = {}
MAX_ITEMS = 5000  # in-process mode: drop the oldest entries beyond this
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
    if len(_mem) > MAX_ITEMS:  # dicts keep insertion order: the first keys are the oldest
        for k in list(_mem)[: len(_mem) - MAX_ITEMS]:
            del _mem[k]
