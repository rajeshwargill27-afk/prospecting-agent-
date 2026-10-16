"""
Join keys between Apollo and Clay.

Apollo IDs are private to Apollo. Clay has never seen them and will not return
them. So every hand-off between the two tools has to be re-joined on a value
both systems can independently observe:

    companies -> domain
    people    -> (name, domain)

Normalisation matters more than it looks. "Acmefurniture.com " and
"acmefurniture.com" are the same company and different dict keys, and that
mismatch fails silently: no exception, just an unmatched row that quietly
disappears. Every join in this repo goes through these functions.
"""

from urllib.parse import urlparse


def norm_domain(value: str | None) -> str:
    """Reduce anything domain-shaped to a bare lowercase hostname."""
    if not value:
        return ""
    v = value.strip().lower()
    if v.startswith(("http://", "https://")):
        v = urlparse(v).netloc
    v = v.removeprefix("www.")
    return v.split("/")[0].strip()


def norm_name(value: str | None) -> str:
    """Collapse whitespace and case so 'Jane  Smith' == 'jane smith'."""
    if not value:
        return ""
    return " ".join(value.split()).lower()


def company_key(domain: str | None) -> str:
    return norm_domain(domain)


def person_key(name: str | None, domain: str | None) -> tuple[str, str]:
    return (norm_name(name), norm_domain(domain))


def build_lookup(records: list[dict], key_fn, id_field: str) -> dict:
    """
    Save the Apollo IDs BEFORE handing a list to Clay.

    Returns {join_key: apollo_id}. Collisions are reported rather than
    silently overwritten — two people with the same name at the same company
    is rare but real, and you want to know rather than lose one.
    """
    lookup: dict = {}
    collisions: list = []
    for r in records:
        k = key_fn(r)
        if k in lookup:
            collisions.append(k)
        lookup[k] = r[id_field]
    if collisions:
        print(f"  ! {len(collisions)} join-key collision(s): {collisions[:3]}")
    return lookup


def rejoin(returned: list[dict], lookup: dict, key_fn) -> tuple[list[dict], list[dict]]:
    """
    Match enriched rows coming back from Clay to their original Apollo IDs.

    Returns (matched, unmatched). Unmatched rows are returned, never dropped —
    an unexpectedly large unmatched pile is the earliest signal that something
    upstream broke.
    """
    matched, unmatched = [], []
    for row in returned:
        apollo_id = lookup.get(key_fn(row))
        if apollo_id is None:
            unmatched.append(row)
        else:
            matched.append({**row, "apollo_id": apollo_id})
    return matched, unmatched
