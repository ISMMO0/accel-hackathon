"""Postgres (+pgvector) store for Aiven. Same interface as MemoryStore."""
import hashlib
import json
from pathlib import Path

import asyncpg

from .memory import new_id

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _vec(v: list[float] | None) -> str | None:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]" if v else None


def _row(r) -> dict | None:
    return dict(r) if r else None


class PgStore:
    kind = "postgres"

    def __init__(self, url: str):
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def init(self):
        async def setup(conn):
            await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

        # Aiven requires SSL (sslmode=require in the URL). Small pool: free plans allow few connections.
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5, init=setup, command_timeout=30)
        async with self.pool.acquire() as c:
            for f in sorted(MIGRATIONS.glob("*.sql")):
                await c.execute(f.read_text())

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _one(self, q, *a):
        async with self.pool.acquire() as c:
            return _row(await c.fetchrow(q, *a))

    async def _all(self, q, *a):
        async with self.pool.acquire() as c:
            return [dict(r) for r in await c.fetch(q, *a)]

    async def _exec(self, q, *a):
        async with self.pool.acquire() as c:
            return await c.execute(q, *a)

    # ---- researches ----
    _R = "id, query, market_label, status, mode, market, totals, is_recorded, started_at, finished_at, seen_at"

    async def create_research(self, *, query, embedding, mode="fresh", is_recorded=False, market_label=""):
        return await self._one(
            f"INSERT INTO researches (id, query, query_embedding, mode, is_recorded, market_label) "
            f"VALUES ($1,$2,$3::vector,$4,$5,$6) RETURNING {self._R}",
            new_id(), query, _vec(embedding), mode, is_recorded, market_label)

    async def get_research(self, rid):
        return await self._one(f"SELECT {self._R} FROM researches WHERE id=$1", rid)

    async def list_researches(self):
        return await self._all(f"SELECT {self._R} FROM researches ORDER BY coalesce(finished_at, started_at) DESC")

    async def update_research(self, rid, **fields):
        if not fields:
            return
        cols = list(fields)
        sets = ", ".join(f"{c}=${i + 2}" for i, c in enumerate(cols))
        await self._exec(f"UPDATE researches SET {sets} WHERE id=$1", rid, *[fields[c] for c in cols])

    async def find_similar(self, embedding, threshold):
        r = await self._one(
            f"SELECT {self._R}, 1 - (query_embedding <=> $1::vector) AS score FROM researches "
            f"WHERE status='done' AND query_embedding IS NOT NULL ORDER BY query_embedding <=> $1::vector LIMIT 1",
            _vec(embedding))
        if r and r["score"] >= threshold:
            score = r.pop("score")
            return r, score
        return None

    # ---- companies ----
    async def upsert_company(self, domain, name, location=""):
        return await self._one(
            "INSERT INTO companies (id, domain, name, location) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (domain) DO UPDATE SET domain=EXCLUDED.domain RETURNING id, domain, name, location, lat, lng",
            new_id(), domain, name, location)

    async def update_company(self, cid, **fields):
        cols = list(fields)
        sets = ", ".join(f"{c}=${i + 2}" for i, c in enumerate(cols))
        await self._exec(f"UPDATE companies SET {sets} WHERE id=$1", cid, *[fields[c] for c in cols])

    async def get_company(self, cid):
        return await self._one("SELECT id, domain, name, location, lat, lng FROM companies WHERE id=$1", cid)

    async def link_company(self, research_id, company_id, similarity, angle, position, view=None):
        await self._exec(
            "INSERT INTO research_companies (research_id, company_id, similarity, angle, position, view) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (research_id, company_id) DO UPDATE SET "
            "similarity=EXCLUDED.similarity, angle=EXCLUDED.angle, position=EXCLUDED.position, "
            "view=research_companies.view || EXCLUDED.view",
            research_id, company_id, similarity, angle, position, view or {})

    async def update_link(self, research_id, company_id, view_patch):
        await self._exec("UPDATE research_companies SET view = view || $3 WHERE research_id=$1 AND company_id=$2",
                         research_id, company_id, view_patch)

    async def research_companies(self, research_id):
        return await self._all(
            "SELECT c.id, c.domain, c.name, c.location, c.lat, c.lng, rc.research_id, rc.similarity, rc.angle, "
            "rc.position, rc.view FROM research_companies rc JOIN companies c ON c.id = rc.company_id "
            "WHERE rc.research_id=$1 ORDER BY rc.position", research_id)

    # ---- snapshots & facts ----
    async def latest_snapshot(self, company_id, url):
        return await self._one(
            "SELECT id, company_id, url, content_hash, text, fetcher, fetched_at FROM snapshots "
            "WHERE company_id=$1 AND url=$2 ORDER BY fetched_at DESC LIMIT 1", company_id, url)

    async def add_snapshot(self, company_id, url, text, fetcher):
        return await self._one(
            "INSERT INTO snapshots (id, company_id, url, content_hash, text, fetcher) VALUES ($1,$2,$3,$4,$5,$6) "
            "RETURNING id, company_id, url, content_hash, fetcher, fetched_at",
            new_id(), company_id, url, hashlib.sha256(text.encode()).hexdigest(), text, fetcher)

    async def add_facts(self, rows):
        if not rows:
            return
        async with self.pool.acquire() as c:
            await c.executemany(
                "INSERT INTO facts (id, company_id, research_id, snapshot_id, kind, text, value, source) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
                [(new_id(), r["company_id"], r["research_id"], r["snapshot_id"], r["kind"], r["text"], r["value"],
                  r["source"]) for r in rows])

    async def facts_for_company(self, company_id, limit=60):
        return await self._all(
            "SELECT * FROM (SELECT id, company_id, kind, text, value, source, created_at FROM facts "
            "WHERE company_id=$1 ORDER BY created_at DESC LIMIT $2) f ORDER BY created_at", company_id, limit)

    async def count_facts(self, company_ids):
        r = await self._one("SELECT count(*) AS n FROM facts WHERE company_id = ANY($1::text[])", list(company_ids))
        return r["n"]

    async def search_facts(self, company_ids, words, limit=12):
        pattern = "|".join(w.lower() for w in words if len(w) > 2) or "$^"
        return await self._all(
            "SELECT id, company_id, kind, text, value, source FROM facts WHERE company_id = ANY($1::text[]) "
            "AND lower(text) ~ $2 LIMIT $3", list(company_ids), pattern, limit)

    # ---- events ----
    async def add_event(self, research_id, agent, type, payload):
        return await self._one(
            "INSERT INTO events (research_id, agent, type, payload) VALUES ($1,$2,$3,$4) "
            "RETURNING id, research_id, agent, type, payload, created_at", research_id, agent, type, payload)

    async def events_after(self, research_id, after_id):
        return await self._all("SELECT id, research_id, agent, type, payload, created_at FROM events "
                               "WHERE research_id=$1 AND id>$2 ORDER BY id", research_id, after_id)

    async def last_event_id(self, research_id):
        r = await self._one("SELECT coalesce(max(id), 0) AS n FROM events WHERE research_id=$1", research_id)
        return r["n"]

    # ---- chat, alerts, llm log ----
    async def add_chat(self, research_id, role, body):
        await self._exec("INSERT INTO chat_messages (research_id, role, body) VALUES ($1,$2,$3)",
                         research_id, role, body)

    async def chat(self, research_id):
        return await self._all("SELECT role, body FROM chat_messages WHERE research_id=$1 ORDER BY id", research_id)

    async def add_alert(self, company_id, text):
        await self._exec("INSERT INTO alerts (company_id, text) VALUES ($1,$2)", company_id, text)

    async def count_alerts(self, company_ids, since):
        r = await self._one("SELECT count(*) AS n FROM alerts WHERE company_id = ANY($1::text[]) AND created_at > $2",
                            list(company_ids), since)
        return r["n"]

    async def log_llm_call(self, **row):
        await self._exec(
            "INSERT INTO llm_calls (research_id, agent, model, in_tokens, out_tokens, ms, cost_usd, ok) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", row["research_id"], row["agent"], row["model"], row["in_tokens"],
            row["out_tokens"], row["ms"], row["cost_usd"], row["ok"])

    async def llm_stats(self, research_id):
        r = await self._one("SELECT count(*) AS calls, coalesce(sum(cost_usd),0) AS cost, "
                            "coalesce(sum(in_tokens+out_tokens),0) AS tokens FROM llm_calls WHERE research_id=$1",
                            research_id)
        return dict(calls=r["calls"], cost_usd=round(r["cost"], 4), tokens=r["tokens"])
