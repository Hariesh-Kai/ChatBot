from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

import backend.llm.loader as llm_loader
from backend.llm.hf_cache_utils import resolve_local_snapshot
from backend.llm.loader import GGUF_MODELS, HF_MODELS, resolve_gguf_model_path
from backend.llm.model_config_store import GGUF_DIR, HF_CACHE_DIR
from backend.rag.preprocessor_registry import (
    DEFAULT_RAG_PREPROCESSOR,
    get_rag_preprocessor_options,
)


_KNOWN_HF_ASSETS: Dict[str, Dict[str, str]] = {
    "base_qwen_3b": {
        "repo_id": "Qwen/Qwen2.5-3B-Instruct",
        "group": "chat",
        "component": "base",
        "label": "Qwen 2.5 3B Instruct",
        "note": "Primary Hugging Face base chat model.",
    },
    "base_qwen_7b": {
        "repo_id": "Qwen/Qwen2.5-7B-Instruct",
        "group": "chat",
        "component": "base",
        "label": "Qwen 2.5 7B Instruct",
        "note": "Larger Hugging Face base chat model.",
    },
    "embedding_bge_m3": {
        "repo_id": "BAAI/bge-m3",
        "group": "retrieval",
        "component": "embedding",
        "label": "BGE M3",
        "note": "Embedding model used by ingestion and retrieval.",
    },
    "intent_classifier": {
        "repo_id": "facebook/bart-large-mnli",
        "group": "reasoning",
        "component": "intent_classifier",
        "label": "BART Large MNLI",
        "note": "Zero-shot intent classifier.",
    },
    "unstructured_table_transformer": {
        "repo_id": "microsoft/table-transformer-structure-recognition",
        "group": "preprocessing",
        "component": "unstructured_table",
        "label": "Table Transformer",
        "note": "Unstructured hi_res table structure model.",
    },
}

_KNOWN_GGUF_ASSETS: Dict[str, Dict[str, str]] = {
    "agent_qwen_0_5b_q4": {
        "group": "reasoning",
        "component": "agent_router",
        "label": "Qwen 2.5 0.5B Agent GGUF",
        "note": "Hidden routing, extraction, and release-gate model.",
    },
    "lite_qwen_3b_q4": {
        "group": "chat",
        "component": "lite",
        "label": "Qwen 2.5 3B GGUF",
        "note": "Recommended single local model for CPU-first developer setups.",
    },
    "lite_qwen_1_5b_q4": {
        "group": "chat",
        "component": "lite",
        "label": "Qwen 2.5 1.5B GGUF",
        "note": "CPU-first local chat model.",
    },
    "lite_qwen_q4": {
        "group": "chat",
        "component": "lite",
        "label": "Qwen 2.5 7B GGUF",
        "note": "Larger GGUF fallback chat model.",
    },
    "lite_llama_8b": {
        "group": "chat",
        "component": "lite",
        "label": "Llama 3.1 8B GGUF",
        "note": "Alternate GGUF chat model.",
    },
}

_UNSTRUCTURED_YOLO_REPO_ID = "unstructuredio/yolo_x_layout"
_UNSTRUCTURED_YOLO_FILES = (
    "yolox_l0.05_quantized.onnx",
    "yolox_l0.05.onnx",
)
_FLASHRANK_MODEL_NAME = "ms-marco-MiniLM-L-12-v2"


def _safe_cache_repo_to_repo_id(repo_dir_name: str) -> Optional[str]:
    if not repo_dir_name.startswith("models--"):
        return None
    body = repo_dir_name[len("models--") :]
    owner, sep, name = body.partition("--")
    if not owner or not sep or not name:
        return None
    return f"{owner}/{name}"


def _snapshot_entry(
    *,
    asset_id: str,
    repo_id: str,
    group: str,
    component: str,
    label: str,
    note: str,
    loaded: bool = False,
) -> Dict[str, Any]:
    location = resolve_local_snapshot(HF_CACHE_DIR, repo_id)
    return {
        "id": asset_id,
        "kind": "hf_snapshot",
        "group": group,
        "component": component,
        "label": label,
        "repo_id": repo_id,
        "ready": bool(location),
        "loaded": bool(loaded),
        "location": location,
        "note": note,
        "source": "known",
    }


def _gguf_entry(
    *,
    model_id: str,
    group: str,
    component: str,
    label: str,
    note: str,
) -> Dict[str, Any]:
    location = resolve_gguf_model_path(model_id)
    return {
        "id": model_id,
        "kind": "gguf",
        "group": group,
        "component": component,
        "label": label,
        "model_id": model_id,
        "ready": bool(location and Path(location).exists()),
        "loaded": model_id in getattr(llm_loader, "_llama_cache", {}),
        "location": location,
        "note": note,
        "source": "known",
    }


def _package_entry(
    *,
    asset_id: str,
    component: str,
    label: str,
    module_name: str,
    note: str,
) -> Dict[str, Any]:
    return {
        "id": asset_id,
        "kind": "python_package",
        "group": "preprocessing",
        "component": component,
        "label": label,
        "module_name": module_name,
        "ready": importlib.util.find_spec(module_name) is not None,
        "loaded": False,
        "location": module_name,
        "note": note,
        "source": "runtime",
    }


def _dir_entry(
    *,
    asset_id: str,
    group: str,
    component: str,
    label: str,
    directory: Path,
    note: str,
) -> Dict[str, Any]:
    ready = directory.exists() and any(directory.iterdir())
    return {
        "id": asset_id,
        "kind": "directory",
        "group": group,
        "component": component,
        "label": label,
        "ready": ready,
        "loaded": False,
        "location": str(directory),
        "note": note,
        "source": "known",
    }


def _discovered_gguf_entries(known_locations: set[str]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    base_dir = Path(GGUF_DIR)
    if not base_dir.exists():
        return entries

    for path in sorted(base_dir.glob("*.gguf")):
        resolved = str(path.resolve())
        if resolved in known_locations:
            continue
        entries.append(
            {
                "id": f"gguf::{path.name}",
                "kind": "gguf",
                "group": "discovered",
                "component": "local_file",
                "label": path.name,
                "ready": True,
                "loaded": False,
                "location": str(path),
                "note": "Local GGUF file found in models/gguf but not registered to a chat mode.",
                "source": "discovered",
            }
        )
    return entries


def _discovered_hf_entries(known_repo_ids: set[str]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    base_dir = Path(HF_CACHE_DIR)
    if not base_dir.exists():
        return entries

    for repo_dir in sorted(base_dir.glob("models--*")):
        repo_id = _safe_cache_repo_to_repo_id(repo_dir.name)
        if not repo_id or repo_id in known_repo_ids:
            continue
        snapshot = resolve_local_snapshot(HF_CACHE_DIR, repo_id)
        entries.append(
            {
                "id": f"hf::{repo_id}",
                "kind": "hf_snapshot",
                "group": "discovered",
                "component": "cached_repo",
                "label": repo_id,
                "repo_id": repo_id,
                "ready": bool(snapshot),
                "loaded": False,
                "location": snapshot or str(repo_dir),
                "note": "Cached Hugging Face repo found on disk.",
                "source": "discovered",
            }
        )
    return entries


def build_model_inventory() -> Dict[str, Any]:
    inventory: List[Dict[str, Any]] = []

    known_repo_ids: set[str] = set()
    known_gguf_locations: set[str] = set()

    for model_id in dict(GGUF_MODELS).keys():
        meta = _KNOWN_GGUF_ASSETS.get(
            model_id,
            {
                "group": "chat",
                "component": "lite",
                "label": model_id,
                "note": "Registered GGUF chat model.",
            },
        )
        entry = _gguf_entry(
            model_id=model_id,
            group=str(meta["group"]),
            component=str(meta["component"]),
            label=str(meta["label"]),
            note=str(meta["note"]),
        )
        inventory.append(entry)
        if entry.get("location"):
            known_gguf_locations.add(str(Path(entry["location"]).resolve()))

    for model_id, repo_id in dict(HF_MODELS).items():
        meta = _KNOWN_HF_ASSETS.get(
            model_id,
            {
                "group": "chat",
                "component": "base",
                "label": model_id,
                "note": "Registered Hugging Face chat model.",
            },
        )
        local_path = Path(repo_id or "")
        if repo_id and local_path.exists():
            entry = {
                "id": model_id,
                "kind": "hf_local_path",
                "group": str(meta["group"]),
                "component": str(meta["component"]),
                "label": str(meta["label"]),
                "repo_id": repo_id,
                "ready": True,
                "loaded": model_id in getattr(llm_loader, "_hf_model_cache", {}),
                "location": str(local_path),
                "note": str(meta["note"]),
                "source": "registered",
            }
        else:
            entry = _snapshot_entry(
                asset_id=model_id,
                repo_id=repo_id,
                group=str(meta["group"]),
                component=str(meta["component"]),
                label=str(meta["label"]),
                note=str(meta["note"]),
                loaded=model_id in getattr(llm_loader, "_hf_model_cache", {}),
            )
        inventory.append(entry)
        known_repo_ids.add(repo_id)

    # Auxiliary runtime models used outside chat-mode assignment.
    inventory.append(
        _snapshot_entry(
            asset_id="embedding_bge_m3",
            repo_id="BAAI/bge-m3",
            group="retrieval",
            component="embedding",
            label="BGE M3",
            note="Embedding model used by ingestion and retrieval.",
        )
    )
    known_repo_ids.add("BAAI/bge-m3")

    inventory.append(
        _snapshot_entry(
            asset_id="intent_classifier",
            repo_id="facebook/bart-large-mnli",
            group="reasoning",
            component="intent_classifier",
            label="BART Large MNLI",
            note="Intent classifier used by query routing.",
            loaded=getattr(llm_loader, "_intent_classifier", None) is not None,
        )
    )
    known_repo_ids.add("facebook/bart-large-mnli")

    inventory.append(
        _dir_entry(
            asset_id="flashrank_reranker",
            group="retrieval",
            component="reranker",
            label="FlashRank MiniLM",
            directory=Path(HF_CACHE_DIR) / "flashrank" / _FLASHRANK_MODEL_NAME,
            note="Reranker used to reorder retrieved passages.",
        )
    )

    inventory.append(
        _snapshot_entry(
            asset_id="unstructured_table_transformer",
            repo_id="microsoft/table-transformer-structure-recognition",
            group="preprocessing",
            component="unstructured_table",
            label="Table Transformer",
            note="Unstructured hi_res table structure model.",
        )
    )
    known_repo_ids.add("microsoft/table-transformer-structure-recognition")

    yolo_snapshot = resolve_local_snapshot(HF_CACHE_DIR, _UNSTRUCTURED_YOLO_REPO_ID)
    yolo_ready = bool(
        yolo_snapshot
        and all((Path(yolo_snapshot) / filename).exists() for filename in _UNSTRUCTURED_YOLO_FILES)
    )
    inventory.append(
        {
            "id": "unstructured_yolox_layout",
            "kind": "hf_snapshot",
            "group": "preprocessing",
            "component": "unstructured_layout",
            "label": "YOLOX Layout",
            "repo_id": _UNSTRUCTURED_YOLO_REPO_ID,
            "ready": yolo_ready,
            "loaded": False,
            "location": yolo_snapshot,
            "note": "Unstructured layout model assets.",
            "source": "known",
        }
    )
    known_repo_ids.add(_UNSTRUCTURED_YOLO_REPO_ID)

    inventory.append(
        _dir_entry(
            asset_id="docling_models",
            group="preprocessing",
            component="docling_assets",
            label="Docling Runtime Models",
            directory=Path(HF_CACHE_DIR) / "docling" / "models",
            note="Docling layout, OCR and table assets.",
        )
    )

    preprocessor_notes = get_rag_preprocessor_options()
    inventory.extend(
        [
            _package_entry(
                asset_id="preprocessor_pypdf_text",
                component="pypdf_text",
                label="PyPDF",
                module_name="pypdf",
                note=preprocessor_notes.get("pypdf_text", "PyPDF text extraction."),
            ),
            _package_entry(
                asset_id="preprocessor_pymupdf4llm",
                component="pymupdf4llm",
                label="PyMuPDF4LLM",
                module_name="pymupdf4llm",
                note=preprocessor_notes.get("pymupdf4llm", "PyMuPDF4LLM preprocessor."),
            ),
            _package_entry(
                asset_id="preprocessor_unstructured",
                component="unstructured",
                label="Unstructured",
                module_name="unstructured",
                note=preprocessor_notes.get("unstructured", "Unstructured preprocessor."),
            ),
            _package_entry(
                asset_id="preprocessor_docling",
                component="docling",
                label="Docling",
                module_name="docling",
                note=preprocessor_notes.get("docling", "Docling preprocessor."),
            ),
        ]
    )

    inventory.extend(_discovered_gguf_entries(known_gguf_locations))
    inventory.extend(_discovered_hf_entries(known_repo_ids))

    inventory.sort(
        key=lambda item: (
            str(item.get("group") or ""),
            str(item.get("component") or ""),
            str(item.get("label") or item.get("id") or ""),
        )
    )

    return {
        "default_rag_preprocessor": DEFAULT_RAG_PREPROCESSOR,
        "inventory": inventory,
    }
