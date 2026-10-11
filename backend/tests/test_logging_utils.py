import logging

from backend.logging_utils import DownloadTokenFilter, install_access_log_redaction


def record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "server",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", path, "1.1", 200),
        None,
    )


def test_download_bearer_and_query_are_redacted_but_route_status_remain():
    event = record("/downloads/private-grant?copied=private-grant")
    assert DownloadTokenFilter().filter(event)
    output = event.getMessage()
    assert "private-grant" not in output
    assert '/downloads/[redacted] HTTP/1.1" 200' in output
    normal = record("/resumes/example?limit=10")
    before = normal.getMessage()
    DownloadTokenFilter().filter(normal)
    assert normal.getMessage() == before


def test_install_is_idempotent_across_multiple_app_lifespans():
    install_access_log_redaction()
    install_access_log_redaction()
    assert (
        sum(
            isinstance(item, DownloadTokenFilter)
            for item in logging.getLogger("uvicorn.access").filters
        )
        == 1
    )


def test_preformatted_access_line_is_redacted_too():
    event = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "server",
        1,
        "GET /downloads/private-grant HTTP/1.1",
        (),
        None,
    )
    DownloadTokenFilter().filter(event)
    assert event.getMessage() == "GET /downloads/[redacted] HTTP/1.1"
