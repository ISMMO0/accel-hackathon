"""The state: the shared memory every agent of a graph reads from and writes to.

LangGraph passes the state to each node (agent). A node returns only the keys it changed, and LangGraph
merges them into the state. By default a new value REPLACES the old one. A "reducer" changes that rule
for one key: `Annotated[list, operator.add]` means "concatenate", so many agents running in parallel can
each add their part without overwriting each other.
"""
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_dicts(old: dict, new: dict) -> dict:
    """Reducer for dicts written by parallel nodes: keep both."""
    return {**old, **new}


# ---------------------------------------------------------------- hunt graph
class HuntState(TypedDict, total=False):
    """State of one research run. The checkpointer saves it after every step (thread_id = research id)."""

    research_id: str
    query: str
    started_at: float  # time.time() when the run started

    companies: list[dict]  # written by the Scout

    # written by the Researchers, in parallel -> reducers
    facts: Annotated[list[dict], operator.add]  # every fact, with its company_id and source page
    reused: Annotated[int, operator.add]  # facts reused from memory (unchanged pages: no Claude call)

    # written by the Geocoder, in parallel with the Researchers
    locations: Annotated[dict, merge_dicts]  # {company_id: [lat, lng]}

    analysis: dict  # written by the Analyst (the market map)
    strategy: dict  # written by the Strategist (the gap, how to beat each competitor)


class ResearcherInput(TypedDict):
    """What `Send` hands to ONE Researcher: its own small input, not the whole state."""

    research_id: str
    query: str
    company: dict
    index: int


class GeocoderInput(TypedDict):
    research_id: str
    companies: list[dict]


# ---------------------------------------------------------------- chat graph
class ChatState(TypedDict):
    """State of one chat thread (one per research). The checkpointer keeps it between questions:
    that is the chat's short-term memory."""

    research_id: str
    context: str  # system prompt with the research's stored facts, rebuilt for every question
    # add_messages is LangGraph's reducer for conversations: appends new messages to the history
    messages: Annotated[list[AnyMessage], add_messages]
