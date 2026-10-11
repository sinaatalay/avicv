"""Workspace boundaries are exercised with real cookies and revocable PATs."""

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.auth import COOKIE_NAME, authorize_credential_id, resolve_credential
from backend.db import connect_app
from backend.main import app
from backend.resumes import service


def browser():
    client = TestClient(app)
    response = client.post("/auth/session")
    assert response.status_code == 200
    return client, response.json()["workspace_id"]


def test_persistent_session_reuses_workspace_and_stores_only_hash(settings):
    client = TestClient(app)
    assert client.get("/auth/session").status_code == 401
    response = client.post("/auth/session")
    cookie = client.cookies[COOKIE_NAME]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Max-Age=31536000" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "private, no-store"
    assert client.post("/auth/session").json() == response.json()
    assert client.get("/auth/session").json() == response.json()
    restored = TestClient(app, cookies={COOKIE_NAME: cookie})
    assert restored.get("/auth/session").json() == response.json()
    conn = connect_app(settings.app_db)
    record = dict(conn.execute("SELECT * FROM credentials").fetchone())
    conn.close()
    assert cookie not in repr(record)
    assert len(record["secret_hash"]) == 64


def test_invalid_session_is_not_identity_but_post_can_create_a_new_workspace(settings):
    client, original = browser()
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, "guessed-resource-id")
    assert client.get("/auth/session").status_code == 401
    assert client.get("/resumes").status_code == 401
    response = client.post("/auth/session")
    assert response.status_code == 200
    assert response.json()["workspace_id"] != original


def test_tokens_share_workspace_are_listed_without_secret_and_revoke_immediately(
    settings,
):
    client, workspace = browser()
    token = client.post("/auth/tokens", json={"name": "Local editor"}).json()
    assert token["last_used_at"] is None
    agent = TestClient(app, headers={"Authorization": f"Bearer {token['token']}"})
    assert agent.get("/auth/session").json() == {"workspace_id": workspace}
    assert agent.get("/resumes").status_code == 200
    assert agent.post("/auth/tokens", json={"name": "nested"}).status_code == 403
    metadata = client.get("/auth/tokens").json()["items"]
    assert metadata[0]["last_used_at"] is not None
    assert "token" not in metadata[0]
    assert "secret_hash" not in metadata[0]
    other, _ = browser()
    assert other.delete(f"/auth/tokens/{token['id']}").status_code == 404
    assert client.delete(f"/auth/tokens/{token['id']}").status_code == 204
    assert agent.get("/resumes").status_code == 401
    with pytest.raises(HTTPException) as denied:
        authorize_credential_id(token["id"], workspace, settings)
    assert denied.value.status_code == 401


def test_foreign_and_ownerless_ids_never_authorize_any_private_endpoint(
    settings, make_pdf
):
    owner, workspace = browser()
    uploaded = owner.post(
        "/resumes", files={"file": ("private.pdf", make_pdf(), "application/pdf")}
    )
    assert uploaded.status_code == 202
    resume_id = uploaded.json()["id"]
    conn = connect_app(settings.app_db)
    conn.execute(
        "INSERT INTO resumes(id,created_at,pdf_sha256,status) VALUES('legacy','old','hash','done')"
    )
    conn.commit()
    assert (
        conn.execute(
            "SELECT workspace_id FROM resumes WHERE id=?", (resume_id,)
        ).fetchone()[0]
        == workspace
    )
    conn.close()
    stranger, _ = browser()
    assert stranger.get("/resumes").json()["items"] == []
    assert owner.get("/resumes").json()["items"][0]["id"] == resume_id
    requests = [
        ("GET", "", None),
        ("GET", "/rendercv.yaml", None),
        ("GET", "/match-results", None),
        ("GET", "/matches", None),
        ("GET", "/good-match-links", None),
        ("GET", "/time-savings", None),
        ("GET", "/design", None),
        ("GET", "/tailored", None),
        ("GET", "/tailored/artifact", None),
        ("GET", "/tailored/artifact/cv.pdf", None),
        ("POST", "/tailor", {"job_key": "known"}),
        ("POST", "/retry", None),
        ("POST", "/rematch", None),
        ("DELETE", "", None),
    ]
    for target in [resume_id, "legacy"]:
        for method, suffix, body in requests:
            response = stranger.request(method, f"/resumes/{target}{suffix}", json=body)
            assert response.status_code == 404, (method, suffix, response.text)
    anonymous = TestClient(app)
    assert anonymous.get(f"/resumes/{resume_id}").status_code == 401
    assert owner.get(f"/resumes/{resume_id}").status_code == 200


def test_reserved_upload_is_idempotent_and_foreign_owner_cannot_reuse_it(
    settings, make_pdf
):
    client, _ = browser()
    principal = resolve_credential(client.cookies[COOKIE_NAME], settings)
    first = service.create_resume(
        principal, make_pdf(), "cv.pdf", settings, resume_id="reserved"
    )
    second = service.create_resume(
        principal, make_pdf(), "cv.pdf", settings, resume_id="reserved"
    )
    assert first == second
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM resume_queue").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT used FROM usage_budgets WHERE owner=? AND action='upload'",
            (principal.workspace_id,),
        ).fetchone()[0]
        == 1
    )
    conn.close()
    other, _ = browser()
    other_principal = resolve_credential(other.cookies[COOKIE_NAME], settings)
    with pytest.raises(HTTPException) as conflict:
        service.create_resume(
            other_principal, make_pdf(), "cv.pdf", settings, resume_id="reserved"
        )
    assert conflict.value.status_code == 409


def test_browser_origin_enforced_while_bearer_auth_is_independent(settings):
    client, _ = browser()
    assert (
        client.post(
            "/auth/session", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/auth/tokens", json={"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    token = client.post("/auth/tokens", json={"name": "editor"}).json()["token"]
    agent = TestClient(
        app,
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://editor.example",
        },
    )
    assert agent.get("/resumes").status_code == 200
    assert agent.post("/resumes/missing/retry").status_code == 404


def test_expired_sessions_and_wrong_credential_kinds_are_rejected(settings):
    client, workspace = browser()
    cookie = client.cookies[COOKIE_NAME]
    token = client.post("/auth/tokens", json={"name": "editor"}).json()["token"]
    assert (
        TestClient(app, headers={"Authorization": f"Bearer {cookie}"})
        .get("/auth/session")
        .status_code
        == 401
    )
    assert (
        TestClient(app, cookies={COOKIE_NAME: token}).get("/auth/session").status_code
        == 401
    )
    conn = connect_app(settings.app_db)
    conn.execute(
        "UPDATE credentials SET expires_at=? WHERE kind='session'", (time.time() - 1,)
    )
    conn.commit()
    conn.close()
    assert client.get("/auth/session").status_code == 401
    assert client.post("/auth/session").json()["workspace_id"] != workspace


def test_owned_listing_and_artifacts_are_shared_with_agent_and_paginated(settings):
    from conftest import workspace_session
    from test_artifacts import seed
    from test_resume_reliability import seed_resume
    from test_time_savings import save_artifact

    seed(settings)
    seed_resume(settings, "second", queued=False)
    _, cookie = workspace_session(settings)
    client = TestClient(app, cookies={COOKIE_NAME: cookie})
    token = client.post("/auth/tokens", json={"name": "agent"}).json()["token"]
    agent = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    conn = connect_app(settings.app_db)
    artifact_id = save_artifact(
        conn, {"id": 1, "source_url": "https://example.com/job"}
    )
    conn.commit()
    conn.close()
    listing = agent.get("/resumes?limit=1").json()
    assert listing["total"] == 2
    assert listing["has_more"]
    assert listing == client.get("/resumes?limit=1").json()
    second_page = agent.get("/resumes?limit=1&offset=1").json()
    assert second_page["items"][0]["id"] != listing["items"][0]["id"]
    assert not second_page["has_more"]
    for row in [*listing["items"], *second_page["items"]]:
        assert row["good_total"] == (2 if row["id"] == "resume" else 0)
    artifacts = agent.get("/resumes/resume/tailored").json()
    assert artifacts == client.get("/resumes/resume/tailored").json()
    assert artifacts["items"][0]["id"] == artifact_id
    assert artifacts["items"][0]["job_key"]
    assert artifacts["items"][0]["created_at"]
    assert agent.get(artifacts["items"][0]["download_url"]).status_code == 200
    assert (
        agent.get(f"/resumes/second/tailored/{artifact_id}/cv.pdf").status_code == 404
    )


def test_v3_migration_preserves_legacy_pdf_without_claiming_it(settings):
    import sqlite3

    from backend.db import SCHEMA_VERSION

    conn = sqlite3.connect(settings.app_db)
    conn.execute(
        "CREATE TABLE resumes(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, filename TEXT, pdf_sha256 TEXT NOT NULL, status TEXT NOT NULL, error TEXT, parse_preview TEXT, rendercv_yaml TEXT, parse_seconds REAL, parse_cost_usd REAL, match_total INTEGER NOT NULL DEFAULT 0, match_done INTEGER NOT NULL DEFAULT 0, match_cost_usd REAL NOT NULL DEFAULT 0, upload_pdf BLOB)"
    )
    conn.execute(
        "INSERT INTO resumes(id,created_at,pdf_sha256,status,upload_pdf) VALUES('private-legacy','old','hash','done',?)",
        (b"source bytes",),
    )
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    conn.close()
    migrated = connect_app(settings.app_db)
    row = migrated.execute("SELECT upload_pdf,workspace_id FROM resumes").fetchone()
    assert row["upload_pdf"] == b"source bytes"
    assert row["workspace_id"] is None
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    migrated.close()
    client, _ = browser()
    assert client.get("/resumes").json()["total"] == 0
    assert client.get("/resumes/private-legacy").status_code == 404


def test_deleted_reserved_upload_cannot_be_resurrected_by_a_delayed_retry(
    settings, make_pdf
):
    client, _ = browser()
    principal = resolve_credential(client.cookies[COOKIE_NAME], settings)
    pdf = make_pdf()
    service.create_resume(principal, pdf, "cv.pdf", settings, resume_id="reserved")
    # Models a lost initial upload response: its ticket may not have been marked
    # accepted yet. Only the core transaction knows this résumé ID is retired.
    assert client.delete("/resumes/reserved").status_code == 204
    with pytest.raises(HTTPException) as deleted:
        service.create_resume(principal, pdf, "cv.pdf", settings, resume_id="reserved")
    assert deleted.value.status_code == 410
    conn = connect_app(settings.app_db)
    assert conn.execute("SELECT COUNT(*) FROM resumes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM resume_queue").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT used FROM usage_budgets WHERE owner=? AND action='upload'",
            (principal.workspace_id,),
        ).fetchone()[0]
        == 1
    )
    conn.close()
    fresh = service.create_resume(principal, pdf, "cv.pdf", settings)
    assert fresh.id != "reserved"


def test_chat_admission_shares_workspace_budget_across_browser_and_agent(
    settings, monkeypatch
):
    monkeypatch.setattr(settings, "chats_per_day", 2)
    client, workspace = browser()
    token = client.post("/auth/tokens", json={"name": "agent"}).json()["token"]
    agent = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    assert TestClient(app).post("/auth/chat-admission", json={}).status_code == 401
    assert (
        client.post(
            "/auth/chat-admission", headers={"Origin": "https://evil.example"}, json={}
        ).status_code
        == 403
    )
    admitted = client.post("/auth/chat-admission", json={})
    assert admitted.status_code == 200
    assert admitted.json() == {"accepted": True}
    assert admitted.headers["cache-control"] == "private, no-store"
    assert agent.post("/auth/chat-admission", json={}).status_code == 200
    denied = client.post("/auth/chat-admission", json={})
    assert denied.status_code == 429
    assert int(denied.headers["retry-after"]) > 0
    conn = connect_app(settings.app_db)
    assert (
        conn.execute(
            "SELECT used FROM usage_budgets WHERE owner=? AND action='chat'",
            (workspace,),
        ).fetchone()[0]
        == 2
    )
    conn.close()


def test_fresh_workspaces_cannot_bypass_global_chat_budget_and_failed_reservations_rollback(
    settings, monkeypatch
):
    monkeypatch.setattr(settings, "global_actions_per_day", 2)
    first, _ = browser()
    second, _ = browser()
    third, third_workspace = browser()
    assert first.post("/auth/chat-admission", json={}).status_code == 200
    assert second.post("/auth/chat-admission", json={}).status_code == 200
    denied = third.post("/auth/chat-admission", json={})
    assert denied.status_code == 429
    conn = connect_app(settings.app_db)
    assert (
        conn.execute(
            "SELECT used FROM usage_budgets WHERE owner='*' AND action='chat'"
        ).fetchone()[0]
        == 2
    )
    assert (
        conn.execute(
            "SELECT used FROM usage_budgets WHERE owner=? AND action='chat'",
            (third_workspace,),
        ).fetchone()
        is None
    )
    conn.close()


def test_browser_upload_runs_pdf_validation_and_sqlite_outside_event_loop(
    settings, make_pdf, monkeypatch
):
    import asyncio
    import threading

    import httpx

    client, _ = browser()
    cookie = client.cookies[COOKIE_NAME]
    original = service.create_resume
    worker_threads = []

    def create(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "create_resume", create)

    async def upload():
        event_loop_thread = threading.get_ident()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies={COOKIE_NAME: cookie},
        ) as asynchronous:
            result = await asynchronous.post(
                "/resumes", files={"file": ("cv.pdf", make_pdf(), "application/pdf")}
            )
        assert result.status_code == 202
        assert worker_threads and all(
            worker != event_loop_thread for worker in worker_threads
        )

    asyncio.run(upload())
