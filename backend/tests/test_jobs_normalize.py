"""Normalization pitfalls, on trimmed real payloads from tests/fixtures/jobs."""

import json
from pathlib import Path
from typing import Any

import pytest

from backend.jobs import normalize
from backend.jobs.geo import classify
from backend.jobs.text import html_to_text

FIXTURES = Path(__file__).parent / "fixtures" / "jobs"
BOARD = {"id": "acme", "company": "Acme"}


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def to_row(provider: str, raw: Any) -> dict[str, Any]:
    record = normalize.NORMALIZERS[provider](raw, BOARD)
    assert record is not None
    return normalize.to_row(provider, "acme", raw, record, normalize.content_hash(raw))


def test_greenhouse_content_is_unescaped_exactly_once() -> None:
    job = load("greenhouse.json")["jobs"][0]
    assert job["content"].startswith("&lt;div class=&quot;content-intro&quot;&gt;")
    text = to_row("greenhouse", job)["description_text"]
    assert text.startswith("Airbnb was born in 2007")
    assert "Airbnb's hubs" in text  # &amp;#x27; in the payload: escaped twice
    for leftover in ("&lt;", "&amp;", "&#x27;", "<p>", "<span"):
        assert leftover not in text


def test_greenhouse_pay_is_in_cents_and_keeps_its_stated_interval() -> None:
    us, canada, stripe = load("greenhouse.json")["jobs"]
    assert us["pay_input_ranges"][0]["min_cents"] == 21_200_000
    row = to_row("greenhouse", us)
    assert (row["salary_min"], row["salary_max"], row["salary_currency"]) == (
        212_000,
        265_000,
        "USD",
    )
    assert (
        row["salary_interval"] is None
    )  # labelled just "Pay Range": not stated, not guessed
    row = to_row("greenhouse", canada)
    assert (row["salary_min"], row["salary_currency"], row["salary_interval"]) == (
        128_000,
        "CAD",
        "year",
    )
    assert row["salary_text"] == "Canada Annual Pay Range"
    assert to_row("greenhouse", stripe)["salary_min"] is None


def test_greenhouse_comma_ca_is_california() -> None:
    row = to_row("greenhouse", load("greenhouse.json")["jobs"][2])
    assert row["locations_text"] == "San Francisco, CA | US"
    assert json.loads(row["countries"]) == ["US"]
    assert json.loads(row["regions"]) == ["US-CA"]


def test_greenhouse_timestamps_keep_their_offset() -> None:
    row = to_row("greenhouse", load("greenhouse.json")["jobs"][0])
    assert (row["posted_at"], row["posted_at_field"]) == (
        "2026-10-06T15:59:10-04:00",
        "first_published",
    )


@pytest.mark.parametrize(
    ("location", "bucket", "countries", "regions"),
    [
        ("San Francisco, CA", "us_ca", ["US"], ["US-CA"]),
        ("Toronto, ON, CA", "us_ca", ["CA"], ["CA-ON"]),
        ("Calgary, Alberta", "us_ca", ["CA"], ["CA-AB"]),
        ("London, ON", "us_ca", ["CA"], ["CA-ON"]),
        ("Albuquerque, New Mexico", "us_ca", ["US"], ["US-NM"]),
        ("Vancouver, WA", "us_ca", ["US"], ["US-WA"]),
        ("Remote, North America", "us_ca", ["CA", "US"], []),
        ("Bengaluru, IN", "other", ["IN"], []),
        ("Tel Aviv, IL", "other", ["IL"], []),
        ("London, United Kingdom", "other", ["GB"], []),
        ("Remote - EMEA", "other", [], []),
        ("Remote", "remote_unspecified", [], []),
        ("Hybrid", "unknown", [], []),
    ],
)
def test_geography(
    location: str, bucket: str, countries: list[str], regions: list[str]
) -> None:
    assert classify([location]) == {
        "countries": countries,
        "regions": regions,
        "geo_bucket": bucket,
    }


def test_structured_country_hints_win() -> None:
    assert classify(["Remote"], ["us"])["geo_bucket"] == "us_ca"
    assert classify(["Remote"], ["European Union"])["geo_bucket"] == "other"


def test_lever_keeps_evergreen_dates() -> None:
    palantir, zoox = load("lever.json")
    assert palantir["createdAt"] == 1_259_971_200_000
    row = to_row("lever", palantir)
    assert (row["posted_at"], row["posted_at_field"]) == (
        "2009-12-05T00:00:00+00:00",
        "createdAt",
    )
    row = to_row("lever", zoox)
    assert (row["salary_min"], row["salary_max"], row["salary_interval"]) == (
        324_000,
        389_000,
        "year",
    )
    assert json.loads(row["regions"]) == ["US-CA", "US-MA"]


def test_lever_description_includes_list_sections() -> None:
    zoox = load("lever.json")[1]
    text = to_row("lever", zoox)["description_text"]
    heading = zoox["lists"][0]["text"]
    assert heading in text
    assert "\n- " in text


def test_ashby_compensation_unlisted_and_secondary_locations() -> None:
    manager, benevity, unlisted = load("ashby.json")["jobs"]
    row = to_row("ashby", manager)
    assert (
        row["salary_min"],
        row["salary_max"],
        row["salary_currency"],
        row["salary_interval"],
    ) == (
        210_000,
        280_313,
        "USD",
        "year",
    )
    row = to_row("ashby", benevity)
    assert row["locations_text"] == "Calgary, Alberta | Vancouver, British Columbia"
    assert json.loads(row["regions"]) == ["CA-AB", "CA-BC"]
    assert row["workplace_type"] == "hybrid"
    assert normalize.ashby(unlisted, BOARD) is None


def test_ashby_hidden_compensation_is_ignored() -> None:
    manager = load("ashby.json")["jobs"][0] | {
        "shouldDisplayCompensationOnJobPostings": False
    }
    assert to_row("ashby", manager)["salary_min"] is None


def test_smartrecruiters_needs_the_detail_response() -> None:
    item = load("smartrecruiters_list.json")["content"][0]
    detail = load("smartrecruiters_detail.json")[item["id"]]
    full = to_row("smartrecruiters", {"list": item, "detail": detail})
    assert full["description_text"].startswith("Company Description\n\nExperian is")
    assert full["apply_url"].endswith("?oga=true")
    assert (full["salary_min"], full["salary_interval"]) == (
        65_883,
        "year",
    )  # period "YEARLY"
    listed_only = to_row("smartrecruiters", {"list": item, "detail": None})
    assert listed_only["description_text"] is None
    assert listed_only["apply_url"] is None
    assert (
        listed_only["source_url"]
        == f"https://jobs.smartrecruiters.com/Experian/{item['id']}"
    )
    assert listed_only["workplace_type"] == "remote"


def test_himalayas_epochs_and_salary() -> None:
    usd, cad = load("himalayas.json")["jobs"]
    row = to_row("himalayas", cad)
    assert row["posted_at"] == "2026-10-04T17:08:05+00:00"
    assert row["expires_at"] is not None
    assert (row["salary_min"], row["salary_currency"], row["salary_interval"]) == (
        65_000,
        "CAD",
        "year",
    )
    assert to_row("himalayas", usd)["locations_text"] == "Canada | United States"


def test_rows_store_compressed_raw_and_no_html() -> None:
    job = load("greenhouse.json")["jobs"][0]
    row = to_row("greenhouse", job)
    assert "description_html" not in row
    assert normalize.unpack(row["raw_zstd"]) == job
    assert len(row["raw_zstd"]) < len(normalize.canonical_json(job)) / 1.5


def test_content_hash_ignores_key_order_but_not_content() -> None:
    job = load("lever.json")[0]
    reordered = dict(reversed(list(job.items())))
    assert normalize.content_hash(reordered) == normalize.content_hash(job)
    assert normalize.content_hash(job | {"text": "Renamed"}) != normalize.content_hash(
        job
    )


def test_html_to_text() -> None:
    html = (
        "<p>Intro <b>bold</b>&nbsp;text\u200b</p>"
        "<ul>\n  <li>\n    <p>One</p>\n  </li>\n  <li><p>Two</p><p>more</p></li><li></li>\n</ul>"
        "<ol><li>first</li><li>second</li></ol>"
        "<script>alert(1)</script><style>p {}</style><p>end<br>line two</p>"
    )
    assert html_to_text(html) == (
        "Intro bold text\n\n- One\n- Two\nmore\n\n1. first\n2. second\n\nend\nline two"
    )
    assert html_to_text(None) == ""
