"""Apply database migrations before starting the API server."""

from __future__ import annotations

import logging
import os
import sys

from app.bootstrap_db import upgrade_database


def main() -> None:
    """Upgrade the database, then replace this process with its configured service."""
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        upgrade_database()
    except Exception:
        logging.getLogger(__name__).exception(
            "Database migrations failed; the service was not started"
        )
        raise
    command = sys.argv[1:] or [
        "gunicorn",
        "app.main:create_app()",
        "--worker-class",
        "uvicorn.workers.UvicornWorker",
        "--bind",
        "0.0.0.0:3000",
        "--workers",
        os.getenv("WEB_CONCURRENCY", "1"),
        "--timeout",
        os.getenv("GUNICORN_TIMEOUT", "60"),
    ]
    os.execvp(command[0], command)  # noqa: S606, S607 - deployment command, no shell


if __name__ == "__main__":
    main()
