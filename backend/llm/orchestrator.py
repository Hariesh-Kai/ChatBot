# backend/llm/orchestrator.py

"""
orchestrator.py (SAFE, DISABLED-BY-DEFAULT)

Hierarchical deliberation engine.

- Disabled unless ADVANCED_REASONING=true
- Abort-aware
- Single-reasoner by default
- Verifier/editor optional
"""

from typing import List, Optional
import time
import os

from backend.llm.loader import get_llm, hf_stream_generate
from backend.llm.prompts import clean_model_output
from backend.llm.response_policy import apply_response_policy
from backend.state.abort_signals import is_aborted

# ============================================================
# GLOBAL FEATURE FLAG
# ============================================================

ADVANCED_REASONING_ENABLED = os.getenv(
    "ADVANCED_REASONING", "false"
).lower() == "true"

# ============================================================
# INTERNAL EXECUTION HELPER
# ============================================================

def _run_model_once(
    *,
    model_id: str,
    prompt: str,
    session_id: Optional[str],
    max_tokens: int = 512,
    role: str = "unknown",
) -> str:
    """
    Execute ONE model synchronously.
    Abort-aware. Returns cleaned text or empty string.
    """

    start = time.time()
    print(f"🧠 [ORCH:{role}] START {model_id}")

    info = get_llm(model_id)
    tokens: List[str] = []

    try:
        if info["type"] == "gguf":
            for chunk in info["llm"](
                prompt,
                max_tokens=max_tokens,
                stream=True,
            ):
                if session_id and is_aborted(session_id):
                    print(f"🛑 [ORCH:{role}] aborted")
                    return ""

                text = ""
                if isinstance(chunk, dict) and "choices" in chunk:
                    text = chunk["choices"][0].get("text", "")
                elif isinstance(chunk, str):
                    text = chunk

                if text:
                    tokens.append(text)

        else:
            for t in hf_stream_generate(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=max_tokens,
                session_id=session_id,
            ):
                if session_id and is_aborted(session_id):
                    print(f"🛑 [ORCH:{role}] aborted")
                    return ""

                if t:
                    tokens.append(t)

    except Exception as e:
        print(f"🔥 [ORCH:{role}] ERROR {model_id}: {repr(e)}")
        return ""

    output = clean_model_output("".join(tokens))
    elapsed = round(time.time() - start, 2)
    print(f"✅ [ORCH:{role}] END {model_id} | {elapsed}s")

    return output

# ============================================================
# PUBLIC API
# ============================================================

def deliberate_answer(
    *,
    question: str,
    context_text: str,
    reasoner_models: List[str],
    verifier_models: List[str],
    editor_model: str,
    verbosity: str,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """
    Advanced reasoning entry point.

    Returns None immediately if ADVANCED_REASONING is disabled.
    """

    if not ADVANCED_REASONING_ENABLED:
        return None

    if session_id and is_aborted(session_id):
        return None

    print("🚦 [ORCH] Advanced reasoning enabled")

    # ----------------------------
    # STAGE 1 — REASONER (ONE)
    # ----------------------------

    primary_reasoner = reasoner_models[0]

    reasoner_prompt = f"""
Answer the question using ONLY the provided document.

DOCUMENT:
{context_text}

QUESTION:
{question}

ANSWER:
""".strip()

    candidate = _run_model_once(
        model_id=primary_reasoner,
        prompt=reasoner_prompt,
        role="reasoner",
        session_id=session_id,
    )

    if not candidate:
        return None

    # ----------------------------
    # STAGE 2 — OPTIONAL VERIFIER
    # ----------------------------

    if verifier_models and not is_aborted(session_id):
        verifier = verifier_models[0]

        verify_prompt = f"""
Verify whether the answer is fully supported by the document.
Return the SAME answer if correct.
Return NOTHING if incorrect.

DOCUMENT:
{context_text}

ANSWER:
{candidate}
""".strip()

        verified = _run_model_once(
            model_id=verifier,
            prompt=verify_prompt,
            role="verifier",
            session_id=session_id,
        )

        if verified:
            candidate = verified

    # ----------------------------
    # STAGE 3 — EDITOR (OPTIONAL)
    # ----------------------------

    editor_prompt = f"""
Choose the best final answer.

QUESTION:
{question}

ANSWER:
{candidate}
""".strip()

    final = _run_model_once(
        model_id=editor_model,
        prompt=editor_prompt,
        role="editor",
        session_id=session_id,
    )

    final = final or candidate
    final = apply_response_policy(final, verbosity=verbosity)

    print("🏁 [ORCH] Finished")
    return final
