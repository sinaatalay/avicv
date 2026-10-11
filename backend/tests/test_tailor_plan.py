import copy
import json

import pytest

from backend.resumes import tailor_plan as plan_module

CV = """cv:
  name: Candidate Name
  email: private@example.com
  headline: Mechanical Engineer
  sections:
    Summary:
      - Mechanical engineer working on thermal systems
    Experience:
      - company: Real Company
        position: Mechanical Engineer
        start_date: 2022-01
        end_date: present
        location: Toronto
        highlights:
          - Reduced measured heat loss by 12 percent
          - Built a MATLAB thermal model
      - company: Earlier Company
        position: Intern
        start_date: 2021-01
        end_date: 2021-09
        highlights:
          - Prepared CAD drawings
    Education:
      - BSc in Mechanical Engineering, Real University, 2021
      - Engineering Diploma, Earlier College, 2018
    Skills:
      - label: Tools
        details: MATLAB, Python (NumPy, Pandas), SolidWorks | SQL
      - label: Methods
        details: Thermal analysis
    Projects:
      - name: Heat Exchanger
        summary: Built a thermal system
        highlights:
          - Designed a 42 kW exchanger
    Strengths:
      - bullet: Prepared technical reports
    Notes:
      - 'Project documentation is at https://example.com/project'
"""


def complete_plan(yaml: str = CV) -> plan_module.TailoringPlan:
    return plan_module.TailoringPlan(
        sections=[
            plan_module.SectionSelection(
                section_id=section["section_id"],
                entries=[
                    plan_module.EntrySelection(entry_id=entry["entry_id"])
                    for entry in section["entries"]
                ],
            )
            for section in plan_module.source_sections(
                plan_module.source_document(yaml)
            )
        ]
    )


def grounded(text: str, *evidence: str) -> plan_module.GroundedText:
    return plan_module.GroundedText(text=text, evidence_ids=list(evidence))


def test_rewrites_change_narrative_and_preserve_career_identity() -> None:
    plan = complete_plan()
    plan.headline = grounded(
        "Mechanical Engineer | Thermal Modeling", "headline", "s1:e0:h1"
    )
    plan.profile = grounded(
        "Mechanical engineer using MATLAB thermal models and reducing heat loss by 12%.",
        "headline",
        "s1:e0:h0",
        "s1:e0:h1",
    )
    plan.sections.pop(0)
    plan.sections[0].entries[0].summary = grounded(
        "Developed thermal models with MATLAB.", "s1:e0:h1"
    )
    plan.sections[0].entries[0].rewritten_highlights = [
        grounded("Modeled thermal performance in MATLAB.", "s1:e0:h1"),
        grounded("Cut measured heat loss by 12%.", "s1:e0:h0"),
    ]
    plan.sections[2].entries[0].detail_indices = [3, 1]
    plan.sections[3].title = grounded("Thermal Projects", "s4:e0")
    plan.sections[3].entries[0].summary = grounded(
        "Developed a 42 kW thermal exchanger.", "s4:e0:h0"
    )
    plan.sections[4].entries[0].rewrite = grounded(
        "Wrote technical reports.", "s5:e0:bullet"
    )
    plan.sections[5].entries[0].rewrite = grounded(
        "Documentation: https://example.com/project", "s6:e0"
    )

    original = plan_module.source_document(CV)["cv"]
    result = plan_module.source_document(plan_module.apply_plan(CV, plan))["cv"]
    assert result["name"] == original["name"]
    assert result["email"] == original["email"]
    assert next(iter(result["sections"])) == "Summary"
    assert result["sections"]["Summary"] == [plan.profile.text]
    for source, tailored in zip(
        original["sections"]["Experience"],
        result["sections"]["Experience"],
        strict=True,
    ):
        assert {
            key: value
            for key, value in source.items()
            if key not in {"summary", "highlights"}
        } == {
            key: value
            for key, value in tailored.items()
            if key not in {"summary", "highlights"}
        }
    assert result["sections"]["Experience"][0]["highlights"] == [
        "Modeled thermal performance in MATLAB.",
        "Cut measured heat loss by 12%.",
    ]
    assert result["sections"]["Education"] == original["sections"]["Education"]
    assert result["sections"]["Skills"][0]["details"] == "SQL, Python (NumPy, Pandas)"
    assert result["sections"]["Thermal Projects"][0]["name"] == "Heat Exchanger"
    assert result["sections"]["Strengths"] == [{"bullet": "Wrote technical reports."}]
    assert result["sections"]["Notes"] == ["Documentation: https://example.com/project"]

    claims = plan_module.rewrite_claims(CV, plan)
    assert {claim["id"] for claim in claims} == {
        "headline",
        "profile",
        "sections.0.entries.0.summary",
        "sections.0.entries.0.rewritten_highlights.0",
        "sections.0.entries.0.rewritten_highlights.1",
        "sections.2.entries.0.details",
        "sections.3.title",
        "sections.3.entries.0.summary",
        "sections.4.entries.0.rewrite",
        "sections.5.entries.0.rewrite",
    }
    facts = plan_module.source_facts(plan_module.source_document(CV))
    for claim in claims:
        assert claim["evidence"]
        assert all(item["text"] == facts[item["id"]] for item in claim["evidence"])
    assert "private@example.com" not in str(claims)
    assert "Candidate Name" not in str(claims)


@pytest.mark.parametrize(
    ("text", "evidence", "message"),
    [
        ("Reduced heat loss by 99%", ["s1:e0:h0"], "numbers"),
        ("Designed a 42 kW system", ["s1:e0:h0"], "numbers"),
        ("Prepared CAD drawings", ["s1:e1:h0"], "same source entry"),
        ("Designed a 42 kW exchanger", ["s4:e0:h0"], "same source entry"),
        ("Built a model", ["s1:e0:h999"], "Unknown source"),
        ("Built a model", ["s1:e0:h1", "s1:e0:h1"], "must not repeat"),
        ("Work: https://invented.example.com", ["s1:e0"], "URLs"),
        ("Work: www.invented.example.com", ["s1:e0"], "URLs"),
        ("[Work](/invented-project)", ["s1:e0"], "URLs"),
        ("Contact mailto:invented@example.com", ["s1:e0"], "URLs"),
        ("   ", ["s1:e0:h1"], "blank"),
    ],
)
def test_invalid_rewrite_evidence_is_rejected(
    text: str, evidence: list[str], message: str
) -> None:
    plan = complete_plan()
    plan.sections[1].entries[0].rewritten_highlights = [grounded(text, *evidence)]
    with pytest.raises(ValueError, match=message):
        plan_module.apply_plan(CV, plan)
    with pytest.raises(ValueError, match=message):
        plan_module.rewrite_claims(CV, plan)


@pytest.mark.parametrize(
    "title", ["EDUCATION", "Professional History", "Work-Experience"]
)
def test_plaintext_credentials_cannot_be_rewritten_or_removed(title: str) -> None:
    yaml = f"""cv:
  name: Candidate
  sections:
    {title}:
      - Original credential or employer, 2024
      - Earlier credential or employer, 2021
"""
    plan = complete_plan(yaml)
    plan.sections[0].entries[0].rewrite = grounded("Changed credential, 2024", "s0:e0")
    with pytest.raises(ValueError, match="unchanged"):
        plan_module.apply_plan(yaml, plan)
    plan = complete_plan(yaml)
    plan.sections[0].entries.pop()
    with pytest.raises(ValueError, match="every employment and education"):
        plan_module.apply_plan(yaml, plan)
    plan = complete_plan(yaml)
    plan.sections[0].entries.reverse()
    with pytest.raises(ValueError, match="original source order"):
        plan_module.apply_plan(yaml, plan)


def test_profile_replaces_summary_without_duplicate_sections() -> None:
    plan = complete_plan()
    plan.profile = grounded("Engineer with thermal modeling experience", "s1:e0:h1")
    with pytest.raises(ValueError, match="do not select both"):
        plan_module.apply_plan(CV, plan)
    plan.sections.pop(0)
    plan.sections[3].title = grounded("Professional Summary", "s4:e0")
    with pytest.raises(ValueError, match="another summary/profile"):
        plan_module.apply_plan(CV, plan)


def test_titles_must_use_their_own_section_evidence_and_remain_unique() -> None:
    plan = complete_plan()
    plan.sections[4].title = grounded("Projects", "s1:e0")
    with pytest.raises(ValueError, match="that source section"):
        plan_module.apply_plan(CV, plan)
    plan.sections[4].title = grounded("EXPERIENCE", "s4:e0")
    with pytest.raises(ValueError, match="unique"):
        plan_module.apply_plan(CV, plan)


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        (
            "Python (NumPy, Pandas), SQL; C++ | MATLAB",
            ["Python (NumPy, Pandas)", "SQL", "C++", "MATLAB"],
        ),
        ("Tools [A, B (C; D)] | E", ["Tools [A, B (C; D)]", "E"]),
        ("Clear technical writing", ["Clear technical writing"]),
    ],
)
def test_skill_splitting_preserves_bracketed_items(
    details: str, expected: list[str]
) -> None:
    assert plan_module.split_skill_items(details) == expected


@pytest.mark.parametrize("indices", [[4], [1, 1], []])
def test_skill_selection_cannot_add_or_duplicate_items(indices: list[int]) -> None:
    plan = complete_plan()
    plan.sections[3].entries[0].detail_indices = indices
    with pytest.raises(ValueError):
        plan_module.apply_plan(CV, plan)


def test_exact_selection_stays_backwards_compatible_and_does_not_mutate_source() -> (
    None
):
    plan = complete_plan()
    original = plan_module.source_document(CV)
    plan.sections[1].entries[0].highlights = [1, 0]
    before = copy.deepcopy(plan)
    result = plan_module.source_document(plan_module.apply_plan(CV, plan))
    assert result["cv"]["sections"]["Experience"][0]["highlights"] == list(
        reversed(original["cv"]["sections"]["Experience"][0]["highlights"])
    )
    assert plan == before
    assert plan_module.rewrite_claims(CV, plan) == []
    assert plan_module.source_document(CV) == original


def test_original_and_rewritten_highlights_are_mutually_exclusive() -> None:
    plan = complete_plan()
    entry = plan.sections[1].entries[0]
    entry.highlights = [1]
    entry.rewritten_highlights = [grounded("Modeled thermal systems", "s1:e0:h1")]
    with pytest.raises(ValueError, match="not both"):
        plan_module.apply_plan(CV, plan)


def test_numeric_formatting_and_existing_urls_are_allowed_from_precise_evidence() -> (
    None
):
    yaml = """cv:
  name: Candidate
  sections:
    Projects:
      - name: Test
        summary: 'Analyzed 1,000 measurements. Details at https://example.com/project'
"""
    plan = complete_plan(yaml)
    plan.sections[0].entries[0].summary = grounded(
        "Analyzed 1000 measurements; https://example.com/project", "s0:e0"
    )
    assert "1000 measurements" in plan_module.apply_plan(yaml, plan)


@pytest.mark.parametrize(
    "identity",
    [
        {"company": "Real Company", "position": "Engineer"},
        {"institution": "Real University", "area": "Engineering", "degree": "BSc"},
        {"name": "Thermal Project"},
    ],
)
def test_source_summary_can_be_rewritten_as_highlights(
    identity: dict[str, str],
) -> None:
    yaml = json.dumps(
        {
            "cv": {
                "name": "Candidate",
                "sections": {
                    "Background": [
                        {**identity, "summary": "Built a MATLAB thermal model"}
                    ]
                },
            }
        }
    )
    plan = complete_plan(yaml)
    plan.sections[0].entries[0].rewritten_highlights = [
        grounded("Modeled thermal performance in MATLAB.", "s0:e0:summary")
    ]
    result = plan_module.source_document(plan_module.apply_plan(yaml, plan))
    entry = result["cv"]["sections"]["Background"][0]
    assert entry["highlights"] == ["Modeled thermal performance in MATLAB."]
    assert all(entry[key] == value for key, value in identity.items())
    assert plan_module.rewrite_claims(yaml, plan)[0]["evidence"] == [
        {"id": "s0:e0:summary", "text": "Built a MATLAB thermal model"}
    ]


def test_verbatim_replacements_do_not_claim_substantive_changes() -> None:
    plan = complete_plan()
    plan.headline = grounded("Mechanical Engineer", "headline")
    plan.sections[0].entries[0].rewrite = grounded(
        "Mechanical engineer working on thermal systems", "s0:e0"
    )
    plan.sections[1].entries[0].rewritten_highlights = [
        grounded("Reduced measured heat loss by 12 percent", "s1:e0:h0"),
        grounded("Built a MATLAB thermal model", "s1:e0:h1"),
    ]
    plan.sections[4].title = grounded("Projects", "s4:e0")
    plan.sections[4].entries[0].summary = grounded(
        "Built a thermal system", "s4:e0:summary"
    )
    plan.sections[5].entries[0].rewrite = grounded(
        "Prepared technical reports", "s5:e0:bullet"
    )
    plan.sections[6].entries[0].rewrite = grounded(
        "Project documentation is at https://example.com/project", "s6:e0"
    )
    assert plan_module.rewrite_claims(CV, plan) == []
    assert plan_module.source_document(
        plan_module.apply_plan(CV, plan)
    ) == plan_module.source_document(CV)


def test_selected_skills_are_audited_against_shared_source_qualifiers() -> None:
    yaml = """cv:
  name: Candidate
  sections:
    Skills:
      - label: Languages
        details: Beginner exposure to Python, SQL, Rust
"""
    plan = complete_plan(yaml)
    plan.sections[0].entries[0].detail_indices = [1, 2]
    assert plan_module.rewrite_claims(yaml, plan) == [
        {
            "id": "sections.0.entries.0.details",
            "text": "SQL, Rust",
            "evidence": [
                {
                    "id": "s0:e0:details",
                    "text": "Beginner exposure to Python, SQL, Rust",
                }
            ],
            "context": {
                "section": "Skills",
                "entry_id": "s0:e0",
                "identity": {"label": "Languages"},
            },
        }
    ]
    # Keeping the complete source statement adds no changed claim.
    plan.sections[0].entries[0].detail_indices = [0, 1, 2]
    assert plan_module.rewrite_claims(yaml, plan) == []
