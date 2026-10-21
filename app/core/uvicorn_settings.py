"""Resource limits passed to Giftly's Uvicorn Gunicorn worker."""

UVICORN_WORKER_CONFIG: dict[str, str | int | bool] = {
    "loop": "auto",
    "http": "auto",
    "ws": "websockets-sansio",
    "ws_max_size": 4096,
    "ws_max_queue": 1,
    "ws_per_message_deflate": False,
}
