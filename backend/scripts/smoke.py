"""End-to-end check without a server: seed, memory match, fresh hunt, live events, report, chat, continue.

Run: .venv/bin/python -m scripts.smoke   (from backend/)
"""
import asyncio
import json
import os
import sys
import time

os.environ.setdefault("MOCK_DELAY", "0.05")

import httpx  # noqa: E402

from app.main import app  # noqa: E402


async def sse(client, rid, after=0, limit_s=60):
    events = []
    async with client.stream("GET", f"/api/research/{rid}/events", params={"after": after}, timeout=limit_s) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: ") and line != "data: {}":
                events.append(json.loads(line[6:]))
            if line.startswith("event: end"):
                break
    return events


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        sys.exit(1)


async def main():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            h = (await c.get("/api/health")).json()
            print("health:", h)

            lib = (await c.get("/api/library")).json()
            check(len(lib) >= 2, f"library seeded with {len(lib)} researches")
            check(any(r["alerts"] for r in lib), "seeded research has Watcher alerts")

            m = (await c.post("/api/research", json={"query": "Specialty coffee shop in Paris 17e near Batignolles"})).json()
            check("match" in m, f"memory match found: {m.get('match', {}).get('q')}")

            t0 = time.perf_counter()
            r = (await c.post("/api/research", json={"query": "A dating app for students", "mode": "fresh"})).json()
            ev = await sse(c, r["id"])
            types = [e["type"] for e in ev]
            check("competitor_found" in types and "fact_added" in types, f"live events streamed ({len(ev)})")
            check(types[-1] == "run_done", f"fresh hunt finished in {time.perf_counter() - t0:.1f}s")

            rep = (await c.get(f"/api/research/{r['id']}")).json()
            check(len(rep["C"]) >= 5 and rep["market"].get("gap"), f"report has {len(rep['C'])} competitors + gap")
            check(all(c_["f"] for c_ in rep["C"]), "every competitor has sourced facts")
            check(sum(1 for c_ in rep["C"] if c_["ll"]) >= 5, "competitors geocoded for the map")

            for q in ["Which app has the best reviews?", "Dig deeper into Hinge's pricing", "Add Raya to this market"]:
                a = (await c.post(f"/api/research/{r['id']}/chat", json={"message": q})).json()
                check(bool(a["a"]) and a["steps"], f"chat: {q!r} → {len(a['steps'])} steps")

            cont = (await c.post("/api/research", json={"query": "A dating app for students", "mode": "continue",
                                                        "parent_id": r["id"]})).json()
            ev2 = await sse(c, cont["id"], after=cont["after"])
            done = ev2[-1]["payload"]
            check(done.get("mode") == "continue" and done.get("reused", 0) > 0,
                  f"continue reused {done.get('reused')} facts in {done.get('secs')}s")
            print("\nall checks passed")


if __name__ == "__main__":
    asyncio.run(main())
