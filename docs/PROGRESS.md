# Launch Spy: build progress and handoff

Living document, updated after every phase so any engineer or coding agent (Claude Code, Codex…) can pick
up the work with full context. Read this first, then `docs/ARCHITECTURE.md` and `backend/README.md`.

**Event:** Accel AI Innovate Paris, Thursday 22 October 2026. **Submission deadline: 15:00.** Top-8 pitches 16:00.
**Repo:** github.com/ISMMO0/accel-hackathon (private). Work happens on branch **`backend`** (not pushed yet).

---

## 1. The product in one paragraph

**Launch Spy**: a founder (or VC) describes a business idea, e.g. "a specialty coffee shop in Paris 11e".
A team of AI agents finds the real competitors on the live web, reads their websites and review pages,
places them on a market map (price × positioning), finds the empty spot ("the gap"), and writes how to beat
each one. Every fact links to the page it came from. Competitors are then **watched over time**: changed
pages raise alerts. Past research is reused ("Continue research"), which makes later hunts faster and cheaper.

Judging (20% each): customer problem & value · agent capability · data at the core · evidence it works ·
execution & pitch. Aiven gives a separate prize for best use of its platform. Using sponsor credits counts.

## 2. Where things are

```
accel hackathon/
  docs/ARCHITECTURE.md      system design (data model, pipeline, events contract, LLM rules)
  docs/PROGRESS.md          ← this file
  PRODUCT.md                product context for design work
  mockup/index.html         original clickable mockup (fixture data only, kept for reference)
  backend/                  the real app (FastAPI + static UI)
    .env                    keys (git-ignored, NEVER commit) — template in .env.example
    app/main.py             routes, SSE stream, startup (migrations, demo seed, Watcher loop)
    app/config.py           settings; settings.live() says which providers are real vs mock
    app/schemas.py          Pydantic structured outputs for each agent
    app/pipeline/hunt.py    Scout → Researchers (parallel) → Analyst → Strategist; Continue; Watcher
    app/pipeline/chat.py    report chat (Claude + tools: search_facts, dig_into_company, add_company)
    app/providers/llm.py    Claude calls (ClaudeLLM) + fixture MockLLM, same interface
    app/providers/web.py    search (Keenable→Tavily→Claude), fetch (cache→Keenable→Jina→Firecrawl→H),
                            embeddings (OpenAI or hash), geocoding (Nominatim)
    app/store/              PgStore (Aiven Postgres + pgvector) / MemoryStore, same interface
    app/static/             UI: index.html (markup+CSS from mockup) + app.js (API + SSE driven)
    migrations/001_init.sql schema, idempotent, runs on startup
    scripts/smoke.py        end-to-end test in mock mode (no keys needed)
    scripts/live_run.py     runs one real hunt against the server, prints timeline + per-agent cost
```

## 3. Accounts, credits and keys (status)

| Service | Credits | In `backend/.env` | Used for |
|---|---|---|---|
| Anthropic (Claude) | $100 | `ANTHROPIC_API_KEY` ✅ | all agents + chat |
| OpenAI | $50 | `OPENAI_API_KEY` ✅ | embeddings only (memory match) |
| Keenable | $1,000 | `KEENABLE_API_KEY` ✅ | web search + page fetch (main provider) |
| Aiven | $50 trial (30 days) | `DATABASE_URL` ✅ | Postgres 18.6 + pgvector, service `pg-361f3e6c`, project `accelhackathon`, region DigitalOcean Frankfurt |
| Modal | $100 | — | not used yet (idea: run the eval in parallel) |
| Supabase | $25 | — | not used (would compete with Aiven) |
| H Company | $50 top-up, free plan (3 concurrent) | `H_API_KEY`/`H_API_URL` empty | browser agent for hard pages; request shape unverified, `_h_browser()` is a stub |
| n8n | 1 month Pro (code at checkout) | — | idea: Watcher alerts to Slack/email |
| ElevenLabs, Lightdash, Vercel, Stripe, Miro, Lovable, Port | event credits | — | ideas: voice briefing (ElevenLabs), eval dashboard on Aiven PG (Lightdash) |

Keys are only ever in `backend/.env`. Never paste them in chat, code, or commits.

## 4. How to run

```bash
cd backend
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt   # once
.venv/bin/uvicorn app.main:app --port 8000 --reload --reload-dir app                     # http://localhost:8000
.venv/bin/python -m scripts.live_run "A specialty coffee shop in Paris 11e"              # one real hunt, timed
MOCK_DELAY=0.05 ANTHROPIC_API_KEY= OPENAI_API_KEY= DATABASE_URL= KEENABLE_API_KEY= \
  .venv/bin/python -m scripts.smoke                                                      # offline regression test
```

The smoke test must run with keys blanked as above, otherwise it would write test data into Aiven and spend credits.

## 5. Phase log

### Phase 0 (night before): design + backend skeleton ✅
- Mockup → architecture → FastAPI backend with mock providers; UI wired to API + SSE.
- Smoke test passes on in-memory store and on a real local Postgres+pgvector.

### Phase 1 (morning): go live ✅
**Done**
- All keys set; Aiven Postgres connected (pgvector available); tables created automatically on startup.
- Keenable added as first-choice search (`/v1/search`, mode `realtime`) and fetch (`/v1/fetch`, `live=true`).
  Keyless public endpoints exist too (`/search/public`, needs `X-Keenable-Title`).
- First live hunts on "A specialty coffee shop in Paris 11e". Bugs found and fixed:
  1. Small businesses have no website → all candidates collapsed into one company.
     Fix: `company_key()` in hunt.py — use the domain only if it's a real own site, else `<name-slug>.place`;
     businesses without a site are researched from review/listing search results.
  2. Researchers timed out (pages extracted one by one). Fix: pages fetched and extracted in parallel;
     `RESEARCHER_TIMEOUT_S` 25→60, `HUNT_TIMEOUT_S` 90→180.
  3. Scout found only 2 cafés (search returns "best of" guides). Fix: Scout now also reads the top 3
     results' page text and extracts businesses named in guides.
  4. Duplicate facts per page. Fix: extraction prompt caps 8 most decision-relevant facts per page
     (facts are also de-duplicated in the DB by `(company_id, md5(text))`).
  5. Missing map pins. Fix: geocode the Scout's address hints in the background during research, retry
     with cleaned address / name + city.
  6. Rating 0 shown as "0★". Fix: UI shows "–" when unknown.
  7. Real embeddings score similar markets ~0.6–0.8 (unrelated ~0.2–0.45): `MEMORY_THRESHOLD` 0.85→0.6.
- Speed tweaks: Analyst gets max 15 facts per competitor; Strategist effort high→medium.

**Measured (before the speed tweaks), live hunt "specialty coffee shop Paris 11e", all models Opus 5.5**

| Step | Time | Cost |
|---|---|---|
| search + Scout (3 searches, 3 guide pages, 1 Opus call) | 14s to first competitor | $0.05 |
| Researchers (26 extraction calls, avg 7s) | 38s | $0.63 |
| Analyst (1 call, 29s) + geocoding | 48s | $0.13 |
| Strategist (1 call) | 29s | $0.05 |
| **Total** | **127s, 8 competitors, 274 facts, all geocoded** | **$0.85** |

**Decision taken:** `MODEL_EXTRACT=claude-haiku-5-5` (see Phase 2). Scout/Analyst/Strategist/chat stay Opus 5.5.

### Phase 2: speed + resilience on bad Wi-Fi ✅
**Root cause found:** venue Wi-Fi to Aiven (Frankfurt) has packet loss and 1–2s spikes (sometimes 12s to open
a connection). Sequential DB calls on the agents' path made hunts time out. LLMs were not the problem.

**Changes (all tested on memory store, local Postgres, and live on Aiven)**
- `MODEL_EXTRACT=claude-haiku-5-5` (Ismail's choice). Page reading went from $0.63 to ~$0.01 per hunt.
  Haiku has no server-side refusal fallback, so `fallbacks` is only sent for Opus/Sonnet (`llm.py`).
- Effort per job is configurable: `EFFORT_SCOUT/ANALYST/STRATEGIST` default `low` (Analyst 28s→12s, Strategist 20s→11s).
- **Events (`bus.py`)**: emitted to memory first (UI sees them instantly), written to Postgres in batches by a
  background writer with retries. Ids are monotonic microsecond timestamps, so replay with `?after=` works across
  memory and DB. `bus.flush()` waits for pending writes (used at shutdown and in tests).
- **Hunt (`hunt.py`)**: runs in memory. Every DB write goes through `later(rid, fn, after=ready)` (background
  task with retries); `settle(rid)` awaits them all before the run is marked `done`. Company ids are deterministic
  (`company_id(domain)` = `"c" + sha1(domain)[:11]`, in `store/memory.py`), so no DB round trip is needed to know
  them. LLM call logs are also written in the background.
- **Store**: `save_page()` writes snapshot + facts in ONE round trip; `report_facts()` and `library_rows()`
  load a report / the library in one query each; pool 10 (Aiven limit 15), command timeout 60s.
- **Fetching**: Keenable indexed copy first, then live; per-fetcher timeouts (10s Keenable, 8s Jina, 12s Firecrawl);
  each page has a 15s budget; researcher hard cap `RESEARCHER_TIMEOUT_S=30` (facts already found are kept);
  review search runs in parallel with the competitor's own site; `PAGE_CHAR_LIMIT=12000`.
- Scout emits "N web results, reading 3 guides" so the feed moves while it thinks.
- Aiven tables were reset once (test data only) for the deterministic company ids; demo seed re-created them.
- `uvicorn --timeout-graceful-shutdown 2`: with `--reload`, open SSE streams otherwise block the restart.

**Measured live (Wi-Fi), coffee shop Paris 11e:** first competitor 13–15s, full hunt ~86–105s, **$0.14–0.19**,
8 competitors, all geocoded. Dating market: big app sites are slow to fetch, several researchers hit the 30s cap.
Report page load 4–10s over this Wi-Fi (library 0.8s). **Biggest remaining speed win = deploy on Aiven Runtime**
(app next to the database and the APIs).

### Phase 3: evidence it works ✅ (first version)
**Built**
- `backend/scripts/eval.py` + answer key `backend/scripts/data/answer_key.json` (5 markets, 8–9 known competitors each).
  Compares Launch Spy (full live hunt through the API) with plain Claude (one call, no tools), and runs a memory
  curve ("Continue research" on the first market). Writes rows to table `eval_runs` (migration `002_eval.sql`)
  and a summary to `docs/EVAL_RESULTS.md`. Run: `cd backend && PYTHONUNBUFFERED=1 .venv/bin/python -m scripts.eval`
  (server must be running; ~12 min, ~$0.8). **Don't edit backend Python while it runs** (dev server reloads, kills hunts).
- **Evidence page** in the UI (`#/evidence`, `GET /api/eval`): KPIs, recall chart per market, memory table, all runs.
- **Page memory** (`store.page_memory()`, one query): researchers reuse facts of pages whose fingerprint
  (sha256 of text) is unchanged, with no Claude call. Emits `fact_added` with `reused: true`.
- **Hybrid Scout**: `llm.known_players()` (what Claude already knows, run in parallel with web search) is given to
  the Scout, which keeps established players and adds web-found newer/local ones.
- **Watcher**: pages compared in parallel using page memory; when a fingerprint differs, Haiku `llm.changes()`
  lists only meaningful changes vs known facts (stops false alerts from dynamic app-store/review pages).
- Continue mode reports this run's own LLM calls/cost (`base_stats`), not the research lifetime totals.
- Startup marks hunts left `running` by a restart as `interrupted`. Eval script falls back to polling if SSE drops.

**Results (live, run `20261022-1006xx`, see docs/EVAL_RESULTS.md)**
| | Launch Spy | Plain Claude |
|---|---|---|
| Known competitors found (recall) | 67% (was 50% before hybrid Scout) | 84% |
| Facts linked to a source page | 100% (840 facts) | 0% |
| Cost per market | $0.13 | $0.002 |
| Continue research (re-check a known market) | 0 new Claude calls, $0.00 when nothing changed* | – |

*Measured before semantic change detection: 172s (sequential). After the Watcher fixes: ~47–56s, and when pages
change it re-runs Analyst+Strategist (~$0.09). The Scout prompt was tightened after this eval run; re-run the eval
(ideally from the deployed server) to refresh the numbers.

**How to pitch the evidence honestly:** plain Claude is good at naming famous brands from memory; Launch Spy is
close on that (67% vs 84%) but every claim is sourced and fresh (prices read today), it finds local businesses
that a model can't know (e.g. the cafés of Paris 11e), and re-checking a market costs ~nothing when nothing changed.

### Next phases (plan)
- **Phase 3b (optional):** Lightdash dashboard on `eval_runs`; re-run eval after deploy.
- **Phase 4 — deploy + demo safety:** Aiven App Runtime (Dockerfile ready), fake competitor site +
  "Check now" Watcher button, pre-run demo researches, backup video.
- **Phase 5 — extras if time:** ElevenLabs voice briefing, n8n alert delivery, Modal-parallel eval.
- **Last 30 min:** pitch slide, rehearse, submit before 15:00 (make repo public only if required).

## 6. Known gaps / gotchas
- H Company fetcher is a stub until their API shape is confirmed (`app/providers/web.py::_h_browser`).
- Report chat in live mode (Claude + tools) has not been exercised live yet.
- Continue mode / Watcher in live mode not yet exercised live (works in mock + Postgres tests).
- Nominatim geocoding is rate-limited to 1 request/second (policy); results cached 30 days.
- Seeded demo researches are fixture data, marked "Recorded" in the UI; they live in Aiven too.
- The SSE stream ends on `run_done`/`run_error`; the UI reconnects with `?after=<last id>` for continue runs.
