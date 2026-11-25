"""The chat tools' work, plus recorded answers for keyless demo mode.

The chat agent itself (ReAct loop, LangGraph + LangChain) is in app/agents/chat_graph.py; its tools call
_run_tool() below. Whatever a tool finds is saved to the research, so the project grows with every question:

web_research       → search the web for the question about one competitor, read the pages, save the facts
dig_into_company   → re-read one competitor's own pages and save new facts
add_company        → Scout + Researcher for a competitor the user names, added to this research
"""
import asyncio
import html
import re

from .. import fixtures
from ..providers import web
from ..store import get_store
from . import hunt


def _find(comps, text):
    low = text.lower()
    return next((c for c in comps if c["name"].lower() in low or c["name"].lower().split()[0] in low), None)


async def _run_tool(rid, comps, name, args) -> dict:
    store = get_store()
    if name == "web_research":
        c = _find(comps, args["company"])
        if not c:
            return {"steps": [], "data": {"error": f"{args['company']} is not in this research"}}
        query = (await store.get_research(rid))["query"]
        try:
            results = await web.search(f"{c['name']} {args['question']}")
        except Exception:
            results = []
        memory = await store.page_memory([c["id"]])
        urls = [x["url"] for x in results if (c["id"], x["url"]) not in memory][:3]
        pages = [p for p in await asyncio.gather(*(hunt._fetch_page(u) for u in urls)) if p]
        exts = await asyncio.gather(*(hunt.llm().extract(f"{query}. Question: {args['question']}", c, u, t, rid)
                                      for u, t, _ in pages), return_exceptions=True)
        found = []
        for (u, t, fetcher), ext in zip(pages, exts):
            if isinstance(ext, Exception):
                continue
            rows = [{"kind": f.kind, "text": f.text, "value": f.value, "item": f.item} for f in ext.facts]
            found += [{**r, "source": u} for r in rows]
            hunt.later(rid, lambda u=u, t=t, fetcher=fetcher, rows=rows: store.save_page(c["id"], rid, u, t, fetcher, rows))
        await hunt.settle(rid)
        return {"steps": [["research", f"searching the web: {c['name']} {args['question']}", f"{len(pages)} pages read"],
                          ["analyst", f"saved {len(found)} new facts to this research", "postgres"]],
                "data": [{"fact": f["text"], "source": f["source"]} for f in found],
                "cites": sorted({f["source"] for f in found})[:3], "highlight": [c["id"]],
                "upd": f"+{len(found)} facts saved to this research" if found else "", "refresh": bool(found)}
    if name == "dig_into_company":
        c = _find(comps, args["name"])
        if not c:
            return {"steps": [], "data": {"error": f"{args['name']} is not in this research"}}
        before = await store.count_facts([c["id"]])
        query = (await store.get_research(rid))["query"]
        await hunt.research_company(rid, query, c, 0)
        await hunt.settle(rid)
        new = await store.count_facts([c["id"]]) - before
        facts = await store.facts_for_company(c["id"], limit=12)
        return {"steps": [["research", f"re-reading {c['name']}'s pages", "jina · H Company"]],
                "data": [{"fact": f["text"], "source": f["source"]} for f in facts],
                "cites": sorted({f["source"] for f in facts})[:2], "highlight": [c["id"]],
                "upd": f"+{new} facts saved to this research" if new else "", "refresh": bool(new)}
    if name == "add_company":
        comp = await store.upsert_company(hunt._domain(args["domain"]), args["name"])
        n = len(comps)
        await store.link_company(rid, comp["id"], 0.6, -40 + n * 47, n, {"sum": f"Added from chat: {args['name']}."})
        r = await store.get_research(rid)
        await hunt.research_company(rid, r["query"], comp, 0)
        await hunt.settle(rid)
        facts = await store.facts_for_company(comp["id"], limit=12)
        return {"steps": [["scout", f"adding {args['name']}", comp["domain"]],
                          ["research", f"reading {args['name']}'s site", "jina · H Company"]],
                "data": [{"fact": f["text"], "source": f["source"]} for f in facts],
                "highlight": [comp["id"]], "refresh": True, "upd": f"+1 competitor, +{len(facts)} facts saved"}
    return {"steps": [], "data": {"error": "unknown tool"}}


# ------------------------------------------------------------------ recorded mode (no key)
async def _recorded(rid: str, message: str) -> dict:
    store = get_store()
    r = await store.get_research(rid)
    comps = await store.research_companies(rid)
    low = message.lower()
    await asyncio.sleep(web.mock_wait(1.5))
    comp = _find(comps, message)
    m = re.match(r"\s*add\s+(.+?)(?:\s+to\s+this\s+market.*)?$", message, re.I)
    if m:
        name = m.group(1).strip().title()
        c = await store.upsert_company(hunt._domain(name.replace(" ", "") + ".example"), name, "Paris")
        unit_price = 5.1 if fixtures.market_for(r["query"]) == "coffee" else 28
        await store.link_company(rid, c["id"], 0.6, -40 + len(comps) * 47, len(comps), {
            "price": unit_price, "y": 0.62, "scale": "New", "hiring": "Unknown", "complaint": "Few reviews yet",
            "rating": 4.3, "sum": f"{name} was added from chat. The Scout found its site and the Researcher read "
            "4 pages.", "plans": [["Entry", str(unit_price)]], "q": [], "change": "Added",
            "beat": ["Watch for now. Too early to tell"]})
        snap = await store.add_snapshot(c["id"], f"{c['domain']}/", f"{name} home page", "fixture")
        await store.add_facts([dict(company_id=c["id"], research_id=rid, snapshot_id=snap["id"], kind="other",
                                    text=f"Found {name}'s official site and pricing", value="", source=snap["url"])])
        return {"steps": [["scout", f"searching for {name}", "tavily"], ["research", f"reading {name}'s site",
                          "H Company · 1 session"], ["analyst", "placing it on the chart", "postgres"]],
                "a": f"Added <b>{html.escape(name)}</b> to this market. It lands between the top competitors on "
                     "price and intent: a real competitor, not yet a threat to your gap.",
                "cites": [], "upd": "+1 competitor saved to this research", "highlight": [c["id"]], "refresh": True}
    if comp and re.search(r"deeper|dig|pric|more|update|check", low):
        v = comp["view"]
        paid = ", ".join(f"{p[0]} €{p[1]}" for p in v.get("plans", []) if p[1] != "0")
        tl = v.get("tl") or []
        change = f" Latest change: {tl[0][1]} ({tl[0][0]})." if tl else " No change since the last snapshot."
        facts = await store.facts_for_company(comp["id"], limit=3)
        return {"steps": [["research", f"re-opening {comp['name']}'s pricing page", "H Company · 1 session"],
                          ["watch", "comparing with the last snapshot", "snapshot diff"],
                          ["analyst", "checking against reviews", "reviews table"]],
                "a": f"<b>{comp['name']}</b> today: {paid}.{change} Customers mostly complain that "
                     f"{v.get('complaint', '').lower()}, which is your opening.",
                "cites": [f["source"] for f in facts][:2], "upd": "", "highlight": [comp["id"]], "refresh": False}
    if re.search(r"review|rated|best|complain|love", low):
        top = sorted(comps, key=lambda c: -(c["view"].get("rating") or 0))[:3]
        items = "".join(f"<li><b>{c['name']}</b> {c['view'].get('rating')}★, but people say "
                        f"“{(c['view'].get('q') or [''])[0]}”</li>" for c in top)
        return {"steps": [["analyst", "ranking competitors by rating", "reviews table"]],
                "a": f"Best rated in this market:<ul>{items}</ul>", "cites": [c["name"] + " reviews" for c in top],
                "upd": "", "highlight": [c["id"] for c in top], "refresh": False}
    if re.search(r"cheap|price|expensive|cost", low):
        by = sorted(comps, key=lambda c: c["view"].get("price") or 0)
        lo, hi = by[0], by[-1]
        return {"steps": [["analyst", "sorting by price", "prices table"]],
                "a": f"Cheapest is <b>{lo['name']}</b> (€{lo['view'].get('price')}), most expensive is "
                     f"<b>{hi['name']}</b> (€{hi['view'].get('price')}). Your planned price of "
                     f"€{r['market'].get('you', {}).get('price')} sits inside the empty spot.",
                "cites": [], "upd": "", "highlight": [lo["id"], hi["id"]], "refresh": False}
    top = sorted(comps, key=lambda c: -c["similarity"])[:2]
    return {"steps": [["analyst", "searching this research's facts", "postgres"]],
            "a": f"From this research: {r['market'].get('verdict', ['', ''])[1]} The two closest competitors are "
                 f"<b>{top[0]['name']}</b> and <b>{top[1]['name']}</b>.",
            "cites": [], "upd": "", "highlight": [c["id"] for c in top], "refresh": False}
