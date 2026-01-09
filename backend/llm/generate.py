# backend/llm/generate.py

"""
Unified text generation for KavinBase / KavinBase Lite / KavinBase Net.

CRITICAL GUARANTEES:
- ALWAYS yields at least one string
- Streaming-safe
- Abort-safe
- Net-safe (rate + concurrency guarded)
"""

import os
from typing import List, Dict, Optional, Literal, Generator

import torch

from backend.state.abort_signals import is_aborted
from backend.llm.loader import get_llm, hf_stream_generate
from backend.llm.net_loader import generate_net_answer_stream
from backend.llm.net_models import get_active_net_provider, NET_MAX_TOKENS
from backend.llm.prompts import (
    build_prompt_hf,
    build_prompt_gguf,
    build_prompt_cot,
    clean_model_output,
)
from backend.llm.answer_policy import decide_answer_style, infer_answer_policy
from backend.llm.response_policy import apply_response_policy

# Net guards
from backend.api.net import (
    check_rate_limit,
    acquire_stream_slot,
    release_stream_slot,
)

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


def _context_to_text(chunks: Optional[List[Dict[str, str]]]) -> str:
    if not chunks:
        return ""
    return "\n\n".join(c["content"] for c in chunks if c.get("content"))


def _build_prompt(question, model, context_chunks, chat_history):
    if model == "lite":
        return build_prompt_gguf(question, context_chunks)
    return build_prompt_hf(question, context_chunks, chat_history)


# ============================================================
# MAIN STREAM GENERATOR
# ============================================================

def generate_answer_stream(
    *,
    question: str,
    model: Literal["base", "lite", "net"] = "lite",
    context_chunks: Optional[List[Dict[str, str]]] = None,
    intent: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
    max_tokens: int = 1024,
    session_id: Optional[str] = None,
) -> Generator[str, None, None]:

    # --- DEBUG: VERIFY CHUNKS ---
    chunk_count = len(context_chunks) if context_chunks else 0
    print(f"🧩 [GENERATE DEBUG] Received {chunk_count} chunks")
    if chunk_count > 0:
        print(f"   - First Chunk Sample: {str(context_chunks[0])[:50]}...")
    # ----------------------------

    # ---------------------------
    # HARD GUARANTEE: at least one yield
    # ---------------------------
    yielded_anything = False

    def _yield(text: str):
        nonlocal yielded_anything
        if text:
            yielded_anything = True
            return text
        return None

    if not question:
        yield "Please ask a question."
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
        else:
            prompt = build_prompt_cot(question, context_chunks, chat_history)

            try:
                if model == "net":
                    if not session_id:
                        yield "Session required for Net mode."
                        return

                    check_rate_limit(session_id)
                    acquire_stream_slot()

                    try:
                        provider = get_active_net_provider()

                        for token in generate_net_answer_stream(
                            prompt=prompt,
                            provider=provider,
                            variant="default",
                            max_tokens=min(max_tokens, NET_MAX_TOKENS),
                        ):
                            if is_aborted(session_id):
                                break
                            if token:
                                yield _yield(token)

                    finally:
                        release_stream_slot()

                else:
                    for t in hf_stream_generate(
                        model_id=BASE_RANK,
                        prompt=prompt,
                        max_new_tokens=max_tokens,
                        session_id=session_id,
                    ):
                        if session_id and is_aborted(session_id):
                            break
                        if t:
                            yield _yield(t)

            except Exception:
                yield "Error while processing documents."
                return

            if not yielded_anything:
                yield "No answer could be generated from the documents."
            return

    # ========================================================
    # LITE / FAST
    # ========================================================

    if _is_conversational(intent):
        max_tokens = min(max_tokens, 128)

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
                yield apply_response_policy(final, verbosity=style.verbosity)
                return
        except Exception:
            pass

    # -------- Standard lite generation

    prompt = _build_prompt(question, model, context_chunks, chat_history)

    try:
        llm = get_llm(LITE_RANK_1)
    except Exception:
        yield "Model unavailable."
        return

    collected: List[str] = []

    try:
        if llm["type"] == "gguf":
            for chunk in llm["llm"](prompt, max_tokens=max_tokens, stream=True):
                if session_id and is_aborted(session_id):
                    break

                text = ""
                if isinstance(chunk, dict):
                    text = chunk.get("choices", [{}])[0].get("text", "")
                elif isinstance(chunk, str):
                    text = chunk

                if text:
                    collected.append(text)
                    yield _yield(text)

        else:
            for t in hf_stream_generate(
                model_id=LITE_RANK_1,
                prompt=prompt,
                max_new_tokens=max_tokens,
                session_id=session_id,
            ):
                if session_id and is_aborted(session_id):
                    break
                if t:
                    collected.append(t)
                    yield _yield(t)

    except Exception:
        yield "Generation failed."
        return

    if not yielded_anything:
        yield "How can I help you?"
        return