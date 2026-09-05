from celery import Celery

from forge_erp.core.config import settings
from forge_erp.core.observability import configure_logging

configure_logging()
celery_app = Celery(
    "forge",
    broker=settings().redis_url,
    include=[
        "forge_erp.workers.outbox",
        "forge_erp.workers.catalog_import",
        "forge_erp.workers.assistant_retention",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    worker_hijack_root_logger=False,
    worker_log_format="%(message)s",
    worker_task_log_format="%(message)s",
    beat_schedule={
        "purge-assistant": {"task": "forge.assistant.retention", "schedule": 60.0},
        "poll-outbox": {"task": "forge.outbox.poll", "schedule": 5.0},
        "poll-catalog-import": {"task": "forge.catalog_import.poll", "schedule": 5.0},
    },
)
