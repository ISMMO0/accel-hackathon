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

**Open decision (owner: Ismail):** switch `MODEL_EXTRACT` to `claude-haiku-5-5` (and possibly
`MODEL_ANALYST` to `claude-sonnet-5-5`). Extraction is ~75% of cost; Haiku is ~40× cheaper and faster.
Set in `backend/.env`; no code change needed.

### Next phases (plan)
- **Phase 2 — speed:** target first competitor < 5s, full hunt < 60s. Measure after the model decision.
  Ideas: Keenable fetch `prompt=` extraction, fewer pages per competitor, Valkey cache (`VALKEY_URL`).
- **Phase 3 — evidence:** `scripts/eval.py` + answer key (6–8 markets with known competitors):
  recall vs plain Claude, grounding rate, price freshness, memory curve (4 related hunts getting faster/cheaper).
  Store results in Aiven Postgres; Lightdash dashboard on top.
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
