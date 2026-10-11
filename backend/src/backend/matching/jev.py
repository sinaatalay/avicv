"""Scoring (candidate, job) pairs with Jev, TypeSafe's decision model.

Each pair gets one request. Its state is `{candidate, job}`, and it asks every question at once: TypeSafe answers
questions about one state in parallel, so extra questions are nearly free, whereas several jobs in one state would
distract the model. Jev returns calibrated probabilities instead of text, and bills input tokens only.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from rendercv.schema.models.cv.cv import Cv
from rendercv.schema.models.cv.entries.bases.entry_with_complex_fields import (
    get_date_object,
)
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, NoulCriteria, Score

from ..config import Settings

log = logging.getLogger(__name__)

PRICE_PER_MTOK = 0.042  # USD per million input tokens for jev-1.13; output is free

# Bump when the questions change, so cached answers to the old ones aren't reused
QUESTIONS_VERSION = "1"
QUESTIONS = {
    "fit": Score(
        instructions="How well does `candidate` match the work and the requirements described in `job`?",
        criteria=[
            "Unrelated: a different field or kind of work",
            "Adjacent field, but missing most of the core requirements",
            "Meets some of the core requirements",
            "Meets most of the core requirements",
            "Meets essentially all of the core requirements",
        ],
    ),
    "interview": Noul(
        instructions="Would a recruiter screening applicants for `job` likely invite `candidate` to a first interview?",
        criteria=NoulCriteria(
            true="The candidate's experience and skills make them a credible applicant for this role.",
            false="The candidate would likely be screened out, e.g. wrong field, too junior, or missing required skills.",
        ),
    ),
    "must_haves": Noul(
        instructions="Does `candidate` meet every qualification that `job` explicitly lists as required?",
        criteria=NoulCriteria(
            true="Every required skill, degree, certification and minimum amount of experience is evidenced in the "
            "candidate's profile.",
            false="At least one explicitly required qualification is missing from the candidate's profile.",
        ),
    ),
    "seniority": Choice(
        instructions="How does the seniority of `candidate` compare to the level `job` is hiring for?",
        criteria={
            "below": "The job expects clearly more experience or seniority than the candidate has.",
            "matches": "The candidate's level is about what the job is hiring for.",
            "above": "The candidate is clearly more senior than the role.",
        },
    ),
    "location": Noul(
        instructions="Can `candidate` take `job` without relocating, given where the candidate lives and the job's "
        "location and remote policy?",
        criteria=NoulCriteria(
            true="The job is in the candidate's area, or it is remote and open to people in the candidate's country.",
            false="The job requires being in a place far from the candidate, or is remote only for other countries.",
        ),
    ),
}


@dataclass
class JobScore:
    job_id: int
    job_hash: str
    score: float
    fit: float
    interview: float
    must_haves: float
    location: float
    seniority: str
    input_tokens: int


def composite(fit: float, interview: float, must_haves: float) -> float:
    """Weights live in code, where they're cheap to tune. Location is a filter in the UI, not part of the score."""
    return 0.5 * fit / 4 + 0.3 * interview + 0.2 * must_haves


def candidate_state(cv: Cv, today: date | None = None) -> dict[str, Any]:
    """What Jev sees of the candidate: the professional content, never contact details.

    Years of experience are computed here, because Jev is unreliable at date arithmetic.
    """
    today = today or date.today()
    state: dict[str, Any] = {"headline": cv.headline, "location": cv.location}
    roles: list[dict[str, Any]] = []
    intervals: list[tuple[date, date]] = []
    for section in cv.rendercv_sections:
        title, entries = section.title.lower(), section.entries
        match section.entry_type:
            case "TextEntry":
                state.setdefault("summary", []).extend(entries)
            case "ExperienceEntry":
                for entry in entries:
                    role, interval = describe_role(entry, today)
                    roles.append(role)
                    if interval:
                        intervals.append(interval)
            case "EducationEntry":
                state.setdefault("education", []).extend(map(describe_degree, entries))
            case "OneLineEntry":
                skills = {e.label: e.details for e in entries}
                state.setdefault("skills", {}).update(skills)
            case "NormalEntry":
                items = [" - ".join(filter(None, [e.name, e.summary])) for e in entries]
                state.setdefault(title, []).extend(items)
            case "PublicationEntry":
                items = [f"{e.title} ({e.journal or ''})" for e in entries]
                state.setdefault("publications", []).extend(items)
            case "BulletEntry":
                state.setdefault(title, []).extend(e.bullet for e in entries)
            case "NumberedEntry":
                state.setdefault(title, []).extend(e.number for e in entries)
            case "ReversedNumberedEntry":
                state.setdefault(title, []).extend(e.reversed_number for e in entries)

    years = round(merged_months(intervals) / 12, 1)
    state["total_years_of_professional_experience"] = years
    state["experience"] = roles
    return {k: v for k, v in state.items() if v not in (None, [], {})}


def describe_role(
    entry: Any, today: date
) -> tuple[dict[str, Any], tuple[date, date] | None]:
    """A role and, when its dates are exact, the interval it covers."""
    start = entry.start_date or entry.date
    role = {
        "title": entry.position,
        "company": entry.company,
        "dates": f"{start or '?'} to {entry.end_date or 'present'}",
        "summary": entry.summary,
        "highlights": entry.highlights or [],
    }
    if start is None or not DATE.fullmatch(str(start)):  # free text like "Summer 2020"
        return role, None
    interval = (
        get_date_object(start, today),
        get_date_object(entry.end_date or "present", today),
    )
    role["years"] = round(months(*interval) / 12, 1)
    return role, interval if entry.start_date else None


def describe_degree(entry: Any) -> str:
    parts = [entry.degree, entry.area, "at", entry.institution]
    return " ".join(filter(None, parts)) + f" ({entry.end_date or entry.date or ''})"


def months(start: date, end: date) -> int:
    return max((end.year - start.year) * 12 + end.month - start.month, 0)


def merged_months(intervals: list[tuple[date, date]]) -> int:
    """Total months covered, counting overlapping roles once."""
    total, cursor = 0, None
    for start, end in sorted(intervals):
        if cursor is None or start > cursor:
            total += months(start, end)
            cursor = end
        elif end > cursor:
            total += months(cursor, end)
            cursor = end
    return total


DATE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")

BOILERPLATE = re.compile(
    r"(equal (employment )?opportunity|we are an equal|\beeo\b|reasonable accommodation|benefits|perks|"
    r"what we offer|privacy (notice|policy)|pay transparency|e-verify|background check)",
    re.IGNORECASE,
)


def job_state(job: Mapping[str, Any], max_chars: int = 6000) -> dict[str, Any]:
    """What Jev sees of the job: benefits and legal boilerplate dropped, long descriptions cut."""
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", job.get("description_text") or "")
        if p.strip()
    ]
    description = "\n\n".join(p for p in paragraphs if not BOILERPLATE.search(p[:120]))
    if len(description) > max_chars:
        description = description[:max_chars].rsplit(" ", 1)[0] + " ..."
    return {
        "title": job["title"],
        "company": job.get("company"),
        "locations": job.get("locations"),
        "workplace_type": job.get("workplace_type"),
        "description": description,
    }


def make_client(settings: Settings) -> AsyncTypeSafeClient:
    if settings.typesafe_api_key is None:
        raise RuntimeError("TYPESAFE_API_KEY is not set")
    return AsyncTypeSafeClient(
        api_key=settings.typesafe_api_key.get_secret_value(), model=settings.jev_model
    )


async def score_jobs(
    client: AsyncTypeSafeClient,
    candidate: dict[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    *,
    concurrency: int,
) -> AsyncIterator[tuple[Mapping[str, Any], JobScore | Exception]]:
    """Score every job, yielding results as they complete. A failed request yields its exception."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(
        job: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], JobScore | Exception]:
        try:
            async with semaphore:
                response = await client.system_one(
                    state={"candidate": candidate, "job": job_state(job)},
                    questions=QUESTIONS,
                )
            nouls, scores, choices = response.nouls, response.scores, response.choices
            fit, interview, must_haves = (
                scores["fit"].score,
                nouls["interview"].noul,
                nouls["must_haves"].noul,
            )
            return job, JobScore(
                job_id=job["id"],
                job_hash=job["content_hash"],
                score=composite(fit, interview, must_haves),
                fit=fit,
                interview=interview,
                must_haves=must_haves,
                location=nouls["location"].noul,
                seniority=choices["seniority"].choice,
                input_tokens=response.usage.input_tokens or 0,
            )
        except Exception as e:  # noqa: BLE001 - one failed job (after the SDK's retries) mustn't stop the run
            log.warning("Jev request for job %s failed: %r", job["id"], e)
            return job, e

    for next_done in asyncio.as_completed([one(job) for job in jobs]):
        yield await next_done
