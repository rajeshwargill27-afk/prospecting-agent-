"""
Enrichment cache.

Clay is the only step in this pipeline that costs real money per record
(3-8 credits per lead, depending on how deep the waterfall runs). A portfolio
project gets re-run dozens of times during debugging. Without a cache you pay
for the same email every single run.

Rule enforced here: nothing reaches Clay if it is already known locally.

The cache stores misses as well as hits. A person Clay could not find is
still information you paid for — re-asking next week rarely helps, and
re-asking on every debug run is pure waste. Misses expire after
`miss_ttl_days` so the data does eventually get another chance.
"""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contact_cache (
    key         TEXT PRIMARY KEY,
    payload     TEXT,
    found       INTEGER NOT NULL,
    fetched_at  REAL NOT NULL,
    credits     REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spend_log (
    ts          REAL NOT NULL,
    step        TEXT NOT NULL,
    records     INTEGER NOT NULL,
    credits     REAL NOT NULL
);
"""


class EnrichmentCache:
    def __init__(self, path: str = "out/cache.sqlite", miss_ttl_days: int = 30):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.miss_ttl = miss_ttl_days * 86400

    @staticmethod
    def _k(key: tuple[str, str] | str) -> str:
        return "|".join(key) if isinstance(key, tuple) else str(key)

    def get(self, key) -> dict | None:
        row = self.conn.execute(
            "SELECT payload, found, fetched_at FROM contact_cache WHERE key = ?",
            (self._k(key),),
        ).fetchone()
        if row is None:
            return None
        payload, found, fetched_at = row
        if not found and (time.time() - fetched_at) > self.miss_ttl:
            return None  # stale miss — allow one more attempt
        return {"payload": json.loads(payload) if payload else None, "found": bool(found)}

    def put(self, key, payload: dict | None, found: bool, credits: float = 0.0) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO contact_cache VALUES (?,?,?,?,?)",
            (self._k(key), json.dumps(payload) if payload else None,
             int(found), time.time(), credits),
        )
        self.conn.commit()

    def partition(self, keys: list) -> tuple[dict, list]:
        """Split requested keys into {cached} and [needs_fetch]."""
        cached, missing = {}, []
        for k in keys:
            hit = self.get(k)
            if hit is None:
                missing.append(k)
            else:
                cached[k] = hit
        return cached, missing

    def log_spend(self, step: str, records: int, credits: float) -> None:
        self.conn.execute(
            "INSERT INTO spend_log VALUES (?,?,?,?)", (time.time(), step, records, credits)
        )
        self.conn.commit()

    def total_credits(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(credits),0) FROM spend_log").fetchone()
        return round(row[0], 2)
