"""Keep short-lived download credentials out of Uvicorn access logs."""

import logging
import re

_DOWNLOAD_PATH = re.compile(r"/downloads/[^\s\"?#]+(?:\?[^\s\"]*)?")


class DownloadTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn logs (client, method, path-with-query, HTTP version, status).
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if isinstance(args[2], str):
                args[2] = _DOWNLOAD_PATH.sub("/downloads/[redacted]", args[2])
            record.args = tuple(args)
        elif isinstance(record.msg, str):
            record.msg = _DOWNLOAD_PATH.sub("/downloads/[redacted]", record.msg)
        return True


def install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, DownloadTokenFilter) for item in logger.filters):
        logger.addFilter(DownloadTokenFilter())
