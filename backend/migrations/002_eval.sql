-- Evaluation results ("Evidence it works"): one row per system per market per eval run. Lightdash reads this.
CREATE TABLE IF NOT EXISTS eval_runs (
  id            bigserial PRIMARY KEY,
  run_id        text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  market        text NOT NULL,
  system        text NOT NULL,            -- launch_spy | plain_claude | launch_spy_fresh | launch_spy_memory
  research_id   text,
  expected      int NOT NULL DEFAULT 0,   -- competitors in the answer key
  found         int NOT NULL DEFAULT 0,   -- answer-key competitors the system named
  recall        double precision,
  named         jsonb NOT NULL DEFAULT '[]'::jsonb,
  facts         int NOT NULL DEFAULT 0,
  sourced_facts int NOT NULL DEFAULT 0,   -- facts that link to a stored page snapshot
  secs          double precision,
  llm_calls     int NOT NULL DEFAULT 0,
  cost_usd      double precision NOT NULL DEFAULT 0,
  reused_facts  int NOT NULL DEFAULT 0
);
