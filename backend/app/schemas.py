"""Structured outputs the agents must return. Validated with Pydantic; the JSON schema is sent to Claude."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- Scout ----
class Candidate(Strict):
    name: str
    domain: str = Field(description="Bare domain of the company's main website, e.g. hinge.co")
    why: str = Field(description="One sentence: why this is a competitor")
    similarity: float = Field(description="0 to 1, how close it is to the user's idea")
    location_hint: str = Field(description="City or street if known, else empty string")


class ScoutResult(Strict):
    market_label: str = Field(description="Short market name, e.g. 'Dating apps · Paris'")
    candidates: list[Candidate]


class KnownPlayers(Strict):
    names: list[str] = Field(description="Established direct competitors, most important first")


# ---- Researcher (page -> facts) ----
FactKind = Literal["price", "feature", "hiring", "review", "location", "funding", "change", "other"]


class ExtractedFact(Strict):
    kind: FactKind
    text: str = Field(description="One self-contained factual sentence, taken only from the page")
    value: str = Field(description="Key value if any: for prices the number only (e.g. '14.50'), else a city, "
                                   "a number, or empty string")
    item: str = Field(description="For prices and features: the product, plan or menu item it is about "
                                  "(e.g. 'Tonkotsu ramen', 'Pro plan'), else empty string")


class PageExtraction(Strict):
    facts: list[ExtractedFact]


class PageChanges(Strict):
    changes: list[str] = Field(description="Meaningful changes vs the known facts, one sentence each; empty if none")


# ---- Analyst (whole market) ----
class PlanPrice(Strict):
    name: str
    price: str = Field(description="Number as text without currency, '0' for free")


class CompanyAnalysis(Strict):
    domain: str
    location: str = Field(description="HQ city for online businesses, street address for local ones")
    x: float = Field(description="Value on the market's x axis (usually a price)")
    y: float = Field(description="0 to 1 on the market's y axis")
    scale: str = Field(description="Users, rating count or similar size signal")
    hiring: str
    complaint: str = Field(description="Most common customer complaint, short")
    rating: float = Field(description="Average review score out of 5, 0 if unknown")
    summary: str
    plans: list[PlanPrice]
    quotes: list[str] = Field(description="Up to 2 short customer quotes found in the facts")
    change: str = Field(description="Most recent notable change, or 'None'")


class MarketFrame(Strict):
    x_label: str
    unit: str = Field(description="Price unit suffix like '/mo', or empty string")
    x_min: float
    x_max: float
    y_top: str
    y_bot: str
    loc_label: str
    scale_label: str


class Analysis(Strict):
    frame: MarketFrame
    companies: list[CompanyAnalysis]


# ---- Strategist ----
class Gap(Strict):
    x0: float
    x1: float
    y0: float
    y1: float
    label: str


class CompanyStrategy(Strict):
    domain: str
    beat: list[str] = Field(description="1 to 3 concrete ways to beat this competitor")


class Strategy(Strict):
    headline: str = Field(description="The gap in one sentence")
    detail: str = Field(description="Two or three sentences of evidence")
    plan: str = Field(description="One sentence starting with 'Plan:'")
    you_x: float
    you_y: float
    gap: Gap
    companies: list[CompanyStrategy]
