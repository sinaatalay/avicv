import sqlite3
from pathlib import Path

import pytest

from backend.db import connect


def test_readonly_connection_to_a_closed_wal_database(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    writer = connect(path)
    writer.execute("CREATE TABLE jobs (title TEXT)")
    writer.execute("INSERT INTO jobs VALUES ('Backend Engineer')")
    writer.commit()
    writer.close()
    # SQLite removes the WAL files once the last connection closes
    assert not (tmp_path / "jobs.db-shm").exists()

    reader = connect(path, readonly=True)
    assert (
        reader.execute("SELECT title FROM jobs").fetchone()["title"]
        == "Backend Engineer"
    )
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.execute("DELETE FROM jobs")
    reader.close()


def test_readonly_connection_needs_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "missing.db", readonly=True)
    assert not (tmp_path / "missing.db").exists()
