"""The drafting model cannot approve its own unsupported CV claims."""

import asyncio
import json

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from backend.config import Settings
from backend.resumes import tailor

CV = """cv:
  name: Private Candidate
  email: private@example.com
  sections:
    Experience:
      - company: Actual Employer
        position: Engineer
        start_date: 2022-01
        end_date: present
        highlights:
          - Helped build a Python prototype for document search.
          - Evaluated retrieval accuracy with the research team.
    Education:
      - institution: Actual University
        degree: BSc
        area: Computer Science
        end_date: 2021
"""
JOB = {
    "title": "Platform Lead",
    "description_text": "JOB_ONLY_MARKER Kubernetes leadership",
}


def plan(
    text: str = "Engineer with experience contributing to a Python document-search prototype.",
):
    return tailor.TailoringPlan(
        profile=tailor.GroundedText(text=text, evidence_ids=["s0:e0:h0"]),
        sections=[
            tailor.SectionSelection(
                section_id="s0", entries=[tailor.EntrySelection(entry_id="s0:e0")]
            ),
            tailor.SectionSelection(
                section_id="s1", entries=[tailor.EntrySelection(entry_id="s1:e0")]
            ),
        ],
    )


def prompt_data(messages):
    for message in reversed(messages):
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return json.loads(part.content)
    raise AssertionError("Missing user JSON")


def response(data, cost=0.1):
    return ModelResponse(
        parts=[TextPart(json.dumps(data))], provider_details={"cost": cost}
    )


def verdict(claim_id="profile", supported=True):
    return {
        "claim_id": claim_id,
        "supported": supported,
        "reason": "Prototype contribution is supported."
        if supported
        else "Source says helped build a prototype, not led a production Kubernetes platform.",
    }


def test_failed_factual_review_requires_corrected_draft_and_counts_all_calls(
    settings: Settings, monkeypatch
):
    calls = []
    drafts = []

    def respond(messages, info):
        data = prompt_data(messages)
        calls.append(data)
        if "review_schema" in data:
            # The reviewer gets source evidence, never the employer's wish list or contact fields.
            serialized = json.dumps(data)
            assert "JOB_ONLY_MARKER" not in serialized
            assert "private@example.com" not in serialized
            assert "Private Candidate" not in serialized
            assert "Helped build a Python prototype" in serialized
            return response({"claims": [verdict(supported=len(drafts) > 1)]}, cost=0.2)
        if drafts:
            assert data["rejected_claims"][0]["supported"] is False
            assert "not led" in data["rejected_claims"][0]["reason"]
            output = plan()
        else:
            output = plan("Led a production Kubernetes document-search platform.")
        drafts.append(output)
        return response(output.model_dump(), cost=0.1)

    monkeypatch.setattr(
        tailor, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    result = asyncio.run(tailor.tailor_resume(CV, JOB, settings))
    assert len(calls) == 4
    assert "Kubernetes" not in result.yaml
    assert "contributing to a Python" in result.yaml
    assert result.cost_usd == pytest.approx(0.6)
    original = tailor.source_document(CV)["cv"]
    output = tailor.source_document(result.yaml)["cv"]
    assert output["sections"]["Experience"] == original["sections"]["Experience"]
    assert output["sections"]["Education"] == original["sections"]["Education"]
    assert output["name"] == original["name"]
    assert output["email"] == original["email"]


def test_persistently_unsupported_drafts_fail_closed_with_bounded_attempts(
    settings: Settings, monkeypatch
):
    drafts = reviews = 0

    def respond(messages, info):
        nonlocal drafts, reviews
        if "review_schema" in prompt_data(messages):
            reviews += 1
            return response({"claims": [verdict(supported=False)]})
        drafts += 1
        return response(plan("Led a production Kubernetes platform.").model_dump())

    monkeypatch.setattr(
        tailor, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    with pytest.raises(ValueError, match="could not pass its factual review"):
        asyncio.run(tailor.tailor_resume(CV, JOB, settings))
    assert drafts == reviews == tailor.MAX_DRAFT_ATTEMPTS


@pytest.mark.parametrize(
    "invalid_claims", [[verdict(), verdict()], [verdict("invented")]]
)
def test_review_must_cover_exactly_the_submitted_claims(
    settings: Settings, monkeypatch, invalid_claims
):
    reviews = 0

    def respond(messages, info):
        nonlocal reviews
        if "review_schema" in prompt_data(messages):
            reviews += 1
            return response({"claims": invalid_claims if reviews == 1 else [verdict()]})
        return response(plan().model_dump())

    monkeypatch.setattr(
        tailor, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    result = asyncio.run(tailor.tailor_resume(CV, JOB, settings))
    assert reviews == 2
    assert result.yaml


def test_selection_only_generation_retries_then_fails_instead_of_returning_unchanged_cv(
    settings: Settings, monkeypatch
):
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        output = plan()
        output.profile = None
        return response(output.model_dump())

    monkeypatch.setattr(
        tailor, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    with pytest.raises(UnexpectedModelBehavior):
        asyncio.run(tailor.tailor_resume(CV, JOB, settings))
    assert calls == 3


def test_reviewer_outage_does_not_return_an_unreviewed_cv(
    settings: Settings, monkeypatch
):
    def respond(messages, info):
        if "review_schema" in prompt_data(messages):
            raise ConnectionError("reviewer unavailable")
        return response(plan().model_dump())

    monkeypatch.setattr(
        tailor, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    with pytest.raises(ConnectionError, match="reviewer unavailable"):
        asyncio.run(tailor.tailor_resume(CV, JOB, settings))
