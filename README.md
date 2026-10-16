# GTM Prospecting Pipeline

A six-phase outbound prospecting pipeline that routes work between Apollo and Clay based on what each tool is actually good at, with cost control and a hard human gate before anything sends.

Runs end to end on synthetic fixtures shaped like real API responses. Switching to live is a backend argument, not a rewrite.

**Scope note:** this is a deterministic pipeline, not an agent. It runs a fixed sequence with one LLM call (phase 2 tier scoring) and no autonomous decision-making. A conversational layer that elicits the ICP and constructs the search is the intended v2, and is deliberately not claimed here.

```bash
python data/make_fixtures.py     # generate fixtures
python run.py                    # phases 1-5, stops at the gate
python run.py --approve <token>  # phase 6
```

---

## The problem

Most prospecting automation treats "find companies," "qualify them," "find people," and "get their emails" as one undifferentiated job handed to one tool. That is expensive and it produces bad lists.

Apollo and Clay are not competitors. Apollo owns a database and a sending engine; it is good at turning a universe into a shortlist and at putting mail on the wire. Clay owns nothing — it chains 100+ third-party providers in a waterfall until one returns a verified email. Single-source lookup finds roughly 60–75% of emails; a waterfall reaches 85–92%.

So the design rule underneath this repo:

> **Rows getting fewer → Apollo. Rows getting wider → Clay.**
>
> Filtering is deletion. Enrichment is addition. Every step is one or the other.

## Pipeline

| Phase | Step | Tool | Cost |
|---|---|---|---|
| 1 | Company search — firmographics only | Apollo | free |
| 1.5 | Rule disqualification | code | free |
| 2 | Tier + confidence scoring | LLM | cents |
| 3 | Contact search, scoped by `organization_ids` | Apollo | free |
| 4 | CRM dedupe | code | free |
| 5 | **Email waterfall** | **Clay** | **the only paid step** |
| — | **Human approval gate** | — | — |
| 6 | Create, label, enroll | Apollo | plan-included |

Sample run on 22 synthetic companies:

```
1   Apollo company search                22 companies
1.5 Rule disqualification                14 kept  (8 killed, $0)
2   Tier scoring                         12 enrichable
    -> review queue (low confidence)      2
3   Apollo contact search                26 people (no emails yet)
4   HubSpot dedupe                       23 new  (3 already owned, $0)
5   Clay waterfall                       19 verified  (83% hit rate)
    -> served from cache                  0  (0 credits)
    -> billed to Clay                    23  (92 credits)

  STOP — human approval required before phase 6
```

Second run, unchanged inputs:

```
5   Clay waterfall                       19 verified  (83% hit rate)
    -> served from cache                 23  (0 credits)
    -> billed to Clay                     0  (0 credits)
```

---

## Four decisions worth explaining

### 1. Clay does one job

An earlier version used Clay for account qualification too — read the company site, decide whether they actually manufacture. That is a judgment call, and judgment is cheap: an LLM does it for a fraction of a cent while Clay charges credits.

What Clay is irreplaceable for is the waterfall. No amount of reasoning conjures somebody's work email.

So Clay was cut back to a single call on the smallest list in the pipeline — ~23 contacts instead of 22 companies *plus* 23 contacts. Same output, most of the bill gone.

### 2. Qualification splits into two steps with different costs

Phase 1.5 is free rules on fields Apollo already returned. It kills the obvious Tier 3 — retailers, showrooms, distributors — using industry codes, name patterns (`gallery`, `showroom`, `interiors`), headcount bands, and geography. Eight of 22 companies die here at zero cost.

Phase 2 is the paid-but-cheap tier split, and it *cannot* happen at 1.5: Apollo labels a sofa specialist and a general furniture maker identically as `furniture`. Splitting Tier 1 from Tier 2 on firmographics alone would be a guess, and then you would spend Clay credits according to a guess.

### 3. Routing is on confidence, not tier

The intuitive design routes Tier 3 to a human. That is backwards — a confidently-classified retailer is the *easy* case, and nobody needs to look at it.

The rows that need a human are the ones the classifier could not decide. `Nordic Interiors Ltd — "Furniture company." — 40 employees` could be a manufacturer or a showroom. That row is worth thirty seconds of attention, because it might be a Tier 1 about to be discarded.

So every company gets a tier **and** a confidence score. Below threshold goes to `out/review_queue.json` regardless of tier. The system reports what it is unsure about instead of quietly guessing.

### 4. The gate is structural

Everything through phase 5 is reversible. Phase 6 is not — a sent email cannot be recalled, and one bad filter propagated silently through five automated phases is how you mail 200 wrong people from your own domain.

So phase 6 is a separate invocation requiring a token, and the token is a hash of the contact list itself:

```python
manifest["approval_token"] = hashlib.sha256(
    json.dumps(manifest["contacts"], sort_keys=True).encode()
).hexdigest()[:16]
```

If anything upstream changed after the human read the manifest, the hash no longer matches and the send refuses. You cannot approve one list and ship a different one. There is no code path from `run()` to a live send.

---

## The join problem

Apollo IDs are private to Apollo. Clay has never seen them and will not return them. So every hand-off has to be re-joined on something both systems observe independently: **domain** for companies, **(name, domain)** for people.

This fails silently when it fails. No exception — just a row that quietly disappears, or worse, an email attached to the wrong person.

Two defences, both in `src/join.py`:

```python
def rejoin(returned, lookup, key_fn):
    matched, unmatched = [], []
    for row in returned:
        apollo_id = lookup.get(key_fn(row))
        if apollo_id is None:
            unmatched.append(row)      # surfaced, never dropped
        else:
            matched.append({**row, "apollo_id": apollo_id})
    return matched, unmatched
```

Normalisation, because `Acmefurniture.com ` and `acmefurniture.com` are the same company and different dict keys. The fixtures deliberately include one row with mangled casing and whitespace to prove it rejoins.

And an explicit `unmatched` pile written to disk. An unexpectedly large one is the earliest signal something upstream broke.

## Cost control

`src/cache.py` — SQLite, keyed on the same normalised join key. Nothing reaches Clay if it is already known locally.

Misses are cached too, with a 30-day TTL. A person Clay could not find is still information you paid for; re-asking on every debug run is pure waste.

Every billed call is written to a `spend_log` table, so lifetime credit burn is queryable rather than a surprise on the invoice.

## Config, not code

`config/furniture_arte.json` holds the ICP, the disqualification rules, the tier rubric, the confidence threshold, and the Apollo filter payloads. The engine reads it; nothing furniture-specific is hardcoded.

Onboarding a second client is a new JSON file.

## Layout

```
config/furniture_arte.json     ICP, rules, tier rubric, thresholds
data/make_fixtures.py          synthetic fixtures in real API shapes
src/join.py                    normalisation + rejoin (the fragile seam)
src/cache.py                   SQLite enrichment cache + spend log
src/adapters/apollo.py         search, create, label, enroll
src/adapters/clay.py           waterfall only, cache-gated
src/phases/qualify.py          1.5 disqualify, 2 score
src/phases/dedupe.py           4 CRM dedupe
src/pipeline.py                orchestrator + gate
run.py                         CLI
```

## Live mode

Both adapters carry live code paths that raise until credentials are present. Two known blockers:

- Apollo's `mixed_people/api_search` is plan-gated and returns `API_INACCESSIBLE` on Free. Whether Basic unlocks it or Organization is required is unconfirmed — Apollo's own error message and their published pricing disagree.
- Clay's HTTP API and webhooks sit on the Growth plan.

Provider ordering inside the waterfall (cheap first, expensive as fallback) may only be configurable in Clay's own UI rather than through the API. If so it is a one-time manual setup step, noted here rather than silently assumed.
