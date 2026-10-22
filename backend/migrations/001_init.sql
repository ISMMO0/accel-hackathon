-- Launch Spy schema. Idempotent: safe to run on every startup.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS researches (
  id            text PRIMARY KEY,
  query         text NOT NULL,
  query_embedding vector(1536),
  market_label  text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'running',      -- running | done | failed | interrupted
  mode          text NOT NULL DEFAULT 'fresh',        -- fresh | continue
  market        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- axes, gap, verdict, plan (analyst + strategist output)
  totals        jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_recorded   boolean NOT NULL DEFAULT false,       -- seeded/recorded vs live run (the brief asks us to say which)
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  seen_at       timestamptz NOT NULL DEFAULT now()
);

-- One row per real company, shared by every research that finds it: this is the long-term memory.
CREATE TABLE IF NOT EXISTS companies (
  id            text PRIMARY KEY,
  domain        text NOT NULL UNIQUE,
  name          text NOT NULL,
  location      text NOT NULL DEFAULT '',
  lat           double precision,
  lng           double precision,
  first_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_companies (
  research_id   text NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
  company_id    text NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  similarity    double precision NOT NULL DEFAULT 0.5,
  angle         double precision NOT NULL DEFAULT 0,
  position      int NOT NULL DEFAULT 0,
  view          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- dossier shown in the UI (analysis + strategy)
  PRIMARY KEY (research_id, company_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
  id            text PRIMARY KEY,
  company_id    text NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  url           text NOT NULL,
  content_hash  text NOT NULL,
  text          text NOT NULL,
  fetcher       text NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS snapshots_company_url ON snapshots (company_id, url, fetched_at DESC);

-- Every fact must point at the snapshot it came from: grounding is enforced by the schema.
CREATE TABLE IF NOT EXISTS facts (
  id            text PRIMARY KEY,
  company_id    text NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  research_id   text REFERENCES researches(id) ON DELETE SET NULL,
  snapshot_id   text NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  kind          text NOT NULL,
  text          text NOT NULL,
  value         text NOT NULL DEFAULT '',
  source        text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS facts_company ON facts (company_id);
-- The same fact read twice (re-research, continue mode) is stored once.
CREATE UNIQUE INDEX IF NOT EXISTS facts_dedupe ON facts (company_id, md5(text));

-- Append-only agent log: drives the live feed, replays after refresh, and is the audit trail.
CREATE TABLE IF NOT EXISTS events (
  id            bigserial PRIMARY KEY,
  research_id   text NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
  agent         text NOT NULL,
  type          text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_research ON events (research_id, id);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            bigserial PRIMARY KEY,
  research_id   text NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
  role          text NOT NULL,
  body          jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
  id            bigserial PRIMARY KEY,
  company_id    text NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  text          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id            bigserial PRIMARY KEY,
  research_id   text,
  agent         text NOT NULL,
  model         text NOT NULL,
  in_tokens     int NOT NULL,
  out_tokens    int NOT NULL,
  ms            int NOT NULL,
  cost_usd      double precision NOT NULL,
  ok            boolean NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
