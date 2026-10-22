"""The hunt: Scout → parallel Researchers → Analyst → Strategist, plus Continue mode and the Watcher.

Every step writes to the store and emits an event before moving on, so nothing is lost if the
browser disconnects and a failed competitor never fails the whole run.
"""
import asyncio
import hashlib
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
        await emit(rid, "strat", "run_error", f"The hunt stopped: {type(e).__name__}. Saved results are kept.",
                   str(e)[:200])
        for attempt in range(8):  # the network may be the reason it failed: keep trying to record the status
            try:
                await store.update_research(rid, status="failed", finished_at=now())
                break
            except Exception:
                await asyncio.sleep(min(30, 2 ** attempt))


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
    # What the model already knows, in parallel with the live web search (the Scout reconciles both).
    known_task = asyncio.create_task(llm().known_players(query, rid))
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
    try:
        known = await known_task
    except Exception:
        known = []
    scout = await llm().scout(query, results, rid, guides=guides, known=known)
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
                                                          "sim": c["sim"], "ang": c["angle"], "domain": c["domain"]})
        await _mock_pause(0.45)

    # Map pins: geocode the Scout's addresses in the background while the researchers read.
    geo = asyncio.create_task(_geocode_all(rid, companies, ready))

    # Memory: pages we already read for these competitors (one query, overlaps with the Scout's writes).
    try:
        memory = await store.page_memory([c["id"] for c in companies])
    except Exception:
        memory = {}
    known = sum(1 for (cid, _) in memory if any(c["id"] == cid for c in companies))
    if known:
        await emit(rid, "analyst", "memory_match", f"Memory: {known} pages already read for these competitors",
                   "unchanged pages are reused without calling Claude", status="Loading memory")

    # 2. Researchers, all in parallel; one failing never stops the others
    await emit(rid, "research", "agent_status", "", "", status=f"Reading pages for {len(companies)} competitors")
    await asyncio.gather(*(_research_company(rid, query, c, i, ready, memory) for i, c in enumerate(companies)))
    await geo

    # 3 + 4. Analyst, Strategist
    await _analyze_and_strategize(rid, query, companies, ready)

    await _finish(rid, t0, mode="fresh", companies=companies, reused=sum(c.get("reused", 0) for c in companies))


async def _research_company(rid: str, query: str, comp: dict, i: int, ready: asyncio.Task | None = None,
                            memory: dict | None = None):
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
                seen = (memory or {}).get((comp["id"], url))
                if seen and seen["hash"] == hashlib.sha256(text.encode()).hexdigest() and seen["facts"]:
                    rows = seen["facts"]  # unchanged since we last read it: reuse, no Claude call
                    comp["facts"] += [{**r, "source": url} for r in rows]
                    comp["reused"] = comp.get("reused", 0) + len(rows)
                    await emit(rid, "research", "fact_added", f"{comp['name']}: unchanged, {len(rows)} facts from memory",
                               f"{url} · memory", company_id=comp["id"], count=len(rows), reused=True)
                    return len(rows)
                ext = await llm().extract(query, comp, url, text, rid)
                rows = [{"kind": f.kind, "text": f.text, "value": f.value, "item": f.item} for f in ext.facts]
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


async def _finish(rid: str, t0: float, mode: str, reused: int = 0, companies: list[dict] | None = None,
                  base_stats: dict | None = None):
    store = get_store()
    secs = round(time.perf_counter() - t0, 1)  # what the user waited for
    await settle(rid)  # every background write has landed before we call it done
    if companies is not None and all("facts" in c for c in companies):
        n_comps, facts = len(companies), sum(len(c["facts"]) for c in companies)
    else:
        comps = await store.research_companies(rid)
        n_comps, facts = len(comps), await store.count_facts([c["id"] for c in comps])
    stats = await store.llm_stats(rid)
    if base_stats:  # continue mode: report this run's calls and cost, not the research's lifetime totals
        stats = {k: round(stats[k] - base_stats.get(k, 0), 4) for k in stats}
    totals = {"comps": n_comps, "facts": facts, "srcs": max(1, facts // 3), "secs": secs, "reused": reused,
              "llm": stats}
    await store.update_research(rid, status="done", finished_at=now(), totals=totals)
    try:
        await store.log_knowledge(rid, "hunt" if mode == "fresh" else mode)
    except Exception:
        log.warning("knowledge log failed for %s", rid)
    await emit(rid, "strat", "run_done", "", "", mode=mode, **totals)


# ---------------------------------------------------------------- continue + watcher
async def _continue(rid: str, query: str):
    store, t0 = get_store(), time.perf_counter()
    r, comps, base_stats = await asyncio.gather(store.get_research(rid), store.research_companies(rid),
                                                store.llm_stats(rid))
    reused = await store.count_facts([c["id"] for c in comps])
    await emit(rid, "analyst", "memory_match", f"Found your research from {_ago(r['finished_at'])}. Reusing "
               f"{reused} facts from memory instead of starting over", "pgvector similarity · postgres",
               status="Loading memory")
    for c in comps:
        n = await store.count_facts([c["id"]])
        await emit(rid, "analyst", "competitor_found", "", "", quiet=True, facts=n,
                   company={"id": c["id"], "n": c["name"], "loc": c["location"], "sim": c["similarity"],
                            "ang": c["angle"], "domain": c["domain"]})
        await _mock_pause(0.15)
    await emit(rid, "watch", "agent_status", "", "", status="Checking what changed")
    changed = await check_changes(rid, comps)
    if changed and web.live():
        await _analyze_and_strategize(rid, query, comps)
    else:
        m = (await store.get_research(rid))["market"]
        await emit(rid, "strat", "insight", f"Plan still holds: {m.get('plan', '').removeprefix('Plan: ')}",
                   f"{len(changed)} dossiers updated")
    await _finish(rid, t0, mode="continue", reused=reused, base_stats=base_stats)


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
        urls = [u for (cid, u) in memory if cid == c["id"]][: settings.pages_per_company]
        pages = await asyncio.gather(*(_fetch_page(u) for u in urls))  # all pages at once

        async def diff(page) -> int:
            url, text, fetcher = page
            seen = memory[(c["id"], url)]
            if hashlib.sha256(text.encode()).hexdigest() == seen["hash"]:
                return 0  # identical page: nothing to read, nothing to write
            # The page differs, but dynamic pages (stores, reviews) change on every visit: ask what MATTERS.
            found = await llm().changes(c, url, [f["text"] for f in seen["facts"]], text, rid)
            rows = [{"kind": "change", "text": t, "value": "", "item": ""} for t in found]
            later(rid, lambda: store.save_page(c["id"], rid, url, text, fetcher, rows))  # new fingerprint
            if not found:
                return 0
            note = found[0]
            later(rid, lambda: store.add_alert(c["id"], note))
            later(rid, lambda: store.update_link(rid, c["id"], {"tl": [["just now", note]], "change": note[:40]}))
            await emit(rid, "watch", "change_detected", f"{c['name']} changed: {note}", f"snapshot diff · {url}",
                       company_id=c["id"])
            return 1

        diffs = sum(await asyncio.gather(*(diff(p) for p in pages if p)))
        if diffs:
            changed.append(c["id"])
        else:
            await emit(rid, "watch", "no_change", f"{c['name']}: no change since last snapshot",
                       f"{sum(1 for p in pages if p)} pages compared", company_id=c["id"])

    memory = await store.page_memory([c["id"] for c in comps]) if web.live() else {}
    await asyncio.gather(*(one(c) for c in comps), return_exceptions=True)
    await settle(rid)
    return changed


# ---------------------------------------------------------------- enrich (the project keeps growing)
async def start_enrich(rid: str) -> dict:
    after = await bus.last_event_id(rid)
    await get_store().update_research(rid, status="running")
    spawn(_guard(rid, _enrich(rid)))
    return {"id": rid, "after": after}


async def _enrich(rid: str):
    """Go back to every competitor and fetch what's still missing: prices/menu, reviews, news.

    Pages already read (page memory) are skipped, so every round adds new sources instead of repeating."""
    store, t0 = get_store(), time.perf_counter()
    r, comps, base_stats = await asyncio.gather(store.get_research(rid), store.research_companies(rid),
                                                store.llm_stats(rid))
    facts_by = await store.report_facts([c["id"] for c in comps], per_company=200)
    memory = await store.page_memory([c["id"] for c in comps]) if web.live() else {}
    if not any(k["research_id"] == rid for k in await store.knowledge_rows()):
        await store.log_knowledge(rid, "hunt")  # starting point of the growth curve (older researches)
    await emit(rid, "research", "agent_status", f"Enriching {len(comps)} competitors: looking for what's missing",
               "prices · menus · reviews · news", status="Enriching the research")

    async def one(c, i):
        have = {f["kind"] for f in facts_by[c["id"]]["latest"]}
        priced = sum(1 for f in facts_by[c["id"]]["latest"] if f["kind"] == "price" and f.get("item"))
        where = c.get("location") or ""
        wants = []
        if priced < 5:
            wants.append(f"{c['name']} {where} menu prix tarifs pricing")
        if "review" not in have:
            wants.append(f"{c['name']} {where} avis reviews")
        wants.append(f"{c['name']} {where} news 2026")
        if not web.live():
            await _mock_pause(0.3 * i)
            await emit(rid, "research", "fact_added", f"{c['name']}: nothing new in recorded mode", "", company_id=c["id"], count=0)
            return 0
        results = await asyncio.gather(*(web.search(q) for q in wants), return_exceptions=True)
        urls = []
        for res in results:
            for x in res if isinstance(res, list) else []:
                if (c["id"], x["url"]) not in memory and x["url"] not in urls:
                    urls.append(x["url"])
        pages = [p for p in await asyncio.gather(*(_fetch_page(u) for u in urls[:4])) if p]
        added = 0
        for (url, text, fetcher), ext in zip(pages, await asyncio.gather(
                *(llm().extract(r["query"], c, u, t, rid) for u, t, _ in pages), return_exceptions=True)):
            if isinstance(ext, Exception) or not ext.facts:
                continue
            rows = [{"kind": f.kind, "text": f.text, "value": f.value, "item": f.item} for f in ext.facts]
            later(rid, lambda url=url, text=text, fetcher=fetcher, rows=rows:
                  store.save_page(c["id"], rid, url, text, fetcher, rows))
            added += len(rows)
            await emit(rid, "research", "fact_added", f"{c['name']}: {rows[0]['text']}", f"{url} · new source",
                       company_id=c["id"], count=len(rows))
        return added

    added = sum(n for n in await asyncio.gather(*(one(c, i) for i, c in enumerate(comps)), return_exceptions=True)
                if isinstance(n, int))
    await settle(rid)
    if added and web.live():  # refresh the dossiers, prices and the gap with the new facts
        await _analyze_and_strategize(rid, r["query"], comps)
    else:
        await emit(rid, "strat", "insight", f"Enrich round done: {added} new facts", "", status="Done")
    await _finish(rid, t0, mode="enrich", reused=0, base_stats=base_stats)


async def upgrade_next() -> str | None:
    """Upgrader agent: enrich the live research that has gone longest without new data."""
    store = get_store()
    rows = [r for r in await store.library_rows() if r["status"] == "done" and not r["is_recorded"]]
    if not rows:
        return None
    last = {}
    for k in await store.knowledge_rows():
        last[k["research_id"]] = k["created_at"]
    oldest = min(rows, key=lambda r: last.get(r["id"]) or r["finished_at"] or r["started_at"])
    log.info("upgrader: enriching %s (%s)", oldest["id"], oldest["query"][:50])
    await start_enrich(oldest["id"])
    return oldest["id"]


async def make_plan(rid: str) -> dict:
    """Planner agent: build (or rebuild) the ideal business plan from everything stored for this research."""
    from ..views import report
    store = get_store()
    rep_ = await report(rid)
    facts_by = await store.report_facts([c["id"] for c in rep_["C"]], per_company=25)
    comps = [{"name": c["n"], "loc": c["loc"], "rating": c["rating"], "complaint": c["complaint"],
              "facts": facts_by[c["id"]]["latest"]} for c in rep_["C"]]
    if not web.live():
        raise RuntimeError("The Planner needs the live Claude key")
    p = await llm().plan(rep_["q"], rep_["market"], comps, rid)
    doc = p.model_dump()
    if p.location.is_physical:  # green zone on the map
        ll = await web.geocode(p.location.map_query) if p.location.map_query else None
        if not ll:
            ll = await web.geocode(re.split(r"[/(]", p.location.area)[0].strip(" ,"))
        doc["location"]["ll"] = list(ll) if ll else None
    market = {**rep_["market"], "biz_plan": doc}
    await store.update_research(rid, market=market)
    await settle(rid)
    return doc


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
