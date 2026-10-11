"""Real HTTP MCP transport tests with real persistence and no paid provider calls."""

import asyncio
import hashlib
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from test_tailor import CV

from backend import main
from backend.db import connect_app
from backend.matching.jev import JobScore
from backend.resumes.pipeline import save_score
from backend.resumes.storage import job_key


@pytest.fixture
def clients(settings, monkeypatch):
    async def idle(_settings):
        await asyncio.Future()

    monkeypatch.setattr(main, "worker", idle)
    with TestClient(main.app) as browser:
        browser.post("/auth/session").raise_for_status()
        token = browser.post("/auth/tokens", json={"name": "MCP test"}).json()
        yield browser, token


def rpc(client, token, method, params=None):
    response = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token['token']}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    return body["result"]


def call(client, token, name, **arguments):
    result = rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert not result.get("isError"), result
    return result["structuredContent"]


def test_real_mcp_discovery_auth_origin_and_revocation(clients):
    browser, token = clients
    hello = rpc(
        browser,
        token,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "AviCV integration test", "version": "1"},
        },
    )
    assert hello["serverInfo"]["name"] == "AviCV"
    tools = rpc(browser, token, "tools/list")["tools"]
    by_name = {item["name"]: item for item in tools}
    assert {
        "list_resumes",
        "tailor_cv",
        "get_good_match_links",
        "begin_resume_upload",
        "list_tailored_cvs",
    } <= by_name.keys()
    assert by_name["list_resumes"]["annotations"]["readOnlyHint"] is True
    assert by_name["rematch"]["annotations"]["idempotentHint"] is False
    assert all("ctx" not in item["inputSchema"].get("properties", {}) for item in tools)
    assert call(browser, token, "list_resumes")["items"] == []
    assert (
        browser.post(
            "/mcp", headers={"Authorization": "Bearer invalid"}, json={}
        ).status_code
        == 401
    )
    assert (
        browser.post(
            "/mcp", headers={"Origin": "https://attacker.example"}, json={}
        ).status_code
        == 403
    )
    browser.delete(f"/auth/tokens/{token['id']}").raise_for_status()
    assert (
        browser.post(
            "/mcp", headers={"Authorization": f"Bearer {token['token']}"}, json={}
        ).status_code
        == 401
    )


def test_browser_upload_agent_matching_and_pdf_reused_by_browser(
    clients, settings, make_pdf
):
    browser, token = clients
    original = make_pdf()
    resume_id = browser.post(
        "/resumes", files={"file": ("cv.pdf", original, "application/pdf")}
    ).json()["id"]
    conn = connect_app(settings.app_db)
    conn.execute(
        "UPDATE resumes SET status='done',rendercv_yaml=? WHERE id=?", (CV, resume_id)
    )
    job = {
        "id": 1,
        "provider": "test",
        "board": "board",
        "source_id": "1",
        "content_hash": "hash1",
        "title": "Thermal engineer",
        "company": "Example",
        "description_text": "Thermal modeling and CAD",
        "locations": ["Canada"],
        "apply_url": "https://example.com/apply",
    }
    save_score(
        conn,
        resume_id,
        0,
        0,
        job,
        JobScore(1, "hash1", 0.9, 3.8, 0.9, 0.9, 0.9, "matches", 100),
    )
    conn.commit()
    conn.close()
    assert call(browser, token, "list_resumes")["items"][0]["id"] == resume_id
    matches = call(browser, token, "list_matches", resume_id=resume_id)
    assert matches["items"][0]["category"] == "good"
    assert "score" not in matches["items"][0]
    assert (
        call(browser, token, "get_job", resume_id=resume_id, job_key=job_key(job))[
            "job"
        ]["description_text"]
        == job["description_text"]
    )
    created = call(
        browser, token, "tailor_cv", resume_id=resume_id, job_key=job_key(job)
    )
    same = browser.post(
        f"/resumes/{resume_id}/tailor", json={"job_key": job_key(job)}
    ).json()
    assert same["id"] == created["id"]
    assert (
        browser.get(f"/resumes/{resume_id}/tailored").json()["items"][0]["id"]
        == created["id"]
    )
    conn = connect_app(settings.app_db)
    conn.execute(
        "UPDATE tailored_cvs SET status='ready',pdf_bytes=?,filename='tailored.pdf' WHERE id=?",
        (original, created["id"]),
    )
    conn.commit()
    conn.close()
    ready = call(
        browser,
        token,
        "get_tailored_cv",
        resume_id=resume_id,
        artifact_id=created["id"],
    )
    link = urlsplit(ready["download_url"]).path
    assert browser.get(link).content == original
    assert (
        browser.get(f"/resumes/{resume_id}/tailored/{created['id']}/cv.pdf").content
        == original
    )
    other = TestClient(main.app)
    other.post("/auth/session")
    foreign_token = other.post("/auth/tokens", json={"name": "Other"}).json()
    denied = rpc(
        other,
        foreign_token,
        "tools/call",
        {"name": "get_resume", "arguments": {"resume_id": resume_id}},
    )
    assert denied["isError"] is True
    assert (
        other.get(f"/resumes/{resume_id}/tailored/{created['id']}/cv.pdf").status_code
        == 404
    )
    browser.delete(f"/auth/tokens/{token['id']}")
    assert browser.get(link).status_code == 401


def test_agent_upload_recover_retry_and_browser_discovery(clients, make_pdf):
    browser, token = clients
    pdf = make_pdf()
    ticket = call(
        browser,
        token,
        "begin_resume_upload",
        filename="agent.pdf",
        size_bytes=len(pdf),
        sha256=hashlib.sha256(pdf).hexdigest(),
    )
    path = urlsplit(ticket["upload_url"]).path
    assert (
        browser.put(path, headers=ticket["headers"], content=pdf + b"extra").status_code
        == 413
    )
    response = browser.put(path, headers=ticket["headers"], content=pdf)
    assert response.status_code == 202, response.text
    resume_id = response.json()["id"]
    assert (
        browser.put(path, headers=ticket["headers"], content=pdf).json()["id"]
        == resume_id
    )
    assert (
        call(browser, token, "get_upload", upload_id=ticket["upload_id"])["resume_id"]
        == resume_id
    )
    assert browser.get("/resumes").json()["total"] == 1
    browser.delete(f"/resumes/{resume_id}").raise_for_status()
    assert browser.put(path, headers=ticket["headers"], content=pdf).status_code == 410
    assert browser.get("/resumes").json()["total"] == 0


def test_revoked_credential_cannot_use_previously_issued_upload(clients, make_pdf):
    browser, token = clients
    pdf = make_pdf()
    ticket = call(
        browser, token, "begin_resume_upload", filename="cv.pdf", size_bytes=len(pdf)
    )
    browser.delete(f"/auth/tokens/{token['id']}")
    assert (
        browser.put(
            urlsplit(ticket["upload_url"]).path, headers=ticket["headers"], content=pdf
        ).status_code
        == 401
    )


def test_browser_and_agent_share_upload_budget(clients, settings, make_pdf):
    browser, token = clients
    settings.uploads_per_day = 1
    pdf = make_pdf()
    assert (
        browser.post(
            "/resumes", files={"file": ("cv.pdf", pdf, "application/pdf")}
        ).status_code
        == 202
    )
    ticket = call(
        browser, token, "begin_resume_upload", filename="cv.pdf", size_bytes=len(pdf)
    )
    denied = browser.put(
        urlsplit(ticket["upload_url"]).path, headers=ticket["headers"], content=pdf
    )
    assert denied.status_code == 429
    assert int(denied.headers["retry-after"]) > 0
    assert browser.get("/resumes").json()["total"] == 1
