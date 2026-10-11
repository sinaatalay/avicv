"""Local administrator migration for pre-workspace AviCV data.

This is deliberately a CLI, never a public endpoint: knowledge of an old résumé
ID does not establish ownership. The administrator chooses the destination by
entering a currently valid connection token belonging to that workspace.
"""

import argparse
import getpass
from dataclasses import dataclass

from fastapi import HTTPException

from .auth import Principal, resolve_credential
from .config import Settings, get_settings
from .db import connect_app


@dataclass(frozen=True)
class MigrationResult:
    assigned: int
    already_owned: int
    missing: int


def assign_legacy_resumes(
    principal: Principal,
    settings: Settings,
    *,
    resume_ids: list[str] | None = None,
    all_unowned: bool = False,
) -> MigrationResult:
    """Assign only ownerless rows in one transaction; leave all assets intact."""
    if bool(resume_ids) == all_unowned:
        raise ValueError(
            "Choose specific résumé IDs or explicitly choose all unowned résumés."
        )
    conn = connect_app(settings.app_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if (
            conn.execute(
                "SELECT 1 FROM workspaces WHERE id=?", (principal.workspace_id,)
            ).fetchone()
            is None
        ):
            raise ValueError("The destination workspace no longer exists.")
        if all_unowned:
            result = conn.execute(
                "UPDATE resumes SET workspace_id=? WHERE workspace_id IS NULL",
                (principal.workspace_id,),
            )
            assigned, already_owned, missing = result.rowcount, 0, 0
        else:
            assigned = already_owned = missing = 0
            for resume_id in dict.fromkeys(resume_ids or []):
                row = conn.execute(
                    "SELECT workspace_id FROM resumes WHERE id=?", (resume_id,)
                ).fetchone()
                if row is None:
                    missing += 1
                elif row["workspace_id"] is not None:
                    already_owned += 1
                else:
                    assigned += conn.execute(
                        "UPDATE resumes SET workspace_id=? WHERE id=? AND workspace_id IS NULL",
                        (principal.workspace_id, resume_id),
                    ).rowcount
        conn.commit()
        return MigrationResult(assigned, already_owned, missing)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assign legacy ownerless CVs to an explicit AviCV workspace. Run only as the local database administrator."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--resume",
        dest="resume_ids",
        action="append",
        metavar="ID",
        help="Legacy résumé ID to assign; repeat for several CVs.",
    )
    selection.add_argument(
        "--all-unowned",
        action="store_true",
        help="Explicitly assign every currently ownerless résumé in this database.",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    token = getpass.getpass(
        "Connection token for the destination workspace (hidden): "
    ).strip()
    try:
        principal = resolve_credential(token, settings, kind="token")
    except HTTPException:
        parser.exit(
            2,
            "The connection token is invalid, expired, or revoked. No CVs were assigned.\n",
        )
    finally:
        token = ""
    result = assign_legacy_resumes(
        principal, settings, resume_ids=args.resume_ids, all_unowned=args.all_unowned
    )
    print(
        f"Assigned: {result.assigned}. Already owned (unchanged): {result.already_owned}. Not found: {result.missing}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
