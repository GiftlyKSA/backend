"""Initialize a fresh database before API workers start."""

import asyncio

from app.main import create_app


async def bootstrap() -> None:
    """Run the same bounded schema and seed initialization as API startup."""
    app = create_app()
    async with app.router.lifespan_context(app):
        pass


if __name__ == "__main__":
    asyncio.run(bootstrap())
