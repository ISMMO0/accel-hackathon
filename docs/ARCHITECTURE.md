# Launch Spy — Architecture

Status: implemented in `backend/` (mock mode tested end to end on memory and Postgres stores), 22 Oct 2026.

## 1. Goals and constraints

- A hunt (query → competitors → facts → verdict) finishes in **under 60 s**, shows its first competitor in **under 5 s**, and streams every step live.
- Nothing is lost: every step is persisted before it's shown. A browser refresh replays the run.
- Every claim is **grounded**: it points to a stored page snapshot (URL + fetch time).
- Runs fully **without API keys** (mock mode) so we can build and demo the UI before keys arrive.
- One deployable service on **Aiven Runtime**; data on **Aiven Postgres (+pgvector)** and **Aiven Valkey**.
- Build time tomorrow: ~6 h. Simple beats clever.

## 2. Shape

```
Browser (static HTML/JS, no build step)
   │  REST (JSON)            SSE /api/research/{id}/events
   ▼
FastAPI app (one process, asyncio)
   ├─ api/          routes: research, chat, library, map, watcher
   ├─ pipeline/     orchestrator + agents (scout, researcher, analyst, strategist, watcher)
   ├─ providers/    llm, search, fetch, browser(H), embed, geocode  ← each has real + mock
   ├─ store/        Postgres repo + event bus (LISTEN/NOTIFY)
   └─ scheduler     Watcher loop (in-process)
   │
   ├── Aiven Postgres + pgvector  (source of truth)
   └── Aiven Valkey               (page cache, rate limits; optional)
```

Single process on purpose: no queue workers, no microservices. Agents are asyncio tasks inside the app. If the process restarts mid-hunt, the research is marked `interrupted` and can be resumed (facts already saved are kept).

## 3. The hunt pipeline

```
POST /api/research {query}
  0. Memory      embed(query) → nearest past research (cosine ≥ 0.85) → return match, UI asks Continue / Fresh
  1. Scout       LLM + search (3–4 queries in parallel) → candidate list → dedupe by domain → top 8
                 emits competitor_found as each one is confirmed (UI shows nodes immediately)
  2. Researchers one task per competitor, all in parallel (bounded):
                 pages = [home, pricing, careers, reviews/reddit]
                 fetch chain: cache → Jina (fast) → Firecrawl (JS) → H Company (hard, max 3 concurrent)
                 store snapshot (hash) → LLM extract (cheap model, structured) → facts with snapshot_id
  3. Analyst     LLM over stored facts only: positioning scores (0–1 axes it names), price table,
                 review themes, location → geocode
  4. Strategist  LLM over analyst output: gap, verdict, how-to-beat per competitor
  5. Done        status=done, totals, cost/latency recorded
```

The Analyst runs once over all competitors after the researchers finish, so every company is placed on the same axes. Researchers each have their own timeout, so one slow site can't hold the others.

**Time budget:** Scout ≤ 10 s, each Researcher ≤ 25 s (hard timeout, partial facts kept), Analyst/Strategist ≤ 15 s. Overall hard cap 90 s → whatever is done is shown, the rest flagged `incomplete`.

**Continue mode:** reuse competitors + facts; Watcher-style diff on each competitor's pages (hash compare); only re-extract pages that changed. This is our measurable "memory makes it faster/cheaper" proof.

## 4. LLM usage

| Job | Model (env var) | Effort | Output |
|---|---|---|---|
| Scout (pick competitors from search results) | `MODEL_SCOUT` | medium | `ScoutResult` |
| Page → facts extraction | `MODEL_EXTRACT` | low | `PageExtraction` |
| Analyst (positioning, themes) | `MODEL_ANALYST` | medium | `Analysis` |
| Strategist (verdict, how to win) | `MODEL_STRATEGIST` | high | `Strategy` |
| Report chat | `MODEL_CHAT` | medium | text + tool steps |

All default to `claude-opus-5-5`. Switching extraction to `claude-haiku-5-5` is a one-line `.env` change if speed or cost needs it. Server-side refusal fallbacks are enabled on every call.

Rules:
- **Structured output only** (tool/JSON schema validated with Pydantic). A response that fails validation is retried once, then the step degrades gracefully.
- **Citations enforced in code:** every fact must carry a `snapshot_id` that exists; uncited facts are dropped, not shown.
- **Untrusted input:** scraped pages go to the LLM as quoted data inside a clear delimiter with an instruction that page text is never instructions. Extraction agents have **no action tools** (no email, no writes beyond returning JSON), so a malicious page can't make them do anything.
- **Truncate before sending:** pages are cleaned to main text and capped (~6k tokens) before extraction.
- **Prompt caching** on the long static system prompts.
- **Every call is logged** to `llm_calls` (agent, model, tokens in/out, latency, cost). This feeds the cost/speed numbers for the pitch.
- Model IDs live in config, not in code.

## 5. Data model (Postgres)

```
researches   id, query, query_embedding vector, market_label, status(running|done|failed|interrupted),
             mode(fresh|continue), parent_id, verdict jsonb, axes jsonb, totals jsonb,
             started_at, finished_at, is_recorded bool
companies    id, domain UNIQUE, name, hq_city, lat, lng, logo_url, first_seen_at        ← shared across researches
research_companies  research_id, company_id, similarity, angle, position jsonb, dossier jsonb (strategist output)
snapshots    id, company_id, url, kind(home|pricing|careers|reviews|other), content_hash, text, fetched_at, fetcher
facts        id, company_id, research_id, snapshot_id, kind(price|feature|hiring|review|location|other),
             text, value jsonb, embedding vector, confidence, created_at
events       id BIGSERIAL, research_id, agent, type, payload jsonb, created_at      ← drives the live feed
chat_messages id, research_id, role, content, steps jsonb, citations jsonb, created_at
alerts       id, company_id, kind, before jsonb, after jsonb, seen bool, created_at
llm_calls    id, research_id, agent, model, in_tokens, out_tokens, ms, cost_usd, ok, created_at
```

Why this shape:
- **`companies` keyed by domain** → "Hinge" found in two researches is one company; its snapshots, facts and alerts accumulate. That *is* the long-term memory.
- **`snapshots` with `content_hash`** → change detection is a hash compare; identical refetches cost nothing.
- **`facts.snapshot_id` NOT NULL** → grounding is enforced by the schema.
- **`events` append-only** → live feed, replay after refresh, and an audit trail of what each agent did.
- **`is_recorded`** → the brief asks us to say which results are live vs recorded; the UI shows a badge.

Indexes: `events(research_id, id)`, `facts(company_id)`, `snapshots(company_id, url, fetched_at desc)`, HNSW on `researches.query_embedding` and `facts.embedding`.

## 6. Live events contract (backend ↔ frontend)

SSE stream `GET /api/research/{id}/events?after={last_id}`. Each message:

```json
{ "id": 1287, "agent": "researcher", "type": "fact_added",
  "payload": { "company_id": "...", "count": 3, "text": "...", "source": "hinge.co/pricing", "fetcher": "h" },
  "at": "2026-10-22T09:14:03Z" }
```

Types: `run_started`, `memory_match`, `agent_status`, `search`, `competitor_found`, `page_fetched`, `fact_added`, `change_detected`, `insight`, `run_done`, `run_error`.

The frontend renders the constellation, roster and feed **only from events**, so live runs, replays and recorded demos all use the same code path. Delivery: insert row → wake in-process listeners (`bus.py`) → SSE handler reads rows `id > last_id`, with a 1 s re-read as a safety net. One app instance means no cross-process notify is needed; if we ever run several, switch the wake-up to Postgres `LISTEN/NOTIFY` on a direct (non-pooled) Aiven connection.

## 7. REST API

```
POST /api/research                 {query, mode?: "fresh"|"continue", parent_id?} → {id} or {match}
GET  /api/research/{id}            full report: companies, positions, facts, verdict, totals
GET  /api/research/{id}/events     SSE
GET  /api/library                  list with thumbnails data + unseen alert counts
POST /api/research/{id}/chat       {message} → streams steps + answer (SSE)
GET  /api/map/{research_id}        companies with lat/lng
POST /api/watcher/run              check now (for the live demo)
GET  /api/health                   which providers are live vs mock
```

## 8. Providers (real + mock)

| Provider | Real | Mock (no key) |
|---|---|---|
| llm | Anthropic SDK | deterministic outputs from fixture datasets |
| search | Tavily (or Exa) | fixture search results |
| fetch | Jina Reader → Firecrawl | fixture page texts |
| browser | H Company API (max 3 concurrent) | fixture page texts, slower delay |
| embed | OpenAI text-embedding-3-small (1536) | hash-based pseudo-embedding (same dimension) |
| geocode | Nominatim (free, 1 req/s, cached forever) | fixture coords |

Each provider is chosen independently: paste one key tomorrow and only that provider goes live. `/api/health` and the UI's status pill show which are live.

Resilience: per-provider semaphore + timeout + 1 retry with backoff; fetch chain falls back down the list; a failed competitor never fails the run.

## 9. Watcher

- In-process loop every N minutes (config; 1 min in demo mode): for each watched company, refetch pricing/careers pages, compare hash, extract diff with the LLM only if changed, write `alerts` + `change_detected` events.
- `POST /api/watcher/run` triggers it on demand.
- **Live demo:** a tiny fake competitor site we control (`/demo-site/pricing`), so we can change a price on stage and show the alert within seconds.

## 10. Speed and cost

- Parallel researchers; streaming first results; cache every fetch in Valkey (24 h) and snapshots in Postgres.
- Cheap model for bulk extraction, expensive model once per run.
- Hard caps: 8 competitors, 4 pages each, 6k tokens per page.
- Target per fresh hunt: ~40 LLM calls, < $0.50. Continue mode: < 10 calls.

## 11. Proof it works (judging criterion 4)

- `scripts/eval.py`: 10 markets with a hand-checked list of real competitors.
  - **Recall:** share of known competitors found (Launch Spy vs a single Claude call without tools).
  - **Grounding:** share of displayed facts with a valid source (should be 100% by construction).
  - **Freshness:** prices checked against today's page vs the model's memory.
  - **Memory:** time and cost of fresh vs continue.
- Output a small table + chart for the pitch slide.

## 12. Deployment

- `Dockerfile` (python slim, uvicorn), reads `PORT`.
- Migrations run on startup (idempotent SQL files).
- Aiven Postgres needs `sslmode=require`; small pool (5) — free plans have low connection limits; one extra direct connection for LISTEN.
- Static frontend served by FastAPI, same origin → no CORS, keys never reach the browser.
- Local dev: `docker compose up` (pgvector Postgres + Valkey) or point at Aiven.

## 13. Demo safety

- **Seed script** loads 2–3 complete researches (marked recorded) so the library, map and report look full from minute one.
- **Replay mode:** any finished research can be replayed from its events at real speed, a fallback if Wi-Fi or an API fails on stage.

## 14. Out of scope for the hackathon

Auth and multi-user (single workspace), billing, mobile polish, Kafka/ClickHouse, scraping LinkedIn/G2.
