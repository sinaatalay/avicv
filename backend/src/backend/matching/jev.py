"""Scoring (candidate, job) pairs with Jev, TypeSafe's decision model.

Each pair gets one request. Its state is `{candidate, job}`, and it asks every question at once: TypeSafe answers
questions about one state in parallel, so extra questions are nearly free, whereas several jobs in one state would
distract the model. Its answers are model estimates, not measured hiring probabilities.
"""

import asyncio
import logging
import math
import re
from collections.abc import AsyncGenerator, Mapping, Sequence
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
QUESTIONS_VERSION = "2"
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
        "location and remote policy? If only the candidate's country is known, assess country eligibility; "
        "do not invent a city or reject a job for an unknown commute within that country.",
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
    location: float | None
    seniority: str
    input_tokens: int

    def __post_init__(self) -> None:
        for name, maximum in (
            ("score", 1),
            ("fit", 4),
            ("interview", 1),
            ("must_haves", 1),
            ("location", 1),
        ):
            value = getattr(self, name)
            if value is None and name == "location":
                continue
            if not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"Invalid {name} score: {value!r}")
        if self.seniority not in {"below", "matches", "above"}:
            raise ValueError(f"Invalid seniority: {self.seniority!r}")
        if self.input_tokens < 0:
            raise ValueError("input_tokens must be nonnegative")


def composite(fit: float, interview: float, must_haves: float) -> float:
    """Weights live in code, where they're cheap to tune. Location is a filter in the UI, not part of the score."""
    return 0.5 * fit / 4 + 0.3 * interview + 0.2 * must_haves


def candidate_state(cv: Cv, today: date | None = None) -> dict[str, Any]:
    """Project RenderCV's entry types into a stable professional profile.

    Custom section names never become root keys: a section called "Skills" may
    contain bullets, prose, or one-line entries without changing a field's type.
    Preserve projects, research, and educational evidence alongside employment.
    Top-level contact fields are omitted; free text is not a redaction boundary.
    """
    today = today or date.today()
    location = (cv.location or "").strip()
    state: dict[str, Any] = {
        "headline": cv.headline,
        "location": location or "United States",
        "location_source": "resume" if location else "default",
        "summary": [],
        "skills": {},
        "education": [],
        "publications": [],
        "sections": [],
    }
    roles: list[dict[str, Any]] = []
    intervals: list[tuple[date, date]] = []
    for section in cv.rendercv_sections:
        entries = section.entries
        match section.entry_type:
            case "TextEntry":
                state["summary"].extend(entries)
            case "ExperienceEntry":
                for entry in entries:
                    role, interval = describe_role(entry, today)
                    roles.append(role)
                    if interval:
                        intervals.append(interval)
            case "EducationEntry":
                state["education"].extend(
                    entry.model_dump(mode="json", exclude_none=True)
                    for entry in entries
                )
            case "OneLineEntry":
                for entry in entries:
                    previous = state["skills"].get(entry.label)
                    state["skills"][entry.label] = (
                        f"{previous}; {entry.details}" if previous else entry.details
                    )
            case "PublicationEntry":
                state["publications"].extend(
                    entry.model_dump(mode="json", exclude_none=True)
                    for entry in entries
                )
            case _:
                state["sections"].append(
                    {
                        "title": section.title,
                        "entry_type": section.entry_type,
                        "entries": [
                            entry
                            if isinstance(entry, str)
                            else entry.model_dump(mode="json", exclude_none=True)
                            for entry in entries
                        ],
                    }
                )

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
        "employment_type": job.get("employment_type"),
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
) -> AsyncGenerator[tuple[Mapping[str, Any], JobScore | Exception]]:
    """Score every job, yielding results as they complete. A failed request yields its exception."""
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    semaphore = asyncio.Semaphore(concurrency)
    location_known = bool(str(candidate.get("location") or "").strip())
    questions = {
        key: value
        for key, value in QUESTIONS.items()
        if key != "location" or location_known
    }

    async def one(
        job: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], JobScore | Exception]:
        try:
            async with semaphore:
                response = await client.system_one(
                    state={"candidate": candidate, "job": job_state(job)},
                    questions=questions,
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
                location=nouls["location"].noul if location_known else None,
                seniority=choices["seniority"].choice,
                input_tokens=response.usage.input_tokens or 0,
            )
        except Exception as e:  # noqa: BLE001 - one failed job (after the SDK's retries) mustn't stop the run
            log.warning("Jev request for job %s failed: %r", job["id"], e)
            return job, e

    tasks = [asyncio.create_task(one(job)) for job in jobs]
    try:
        for next_done in asyncio.as_completed(tasks):
            yield await next_done
    finally:
        # A cancelled worker or closed generator must not leave paid requests running.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
