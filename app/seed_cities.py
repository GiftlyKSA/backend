"""Run the idempotent 20-city Saudi catalog seed after database migration."""

from __future__ import annotations

import asyncio

from app.seed import seed_cities


def main() -> None:
    """Create missing default cities and refresh their Arabic names."""
    created = asyncio.run(seed_cities())
    print(f"City seed complete. Created {created} cities.")


if __name__ == "__main__":
    main()
