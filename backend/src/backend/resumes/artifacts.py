"""Checkpointed design reconstruction and factual tailoring, independent of matching."""

import json
import re
import unicodedata
from typing import Any

from ..config import Settings
from ..db import connect_app
from .artifact_queue import ArtifactClaim, ArtifactLeaseLost, begin_artifact
from .artifact_store import now_iso
from .parse import load_yaml
from .tailor import TAILOR_VERSION, tailor_resume


def pdf_filename(yaml: str, job: dict[str, Any]) -> str:
    parts = [
        load_yaml(yaml).cv.name or "CV",
        job.get("company") or "",
        job.get("title") or "Tailored",
    ]
    text = (
        unicodedata.normalize("NFKD", "-".join(parts))
        .encode("ascii", "ignore")
        .decode()
    )
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")[:150]
    return (stem or "tailored-cv") + ".pdf"


async def process_artifact(settings: Settings, claim: ArtifactClaim) -> float | None:
    if claim.kind == "design":
        await process_design(settings, claim)
        return None
    return await process_tailoring(settings, claim)


async def process_design(settings: Settings, claim: ArtifactClaim) -> None:
    from .design import default_design, reconstruct_design

    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT d.*, r.upload_pdf FROM resume_designs d JOIN resumes r ON r.id = d.resume_id WHERE d.id = ?",
            (claim.target_id,),
        ).fetchone()
        if row is None:
            raise ArtifactLeaseLost("Résumé was deleted")
        if row["status"] == "ready" and row["design_json"]:
            return
        begin_artifact(conn, claim)
        conn.execute(
            "UPDATE resume_designs SET status = 'designing', error = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), claim.target_id),
        )
        conn.commit()
        if row["upload_pdf"]:
            result = await reconstruct_design(
                row["upload_pdf"],
                row["source_yaml"],
                settings.model_copy(update={"design_model": row["model"]}),
            )
            design, score, iterations, notes, cost = (
                result.design,
                result.score,
                result.iterations,
                result.notes,
                result.cost_usd,
            )
            source = "reconstructed"
        else:
            design, score, iterations, cost = default_design(), None, 0, 0
            notes = (
                "The original PDF is unavailable; this CV uses a clean default design."
            )
            source = "default"
        begin_artifact(conn, claim)
        conn.execute(
            """
            UPDATE resume_designs SET status = 'ready', design_json = ?, source = ?, quality_score = ?,
                iterations = ?, notes = ?, cost_usd = ?, updated_at = ?, error = NULL WHERE id = ?
        """,
            (
                json.dumps(design),
                source,
                score,
                iterations,
                notes,
                cost,
                now_iso(),
                claim.target_id,
            ),
        )
        # Wake dependents in the same fenced transaction as the saved design.
        conn.execute(
            """
                UPDATE artifact_queue SET available_at = 0 WHERE kind = 'tailor' AND state = 'pending'
                    AND target_id IN (SELECT id FROM tailored_cvs WHERE design_id = ?)
            """,
            (claim.target_id,),
        )
        conn.commit()
    finally:
        conn.close()


async def process_tailoring(settings: Settings, claim: ArtifactClaim) -> float | None:
    from .render import RENDER_VERSION, render_resume

    conn = connect_app(settings.app_db)
    try:
        row = conn.execute(
            "SELECT * FROM tailored_cvs WHERE id = ?", (claim.target_id,)
        ).fetchone()
        if row is None:
            raise ArtifactLeaseLost("Résumé was deleted")
        if row["status"] == "ready" and row["pdf_bytes"]:
            return None
        design = conn.execute(
            "SELECT * FROM resume_designs WHERE id = ?", (row["design_id"],)
        ).fetchone()
        begin_artifact(conn, claim)
        if design["status"] == "failed":
            conn.execute(
                "UPDATE tailored_cvs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (
                    "We couldn't prepare this résumé's design. Select Tailor CV again to retry.",
                    now_iso(),
                    claim.target_id,
                ),
            )
            conn.commit()
            return None
        if design["status"] != "ready" or not design["design_json"]:
            conn.execute(
                "UPDATE tailored_cvs SET status = 'waiting_for_design', error = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), claim.target_id),
            )
            conn.commit()
            return 2
        if row["version"] != TAILOR_VERSION or row["render_version"] != RENDER_VERSION:
            conn.rollback()
            raise RuntimeError(
                "Artifact versions changed; a new Tailor CV request will use the current versions"
            )
        job = json.loads(row["job_snapshot"])
        yaml = row["tailored_yaml"]
        conn.execute(
            "UPDATE tailored_cvs SET status = ?, error = NULL, updated_at = ? WHERE id = ?",
            ("rendering" if yaml else "tailoring", now_iso(), claim.target_id),
        )
        conn.commit()
        if not yaml:
            result = await tailor_resume(
                row["source_yaml"],
                job,
                settings.model_copy(update={"tailor_model": row["model"]}),
            )
            yaml = result.yaml
            begin_artifact(conn, claim)
            conn.execute(
                """
                UPDATE tailored_cvs SET status = 'rendering', plan_json = ?, tailored_yaml = ?,
                    cost_usd = ?, updated_at = ? WHERE id = ?
            """,
                (
                    result.plan.model_dump_json(),
                    yaml,
                    result.cost_usd,
                    now_iso(),
                    claim.target_id,
                ),
            )
            conn.commit()
        rendered = await render_resume(
            yaml,
            json.loads(design["design_json"]),
            timeout_seconds=settings.render_timeout_seconds,
            previews=False,
        )
        if not rendered.pdf.startswith(b"%PDF-"):
            raise ValueError("Renderer did not produce a PDF")
        begin_artifact(conn, claim)
        conn.execute(
            """
            UPDATE tailored_cvs SET status = 'ready', pdf_bytes = ?, filename = ?, error = NULL, updated_at = ? WHERE id = ?
        """,
            (rendered.pdf, pdf_filename(yaml, job), now_iso(), claim.target_id),
        )
        conn.commit()
        return None
    finally:
        conn.close()
