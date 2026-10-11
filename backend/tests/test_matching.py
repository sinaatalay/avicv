from datetime import date
from pathlib import Path

from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model

from backend.matching.jev import candidate_state, composite, job_state
from backend.matching.prefilter import search_terms, shortlist

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
    assert state["education"] == [
        "BSc Computer Science at University of Calgary (2019-04)"
    ]
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
