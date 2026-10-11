import pytest
from fastapi.testclient import TestClient

from backend.auth import COOKIE_NAME, resolve_credential
from backend.db import connect_app
from backend.main import app
from backend.migrate_owner import assign_legacy_resumes, main


def destination(settings):
    client = TestClient(app)
    client.post("/auth/session")
    principal = resolve_credential(client.cookies[COOKIE_NAME], settings)
    return client, principal


def legacy_rows(settings, other_owner: str):
    conn = connect_app(settings.app_db)
    for resume_id, workspace in [
        ("first", None),
        ("second", None),
        ("owned", other_owner),
    ]:
        conn.execute(
            "INSERT INTO resumes(id,workspace_id,created_at,pdf_sha256,status,upload_pdf,rendercv_yaml) VALUES(?,?,'original-date','original-hash','done',?,'original-yaml')",
            (resume_id, workspace, b"original PDF"),
        )
    conn.execute(
        "INSERT INTO resume_designs(id,resume_id,created_at,updated_at,version,model,source_yaml,source_pdf_hash,status,design_json) VALUES('design','first','old','old','v','model','original-yaml','original-hash','ready','{}')"
    )
    conn.execute(
        "INSERT INTO tailored_cvs(id,resume_id,design_id,fingerprint,job_key,source_yaml,job_snapshot,model,version,render_version,created_at,updated_at,status,pdf_bytes) VALUES('artifact','first','design','fingerprint','job','yaml','{}','model','v','render','old','old','ready',?)",
        (b"tailored PDF",),
    )
    conn.commit()
    before = {
        row["id"]: {
            key: value for key, value in dict(row).items() if key != "workspace_id"
        }
        for row in conn.execute("SELECT * FROM resumes")
    }
    assets = [tuple(row) for row in conn.execute("SELECT * FROM tailored_cvs")]
    conn.close()
    return before, assets


def test_selected_migration_is_atomic_idempotent_and_never_reassigns_owned_data(
    settings,
):
    _, principal = destination(settings)
    _, other = destination(settings)
    before, assets = legacy_rows(settings, other.workspace_id)
    result = assign_legacy_resumes(
        principal, settings, resume_ids=["first", "first", "owned", "missing"]
    )
    assert (result.assigned, result.already_owned, result.missing) == (1, 1, 1)
    rerun = assign_legacy_resumes(principal, settings, resume_ids=["first"])
    assert (rerun.assigned, rerun.already_owned) == (0, 1)
    conn = connect_app(settings.app_db)
    owners = {
        row["id"]: row["workspace_id"]
        for row in conn.execute("SELECT id,workspace_id FROM resumes")
    }
    assert owners == {
        "first": principal.workspace_id,
        "second": None,
        "owned": other.workspace_id,
    }
    after = {
        row["id"]: {
            key: value for key, value in dict(row).items() if key != "workspace_id"
        }
        for row in conn.execute("SELECT * FROM resumes")
    }
    assert after == before
    assert [tuple(row) for row in conn.execute("SELECT * FROM tailored_cvs")] == assets
    conn.close()


def test_all_unowned_is_explicit_and_leaves_other_workspace_untouched(settings):
    _, principal = destination(settings)
    _, other = destination(settings)
    legacy_rows(settings, other.workspace_id)
    with pytest.raises(ValueError):
        assign_legacy_resumes(principal, settings)
    with pytest.raises(ValueError):
        assign_legacy_resumes(
            principal, settings, resume_ids=["first"], all_unowned=True
        )
    result = assign_legacy_resumes(principal, settings, all_unowned=True)
    assert result.assigned == 2
    conn = connect_app(settings.app_db)
    assert (
        conn.execute("SELECT workspace_id FROM resumes WHERE id='owned'").fetchone()[0]
        == other.workspace_id
    )
    conn.close()
    assert assign_legacy_resumes(principal, settings, all_unowned=True).assigned == 0


def test_cli_reads_hidden_token_not_argv_and_rejects_revoked_credentials(
    settings, monkeypatch, capsys
):
    client, principal = destination(settings)
    legacy_rows(settings, principal.workspace_id)
    issued = client.post("/auth/tokens", json={"name": "migration"}).json()
    monkeypatch.setattr(
        "backend.migrate_owner.getpass.getpass", lambda _: issued["token"]
    )
    assert main(["--resume", "first"]) == 0
    assert issued["token"] not in capsys.readouterr().out
    client.delete(f"/auth/tokens/{issued['id']}")
    with pytest.raises(SystemExit) as denied:
        main(["--all-unowned"])
    assert denied.value.code == 2
    conn = connect_app(settings.app_db)
    assert (
        conn.execute("SELECT workspace_id FROM resumes WHERE id='second'").fetchone()[0]
        is None
    )
    conn.close()
