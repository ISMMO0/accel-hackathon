"""MCP server (streamable HTTP, JSON responses) so any agent (Claude, Codex, Cursor, ChatGPT...) can use the
user's Launch Spy projects as tools. Mounted at POST /mcp.

Minimal JSON-RPC 2.0: initialize, tools/list, tools/call, ping; notifications get 202 with no body.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import views
from .pipeline import chat, hunt
from .store import get_store

router = APIRouter()
PROTOCOL = "2025-06-18"


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


ID = {"project_id": {"type": "string", "description": "Project id from list_projects"}}
TOOLS = [
    {"name": "list_projects", "description": "List the user's competitive-research projects (id, title, size).",
     "inputSchema": _schema({}, [])},
    {"name": "get_project", "description": "A project's full report: market gap, competitors (prices, ratings, "
                                           "complaints, location, how to beat them) and its business plan.",
     "inputSchema": _schema(ID, ["project_id"])},
    {"name": "search_facts", "description": "Search a project's stored, sourced facts (prices, menus, reviews, "
                                            "hiring...). Returns fact, competitor, item, value and source URL.",
     "inputSchema": _schema({**ID, "query": {"type": "string"}}, ["project_id", "query"])},
    {"name": "ask_project", "description": "Ask the project's analyst agent a question. It answers from stored "
                                           "facts, searches the web if needed, and saves what it learns.",
     "inputSchema": _schema({**ID, "question": {"type": "string"}}, ["project_id", "question"])},
    {"name": "start_research", "description": "Start a new competitive research for a business idea "
                                              "(runs ~1-2 min in the background). Returns the new project id.",
     "inputSchema": _schema({"idea": {"type": "string"}}, ["idea"])},
    {"name": "enrich_project", "description": "Send the agents back to every competitor for missing prices, menus, "
                                              "reviews and news (background, ~1-2 min).",
     "inputSchema": _schema(ID, ["project_id"])},
    {"name": "business_plan", "description": "Build the ideal business plan for a project (pricing, location, launch "
                                             "phases, how to beat each competitor). Takes ~1 min.",
     "inputSchema": _schema(ID, ["project_id"])},
]


async def _call(name: str, a: dict):
    store = get_store()
    if name == "list_projects":
        return [{"project_id": r["id"], "title": r["label"] or r["q"], "idea": r["q"], "status": r["status"],
                 "competitors": r["comps"], "facts": r["facts"]} for r in await views.library()]
    if name == "get_project":
        r = await views.report(a["project_id"])
        if not r:
            raise ValueError("unknown project_id")
        return {"title": r["label"] or r["q"], "idea": r["q"], "gap": r["market"].get("verdict"),
                "plan_line": r["market"].get("plan"), "business_plan": r["market"].get("biz_plan"),
                "competitors": [{k: c[k] for k in ("n", "domain", "loc", "rating", "complaint", "plans", "beat", "facts")}
                                for c in r["C"]]}
    if name == "search_facts":
        comps = await store.research_companies(a["project_id"])
        names = {c["id"]: c["name"] for c in comps}
        words = [w.lower() for w in a["query"].split() if len(w) > 2]
        rows = [f for f in await store.all_facts(list(names))
                if not words or any(w in (f["text"] + " " + f.get("item", "")).lower() for w in words)]
        return [{"competitor": names[f["company_id"]], "kind": f["kind"], "item": f.get("item", ""),
                 "value": f["value"], "fact": f["text"], "source": f["source"]} for f in rows[:60]]
    if name == "ask_project":
        out = await chat.answer(a["project_id"], a["question"])
        return {"answer": out["a"], "sources": out.get("cites", []), "saved": out.get("upd", "")}
    if name == "start_research":
        r = await hunt.start(a["idea"], mode="fresh")
        return {"project_id": r["id"], "status": "running", "hint": "call get_project in ~2 minutes"}
    if name == "enrich_project":
        return await hunt.start_enrich(a["project_id"])
    if name == "business_plan":
        return await hunt.make_plan(a["project_id"])
    raise ValueError(f"unknown tool {name}")


@router.post("/mcp")
async def mcp(request: Request):
    from .config import settings
    if settings.mcp_token:  # public deploys: agents must send the token (header or ?token=)
        sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if sent != settings.mcp_token:
            return JSONResponse({"error": "missing or wrong MCP token"}, status_code=401)
    msg = await request.json()
    if "id" not in msg:  # notification (e.g. notifications/initialized)
        return Response(status_code=202)
    mid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}

    def ok(result):
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": result})

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion", PROTOCOL),
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "before-you-start", "version": "0.1.0"},
                   "instructions": "Before You Start: competitive research projects built by AI agents from live web data. "
                                   "Start with list_projects, then get_project or search_facts."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            data = await _call(params.get("name"), params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]})
        except Exception as e:
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}})
