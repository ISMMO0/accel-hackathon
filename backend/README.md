# Launch Spy backend

FastAPI app that runs the agent team and serves the UI. Works with zero keys (recorded data), then
switches to live agents provider by provider as you paste keys into `.env`.

## Run it

```bash
cd backend
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env        # fill in keys when you have them
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. The status pill top right says **Recorded data** or **Live agents**;
hover it to see which providers are live. `GET /api/health` returns the same.

Check everything end to end (no server needed):

```bash
.venv/bin/python -m scripts.smoke
```

## Morning checklist (hackathon day)

1. **Anthropic key** → `ANTHROPIC_API_KEY`. This turns on live agents (Claude web search is used until Tavily is set).
2. **Aiven**: create a PostgreSQL service (pgvector is available on Aiven Postgres) → copy the Service URI into `DATABASE_URL`. Tables are created on startup.
3. **Aiven Valkey** (optional) → `VALKEY_URL`.
4. **OpenAI key** → `OPENAI_API_KEY` for embeddings (memory matching).
5. **Keenable** (sign up at app.keenable.ai, code `ACCEL-PARIS`, $1,000 credits) → `KEENABLE_API_KEY`. Main search + page reader; works keyless at lower limits. Tavily is an optional fallback.
6. **H Company**: create a new API key named `accel-hackathon`, read their API docs, set `H_API_KEY` + `H_API_URL`, then adjust `_h_browser()` in `app/providers/web.py` to the real request/response shape.
7. Run one live hunt, check the report, then run `scripts/smoke.py` again.
8. Deploy on Aiven App Runtime with the `Dockerfile` (set the same env vars there).

## How it fits together

```
app/
  main.py            routes, SSE stream, startup (migrations, demo seed, Watcher loop)
  config.py          all settings + which providers are live
  schemas.py         structured outputs each agent must return (Pydantic → JSON schema)
  bus.py             emit(): persist an event, then wake live listeners
  views.py           rows → JSON the UI renders
  pipeline/hunt.py   Scout → parallel Researchers → Analyst → Strategist; Continue mode; Watcher
  pipeline/chat.py   report chat: Claude + tools (search_facts, dig_into_company, add_company)
  providers/llm.py   Claude calls per job + MockLLM (fixtures)
  providers/web.py   search (Keenable → Tavily → Claude), fetch (cache → Keenable → Jina → Firecrawl → H), embeddings, geocoding
  providers/cache.py Valkey or in-process cache
  store/             PgStore (Aiven Postgres + pgvector) and MemoryStore, same interface
  static/            the UI (index.html + app.js), no build step
migrations/001_init.sql
scripts/smoke.py     end-to-end test
```

API:

| Method | Path | What |
|---|---|---|
| POST | `/api/research` | `{query}` → `{match}` if a similar research exists, else starts a hunt → `{id, after}`. Pass `mode: "fresh"` or `mode: "continue", parent_id` to choose. |
| GET | `/api/research/{id}` | Full report (market, competitors, facts, chat). `?seen=true` clears change alerts. |
| GET | `/api/research/{id}/events?after=N` | Live agent events (Server-Sent Events). Replays from `N`, so refreshes lose nothing. |
| POST | `/api/research/{id}/chat` | `{message}` → agent steps, answer, citations, what to highlight. |
| GET | `/api/library` | All researches with thumbnail data and unseen alerts. |
| POST | `/api/watcher/run` | Run the Watcher now (for the live demo). |
| GET | `/api/health` | Store type and live/mock status per provider. |

## Notes

- **Models**: every job defaults to `claude-opus-5-5` (with server-side refusal fallbacks enabled). Set `MODEL_EXTRACT=claude-haiku-5-5` in `.env` if page reading needs to be faster or cheaper.
- **Grounding**: a fact can't be stored without the page snapshot it came from (`facts.snapshot_id NOT NULL`).
- **Memory**: companies are shared across researches by domain; facts are deduplicated; unchanged pages are skipped by content hash.
- **Untrusted pages**: scraped text is passed to Claude inside `<page>` tags as data; extraction agents have no action tools.
- **Recorded vs live**: researches made without a Claude key are marked *recorded data* in the UI, as the brief asks.
