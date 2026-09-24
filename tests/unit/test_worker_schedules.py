"""The checked-in Taskiq entry points discover every maintenance schedule."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


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
        "app.workers.auto_approve:run_auto_approve": "*/15 * * * *",
        "app.workers.expiry:run_expire_stale": "*/10 * * * *",
        "app.workers.expiry:run_purge_refresh_tokens": "0 4 * * *",
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
