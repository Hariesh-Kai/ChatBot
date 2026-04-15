# backend/llm/loader.py

"""
LLM Loader for Chat UI / Chat UI Lite

PHASE 2 GUARANTEES:
- GGUF wrapper ALWAYS yields normalized dicts
- HF streamer attempts short join after abort
- No generator exits without yield where possible
- Abort-safe, thread-safe, stream-safe
"""

import os
import threading
import traceback
from typing import Dict, Any, Generator, Tuple, Optional, Iterable

import torch

# Developer-managed model config (persisted under models/model_config.json)
from backend.llm.model_config_store import (
    GGUF_DIR as CONFIG_GGUF_DIR,
    HF_CACHE_DIR as CONFIG_HF_CACHE_DIR,
    get_model_config_fingerprint,
    load_model_config,
)

# Optional imports (guarded)
try:
    from llama_cpp import Llama
except Exception:
    Llama = None

try:
    from transformers import (
        AutoTokenizer,
        AutoModelForCausalLM,
        TextIteratorStreamer,
        pipeline,
    )
except Exception:
    AutoTokenizer = AutoModelForCausalLM = TextIteratorStreamer = pipeline = None

from backend.state.abort_signals import is_aborted


# ============================================================
# DEVICE DETECTION
# ============================================================

def _detect_device() -> str:
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    try:
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


DEVICE = _detect_device()
DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

print(f"LLM device detected: {DEVICE}")


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
HF_CACHE_DIR = str(CONFIG_HF_CACHE_DIR)
GGUF_DIR = str(CONFIG_GGUF_DIR)
MODELS_DIR = os.path.dirname(HF_CACHE_DIR)


# ============================================================
# MODEL REGISTRY
# ============================================================

_BUILTIN_GGUF_FILE_CANDIDATES: Dict[str, tuple[str, ...]] = {
    "agent_qwen_0_5b_q4": (
        "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
    ),
    "lite_qwen_3b_q4": (
        "qwen2.5-3b-instruct-q4_k_m.gguf",
        "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
    ),
    "lite_llama_8b": (
        "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "meta-llama-3.1-8b-instruct-q4_k_m.gguf",
    ),
    "lite_qwen_1_5b_q4": (
        "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
    ),
    "lite_qwen_q4": (
        "qwen2.5-7b-instruct-q4_k_m.gguf",
        "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
    ),
}


def _default_builtin_gguf_path(model_id: str) -> str:
    filenames = _BUILTIN_GGUF_FILE_CANDIDATES.get(model_id, ())
    if not filenames:
        return ""
    return os.path.join(GGUF_DIR, filenames[0])


def resolve_gguf_model_path(model_id: str) -> str:
    """
    Resolve the current GGUF path for a model id.

    Built-in models accept multiple filename variants so users can download
    the official Hugging Face GGUF file names without manual renaming.
    """
    configured_path = (GGUF_MODELS.get(model_id) or "").strip()

    filenames = _BUILTIN_GGUF_FILE_CANDIDATES.get(model_id)
    if filenames:
        for filename in filenames:
            candidate = os.path.join(GGUF_DIR, filename)
            if os.path.exists(candidate):
                GGUF_MODELS[model_id] = candidate
                return candidate

        fallback = configured_path or _default_builtin_gguf_path(model_id)
        if fallback:
            GGUF_MODELS[model_id] = fallback
        return fallback

    return configured_path


_BUILTIN_GGUF_MODELS: Dict[str, str] = {
    model_id: _default_builtin_gguf_path(model_id)
    for model_id in _BUILTIN_GGUF_FILE_CANDIDATES.keys()
}

_BUILTIN_HF_MODELS: Dict[str, str] = {
    "base_qwen_7b": "Qwen/Qwen2.5-7B-Instruct",
    "base_qwen_3b": "Qwen/Qwen2.5-3B-Instruct",
}

INTENT_CLASSIFIER_MODEL = "facebook/bart-large-mnli"

# Effective registries (include dev-installed models)
GGUF_MODELS: Dict[str, str] = dict(_BUILTIN_GGUF_MODELS)
HF_MODELS: Dict[str, str] = dict(_BUILTIN_HF_MODELS)


# ============================================================
# THREAD-SAFE CACHES
# ============================================================

_lock = threading.RLock()
_llama_cache: Dict[str, Any] = {}
_hf_model_cache: Dict[str, Any] = {}
_hf_tokenizer_cache: Dict[str, Any] = {}
_intent_classifier: Optional[Any] = None
_model_config_fingerprint = (0, 0)


# ============================================================
# DEV MODEL CONFIG (OPTIONAL)
# ============================================================

def reload_model_config() -> Dict[str, Any]:
    """
    Reload custom model registrations from `models/model_config.json`.

    This lets the Developer Dashboard add HF/GGUF models without code changes.
    """
    cfg = load_model_config()

    hf = cfg.get("hf_models", {})
    gguf = cfg.get("gguf_models", {})

    with _lock:
        GGUF_MODELS.clear()
        GGUF_MODELS.update(_BUILTIN_GGUF_MODELS)

        HF_MODELS.clear()
        HF_MODELS.update(_BUILTIN_HF_MODELS)

        if isinstance(hf, dict):
            for model_id, repo_or_path in hf.items():
                if isinstance(model_id, str) and model_id.strip() and isinstance(repo_or_path, str) and repo_or_path.strip():
                    HF_MODELS[model_id.strip()] = repo_or_path.strip()

        if isinstance(gguf, dict):
            for model_id, path in gguf.items():
                if not (isinstance(model_id, str) and model_id.strip() and isinstance(path, str) and path.strip()):
                    continue
                p = path.strip()
                if not os.path.isabs(p):
                    # Accept relative paths under `models/` or `models/gguf/`.
                    candidate_1 = os.path.join(MODELS_DIR, p)
                    candidate_2 = os.path.join(GGUF_DIR, p)
                    p = candidate_1 if os.path.exists(candidate_1) else candidate_2
                GGUF_MODELS[model_id.strip()] = p

    return cfg


def sync_model_runtime_if_needed(force: bool = False) -> Dict[str, Any]:
    """
    Reload model registries when `models/model_config.json` changes on disk.

    This keeps a running backend in sync with external installer scripts
    without requiring a restart.
    """
    global _model_config_fingerprint

    current = get_model_config_fingerprint()
    if not force and current == _model_config_fingerprint:
        return load_model_config()

    cfg = reload_model_config()

    try:
        from backend.llm.model_registry import reload_model_registry

        reload_model_registry()
    except Exception as exc:
        print(f"[LLM] Failed to reload model registry: {exc}")

    _model_config_fingerprint = current
    return cfg


try:
    sync_model_runtime_if_needed(force=True)
except Exception as _e:
    print(f"[LLM] Failed to load model_config.json: {_e}")

# ============================================================
# GGUF (llama_cpp) LOADER + STREAM WRAPPER
# ============================================================

def _ensure_llama_available():
    if Llama is None:
        raise RuntimeError("llama_cpp not installed; GGUF unavailable")


def _is_known_incompatible_gguf(model_path: str) -> bool:
    """
    Prevent loading GGUF quantizations that are known to fail with
    current llama_cpp runtime builds.
    """
    return "q4_0_4_8" in os.path.basename(model_path or "").lower()


def _load_gguf(model_id: str) -> Any:
    if model_id in _llama_cache:
        return _llama_cache[model_id]

    model_path = resolve_gguf_model_path(model_id)
    if not model_path or not os.path.exists(model_path):
        raise FileNotFoundError(f"GGUF model not found for '{model_id}': {model_path}")
    if _is_known_incompatible_gguf(model_path):
        raise RuntimeError(
            "Incompatible GGUF quantization detected (Q4_0_4_8). "
            "Use a Q4_K_M/Q4_0 model for current llama_cpp runtime."
        )

    with _lock:
        if model_id in _llama_cache:
            return _llama_cache[model_id]

        print(f"Loading GGUF model [{model_id}] …")
        gpu_layers = -1 if DEVICE in ("cuda", "mps") else 0

        llm = Llama(
            model_path=model_path,
            n_ctx=8192,
            n_threads=os.cpu_count() or 4,
            n_gpu_layers=gpu_layers,
            verbose=False,
        )

        _llama_cache[model_id] = llm
        print(f"GGUF model loaded [{model_id}] | gpu_layers={gpu_layers}")
        return llm


def _gguf_stream_wrapper(
    llm_instance: Any,
    prompt: str,
    max_tokens: int = 512,
    stream: bool = True,
    stop: Optional[Iterable[str]] = None,
    session_id: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """
    PHASE-2 SAFE:
    - ALWAYS yields normalized dicts
    - Emits one empty yield on abort to avoid silent close
    """
    _ensure_llama_available()

    try:
        try:
            gen = llm_instance(prompt, max_tokens=max_tokens, stream=stream, stop=stop)
        except TypeError:
            gen = llm_instance.generate(prompt, max_tokens=max_tokens, stream=stream, stop=stop)
    except Exception as e:
        yield {"choices": [{"text": "GGUF generation failed."}]}
        return

    yielded = False

    for item in gen:
        if session_id and is_aborted(session_id):
            print(f"[GGUF] Abort detected for session {session_id}")
            break

        if isinstance(item, dict):
            yielded = True
            yield item
        elif isinstance(item, str):
            yielded = True
            yield {"choices": [{"text": item}]}
        else:
            try:
                s = str(item)
            except Exception:
                s = ""
            yielded = True
            yield {"choices": [{"text": s}]}

    # 🔥 FINAL SAFETY YIELD
    if not yielded:
        yield {"choices": [{"text": ""}]}


# ============================================================
# HF (transformers) LOADER + STREAM
# ============================================================

def _load_hf(model_id: str) -> Tuple[Any, Any]:
    if AutoTokenizer is None or AutoModelForCausalLM is None:
        raise RuntimeError("transformers not installed")

    if model_id in _hf_model_cache:
        return _hf_model_cache[model_id], _hf_tokenizer_cache[model_id]

    model_name = HF_MODELS.get(model_id)
    if not model_name:
        raise ValueError(f"Unknown HF model_id: {model_id}")

    with _lock:
        if model_id in _hf_model_cache:
            return _hf_model_cache[model_id], _hf_tokenizer_cache[model_id]

        print(f" Loading HF model [{model_id}] on {DEVICE} …")

        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            cache_dir=HF_CACHE_DIR,
            local_files_only=True,
        )

        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            cache_dir=HF_CACHE_DIR,
            torch_dtype=DTYPE,
            device_map="auto" if DEVICE == "cuda" else None,
            local_files_only=True,
        )

        if model.config.pad_token_id is None:
            model.config.pad_token_id = tokenizer.eos_token_id

        model.eval()

        _hf_model_cache[model_id] = model
        _hf_tokenizer_cache[model_id] = tokenizer

        print(f"HF model loaded [{model_id}]")
        return model, tokenizer


def hf_stream_generate(
    model_id: str,
    prompt: str,
    max_new_tokens: int = 512,
    session_id: Optional[str] = None,
) -> Generator[str, None, None]:
    """
    PHASE-2 SAFE:
    - Short thread.join after abort
    - Never crashes streamer
    - Timeout on streamer iteration to prevent hanging
    """
    import queue
    
    model, tokenizer = _load_hf(model_id)

    # Increase timeout for CPU-based generation (slower than GPU)
    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, timeout=300)
    inputs = tokenizer(prompt, return_tensors="pt")

    try:
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
    except Exception:
        pass

    kwargs = dict(
        **inputs,
        streamer=streamer,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id,
    )

    thread = threading.Thread(
        target=model.generate,
        kwargs=kwargs,
        daemon=True,
    )
    thread.start()

    try:
        for token in streamer:
            if session_id and is_aborted(session_id):
                print(f"[HF] Abort detected for session {session_id}")
                try:
                    thread.join(timeout=0.2)
                except Exception:
                    pass
                break
            yield token
    except queue.Empty:
        # Streamer timeout - this is expected for slow CPU generation
        print(f"[HF] Streamer timeout - generation may still be in progress")
        yield ""
    except Exception:
        traceback.print_exc()
        yield ""
    finally:
        # Always try to clean up the thread
        try:
            if thread.is_alive():
                thread.join(timeout=1.0)
        except Exception:
            pass


# ============================================================
# INTENT CLASSIFIER
# ============================================================

def load_intent_classifier():
    global _intent_classifier

    if _intent_classifier is not None:
        return _intent_classifier

    if pipeline is None:
        raise RuntimeError("transformers pipeline unavailable")

    with _lock:
        if _intent_classifier is not None:
            return _intent_classifier

        print("Loading intent classifier [bart-large-mnli]…")
        device_id = 0 if DEVICE == "cuda" else -1

        _intent_classifier = pipeline(
            task="zero-shot-classification",
            model=INTENT_CLASSIFIER_MODEL,
            device=device_id,
            cache_dir=HF_CACHE_DIR,
            local_files_only=True,
        )

        print("Intent classifier loaded")
        return _intent_classifier


# ============================================================
# PUBLIC API
# ============================================================

def get_llm(model_id: str) -> Dict[str, Any]:
    """
    Returns:
    - GGUF: {"type": "gguf", "llm": callable}
    - HF:   {"type": "hf", "model": model, "tokenizer": tokenizer}
    """
    sync_model_runtime_if_needed()

    if model_id in GGUF_MODELS:
        llm_inst = _load_gguf(model_id)

        def gguf_callable(
            prompt: str,
            max_tokens: int = 512,
            stream: bool = True,
            stop: Optional[Iterable[str]] = None,
            session_id: Optional[str] = None,
        ):
            return _gguf_stream_wrapper(
                llm_inst,
                prompt,
                max_tokens=max_tokens,
                stream=stream,
                stop=list(stop) if stop else None,
                session_id=session_id,
            )

        return {"type": "gguf", "llm": gguf_callable}

    if model_id in HF_MODELS:
        model, tokenizer = _load_hf(model_id)
        return {"type": "hf", "model": model, "tokenizer": tokenizer}

    raise ValueError(f"Unknown model_id '{model_id}'")
