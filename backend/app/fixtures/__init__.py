"""Recorded market data used by mock providers and the demo seed. Illustrative only, not verified."""
import json
import re
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("markets.json")


@lru_cache
def markets() -> dict:
    return json.loads(_PATH.read_text())


def market_for(query: str) -> str:
    return "coffee" if re.search(r"caf|coffee|espresso|barista|17e|17th", query, re.I) else "dating"


def domain_of(c: dict) -> str:
    """Fixture companies have no domain; derive a stable fake one from the id."""
    return c.get("domain") or f"{c['id']}.example"


def company_by_domain(domain: str) -> tuple[str, dict] | None:
    for key, m in markets().items():
        for c in m["C"]:
            if domain_of(c) == domain:
                return key, c
    return None
