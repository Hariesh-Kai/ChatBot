from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODELS_DIR = PROJECT_ROOT / "models"
HF_CACHE_DIR = MODELS_DIR / "hf_cache"
GGUF_DIR = MODELS_DIR / "gguf"
DOCLING_CACHE_DIR = HF_CACHE_DIR / "docling"
FLASHRANK_CACHE_DIR = HF_CACHE_DIR / "flashrank"
MANIFEST_PATH = MODELS_DIR / "download_manifest.json"


def configure_env() -> None:
    for path in (MODELS_DIR, HF_CACHE_DIR, GGUF_DIR, DOCLING_CACHE_DIR, FLASHRANK_CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_CACHE_DIR))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("DOCLING_CACHE_DIR", str(DOCLING_CACHE_DIR))


configure_env()

from huggingface_hub import hf_hub_download, login, snapshot_download  # noqa: E402
from backend.llm.model_config_store import (  # noqa: E402
    ensure_model_paths,
    patch_model_registry_overrides,
    upsert_gguf_model,
    upsert_hf_model,
)


@dataclass(frozen=True)
class GGUFSpec:
    model_id: str
    repo_id: str
    filename: str
    target_name: str
    task: str
    component: str
    required: bool
    note: str


@dataclass(frozen=True)
class HFSnapshotSpec:
    name: str
    repo_id: str
    task: str
    component: str
    required: bool
    note: str


@dataclass(frozen=True)
class AuxAssetSpec:
    name: str
    task: str
    component: str
    kind: str
    required: bool
    note: str


GGUF_SPECS: tuple[GGUFSpec, ...] = (
    GGUFSpec(
        model_id="lite_qwen_1_5b_q4",
        repo_id="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
        target_name="qwen2.5-1.5b-instruct-q4_k_m.gguf",
        task="chat_generation",
        component="lite",
        required=True,
        note="Recommended CPU-first local chat model for this machine.",
    ),
    GGUFSpec(
        model_id="lite_qwen_q4",
        repo_id="Qwen/Qwen2.5-7B-Instruct-GGUF",
        filename="qwen2.5-7b-instruct-q4_k_m.gguf",
        target_name="qwen2.5-7b-instruct-q4_k_m.gguf",
        task="chat_generation",
        component="lite_fallback",
        required=False,
        note="Larger GGUF fallback for higher quality if RAM allows.",
    ),
    GGUFSpec(
        model_id="lite_llama_8b",
        repo_id="AI-Engine/Meta-Llama-3.1-8B-Instruct-GGUF",
        filename="Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        target_name="Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        task="chat_generation",
        component="lite_alternate",
        required=False,
        note="Legacy alternate lite model. May require extra Hugging Face access terms.",
    ),
)

HF_SNAPSHOT_SPECS: tuple[HFSnapshotSpec, ...] = (
    HFSnapshotSpec(
        name="base_qwen_3b",
        repo_id="Qwen/Qwen2.5-3B-Instruct",
        task="chat_generation",
        component="base",
        required=True,
        note="Built-in base CPU fallback HF model.",
    ),
    HFSnapshotSpec(
        name="base_qwen_7b",
        repo_id="Qwen/Qwen2.5-7B-Instruct",
        task="chat_generation",
        component="base_large",
        required=False,
        note="Built-in larger base HF model.",
    ),
    HFSnapshotSpec(
        name="embedding_bge_m3",
        repo_id="BAAI/bge-m3",
        task="retrieval_embedding",
        component="embedding",
        required=True,
        note="Embedding model used by retrieval and ingestion.",
    ),
    HFSnapshotSpec(
        name="intent_classifier",
        repo_id="facebook/bart-large-mnli",
        task="intent_classification",
        component="intent_classifier",
        required=True,
        note="Zero-shot intent classifier.",
    ),
    HFSnapshotSpec(
        name="unstructured_table_transformer",
        repo_id="microsoft/table-transformer-structure-recognition",
        task="document_preprocessing",
        component="unstructured_table",
        required=False,
        note="Unstructured hi_res table-structure model.",
    ),
)

_LARGE_CHAT_MODEL_IDS = {
    "lite_qwen_q4",
    "lite_llama_8b",
    "base_qwen_7b",
}
_CHAT_GGUF_MODEL_IDS = {
    "lite_qwen_1_5b_q4",
    "lite_qwen_q4",
    "lite_llama_8b",
}

UNSTRUCTURED_YOLO_FILES: tuple[str, ...] = (
    "yolox_l0.05_quantized.onnx",
    "yolox_l0.05.onnx",
)

UNSTRUCTURED_YOLO_REPO = "unstructuredio/yolo_x_layout"
FLASHRANK_MODEL_NAME = "ms-marco-MiniLM-L-12-v2"
_CHAT_HF_MODEL_IDS = {"base_qwen_3b", "base_qwen_7b"}

AUX_ASSET_SPECS: tuple[AuxAssetSpec, ...] = (
    AuxAssetSpec(
        name="flashrank_reranker",
        task="retrieval_reranking",
        component="reranker",
        kind="hf_aux",
        required=True,
        note="FlashRank MiniLM reranker used to reorder retrieved passages.",
    ),
    AuxAssetSpec(
        name="unstructured_models",
        task="document_preprocessing",
        component="unstructured_layout",
        kind="hf_aux",
        required=False,
        note="YOLOX layout + table models for Unstructured hi_res extraction.",
    ),
    AuxAssetSpec(
        name="docling_models",
        task="document_preprocessing",
        component="docling_assets",
        kind="hf_aux",
        required=False,
        note="Docling layout, OCR, and table runtime models.",
    ),
)

AGENTIC_GGUF_SPECS: tuple[GGUFSpec, ...] = (
    GGUFSpec(
        model_id="agent_qwen_0_5b_q4",
        repo_id="Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        filename="qwen2.5-0.5b-instruct-q4_k_m.gguf",
        target_name="qwen2.5-0.5b-instruct-q4_k_m.gguf",
        task="planning_routing",
        component="agent_router",
        required=False,
        note="Optional micro-agent model for routing, extraction, and verification tasks on CPU.",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prefetch backend model assets into models/gguf and models/hf_cache.",
    )
    parser.add_argument(
        "--hf-token",
        default="",
        help="Hugging Face token. Optional for public repos, recommended for gated repos.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download of files that already exist.",
    )
    parser.add_argument(
        "--skip-doc-models",
        action="store_true",
        help="Skip Docling and Unstructured document-model assets.",
    )
    parser.add_argument(
        "--include-large-models",
        action="store_true",
        help="Also download larger optional 7B/8B chat models. Not recommended on ~16 GB RAM systems.",
    )
    parser.add_argument(
        "--include-agentic-models",
        action="store_true",
        help="Also download the optional small router/planner GGUF for a multi-agent pipeline.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any optional model fails.",
    )
    parser.add_argument(
        "--show-plan",
        action="store_true",
        help="Print the selected task/model download plan and exit without downloading.",
    )
    return parser.parse_args()


def resolve_token(cli_value: str) -> str | None:
    token = (cli_value or "").strip()
    if token:
        return token

    for env_name in ("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    return None


def activate_token(token: str | None) -> None:
    if not token:
        return

    os.environ["HF_TOKEN"] = token
    os.environ["HUGGINGFACE_HUB_TOKEN"] = token
    os.environ["HUGGINGFACE_TOKEN"] = token
    try:
        login(token=token, add_to_git_credential=False, skip_if_logged_in=True)
    except Exception:
        # Direct token args below are enough even if login persistence fails.
        pass


def snapshot_repo(repo_id: str, *, token: str | None, force: bool) -> str:
    return snapshot_download(
        repo_id=repo_id,
        cache_dir=str(HF_CACHE_DIR),
        token=token,
        force_download=force,
        local_dir_use_symlinks=False,
        resume_download=True,
    )


def download_gguf(spec: GGUFSpec, *, token: str | None, force: bool) -> str:
    path = hf_hub_download(
        repo_id=spec.repo_id,
        filename=spec.filename,
        local_dir=str(GGUF_DIR),
        token=token,
        force_download=force,
        local_dir_use_symlinks=False,
        resume_download=True,
    )

    downloaded_path = Path(path)
    target_path = GGUF_DIR / spec.target_name
    if downloaded_path != target_path and downloaded_path.exists():
        if target_path.exists() and force:
            target_path.unlink()
        if not target_path.exists():
            downloaded_path.replace(target_path)
        path = str(target_path)

    return path


def prefetch_flashrank() -> str:
    from flashrank import Ranker

    Ranker(model_name=FLASHRANK_MODEL_NAME, cache_dir=str(FLASHRANK_CACHE_DIR))
    return str(FLASHRANK_CACHE_DIR / FLASHRANK_MODEL_NAME)


def prefetch_docling(*, force: bool) -> str:
    from docling.utils.model_downloader import download_models

    output_dir = DOCLING_CACHE_DIR / "models"
    download_models(
        output_dir=output_dir,
        force=force,
        progress=True,
        with_layout=True,
        with_tableformer=True,
        with_tableformer_v2=False,
        with_code_formula=True,
        with_picture_classifier=True,
        with_smolvlm=False,
        with_granitedocling=False,
        with_granitedocling_mlx=False,
        with_smoldocling=False,
        with_smoldocling_mlx=False,
        with_granite_vision=False,
        with_granite_chart_extraction=False,
        with_rapidocr=True,
        with_easyocr=False,
    )
    return str(output_dir)


def prefetch_unstructured(*, token: str | None, force: bool) -> dict[str, str]:
    outputs: dict[str, str] = {}
    outputs["table_transformer"] = snapshot_repo(
        "microsoft/table-transformer-structure-recognition",
        token=token,
        force=force,
    )
    for filename in UNSTRUCTURED_YOLO_FILES:
        local_path = hf_hub_download(
            repo_id=UNSTRUCTURED_YOLO_REPO,
            filename=filename,
            cache_dir=str(HF_CACHE_DIR),
            token=token,
            force_download=force,
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        outputs[filename] = local_path
    return outputs


def build_manifest() -> dict[str, Any]:
    return {
        "project_root": str(PROJECT_ROOT),
        "models_dir": str(MODELS_DIR),
        "gguf_dir": str(GGUF_DIR),
        "hf_cache_dir": str(HF_CACHE_DIR),
        "docling_cache_dir": str(DOCLING_CACHE_DIR),
        "flashrank_cache_dir": str(FLASHRANK_CACHE_DIR),
        "gguf_models": [asdict(spec) for spec in GGUF_SPECS],
        "agentic_gguf_models": [asdict(spec) for spec in AGENTIC_GGUF_SPECS],
        "hf_snapshot_models": [asdict(spec) for spec in HF_SNAPSHOT_SPECS],
        "auxiliary_assets": [asdict(spec) for spec in AUX_ASSET_SPECS],
        "unstructured_yolo_repo": UNSTRUCTURED_YOLO_REPO,
        "unstructured_yolo_files": list(UNSTRUCTURED_YOLO_FILES),
        "flashrank_model_name": FLASHRANK_MODEL_NAME,
    }


def iter_selected_gguf_specs(*, include_large_models: bool, include_agentic_models: bool = False) -> list[GGUFSpec]:
    specs: list[GGUFSpec] = []
    for spec in GGUF_SPECS:
        if not include_large_models and spec.model_id in _LARGE_CHAT_MODEL_IDS:
            continue
        specs.append(spec)
    if include_agentic_models:
        specs.extend(AGENTIC_GGUF_SPECS)
    return specs


def iter_selected_hf_snapshot_specs(*, include_large_models: bool) -> list[HFSnapshotSpec]:
    specs: list[HFSnapshotSpec] = []
    for spec in HF_SNAPSHOT_SPECS:
        if not include_large_models and spec.name in _LARGE_CHAT_MODEL_IDS:
            continue
        specs.append(spec)
    return specs


def build_task_plan(
    *,
    include_large_models: bool,
    include_agentic_models: bool,
    skip_doc_models: bool,
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []

    for spec in iter_selected_gguf_specs(
        include_large_models=include_large_models,
        include_agentic_models=include_agentic_models,
    ):
        plan.append(
            {
                "task": spec.task,
                "component": spec.component,
                "asset_id": spec.model_id,
                "kind": "gguf",
                "repo_id": spec.repo_id,
                "required": spec.required,
                "note": spec.note,
            }
        )

    for spec in iter_selected_hf_snapshot_specs(include_large_models=include_large_models):
        plan.append(
            {
                "task": spec.task,
                "component": spec.component,
                "asset_id": spec.name,
                "kind": "hf_snapshot",
                "repo_id": spec.repo_id,
                "required": spec.required,
                "note": spec.note,
            }
        )

    if not skip_doc_models:
        for spec in AUX_ASSET_SPECS:
            plan.append(
                {
                    "task": spec.task,
                    "component": spec.component,
                    "asset_id": spec.name,
                    "kind": spec.kind,
                    "repo_id": None,
                    "required": spec.required,
                    "note": spec.note,
                }
            )

    plan.sort(key=lambda item: (str(item["task"]), str(item["component"]), str(item["asset_id"])))
    return plan


def print_task_plan(plan: list[dict[str, Any]]) -> None:
    print("Selected backend model stack:")
    for item in plan:
        required_label = "required" if item.get("required") else "optional"
        repo_label = f" | repo={item['repo_id']}" if item.get("repo_id") else ""
        print(
            f"- task={item['task']} | component={item['component']} | asset={item['asset_id']} "
            f"| kind={item['kind']} | {required_label}{repo_label}"
        )
        print(f"  note: {item['note']}")
    print("")


def sync_runtime_model_config(
    *,
    gguf_paths: dict[str, str],
    hf_repo_ids: dict[str, str],
    include_large_models: bool,
) -> dict[str, Any]:
    ensure_model_paths()

    synced_gguf: list[str] = []
    synced_hf: list[str] = []

    for model_id, path in sorted(gguf_paths.items()):
        if model_id not in _CHAT_GGUF_MODEL_IDS:
            continue
        upsert_gguf_model(model_id=model_id, path=path)
        synced_gguf.append(model_id)

    for model_id, repo_id in sorted(hf_repo_ids.items()):
        if model_id not in _CHAT_HF_MODEL_IDS:
            continue
        upsert_hf_model(model_id=model_id, repo_id=repo_id)
        synced_hf.append(model_id)

    registry_patch: dict[str, dict[str, str]] = {}
    if "lite_qwen_1_5b_q4" in gguf_paths:
        registry_patch["lite"] = {"default": "lite_qwen_1_5b_q4"}
        if include_large_models and "lite_qwen_q4" in gguf_paths:
            registry_patch["lite"]["fallback"] = "lite_qwen_q4"
        else:
            registry_patch["lite"]["fallback"] = "lite_qwen_1_5b_q4"

    if "base_qwen_3b" in hf_repo_ids:
        registry_patch["base"] = {"default": "base_qwen_3b", "cpu_fallback": "base_qwen_3b"}

    if registry_patch:
        patch_model_registry_overrides(registry_patch)

    return {
        "synced_gguf_models": synced_gguf,
        "synced_hf_models": synced_hf,
        "registry_patch": registry_patch,
    }


def main() -> int:
    args = parse_args()
    token = resolve_token(args.hf_token)
    activate_token(token)
    task_plan = build_task_plan(
        include_large_models=args.include_large_models,
        include_agentic_models=args.include_agentic_models,
        skip_doc_models=args.skip_doc_models,
    )

    manifest = build_manifest()
    manifest["include_large_models"] = bool(args.include_large_models)
    manifest["include_agentic_models"] = bool(args.include_agentic_models)
    manifest["skip_doc_models"] = bool(args.skip_doc_models)
    manifest["task_plan"] = task_plan
    manifest["selected_gguf_models"] = [
        asdict(spec)
        for spec in iter_selected_gguf_specs(
            include_large_models=args.include_large_models,
            include_agentic_models=args.include_agentic_models,
        )
    ]
    manifest["selected_hf_snapshot_models"] = [
        asdict(spec)
        for spec in iter_selected_hf_snapshot_specs(include_large_models=args.include_large_models)
    ]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    downloaded_gguf_paths: dict[str, str] = {}
    downloaded_hf_repo_ids: dict[str, str] = {}

    print_task_plan(task_plan)
    if args.show_plan:
        print("--show-plan was used, so no downloads were started.")
        return 0

    def record_ok(kind: str, name: str, location: Any, note: str) -> None:
        results.append(
            {
                "kind": kind,
                "name": name,
                "ok": True,
                "location": location,
                "note": note,
            }
        )
        print(f"[OK] {kind}: {name}")

    def record_fail(kind: str, name: str, error: Exception, note: str, required: bool) -> None:
        item = {
            "kind": kind,
            "name": name,
            "ok": False,
            "required": required,
            "error": str(error),
            "note": note,
        }
        failures.append(item)
        level = "ERROR" if required or args.strict else "WARN"
        print(f"[{level}] {kind}: {name} -> {error}")

    for spec in iter_selected_gguf_specs(
        include_large_models=args.include_large_models,
        include_agentic_models=args.include_agentic_models,
    ):
        try:
            path = download_gguf(spec, token=token, force=args.force)
            downloaded_gguf_paths[spec.model_id] = path
            record_ok("gguf", spec.model_id, path, spec.note)
        except Exception as exc:
            record_fail("gguf", spec.model_id, exc, spec.note, spec.required)
            if spec.required or args.strict:
                break

    if not failures or not any(item["required"] or args.strict for item in failures):
        for spec in iter_selected_hf_snapshot_specs(include_large_models=args.include_large_models):
            try:
                path = snapshot_repo(spec.repo_id, token=token, force=args.force)
                downloaded_hf_repo_ids[spec.name] = spec.repo_id
                record_ok("hf_snapshot", spec.name, path, spec.note)
            except Exception as exc:
                record_fail("hf_snapshot", spec.name, exc, spec.note, spec.required)
                if spec.required or args.strict:
                    break

    if not args.skip_doc_models and not any(item["required"] or args.strict for item in failures):
        try:
            path = prefetch_flashrank()
            record_ok("hf_aux", "flashrank_reranker", path, "FlashRank ONNX reranker cache.")
        except Exception as exc:
            record_fail(
                "hf_aux",
                "flashrank_reranker",
                exc,
                "FlashRank ONNX reranker cache.",
                True,
            )

        try:
            outputs = prefetch_unstructured(token=token, force=args.force)
            record_ok(
                "hf_aux",
                "unstructured_models",
                outputs,
                "Unstructured hi_res layout and table models.",
            )
        except Exception as exc:
            record_fail(
                "hf_aux",
                "unstructured_models",
                exc,
                "Unstructured hi_res layout and table models.",
                False,
            )

        try:
            path = prefetch_docling(force=args.force)
            record_ok(
                "hf_aux",
                "docling_models",
                path,
                "Docling layout/table/OCR runtime models.",
            )
        except Exception as exc:
            record_fail(
                "hf_aux",
                "docling_models",
                exc,
                "Docling layout/table/OCR runtime models.",
                False,
            )

    manifest["results"] = results
    manifest["failures"] = failures
    manifest["config_sync"] = sync_runtime_model_config(
        gguf_paths=downloaded_gguf_paths,
        hf_repo_ids=downloaded_hf_repo_ids,
        include_large_models=args.include_large_models,
    )
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("")
    print(f"Manifest written to: {MANIFEST_PATH}")
    print(f"GGUF folder: {GGUF_DIR}")
    print(f"HF cache folder: {HF_CACHE_DIR}")
    if not args.include_large_models:
        print("Large optional 7B/8B chat models were skipped for this run.")

    required_failures = [item for item in failures if item.get("required")]
    if required_failures:
        print("")
        print("Required downloads failed. See manifest for details.")
        return 1

    if failures and args.strict:
        print("")
        print("Optional downloads failed and --strict was enabled.")
        return 1

    print("")
    print("Backend model prefetch completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
