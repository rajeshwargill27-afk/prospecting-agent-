"""
Phases 1.5 and 2 — qualification.

Split deliberately into two steps with different costs:

  1.5  disqualify()  free. Rules on fields Apollo already gave us. Kills the
       obvious Tier 3 (retailers, showrooms, distributors) before anything
       paid runs. This is the cheapest row-removal in the pipeline, so it
       goes as early as possible.

  2    score()       cents. An LLM reads the company description and assigns
       tier + confidence. This cannot happen at 1.5: Apollo labels a sofa
       manufacturer and a general furniture maker identically, so a T1/T2
       split on firmographics alone would be a guess.

Routing is on CONFIDENCE, not tier. A confident Tier 3 needs no human — it is
the easy case. An uncertain anything does, because that is where a real Tier 1
gets thrown away by mistake.
"""

import json
import os
import urllib.request

MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------- phase 1.5
def disqualify(companies: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    """Free binary keep/kill. Returns (survivors, killed)."""
    industry_block = [s.lower() for s in rules["industry_blocklist"]]
    name_block = [s.lower() for s in rules["name_blocklist"]]
    allowed = set(rules["allowed_countries"])

    survivors, killed = [], []
    for c in companies:
        name = (c.get("name") or "").lower()
        industry = (c.get("industry") or "").lower()
        heads = c.get("estimated_num_employees") or 0
        reason = None

        if any(b in industry for b in industry_block):
            reason = f"industry '{c.get('industry')}' on blocklist"
        elif any(b in name for b in name_block):
            reason = "company name signals retail/showroom"
        elif not (rules["min_employees"] <= heads <= rules["max_employees"]):
            reason = f"headcount {heads} outside band"
        elif c.get("country") not in allowed:
            reason = f"country '{c.get('country')}' out of scope"

        if reason:
            killed.append({**c, "kill_reason": reason})
        else:
            survivors.append(c)

    return survivors, killed


# ------------------------------------------------------------------ phase 2
PROMPT = """You classify furniture companies into an ICP tier from their profile.

{rubric}

Company:
  name: {name}
  industry: {industry}
  employees: {employees}
  description: {description}

Return ONLY a JSON object, no markdown, no preamble:
{{"tier": 1|2|3, "confidence": 0.0-1.0, "reason": "<12 words"}}

Set confidence below 0.7 whenever the description is too thin to tell
manufacturing from reselling. Guessing is worse than flagging."""


def _rubric_text(tiers: dict) -> str:
    lines = []
    for t in ("1", "2", "3"):
        spec = tiers[t]
        lines.append(f"Tier {t} — {spec['label']}")
        lines.extend(f"  - {s}" for s in spec["signals"])
    return "\n".join(lines)


def _heuristic(c: dict) -> dict:
    """Offline fallback so the pipeline runs with no API key."""
    text = f"{c.get('name','')} {c.get('short_description','')}".lower()
    thin = len(c.get("short_description") or "") < 40
    if any(w in text for w in ("sofa", "upholster", "sectional", "armchair")):
        return {"tier": 1, "confidence": 0.55 if thin else 0.88, "reason": "upholstered seating mentioned"}
    if any(w in text for w in ("manufactur", "factory", "production", "millwork")):
        return {"tier": 2, "confidence": 0.50 if thin else 0.80, "reason": "general furniture manufacturing"}
    if any(w in text for w in ("retail", "store", "showroom", "reseller", "distribut")):
        return {"tier": 3, "confidence": 0.85, "reason": "resells rather than manufactures"}
    return {"tier": 2, "confidence": 0.35, "reason": "insufficient signal to classify"}


def _call_claude(c: dict, rubric: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return _heuristic(c)
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": PROMPT.format(
            rubric=rubric,
            name=c.get("name"),
            industry=c.get("industry"),
            employees=c.get("estimated_num_employees"),
            description=c.get("short_description") or "(none)",
        )}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = json.loads(r.read())["content"][0]["text"]
        return json.loads(text.replace("```json", "").replace("```", "").strip())
    except Exception as e:
        print(f"  ! scoring fell back to heuristic for {c.get('name')}: {e}")
        return _heuristic(c)


def score(companies: list[dict], config: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (to_enrich, review_queue, rejected).

    to_enrich    — confident, and in an enrichable tier
    review_queue — below the confidence threshold, ANY tier
    rejected     — confident, but in a tier we do not pursue
    """
    rubric = _rubric_text(config["tiers"])
    routing = config["routing"]
    threshold = routing["confidence_threshold"]
    enrich_tiers = set(routing["enrich_tiers"])

    to_enrich, review, rejected = [], [], []
    for c in companies:
        verdict = _call_claude(c, rubric)
        row = {**c, **verdict}
        if verdict["confidence"] < threshold:
            review.append(row)
        elif verdict["tier"] in enrich_tiers:
            to_enrich.append(row)
        else:
            rejected.append(row)

    return to_enrich, review, rejected
