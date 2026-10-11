import asyncio
import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model

from backend.matching.jev import (
    JobScore,
    candidate_state,
    composite,
    job_state,
    score_jobs,
)
from backend.matching.prefilter import (
    search_terms,
    shortlist,
    shortlist_with_diagnostics,
)

CV = """\
cv:
  name: Jordan Lee
  headline: Senior Backend Engineer
  location: Calgary, AB
  email: jordan.lee@email.com
  phone: +1 403 555 0182
  sections:
    Summary:
      - Backend engineer.
    Experience:
      - company: Northwind Payments
        position: Senior Backend Engineer
        start_date: 2023-02
        end_date: present
      - company: Side Project Co
        position: Consultant
        start_date: 2023-06
        end_date: 2024-06
      - company: Prairie Logistics
        position: Software Engineer
        start_date: 2020-06
        end_date: 2023-01
    Education:
      - institution: University of Calgary
        area: Computer Science
        degree: BSc
        end_date: 2019-04
    Skills:
      - label: Languages
        details: Python, Go, C++
"""


def cv():
    return build_rendercv_dictionary_and_model(CV)[1].cv


def test_candidate_state_computes_years_and_drops_contact_details() -> None:
    state = candidate_state(cv(), today=date(2026, 10, 11))
    assert (
        state["total_years_of_professional_experience"] == 6.2
    )  # 31 + 44 months; the 2023 consulting overlaps
    assert [role["years"] for role in state["experience"]] == [3.7, 1.0, 2.6]
    assert state["skills"] == {"Languages": "Python, Go, C++"}
    assert state["education"][0]["area"] == "Computer Science"
    assert state["education"][0]["degree"] == "BSc"
    assert state["education"][0]["date"] == "2019-04"
    assert not {"name", "email", "phone"} & set(state)


def test_job_state_drops_boilerplate_and_caps_length() -> None:
    description = (
        "Build payment APIs.\n\nBenefits: free lunch.\n\nWe are an equal opportunity employer.\n\n"
        + "x " * 5000
    )
    state = job_state(
        {
            "title": "Backend Engineer",
            "description_text": description,
            "locations": ["Remote"],
        }
    )
    assert (
        "Benefits" not in state["description"]
        and "equal opportunity" not in state["description"]
    )
    assert state["description"].startswith("Build payment APIs.")
    assert len(state["description"]) <= 6004


def test_composite_weights() -> None:
    assert composite(fit=4, interview=1, must_haves=1) == 1
    assert composite(fit=0, interview=0, must_haves=0) == 0


def test_search_terms_skip_stopwords() -> None:
    terms = search_terms(candidate_state(cv()))
    assert {"backend", "engineer", "python", "go", "c++", "consultant"} <= set(terms)
    assert "senior" not in terms


def test_shortlist_groups_postings_of_one_role(jobs_db: Path) -> None:
    roles = shortlist(jobs_db, candidate_state(cv()), limit=10)
    by_company = {role["company"]: role for role in roles}
    assert set(by_company) == {
        "Stripe",
        "Affirm",
        "Ramp",
    }  # Monzo is in the UK; Wealthsimple's job closed
    assert by_company["Stripe"]["locations"] == ["Toronto, Canada", "Remote in Canada"]
    assert by_company["Stripe"]["posting_ids"] == [1, 2]


def test_shortlist_before_first_sync(tmp_path: Path) -> None:
    assert shortlist(tmp_path / "missing.db", candidate_state(cv()), limit=10) == []


@pytest.mark.parametrize(
    "entries",
    [
        "      - bullet: Python and SQL\n",
        "      - Python and SQL\n",
        "      - label: Tools\n        details: Python and SQL\n",
        "      - number: Python and SQL\n",
        "      - name: Python\n        highlights:\n          - SQL databases\n",
    ],
)
def test_all_skill_entry_types_contribute_to_retrieval(entries: str) -> None:
    model = build_rendercv_dictionary_and_model(
        "cv:\n  name: Example\n  sections:\n    Skills:\n" + entries
    )[1]
    state = candidate_state(model.cv)
    assert isinstance(state.get("skills", {}), dict)
    assert {"python", "sql"} <= set(search_terms(state))
    assert state["location"] == "United States"
    assert state["location_source"] == "default"


def test_custom_sections_cannot_overwrite_profile_fields() -> None:
    model = build_rendercv_dictionary_and_model("""cv:
  name: Example
  location: Toronto, ON
  sections:
    location:
      - bullet: Computational mechanics
    education:
      - name: Independent Research
        highlights:
          - Superconducting magnets
    experience:
      - label: Simulation
        details: Finite elements
""")[1]
    state = candidate_state(model.cv)
    assert state["location"] == "Toronto, ON"
    assert state["location_source"] == "resume"
    assert {"computational", "superconducting", "finite"} <= set(search_terms(state))


def test_retrieval_preserves_education_research_and_project_evidence() -> None:
    model = build_rendercv_dictionary_and_model("""cv:
  name: Example
  sections:
    Education:
      - institution: Example University
        area: Mechanical and Aerospace Engineering
        degree: PhD
        highlights:
          - Superconducting simulation
    Projects:
      - name: '[Package](https://secret.example.invalid/ignoredword)'
        highlights:
          - Built Python APIs with PostgreSQL
    Publications:
      - title: Magnetothermal analysis
        authors:
          - Example Author
""")[1]
    state = candidate_state(model.cv)
    assert "PostgreSQL" in json.dumps(state["sections"])
    terms = set(search_terms(state))
    assert {
        "mechanical",
        "aerospace",
        "superconducting",
        "postgresql",
        "magnetothermal",
    } <= terms
    assert "ignoredword" not in terms
    assert "author" not in terms


def test_duplicate_postings_do_not_consume_the_role_budget(jobs_db: Path) -> None:
    conn = sqlite3.connect(jobs_db)
    columns = [
        row[1]
        for row in conn.execute("PRAGMA table_info(jobs)")
        if row[1] not in {"id", "source_id", "locations_text"}
    ]
    for i in range(50):
        conn.execute(
            f"INSERT INTO jobs (source_id, locations_text, {', '.join(columns)}) "
            f"SELECT ?, ?, {', '.join(columns)} FROM jobs WHERE id=1",
            (f"duplicate-{i}", f"City {i}, Canada"),
        )
    conn.commit()
    conn.close()
    roles, stats = shortlist_with_diagnostics(jobs_db, candidate_state(cv()), 3)
    assert len(roles) == 3
    assert stats["catalog_total"] == 55
    assert stats["eligible_total"] == 54
    assert stats["distinct_roles"] == 3
    stripe = next(role for role in roles if role["company"] == "Stripe")
    assert len(stripe["posting_ids"]) == 52
    assert len(stripe["postings"]) == 52
    assert stripe["postings"][-1]["source_id"] == "duplicate-49"
    assert "City 49, Canada" in stripe["locations"]
    assert stripe["provider"] == "greenhouse"
    assert stripe["source_id"]
    assert stripe["salary_currency"] == "CAD"


def test_same_title_with_different_work_is_not_merged(jobs_db: Path) -> None:
    conn = sqlite3.connect(jobs_db)
    conn.execute(
        "UPDATE jobs SET description_text='Python fraud investigation' WHERE id=2"
    )
    # Fixture's FTS has only an INSERT trigger; the shared title still matches.
    conn.commit()
    conn.close()
    roles, stats = shortlist_with_diagnostics(jobs_db, candidate_state(cv()), 10)
    assert len([role for role in roles if role["company"] == "Stripe"]) == 2
    assert stats["distinct_roles"] == 4


@pytest.mark.parametrize(
    ("field", "value"), [("workplace_type", "onsite"), ("employment_type", "contract")]
)
def test_different_work_arrangements_are_not_merged(
    jobs_db: Path, field: str, value: str
) -> None:
    conn = sqlite3.connect(jobs_db)
    conn.execute(f"UPDATE jobs SET {field}=? WHERE id=2", (value,))
    conn.commit()
    conn.close()
    roles = shortlist(jobs_db, candidate_state(cv()), 10)
    assert len([role for role in roles if role["company"] == "Stripe"]) == 2


def test_catalog_being_initialized_returns_empty_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    sqlite3.connect(path).close()
    roles, stats = shortlist_with_diagnostics(path, candidate_state(cv()), 10)
    assert roles == []
    assert stats["eligible_total"] == 0


def response():
    return SimpleNamespace(
        scores={"fit": SimpleNamespace(score=3.0)},
        nouls={
            name: SimpleNamespace(noul=0.8)
            for name in ("interview", "must_haves", "location")
        },
        choices={"seniority": SimpleNamespace(choice="matches")},
        usage=SimpleNamespace(input_tokens=100),
    )


def test_unknown_location_is_not_scored_as_a_negative() -> None:
    class Client:
        async def system_one(self, **kwargs):
            assert "location" not in kwargs["questions"]
            return response()

    async def run():
        outcomes = [
            outcome
            async for _, outcome in score_jobs(
                Client(),
                {},
                [{"id": 1, "content_hash": "x", "title": "Engineer"}],
                concurrency=1,
            )
        ]
        assert isinstance(outcomes[0], JobScore)
        assert outcomes[0].location is None

    asyncio.run(run())


def test_closing_scorer_cancels_all_inflight_requests() -> None:
    async def run():
        waiting = asyncio.Event()
        active: set[str] = set()

        class Client:
            async def system_one(self, **kwargs):
                title = kwargs["state"]["job"]["title"]
                if title == "fast":
                    await waiting.wait()
                    return response()
                active.add(title)
                waiting.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    active.remove(title)

        jobs = [
            {"id": i, "content_hash": str(i), "title": title}
            for i, title in enumerate(("fast", "slow1", "slow2"))
        ]
        stream = score_jobs(
            Client(), {"location": "United States"}, jobs, concurrency=3
        )
        await anext(stream)
        assert active
        await stream.aclose()
        assert not active

    asyncio.run(run())


def test_invalid_model_scores_are_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid interview"):
        JobScore(1, "hash", 0.8, 3, float("nan"), 0.8, 0.8, "matches", 100)
