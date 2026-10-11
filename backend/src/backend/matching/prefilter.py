"""Lexical retrieval over professional evidence, with explicit coverage diagnostics.

Read all eligible lexical hits in rank order so duplicate postings cannot consume
the shortlist budget. Collapse only company/title/description-equivalent postings;
different teams with the same title remain different roles.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..db import connect

STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with",
    "senior", "junior", "lead", "staff", "principal", "intern", "i", "ii", "iii",
    "about", "after", "all", "also", "are", "be", "been", "before", "being", "between", "both", "but",
    "can", "could", "did", "do", "does", "each", "had", "has", "have", "how", "if", "is", "it", "its",
    "more", "most", "my", "not", "our", "out", "over", "per", "same", "some", "such", "than", "that",
    "their", "them", "then", "there", "these", "they", "this", "through", "under", "up", "using", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who", "will", "would", "you", "your",
    "proficient", "familiar", "experienced", "fluent", "native", "full", "time", "summer", "present",
}  # fmt: skip
MAX_TERMS = 128
WORDS = re.compile(r"[^\W\d_][\w+#.-]*", re.UNICODE)
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
EVIDENCE_FIELDS = {
    "name",
    "title",
    "area",
    "summary",
    "highlights",
    "details",
    "bullet",
    "number",
    "reversed_number",
}
ELIGIBLE = "j.closed_at IS NULL AND j.geo_bucket IN ('us_ca', 'remote_unspecified')"


def evidence(value: Any) -> Iterator[str]:
    """Only professional content, excluding URLs, dates, contacts, and institutions."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from evidence(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in EVIDENCE_FIELDS:
                yield from evidence(item)


def search_terms(candidate: dict[str, Any]) -> list[str]:
    """Bounded, deterministic terms from employment, education, skills and projects.

    High-signal fields get priority if an unusually long CV exceeds the term cap.
    Accept older persisted candidate shapes as well as the current projection.
    """
    weighted: list[tuple[Any, int]] = [(candidate.get("headline"), 4)]
    for role in candidate.get("experience", []):
        weighted.extend(
            [
                (role.get("title"), 4),
                (role.get("summary"), 1),
                (role.get("highlights"), 1),
            ]
        )
    skills = candidate.get("skills", {})
    weighted.append((list(skills.values()) if isinstance(skills, dict) else skills, 3))
    weighted.extend(
        (candidate.get(key), weight)
        for key, weight in (
            ("education", 3),
            ("summary", 2),
            ("projects", 2),
            ("publications", 2),
        )
    )
    for section in candidate.get("sections", []):
        weighted.append((section.get("entries"), 2))
    counts: Counter[str] = Counter()
    for value, weight in weighted:
        for text in evidence(value):
            text = URL.sub(" ", LINK.sub(r"\1", text))
            for word in WORDS.findall(text.casefold()):
                word = word.strip(".-")
                if (
                    word
                    and word not in STOPWORDS
                    and (len(word) > 1 or word in {"c", "r"})
                ):
                    counts[word] += weight
    return sorted(sorted(counts, key=lambda term: (-counts[term], term))[:MAX_TERMS])


def role_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Equivalent content is evidence of a duplicate; a shared title alone is not."""
    description = (
        re.sub(r"\s+", " ", row.get("description_text") or "").strip().casefold()
    )
    if not description:
        return (row["provider"], row["board"], row["source_id"])
    return (
        (row.get("company") or "").casefold(),
        row["title"].casefold(),
        row.get("workplace_type") or "",
        row.get("employment_type") or "",
        hashlib.sha256(description.encode()).hexdigest(),
    )


def posting_variant(row: dict[str, Any], locations: list[str]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "id",
            "provider",
            "board",
            "source_id",
            "content_hash",
            "apply_url",
            "source_url",
            "workplace_type",
            "employment_type",
        )
    } | {"locations": locations}


def locations_for(row: dict[str, Any]) -> list[str]:
    if raw := row.get("locations_json"):
        try:
            values = json.loads(raw)
            if isinstance(values, list) and all(
                isinstance(value, str) for value in values
            ):
                return list(
                    dict.fromkeys(value.strip() for value in values if value.strip())
                )
        except TypeError, ValueError:
            pass
    return list(
        dict.fromkeys(
            part.strip()
            for part in re.split(r"[|;]", row.get("locations_text") or "")
            if part.strip()
        )
    )


def shortlist_with_diagnostics(
    jobs_db: Path, candidate: dict[str, Any], limit: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if limit < 1:
        raise ValueError("limit must be positive")
    diagnostics = dict.fromkeys(
        (
            "catalog_total",
            "eligible_total",
            "matching_postings",
            "distinct_roles",
            "shortlisted",
            "missing_descriptions",
        ),
        0,
    )
    if not jobs_db.exists():
        return [], diagnostics
    terms = search_terms(candidate)
    conn = connect(jobs_db.resolve(), readonly=True)
    try:
        # The writer can have created the file before its first schema commit.
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        if not {"jobs", "jobs_fts"} <= tables:
            return [], diagnostics
        conn.execute("BEGIN")  # counts and ranked rows describe one catalog snapshot
        diagnostics["catalog_total"] = conn.execute(
            "SELECT count(*) FROM jobs WHERE closed_at IS NULL"
        ).fetchone()[0]
        diagnostics["eligible_total"] = conn.execute(
            f"SELECT count(*) FROM jobs j WHERE {ELIGIBLE}"
        ).fetchone()[0]
        if not terms:
            return [], diagnostics
        query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        # Exclude compressed provider payloads from the in-memory model context.
        columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(jobs)")
            if row["name"] != "raw_zstd"
        ]
        rows = conn.execute(
            f"SELECT {', '.join('j.' + column for column in columns)}, bm25(jobs_fts, 4.0, 0.5, 0.0, 1.0) AS rank "
            "FROM jobs_fts JOIN jobs j ON j.id = jobs_fts.rowid "
            f"WHERE jobs_fts MATCH :query AND {ELIGIBLE} ORDER BY rank, j.id",
            {"query": query},
        )
        roles: dict[tuple[str, ...], dict[str, Any]] = {}
        seen: set[tuple[str, ...]] = set()
        for item in rows:
            row = dict(item)
            diagnostics["matching_postings"] += 1
            key = role_key(row)
            seen.add(key)
            locations = locations_for(row)
            if key in roles:
                role = roles[key]
                role["locations"] += [
                    location
                    for location in locations
                    if location not in role["locations"]
                ]
                role["posting_ids"].append(row["id"])
                role["postings"].append(posting_variant(row, locations))
            elif len(roles) < limit:
                roles[key] = row | {
                    "locations": locations,
                    "posting_ids": [row["id"]],
                    "postings": [posting_variant(row, locations)],
                }
        diagnostics["distinct_roles"] = len(seen)
        diagnostics["shortlisted"] = len(roles)
        diagnostics["missing_descriptions"] = sum(
            not row.get("description_text") for row in roles.values()
        )
        return list(roles.values()), diagnostics
    finally:
        conn.close()


def shortlist(
    jobs_db: Path, candidate: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    return shortlist_with_diagnostics(jobs_db, candidate, limit)[0]
