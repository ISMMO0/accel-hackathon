"""Agent events: shown live immediately, persisted to the store in the background.

Agents never wait on the database: emit() records the event in memory, wakes live listeners, and queues
it for a background writer that inserts batches into Postgres (with retries). Event ids are assigned
here (monotonic), so the live stream and the stored rows share the same ids and a browser that
reconnects with ?after=<id> resumes exactly where it was, even after a restart (from the store).
"""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from .store import get_store

log = logging.getLogger("bus")
_conds: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)
_cache: dict[str, list[dict]] = defaultdict(list)  # events emitted by this process, per research
_complete: set[str] = set()  # researches created in this process: their cache holds every event
_queue: asyncio.Queue | None = None
_writer: asyncio.Task | None = None
_last_id = 0


def _next_id() -> int:
    global _last_id
    _last_id = max(time.time_ns() // 1000, _last_id + 1)  # microseconds: unique and increasing
    return _last_id


def _ensure_writer():
    global _queue, _writer
    if _queue is None:
        _queue = asyncio.Queue()
    if _writer is None or _writer.done():
        _writer = asyncio.create_task(_write_loop())


async def _write_loop():
    while True:
        batch = [await _queue.get()]
        await asyncio.sleep(0.25)  # let a few more events arrive, then write them together
        while not _queue.empty() and len(batch) < 500:
            batch.append(_queue.get_nowait())
        for attempt in range(5):
            try:
                await get_store().add_events(batch)
                break
            except Exception as e:  # flaky network: back off and retry, never drop silently
                log.warning("event write failed (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(0.5 * 2 ** attempt)
        else:
            log.error("dropped %d events after retries", len(batch))
        for _ in batch:
            _queue.task_done()


async def emit(research_id: str, agent: str, type: str, msg: str = "", src: str = "", **data) -> dict:
    """msg/src are the human-readable feed line; data carries the structured fields the UI animates."""
    _ensure_writer()
    e = {"id": _next_id(), "research_id": research_id, "agent": agent, "type": type,
         "payload": {"msg": msg, "src": src, **data}, "created_at": datetime.now(timezone.utc)}
    _cache[research_id].append(e)
    await _queue.put(e)
    cond = _conds[research_id]
    async with cond:
        cond.notify_all()
    return e


def new_research(research_id: str) -> None:
    _complete.add(research_id)


async def events_after(research_id: str, after: int) -> list[dict]:
    cached = [e for e in _cache.get(research_id, []) if e["id"] > after]
    if research_id in _complete:
        return cached
    rows = await get_store().events_after(research_id, after)  # older runs (before a restart)
    top = rows[-1]["id"] if rows else after
    return rows + [e for e in cached if e["id"] > top]  # plus events not yet written


async def last_event_id(research_id: str) -> int:
    if _cache.get(research_id):
        return _cache[research_id][-1]["id"]
    return await get_store().last_event_id(research_id)


async def flush():
    """Wait until every queued event is stored (tests, shutdown)."""
    if _queue is not None:
        await _queue.join()


async def wait(research_id: str, timeout: float) -> None:
    cond = _conds[research_id]
    async with cond:
        try:
            await asyncio.wait_for(cond.wait(), timeout)
        except asyncio.TimeoutError:
            pass
