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
import json
from backend.state.abort_signals import is_aborted
from backend.llm.loader import get_llm, hf_stream_generate
from backend.llm.net_loader import generate_net_answer_stream, NetRateLimitError
from backend.llm.net_models import get_active_net_provider, NET_MAX_TOKENS
from backend.contracts.ui_events import net_rate_limited_event


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
    model_id: str,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    intent: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
    max_tokens: int = 1024,
    session_id: Optional[str] = None,
) -> Generator[str, None, None]:
     
    
    # Resolve chat mode from model_id
    if model_id.startswith("lite"):
        model = "lite"
    elif model_id.startswith("base"):
        model = "base"
    else:
        model = "net"

    # --- DEBUG: VERIFY CHUNKS ---
    chunk_count = len(context_chunks) if context_chunks else 0
    print(f"🧩 [GENERATE DEBUG] Context chunks = {chunk_count}")
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
            model_id = "lite_llama_8b"
        else:
            prompt = build_prompt_cot(question, context_chunks, chat_history)

            try:
                if model == "net":
                    if not session_id:
                        yield UI_EVENT_PREFIX + json.dumps(
                           text_event("Session required for Net mode.")
                          ) + "\n"
                        return
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
                    for t in hf_stream_generate(
                        model_id=model_id,
                        prompt=prompt,
                        max_new_tokens=max_tokens,
                        session_id=session_id,
                    ):
                        if session_id and is_aborted(session_id):
                            yield ""  # allow UI to close stream cleanly
                            return
                        if t:
                            yielded_anything = True   # ✅ REQUIRED
                            collected.append(t)      # ✅ REQUIRED
                            yield t



            except Exception:
                yield UI_EVENT_PREFIX + json.dumps(
                    text_event("Error while processing documents.")
                ) + "\n"
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
    
    try:
        llm = get_llm(model_id)
    except Exception:
        yield UI_EVENT_PREFIX + json.dumps(
            text_event("Model unavailable.")
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
            for t in hf_stream_generate(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=max_tokens,
                session_id=session_id,
            ):
                if session_id and is_aborted(session_id):
                    yield ""  # allow UI to close stream cleanly
                    return
                if t:
                    collected.append(t)
                    yield t


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