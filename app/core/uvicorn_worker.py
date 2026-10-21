"""Uvicorn worker with bounded WebSocket transport resources."""

from uvicorn.workers import UvicornWorker

from app.core.uvicorn_settings import UVICORN_WORKER_CONFIG


class GiftlyUvicornWorker(UvicornWorker):
    """Keep inbound frame buffering small and avoid compression CPU overhead."""

    CONFIG_KWARGS = UVICORN_WORKER_CONFIG
