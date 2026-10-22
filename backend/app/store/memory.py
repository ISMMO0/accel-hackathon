"""In-process store used when DATABASE_URL is empty. Same interface as PgStore; data lives until restart."""
import copy
import hashlib
import itertools
import uuid
from datetime import datetime, timezone

from ..providers.web import cosine


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def company_id(domain: str) -> str:
    """Stable id from the domain: known without a database round trip, same in every research."""
    return "c" + hashlib.sha1(domain.encode()).hexdigest()[:11]


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self.researches: dict[str, dict] = {}
        self.companies: dict[str, dict] = {}
        self.links: dict[tuple[str, str], dict] = {}
        self.snapshots: list[dict] = []
        self.facts: list[dict] = []
        self.events: list[dict] = []
        self.chats: list[dict] = []
        self.alerts: list[dict] = []
        self.llm: list[dict] = []
        self._eid = itertools.count(1)

    async def init(self):
        pass

    async def close(self):
        pass

    # ---- researches ----
    async def create_research(self, *, query, embedding, mode="fresh", is_recorded=False, market_label=""):
        r = dict(id=new_id(), query=query, embedding=embedding, market_label=market_label, status="running",
                 mode=mode, market={}, totals={}, is_recorded=is_recorded, started_at=now(), finished_at=None,
                 seen_at=now())
        self.researches[r["id"]] = r
        return copy.deepcopy(r)

    async def get_research(self, rid):
        r = self.researches.get(rid)
        return copy.deepcopy(r) if r else None

    async def list_researches(self):
        rs = sorted(self.researches.values(), key=lambda r: r["finished_at"] or r["started_at"], reverse=True)
        return [copy.deepcopy(r) for r in rs]

    async def update_research(self, rid, **fields):
        self.researches[rid].update(fields)

    async def mark_interrupted(self):
        for r in self.researches.values():
            if r["status"] == "running":
                r["status"] = "interrupted"

    async def find_similar(self, embedding, threshold):
        best = None
        for r in self.researches.values():
            if r["status"] != "done" or not r.get("embedding"):
                continue
            s = cosine(embedding, r["embedding"])
            if s >= threshold and (best is None or s > best[1]):
                best = (copy.deepcopy(r), s)
        return best

    # ---- companies ----
    async def upsert_company(self, domain, name, location="", cid=None):
        for c in self.companies.values():
            if c["domain"] == domain:
                return dict(c)
        c = dict(id=cid or company_id(domain), domain=domain, name=name, location=location, lat=None, lng=None)
        self.companies[c["id"]] = c
        return dict(c)

    async def update_company(self, cid, **fields):
        self.companies[cid].update(fields)

    async def get_company(self, cid):
        c = self.companies.get(cid)
        return dict(c) if c else None

    async def link_company(self, research_id, company_id, similarity, angle, position, view=None):
        key = (research_id, company_id)
        old = self.links.get(key, {})
        self.links[key] = dict(research_id=research_id, company_id=company_id, similarity=similarity, angle=angle,
                               position=position, view={**old.get("view", {}), **(view or {})})

    async def update_link(self, research_id, company_id, view_patch):
        self.links[(research_id, company_id)]["view"].update(view_patch)

    async def research_companies(self, research_id):
        out = []
        for (rid, cid), link in self.links.items():
            if rid == research_id:
                out.append({**self.companies[cid], **{k: copy.deepcopy(v) for k, v in link.items()}, "id": cid})
        return sorted(out, key=lambda x: x["position"])

    # ---- snapshots & facts ----
    async def latest_snapshot(self, company_id, url):
        snaps = [s for s in self.snapshots if s["company_id"] == company_id and s["url"] == url]
        return dict(snaps[-1]) if snaps else None

    async def add_snapshot(self, company_id, url, text, fetcher):
        s = dict(id=new_id(), company_id=company_id, url=url, text=text, fetcher=fetcher, fetched_at=now(),
                 content_hash=hashlib.sha256(text.encode()).hexdigest())
        self.snapshots.append(s)
        return dict(s)

    async def add_facts(self, rows):
        seen = {(f["company_id"], f["text"]) for f in self.facts}
        for r in rows:
            if (r["company_id"], r["text"]) not in seen:
                seen.add((r["company_id"], r["text"]))
                self.facts.append({**r, "id": new_id(), "created_at": now()})

    async def facts_for_company(self, company_id, limit=60):
        return [dict(f) for f in self.facts if f["company_id"] == company_id][-limit:]

    async def count_facts(self, company_ids):
        ids = set(company_ids)
        return sum(1 for f in self.facts if f["company_id"] in ids)

    async def search_facts(self, company_ids, words, limit=12):
        ids, words = set(company_ids), [w.lower() for w in words if len(w) > 2]
        scored = []
        for f in self.facts:
            if f["company_id"] in ids:
                s = sum(w in f["text"].lower() for w in words)
                if s:
                    scored.append((s, f))
        return [dict(f) for _, f in sorted(scored, key=lambda x: -x[0])[:limit]]

    # ---- events ----
    async def add_event(self, research_id, agent, type, payload):
        e = dict(id=next(self._eid), research_id=research_id, agent=agent, type=type, payload=payload,
                 created_at=now())
        self.events.append(e)
        return dict(e)

    async def add_events(self, events):
        known = {e["id"] for e in self.events}
        self.events += [dict(e) for e in events if e["id"] not in known]

    async def save_page(self, company_id, research_id, url, text, fetcher, facts):
        snap = await self.add_snapshot(company_id, url, text, fetcher)
        await self.add_facts([dict(company_id=company_id, research_id=research_id, snapshot_id=snap["id"],
                                   kind=f["kind"], text=f["text"], value=f["value"], item=f.get("item", ""),
                                   source=url) for f in facts])

    async def page_memory(self, company_ids):
        ids, out = set(company_ids), {}
        for s in self.snapshots:  # later snapshots overwrite earlier ones
            if s["company_id"] in ids:
                facts = [{"kind": f["kind"], "text": f["text"], "value": f["value"], "item": f.get("item", "")}
                         for f in self.facts
                         if f["company_id"] == s["company_id"] and f["source"] == s["url"]]
                out[(s["company_id"], s["url"])] = {"hash": s["content_hash"], "facts": facts}
        return out

    async def report_facts(self, company_ids, per_company=8):
        out = {}
        for cid in company_ids:
            fs = [dict(f) for f in self.facts if f["company_id"] == cid]
            out[cid] = {"count": len(fs), "latest": fs[-per_company:]}
        return out

    async def eval_rows(self):
        return []

    async def all_facts(self, company_ids):
        ids = set(company_ids)
        return [{**f, "item": f.get("item", "")} for f in self.facts if f["company_id"] in ids]

    async def log_knowledge(self, research_id, event):
        ids = [c["id"] for c in await self.research_companies(research_id)]
        fs = await self.all_facts(ids)
        self.knowledge = getattr(self, "knowledge", []) + [{
            "research_id": research_id, "query": self.researches[research_id]["query"], "event": event,
            "facts": len(fs), "sources": len({f["source"] for f in fs}),
            "priced_items": sum(1 for f in fs if f["kind"] == "price" and f["item"]), "created_at": now()}]

    async def knowledge_rows(self):
        return list(getattr(self, "knowledge", []))

    async def library_rows(self):
        out = []
        for r in await self.list_researches():
            comps = await self.research_companies(r["id"])
            ids = [c["id"] for c in comps]
            out.append({**r, "thumb": [{"n": c["name"], "sim": c["similarity"], "ang": c["angle"], "d": c["domain"]}
                                       for c in comps],
                        "comps": len(comps), "facts": await self.count_facts(ids),
                        "alerts": await self.count_alerts(ids, r["seen_at"])})
        return out

    async def events_after(self, research_id, after_id):
        return [dict(e) for e in self.events if e["research_id"] == research_id and e["id"] > after_id]

    async def last_event_id(self, research_id):
        ids = [e["id"] for e in self.events if e["research_id"] == research_id]
        return max(ids) if ids else 0

    # ---- chat, alerts, llm log ----
    async def add_chat(self, research_id, role, body):
        self.chats.append(dict(research_id=research_id, role=role, body=body, created_at=now()))

    async def chat(self, research_id):
        return [dict(role=c["role"], body=c["body"]) for c in self.chats if c["research_id"] == research_id]

    async def add_alert(self, company_id, text):
        self.alerts.append(dict(company_id=company_id, text=text, created_at=now()))

    async def count_alerts(self, company_ids, since):
        ids = set(company_ids)
        return sum(1 for a in self.alerts if a["company_id"] in ids and a["created_at"] > since)

    async def log_llm_call(self, **row):
        self.llm.append({**row, "created_at": now()})

    async def llm_stats(self, research_id):
        rows = [r for r in self.llm if r["research_id"] == research_id]
        return dict(calls=len(rows), cost_usd=round(sum(r["cost_usd"] for r in rows), 4),
                    tokens=sum(r["in_tokens"] + r["out_tokens"] for r in rows))
