# backend/storage/minio_client.py

import os
from pathlib import Path
from typing import Optional

from minio import Minio
from minio.error import S3Error
from threading import Lock
from hashlib import sha256

_CLIENT_LOCK = Lock()

# ============================================================
# CONFIG — LAZY LOAD (FIXED)
# ============================================================
# We read these, but we DO NOT raise errors at the module level.
# This prevents the server from crashing on startup if vars are missing.

def _get_config():
    return {
        "endpoint": os.getenv("MINIO_ENDPOINT"),
        "access_key": os.getenv("MINIO_ACCESS_KEY"),
        "secret_key": os.getenv("MINIO_SECRET_KEY"),
        "bucket": os.getenv("MINIO_BUCKET", "kavin-documents"),
        "secure": os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
    }

# ============================================================
# CLIENT (SINGLETON)
# ============================================================

_minio_client: Optional[Minio] = None

def get_minio_client() -> Minio:
    global _minio_client
    
    # 1. Load config inside the function
    conf = _get_config()
    # 🔥 DEBUG: PRINT WHAT WE SEE
    print("------------------------------------------------")
    print(f"DEBUG: Endpoint   = '{conf['endpoint']}'")
    print(f"DEBUG: Access Key = '{conf['access_key']}'")
    print("------------------------------------------------")
    
    # 2. Validate ONLY when needed (Lazy Validation)
    if not conf["endpoint"]:
        # Log a warning instead of crashing the whole app, or handle gracefully
        print("⚠️  MinIO Config Missing: MINIO_ENDPOINT not set. Uploads will fail.")
        return None # Return None so the caller can handle it

    if not conf["access_key"] or not conf["secret_key"]:
        print("⚠️  MinIO Config Missing: Access/Secret keys not set.")
        return None

    if _minio_client is None:
        with _CLIENT_LOCK:
            if _minio_client is None:
                try:
                    _minio_client = Minio(
                        conf["endpoint"],
                        access_key=conf["access_key"],
                        secret_key=conf["secret_key"],
                        secure=conf["secure"],
                    )
                except Exception as e:
                    print(f"❌ Failed to initialize MinIO client: {e}")
                    return None

    return _minio_client

def _checksum(path: str) -> str:
    h = sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

# ============================================================
# BUCKET MANAGEMENT (RACE SAFE)
# ============================================================

def ensure_bucket() -> None:
    client = get_minio_client()
    if not client: return

    conf = _get_config()
    bucket_name = conf["bucket"]

    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except S3Error as e:
        if e.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    except Exception as e:
        print(f"⚠️  Bucket check failed: {e}")


# ============================================================
# PATH HELPERS
# ============================================================

def _object_path(document_id: str, revision: int, filename: str) -> str:
    clean_name = Path(filename).name
    return f"{document_id}/v{revision}/{clean_name}"


# ============================================================
# METADATA CHECK (ENTERPRISE SAFETY)
# ============================================================

def pdf_exists(*, document_id: str, revision: int, filename: str) -> bool:
    client = get_minio_client()
    if not client: return False # Fail safe

    ensure_bucket()
    
    conf = _get_config()
    object_name = _object_path(document_id, revision, filename)

    try:
        client.stat_object(conf["bucket"], object_name)
        return True
    except S3Error as e:
        if e.code == "NoSuchKey":
            return False
        raise
    except Exception:
        return False


# ============================================================
# UPLOAD
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
        raise RuntimeError("MinIO not configured. Cannot upload.")

    ensure_bucket()
    
    conf = _get_config()
    bucket_name = conf["bucket"]
    object_name = _object_path(document_id, revision, filename)

    if not overwrite and pdf_exists(
        document_id=document_id,
        revision=revision,
        filename=filename,
    ):
        raise RuntimeError(
            f"PDF already exists in MinIO for "
            f"document_id={document_id}, revision={revision}"
        )

    client.fput_object(
        bucket_name=bucket_name,
        object_name=object_name,
        file_path=local_path,
        content_type="application/pdf",
    )

    return f"{bucket_name}/{object_name}"


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
        raise RuntimeError("MinIO not configured. Cannot download.")

    conf = _get_config()
    object_name = _object_path(document_id, revision, filename)

    client.fget_object(
        bucket_name=conf["bucket"],
        object_name=object_name,
        file_path=local_path,
    )