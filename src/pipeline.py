"""
Pipeline orchestrator.

Runs phases 1 -> 5, then STOPS. Phase 6 is a separate invocation that requires
an explicit approval flag and a matching manifest hash.

The gate is structural rather than a warning in a docstring: phase 6 lives in
a different function, and calling it needs a token produced by a human reading
the manifest. There is no code path from `run()` to a live send.
"""

import hashlib
import json
from pathlib import Path

from src.adapters.apollo import ApolloAdapter
from src.adapters.clay import ClayAdapter
from src.cache import EnrichmentCache
from src.join import build_lookup, company_key, norm_domain, norm_name, person_key, rejoin
from src.phases.dedupe import dedupe, load_crm_contacts
from src.phases.qualify import disqualify, score

OUT = Path("out")


def _write(name: str, obj) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2))


def run(config_path: str, backend: str = "mock", crm_csv: str = "data/synthetic/hubspot_contacts.csv") -> dict:
    config = json.loads(Path(config_path).read_text())
    cache = EnrichmentCache()
    apollo = ApolloAdapter(backend=backend)
    clay = ClayAdapter(backend=backend, cache=cache)

    print(f"\n{config['client']} — prospecting run [backend={backend}]")
    print("=" * 58)

    # ---- Phase 1 — company search (Apollo, free) ----
    companies = apollo.search_companies(config["apollo_company_search"])
    print(f"1  Apollo company search              {len(companies):>4} companies")

    # ---- Phase 1.5 — rule disqualification (free) ----
    survivors, killed = disqualify(companies, config["disqualify"])
    print(f"1.5 Rule disqualification             {len(survivors):>4} kept  ({len(killed)} killed, $0)")

    # ---- Phase 2 — tier + confidence (cents) ----
    to_enrich, review, rejected = score(survivors, config)
    print(f"2  Tier scoring                       {len(to_enrich):>4} enrichable")
    print(f"   -> review queue (low confidence)   {len(review):>4}")
    print(f"   -> rejected (confident wrong tier) {len(rejected):>4}")
    _write("review_queue.json", review)

    # ---- Phase 3 — contact search scoped to survivors (Apollo, free) ----
    org_ids = [c["id"] for c in to_enrich]
    people = apollo.search_people(org_ids, config["apollo_people_search"]["person_titles"])
    print(f"3  Apollo contact search              {len(people):>4} people (no emails yet)")

    # Save Apollo IDs BEFORE anything leaves for Clay.
    lookup = build_lookup(
        people,
        lambda p: person_key(p["name"], (p.get("organization") or {}).get("primary_domain")),
        "id",
    )

    # ---- Phase 4 — CRM dedupe (free) ----
    crm = load_crm_contacts(crm_csv)
    fresh, known = dedupe(people, crm)
    print(f"4  HubSpot dedupe                     {len(fresh):>4} new  ({len(known)} already owned, $0)")

    # ---- Phase 5 — Clay waterfall (THE paid step) ----
    rows = [{"name": p["name"],
             "domain": norm_domain((p.get("organization") or {}).get("primary_domain")),
             "company": (p.get("organization") or {}).get("name")}
            for p in fresh]
    cap = config["enrichment"]["max_contacts_per_run"]
    result = clay.enrich_contacts(rows[:cap], config["enrichment"]["require_verified_email"])

    matched, unmatched = rejoin(
        result["enriched"], lookup,
        lambda r: person_key(r.get("name"), r.get("domain")),
    )
    hit_rate = len(result["enriched"]) / max(1, len(rows)) * 100
    print(f"5  Clay waterfall                     {len(matched):>4} verified  ({hit_rate:.0f}% hit rate)")
    print(f"   -> served from cache               {result['from_cache']:>4}  (0 credits)")
    print(f"   -> billed to Clay                  {result['billed']:>4}  ({result['credits']:.0f} credits)")
    if unmatched:
        print(f"   ! UNMATCHED on rejoin              {len(unmatched):>4}  <- investigate before sending")
    _write("unmatched.json", unmatched)

    # ---- Gate ----
    manifest = {
        "client": config["client"],
        "contacts": [
            {"apollo_id": m["apollo_id"], "name": m["name"],
             "email": m["email"], "domain": m["domain"], "provider": m.get("provider")}
            for m in matched
        ],
        "label": config["apollo_load"]["label_name"],
        "sequence_id": config["apollo_load"]["sequence_id"],
        "credits_this_run": result["credits"],
        "credits_lifetime": cache.total_credits(),
    }
    manifest["approval_token"] = hashlib.sha256(
        json.dumps(manifest["contacts"], sort_keys=True).encode()
    ).hexdigest()[:16]
    _write("send_manifest.json", manifest)

    print("-" * 58)
    print(f"   credits this run {result['credits']:.0f}   lifetime {cache.total_credits():.0f}")
    print(f"\n  STOP — human approval required before phase 6")
    print(f"  Read out/send_manifest.json ({len(matched)} contacts), then:")
    print(f"    python run.py --approve {manifest['approval_token']}\n")
    return manifest


def send(config_path: str, token: str, backend: str = "mock") -> dict:
    """
    Phase 6. Only reachable with a token matching the current manifest.

    The hash is over the contact list itself, so if anything upstream changed
    since the human read it, the token no longer matches and the send refuses.
    You cannot approve one list and ship a different one.
    """
    config = json.loads(Path(config_path).read_text())
    manifest = json.loads((OUT / "send_manifest.json").read_text())

    if token != manifest["approval_token"]:
        raise SystemExit(
            f"Approval token mismatch.\n"
            f"  manifest: {manifest['approval_token']}\n  supplied: {token}\n"
            f"The contact list changed since it was reviewed. Re-read the manifest."
        )
    if manifest["sequence_id"] == "SET_ME_BEFORE_LIVE_RUN" and backend == "live":
        raise SystemExit("Refusing to send: sequence_id is still the placeholder.")

    apollo = ApolloAdapter(backend=backend)
    contacts = manifest["contacts"]

    created = apollo.bulk_create_contacts(contacts)
    ids = [c["apollo_id"] for c in contacts]
    labeled = apollo.add_to_label(ids, manifest["label"])
    enrolled = apollo.enroll_in_sequence(ids, manifest["sequence_id"])

    print(f"\n6  Apollo load + enroll")
    print(f"   created  {created}")
    print(f"   labeled  {labeled}")
    print(f"   enrolled {enrolled}\n")
    return {"created": created, "labeled": labeled, "enrolled": enrolled}
