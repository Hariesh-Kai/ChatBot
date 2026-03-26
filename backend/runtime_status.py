from __future__ import annotations

import os
import socket
import sys
from typing import Any, Dict, Optional, Tuple
from importlib.metadata import PackageNotFoundError, version as pkg_version
from urllib.parse import urlparse

import torch

from backend.llm.loader import DEVICE as LLM_DEVICE
from backend.queue.celery_app import is_celery_enabled, use_celery_for_outbox


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_pkg_version(package_name: str) -> Optional[str]:
    try:
        return pkg_version(package_name)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def _masked_url(raw_url: Optional[str]) -> Optional[str]:
    value = (raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
        if not parsed.scheme:
            return None
        host = parsed.hostname or "unknown-host"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://***:***@{host}{port}{parsed.path or ''}"
    except Exception:
        return None


def _tcp_check(host: str, port: int, timeout_sec: float = 1.0) -> Tuple[bool, Optional[str]]:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True, None
    except Exception as e:
        return False, str(e)


def _resolve_rabbitmq_target() -> Dict[str, Any]:
    broker_url = ""
    broker_source = None
    for env_name in ("RABBITMQ_URL", "CELERY_BROKER_URL"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            broker_url = value
            broker_source = env_name
            break

    host = (os.getenv("RABBITMQ_HOST") or "").strip() or None
    port = _safe_int(os.getenv("RABBITMQ_PORT"), 5672)

    if broker_url:
        parsed = urlparse(broker_url)
        scheme = (parsed.scheme or "").lower()
        if scheme in ("amqp", "amqps", "pyamqp", "rabbitmq"):
            host = parsed.hostname or host or "127.0.0.1"
            if parsed.port:
                port = int(parsed.port)
            return {
                "enabled": True,
                "host": host,
                "port": port,
                "broker_url": broker_url,
                "source": broker_source,
                "kind": "rabbitmq",
            }
        return {
            "enabled": False,
            "host": host or "127.0.0.1",
            "port": port,
            "broker_url": broker_url,
            "source": broker_source,
            "kind": "non_rabbitmq_broker",
        }

    return {
        "enabled": bool(host),
        "host": host or "127.0.0.1",
        "port": port,
        "broker_url": None,
        "source": None,
        "kind": "rabbitmq",
    }


def get_gpu_status() -> Dict[str, Any]:
    available = bool(torch.cuda.is_available())
    devices = []
    if available:
        count = torch.cuda.device_count()
        for idx in range(count):
            props = torch.cuda.get_device_properties(idx)
            devices.append(
                {
                    "index": idx,
                    "name": torch.cuda.get_device_name(idx),
                    "total_memory_gb": round(float(props.total_memory) / (1024 ** 3), 2),
                }
            )
    return {
        "available": available,
        "count": len(devices),
        "devices": devices,
        "llm_device": LLM_DEVICE,
        "auto_selected": LLM_DEVICE == "cuda" and available,
    }


def get_rabbitmq_status() -> Dict[str, Any]:
    target = _resolve_rabbitmq_target()
    masked = _masked_url(target.get("broker_url"))
    # Do not expose raw credentials in API responses.
    target = {**target, "broker_url": masked}
    if target.get("kind") == "non_rabbitmq_broker":
        return {
            **target,
            "connected": False,
            "status": "not_rabbitmq",
            "error": "Broker URL is set but not AMQP/RabbitMQ.",
        }

    if not target.get("enabled"):
        return {
            **target,
            "connected": False,
            "status": "disabled",
            "error": "RabbitMQ is not configured.",
        }

    connected, error = _tcp_check(str(target["host"]), int(target["port"]))
    return {
        **target,
        "connected": connected,
        "status": "ok" if connected else "error",
        "error": error,
    }


def get_software_status() -> Dict[str, Any]:
    celery_mode = is_celery_enabled()
    outbox_via_celery = use_celery_for_outbox()

    packages = {
        "python": sys.version.split(" ")[0],
        "fastapi": _safe_pkg_version("fastapi"),
        "uvicorn": _safe_pkg_version("uvicorn"),
        "celery": _safe_pkg_version("celery"),
        "redis": _safe_pkg_version("redis"),
        "minio": _safe_pkg_version("minio"),
        "torch": _safe_pkg_version("torch"),
        "transformers": _safe_pkg_version("transformers"),
        "langchain-postgres": _safe_pkg_version("langchain-postgres"),
        "llama-cpp-python": _safe_pkg_version("llama-cpp-python"),
    }

    functions = [
        {
            "id": "rag_commit_pipeline",
            "name": "RAG Commit Pipeline",
            "software": "Celery worker" if celery_mode else "In-process worker thread",
            "task_name": "chatui.rag.commit" if celery_mode else None,
            "description": "Chunking, metadata enrichment, embedding, indexing, active document update.",
        },
        {
            "id": "minio_outbox_retry",
            "name": "MinIO Outbox Retry",
            "software": (
                "Celery beat + worker"
                if outbox_via_celery
                else "In-process outbox worker"
            ),
            "task_name": "chatui.minio.outbox.tick" if outbox_via_celery else None,
            "description": "Retries failed/offline MinIO uploads from durable outbox.",
        },
        {
            "id": "gpu_auto_connect",
            "name": "GPU Auto Connect",
            "software": "PyTorch device detection",
            "task_name": None,
            "description": "Auto-selects CUDA when available; otherwise CPU fallback.",
        },
    ]

    return {
        "packages": packages,
        "functions": functions,
        "queue": {
            "celery_enabled": celery_mode,
            "outbox_via_celery": outbox_via_celery,
            "default_queue": os.getenv("CELERY_DEFAULT_QUEUE", "chatui.default"),
            "broker_source": (
                "CELERY_BROKER_URL"
                if (os.getenv("CELERY_BROKER_URL") or "").strip()
                else ("RABBITMQ_URL" if (os.getenv("RABBITMQ_URL") or "").strip() else None)
            ),
        },
    }


def get_worker_status() -> Dict[str, Any]:
    commit_jobs: Dict[str, str] = {}
    outbox_counts: Dict[str, int] = {}
    outbox_error = None

    try:
        from backend.rag.commit_worker import get_active_commit_jobs

        commit_jobs = get_active_commit_jobs()
    except Exception as e:
        outbox_error = f"commit_worker_unavailable: {e}"

    try:
        from backend.storage.minio_outbox import get_outbox_summary

        summary = get_outbox_summary()
        outbox_counts = dict((summary or {}).get("counts", {}) or {})
    except Exception as e:
        msg = str(e)
        outbox_error = f"{outbox_error}; outbox_unavailable: {msg}" if outbox_error else msg

    return {
        "celery_enabled": is_celery_enabled(),
        "outbox_via_celery": use_celery_for_outbox(),
        "active_commit_jobs": len(commit_jobs),
        "commit_jobs": commit_jobs,
        "outbox_counts": outbox_counts,
        "error": outbox_error,
    }


def get_runtime_status() -> Dict[str, Any]:
    return {
        "gpu": get_gpu_status(),
        "rabbitmq": get_rabbitmq_status(),
        "workers": get_worker_status(),
        "software": get_software_status(),
    }
