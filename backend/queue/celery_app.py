from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

try:
    from celery import Celery
except Exception:  # pragma: no cover - runtime guard when celery is unavailable
    Celery = None  # type: ignore


def _as_bool(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _broker_url() -> str:
    return (
        (os.getenv("CELERY_BROKER_URL") or "").strip()
        or (os.getenv("RABBITMQ_URL") or "").strip()
        or "amqp://guest:guest@127.0.0.1:5672//"
    )


def is_celery_enabled() -> bool:
    if Celery is None:
        return False
    explicit = os.getenv("CELERY_ENABLED")
    if explicit is not None:
        return _as_bool(explicit, default=False)
    configured_broker = (
        (os.getenv("CELERY_BROKER_URL") or "").strip()
        or (os.getenv("RABBITMQ_URL") or "").strip()
    )
    if not configured_broker:
        return False
    broker = configured_broker.lower()
    return broker.startswith("amqp://") or broker.startswith("amqps://")


def use_celery_for_outbox() -> bool:
    return is_celery_enabled() and _as_bool(
        os.getenv("CELERY_OUTBOX_ENABLED", "1"),
        default=True,
    )


def _beat_schedule() -> Dict[str, Dict[str, object]]:
    if not use_celery_for_outbox():
        return {}
    interval = max(1.0, float(os.getenv("MINIO_OUTBOX_POLL_SEC", "5")))
    return {
        "minio-outbox-tick": {
            "task": "chatui.minio.outbox.tick",
            "schedule": interval,
        }
    }


if Celery is not None:
    is_windows = os.name == "nt"
    default_pool = os.getenv(
        "CELERY_WORKER_POOL",
        "solo" if is_windows else "prefork",
    )
    default_concurrency = _safe_int(
        os.getenv("CELERY_WORKER_CONCURRENCY", "1" if is_windows else "4"),
        1 if is_windows else 4,
    )

    celery_app = Celery("chat_ui")
    celery_app.conf.update(
        broker_url=_broker_url(),
        result_backend=(os.getenv("CELERY_RESULT_BACKEND") or "rpc://"),
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        worker_pool=default_pool,
        worker_concurrency=default_concurrency,
        task_default_queue=(os.getenv("CELERY_DEFAULT_QUEUE") or "chatui.default"),
        beat_schedule=_beat_schedule(),
    )
    celery_app.autodiscover_tasks(["backend.queue"])
else:  # pragma: no cover
    celery_app = None  # type: ignore
