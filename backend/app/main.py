"""Launch Spy API + static frontend. Run: uvicorn app.main:app --reload"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bus, views
from .config import settings
from .pipeline import chat, hunt
from .providers import web
from .store import get_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).with_name("static")

SEED = [
    ("I want to open a specialty coffee shop in Paris 17e", 1),
    ("A dating app for young professionals in Paris", 3),
]


async def seed():
    """Recorded demo researches so the library, report and map are full from minute one."""
    store = get_store()
    existing = {r["query"] for r in await store.list_researches()}
    t1, t2 = web.FORCE_MOCK.set(True), web.SPEED.set(0)
    try:
        for query, days in reversed(SEED):
            if query in existing:
                continue
            r = await store.create_research(query=query, embedding=await web.embed(query), is_recorded=True)
            bus.new_research(r["id"])
            await hunt._fresh(r["id"], query)
            when = datetime.now(timezone.utc) - timedelta(days=days)
            await store.update_research(r["id"], finished_at=when, started_at=when, seen_at=when)
            await hunt.check_changes(r["id"], await store.research_companies(r["id"]))  # "changes since last visit"
    finally:
        web.FORCE_MOCK.reset(t1)
        web.SPEED.reset(t2)


async def watcher_loop():
    while True:
        await asyncio.sleep(settings.watch_interval_min * 60)
        try:
            await hunt.watch_all()
        except Exception:
            logging.exception("watcher pass failed")


async def upgrader_loop():
    while True:
        await asyncio.sleep(settings.upgrade_interval_min * 60)
        if not settings.live()["llm"]:
            continue
        try:
            await hunt.upgrade_next()
        except Exception:
            logging.exception("upgrader pass failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    await store.init()
    await store.mark_interrupted()  # hunts cut off by a restart: say so instead of "running" forever
    if settings.seed_demo:
        await seed()
    if settings.watch_interval_min > 0:
        hunt.spawn(watcher_loop())
    if settings.upgrade_interval_min > 0:
        hunt.spawn(upgrader_loop())
    logging.info("Launch Spy ready · store=%s · live=%s", store.kind, settings.live())
    yield
    await bus.flush()
    await store.close()


app = FastAPI(title="Launch Spy", lifespan=lifespan)


class ResearchIn(BaseModel):
    query: str
    mode: str | None = None  # None = check memory first; "fresh" | "continue"
    parent_id: str | None = None


class ChatIn(BaseModel):
    message: str


@app.get("/api/health")
async def health():
    return {"ok": True, "store": get_store().kind, "live": settings.live(),
            "upgrader_every_min": settings.upgrade_interval_min}


@app.post("/api/research")
async def create_research(body: ResearchIn):
    q = body.query.strip()
    if not q:
        raise HTTPException(400, "Describe a business or idea first.")
    if body.mode == "check":  # the UI asks before every run: new project or an existing one?
        m = await hunt.memory_match(q)
        return {"match": {"id": m["research"]["id"], "score": m["score"]} if m else None}
    if body.mode is None and (m := await hunt.memory_match(q)):
        r = m["research"]
        comps = await get_store().research_companies(r["id"])
        return {"match": {"id": r["id"], "q": r["query"], "when": hunt.ago(r["finished_at"]), "score": m["score"],
                          "comps": len(comps), "facts": await get_store().count_facts([c["id"] for c in comps])}}
    return await hunt.start(q, mode=body.mode or "fresh", parent_id=body.parent_id)


@app.get("/api/research/{rid}")
async def get_research(rid: str, seen: bool = False):
    data = await views.report(rid)
    if not data:
        raise HTTPException(404, "Research not found")
    if seen and data["status"] == "done":
        await get_store().update_research(rid, seen_at=datetime.now(timezone.utc))
    return data


@app.get("/api/research/{rid}/events")
async def events(rid: str, request: Request, after: int = 0):
    """Server-Sent Events. Replays everything after `after` (or Last-Event-ID), then streams live."""
    store = get_store()
    if not await store.get_research(rid):
        raise HTTPException(404, "Research not found")
    last = int(request.headers.get("last-event-id") or after)

    async def stream():
        nonlocal last
        idle = 0.0
        while not await request.is_disconnected():
            rows = await bus.events_after(rid, last)
            for e in rows:
                last = e["id"]
                data = {k: e[k] for k in ("id", "agent", "type", "payload")}
                yield f"id: {e['id']}\ndata: {json.dumps(data, default=str)}\n\n"
            if rows:
                idle = 0.0
                if rows[-1]["type"] in ("run_done", "run_error"):
                    yield "event: end\ndata: {}\n\n"
                    return
            await bus.wait(rid, timeout=1.0)
            idle += 1.0
            if idle >= 15:
                idle = 0.0
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/library")
async def library():
    return await views.library()


@app.post("/api/transcribe")
async def transcribe(request: Request, lang: str = "any"):
    """Voice input: the browser posts a WAV recording, Gradium speech-to-text returns the text.

    The API key stays on the server. Response from Gradium is NDJSON; we join the "text" segments."""
    if not settings.gradium_api_key:
        raise HTTPException(503, "Voice transcription is not configured")
    audio = await request.body()
    if not audio or len(audio) > 15_000_000:
        raise HTTPException(400, "Send a WAV recording under 15 MB")
    lang = lang if lang in ("en", "fr", "de", "es", "pt") else "any"
    r = await web.http().post("https://api.gradium.ai/api/post/speech/asr",
                              params={"json_config": json.dumps({"language": lang})},
                              headers={"x-api-key": settings.gradium_api_key, "Content-Type": "audio/wav"},
                              content=audio, timeout=60)
    if r.status_code != 200:
        raise HTTPException(502, f"Transcription failed ({r.status_code})")
    parts = []
    for line in r.text.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("type") == "text" and m.get("text"):
            parts.append(m["text"].strip())
    return {"text": " ".join(parts).strip()}


@app.post("/api/research/{rid}/chat")
async def research_chat(rid: str, body: ChatIn):
    if not await get_store().get_research(rid):
        raise HTTPException(404, "Research not found")
    if not body.message.strip():
        raise HTTPException(400, "Ask something first.")
    return await chat.answer(rid, body.message.strip())


@app.post("/api/research/{rid}/enrich")
async def enrich(rid: str):
    """Send the agents back to every competitor to find what's still missing."""
    r = await get_store().get_research(rid)
    if not r:
        raise HTTPException(404, "Research not found")
    if r["status"] == "running":
        raise HTTPException(409, "Agents are already working on this research")
    return await hunt.start_enrich(rid)


@app.get("/api/research/{rid}/facts")
async def research_facts(rid: str):
    """Every stored fact of a research (the Data tab): company, kind, item, value, text, source."""
    store = get_store()
    comps = await store.research_companies(rid)
    names = {c["id"]: c["name"] for c in comps}
    rows = await store.all_facts(list(names))
    return [{"company": names[f["company_id"]], "company_id": f["company_id"], "kind": f["kind"],
             "item": f.get("item", ""), "value": f["value"], "text": f["text"], "source": f["source"]} for f in rows]


@app.get("/api/knowledge")
async def knowledge():
    """How much each research knows after every hunt / enrich / chat round (Evidence page growth chart)."""
    return await get_store().knowledge_rows()


@app.get("/api/eval")
async def eval_results():
    """Latest evaluation run (scripts/eval.py): Launch Spy vs plain Claude + memory curve."""
    return await get_store().eval_rows()


@app.post("/api/upgrade/run")
async def run_upgrader():
    """Run the Upgrader agent now (it also runs every UPGRADE_INTERVAL_MIN minutes)."""
    rid = await hunt.upgrade_next()
    return {"research_id": rid, "every_min": settings.upgrade_interval_min}


@app.post("/api/watcher/run")
async def run_watcher():
    hunt.spawn(hunt.watch_all())
    return {"started": True}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
