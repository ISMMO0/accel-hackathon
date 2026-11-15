"""The nodes of the hunt graph: one async function per agent.

A node receives the state (or, for nodes started with `Send`, its own small input), does its work by
calling the agent functions in app/pipeline/hunt.py, and returns ONLY the state keys it changed.
"""
import logging

from ..pipeline import hunt
from ..providers import web
from ..schemas import Analysis
from .state import GeocoderInput, HuntState, ResearcherInput

log = logging.getLogger("nodes")


# 1. Scout --------------------------------------------------------------------------------------------
async def scout(state: HuntState) -> dict:
    """Finds the real competitors (model knowledge + live web search + guides)."""
    companies = await hunt.scout_companies(state["research_id"], state["query"])
    return {"companies": companies}


# 2a. Researcher (one per competitor, all in parallel) ------------------------------------------------
async def researcher(inp: ResearcherInput) -> dict:
    """Reads ONE competitor's pages and extracts facts. Never raises: a failing site keeps partial facts
    and must not fail the other Researchers running in the same step."""
    company = dict(inp["company"])
    facts = await hunt.research_company(inp["research_id"], inp["query"], company, inp["index"])
    tagged = [{**f, "company_id": company["id"]} for f in facts]
    return {"facts": tagged, "reused": company.get("reused", 0)}  # reducers add these to the state


# 2b. Geocoder (in parallel with the Researchers) ------------------------------------------------------
async def geocoder(inp: GeocoderInput) -> dict:
    """Map pins from the Scout's addresses, while the Researchers read."""
    locations = {}
    for c in inp["companies"]:
        if ll := await hunt.geocode_company(inp["research_id"], c):
            locations[c["id"]] = ll
    return {"locations": locations}


# 3. Analyst ------------------------------------------------------------------------------------------
async def analyst(state: HuntState) -> dict:
    """Runs once ALL Researchers are done: places every competitor on the same market map."""
    companies = _with_locations(state)
    facts_by = {c["id"]: [f for f in state.get("facts", []) if f["company_id"] == c["id"]][:15] for c in companies}
    analysis = await hunt.analyze_market(state["research_id"], state["query"], companies, facts_by)
    return {"analysis": analysis.model_dump()}  # plain dicts in the state: the checkpointer saves them as JSON


# 4. Strategist ---------------------------------------------------------------------------------------
async def strategist(state: HuntState) -> dict:
    """Finds the gap and how to beat each competitor, from the Analyst's map."""
    analysis = Analysis.model_validate(state["analysis"])
    s = await hunt.strategize_market(state["research_id"], state["query"], _with_locations(state), analysis,
                                     n_facts=min(len(state.get("facts", [])), 15 * len(state["companies"])))
    return {"strategy": s.model_dump()}


# 5. Finish: the report is ready ----------------------------------------------------------------------
async def finish(state: HuntState) -> dict:
    """Waits for every database write, saves the totals, tells the UI the run is done."""
    companies = [{**c, "facts": [f for f in state.get("facts", []) if f["company_id"] == c["id"]]}
                 for c in state["companies"]]
    await hunt._finish(state["research_id"], state["started_at"], mode="fresh", companies=companies,
                       reused=state.get("reused", 0), plan=False)  # the Planner is the next node
    return {}


# 6. Planner ------------------------------------------------------------------------------------------
async def planner(state: HuntState) -> dict:
    """Writes the ideal business plan from everything stored. Runs after the report is shown, so the user
    is not waiting for it; the UI shows "planning..." meanwhile."""
    rid = state["research_id"]
    if not web.live():  # recorded demo data: no plan without the real Claude key
        return {}
    hunt._planning.add(rid)
    try:
        await hunt.make_plan(rid)
    except Exception:
        log.exception("planner failed for %s", rid)  # the report is already done: never fail the run here
    finally:
        hunt._planning.discard(rid)
    return {}


def _with_locations(state: HuntState) -> list[dict]:
    """The Scout's companies with the Geocoder's map pins."""
    locs = state.get("locations", {})
    return [{**c, "lat": locs.get(c["id"], [None, None])[0], "lng": locs.get(c["id"], [None, None])[1]}
            for c in state["companies"]]
