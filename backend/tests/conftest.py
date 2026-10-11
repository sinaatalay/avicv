import io
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.annotations import Link

from backend.config import Settings, get_settings

# The columns of jobs.db that résumé matching reads (see backend/jobs/store.py)
JOBS_SCHEMA = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY, provider TEXT NOT NULL, board TEXT NOT NULL, source_id TEXT NOT NULL,
    first_seen TEXT NOT NULL, closed_at TEXT, content_hash TEXT NOT NULL, company TEXT, title TEXT NOT NULL,
    locations_text TEXT, geo_bucket TEXT, workplace_type TEXT, employment_type TEXT, posted_at TEXT,
    apply_url TEXT, source_url TEXT, salary_min REAL, salary_max REAL, salary_currency TEXT, salary_interval TEXT,
    description_text TEXT, UNIQUE (provider, board, source_id)
);
CREATE VIRTUAL TABLE jobs_fts USING fts5 (
    title, company, locations_text, description_text,
    content = 'jobs', content_rowid = 'id', tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER jobs_fts_ai AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_fts (rowid, title, company, locations_text, description_text)
    VALUES (new.id, new.title, new.company, new.locations_text, new.description_text);
END;
"""

BACKEND = "We build payment APIs in Python and Go on Kubernetes and PostgreSQL. You know distributed systems."

# (title, company, locations, geo bucket, closed, description)
JOBS = [
    (
        "Backend Engineer, Payments",
        "Stripe",
        "Toronto, Canada",
        "us_ca",
        False,
        BACKEND,
    ),
    (
        "Backend Engineer, Payments",
        "Stripe",
        "Remote in Canada",
        "us_ca",
        False,
        BACKEND,
    ),  # same role
    ("Senior Python Developer", "Affirm", "Remote Canada", "us_ca", False, BACKEND),
    (
        "Account Executive",
        "Ramp",
        "New York, NY",
        "us_ca",
        False,
        "Sell Python-powered finance software.",
    ),
    ("Backend Engineer, Ledger", "Monzo", "London, UK", "other", False, BACKEND),
    ("Go Backend Engineer", "Wealthsimple", "Toronto, Canada", "us_ca", True, BACKEND),
]


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings pointing at a temporary data directory, with fake API keys."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-typesafe-key")
    # Artifact tests advance their mocked workers explicitly; ordinary résumé
    # tests must never start real design/tailoring provider calls.
    monkeypatch.setenv("ARTIFACT_WORKERS_ENABLED", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def jobs_db(settings: Settings) -> Path:
    conn = sqlite3.connect(settings.jobs_db)
    conn.executescript(JOBS_SCHEMA)
    conn.executemany(
        "INSERT INTO jobs (provider, board, source_id, first_seen, closed_at, content_hash, company, title, "
        "locations_text, geo_bucket, workplace_type, apply_url, salary_min, salary_max, salary_currency, "
        "salary_interval, description_text) "
        "VALUES ('greenhouse', ?, ?, '2026-10-11', ?, ?, ?, ?, ?, ?, 'remote', ?, 150000, 190000, 'CAD', 'year', ?)",
        [
            (
                company.lower(),
                str(i),
                "2026-10-10" if closed else None,
                f"hash{i}",
                company,
                title,
                location,
                geo,
                f"https://example.com/jobs/{i}",
                description,
            )
            for i, (title, company, location, geo, closed, description) in enumerate(
                JOBS, start=1
            )
        ],
    )
    conn.commit()
    conn.close()
    return settings.jobs_db


def pdf(pages: int = 1, link: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if link:
        writer.add_annotation(
            page_number=0, annotation=Link(rect=(50, 50, 200, 70), url=link)
        )
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def make_pdf() -> Callable[..., bytes]:
    """A blank PDF, optionally with a link annotation on the first page."""
    return pdf
