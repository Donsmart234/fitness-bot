"""Durable storage. Production: rows in Supabase table public.fra_records (shared with the picks bot, separated by
kind = fit_state / fit_log and ids prefixed 'fit:'), via PostgREST + service key. Local/tests: a JSON file."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger("store")
TABLE = "fra_records"
STATE_ID = "fit:state"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, url: str | None = None, key: str | None = None, path: str | None = None):
        self.url = (url if url is not None else os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
        self.key = (key if key is not None else os.environ.get("SUPABASE_SERVICE_KEY", "")).strip()
        self.remote = bool(self.url and self.key)
        self.path = Path(path or os.environ.get("FIT_DATA_PATH", "data/fit.json"))
        self._client: httpx.AsyncClient | None = None
        self._local: dict | None = None
        self.last_error = ""

    @property
    def backend(self) -> str:
        return "supabase" if self.remote else f"file:{self.path}"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            h = {"apikey": self.key, "Content-Type": "application/json"}
            if not self.key.startswith("sb_"):
                h["Authorization"] = f"Bearer {self.key}"
            self._client = httpx.AsyncClient(base_url=f"{self.url}/rest/v1", headers=h, timeout=httpx.Timeout(15.0))
        return self._client

    async def aclose(self):
        if self._client:
            await self._client.aclose()

    # ---- local file -----------------------------------------------------
    def _load_local(self) -> dict:
        if self._local is None:
            try:
                self._local = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._local = {"records": {}}
        return self._local

    def _flush_local(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._local))
        tmp.replace(self.path)

    # ---- core -----------------------------------------------------------
    async def save(self, rid: str, kind: str, data: dict, status: str = "ok", created_at: str | None = None) -> bool:
        rec = {"id": rid, "kind": kind, "status": status, "data": data, "updated_at": now_iso()}
        if created_at:
            rec["created_at"] = created_at
        if self.remote:
            for attempt in range(3):
                try:
                    r = await self._http().post(f"/{TABLE}", json=rec, params={"on_conflict": "id"},
                                                headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
                    if r.status_code < 300:
                        return True
                    self.last_error = f"HTTP {r.status_code} {r.text[:160]}"
                except httpx.HTTPError as e:
                    self.last_error = e.__class__.__name__
                await asyncio.sleep(1.5 * (attempt + 1))
            log.warning("save failed: %s", self.last_error)
            return False
        db = self._load_local()
        old = db["records"].get(rid, {})
        rec.setdefault("created_at", old.get("created_at") or now_iso())
        db["records"][rid] = rec
        self._flush_local()
        return True

    async def get(self, rid: str) -> dict | None:
        if self.remote:
            for attempt in range(3):
                try:
                    r = await self._http().get(f"/{TABLE}", params={"id": f"eq.{rid}", "select": "*"})
                    r.raise_for_status()
                    rows = r.json()
                    return rows[0] if rows else None
                except (httpx.HTTPError, ValueError) as e:
                    self.last_error = e.__class__.__name__
                    await asyncio.sleep(2 * (attempt + 1))
            raise RuntimeError(f"store unreachable: {self.last_error}")
        return self._load_local()["records"].get(rid)

    async def list(self, kind: str, since: str | None = None, limit: int = 2000) -> list[dict]:
        if self.remote:
            params = {"kind": f"eq.{kind}", "order": "created_at.asc", "limit": str(limit), "select": "*"}
            if since:
                params["created_at"] = f"gte.{since}"
            r = await self._http().get(f"/{TABLE}", params=params)
            r.raise_for_status()
            return r.json()
        rows = [r for r in self._load_local()["records"].values() if r["kind"] == kind and
                (not since or r["created_at"] >= since)]
        return sorted(rows, key=lambda r: r["created_at"])[:limit]

    async def delete(self, rid: str) -> None:
        if self.remote:
            await self._http().delete(f"/{TABLE}", params={"id": f"eq.{rid}"})
            return
        self._load_local()["records"].pop(rid, None)
        self._flush_local()

    # ---- fitness helpers --------------------------------------------------
    async def load_state(self) -> dict:
        rec = await self.get(STATE_ID)
        return (rec or {}).get("data") or {}

    async def save_state(self, state: dict) -> bool:
        return await self.save(STATE_ID, "fit_state", state)

    async def add_log(self, entry: dict) -> str:
        rid = "fit:log:" + uuid.uuid4().hex[:16]
        await self.save(rid, "fit_log", entry, created_at=entry.get("ts"))
        return rid

    async def logs_since(self, since_iso: str) -> list[dict]:
        rows = await self.list("fit_log", since=since_iso)
        out = []
        for r in rows:
            d = dict(r.get("data") or {})
            d["_id"] = r["id"]
            out.append(d)
        return out
