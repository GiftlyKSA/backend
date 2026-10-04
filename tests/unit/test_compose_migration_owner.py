"""Compose uses its successful migration job as the single migration owner."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from app import entrypoint


def test_compose_services_start_after_one_migration_owner() -> None:
    config = yaml.safe_load(Path("docker-compose.yaml").read_text(encoding="utf-8"))
    services = config["services"]
    assert services["migrate"]["entrypoint"] == ["python", "-m", "app.bootstrap_db"]
    for name in ("api", "worker", "scheduler"):
        assert services[name]["entrypoint"] == []
        assert services[name]["depends_on"]["migrate"] == {
            "condition": "service_completed_successfully"
        }


def test_default_image_retains_migration_failure_gate() -> None:
    image = Path("Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["python", "-m", "app.entrypoint"]' in image


def test_default_entrypoint_never_starts_service_after_migration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_service = Mock()
    monkeypatch.setattr(entrypoint, "upgrade_database", Mock(side_effect=RuntimeError("failed")))
    monkeypatch.setattr(entrypoint.os, "execvp", start_service)
    with pytest.raises(RuntimeError, match="failed"):
        entrypoint.main()
    start_service.assert_not_called()
