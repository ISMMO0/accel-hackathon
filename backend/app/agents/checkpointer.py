"""The checkpointer: where LangGraph saves the state after every step.

- With DATABASE_URL (Aiven): AsyncPostgresSaver, in the same Postgres as our data (its own tables,
  created by setup()). A run cut by a crash or restart resumes from its last finished step.
- Without it: InMemorySaver (lost on restart; fine for local mock runs and tests).
"""
import logging

from langgraph.checkpoint.memory import InMemorySaver

from ..config import settings

log = logging.getLogger("checkpointer")
_saver = None
_pool = None


async def open_checkpointer():
    """Called once at startup (FastAPI lifespan)."""
    global _saver, _pool
    if not settings.database_url:
        _saver = InMemorySaver()
        return _saver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # Small pool: the Aiven plan allows ~15 connections and asyncpg (our store) already uses up to 10.
    _pool = AsyncConnectionPool(settings.database_url, min_size=1, max_size=3, open=False,
                                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await _pool.open()
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()  # creates the checkpoint tables once (idempotent)
    log.info("LangGraph checkpoints in Postgres")
    return _saver


def get_checkpointer():
    global _saver
    if _saver is None:  # scripts and tests that skip the lifespan
        _saver = InMemorySaver()
    return _saver


async def close_checkpointer():
    if _pool is not None:
        await _pool.close()
