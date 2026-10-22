"""Settings, read once from the environment (.env in development).

Every external service is optional: an empty key means that provider runs in mock mode.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # keys (empty = mock)
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    keenable_api_key: str = field(default_factory=lambda: _env("KEENABLE_API_KEY"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    firecrawl_api_key: str = field(default_factory=lambda: _env("FIRECRAWL_API_KEY"))
    jina_api_key: str = field(default_factory=lambda: _env("JINA_API_KEY"))
    h_api_key: str = field(default_factory=lambda: _env("H_API_KEY"))
    h_api_url: str = field(default_factory=lambda: _env("H_API_URL"))

    # data
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    valkey_url: str = field(default_factory=lambda: _env("VALKEY_URL"))

    # models (one per job, all overridable)
    model_scout: str = field(default_factory=lambda: _env("MODEL_SCOUT", "claude-opus-5-5"))
    model_extract: str = field(default_factory=lambda: _env("MODEL_EXTRACT", "claude-opus-5-5"))
    model_analyst: str = field(default_factory=lambda: _env("MODEL_ANALYST", "claude-opus-5-5"))
    model_strategist: str = field(default_factory=lambda: _env("MODEL_STRATEGIST", "claude-opus-5-5"))
    model_chat: str = field(default_factory=lambda: _env("MODEL_CHAT", "claude-opus-5-5"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "text-embedding-3-small"))
    # thinking effort per job (low = fastest; raise if quality drops)
    effort_scout: str = field(default_factory=lambda: _env("EFFORT_SCOUT", "low"))
    effort_analyst: str = field(default_factory=lambda: _env("EFFORT_ANALYST", "low"))
    effort_strategist: str = field(default_factory=lambda: _env("EFFORT_STRATEGIST", "low"))

    # limits
    max_competitors: int = field(default_factory=lambda: int(_env("MAX_COMPETITORS", "8")))
    pages_per_company: int = field(default_factory=lambda: int(_env("PAGES_PER_COMPANY", "4")))
    page_char_limit: int = field(default_factory=lambda: int(_env("PAGE_CHAR_LIMIT", "12000")))
    hunt_timeout_s: int = field(default_factory=lambda: int(_env("HUNT_TIMEOUT_S", "240")))
    researcher_timeout_s: int = field(default_factory=lambda: int(_env("RESEARCHER_TIMEOUT_S", "30")))
    llm_concurrency: int = field(default_factory=lambda: int(_env("LLM_CONCURRENCY", "8")))
    fetch_concurrency: int = field(default_factory=lambda: int(_env("FETCH_CONCURRENCY", "12")))
    h_concurrency: int = field(default_factory=lambda: int(_env("H_CONCURRENCY", "3")))
    memory_threshold: float = field(default_factory=lambda: float(_env("MEMORY_THRESHOLD", "0.6")))

    # behaviour
    mock_delay: float = field(default_factory=lambda: float(_env("MOCK_DELAY", "1")))
    seed_demo: bool = field(default_factory=lambda: _env("SEED_DEMO", "1") == "1")
    watch_interval_min: int = field(default_factory=lambda: int(_env("WATCH_INTERVAL_MIN", "0")))
    geocode_user_agent: str = field(default_factory=lambda: _env("GEOCODE_USER_AGENT", "launch-spy-hackathon/0.1"))

    embed_dim: int = 1536

    def live(self) -> dict[str, bool]:
        """Which providers are real vs mock. Shown in /api/health and the UI status pill."""
        # Without a Claude key the whole hunt runs on fixtures (mocking the LLM over real pages is meaningless).
        # With a Claude key: search falls back to Claude's own web search tool, fetch to keyless Jina Reader.
        return {
            "llm": bool(self.anthropic_api_key),
            "search": bool(self.anthropic_api_key),
            "fetch": bool(self.anthropic_api_key),
            "browser": bool(self.h_api_key and self.h_api_url),
            "embed": bool(self.openai_api_key),
            "geocode": bool(self.anthropic_api_key),  # real geocoding is only worth it with real data
            "database": bool(self.database_url),
            "cache": bool(self.valkey_url),
        }


settings = Settings()
