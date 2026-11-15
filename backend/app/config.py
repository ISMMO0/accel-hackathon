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


# Which company's models run the agents: "anthropic" (Claude) or "openai" (GPT). Web search and page
# reading stay on Keenable either way; embeddings stay on OpenAI.
LLM_PROVIDER = _env("LLM_PROVIDER", "anthropic").lower()

# Default model per job and provider. A cheap, fast model for the many small calls (reading pages,
# watching changes), a stronger one for the few calls that reason over the whole market.
DEFAULT_MODELS = {
    "anthropic": {"scout": "claude-opus-5-5", "extract": "claude-opus-5-5", "analyst": "claude-opus-5-5",
                  "strategist": "claude-opus-5-5", "chat": "claude-opus-5-5"},
    "openai": {"scout": "gpt-5.4-mini", "extract": "gpt-5.4-nano", "analyst": "gpt-5.4-mini",
               "strategist": "gpt-5.4-mini", "chat": "gpt-5.4-mini"},
}


def _model(job: str) -> str:
    return _env(f"MODEL_{job.upper()}", DEFAULT_MODELS.get(LLM_PROVIDER, DEFAULT_MODELS["anthropic"])[job])


@dataclass(frozen=True)
class Settings:
    # keys (empty = mock)
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    keenable_api_key: str = field(default_factory=lambda: _env("KEENABLE_API_KEY"))
    gradium_api_key: str = field(default_factory=lambda: _env("GRADIUM_API_KEY"))
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY") or _env("ELEVENLABS"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    firecrawl_api_key: str = field(default_factory=lambda: _env("FIRECRAWL_API_KEY"))
    jina_api_key: str = field(default_factory=lambda: _env("JINA_API_KEY"))
    h_api_key: str = field(default_factory=lambda: _env("H_API_KEY"))
    h_api_url: str = field(default_factory=lambda: _env("H_API_URL"))

    # data
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    mcp_token: str = field(default_factory=lambda: _env("MCP_TOKEN"))  # required on /mcp when set (public deploys)
    valkey_url: str = field(default_factory=lambda: _env("VALKEY_URL"))

    # models (one per job, all overridable with MODEL_<JOB>)
    llm_provider: str = LLM_PROVIDER
    model_scout: str = field(default_factory=lambda: _model("scout"))
    model_extract: str = field(default_factory=lambda: _model("extract"))
    model_analyst: str = field(default_factory=lambda: _model("analyst"))  # Analyst
    model_strategist: str = field(default_factory=lambda: _model("strategist"))  # Strategist + Planner
    model_chat: str = field(default_factory=lambda: _model("chat"))
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
    llm_cache_hours: int = field(default_factory=lambda: int(_env("LLM_CACHE_HOURS", "24")))  # 0 = off
    memory_threshold: float = field(default_factory=lambda: float(_env("MEMORY_THRESHOLD", "0.6")))

    # behaviour
    mock_delay: float = field(default_factory=lambda: float(_env("MOCK_DELAY", "1")))
    seed_demo: bool = field(default_factory=lambda: _env("SEED_DEMO", "1") == "1")
    watch_interval_min: int = field(default_factory=lambda: int(_env("WATCH_INTERVAL_MIN", "0")))
    # Upgrader agent: every N minutes, enrich the research that has gone longest without new data (0 = off)
    upgrade_interval_min: int = field(default_factory=lambda: int(_env("UPGRADE_INTERVAL_MIN", "10")))
    geocode_user_agent: str = field(default_factory=lambda: _env("GEOCODE_USER_AGENT", "launch-spy-hackathon/0.1"))

    embed_dim: int = 1536  # text-embedding-3-small; the pgvector column is vector(1536)

    @property
    def llm_key(self) -> str:
        """The key of the provider that runs the agents (empty = recorded demo data)."""
        return self.openai_api_key if self.llm_provider == "openai" else self.anthropic_api_key

    def live(self) -> dict[str, bool]:
        """Which providers are real vs mock. Shown in /api/health and the UI status pill."""
        # Without an LLM key the whole hunt runs on fixtures (mocking the LLM over real pages is meaningless).
        # With one: search uses Keenable (falls back to Claude's web search), fetch falls back to keyless Jina.
        return {
            "llm": bool(self.llm_key),
            "search": bool(self.llm_key),
            "fetch": bool(self.llm_key),
            "browser": bool(self.h_api_key and self.h_api_url),
            "embed": bool(self.openai_api_key),
            "geocode": bool(self.llm_key),  # real geocoding is only worth it with real data
            "voice": bool(self.elevenlabs_api_key or self.gradium_api_key),
            "database": bool(self.database_url),
            "cache": bool(self.valkey_url),
        }


settings = Settings()
