"""Errors are bounded, scrubbed and formatted consistently across processes."""

import json
import logging
import sys

from app.core.logging import configure_logging
from app.workers.runtime import worker_commands


def formatted(record: logging.LogRecord) -> str:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        formatter = root.handlers[0].formatter
        assert formatter is not None
        return formatter.format(record)
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_error_block_is_bounded_and_cannot_inject_lines():
    record = logging.LogRecord(
        "app.test", logging.ERROR, __file__, 12, "Job failed\nforged log\r" + "x" * 10000, (), None
    )
    record.request_id = "request-123"
    output = formatted(record)
    lines = output.splitlines()
    assert lines[0] == "-" * 47 + "ERROR" + "-" * 40
    assert lines[-1] == "-" * 95
    assert len(lines[1:-1]) <= 6
    assert "ERROR" in lines[1]
    assert "request-123" in output
    assert len(output) < 4000


def test_exception_block_omits_raw_exception_and_locals():
    try:
        raise ValueError("private@example.com arbitrary-sensitive-payload")
    except ValueError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 42, "Email job failed", (), sys.exc_info()
        )
    output = formatted(record)
    assert "ValueError" in output
    assert "test_exception_block_omits_raw_exception_and_locals" in output
    assert "private@example.com" not in output
    assert "arbitrary-sensitive-payload" not in output


def test_log_message_redacts_urls_emails_and_named_secrets():
    record = logging.LogRecord(
        "app.test",
        logging.ERROR,
        __file__,
        51,
        "Failed https://files.test/file?signature=secret-value private@example.com "
        "password=hunter2 otp=12345",
        (),
        None,
    )
    output = formatted(record)
    for private in ("signature=secret-value", "private@example.com", "hunter2", "12345"):
        assert private not in output


def test_info_logs_remain_structured_and_errors_remain_errors():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 61, "Job finished", (), None)
    assert json.loads(formatted(record))["level"] == "INFO"
    record.levelno, record.levelname = logging.ERROR, "ERROR"
    assert "ERROR" in formatted(record)
    assert record.levelno == logging.ERROR


def test_taskiq_does_not_replace_application_logging():
    for command in worker_commands():
        assert "--no-configure-logging" in command


def test_provider_exception_arguments_do_not_expose_raw_payload():
    record = logging.LogRecord(
        "taskiq.receiver",
        logging.ERROR,
        __file__,
        90,
        "Task failed: %s",
        (ValueError("private payload"),),
        None,
    )
    output = formatted(record)
    assert "private payload" not in output
    assert "ValueError" in output


def test_configuring_logging_does_not_enable_sql_parameter_logs():
    sql_logger = logging.getLogger("sqlalchemy.engine")
    previous = sql_logger.level
    try:
        formatted(logging.LogRecord("app.test", logging.INFO, __file__, 100, "Ready", (), None))
        assert not sql_logger.isEnabledFor(logging.INFO)
    finally:
        sql_logger.setLevel(previous)
