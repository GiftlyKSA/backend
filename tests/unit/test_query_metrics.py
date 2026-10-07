from types import SimpleNamespace
from unittest.mock import Mock


def test_query_metrics_count_work_without_retaining_sql_or_values(monkeypatch):
    from app.core import query_metrics

    clock = iter([1.0, 1.025, 2.0, 2.010])
    monkeypatch.setattr(query_metrics, "perf_counter", lambda: next(clock))
    metrics, token = query_metrics.begin_query_metrics()
    connection = SimpleNamespace(info={})
    try:
        for _ in range(2):
            query_metrics.before_query(
                connection, Mock(), "secret SQL", {"password": "secret"}, Mock(), False
            )
            query_metrics.after_query(
                connection, Mock(), "secret SQL", {"password": "secret"}, Mock(), False
            )
        assert metrics.count == 2
        assert round(metrics.elapsed_seconds, 3) == 0.035
        assert "secret" not in repr(metrics)
        assert connection.info == {}
    finally:
        query_metrics.end_query_metrics(token)


def test_query_metrics_are_isolated_between_requests():
    from app.core import query_metrics

    first, token = query_metrics.begin_query_metrics()
    first.count = 10
    query_metrics.end_query_metrics(token)
    second, token = query_metrics.begin_query_metrics()
    assert second.count == 0
    query_metrics.end_query_metrics(token)
