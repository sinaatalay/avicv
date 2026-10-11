import asyncio
import json
from functools import partial

import httpx2
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from backend.config import Settings
from backend.resumes import parse


def test_provider_configuration_defaults_and_rejects_invalid_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_PROVIDER", raising=False)
    assert Settings(_env_file=None).openrouter_provider == "google-vertex"
    monkeypatch.setenv("OPENROUTER_PROVIDER", "google-ai-studio")
    assert Settings(_env_file=None).openrouter_provider == "google-ai-studio"
    for invalid in ("", " ", "Google Vertex", "google-vertex,google-ai-studio", "/"):
        monkeypatch.setenv("OPENROUTER_PROVIDER", invalid)
        with pytest.raises(ValidationError, match="openrouter_provider"):
            Settings(_env_file=None)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider_slug", ["google-vertex", "google-ai-studio"])
def test_shared_model_sends_provider_restriction_with_run_settings(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
    provider_slug: str,
) -> None:
    """Exercise SDK serialization, including reuse after a streamed request."""
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        common = {
            "id": "mock-request",
            "created": 0,
            "model": settings.parse_model,
            "provider": "Google Vertex",
        }
        if body.get("stream"):
            chunks = [
                {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "OK"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            content = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=content + "data: [DONE]\n\n",
            )
        return httpx2.Response(
            200,
            json={
                **common,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async def run() -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond)
        ) as client:
            monkeypatch.setattr(
                parse,
                "OpenRouterProvider",
                partial(OpenRouterProvider, http_client=client),
            )
            configured = settings.model_copy(
                update={"openrouter_provider": provider_slug}
            )
            agent = Agent(parse.openrouter_model(configured))
            # Per-run settings are used by parsing, design, and tailoring. They
            # must augment the model defaults without dropping provider routing.
            for tokens in (6000, 8000):
                run_settings = OpenRouterModelSettings(
                    temperature=0,
                    max_tokens=tokens,
                    openrouter_reasoning={"effort": "low"},
                )
                if stream:
                    async with agent.run_stream(
                        "Reply OK", model_settings=run_settings
                    ) as result:
                        assert await result.get_output() == "OK"
                else:
                    result = await agent.run("Reply OK", model_settings=run_settings)
                    assert result.output == "OK"

    asyncio.run(run())
    assert len(requests) == 2
    for body, tokens in zip(requests, (6000, 8000), strict=True):
        assert body["provider"] == {
            "only": [provider_slug],
            "allow_fallbacks": False,
        }
        assert body["reasoning"] == {"effort": "low"}
        assert body["temperature"] == 0
        assert body["max_tokens"] == tokens
        assert bool(body.get("stream")) == stream
