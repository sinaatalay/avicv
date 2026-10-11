import asyncio
import json

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from backend.config import Settings
from backend.resumes import design
from backend.resumes.render import RenderedResume, RenderError

CV = "cv:\n  name: Test Candidate\n  sections:\n    Summary:\n      - Original professional content\n"


def test_design_renders_and_visually_compares_every_attempt_keeps_best(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, design_max_iterations=3)
    plans = [
        {
            "design": {"theme": "classic", "header": {"alignment": "left"}},
            "similarity": 0,
            "usable": False,
        },
        {
            "design": {"theme": "classic", "header": {"alignment": "center"}},
            "similarity": 0.8,
            "usable": True,
        },
        {
            "design": {"theme": "classic", "header": {"alignment": "right"}},
            "similarity": 0.9,
            "usable": True,
        },
        {"design": None, "similarity": 0.7, "usable": True},
    ]
    calls = []

    def respond(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart(json.dumps(plans[len(calls) - 1]))])

    monkeypatch.setattr(
        design, "openrouter_model", lambda settings: FunctionModel(respond)
    )

    async def preview(*args, **kwargs):
        return RenderedResume(b"original", (b"original1", b"original2"), 2)

    rendered = []

    async def render(cv_yaml, proposed, **kwargs):
        rendered.append((cv_yaml, proposed))
        return RenderedResume(b"draft", (b"draft1", b"draft2"), 2)

    monkeypatch.setattr(design, "preview_pdf", preview)
    monkeypatch.setattr(design, "render_resume", render)
    result = asyncio.run(design.reconstruct_design(b"original", CV, settings))
    assert len(rendered) == result.iterations == 3
    assert all(cv_yaml == CV for cv_yaml, _ in rendered)
    assert result.design["header"]["alignment"] == "center"
    assert result.score == 0.9
    assert len(calls) == 4
    for messages in calls[1:]:
        prompt = next(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        images = [part.data for part in prompt if isinstance(part, BinaryContent)]
        assert images == [b"original1", b"original2", b"draft1", b"draft2"]


def test_no_unreviewed_or_clipped_design_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(messages, info):
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "design": {"theme": "classic"},
                            "similarity": 0.99,
                            "usable": False,
                        }
                    )
                )
            ]
        )

    monkeypatch.setattr(
        design, "openrouter_model", lambda settings: FunctionModel(respond)
    )

    async def preview(*args, **kwargs):
        return RenderedResume(b"original", (b"original",), 1)

    async def render(*args, **kwargs):
        return RenderedResume(b"draft", (b"draft",), 1)

    monkeypatch.setattr(design, "preview_pdf", preview)
    monkeypatch.setattr(design, "render_resume", render)
    with pytest.raises(RenderError, match="readable"):
        asyncio.run(
            design.reconstruct_design(
                b"original", CV, Settings(_env_file=None, design_max_iterations=2)
            )
        )


@pytest.mark.parametrize("failure", ["timeout", "outage", "cancel"])
def test_later_refinement_failure_keeps_reviewed_design_except_cancellation(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    count = 0

    async def respond(messages, info):
        nonlocal count
        count += 1
        if count == 3:
            if failure == "timeout":
                await asyncio.sleep(1)
            elif failure == "cancel":
                raise asyncio.CancelledError
            else:
                raise RuntimeError("provider unavailable")
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "design": {"theme": "classic"},
                            "similarity": 0.82 if count > 1 else 0,
                            "usable": count > 1,
                        }
                    )
                )
            ]
        )

    monkeypatch.setattr(
        design, "openrouter_model", lambda settings: FunctionModel(respond)
    )

    async def preview(*args, **kwargs):
        return RenderedResume(b"original", (b"original",), 1)

    async def render(*args, **kwargs):
        return RenderedResume(b"draft", (b"draft",), 1)

    monkeypatch.setattr(design, "preview_pdf", preview)
    monkeypatch.setattr(design, "render_resume", render)
    settings = Settings(_env_file=None).model_copy(
        update={"design_timeout_seconds": 0.1}
    )
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(design.reconstruct_design(b"original", CV, settings))
    else:
        result = asyncio.run(design.reconstruct_design(b"original", CV, settings))
        assert result.score == 0.82
        assert result.design["theme"] == "classic"
    assert count == 3
