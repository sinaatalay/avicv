"""Check actual BYOK usage with a tiny prompt, using the app's model configuration."""

import asyncio
import json
import sys

from openai import APIConnectionError
from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UserError
from pydantic_ai.models.openrouter import OpenRouterModelSettings

from backend.config import Settings
from backend.resumes.parse import openrouter_model


async def check(settings: Settings) -> bool:
    """Make one small request per distinct configured model; never send CV data."""
    verified = True
    for model in dict.fromkeys(
        [settings.parse_model, settings.design_model, settings.tailor_model]
    ):
        try:
            result = await Agent(
                openrouter_model(settings.model_copy(update={"parse_model": model})),
                retries=0,
            ).run(
                "Reply with OK.",
                model_settings=OpenRouterModelSettings(
                    max_tokens=256,
                    openrouter_reasoning={"effort": "minimal"},
                ),
            )
        except ModelHTTPError as error:
            # Provider error bodies can contain credential/project details.
            print(json.dumps({"model": model, "http_status": error.status_code}))
            verified = False
            continue
        response = result.response
        details = response.provider_details or {}
        is_byok = details.get("is_byok")
        print(
            json.dumps(
                {
                    "model": model,
                    "provider": details.get("downstream_provider"),
                    "is_byok": is_byok,
                    "generation_id": response.provider_response_id,
                }
            )
        )
        # A provider name or zero reported cost is not evidence of BYOK.
        verified = verified and is_byok is True
    return verified


def main() -> None:
    settings = Settings()
    if settings.openrouter_api_key is None:
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        raise SystemExit(1)
    try:
        verified = asyncio.run(check(settings))
    except (AgentRunError, UserError, APIConnectionError) as error:
        print(f"OpenRouter check failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
    if not verified:
        print(
            "BYOK was not verified. Check that the app's OpenRouter API key belongs "
            "to the workspace containing the provider credential, and is allowed "
            "by its API key and model filters. Disable shared capacity fallback "
            "there to prevent OpenRouter credit billing. Inspect the generation "
            "in OpenRouter Activity for provider errors.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
