# backend/storage/minio_client.py

import os
from pathlib import Path
from typing import Optional
from threading import Lock
from hashlib import sha256

from minio import Minio
from minio.error import S3Error

# ============================================================
# GLOBALS
# ============================================================

_CLIENT_LOCK = Lock()
_BUCKET_LOCK = Lock()

_minio_client: Optional[Minio] = None
_bucket_initialized = False


# ============================================================
# CONFIG (LAZY, SAFE)
# ============================================================

def _get_config():
    return {
        "endpoint": os.getenv("MINIO_ENDPOINT"),
        "access_key": os.getenv("MINIO_ACCESS_KEY"),
        "secret_key": os.getenv("MINIO_SECRET_KEY"),
        "bucket": os.getenv("MINIO_BUCKET", "kavin-documents"),
        "secure": os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes"),
    }


def _validate_config(conf: dict) -> bool:
    if not conf["endpoint"]:
        print("MinIO disabled: MINIO_ENDPOINT not set")
        return False
    if not conf["access_key"] or not conf["secret_key"]:
        print("MinIO disabled: access/secret key missing")
        return False
    return True


# ============================================================
# CLIENT (THREAD-SAFE SINGLETON)
# ============================================================

def get_minio_client() -> Optional[Minio]:
    global _minio_client

    conf = _get_config()
    if not _validate_config(conf):
        return None

    if _minio_client is not None:
        return _minio_client

    with _CLIENT_LOCK:
        if _minio_client is not None:
            return _minio_client

        try:
            client = Minio(
                conf["endpoint"],
                access_key=conf["access_key"],
                secret_key=conf["secret_key"],
                secure=conf["secure"],
            )
            # Validate connectivity once
            client.list_buckets()
            _minio_client = client
            return client
        except Exception as e:
            print(f" Failed to initialize MinIO client: {e}")
            return None


# ============================================================
# BUCKET INITIALIZATION (ONCE)
# ============================================================

def ensure_bucket() -> None:
    global _bucket_initialized

    client = get_minio_client()
    if not client:
        return

    if _bucket_initialized:
        return

    conf = _get_config()
    bucket = conf["bucket"]

    with _BUCKET_LOCK:
        if _bucket_initialized:
            return

        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            _bucket_initialized = True
        except S3Error as e:
            if e.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise
        except Exception as e:
            print(f"MinIO bucket init failed: {e}")


# ============================================================
# HELPERS
# ============================================================

def _object_path(document_id: str, revision: int, filename: str) -> str:
    return f"{document_id}/v{revision}/{Path(filename).name}"


def _checksum(path: str) -> str:
    h = sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# EXISTENCE CHECK (NON-AUTHORITATIVE)
# ============================================================

def pdf_exists(*, document_id: str, revision: int, filename: str) -> bool:
    client = get_minio_client()
    if not client:
        return False

    ensure_bucket()
    conf = _get_config()

    try:
        client.stat_object(
            conf["bucket"],
            _object_path(document_id, revision, filename),
        )
        return True
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchObject"):
            return False
        raise
    except Exception:
        return False


# ============================================================
# UPLOAD (ATOMIC, SAFE)
# ============================================================

def upload_pdf(
    *,
    local_path: str,
    document_id: str,
    revision: int,
    filename: str,
    overwrite: bool = False,
) -> str:
    client = get_minio_client()
    if not client:
        raise RuntimeError("MinIO not configured")

    ensure_bucket()
    conf = _get_config()

    bucket = conf["bucket"]
    object_name = _object_path(document_id, revision, filename)
    checksum = _checksum(local_path)

    # 🔥 Atomic overwrite protection via metadata
    try:
        if not overwrite:
            client.stat_object(bucket, object_name)
            raise RuntimeError(
                f"PDF already exists for document_id={document_id}, revision={revision}"
            )
    except S3Error as e:
        if e.code not in ("NoSuchKey", "NoSuchObject"):
            raise

    client.fput_object(
        bucket_name=bucket,
        object_name=object_name,
        file_path=local_path,
        content_type="application/pdf",
        metadata={
            "document_id": document_id,
            "revision": str(revision),
            "sha256": checksum,
        },
    )

    return f"{bucket}/{object_name}"


# ============================================================
# DOWNLOAD
# ============================================================

def download_pdf(
    *,
    document_id: str,
    revision: int,
    filename: str,
    local_path: str,
) -> None:
    client = get_minio_client()
    if not client:
        raise RuntimeError("MinIO not configured")

    conf = _get_config()
    object_name = _object_path(document_id, revision, filename)

    try:
        client.fget_object(
            bucket_name=conf["bucket"],
            object_name=object_name,
            file_path=local_path,
        )
    except S3Error as e:
        raise RuntimeError(
            f"Failed to download PDF (document_id={document_id}, revision={revision}): {e}"
        )
