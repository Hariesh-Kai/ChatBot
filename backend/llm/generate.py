# backend/llm/generate.py

"""
Unified text generation for Chat UI / Chat UI Lite / Chat UI Net.

CRITICAL GUARANTEES:
- ALWAYS yields at least one string
- Streaming-safe
- Abort-safe
- Net-safe (rate + concurrency guarded)
"""

import os
from typing import List, Dict, Optional, Literal, Generator

import torch
import json
from backend.state.abort_signals import is_aborted
from backend.llm.loader import get_llm, hf_stream_generate, GGUF_MODELS, HF_MODELS
from backend.llm.net_loader import generate_net_answer_stream, NetRateLimitError
from backend.llm.net_models import get_active_net_provider, NET_MAX_TOKENS
from backend.contracts.ui_events import net_rate_limited_event, error_event


from backend.llm.prompts import (
    build_prompt_hf,
    build_prompt_gguf,
    build_prompt_cot,
    clean_model_output,
)
from backend.llm.answer_policy import decide_answer_style, infer_answer_policy
from backend.llm.response_policy import apply_response_policy
from backend.contracts.ui_events import text_event
from backend.contracts.ui_constants import UI_EVENT_PREFIX
from backend.rag.grounding import check_grounding




ADVANCED_REASONING = os.getenv("ADVANCED_REASONING", "0").lower() in ("1", "true", "yes")


# ============================================================
# DEVICE
# ============================================================

def _has_gpu() -> bool:
    try:
        return torch.cuda.is_available()
    except Exception:
        return False


HAS_GPU = _has_gpu()

LITE_RANK_1 = "lite_llama_8b"
LITE_RANK_2 = "lite_qwen_q4"

BASE_RANK_GPU = "base_qwen_7b"
BASE_RANK_CPU = "base_qwen_3b"
BASE_RANK = BASE_RANK_GPU if HAS_GPU else BASE_RANK_CPU

# Markers that usually mean the model has started echoing the internal prompt.
_PROMPT_ECHO_STOP_MARKERS = (
    "<|eot_id|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
    "OUTPUT STYLE:",
    "\nCONTEXT:\n",
    "\nQUESTION:\n",
)
_PROMPT_ECHO_TAIL = max(len(m) for m in _PROMPT_ECHO_STOP_MARKERS)


# ============================================================
# HELPERS
# ============================================================

def _is_conversational(intent: Optional[str]) -> bool:
    return intent in ("greeting", "conversation", "confirmation", "chitchat", "fast")


def _is_bad_answer(text: str) -> bool:
    if not text:
        return True
    t = text.strip().lower()
    return any(
        bad in t
        for bad in (
            "i am an ai",
            "i cannot answer",
            "not provided in the document",
            "no information available",
        )
    )


def _friendly_model_error(err: Exception) -> str:
    msg = str(err) if err else "Model unavailable."
    lower = msg.lower()

    if "unknown hf model_id" in lower or "unknown model_id" in lower:
        return "Model not registered. Add or switch the Base model in the Developer Dashboard."
    if "gguf model not found" in lower or "no such file" in lower:
        return "GGUF model file missing. Re-download or register the Lite model."
    if "type_q4_0_4_8 removed" in lower or "q4_0_4_8" in lower:
        return (
            "This GGUF quantization is incompatible with the current llama_cpp runtime. "
            "Use a Q4_K_M/Q4_0 model or re-convert the file."
        )
    if "transformers not installed" in lower:
        return "Transformers not installed on the server (Base model cannot load)."
    if "local_files_only" in lower or "offline mode" in lower:
        return "Model files not found locally. Download the Base model in the Developer Dashboard."
    if "llama_cpp" in lower:
        return "GGUF runtime not installed (llama_cpp missing)."

    # fallback: keep message short
    return msg[:200]


def _friendly_net_error(err: Exception) -> str:
    msg = str(err) if err else "Net model unavailable."
    lower = msg.lower()

    if "not configured" in lower or "no api key" in lower or "api key missing" in lower:
        return "Kavin Net is not configured. Add and verify your API key."
    if "invalid" in lower and "key" in lower:
        return "Net API key is invalid. Please re-verify it."
    if "unsupported net provider" in lower or "invalid net provider" in lower:
        return "Net provider is invalid. Choose Groq or xAI in Net settings."
    if "rate limit" in lower:
        return "Net is rate limited. Please try again shortly."

    return msg[:200]


def _context_to_text(chunks: Optional[List[Dict[str, str]]]) -> str:
    if not chunks:
        return ""
    return "\n\n".join(c["content"] for c in chunks if c.get("content"))


def _first_prompt_echo_index(text: str) -> int:
    idx = -1
    for marker in _PROMPT_ECHO_STOP_MARKERS:
        i = text.find(marker)
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    return idx


def _resolve_model_family(model_id: str) -> str:
    """
    Resolve runtime family from registered model IDs.
    This avoids prefix assumptions for custom model IDs.
    """
    mid = (model_id or "").strip()
    if not mid:
        return "net"

    if mid in GGUF_MODELS:
        return "lite"
    if mid in HF_MODELS:
        return "base"

    # Backward-compatible fallback for legacy IDs.
    if mid.startswith("lite"):
        return "lite"
    if mid.startswith("base"):
        return "base"
    return "net"


def _build_prompt(question, model, context_chunks, chat_history):
    if model == "lite":
        return build_prompt_gguf(question, context_chunks)
    return build_prompt_hf(question, context_chunks, chat_history)


def _lite_fallback_model_id(current_model_id: str) -> Optional[str]:
    try:
        from backend.llm.model_registry import MODEL_REGISTRY
    except Exception:
        return None

    entry = MODEL_REGISTRY.get("lite", {})
    fallback_id = str(entry.get("fallback") or "").strip()
    if not fallback_id or fallback_id == current_model_id:
        return None
    if fallback_id not in GGUF_MODELS:
        return None
    return fallback_id


# ============================================================
# MAIN STREAM GENERATOR
# ============================================================

def generate_answer_stream(
    *,
    question: str,
    model_id: str,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    intent: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
    max_tokens: int = 1024,
    session_id: Optional[str] = None,
) -> Generator[str, None, None]:
     
    
    # Resolve mode from registered model family.
    model = _resolve_model_family(model_id)

    # --- DEBUG: VERIFY CHUNKS ---
    chunk_count = len(context_chunks) if context_chunks else 0
    print(f"[GENERATE DEBUG] Context chunks = {chunk_count}")
    if chunk_count > 0:
        print(f"   - First Chunk Sample: {str(context_chunks[0])[:50]}...")
    # ----------------------------

    # ---------------------------
    # HARD GUARANTEE: at least one yield
    # ---------------------------
    yielded_anything = False
    collected: List[str] = []



    if not question:
        yield UI_EVENT_PREFIX + json.dumps(
            text_event("Please ask a question.")
        ) + "\n"

        
        return

    policy = infer_answer_policy(question)
    style = decide_answer_style(question, context_chunks)
    context_text = _context_to_text(context_chunks)

    # ========================================================
    # BASE / NET (DOCUMENT-AWARE)
    # ========================================================

    if model in ("base", "net"):
        if not context_chunks and _is_conversational(intent):
            model = "lite"
            try:
                from backend.llm.model_selector import resolve_model_id
                model_id = resolve_model_id("lite")
            except Exception:
                # Last-resort fallback when registry is unavailable.
                model_id = "lite_llama_8b"
        else:
            prompt = build_prompt_cot(question, context_chunks, chat_history)

            try:
                if model == "net":
                    if not session_id:
                        yield UI_EVENT_PREFIX + json.dumps(
                           error_event("Session required for Net mode.")
                          ) + "\n"
                        return
                    try:
                        provider = get_active_net_provider()

                        for token in generate_net_answer_stream(
                            prompt=prompt,
                            provider=provider,
                            variant="rank_1",
                            max_tokens=min(max_tokens, NET_MAX_TOKENS),
                        ):
                            if is_aborted(session_id):
                                break
                            if token:
                                yielded_anything = True
                                collected.append(token)
                                yield token
                    except NetRateLimitError as e:
                        msg = str(e)
                        provider = None

                        if ":" in msg:
                            _, provider = msg.split(":", 1)

                        
                        yield "__UI_EVENT__" + json.dumps(
                            net_rate_limited_event(
                                retry_after_sec=30,
                                provider=provider,
                            )
                        ) + "\n"
                        return

                else:
                    stream_text = ""
                    emitted_len = 0
                    for t in hf_stream_generate(
                        model_id=model_id,
                        prompt=prompt,
                        max_new_tokens=max_tokens,
                        session_id=session_id,
                    ):
                        if session_id and is_aborted(session_id):
                            yield ""  # allow UI to close stream cleanly
                            return
                        if not t:
                            continue

                        stream_text += t
                        stop_idx = _first_prompt_echo_index(stream_text)
                        if stop_idx >= 0:
                            safe = stream_text[:stop_idx]
                            delta = safe[emitted_len:]
                            if delta:
                                yielded_anything = True
                                collected.append(delta)
                                yield delta
                            break

                        # Hold back a small tail to avoid leaking partial markers.
                        safe_end = max(0, len(stream_text) - (_PROMPT_ECHO_TAIL - 1))
                        if safe_end > emitted_len:
                            delta = stream_text[emitted_len:safe_end]
                            if delta:
                                yielded_anything = True
                                collected.append(delta)
                                yield delta
                            emitted_len = safe_end

                    if emitted_len < len(stream_text):
                        tail = stream_text[emitted_len:]
                        tail_stop_idx = _first_prompt_echo_index(tail)
                        if tail_stop_idx >= 0:
                            tail = tail[:tail_stop_idx]
                        if tail:
                            yielded_anything = True
                            collected.append(tail)
                            yield tail


            except Exception as e:
                if model == "net":
                    msg = _friendly_net_error(e)
                else:
                    msg = _friendly_model_error(e)
                yield UI_EVENT_PREFIX + json.dumps(
                    error_event(msg or "Error while processing documents.")
                ) + "\n"
                return

            if not yielded_anything:
                yield "No answer could be generated from the documents."
                return

            # --- Grounding check (soft warning, non-blocking) ---
            if context_chunks and collected:
                full_answer = "".join(collected)
                try:
                    grounding = check_grounding(full_answer, context_chunks)
                    if not grounding["is_grounded"] and grounding["grounding_score"] < 0.40:
                        warning = (
                            "\n\n> ⚠️ Some values in this answer could not be "
                            "verified against the source document. "
                            "Please cross-check with the original PDF."
                        )
                        yield warning
                except Exception:
                    pass  # grounding check must never crash the stream
            return

    # ========================================================
    # LITE / FAST
    # ========================================================

    if _is_conversational(intent):
        max_tokens = min(max_tokens, 128)
    if model == "base":
        max_tokens = min(max_tokens, 512 if context_chunks else 256)

    # -------- Advanced reasoning (optional)
    if ADVANCED_REASONING and not _is_conversational(intent):
        try:
            from backend.llm.orchestrator import deliberate_answer

            final = deliberate_answer(
                question=question,
                context_text=context_text,
                reasoner_models=[LITE_RANK_2, LITE_RANK_1],
                verifier_models=[],
                editor_model=LITE_RANK_1,
                verbosity=style.verbosity,
                session_id=session_id,
            )

            if final and not _is_bad_answer(final):
                yield UI_EVENT_PREFIX + json.dumps(
                    text_event(apply_response_policy(final, verbosity=style.verbosity))
                ) + "\n"
                return
        except Exception:
            pass

    # -------- Standard lite generation
    if model == "lite" and not context_chunks:
        # Absolute-safe fallback for normal chat (NO indentation inside prompt)
        prompt = build_prompt_gguf(
            question=question,
            context_chunks=context_chunks,
            answer_style=style,
        )
    else:
        prompt = _build_prompt(question, model, context_chunks, chat_history)
    
    prompt = prompt.rstrip() + "\n"

    
    if not prompt:
        prompt = f"User: {question}\nAssistant:"
    
    llm = None
    try:
        llm = get_llm(model_id)
    except Exception as e:
        # Automatic Lite fallback when the configured GGUF is broken/missing.
        if model == "lite":
            fallback_id = _lite_fallback_model_id(model_id)
            if fallback_id:
                try:
                    print(
                        f"[LLM] Lite load failed for '{model_id}'. "
                        f"Trying fallback '{fallback_id}'."
                    )
                    llm = get_llm(fallback_id)
                    model_id = fallback_id
                except Exception as fallback_error:
                    e = fallback_error
            else:
                pass

        if llm is None:
            yield UI_EVENT_PREFIX + json.dumps(
                text_event(_friendly_model_error(e))
            ) + "\n"
            return

   

    try:
        if llm["type"] == "gguf":
            for chunk in llm["llm"](prompt, max_tokens=max_tokens):
                if session_id and is_aborted(session_id):
                    yield ""
                    return
                text = ""
                if isinstance(chunk, dict):
                    text = chunk.get("choices", [{}])[0].get("text", "")
                elif isinstance(chunk, str):
                    text = chunk

                if text:
                    print("TOKEN:", repr(text))  # ✅ NOW SAFE
                    collected.append(text)
                    yield text


        else:
            stream_text = ""
            emitted_len = 0
            for t in hf_stream_generate(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=max_tokens,
                session_id=session_id,
            ):
                if session_id and is_aborted(session_id):
                    yield ""  # allow UI to close stream cleanly
                    return
                if not t:
                    continue

                stream_text += t
                stop_idx = _first_prompt_echo_index(stream_text)
                if stop_idx >= 0:
                    safe = stream_text[:stop_idx]
                    delta = safe[emitted_len:]
                    if delta:
                        collected.append(delta)
                        yield delta
                    break

                safe_end = max(0, len(stream_text) - (_PROMPT_ECHO_TAIL - 1))
                if safe_end > emitted_len:
                    delta = stream_text[emitted_len:safe_end]
                    if delta:
                        collected.append(delta)
                        yield delta
                    emitted_len = safe_end

            if emitted_len < len(stream_text):
                tail = stream_text[emitted_len:]
                tail_stop_idx = _first_prompt_echo_index(tail)
                if tail_stop_idx >= 0:
                    tail = tail[:tail_stop_idx]
                if tail:
                    collected.append(tail)
                    yield tail


    except Exception:
        yield UI_EVENT_PREFIX + json.dumps(
            text_event("Generation failed.")
        ) + "\n"
        return

    if not collected or not "".join(collected).strip():
        yield UI_EVENT_PREFIX + json.dumps(
            text_event("How can I help you?")
        ) + "\n"
        return

    # --- Grounding check for lite mode (soft warning, non-blocking) ---
    if context_chunks and collected:
        full_answer = "".join(collected)
        try:
            grounding = check_grounding(full_answer, context_chunks)
            if not grounding["is_grounded"] and grounding["grounding_score"] < 0.40:
                warning = (
                    "\n\n> ⚠️ Some values in this answer could not be "
                    "verified against the source document. "
                    "Please cross-check with the original PDF."
                )
                yield warning
        except Exception:
            pass  # grounding check must never crash the stream
