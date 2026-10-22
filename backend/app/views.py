"""Turns stored rows into the JSON shapes the frontend renders (same shape the mockup used)."""
import asyncio

from .pipeline.hunt import ago
from .store import get_store


async def company_views(rid: str) -> list[dict]:
    store = get_store()
    comps = await store.research_companies(rid)
    facts_by = await store.report_facts([c["id"] for c in comps])  # one query for every company
    out = []
    for c in comps:
        v, fb = c["view"], facts_by[c["id"]]
        facts = fb["latest"]
        out.append({
            "id": c["id"], "n": c["name"], "domain": c["domain"], "loc": c["location"] or v.get("loc", ""),
            "sim": c["similarity"], "ang": c["angle"],
            "ll": [c["lat"], c["lng"]] if c.get("lat") is not None else None,
            "price": v.get("price", 0), "y": v.get("y", 0.5), "scale": v.get("scale", ""),
            "hiring": v.get("hiring", ""), "complaint": v.get("complaint", ""), "change": v.get("change", "None"),
            "rating": v.get("rating", 0), "sum": v.get("sum", ""), "plans": v.get("plans", []),
            "f": [[f["text"], f["source"]] for f in facts], "beat": v.get("beat", []), "tl": v.get("tl", []),
            "q": v.get("q", []), "facts": fb["count"],
        })
    return out


async def report(rid: str) -> dict | None:
    store = get_store()
    # independent queries in parallel: on a slow network this costs ~2 round trips instead of 5
    r, comps, chat = await asyncio.gather(store.get_research(rid), company_views(rid), store.chat(rid))
    if not r:
        return None
    alerts = await store.count_alerts([c["id"] for c in comps], r["seen_at"]) if comps else 0
    return {
        "id": r["id"], "q": r["query"], "label": r["market_label"], "status": r["status"], "mode": r["mode"],
        "when": ago(r["finished_at"] or r["started_at"]).capitalize(), "recorded": r["is_recorded"],
        "alerts": alerts, "totals": r["totals"], "market": r["market"], "C": comps, "chat": chat,
    }


async def library() -> list[dict]:
    return [{"id": r["id"], "q": r["query"], "label": r.get("market_label") or "", "status": r["status"],
             "recorded": r["is_recorded"],
             "when": ago(r["finished_at"] or r["started_at"]).capitalize(), "alerts": r["alerts"],
             "comps": r["comps"], "facts": r["facts"], "thumb": r["thumb"]}
            for r in await get_store().library_rows()]
