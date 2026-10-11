"""Free pre-filter before Jev: open US/Canada jobs, ranked by BM25 against the candidate's titles and skills.

Many postings are the same role in several locations (a fifth of Greenhouse's), so postings are grouped by
company and title, and each role is scored once with all its locations.
"""

import re
from pathlib import Path
from typing import Any

from ..db import connect

STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with",
    "senior", "junior", "lead", "staff", "principal", "intern", "i", "ii", "iii",
}  # fmt: skip

SHORTLIST_SQL = """
SELECT j.id, j.content_hash, j.title, j.company, j.locations_text, j.workplace_type, j.description_text,
       bm25(jobs_fts, 4.0, 0.5, 0.0, 1.0) AS rank
FROM jobs_fts
JOIN jobs AS j ON j.id = jobs_fts.rowid
WHERE jobs_fts MATCH :query
  AND j.closed_at IS NULL
  AND j.geo_bucket IN ('us_ca', 'remote_unspecified')
ORDER BY rank
LIMIT :limit
"""


def search_terms(candidate: dict[str, Any]) -> list[str]:
    """Words from the candidate's headline, job titles and skills, for an OR query."""
    text = " ".join(
        [candidate.get("headline") or ""]
        + [role["title"] for role in candidate.get("experience", [])]
        + list(candidate.get("skills", {}).values())
    )
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z+#.]+", text)}
    return sorted(w.rstrip(".") for w in words - STOPWORDS)


def shortlist(
    jobs_db: Path, candidate: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    """Up to `limit` roles, best first. Returns nothing until the first job sync has created `jobs_db`."""
    terms = search_terms(candidate)
    if not terms or not jobs_db.exists():
        return []
    query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    conn = connect(jobs_db, readonly=True)
    try:
        rows = conn.execute(
            SHORTLIST_SQL, {"query": query, "limit": limit * 2}
        ).fetchall()
    finally:
        conn.close()

    roles: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = ((row["company"] or "").casefold(), row["title"].casefold())
        parts = re.split(r"[|;]", row["locations_text"] or "")
        locations = list(dict.fromkeys(p.strip() for p in parts if p.strip()))
        # Rows come best first, so an existing entry is a better-ranked posting of the role
        if role := roles.get(key):
            role["locations"] += [
                loc for loc in locations if loc not in role["locations"]
            ]
            role["posting_ids"].append(row["id"])
        else:
            roles[key] = dict(row) | {
                "locations": locations,
                "posting_ids": [row["id"]],
            }
    return list(roles.values())[:limit]
