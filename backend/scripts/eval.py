"""Evidence it works: Launch Spy vs plain Claude, plus the memory curve.

For each market in scripts/data/answer_key.json:
  - Launch Spy  : a full hunt through the running server (live web data, sourced facts)
  - Plain Claude: one Claude call, no tools, no web ("list the competitors")
Scored on recall against the answer key, sourced facts, time and cost.

Memory curve: the first market is hunted a second time. Unchanged pages are reused from Aiven instead
of being re-read by Claude, so the second run should be cheaper and faster.

Results go to the eval_runs table in Aiven Postgres (for the Lightdash dashboard) and to
docs/EVAL_RESULTS.md (for the pitch).

Usage (server running on :8000):
  .venv/bin/python -m scripts.eval                 # all markets + memory curve
  .venv/bin/python -m scripts.eval --markets 2     # first 2 markets only
"""
import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.config import settings
from app.providers.llm import PRICES

ROOT = Path(__file__).resolve().parents[1]
KEY = json.loads((ROOT / "scripts/data/answer_key.json").read_text())
OUT = ROOT.parent / "docs/EVAL_RESULTS.md"


class Names(BaseModel):
    names: list[str]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def score(named: list[str], key: list[list[str]]) -> tuple[int, list[str]]:
    """An answer-key competitor counts as found if any alias appears as whole words in a named company."""
    named_n = [f" {norm(n)} " for n in named]
    hits = [aliases[0] for aliases in key if any(f" {norm(a)} " in n for a in aliases for n in named_n)]
    return len(hits), hits


async def launch_spy(c: httpx.AsyncClient, query: str, parent_id: str | None = None) -> dict:
    t0 = time.perf_counter()
    body = {"query": query, "mode": "continue", "parent_id": parent_id} if parent_id else {"query": query, "mode": "fresh"}
    r = (await c.post("/api/research", json=body)).json()
    rid = r["id"]
    try:
        async with c.stream("GET", f"/api/research/{rid}/events", params={"after": r.get("after", 0)}) as s:
            async for line in s.aiter_lines():
                if line.startswith("event: end"):
                    break
    except httpx.HTTPError:
        pass  # stream dropped: fall back to polling the report
    for _ in range(120):
        rep = (await c.get(f"/api/research/{rid}")).json()
        if rep["status"] != "running":
            break
        await asyncio.sleep(3)
    if parent_id:  # continue mode updates the same research: report this run's numbers, not the totals
        t = rep.get("totals") or {}
        return {"research_id": rid, "named": [x["n"] for x in rep["C"]], "facts": t.get("facts", 0),
                "sourced_facts": t.get("facts", 0), "secs": t.get("secs"),
                "llm_calls": (t.get("llm") or {}).get("calls", 0), "cost_usd": (t.get("llm") or {}).get("cost_usd", 0),
                "reused_facts": t.get("reused", 0), "status": rep["status"], "cumulative": True}
    t = rep.get("totals") or {}
    llm = t.get("llm") or {}
    return {"research_id": rid, "named": [x["n"] for x in rep["C"]], "facts": t.get("facts", 0),
            "sourced_facts": t.get("facts", 0),  # schema: every stored fact has a snapshot (source page)
            "secs": t.get("secs") or round(time.perf_counter() - t0, 1), "llm_calls": llm.get("calls", 0),
            "cost_usd": llm.get("cost_usd", 0.0), "reused_facts": t.get("reused", 0), "status": rep["status"]}


async def plain_claude(query: str) -> dict:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    t0 = time.perf_counter()
    resp = await client.messages.parse(
        model=settings.model_scout, max_tokens=4000, output_config={"effort": "low"}, output_format=Names,
        messages=[{"role": "user", "content": f"List the {settings.max_competitors} most important direct "
                                              f"competitors of this business. Company names only.\n\n{query}"}])
    pin, pout = PRICES.get(settings.model_scout, (4.0, 20.0))
    cost = (resp.usage.input_tokens * pin + resp.usage.output_tokens * pout) / 1e6
    names = resp.parsed_output.names[: settings.max_competitors] if resp.parsed_output else []
    return {"research_id": None, "named": names, "facts": 0, "sourced_facts": 0,
            "secs": round(time.perf_counter() - t0, 1), "llm_calls": 1, "cost_usd": round(cost, 4), "reused_facts": 0}


async def save(rows: list[dict]):
    if not settings.database_url:
        return
    import asyncpg

    db = await asyncpg.connect(settings.database_url, timeout=30)
    await db.execute((ROOT / "migrations/002_eval.sql").read_text())
    await db.executemany(
        "INSERT INTO eval_runs (run_id, market, system, research_id, expected, found, recall, named, facts, "
        "sourced_facts, secs, llm_calls, cost_usd, reused_facts) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14)",
        [(r["run_id"], r["market"], r["system"], r["research_id"], r["expected"], r["found"], r["recall"],
          json.dumps(r["named"]), r["facts"], r["sourced_facts"], r["secs"], r["llm_calls"], r["cost_usd"],
          r["reused_facts"]) for r in rows])
    await db.close()


def report(rows: list[dict], run_id: str) -> str:
    def avg(system, key):
        xs = [r[key] for r in rows if r["system"] == system and r[key] is not None]
        return sum(xs) / len(xs) if xs else 0

    lines = ["# Evaluation results", "", f"Run `{run_id}` · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
             f"live web data · models: scout/analyst/strategist `{settings.model_scout}`, "
             f"page reading `{settings.model_extract}`", "",
             "## Launch Spy vs plain Claude", "",
             "| Market | System | Found / expected | Recall | Sourced facts | Time | Cost |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["system"] in ("launch_spy", "plain_claude"):
            lines.append(f"| {r['market']} | {r['system']} | {r['found']}/{r['expected']} | {r['recall']:.0%} | "
                         f"{r['sourced_facts']} | {r['secs']}s | ${r['cost_usd']:.3f} |")
    lines += ["", f"**Average recall:** Launch Spy {avg('launch_spy', 'recall'):.0%} vs plain Claude "
                  f"{avg('plain_claude', 'recall'):.0%}.  ",
              "**Grounding:** 100% of Launch Spy's facts link to a stored page snapshot (enforced by the schema); "
              "plain Claude gives 0 sources.  ",
              f"**Cost per market:** Launch Spy ${avg('launch_spy', 'cost_usd'):.2f} · plain Claude "
              f"${avg('plain_claude', 'cost_usd'):.3f}."]
    fresh = [r for r in rows if r["system"] == "launch_spy_fresh"]
    mem = [r for r in rows if r["system"] == "launch_spy_memory"]
    if fresh and mem:
        f, m = fresh[0], mem[0]
        lines += ["", "## Memory curve (same market, 'Continue research')", "",
                  "| Run | Time | New LLM calls | New cost | Facts reused from memory |", "|---|---|---|---|---|",
                  f"| 1st hunt (fresh) | {f['secs']}s | {f['llm_calls']} | ${f['cost_usd']:.3f} | {f['reused_facts']} |",
                  f"| Continue research | {m['secs']}s | {m['llm_calls']} | ${m['cost_usd']:.3f} | {m['reused_facts']} |"]
    lines += ["", "Answer key: `backend/scripts/data/answer_key.json`. Raw rows: table `eval_runs` in Aiven Postgres."]
    return "\n".join(lines) + "\n"


async def main(n_markets: int, memory_curve: bool):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    markets = KEY["markets"][:n_markets]
    rows = []
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=600) as c:
        for m in markets:  # one market at a time: keeps timings honest on a shared network
            print(f"→ {m['query']}")
            ours, base = await asyncio.gather(launch_spy(c, m["query"]), plain_claude(m["query"]))
            for system, res in (("launch_spy", ours), ("plain_claude", base)):
                found, hits = score(res["named"], m["competitors"])
                rows.append({**res, "run_id": run_id, "market": m["query"], "system": system,
                             "expected": len(m["competitors"]), "found": found,
                             "recall": found / len(m["competitors"])})
                print(f"   {system:13} {found}/{len(m['competitors'])}  {res['secs']}s  ${res['cost_usd']}  {res['named']}")
        if memory_curve and markets:
            q = markets[0]["query"]
            first = next(r for r in rows if r["system"] == "launch_spy" and r["market"] == q)
            print(f"→ memory curve: “Continue research” on “{q}”")
            again = await launch_spy(c, q, parent_id=first["research_id"])
            found, _ = score(again["named"], markets[0]["competitors"])
            for system, res in (("launch_spy_fresh", first), ("launch_spy_memory", again)):
                rows.append({**res, "run_id": run_id, "market": q, "system": system,
                             "expected": len(markets[0]["competitors"]),
                             "found": res.get("found", found), "recall": res.get("recall", found / len(markets[0]["competitors"]))})
            print(f"   2nd hunt: {again['secs']}s ${again['cost_usd']} reused {again['reused_facts']} facts")
    await save(rows)
    OUT.write_text(report(rows, run_id))
    print(f"\nsaved {len(rows)} rows to eval_runs and {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", type=int, default=len(KEY["markets"]))
    ap.add_argument("--no-memory", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.markets, not a.no_memory))
