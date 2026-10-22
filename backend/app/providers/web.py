"""Web search (Keenable → Tavily → Claude), page fetching (Keenable → Jina → Firecrawl → H Company),
embeddings and geocoding.

Each function has a real path and a fixture path; the fixture path is used whenever the hunt runs
without a Claude key (see settings.live()).
"""
import asyncio
import contextvars
import hashlib
import math
import re
import time

import httpx

from .. import fixtures
from ..config import settings
from . import cache

_http: httpx.AsyncClient | None = None
_fetch_sem = asyncio.Semaphore(settings.fetch_concurrency)
_h_sem = asyncio.Semaphore(settings.h_concurrency)
_geo_lock = asyncio.Lock()
_geo_last = 0.0


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=True,
                                  headers={"User-Agent": settings.geocode_user_agent})
    return _http


# Seeding demo data forces the recorded path even when keys are set, and can run it instantly.
FORCE_MOCK: contextvars.ContextVar[bool] = contextvars.ContextVar("force_mock", default=False)
SPEED: contextvars.ContextVar[float] = contextvars.ContextVar("speed", default=1.0)


def live() -> bool:
    return settings.live()["llm"] and not FORCE_MOCK.get()


def mock_wait(s: float) -> float:
    return s * settings.mock_delay * SPEED.get()


# ---------------- search ----------------
async def search(query: str, n: int = 8) -> list[dict]:
    """Returns [{title, url, content}]."""
    if not live():
        await asyncio.sleep(mock_wait(0.4))
        m = fixtures.markets()[fixtures.market_for(query)]
        return [{"title": c["n"], "url": f"https://{fixtures.domain_of(c)}", "content": c["sum"]} for c in m["C"]]
    key = "search:" + hashlib.sha1(query.encode()).hexdigest()
    if hit := await cache.get(key):
        return hit
    results = []
    for fn in (_keenable_search, _tavily, _claude_search):  # first provider that answers wins
        try:
            results = await fn(query, n)
            if results:
                break
        except Exception:
            continue
    await cache.set(key, results, ttl_s=6 * 3600)
    return results


KEENABLE = "https://api.keenable.ai/v1"


def _keenable_headers() -> dict:
    # With a key: credits + no hourly cap. Without: public endpoint, 1,000 requests/hour per IP.
    if settings.keenable_api_key:
        return {"X-API-Key": settings.keenable_api_key}
    return {"X-Keenable-Title": "Launch Spy"}


async def _keenable_search(query: str, n: int) -> list[dict]:
    path = "/search" if settings.keenable_api_key else "/search/public"
    r = await http().post(KEENABLE + path, headers=_keenable_headers(), json={"query": query, "mode": "realtime"})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x["url"], "content": x.get("description", "")}
            for x in r.json().get("results", [])[:n]]


async def _tavily(query: str, n: int) -> list[dict]:
    if not settings.tavily_api_key:
        return []
    r = await http().post("https://api.tavily.com/search",
                          headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
                          json={"query": query, "max_results": n, "search_depth": "basic"})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x["url"], "content": x.get("content", "")}
            for x in r.json().get("results", [])]


async def _claude_search(query: str, n: int = 8) -> list[dict]:
    """No Tavily key: let Claude's server-side web search tool run the query and keep its result list."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.model_scout, max_tokens=4000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}],
        messages=[{"role": "user", "content": f"Search the web for: {query}"}],
        output_config={"effort": "low"},
    )
    out = []
    for block in resp.content:
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            out += [{"title": x.title, "url": x.url, "content": ""} for x in block.content]
    return out


# ---------------- fetch ----------------
def fixture_pages(company: dict) -> list[tuple[str, str]]:
    """Mock mode: one fake page per fixture source, containing that source's facts."""
    hit = fixtures.company_by_domain(company["domain"])
    if not hit:
        return []
    _, c = hit
    pages: dict[str, list[str]] = {}
    for text, src in c["f"]:
        kind = "price" if re.search(r"€|price|pric", text, re.I) else "hiring" if re.search(r"hiring|role", text, re.I) \
            else "review" if re.search(r"complain|review|rated|★", text, re.I) else "feature"
        pages.setdefault(src, []).append(f"FACT|{kind}|{text}")
    for q in c["q"]:
        pages.setdefault("reviews (sampled)", []).append(f"FACT|review|Customer quote: “{q}”")
    for p in c["plans"]:
        if p[1] != "0":
            pages.setdefault(f"{company['domain']}/pricing", []).append(f"FACT|price|{p[0]}: €{p[1]}")
    return [(url, "\n".join(lines)) for url, lines in pages.items()]


async def fetch(url: str) -> tuple[str, str]:
    """Returns (text, fetcher). Tries cache → Keenable → Jina → Firecrawl → H Company. Raises if all fail."""
    key = "page:" + hashlib.sha1(url.encode()).hexdigest()
    if hit := await cache.get(key):
        return hit["text"], "cache"
    errors = []
    for name, fn in (("keenable", _keenable_fetch), ("jina", _jina), ("firecrawl", _firecrawl), ("h", _h_browser)):
        try:
            text = await fn(url)
            if text and len(text) > 200:
                await cache.set(key, {"text": text}, ttl_s=6 * 3600)
                return text, name
        except Exception as e:  # try the next fetcher
            errors.append(f"{name}: {e}")
    raise RuntimeError("; ".join(errors) or "no fetcher available")


async def _keenable_fetch(url: str) -> str:
    path = "/fetch" if settings.keenable_api_key else "/fetch/public"
    async with _fetch_sem:
        r = await http().get(KEENABLE + path, headers=_keenable_headers(),
                             params={"url": url, "live": "true", "max_chars": settings.page_char_limit}, timeout=25)
    r.raise_for_status()
    return r.json().get("content", "")


async def _jina(url: str) -> str:
    headers = {"X-Return-Format": "markdown"}
    if settings.jina_api_key:
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    async with _fetch_sem:
        r = await http().get(f"https://r.jina.ai/{url}", headers=headers, timeout=25)
    r.raise_for_status()
    return r.text


async def _firecrawl(url: str) -> str:
    if not settings.firecrawl_api_key:
        raise RuntimeError("no key")
    async with _fetch_sem:
        r = await http().post("https://api.firecrawl.dev/v1/scrape",
                              headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                              json={"url": url, "formats": ["markdown"]}, timeout=40)
    r.raise_for_status()
    return r.json().get("data", {}).get("markdown", "")


async def _h_browser(url: str) -> str:
    """H Company browser agent for pages that block simple readers.

    TODO(hackathon morning): confirm the request/response shape in the H Platform docs and set
    H_API_URL. Until then this fetcher is skipped. Max H_CONCURRENCY sessions at once (free plan: 3).
    """
    if not (settings.h_api_key and settings.h_api_url):
        raise RuntimeError("not configured")
    async with _h_sem:
        r = await http().post(settings.h_api_url, headers={"Authorization": f"Bearer {settings.h_api_key}"},
                              json={"task": f"Go to {url} and return the full visible text of the page, "
                                            "including any pricing tables. Never buy anything, never sign in, "
                                            "never change settings."}, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data.get("answer") or data.get("result") or data.get("output") or ""


def clean(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # images
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------- embeddings ----------------
async def embed(text: str) -> list[float]:
    if settings.openai_api_key:
        r = await http().post("https://api.openai.com/v1/embeddings",
                              headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                              json={"model": settings.embed_model, "input": text[:8000]})
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    return _hash_embed(text)


def _hash_embed(text: str) -> list[float]:
    """Keyless stand-in: bag of words hashed into the same dimension, normalised. Good enough for 'same market?'."""
    v = [0.0] * settings.embed_dim
    words = re.findall(r"[a-zà-ÿ0-9]+", text.lower())
    for w in words + [a + b for a, b in zip(words, words[1:])]:
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % settings.embed_dim] += 1.0 if (h >> 8) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # both are normalised


# ---------------- geocoding ----------------
async def geocode(place: str, near: str = "") -> tuple[float, float] | None:
    """OpenStreetMap Nominatim (free, 1 request/second, cached forever). Fixture coordinates in mock mode."""
    if not place:
        return None
    if not live():
        for m in fixtures.markets().values():
            for c in m["C"]:
                if c["loc"] == place and c.get("ll"):
                    return tuple(c["ll"])
        return None
    key = "geo:" + hashlib.sha1(f"{place}|{near}".encode()).hexdigest()
    if (hit := await cache.get(key)) is not None:
        return tuple(hit) if hit else None
    global _geo_last
    async with _geo_lock:  # Nominatim policy: max 1 request per second
        wait = 1.05 - (time.time() - _geo_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await http().get("https://nominatim.openstreetmap.org/search",
                                 params={"q": f"{place}, {near}" if near else place, "format": "json", "limit": 1})
            _geo_last = time.time()
            hits = r.json() if r.status_code == 200 else []
        except Exception:
            hits = []
    ll = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
    await cache.set(key, list(ll) if ll else [], ttl_s=30 * 86400)
    return ll
