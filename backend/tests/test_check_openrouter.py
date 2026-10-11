import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from backend import check_openrouter
from backend.config import Settings


@pytest.mark.parametrize("is_byok", [True, False, None])
def test_check_requires_explicit_byok_and_checks_duplicate_models_once(
    monkeypatch, capsys, is_byok
):
    calls = []

    def respond(messages, info):
        calls.append(messages)
        return ModelResponse(
            parts=[TextPart("OK")],
            provider_details={"downstream_provider": "Google", "is_byok": is_byok},
            provider_response_id="gen-test",
        )

    monkeypatch.setattr(
        check_openrouter, "openrouter_model", lambda settings: FunctionModel(respond)
    )
    settings = Settings(_env_file=None)
    assert asyncio.run(check_openrouter.check(settings)) is (is_byok is True)
    assert len(calls) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["is_byok"] is is_byok
    assert report["generation_id"] == "gen-test"


def test_check_requires_every_configured_model_to_use_byok(monkeypatch, capsys):
    models = []

    def factory(settings):
        models.append(settings.parse_model)
        return FunctionModel(
            lambda messages, info: ModelResponse(
                parts=[TextPart("OK")],
                provider_details={"is_byok": settings.parse_model != "test/design"},
            )
        )

    monkeypatch.setattr(check_openrouter, "openrouter_model", factory)
    settings = Settings(
        _env_file=None,
        parse_model="test/parse",
        design_model="test/design",
        tailor_model="test/tailor",
    )
    assert not asyncio.run(check_openrouter.check(settings))
    assert models == ["test/parse", "test/design", "test/tailor"]
    assert len(capsys.readouterr().out.splitlines()) == 3
