# Celery + RabbitMQ (Commit Jobs)

## Environment
Set in `.env`:

```env
CELERY_ENABLED=1
CELERY_BROKER_URL=amqp://guest:guest@127.0.0.1:5672//
CELERY_RESULT_BACKEND=rpc://
CELERY_OUTBOX_ENABLED=1
```

## Start Commands (Windows)

```powershell
venv\Scripts\activate
celery -A backend.queue.celery_app:celery_app worker --loglevel=info -Q chatui.default --pool=solo --concurrency=1
celery -A backend.queue.celery_app:celery_app beat --loglevel=info
```

Notes:
- On Windows, use `--pool=solo --concurrency=1` to avoid `billiard`/`WinError 5` worker crashes.
- On Linux, you can use the default prefork pool.

## What Runs on Celery
- `chatui.rag.commit`: full commit pipeline (chunking, enrich, ingest, active-doc save).
- `chatui.minio.outbox.tick`: scheduled MinIO outbox retry processing.
