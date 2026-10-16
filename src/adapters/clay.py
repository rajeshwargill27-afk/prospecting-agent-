"""
Clay adapter — deliberately narrow.

Clay does exactly one job here: the email waterfall. Chain paid providers
until one returns a verified address, stop at the first hit. Single-source
lookup finds roughly 60-75% of emails; a waterfall reaches 85-92%. That gap
is the entire reason Clay is in this stack.

What Clay does NOT do here, on purpose:
  - account qualification  -> an LLM reading the site is cents, not credits
  - tier scoring           -> same
  - company enrichment     -> same

Every one of those is a judgment call, and judgment is cheap. Only the
waterfall is irreplaceable, so only the waterfall gets paid for.

Cost control is structural, not advisory: `enrich_contacts` refuses to call
Clay for any key already in the cache.
"""

import json
from pathlib import Path

CLAY_MCP = "https://api.clay.com/v3/mcp"

# Illustrative. Real cost depends on how deep the chain runs before a hit.
CREDITS_PER_LOOKUP = 4.0


class ClayAdapter:
    def __init__(self, backend: str = "mock", cache=None, fixtures: str = "data/synthetic"):
        self.backend = backend
        self.cache = cache
        self.fixtures = Path(fixtures)
        self._mock_db = None

    def _load_mock(self) -> dict:
        if self._mock_db is None:
            raw = json.loads((self.fixtures / "clay_enrichment.json").read_text())
            self._mock_db = {tuple(k.split("|")): v for k, v in raw.items()}
        return self._mock_db

    def _live_waterfall(self, rows: list[dict]) -> list[dict]:
        raise NotImplementedError(
            f"Live backend not wired. Would call {CLAY_MCP} "
            f"find-and-enrich-list-of-contacts for {len(rows)} rows."
        )

    def enrich_contacts(self, rows: list[dict], require_verified: bool = True) -> dict:
        """
        rows: [{"name": ..., "domain": ..., "company": ...}]

        Returns {"enriched": [...], "not_found": [...], "from_cache": int,
                 "billed": int, "credits": float}
        """
        keys = [(r["name"].lower(), r["domain"].lower()) for r in rows]
        by_key = dict(zip(keys, rows))

        cached, missing = ({}, keys) if self.cache is None else self.cache.partition(keys)

        enriched, not_found = [], []

        # Replay cached results — zero cost.
        for k, hit in cached.items():
            (enriched if hit["found"] else not_found).append(
                hit["payload"] or {"name": by_key[k]["name"], "domain": by_key[k]["domain"]}
            )

        # Only genuinely unknown rows reach the paid provider.
        fresh: list[dict] = []
        if missing:
            if self.backend == "live":
                fresh = self._live_waterfall([by_key[k] for k in missing])
            else:
                db = self._load_mock()
                for k in missing:
                    fresh.append(db.get(k) or {
                        "name": by_key[k]["name"],
                        "domain": by_key[k]["domain"],
                        "email": None,
                        "verified": False,
                        "provider": None,
                    })

            for k, row in zip(missing, fresh):
                found = bool(row.get("email")) and (row.get("verified") or not require_verified)
                if self.cache:
                    self.cache.put(k, row, found, CREDITS_PER_LOOKUP)
                (enriched if found else not_found).append(row)

        credits = len(missing) * CREDITS_PER_LOOKUP
        if self.cache and missing:
            self.cache.log_spend("clay_waterfall", len(missing), credits)

        return {
            "enriched": enriched,
            "not_found": not_found,
            "from_cache": len(cached),
            "billed": len(missing),
            "credits": credits,
        }
