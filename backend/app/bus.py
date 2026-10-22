"""Agent events: persisted first, then pushed to live listeners.

Every event is written to the store before anyone sees it, so a browser that refreshes or reconnects
replays exactly what happened. Listeners are in-process (one app instance); the SSE route waits on
`wait()` and then reads new rows from the store.
"""
import asyncio
from collections import defaultdict

from .store import get_store

_conds: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)


async def emit(research_id: str, agent: str, type: str, msg: str = "", src: str = "", **data) -> dict:
    """msg/src are the human-readable feed line; data carries the structured fields the UI animates."""
    payload = {"msg": msg, "src": src, **data}
    event = await get_store().add_event(research_id, agent, type, payload)
    cond = _conds[research_id]
    async with cond:
        cond.notify_all()
    return event


async def wait(research_id: str, timeout: float) -> None:
    cond = _conds[research_id]
    async with cond:
        try:
            await asyncio.wait_for(cond.wait(), timeout)
        except asyncio.TimeoutError:
            pass
