-- Product / menu item a fact is about (e.g. "Tonkotsu ramen", "Pro plan"), so prices can be compared item by item.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS item text NOT NULL DEFAULT '';
-- Knowledge growth per research over time (fresh hunt, enrich rounds, chat questions): the "it keeps improving" curve.
CREATE TABLE IF NOT EXISTS knowledge_log (
  id          bigserial PRIMARY KEY,
  research_id text NOT NULL REFERENCES researches(id) ON DELETE CASCADE,
  event       text NOT NULL,            -- hunt | enrich | chat | continue
  facts       int NOT NULL,
  sources     int NOT NULL,
  priced_items int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now()
);
