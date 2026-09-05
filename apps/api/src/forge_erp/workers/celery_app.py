from celery import Celery

from forge_erp.core.config import settings
from forge_erp.core.observability import configure_logging

configure_logging()
celery_app = Celery("forge", broker=settings().redis_url, include=["forge_erp.workers.outbox"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    worker_hijack_root_logger=False,
    worker_log_format="%(message)s",
    worker_task_log_format="%(message)s",
    beat_schedule={"poll-outbox": {"task": "forge.outbox.poll", "schedule": 5.0}},
)
