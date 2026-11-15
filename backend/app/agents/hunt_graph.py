"""The hunt graph: how the agents are wired together with LangGraph.

                         START
                           |
                         scout
                           |  fan_out(): one Send per competitor + one for the map pins
          +-----------+----+------+-----------+
          |           |           |           |
     researcher  researcher  researcher    geocoder        <- same step: they run in parallel
          |           |           |           |
          +-----------+----+------+-----------+
                           |  LangGraph waits for ALL of them (end of the step)
                        analyst
                           |
                       strategist
                           |
                        finish        <- report done, UI shows it
                           |
                        planner       <- business plan, in the background of the user's reading
                           |
                          END

Read in order: 1. build the graph  2. run it  3. resume it after a crash.
"""
import time

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import nodes
from .checkpointer import get_checkpointer
from .state import HuntState


# ---------------------------------------------------------------- 1. build
def fan_out(state: HuntState) -> list[Send]:
    """Conditional edge after the Scout. Each `Send(node, input)` starts one copy of that node with its
    own input. All of them run in the same step, so in parallel."""
    rid, query = state["research_id"], state["query"]
    sends = [Send("researcher", {"research_id": rid, "query": query, "company": c, "index": i})
             for i, c in enumerate(state["companies"])]
    sends.append(Send("geocoder", {"research_id": rid, "companies": state["companies"]}))
    return sends


def build_hunt_graph():
    builder = StateGraph(HuntState)

    # the agents (nodes)
    builder.add_node("scout", nodes.scout)
    builder.add_node("researcher", nodes.researcher)
    builder.add_node("geocoder", nodes.geocoder)
    builder.add_node("analyst", nodes.analyst)
    builder.add_node("strategist", nodes.strategist)
    builder.add_node("finish", nodes.finish)
    builder.add_node("planner", nodes.planner)

    # the order (edges)
    builder.add_edge(START, "scout")
    builder.add_conditional_edges("scout", fan_out, ["researcher", "geocoder"])  # parallel fan-out
    builder.add_edge("researcher", "analyst")  # fan-in: the analyst runs once, after the whole step
    builder.add_edge("geocoder", "analyst")
    builder.add_edge("analyst", "strategist")
    builder.add_edge("strategist", "finish")
    builder.add_edge("finish", "planner")
    builder.add_edge("planner", END)

    # compile = check the wiring and attach the checkpointer (state saved after every step)
    return builder.compile(checkpointer=get_checkpointer())


_graph = None


def hunt_graph():
    global _graph
    if _graph is None:  # built once, after the checkpointer is open
        _graph = build_hunt_graph()
    return _graph


# ---------------------------------------------------------------- 2. run
def _config(rid: str) -> dict:
    # thread_id: which saved state to use. One thread per research.
    return {"configurable": {"thread_id": rid}, "run_name": "hunt", "metadata": {"research_id": rid}}


async def run_hunt(rid: str, query: str) -> dict:
    """A fresh hunt. Live progress reaches the UI through the events each agent emits (SSE)."""
    start = {"research_id": rid, "query": query, "started_at": time.time()}
    return await hunt_graph().ainvoke(start, _config(rid))


# ---------------------------------------------------------------- 3. resume
async def can_resume(rid: str) -> bool:
    snapshot = await hunt_graph().aget_state(_config(rid))
    return bool(snapshot.next)  # the nodes that still have to run; empty = finished or never started


async def resume_hunt(rid: str) -> dict:
    """Continues a run cut by a crash or restart from its last saved step. Steps already done (Scout,
    finished Researchers...) are NOT run again: their results are in the checkpoint."""
    return await hunt_graph().ainvoke(None, _config(rid))  # input None = "continue from the checkpoint"
