from __future__ import annotations

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.llm.model_config_store import (  # noqa: E402
    GGUF_DIR,
    ensure_model_paths,
    load_model_config,
    patch_model_registry_overrides,
    upsert_gguf_model,
)


REPO_ID = "Qwen/Qwen2.5-3B-Instruct-GGUF"
MODEL_ID = "lite_qwen_3b_q4"
FILENAME = "qwen2.5-3b-instruct-q4_k_m.gguf"


def main() -> int:
    ensure_model_paths()

    print(f"[MODEL-INSTALL] Downloading {REPO_ID} :: {FILENAME}")
    path = hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=GGUF_DIR,
        local_dir_use_symlinks=False,
    )

    print(f"[MODEL-INSTALL] Registering {MODEL_ID} -> {path}")
    upsert_gguf_model(model_id=MODEL_ID, path=path)
    patch_model_registry_overrides(
        {
            "lite": {"default": MODEL_ID, "fallback": MODEL_ID},
            "base": {"default": MODEL_ID, "cpu_fallback": MODEL_ID},
        }
    )

    cfg = load_model_config()
    print("[MODEL-INSTALL] Done.")
    print(f"[MODEL-INSTALL] GGUF path: {path}")
    print(f"[MODEL-INSTALL] Lite default: {cfg.get('model_registry_overrides', {}).get('lite', {}).get('default')}")
    print(f"[MODEL-INSTALL] Base default: {cfg.get('model_registry_overrides', {}).get('base', {}).get('default')}")
    print("[MODEL-INSTALL] If the backend is already running, the updated runtime will auto-sync on the next model request.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
