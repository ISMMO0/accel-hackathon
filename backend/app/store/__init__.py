from ..config import settings
from .memory import MemoryStore

_store = None


def get_store():
    """Postgres when DATABASE_URL is set (Aiven), in-memory otherwise."""
    global _store
    if _store is None:
        if settings.database_url:
            from .postgres import PgStore

            _store = PgStore(settings.database_url)
        else:
            _store = MemoryStore()
    return _store
