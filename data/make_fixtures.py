"""
Synthetic fixtures in Apollo's real response shape.

Shapes mirror the live API so swapping backend='mock' -> 'live' needs no
parsing changes. Deliberately seeded with awkward cases:
  - retailers that must die at phase 1.5
  - thin descriptions that must land in the review queue, not be guessed
  - a casing/whitespace mismatch to prove the join normalisation earns its keep
  - contacts Clay cannot find, and contacts already in the CRM
"""

import csv
import json
import random
from pathlib import Path

random.seed(7)
OUT = Path(__file__).parent / "synthetic"
OUT.mkdir(exist_ok=True)

MAKERS = [
    ("Northwind Upholstery", "sofa"), ("Cedarline Seating", "sofa"),
    ("Bayview Sofa Works", "sofa"), ("Ironwood Furniture Co", "mixed"),
    ("Prairie Case Goods", "mixed"), ("Lakeshore Upholstered", "sofa"),
    ("Maple Ridge Manufacturing", "mixed"), ("St. Clair Seating", "sofa"),
    ("Fundy Furniture Works", "mixed"), ("Granite Bay Upholstery", "sofa"),
    ("Aurora Contract Seating", "sofa"), ("Riverbend Woodcraft", "mixed"),
]
RETAILERS = [
    "Halton Home Store", "Kingsway Interiors", "Meridian Design Centre",
    "Bloor Furniture Gallery", "Westside Outlet Furnishings",
]
THIN = ["Nordic Interiors Ltd", "Cypress Trading Co", "Delacroix & Sons"]

CITIES = [("Toronto", "Ontario", "Canada"), ("Mississauga", "Ontario", "Canada"),
          ("Grand Rapids", "Michigan", "United States"), ("Montreal", "Quebec", "Canada"),
          ("High Point", "North Carolina", "United States")]

FIRST = ["Jane", "Bob", "Priya", "Marcus", "Elena", "Tomas", "Aisha", "Derek", "Nina", "Owen"]
LAST = ["Smith", "Chen", "Rao", "Okafor", "Duval", "Berg", "Haddad", "Kowalski", "Reyes", "Lindqvist"]
TITLES = ["Owner", "CEO", "Procurement Manager", "Operations Director", "Sourcing Manager"]


def domain_for(name: str) -> str:
    slug = "".join(ch for ch in name.lower() if ch.isalnum() or ch == " ")
    return slug.replace(" ", "").replace("and", "")[:22] + ".com"


def hexid(n: int) -> str:
    return f"{n:024x}"


companies, people = [], []
cid = 0

def add_company(name, kind, desc, industry):
    global cid
    cid += 1
    city, state, country = random.choice(CITIES)
    return {
        "id": hexid(0x5e66b6000000 + cid),
        "name": name,
        "website_url": f"http://www.{domain_for(name)}",
        "primary_domain": domain_for(name),
        "estimated_num_employees": random.choice([14, 28, 45, 88, 130, 260, 410]),
        "industry": industry,
        "keywords": ["furniture", kind],
        "city": city, "state": state, "country": country,
        "short_description": desc,
    }


for name, kind in MAKERS:
    desc = ("Manufacturer of upholstered sofas, sectionals and armchairs for the "
            "contract and wholesale trade, produced in our own facility."
            if kind == "sofa" else
            "Furniture manufacturer producing case goods, tables and storage for "
            "residential and commercial clients.")
    companies.append(add_company(name, kind, desc, "furniture"))

for name in RETAILERS:
    companies.append(add_company(
        name, "retail",
        "Retail showroom offering furniture and home furnishings from leading brands.",
        "retail"))

for name in THIN:
    companies.append(add_company(name, "unknown", "Furniture company.", "furniture"))

# Out-of-band cases that phase 1.5 must catch on firmographics alone.
tiny = add_company("Willow Craft Studio", "sofa", "Small-batch upholstery workshop.", "furniture")
tiny["estimated_num_employees"] = 4
companies.append(tiny)

abroad = add_company("Kettering Sofa Ltd", "sofa",
                     "Manufacturer of upholstered seating.", "furniture")
abroad["country"] = "United Kingdom"
companies.append(abroad)

# People — only inside manufacturer + thin companies.
pid = 0
for c in companies:
    if c["industry"] == "retail":
        continue
    for _ in range(random.choice([1, 2, 2, 3])):
        pid += 1
        fn, ln = random.choice(FIRST), random.choice(LAST)
        people.append({
            "id": hexid(0x7a21c9000000 + pid),
            "first_name": fn, "last_name": ln, "name": f"{fn} {ln}",
            "title": random.choice(TITLES),
            "linkedin_url": f"http://www.linkedin.com/in/{fn.lower()}-{ln.lower()}-{pid}",
            "email": None,                      # Apollo withholds at search time
            "organization_id": c["id"],
            "organization": {"id": c["id"], "name": c["name"],
                             "primary_domain": c["primary_domain"]},
        })

(OUT / "apollo_companies.json").write_text(json.dumps(
    {"organizations": companies,
     "pagination": {"page": 1, "per_page": 100, "total_entries": len(companies),
                    "total_pages": 1}}, indent=2))

(OUT / "apollo_people.json").write_text(json.dumps(
    {"people": people,
     "pagination": {"page": 1, "per_page": 100, "total_entries": len(people),
                    "total_pages": 1}}, indent=2))

# Clay: ~80% hit rate, one row with deliberately dirty casing/whitespace.
clay = {}
for i, p in enumerate(people):
    dom = p["organization"]["primary_domain"]
    key_name = p["name"]
    if i == 3:
        key_name = f"  {p['name'].upper()} "          # must still join after norm
    if random.random() < 0.80:
        first = p["first_name"].lower()
        clay[f"{key_name.strip().lower()}|{dom}"] = {
            "name": p["name"], "domain": dom,
            "email": f"{first}@{dom}", "verified": True,
            "provider": random.choice(["prospeo", "findymail", "datagma"]),
        }
(OUT / "clay_enrichment.json").write_text(json.dumps(clay, indent=2))

# HubSpot export — 15% of people already known.
already = random.sample(people, k=max(1, int(len(people) * 0.15)))
with (OUT / "hubspot_contacts.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["firstname", "lastname", "email", "linkedin_url", "company_domain"])
    for p in already:
        dom = p["organization"]["primary_domain"]
        w.writerow([p["first_name"], p["last_name"],
                    f"{p['first_name'].lower()}@{dom}", p["linkedin_url"], dom])

print(f"companies={len(companies)} people={len(people)} "
      f"clay_hits={len(clay)} crm_known={len(already)}")
