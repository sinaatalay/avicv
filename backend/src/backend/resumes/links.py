"""Shared validation of saved application URLs; never rehydrate catalog URLs."""

from typing import Any
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, TypeAdapter

HTTP_URL = TypeAdapter(AnyHttpUrl)
MAX_JOB_URL_LENGTH = 8192


def valid_job_url(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_JOB_URL_LENGTH
        or not value.lower().startswith(("http://", "https://"))
        or "\\" in value
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return None
    try:
        # Validation may normalize a malformed https:///host; never return
        # the original value unless it already has an explicit authority.
        if not urlsplit(value).netloc:
            return None
        parsed = HTTP_URL.validate_python(value)
        if parsed.host and parsed.username is None and parsed.password is None:
            return value
    except ValueError:
        pass
    return None


def job_url(job: dict[str, Any]) -> str | None:
    """Prefer a usable application URL, falling back to the saved source URL."""
    return valid_job_url(job.get("apply_url")) or valid_job_url(job.get("source_url"))
