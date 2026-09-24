"""Scheduler entry point for cron-labelled maintenance tasks."""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources.label_based import LabelScheduleSource

from app.workers.broker import broker

scheduler = TaskiqScheduler(broker, [LabelScheduleSource(broker)])
