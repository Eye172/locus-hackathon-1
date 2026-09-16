"""SQLite cache for generated profiles, user flags and recent searches."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

from .config import settings
from .models import Profile

DB_PATH = settings.data_dir / "campuslens.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  qid TEXT PRIMARY KEY,
  name TEXT,
  city TEXT,
  json TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  elapsed_ms INTEGER,
  photos INTEGER
);
CREATE TABLE IF NOT EXISTS flags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  qid TEXT NOT NULL,
  photo_id TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS flags_qid ON flags(qid);
CREATE TABLE IF NOT EXISTS external_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  qid TEXT NOT NULL,
  collector TEXT,
  json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ext_qid ON external_candidates(qid);
CREATE TABLE IF NOT EXISTS kv_cache (
  bucket TEXT NOT NULL,
  key TEXT NOT NULL,
  json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (bucket, key)
);
"""


async def kv_get(bucket: str, key: str, max_age_s: float | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT json, created_at FROM kv_cache WHERE bucket=? AND key=?", (bucket, key)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    if max_age_s is not None:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds()
        if age > max_age_s:
            return None
    return json.loads(row[0])


async def kv_set(bucket: str, key: str, value) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO kv_cache(bucket,key,json,created_at) VALUES(?,?,?,?)",
                         (bucket, key, json.dumps(value, ensure_ascii=False, default=str), datetime.now(timezone.utc).isoformat()))
        await db.commit()


async def init() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def get_profile(qid: str) -> Profile | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT json FROM profiles WHERE qid=?", (qid,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    p = Profile.model_validate_json(row[0])
    p.cached = True
    return p


async def save_profile(p: Profile) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO profiles(qid,name,city,json,generated_at,elapsed_ms,photos) VALUES(?,?,?,?,?,?,?)",
            (p.university.qid, p.university.name, p.university.city, p.model_dump_json(),
             p.generated_at, p.elapsed_ms, len(p.photos)),
        )
        await db.commit()


async def list_recent(limit: int = 12) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT qid,name,city,generated_at,elapsed_ms,photos FROM profiles ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(qid=r[0], name=r[1], city=r[2], generated_at=r[3], elapsed_ms=r[4], photos=r[5]) for r in rows]


async def add_flag(qid: str, photo_id: str, reason: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO flags(qid,photo_id,reason,created_at) VALUES(?,?,?,?)",
            (qid, photo_id, reason, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def get_flags(qid: str) -> dict[str, int]:
    """photo_id -> number of flags."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT photo_id, COUNT(*) FROM flags WHERE qid=? GROUP BY photo_id", (qid,)) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


async def add_external(qid: str, collector: str, candidates: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO external_candidates(qid,collector,json,created_at) VALUES(?,?,?,?)",
            [(qid, collector, json.dumps(c, ensure_ascii=False), now) for c in candidates],
        )
        await db.commit()
    return len(candidates)


async def get_external(qid: str, limit: int = 80) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT json, collector FROM external_candidates WHERE qid=? ORDER BY id DESC LIMIT ?",
                              (qid, limit)) as cur:
            rows = await cur.fetchall()
    out = []
    for js, collector in rows:
        d = json.loads(js)
        d.setdefault("collector", collector)
        out.append(d)
    return out


async def clear_external(qid: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM external_candidates WHERE qid=?", (qid,))
        await db.commit()


async def delete_profile(qid: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM profiles WHERE qid=?", (qid,))
        await db.commit()
