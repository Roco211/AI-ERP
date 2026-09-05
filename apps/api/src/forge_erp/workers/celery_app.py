from celery import Celery

from forge_erp.core.config import settings

celery_app = Celery("forge", broker=settings().redis_url, include=["forge_erp.workers.outbox"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    beat_schedule={"poll-outbox": {"task": "forge.outbox.poll", "schedule": 5.0}},
)
