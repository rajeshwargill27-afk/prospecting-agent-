"""
Phase 4 — dedupe against the CRM.

Costs nothing and saves the most. Two separate harms it prevents:

  1. Paying Clay to re-discover an email already sitting in HubSpot.
  2. Cold-sequencing somebody who is mid-conversation with sales, which
     damages a live deal in a way no credit refund covers.

Runs on a CSV export so it works with no HubSpot connection at all. Matching
is layered: email is exact and trustworthy, LinkedIn URL is next, and
(name, domain) is the fallback for records Apollo returned without either.
"""

import csv
from pathlib import Path

from src.join import norm_domain, norm_name


def load_crm_contacts(path: str) -> dict:
    """Build the three lookup sets from a HubSpot contacts export."""
    emails, linkedins, name_domain = set(), set(), set()
    p = Path(path)
    if not p.exists():
        print(f"  ! no CRM export at {path} — dedupe skipped, nothing filtered")
        return {"emails": emails, "linkedins": linkedins, "name_domain": name_domain}

    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            if email:
                emails.add(email)
            li = (row.get("linkedin_url") or "").strip().lower().rstrip("/")
            if li:
                linkedins.add(li)
            nm = norm_name(f"{row.get('firstname','')} {row.get('lastname','')}")
            dom = norm_domain(row.get("company_domain"))
            if nm and dom:
                name_domain.add((nm, dom))

    return {"emails": emails, "linkedins": linkedins, "name_domain": name_domain}


def dedupe(people: list[dict], crm: dict) -> tuple[list[dict], list[dict]]:
    """Returns (new_people, already_known)."""
    new, known = [], []
    for p in people:
        domain = norm_domain((p.get("organization") or {}).get("primary_domain"))
        email = (p.get("email") or "").strip().lower()
        li = (p.get("linkedin_url") or "").strip().lower().rstrip("/")
        key = (norm_name(p.get("name")), domain)

        if email and email in crm["emails"]:
            known.append({**p, "match_on": "email"})
        elif li and li in crm["linkedins"]:
            known.append({**p, "match_on": "linkedin_url"})
        elif key in crm["name_domain"]:
            known.append({**p, "match_on": "name+domain"})
        else:
            new.append(p)

    return new, known
