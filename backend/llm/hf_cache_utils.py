from __future__ import annotations

from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]


def resolve_local_snapshot(cache_dir: PathLike, repo_id: str) -> Optional[str]:
    """
    Resolve a Hugging Face repo_id to a local snapshot path inside cache_dir.

    Returns a filesystem path string if found, else None.
    """
    if not repo_id:
        return None

    cache_root = Path(cache_dir)
    safe_repo = repo_id.replace("/", "--")
    repo_dir = cache_root / f"models--{safe_repo}"

    refs_main = repo_dir / "refs" / "main"
    if refs_main.exists():
        rev = refs_main.read_text(encoding="utf-8").strip()
        if rev:
            snap = repo_dir / "snapshots" / rev
            if snap.exists():
                return str(snap)

    snapshots_dir = repo_dir / "snapshots"
    if snapshots_dir.exists():
        candidates = [p for p in snapshots_dir.iterdir() if p.is_dir()]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(candidates[0])

    return None


def require_local_snapshot(cache_dir: PathLike, repo_id: str) -> str:
    """
    Resolve a Hugging Face repo_id to a local snapshot path or raise.

    This keeps runtime components fully offline: if the model is not already
    cached under `models/hf_cache`, callers can fail fast instead of silently
    falling back to an online download attempt.
    """
    snapshot = resolve_local_snapshot(cache_dir, repo_id)
    if snapshot:
        return snapshot
    raise FileNotFoundError(
        f"Local Hugging Face snapshot not found for '{repo_id}' under '{Path(cache_dir)}'"
    )

