import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx2
import pytest
from conftest import AuthenticatedTestClient as TestClient
from pydantic_ai.messages import ModelMessage, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from typesafe_sdk import AsyncTypeSafeClient

from backend.config import Settings
from backend.main import app
from backend.matching import jev
from backend.resumes import parse, pipeline

CV_YAML = """\
cv:
  name: Jordan Lee
  headline: Backend Engineer
  location: Calgary, AB
  email: jordan.lee@email.com
  phone: {phone}
  social_networks:
    - network: GitHub
      username: jordanlee
  sections:
    Summary:
      - 'Backend engineer: payments and logistics platforms in Python and Go.'
    Experience:
      - company: Northwind Payments
        position: Senior Backend Engineer
        start_date: 2023-02
        end_date: present
        highlights:
          - 'Moved the ledger service to Go microservices on Kubernetes'
    Skills:
      - label: Languages
        details: 'Python, Go, SQL'
"""


def streaming(text: str) -> FunctionModel:
    """A fake model that always answers `text`, streamed in small chunks like Gemini's."""

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        for start in range(0, len(text), 40):
            yield text[start : start + 40]

    return FunctionModel(stream_function=stream)


@pytest.fixture
def gemini(monkeypatch: pytest.MonkeyPatch) -> list[list[ModelMessage]]:
    """Fake Gemini: first answers with a phone number RenderCV rejects, then with valid YAML."""
    calls: list[list[ModelMessage]] = []

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        calls.append(messages)
        phone = "(403) 555-0182" if len(calls) == 1 else "+1 403 555 0182"
        text = "```yaml\n" + CV_YAML.format(phone=phone) + "```"
        for start in range(0, len(text), 40):
            yield text[start : start + 40]

    model = FunctionModel(stream_function=stream)
    monkeypatch.setattr(parse, "openrouter_model", lambda settings: model)
    monkeypatch.setattr(pipeline, "PROGRESS_INTERVAL", 0)  # record every preview
    return calls


@pytest.fixture
def typesafe(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Fake TypeSafe API: backend jobs fit, everything else doesn't."""
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        good = (
            "Backend" in body["state"]["job"]["title"]
            or "Python Developer" in body["state"]["job"]["title"]
        )
        level = 3.6 if good else 0.4
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "fit": {
                        "type": "score",
                        "score": level,
                        "confidence": 0.9,
                        "legend": dict(enumerate(body["questions"]["fit"]["criteria"])),
                        "probabilities": {str(i): 0.2 for i in range(5)},
                    },
                    "interview": {"type": "noul", "noul": 0.9 if good else 0.1},
                    "must_haves": {"type": "noul", "noul": 0.7 if good else 0.05},
                    "seniority": {
                        "type": "choice",
                        "choice": "matches",
                        "confidence": 0.8,
                        "probabilities": {"below": 0.1, "matches": 0.8, "above": 0.1},
                    },
                    "location": {"type": "noul", "noul": 0.9},
                },
                "usage": {"input_tokens": 2000, "output_tokens": 60},
            },
        )

    def make_client(settings: Settings) -> AsyncTypeSafeClient:
        return AsyncTypeSafeClient(
            api_key="test",
            model=settings.jev_model,
            transport=httpx2.MockTransport(handler),
        )

    monkeypatch.setattr(jev, "make_client", make_client)
    return requests


def test_upload_parses_and_matches(
    settings: Settings,
    jobs_db: Path,
    gemini: list[list[ModelMessage]],
    typesafe: list[dict],
    make_pdf: Callable[..., bytes],
) -> None:
    with TestClient(app) as client:
        pdf = make_pdf(link="https://github.com/jordanlee")
        response = client.post(
            "/resumes", files={"file": ("cv.pdf", pdf, "application/pdf")}
        )
        assert response.status_code == 202
        resume_id = response.json()["id"]

        # The durable worker runs independently of the request lifecycle.
        resume = wait_for_resume(client, resume_id)
        assert resume["status"] == "done", resume["error"]
        assert resume["cv"]["name"] == "Jordan Lee"
        # What the page showed while Gemini was still writing
        assert resume["preview"] == {
            "name": "Jordan Lee",
            "sections": ["Summary", "Experience", "Skills"],
        }
        assert [s["title"] for s in resume["cv"]["sections"]] == [
            "Summary",
            "Experience",
            "Skills",
        ]
        assert (
            "+1 403 555 0182" in resume["rendercv_yaml"]
            or "+1-403-555-0182" in resume["rendercv_yaml"]
        )
        # Three open US/Canada roles: the two Stripe postings are one role, Monzo (UK) and the closed job are out
        assert resume["progress"] == {
            "done": 3,
            "total": 3,
            "succeeded": 3,
            "failed": 0,
            "attempted": 3,
            "pending": 0,
        }
        assert resume["cost_usd"] == pytest.approx(3 * 2000 * jev.PRICE_PER_MTOK / 1e6)

        matches = client.get(f"/resumes/{resume_id}/matches").json()
        assert [m["company"] for m in matches][:2] in (
            ["Stripe", "Affirm"],
            ["Affirm", "Stripe"],
        )
        assert matches[-1]["title"] == "Account Executive"
        stripe = next(m for m in matches if m["company"] == "Stripe")
        assert sorted(stripe["locations"]) == ["Remote in Canada", "Toronto, Canada"]

        yaml = client.get(f"/resumes/{resume_id}/rendercv.yaml")
        assert yaml.headers["content-type"].startswith("application/yaml")
        assert yaml.text.startswith("cv:")

    # RenderCV rejected the first answer, so Gemini was asked again with the error
    assert len(gemini) == 2
    prompt = next(
        part for part in gemini[0][0].parts if isinstance(part, UserPromptPart)
    ).content
    assert "https://github.com/jordanlee" in str(prompt[-1])
    # Jev never sees contact details
    assert all("email" not in json.dumps(r["state"]["candidate"]) for r in typesafe)
    assert all("jordan.lee@" not in json.dumps(r) for r in typesafe)


def test_unreadable_resume_fails_cleanly(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, make_pdf: Callable[..., bytes]
) -> None:
    model = streaming("I can't find a résumé here.")
    monkeypatch.setattr(parse, "openrouter_model", lambda settings: model)
    with TestClient(app) as client:
        resume_id = client.post(
            "/resumes", files={"file": ("x.pdf", make_pdf(), "application/pdf")}
        ).json()["id"]
        resume = wait_for_resume(client, resume_id)
    assert resume["status"] == "failed"
    assert resume["error"] == "We couldn't read a résumé from this PDF."


def test_matching_without_jobs_yet(
    settings: Settings, gemini: list, typesafe: list, make_pdf: Callable[..., bytes]
) -> None:
    with TestClient(app) as client:
        resume_id = client.post(
            "/resumes", files={"file": ("cv.pdf", make_pdf(), "application/pdf")}
        ).json()["id"]
        resume = wait_for_resume(client, resume_id, {"waiting_for_jobs"})
        assert resume["status"] == "waiting_for_jobs"
        assert resume["progress"]["total"] == 0
        assert client.get(f"/resumes/{resume_id}/matches").json() == []
    assert typesafe == []


@pytest.mark.parametrize(
    ("kind", "status"), [("not a pdf", 415), ("too long", 422), ("broken", 422)]
)
def test_rejects_bad_uploads(
    settings: Settings, make_pdf: Callable[..., bytes], kind: str, status: int
) -> None:
    content = {
        "not a pdf": b"hello",
        "too long": make_pdf(pages=11),
        "broken": b"%PDF-1.7 broken",
    }[kind]
    with TestClient(app) as client:
        response = client.post(
            "/resumes", files={"file": ("cv.pdf", content, "application/pdf")}
        )
    assert response.status_code == status


def test_unknown_resume(settings: Settings) -> None:
    with TestClient(app) as client:
        assert client.get("/resumes/nope").status_code == 404
        assert client.get("/resumes/nope/matches").status_code == 404


def wait_for_resume(
    client: TestClient, resume_id: str, statuses: set[str] | None = None
) -> dict:
    statuses = statuses or {"done", "partial", "failed"}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/resumes/{resume_id}")
        assert response.status_code == 200
        data = response.json()
        if data["status"] in statuses:
            return data
        time.sleep(0.02)
    pytest.fail(f"Worker did not finish: {data}")
