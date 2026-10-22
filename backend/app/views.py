"""Turns stored rows into the JSON shapes the frontend renders (same shape the mockup used)."""
from .pipeline.hunt import ago
from .store import get_store


async def company_views(rid: str) -> list[dict]:
    store = get_store()
    out = []
    for c in await store.research_companies(rid):
        v = c["view"]
        facts = await store.facts_for_company(c["id"], limit=8)
        out.append({
            "id": c["id"], "n": c["name"], "domain": c["domain"], "loc": c["location"] or v.get("loc", ""),
            "sim": c["similarity"], "ang": c["angle"],
            "ll": [c["lat"], c["lng"]] if c.get("lat") is not None else None,
            "price": v.get("price", 0), "y": v.get("y", 0.5), "scale": v.get("scale", ""),
            "hiring": v.get("hiring", ""), "complaint": v.get("complaint", ""), "change": v.get("change", "None"),
            "rating": v.get("rating", 0), "sum": v.get("sum", ""), "plans": v.get("plans", []),
            "f": [[f["text"], f["source"]] for f in facts], "beat": v.get("beat", []), "tl": v.get("tl", []),
            "q": v.get("q", []), "facts": await store.count_facts([c["id"]]),
        })
    return out


async def report(rid: str) -> dict | None:
    store = get_store()
    r = await store.get_research(rid)
    if not r:
        return None
    comps = await company_views(rid)
    alerts = await store.count_alerts([c["id"] for c in comps], r["seen_at"])
    return {
        "id": r["id"], "q": r["query"], "label": r["market_label"], "status": r["status"], "mode": r["mode"],
        "when": ago(r["finished_at"] or r["started_at"]).capitalize(), "recorded": r["is_recorded"],
        "alerts": alerts, "totals": r["totals"], "market": r["market"], "C": comps,
        "chat": await store.chat(rid),
    }


async def library() -> list[dict]:
    store = get_store()
    out = []
    for r in await store.list_researches():
        comps = await store.research_companies(r["id"])
        ids = [c["id"] for c in comps]
        out.append({
            "id": r["id"], "q": r["query"], "status": r["status"], "recorded": r["is_recorded"],
            "when": ago(r["finished_at"] or r["started_at"]).capitalize(),
            "alerts": await store.count_alerts(ids, r["seen_at"]), "comps": len(comps),
            "facts": await store.count_facts(ids),
            "thumb": [{"n": c["name"], "sim": c["similarity"], "ang": c["angle"]} for c in comps],
        })
    return out
