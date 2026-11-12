"""Limit scheduled delivery work on the shared maintenance worker."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)
_RUN_BUDGET_SECONDS = 45


async def run_delivery_drainer(name: str, drain: Callable[[float], Awaitable[object]]) -> None:
    """Stop admitting pages near the deadline and bound stalls within a page."""
    deadline = asyncio.get_running_loop().time() + _RUN_BUDGET_SECONDS
    budget = asyncio.timeout_at(deadline)
    try:
        async with budget:
            await drain(deadline)
    except TimeoutError:
        if not budget.expired():
            raise
        _logger.warning("Delivery drainer reached its deadline (%s).", name)
