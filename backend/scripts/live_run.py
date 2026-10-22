"""Run one hunt against the running server and print a timeline + per-agent LLM cost/latency.

Usage (server running on :8000):  .venv/bin/python -m scripts.live_run "your query"
"""
import asyncio
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"


async def main(query: str):
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        r = (await c.post("/api/research", json={"query": query, "mode": "fresh"})).json()
        rid, t0, first = r["id"], time.perf_counter(), None
        print(f"research {rid}\n")
        async with c.stream("GET", f"/api/research/{rid}/events") as s:
            async for line in s.aiter_lines():
                if line.startswith("data: ") and line != "data: {}":
                    e = json.loads(line[6:])
                    p, dt = e["payload"], time.perf_counter() - t0
                    if e["type"] == "competitor_found" and first is None:
                        first = dt
                    text = (p.get("msg") or p.get("status") or "").replace("<b>", "").replace("</b>", "")
                    print(f"{dt:6.1f}s {e['agent']:9} {e['type']:16} {text[:90]}  |  {p.get('src', '')[:50]}")
                if line.startswith("event: end"):
                    break
        rep = (await c.get(f"/api/research/{rid}")).json()

    print(f"\nfirst competitor on screen: {first:.1f}s" if first else "\nno competitor found")
    t = rep["totals"]
    print(f"total: {t.get('secs')}s · {t.get('comps')} competitors · {t.get('facts')} facts · llm {t.get('llm')}")
    for comp in rep["C"]:
        print(f"  {comp['n'][:28]:28} facts={comp['facts']:3}  loc={comp['loc'][:30]:30} ll={'yes' if comp['ll'] else 'no'}"
              f"  price={comp['price']}  rating={comp['rating']}")

    from app.config import settings
    if settings.database_url:
        import asyncpg

        db = await asyncpg.connect(settings.database_url)
        rows = await db.fetch("SELECT agent, model, count(*) n, round(avg(ms)) avg_ms, max(ms) max_ms, "
                              "round(sum(cost_usd)::numeric, 4) cost FROM llm_calls WHERE research_id=$1 "
                              "GROUP BY agent, model ORDER BY agent", rid)
        print("\nLLM calls by agent:")
        for x in rows:
            print(f"  {x['agent']:10} {x['model']:18} calls={x['n']:3} avg={x['avg_ms']}ms max={x['max_ms']}ms ${x['cost']}")
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "A specialty coffee shop in Paris 11e"))
