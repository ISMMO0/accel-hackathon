"""The hunt: Scout → parallel Researchers → Analyst → Strategist, plus Continue mode and the Watcher.

Every step writes to the store and emits an event before moving on, so nothing is lost if the
browser disconnects and a failed competitor never fails the whole run.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from .. import fixtures
from .. import bus
from ..bus import emit
from ..config import settings
from ..providers import web
from ..providers.llm import MockLLM, make_llm
from ..store import get_store
from ..store.memory import company_id

log = logging.getLogger("hunt")
_tasks: set[asyncio.Task] = set()
_llm = None
_mock_llm = None


def llm():
    global _llm, _mock_llm
    if web.FORCE_MOCK.get():
        if _mock_llm is None:
            _mock_llm = MockLLM(_log_call)
        return _mock_llm
    if _llm is None:
        _llm = make_llm(_log_call)
    return _llm


async def _log_call(**row):
    """LLM call log (cost/latency) is written in the background, never on the agent's critical path."""
    rid = row.get("research_id")
    later(rid or "_", lambda: get_store().log_llm_call(**row))


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _domain(d: str) -> str:
    d = re.sub(r"^https?://", "", d.strip().lower()).split("/")[0]
    return d.removeprefix("www.")


# Platforms that host many businesses: never use them as a company's identity.
SHARED_HOSTS = ("instagram.com", "facebook.com", "google.com", "goo.gl", "tripadvisor", "yelp.", "linktr.ee",
                "tiktok.com", "ubereats.com", "deliveroo.", "thefork.", "lafourchette.", "pagesjaunes.", "foursquare.")


def company_key(name: str, domain: str, taken: set[str]) -> tuple[str, bool]:
    """(identity key, has_own_site). Small local businesses often have no website of their own."""
    d = _domain(domain)
    if d and "." in d and not any(h in d for h in SHARED_HOSTS) and d not in taken:
        return d, True
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"
    return f"{slug}.place", False


async def _mock_pause(s: float):
    if not web.live():
        await asyncio.sleep(web.mock_wait(s))


# ---------------------------------------------------------------- entry points
async def start(query: str, mode: str = "fresh", parent_id: str | None = None) -> dict:
    """Creates (or reopens) a research and runs the hunt in the background. Returns {id, after}."""
    store = get_store()
    if mode == "continue" and parent_id and await store.get_research(parent_id):
        after = await bus.last_event_id(parent_id)
        await store.update_research(parent_id, status="running", mode="continue")
        spawn(_guard(parent_id, _continue(parent_id, query)))
        return {"id": parent_id, "after": after}
    emb = await web.embed(query)
    r = await store.create_research(query=query, embedding=emb, is_recorded=not web.live())
    bus.new_research(r["id"])
    spawn(_guard(r["id"], _fresh(r["id"], query)))
    return {"id": r["id"], "after": 0}


async def memory_match(query: str) -> dict | None:
    """Is there a finished research about the same market? (pgvector similarity over past queries)."""
    hit = await get_store().find_similar(await web.embed(query), settings.memory_threshold)
    if not hit:
        # Keyless embeddings are crude; in mock mode also match by fixture market.
        if not settings.openai_api_key:
            market = fixtures.market_for(query)
            for r in await get_store().list_researches():
                if r["status"] == "done" and fixtures.market_for(r["query"]) == market:
                    return {"research": r, "score": 0.9}
        return None
    return {"research": hit[0], "score": round(hit[1], 3)}


async def _guard(rid: str, coro):
    store = get_store()
    try:
        async with asyncio.timeout(settings.hunt_timeout_s):
            await coro
    except Exception as e:  # includes TimeoutError: show what we have, say what failed
        log.exception("hunt %s failed", rid)
        await store.update_research(rid, status="failed", finished_at=now())
        await emit(rid, "strat", "run_error", f"The hunt stopped: {type(e).__name__}. Saved results are kept.",
                   str(e)[:200])


# ---------------------------------------------------------------- background writes
# The hunt runs in memory; database writes happen in the background so a slow network to Aiven never
# stalls the agents. A research is only marked done after settle() has awaited every pending write.
_pending: dict[str, list[asyncio.Task]] = {}


def later(rid: str, coro, after: asyncio.Task | None = None) -> asyncio.Task:
    async def run():
        if after:
            await after
        for attempt in range(4):  # flaky network: retry with backoff
            try:
                return await coro_factory()
            except Exception as e:
                if attempt == 3:
                    log.error("write failed for %s: %s", rid, e)
                    raise
                await asyncio.sleep(0.5 * 2 ** attempt)

    # accept either a coroutine function (retryable) or a coroutine (single try)
    coro_factory = coro if callable(coro) else (lambda c=coro: c)
    t = asyncio.create_task(run())
    _pending.setdefault(rid, []).append(t)
    return t


async def settle(rid: str):
    while _pending.get(rid):
        tasks, _pending[rid] = _pending[rid], []
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- fresh hunt
async def _fresh(rid: str, query: str):
    store, t0 = get_store(), time.perf_counter()
    await emit(rid, "scout", "run_started", "", "", query=query, live=web.live())

    # 1. Scout
    queries = [query, f"{query} competitors", f"best alternatives {query}"]
    await emit(rid, "scout", "agent_status", f"Running {len(queries)} searches in parallel",
               " · ".join(f"“{q}”" for q in queries), status="Searching the web")
    batches = await asyncio.gather(*(web.search(q) for q in queries), return_exceptions=True)
    seen, results = set(), []
    for b in batches:
        for x in b if isinstance(b, list) else []:
            if x["url"] not in seen:
                seen.add(x["url"])
                results.append(x)
    # Guides ("best cafés in the 11e") name many competitors at once: read the top few for the Scout.
    guide_pages = await asyncio.gather(*(_fetch_page(r["url"], 8) for r in results[:3])) if web.live() else []
    guides = [{"url": p[0], "text": p[1]} for p in guide_pages if p]
    if web.live():
        await emit(rid, "scout", "agent_status", f"{len(results)} web results, reading {len(guides)} guides",
                   " · ".join(_domain(g["url"]) for g in guides), status="Picking real competitors")
    scout = await llm().scout(query, results, rid, guides=guides)
    later(rid, lambda: store.update_research(rid, market_label=scout.market_label))

    cands, taken, companies = scout.candidates[: settings.max_competitors], set(), []
    n = max(len(cands), 1)
    for i, c in enumerate(cands):
        key, _ = company_key(c.name, c.domain, taken)
        taken.add(key)
        companies.append({"id": company_id(key), "domain": key, "name": c.name, "location": c.location_hint,
                          "lat": None, "lng": None, "sim": max(0.3, min(0.95, c.similarity)),
                          "angle": -60 + i * (360 / n) + (17 if i % 2 else 0), "pos": i, "facts": []})

    async def persist_companies():
        await asyncio.gather(*(store.upsert_company(c["domain"], c["name"], c["location"], cid=c["id"])
                               for c in companies))
        await asyncio.gather(*(store.link_company(rid, c["id"], c["sim"], c["angle"], c["pos"], {})
                               for c in companies))
    ready = later(rid, persist_companies)

    for c in companies:  # shown immediately, saved in the background
        await emit(rid, "scout", "competitor_found", f"Found <b>{c['name']}</b> · {c['location'] or c['domain']}",
                   f"similarity {c['sim']:.2f}", company={"id": c["id"], "n": c["name"], "loc": c["location"],
                                                          "sim": c["sim"], "ang": c["angle"]})
        await _mock_pause(0.45)

    # Map pins: geocode the Scout's addresses in the background while the researchers read.
    geo = asyncio.create_task(_geocode_all(rid, companies, ready))

    # 2. Researchers, all in parallel; one failing never stops the others
    await emit(rid, "research", "agent_status", "", "", status=f"Reading pages for {len(companies)} competitors")
    await asyncio.gather(*(_research_company(rid, query, c, i, ready) for i, c in enumerate(companies)))
    await geo

    # 3 + 4. Analyst, Strategist
    await _analyze_and_strategize(rid, query, companies, ready)

    await _finish(rid, t0, mode="fresh", companies=companies)


async def _research_company(rid: str, query: str, comp: dict, i: int, ready: asyncio.Task | None = None):
    """Fetch + read one competitor's pages. Facts go into comp["facts"] at once and to the DB in the background."""
    store = get_store()
    comp.setdefault("facts", [])
    await _mock_pause(0.55 * i)  # mock: stagger so the demo animation reads well
    try:
        async with asyncio.timeout(settings.researcher_timeout_s):
            if web.live():
                own_site = not comp["domain"].endswith(".place")
                site = [f"https://{comp['domain']}", f"https://{comp['domain']}/pricing"] if own_site else []

                async def reviews():  # reviews and listings: the only source for businesses without a site
                    try:
                        rev = await web.search(f"{comp['name']} {comp.get('location') or ''} avis reviews")
                    except Exception:
                        return []
                    urls = [r["url"] for r in rev if r["url"] not in site][: settings.pages_per_company - len(site)]
                    return await asyncio.gather(*(_fetch_page(u) for u in urls))

                # own site and review search at the same time
                site_pages, review_pages = await asyncio.gather(
                    asyncio.gather(*(_fetch_page(u) for u in site)), reviews())
                pages = [p for p in [*site_pages, *review_pages] if p]
            else:
                pages = [(u, t, "fixture") for u, t in web.fixture_pages(comp)]

            async def read(url, text, fetcher) -> int:
                ext = await llm().extract(query, comp, url, text, rid)
                rows = [{"kind": f.kind, "text": f.text, "value": f.value} for f in ext.facts]
                comp["facts"] += [{**r, "source": url} for r in rows]
                later(rid, lambda: store.save_page(comp["id"], rid, url, text, fetcher, rows), after=ready)
                if rows:
                    via = {"h": " · H Company browser agent", "fixture": " · recorded"}.get(fetcher, f" · {fetcher}")
                    await emit(rid, "research", "fact_added", f"{comp['name']}: {rows[0]['text']}",
                               f"{url}{via}", company_id=comp["id"], count=len(rows))
                return len(rows)

            counts = await asyncio.gather(*(read(*p) for p in pages), return_exceptions=True)
            total = sum(c for c in counts if isinstance(c, int))
            if not total:
                await emit(rid, "research", "fact_added", f"{comp['name']}: no usable pages found", "",
                           company_id=comp["id"], count=0)
    except Exception as e:
        await emit(rid, "research", "agent_status", f"{comp['name']}: research incomplete ({type(e).__name__})",
                   "partial facts kept", status="Some pages failed")


async def _geocode_all(rid: str, companies: list[dict], ready: asyncio.Task | None):
    store = get_store()
    for c in companies:
        loc = c.get("location") or ""
        if not loc:
            continue
        ll = await web.geocode(loc) or await web.geocode(re.sub(r"\(.*?\)", "", loc).strip(" ,"))
        if ll:
            c["lat"], c["lng"] = ll
            later(rid, lambda c=c, ll=ll: store.update_company(c["id"], lat=ll[0], lng=ll[1]), after=ready)


async def _fetch_page(url: str, budget: float = 15):
    """A page that doesn't answer within the budget is skipped: one slow site must not stall the hunt."""
    try:
        async with asyncio.timeout(budget):
            text, fetcher = await web.fetch(url)
        return url, web.clean(text), fetcher
    except Exception:
        return None


async def _analyze_and_strategize(rid: str, query: str, companies: list[dict], ready: asyncio.Task | None = None):
    store = get_store()
    await emit(rid, "analyst", "agent_status", "", "", status="Cross-checking facts")
    if all("facts" in c for c in companies):  # fresh hunt: facts are already in memory
        facts_by = {c["id"]: c["facts"][:15] for c in companies}
    else:  # continue mode: read them from the database (one query)
        got = await store.report_facts([c["id"] for c in companies], per_company=15)
        facts_by = {cid: v["latest"] for cid, v in got.items()}
    payload = [{"name": c["name"], "domain": c["domain"],
                "facts": [{"kind": f["kind"], "text": f["text"], "source": f["source"]} for f in facts_by[c["id"]]]}
               for c in companies]
    analysis = await llm().analyze(query, payload, rid)
    by_domain = {c["domain"]: c for c in companies}

    async def place(a):
        c = by_domain.get(_domain(a.domain))
        if not c:
            return
        ll = (c["lat"], c["lng"]) if c.get("lat") is not None else None
        if not ll:  # not pinned from the Scout's hint: try the analyst's address, then name + city
            ll = await web.geocode(a.location)
            if not ll and web.live():
                ll = await web.geocode(f"{c['name']}, {a.location.split(',')[-1].strip()}")
        view = {"price": a.x, "y": a.y, "scale": a.scale, "hiring": a.hiring, "complaint": a.complaint,
                "rating": a.rating, "sum": a.summary, "plans": [[p.name, p.price] for p in a.plans], "q": a.quotes,
                "change": a.change}
        later(rid, lambda: store.update_company(c["id"], location=a.location, lat=ll[0] if ll else None,
                                                lng=ll[1] if ll else None), after=ready)
        later(rid, lambda: store.update_link(rid, c["id"], view), after=ready)

    await asyncio.gather(*(place(a) for a in analysis.companies))
    for a in sorted(analysis.companies, key=lambda a: -a.rating)[:3]:
        c = by_domain.get(_domain(a.domain))
        if c and a.rating > 0:
            await emit(rid, "analyst", "analysis", f"{c['name']}: {a.rating}★, top complaint “{a.complaint.lower()}”",
                       "reviews clustered", company_id=c["id"])
            await _mock_pause(0.5)

    await emit(rid, "strat", "agent_status", "", "", status="Finding the gap")
    s = await llm().strategize(query, analysis, rid)
    for cs in s.companies:
        if c := by_domain.get(_domain(cs.domain)):
            later(rid, lambda c=c, beat=cs.beat: store.update_link(rid, c["id"], {"beat": beat}), after=ready)
    f = analysis.frame
    you_ll = None
    if not web.live():
        you_ll = fixtures.markets()[fixtures.market_for(query)]["you"].get("ll")
    market = {"unit": f.unit, "locL": f.loc_label, "scaleL": f.scale_label, "xL": f.x_label, "xMin": f.x_min,
              "xMax": f.x_max, "yTop": f.y_top, "yBot": f.y_bot,
              "you": {"price": s.you_x, "y": s.you_y, "ll": you_ll},
              "gap": {"x0": s.gap.x0, "x1": s.gap.x1, "y0": s.gap.y0, "y1": s.gap.y1, "t": s.gap.label},
              "verdict": [s.headline, s.detail], "plan": s.plan}
    later(rid, lambda: store.update_research(rid, market=market))
    facts = sum(len(v) for v in facts_by.values())
    await emit(rid, "strat", "insight", f"Gap found: {s.headline}", f"based on {facts} key facts")
    await _mock_pause(0.8)
    await emit(rid, "strat", "insight", s.plan, f"{len(companies)} dossiers written")


async def _finish(rid: str, t0: float, mode: str, reused: int = 0, companies: list[dict] | None = None):
    store = get_store()
    secs = round(time.perf_counter() - t0, 1)  # what the user waited for
    await settle(rid)  # every background write has landed before we call it done
    if companies is not None and all("facts" in c for c in companies):
        n_comps, facts = len(companies), sum(len(c["facts"]) for c in companies)
    else:
        comps = await store.research_companies(rid)
        n_comps, facts = len(comps), await store.count_facts([c["id"] for c in comps])
    totals = {"comps": n_comps, "facts": facts, "srcs": max(1, facts // 3), "secs": secs, "reused": reused,
              "llm": await store.llm_stats(rid)}
    await store.update_research(rid, status="done", finished_at=now(), totals=totals)
    await emit(rid, "strat", "run_done", "", "", mode=mode, **totals)


# ---------------------------------------------------------------- continue + watcher
async def _continue(rid: str, query: str):
    store, t0 = get_store(), time.perf_counter()
    r = await store.get_research(rid)
    comps = await store.research_companies(rid)
    reused = await store.count_facts([c["id"] for c in comps])
    await emit(rid, "analyst", "memory_match", f"Found your research from {_ago(r['finished_at'])}. Reusing "
               f"{reused} facts from memory instead of starting over", "pgvector similarity · postgres",
               status="Loading memory")
    for c in comps:
        n = await store.count_facts([c["id"]])
        await emit(rid, "analyst", "competitor_found", "", "", quiet=True, facts=n,
                   company={"id": c["id"], "n": c["name"], "loc": c["location"], "sim": c["similarity"],
                            "ang": c["angle"]})
        await _mock_pause(0.15)
    await emit(rid, "watch", "agent_status", "", "", status="Checking what changed")
    changed = await check_changes(rid, comps)
    if changed and web.live():
        await _analyze_and_strategize(rid, query, comps)
    else:
        m = (await store.get_research(rid))["market"]
        await emit(rid, "strat", "insight", f"Plan still holds: {m.get('plan', '').removeprefix('Plan: ')}",
                   f"{len(changed)} dossiers updated")
    await _finish(rid, t0, mode="continue", reused=reused)


async def check_changes(rid: str, comps: list[dict]) -> list[str]:
    """Watcher: refetch each company's known pages, compare hashes, extract only what changed."""
    store, changed = get_store(), []

    async def one(c):
        if not web.live():  # recorded mode: the fixture timeline says what changed
            hit = fixtures.company_by_domain(c["domain"])
            tl = hit[1]["tl"] if hit else []
            await _mock_pause(0.6)
            if tl:
                await store.add_alert(c["id"], tl[0][1])
                await store.update_link(rid, c["id"], {"tl": tl, "change": tl[0][1]})
                await emit(rid, "watch", "change_detected", f"{c['name']} changed: {tl[0][1]}",
                           f"snapshot diff · {tl[0][0]}", company_id=c["id"])
                changed.append(c["id"])
            else:
                await emit(rid, "watch", "no_change", f"{c['name']}: no change since last snapshot",
                           "snapshot diff · skipped re-reading pages", company_id=c["id"])
            return
        facts = await store.facts_for_company(c["id"], limit=200)
        urls = sorted({f["source"] for f in facts})[: settings.pages_per_company]
        diffs = 0
        for url in urls:
            page = await _fetch_page(url)
            if not page:
                continue
            prev = await store.latest_snapshot(c["id"], url)
            snap = await store.add_snapshot(c["id"], url, page[1], page[2])
            if prev and prev["content_hash"] != snap["content_hash"]:
                diffs += 1
                ext = await llm().extract(c["name"], c, url, page[1], rid)
                await store.add_facts([dict(company_id=c["id"], research_id=rid, snapshot_id=snap["id"], kind=f.kind,
                                            text=f.text, value=f.value, source=url) for f in ext.facts])
                note = ext.facts[0].text if ext.facts else f"{url} changed"
                await store.add_alert(c["id"], note)
                await store.update_link(rid, c["id"], {"tl": [["just now", note]], "change": note[:40]})
                await emit(rid, "watch", "change_detected", f"{c['name']} changed: {note}", f"snapshot diff · {url}",
                           company_id=c["id"])
        if diffs:
            changed.append(c["id"])
        else:
            await emit(rid, "watch", "no_change", f"{c['name']}: no change since last snapshot",
                       f"{len(urls)} pages compared", company_id=c["id"])

    await asyncio.gather(*(one(c) for c in comps), return_exceptions=True)
    return changed


async def watch_all():
    """One Watcher pass over every finished research (scheduled loop and POST /api/watcher/run)."""
    store = get_store()
    for r in await store.list_researches():
        if r["status"] == "done" and not r["is_recorded"]:
            await check_changes(r["id"], await store.research_companies(r["id"]))


def _ago(ts) -> str:
    if not ts:
        return "earlier"
    s = (now() - ts).total_seconds()
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} minutes ago"
    if s < 86400:
        return f"{int(s // 3600)} hours ago"
    d = int(s // 86400)
    return "yesterday" if d == 1 else f"{d} days ago"


ago = _ago
