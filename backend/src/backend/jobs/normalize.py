"""Provider payloads to one job schema.

Values are kept as the provider gives them: timestamps keep their own UTC offsets (a
naive one stays naive), salary units and intervals are never guessed, and the raw item
is stored. Epoch timestamps (Lever milliseconds, Himalayas seconds) become UTC ISO 8601,
which loses nothing because epochs are UTC by definition.
"""

import hashlib
import html
import json
import re
from collections.abc import Callable
from compression import zstd
from datetime import UTC, datetime
from typing import Any

from .geo import classify, workplace_from_text
from .text import html_to_text

# Bump to re-normalize every stored job on the next run (also invalidates stored ETags)
NORMALIZER_VERSION = "1"

Raw = dict[str, Any]
Record = dict[str, Any]

# Columns written for a new or changed job, in table order
COLUMNS = (
    "provider", "board", "source_id", "company", "title", "locations_text",
    "locations_json", "countries", "regions", "geo_bucket", "workplace_type",
    "employment_type", "department", "team", "posted_at", "posted_at_field",
    "updated_at", "expires_at", "apply_url", "source_url", "salary_min", "salary_max",
    "salary_currency", "salary_interval", "salary_text", "description_text",
    "content_hash", "raw_zstd",
)  # fmt: skip


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(raw: object) -> str:
    """Changes when the provider's payload or our normalization changes."""
    payload = f"{NORMALIZER_VERSION}\n{canonical_json(raw)}".encode()
    return hashlib.sha256(payload).hexdigest()


def pack(raw: object) -> bytes:
    """The raw payload as stored: zstd-compressed canonical JSON (about 3x smaller)."""
    return zstd.compress(canonical_json(raw).encode(), level=3)


def unpack(blob: bytes) -> Any:
    return json.loads(zstd.decompress(blob))


def epoch_to_iso(value: object, *, millis: bool) -> str | None:
    if not isinstance(value, int | float | str) or value == "":
        return None
    try:
        seconds = float(value) / (1000 if millis else 1)
        return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")
    except ValueError, OverflowError, OSError:
        return None


def norm_employment(raw: object) -> str | None:
    if not raw:
        return None
    s = str(raw).casefold()
    if any(w in s for w in ("intern", "co-op", "coop", "apprentice")):
        return "intern"
    if "contract" in s or "freelance" in s:
        return "contract"
    if any(w in s for w in ("temp", "seasonal", "fixed term", "fixed-term")):
        return "temporary"
    if "part" in s:
        return "part_time"
    if any(w in s for w in ("full", "permanent", "regular")):
        return "full_time"
    return "other"


def norm_workplace(raw: object) -> str | None:
    s = str(raw or "").casefold().replace("-", "").replace(" ", "")
    if s in {"remote", "fullyremote", "remotefirst"}:
        return "remote"
    if s == "hybrid":
        return "hybrid"
    if s in {"onsite", "inoffice", "office", "inperson"}:
        return "onsite"
    return None


_INTERVAL_WORDS = (
    (
        re.compile(
            r"\b(annual|annually|yearly|per year|a year|salary)\b", re.IGNORECASE
        ),
        "year",
    ),
    (re.compile(r"\b(hourly|per hour|an hour)\b", re.IGNORECASE), "hour"),
    (re.compile(r"\b(monthly|per month)\b", re.IGNORECASE), "month"),
    (re.compile(r"\b(weekly|per week)\b", re.IGNORECASE), "week"),
    (re.compile(r"\b(daily|per day)\b", re.IGNORECASE), "day"),
)
_ASHBY_INTERVALS = {
    "1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week", "1 DAY": "day", "1 HOUR": "hour",
}  # fmt: skip
_LEVER_INTERVALS = {
    "per-year-salary": "year", "per-month-salary": "month", "per-week-salary": "week",
    "per-day-wage": "day", "per-hour-wage": "hour",
}  # fmt: skip
_HIMALAYAS_INTERVALS = {
    "annual": "year", "yearly": "year", "monthly": "month", "weekly": "week",
    "daily": "day", "hourly": "hour",
}  # fmt: skip


def interval_from_label(label: str | None) -> str | None:
    """An interval only when the label states one: "Canada Annual Pay Range" is a year,
    plain "Pay Range" is unknown."""
    for pattern, interval in _INTERVAL_WORDS:
        if label and pattern.search(label):
            return interval
    return None


def _amount(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if amount > 0 else None  # 0 means "not given", not "unpaid"


def _salary(
    low: object, high: object, currency: object, interval: str | None, text: str | None
) -> Record | None:
    low_amount, high_amount = _amount(low), _amount(high)
    if low_amount is None and high_amount is None:
        return None
    return {
        "min": low_amount,
        "max": high_amount,
        "currency": str(currency) if currency else None,
        "interval": interval,
        "text": text,
    }


def _dedupe(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str) and (v := value.strip()) and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    return out


def _country(address: Any) -> str | None:
    return ((address or {}).get("postalAddress") or {}).get("addressCountry")


# Providers. Each takes one raw item plus its board entry from boards.toml.


def greenhouse(job: Raw, board: Raw) -> Record:
    meta = {
        str(m.get("name") or "").casefold(): m.get("value")
        for m in job.get("metadata") or []
    }
    workplace = next(
        (
            norm_workplace(v)
            for k, v in meta.items()
            if ("workplace" in k or "remote" in k)
            and isinstance(v, str)
            and norm_workplace(v)
        ),
        None,
    )
    location = (job.get("location") or {}).get("name")
    offices = [o.get("location") or o.get("name") for o in job.get("offices") or []]
    employment = next(
        (
            v
            for k, v in meta.items()
            if ("employment" in k or "job type" in k) and isinstance(v, str)
        ),
        None,
    )
    # Pay ranges are in cents, and one job can list several (zones, countries, currencies)
    ranges = [
        r
        for r in job.get("pay_input_ranges") or []
        if _amount(r.get("min_cents")) or _amount(r.get("max_cents"))
    ]
    salary = None
    if ranges:
        by_currency: dict[str, list[Raw]] = {}
        for r in ranges:
            by_currency.setdefault(r.get("currency_type") or "", []).append(r)
        currency = next(
            (c for c in ("USD", "CAD") if c in by_currency), next(iter(by_currency))
        )
        group = by_currency[currency]
        intervals = {interval_from_label(r.get("title")) for r in group}
        lows = [a for r in group if (a := _amount(r.get("min_cents")))]
        highs = [a for r in group if (a := _amount(r.get("max_cents")))]
        salary = _salary(
            min(lows) / 100 if lows else None,
            max(highs) / 100 if highs else None,
            currency,
            intervals.pop() if len(intervals) == 1 else None,
            "; ".join(_dedupe(r.get("title") for r in group)) or None,
        )
    return {
        "source_id": str(job["id"]),
        "company": job.get("company_name") or board["company"],
        "title": job.get("title"),
        "locations": _dedupe([location, *offices]),
        "country_hints": [],
        "workplace_type": workplace or workplace_from_text(location),
        "employment_type": norm_employment(employment),
        "department": " / ".join(
            _dedupe(d.get("name") for d in job.get("departments") or [])
        )
        or None,
        "team": None,
        "posted_at": job.get("first_published"),
        "posted_at_field": "first_published",
        "updated_at": job.get("updated_at"),
        "expires_at": job.get("application_deadline"),
        "apply_url": job.get("absolute_url"),
        "source_url": job.get("absolute_url"),
        # Greenhouse HTML-escapes `content`; unescape exactly once
        "description_html": html.unescape(job.get("content") or "") or None,
        "salary": salary,
    }


def ashby(job: Raw, board: Raw) -> Record | None:
    if job.get("isListed") is False:
        return None
    compensation = job.get("compensation") or {}
    salary = None
    if job.get("shouldDisplayCompensationOnJobPostings") is not False:
        parts = [
            c
            for c in compensation.get("summaryComponents") or []
            if c.get("compensationType") == "Salary"
        ] or [
            c
            for tier in compensation.get("compensationTiers") or []
            for c in tier.get("components") or []
            if c.get("compensationType") == "Salary"
        ]
        if parts:
            part = parts[0]
            # Several tiers: one overall range, but only if currency and interval agree
            if (
                len(parts) > 1
                and len({(p.get("currencyCode"), p.get("interval")) for p in parts})
                == 1
            ):
                lows = [a for p in parts if (a := _amount(p.get("minValue")))]
                highs = [a for p in parts if (a := _amount(p.get("maxValue")))]
                part = part | {
                    "minValue": min(lows) if lows else None,
                    "maxValue": max(highs) if highs else None,
                }
            salary = _salary(
                part.get("minValue"),
                part.get("maxValue"),
                part.get("currencyCode"),
                _ASHBY_INTERVALS.get(str(part.get("interval") or "")),
                compensation.get("scrapeableCompensationSalarySummary")
                or compensation.get("compensationTierSummary"),
            )
    secondary = job.get("secondaryLocations") or []
    return {
        "source_id": str(job.get("id") or job.get("jobUrl")),
        "company": board["company"],
        "title": job.get("title"),
        "locations": _dedupe(
            [job.get("location"), *(s.get("location") for s in secondary)]
        ),
        "country_hints": _dedupe(
            [
                _country(job.get("address")),
                *(_country(s.get("address")) for s in secondary),
            ]
        ),
        "workplace_type": norm_workplace(job.get("workplaceType"))
        or ("remote" if job.get("isRemote") else None),
        "employment_type": norm_employment(job.get("employmentType")),
        "department": job.get("department") or None,
        "team": job.get("team") or None,
        "posted_at": job.get("publishedAt"),
        "posted_at_field": "publishedAt",
        "updated_at": None,
        "expires_at": None,
        "apply_url": job.get("applyUrl"),
        "source_url": job.get("jobUrl"),
        "description_html": job.get("descriptionHtml") or None,
        "salary": salary,
    }


def lever(job: Raw, board: Raw) -> Record:
    categories = job.get("categories") or {}
    parts = [job.get("description") or ""]  # opening + descriptionBody
    for section in job.get("lists") or []:
        heading = html.escape(section.get("text") or "")
        parts.append(f"<h3>{heading}</h3><ul>{section.get('content') or ''}</ul>")
    parts.append(job.get("additional") or "")
    pay = job.get("salaryRange") or {}
    salary = (
        _salary(
            pay.get("min"),
            pay.get("max"),
            pay.get("currency"),
            _LEVER_INTERVALS.get(str(pay.get("interval") or "")),
            job.get("salaryDescriptionPlain") or None,
        )
        if pay
        else None
    )
    return {
        "source_id": str(job["id"]),
        "company": board["company"],
        "title": job.get("text"),
        "locations": _dedupe(
            categories.get("allLocations") or [categories.get("location")]
        ),
        "country_hints": _dedupe([job.get("country")]),
        "workplace_type": norm_workplace(job.get("workplaceType")),
        "employment_type": norm_employment(categories.get("commitment")),
        "department": categories.get("department") or None,
        "team": categories.get("team") or None,
        # Evergreen requisitions keep their original createdAt (Palantir's go back to 2009)
        "posted_at": epoch_to_iso(job.get("createdAt"), millis=True),
        "posted_at_field": "createdAt",
        "updated_at": epoch_to_iso(job.get("updatedAt"), millis=True),
        "expires_at": None,
        "apply_url": job.get("applyUrl"),
        "source_url": job.get("hostedUrl"),
        "description_html": "".join(parts).strip() or None,
        "salary": salary,
    }


SMARTRECRUITERS_SECTIONS = (
    "companyDescription",
    "jobDescription",
    "qualifications",
    "additionalInformation",
)


def smartrecruiters(raw: Raw, board: Raw) -> Record:
    """`raw` is `{"list": item, "detail": detail or None}`: only the per-posting detail
    response has the description and the apply link."""
    item, detail = raw["list"], raw.get("detail") or {}
    location = item.get("location") or {}
    full = location.get("fullLocation") or ", ".join(
        str(location[k]) for k in ("city", "region", "country") if location.get(k)
    )
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    description = "".join(
        f"<h2>{html.escape(s.get('title') or '')}</h2>{s['text']}"
        for key in SMARTRECRUITERS_SECTIONS
        if (s := sections.get(key)) and s.get("text")
    )
    company_id = (item.get("company") or {}).get("identifier") or board["id"]
    pay = detail.get("compensation") or {}
    salary = (
        _salary(
            pay.get("min"),
            pay.get("max"),
            pay.get("currency"),
            interval_from_label(str(pay.get("period") or pay.get("interval") or "")),
            None,
        )
        if pay
        else None
    )
    remote, hybrid = location.get("remote"), location.get("hybrid")
    return {
        "source_id": str(item["id"]),
        "company": (item.get("company") or {}).get("name") or board["company"],
        "title": item.get("name"),
        "locations": _dedupe([full]),
        "country_hints": _dedupe([location.get("country")]),
        "workplace_type": "remote" if remote else "hybrid" if hybrid else "onsite",
        "employment_type": norm_employment(
            (item.get("typeOfEmployment") or {}).get("label")
        ),
        "department": (item.get("department") or {}).get("label")
        or (item.get("function") or {}).get("label"),
        "team": None,
        "posted_at": item.get("releasedDate"),
        "posted_at_field": "releasedDate",
        "updated_at": None,
        "expires_at": None,
        "apply_url": detail.get("applyUrl"),
        # List items carry no public URL; this documented pattern redirects to the posting
        "source_url": detail.get("postingUrl")
        or f"https://jobs.smartrecruiters.com/{company_id}/{item['id']}",
        "description_html": description or None,
        "salary": salary,
    }


def himalayas(job: Raw, board: Raw | None = None) -> Record:
    restrictions = job.get("locationRestrictions") or []
    return {
        "source_id": str(job["guid"]),
        "company": job.get("companyName"),
        "title": job.get("title"),
        "locations": _dedupe(restrictions) or ["Worldwide"],
        "country_hints": _dedupe(restrictions),
        "workplace_type": "remote",  # a remote-only board
        "employment_type": norm_employment(job.get("employmentType")),
        "department": ", ".join(_dedupe(job.get("parentCategories") or [])) or None,
        "team": None,
        "posted_at": epoch_to_iso(job.get("pubDate"), millis=False),
        "posted_at_field": "pubDate",
        "updated_at": None,
        "expires_at": epoch_to_iso(job.get("expiryDate"), millis=False),
        "apply_url": job.get("applicationLink"),
        "source_url": job.get("guid"),
        "description_html": job.get("description") or None,
        "salary": _salary(
            job.get("minSalary"),
            job.get("maxSalary"),
            job.get("currency"),
            _HIMALAYAS_INTERVALS.get(str(job.get("salaryPeriod") or "").casefold()),
            None,
        ),
    }


NORMALIZERS: dict[str, Callable[[Raw, Raw], Record | None]] = {
    "greenhouse": greenhouse,
    "ashby": ashby,
    "lever": lever,
    "smartrecruiters": smartrecruiters,
    "himalayas": himalayas,
}


def to_row(
    provider: str, board_id: str, raw: object, record: Record, chash: str
) -> Record:
    """A `jobs` row: geography, plain-text description, flat salary, packed raw payload."""
    geo = classify(
        record["locations"], record["country_hints"], record["workplace_type"]
    )
    salary = record.get("salary") or {}
    return {
        "provider": provider,
        "board": board_id,
        "source_id": record["source_id"],
        "company": (record.get("company") or "").strip() or None,
        "title": (record.get("title") or "").strip() or "(untitled)",
        "locations_text": " | ".join(record["locations"]) or None,
        "locations_json": json.dumps(record["locations"], ensure_ascii=False),
        "countries": json.dumps(geo["countries"]),
        "regions": json.dumps(geo["regions"]),
        "geo_bucket": geo["geo_bucket"],
        "workplace_type": record.get("workplace_type"),
        "employment_type": record.get("employment_type"),
        "department": record.get("department"),
        "team": record.get("team"),
        "posted_at": record.get("posted_at"),
        "posted_at_field": record.get("posted_at_field"),
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
        "apply_url": record.get("apply_url"),
        "source_url": record.get("source_url"),
        "salary_min": salary.get("min"),
        "salary_max": salary.get("max"),
        "salary_currency": salary.get("currency"),
        "salary_interval": salary.get("interval"),
        "salary_text": salary.get("text"),
        "description_text": html_to_text(record.get("description_html")) or None,
        "content_hash": chash,
        "raw_zstd": pack(raw),
    }
