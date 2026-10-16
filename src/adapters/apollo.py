"""
Apollo adapter.

Apollo's job in this pipeline: turn a universe into a shortlist (company
search, contact search), and put mail on the wire (create, label, enroll).
It never enriches — that is Clay's one job.

Both backends return identical shapes, so switching to live is a constructor
argument, not a rewrite. The live paths are written out but raise until an
API key and a plan that permits these endpoints are in place. `mixed_people/
api_search` is plan-gated and returns API_INACCESSIBLE on Free.
"""

import json
from pathlib import Path

APOLLO_BASE = "https://api.apollo.io/api/v1"


class ApolloAdapter:
    def __init__(self, backend: str = "mock", api_key: str | None = None,
                 fixtures: str = "data/synthetic"):
        self.backend = backend
        self.api_key = api_key
        self.fixtures = Path(fixtures)

    def _fixture(self, name: str) -> dict:
        return json.loads((self.fixtures / name).read_text())

    def _live(self, path: str, payload: dict) -> dict:
        raise NotImplementedError(
            f"Live backend not wired. Would POST {APOLLO_BASE}/{path} "
            f"with {len(payload)} filter keys. Requires a plan that permits this endpoint."
        )

    # ---------- Phase 1 ----------
    def search_companies(self, filters: dict) -> list[dict]:
        """POST /mixed_companies/search — firmographic filters only. Free."""
        if self.backend == "live":
            return self._live("mixed_companies/search", filters)["organizations"]
        return self._fixture("apollo_companies.json")["organizations"]

    # ---------- Phase 3 ----------
    def search_people(self, organization_ids: list[str], titles: list[str],
                      per_page: int = 100) -> list[dict]:
        """
        POST /mixed_people/api_search, scoped by organization_ids.

        Returns names/titles but NOT emails — Apollo withholds contact detail
        at search time. That is fine; enrichment happens later, on a
        deliberately smaller list.
        """
        payload = {
            "organization_ids": organization_ids,
            "person_titles": titles,
            "per_page": per_page,
        }
        if self.backend == "live":
            return self._live("mixed_people/api_search", payload)["people"]

        people = self._fixture("apollo_people.json")["people"]
        wanted = {t.lower() for t in titles}
        org_set = set(organization_ids)
        return [
            p for p in people
            if p["organization_id"] in org_set and p["title"].lower() in wanted
        ]

    # ---------- Phase 6 ----------
    def bulk_create_contacts(self, contacts: list[dict]) -> dict:
        if self.backend == "live":
            return self._live("contacts/bulk_create", {"contacts": contacts})
        return {"created": len(contacts), "status": "mock"}

    def add_to_label(self, entity_ids: list[str], label_name: str) -> dict:
        if self.backend == "live":
            return self._live("labels/add_entity_ids_to_label_names",
                              {"entity_ids": entity_ids, "label_names": [label_name]})
        return {"labeled": len(entity_ids), "label": label_name, "status": "mock"}

    def enroll_in_sequence(self, contact_ids: list[str], sequence_id: str) -> dict:
        """The irreversible call. Never reached without passing the approval gate."""
        if self.backend == "live":
            return self._live("emailer_campaigns/add_contact_ids",
                              {"contact_ids": contact_ids, "emailer_campaign_id": sequence_id})
        return {"enrolled": len(contact_ids), "sequence": sequence_id, "status": "mock"}
