"""The checked-in Taskiq entry points discover every maintenance schedule."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml


async def test_drainer_deadline_releases_stalled_worker(monkeypatch) -> None:
    from app.workers import delivery_budget

    started = asyncio.Event()
    closed = asyncio.Event()

    async def stalled(deadline: float) -> None:
        assert deadline > asyncio.get_running_loop().time()
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(delivery_budget, "_RUN_BUDGET_SECONDS", 0.01)
    await asyncio.wait_for(delivery_budget.run_delivery_drainer("test drainer", stalled), 1)

    assert started.is_set()
    assert closed.is_set()


def test_compose_scheduler_uses_worker_source_mount() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yaml"
    services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
    assert services["scheduler"]["volumes"] == services["worker"]["volumes"]
    assert services["scheduler"]["volumes"] == ["./app:/app/app"]


@pytest.mark.asyncio
async def test_maintenance_tasks_registered_with_label_schedule_source() -> None:
    from app.workers.broker import broker
    from app.workers.scheduler import scheduler
    from taskiq.schedule_sources.label_based import LabelScheduleSource

    expected = {
        "app.workers.occasion_reminders:deliver_occasion_reminders": "* * * * *",
        "app.workers.expo_receipts:check_expo_receipts": "* * * * *",
        "app.workers.account_requests:deliver_account_request_emails": "* * * * *",
        "app.workers.auto_approve:run_auto_approve": "*/15 * * * *",
        "app.workers.chat_notifications:deliver_chat_notifications": "* * * * *",
        "app.workers.chat_notifications:deliver_chat_live_messages": "* * * * *",
        "app.workers.expiry:run_expire_stale": "*/10 * * * *",
        "app.workers.expiry:cancel_overdue_orders": "0 0 * * *",
        "app.workers.expiry:run_purge_refresh_tokens": "0 * * * *",
        "app.workers.gateway_reconciliation:run_gateway_reconciliation": "*/5 * * * *",
        "app.workers.media_cleanup:run_media_cleanup": "0 * * * *",
        "app.workers.operations:purge_write_operations": "15 * * * *",
        "app.workers.order_notifications:deliver_order_notifications": "* * * * *",
        "app.workers.push_deliveries:deliver_push_notifications": "* * * * *",
        "app.workers.receipts:deliver_pending_receipts": "*/5 * * * *",
        "app.workers.reconciliation:reconcile_ledger": "0 3 * * *",
    }
    assert scheduler.broker is broker
    assert len(scheduler.sources) == 1
    source = scheduler.sources[0]
    assert isinstance(source, LabelScheduleSource)
    assert source.broker is broker
    assert set(broker.get_all_tasks()) == set(expected)

    await source.startup()
    discovered = await source.get_schedules()
    assert {task.task_name: task.cron for task in discovered} == expected
