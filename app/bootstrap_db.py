"""Apply pending database schema migrations before application services start."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)


def upgrade_database() -> None:
    """Create or upgrade the application schema to the latest revision."""
    project_root = Path(__file__).resolve().parent.parent
    config = Config(str(project_root / "alembic.ini"))
    command.upgrade(config, "head")


def main() -> None:
    """Run pending migrations and report failures with their traceback."""
    try:
        upgrade_database()
    except Exception:
        logger.exception("Database migrations failed; the application was not started")
        raise
    logger.info("Database migrations are up to date")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
