"""Apply database migrations before starting the API server."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from app.bootstrap_db import upgrade_database
from app.core.logging import configure_logging


def main() -> None:
    """Upgrade the database, then replace this process with its configured service."""
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    command = sys.argv[1:] or [
        "gunicorn",
        "app.main:create_app()",
        "--worker-class",
        "app.core.uvicorn_worker.GiftlyUvicornWorker",
        "--bind",
        "0.0.0.0:3000",
        "--workers",
        os.getenv("WEB_CONCURRENCY", "3"),
        "--timeout",
        os.getenv("GUNICORN_TIMEOUT", "80"),
    ]
    try:
        upgrade_database()
        if os.getenv("ENVIRONMENT") == "development" and command[0] in {"gunicorn", "uvicorn"}:
            from app.seed_development import seed_development_couriers

            asyncio.run(seed_development_couriers())
    except Exception:
        logging.getLogger(__name__).exception(
            "Database initialization failed; the service was not started"
        )
        raise
    os.execvp(command[0], command)  # noqa: S606, S607 - deployment command, no shell


if __name__ == "__main__":
    main()
