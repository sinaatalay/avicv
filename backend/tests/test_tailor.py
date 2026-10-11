import asyncio
import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from backend.config import Settings
from backend.resumes import tailor

CV = """cv:
  name: Candidate Name
  email: private@example.com
  headline: Mechanical Engineer
  sections:
    Experience:
      - company: Real Company
        position: Mechanical Engineer
        start_date: 2022-01
        end_date: present
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
      - institution: Real University
        area: Mechanical Engineering
        degree: BSc
        end_date: 2021
    Projects:
      - name: Heat Exchanger
        highlights:
          - Designed a 42 kW exchanger
      - name: Volunteer Website
        summary: Built a small website
    Skills:
      - label: Tools
        details: MATLAB, SolidWorks, Python
"""


def complete_plan(yaml: str = CV) -> tailor.TailoringPlan:
    return tailor.TailoringPlan(
        sections=[
            tailor.SectionSelection(
                section_id=section["section_id"],
                entries=[
                    tailor.EntrySelection(entry_id=entry["entry_id"])
                    for entry in section["entries"]
                ],
            )
            for section in tailor.source_sections(tailor.source_document(yaml))
        ]
    )


def test_tailoring_can_only_reorder_or_select_existing_facts() -> None:
    plan = complete_plan()
    plan.sections[0].entries[0].highlights = [1, 0]
    plan.sections[2].entries = plan.sections[2].entries[:1]
    result = tailor.source_document(tailor.apply_plan(CV, plan))
    original = tailor.source_document(CV)
    assert result["cv"]["name"] == original["cv"]["name"]
    assert result["cv"]["email"] == original["cv"]["email"]
    for section in ["Experience", "Education"]:
        for before, after in zip(
            original["cv"]["sections"][section],
            result["cv"]["sections"][section],
            strict=True,
        ):
            assert {k: v for k, v in before.items() if k != "highlights"} == {
                k: v for k, v in after.items() if k != "highlights"
            }
    assert result["cv"]["sections"]["Experience"][0]["highlights"] == [
        "Built a MATLAB thermal model",
        "Reduced measured heat loss by 12 percent",
    ]
    assert [entry["name"] for entry in result["cv"]["sections"]["Projects"]] == [
        "Heat Exchanger"
    ]
    assert result["cv"]["sections"]["Skills"] == original["cv"]["sections"]["Skills"]


@pytest.mark.parametrize(
    "case",
    [
        "unknown_entry",
        "cross_section",
        "duplicate",
        "bad_highlight",
        "missing_education",
        "missing_employer",
        "chronology",
    ],
)
def test_invalid_or_fact_removing_plans_are_rejected(case: str) -> None:
    plan = complete_plan()
    if case == "unknown_entry":
        plan.sections[0].entries[0].entry_id = "invented"
    elif case == "cross_section":
        plan.sections[0].entries[0].entry_id = "s1:e0"
    elif case == "duplicate":
        plan.sections[0].entries.append(plan.sections[0].entries[0])
    elif case == "bad_highlight":
        plan.sections[0].entries[0].highlights = [999]
    elif case == "missing_education":
        del plan.sections[1]
    elif case == "missing_employer":
        plan.sections[0].entries.pop()
    else:
        plan.sections[0].entries.reverse()
    with pytest.raises(ValueError):
        tailor.apply_plan(CV, plan)


def test_model_cannot_add_freeform_claims() -> None:
    data = complete_plan().model_dump()
    data["summary"] = "Invented Stanford PhD with 20 years of experience"
    with pytest.raises(ValidationError):
        tailor.TailoringPlan.model_validate(data)
    data = complete_plan().model_dump()
    data["sections"][0]["entries"][0]["new_highlights"] = [
        "Increased revenue by 500 percent"
    ]
    with pytest.raises(ValidationError):
        tailor.TailoringPlan.model_validate(data)


def test_model_validation_retries_invalid_plan_without_exposing_contact_fields(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = []

    def respond(messages, info):
        requests.append(messages)
        plan = complete_plan().model_dump()
        if len(requests) == 1:
            plan["sections"][0]["entries"].reverse()
        return ModelResponse(parts=[TextPart(json.dumps(plan))])

    monkeypatch.setattr(
        tailor, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    result = asyncio.run(
        tailor.tailor_resume(
            CV,
            {"title": "Thermal Engineer", "description_text": "Thermal modeling"},
            settings,
        )
    )
    assert len(requests) == 2
    assert result.yaml and result.plan.sections[0].entries[0].entry_id == "s0:e0"
    assert "private@example.com" not in str(requests)
    assert "Candidate Name" not in str(requests)
    assert "private@example.com" in result.yaml
    assert json.loads(result.plan.model_dump_json())["sections"]


def test_text_output_still_rejects_fabricated_fields_and_accepts_json_fences() -> None:
    from pydantic_ai import ModelRetry

    valid = complete_plan().model_dump_json()
    assert tailor.read_plan("```json\n" + valid + "\n```") == complete_plan()
    malformed = complete_plan().model_dump()
    malformed["invented_degree"] = "PhD"
    with pytest.raises(ModelRetry):
        tailor.read_plan(json.dumps(malformed))


def test_gemini_wire_protocol_uses_validated_text_without_tool_schema(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert (
            "tools" not in body
            and "tool_choice" not in body
            and "response_format" not in body
        )
        assert "selection_schema" in str(body["messages"])
        assert "private@example.com" not in str(body["messages"])
        plan = complete_plan().model_dump()
        if len(requests) == 1:
            plan["sections"][0]["entries"].reverse()
        return httpx.Response(
            200,
            json={
                "id": "local-test",
                "object": "chat.completion",
                "created": 0,
                "model": settings.tailor_model,
                "provider": "Google AI Studio",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(plan)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                },
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenRouterModel(
                settings.tailor_model,
                provider=OpenRouterProvider(api_key="test", http_client=client),
            )
            monkeypatch.setattr(tailor, "openrouter_model", lambda settings: model)
            return await tailor.tailor_resume(
                CV,
                {"title": "Thermal Engineer", "description_text": "Thermal modeling"},
                settings,
            )

    result = asyncio.run(run())
    assert len(requests) == 2
    assert result.plan.sections[0].entries[0].entry_id == "s0:e0"
    assert "private@example.com" in result.yaml
