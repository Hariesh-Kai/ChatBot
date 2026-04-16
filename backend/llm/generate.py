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
import re
import time
from typing import List, Dict, Optional, Literal, Generator, Any

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
    build_prompt_lite_formatting,
    build_prompt_base_citation,
    clean_model_output,
)
from backend.llm.stop_generation import (
    get_stop_tokens_for_model,
    should_stop_generation,
    clean_llm_response,
    StreamingStopDetector,
    get_default_streaming_detector,
)
from backend.llm.answer_policy import decide_answer_style, infer_answer_policy
from backend.llm.response_policy import apply_response_policy
from backend.contracts.ui_events import text_event, agentic_step_event
from backend.contracts.ui_constants import UI_EVENT_PREFIX
from backend.rag.grounding import check_grounding
from backend.state.dev_settings import get_dev_settings
from backend.rag.extractive_rag import extract_relevant_passages, format_passages_for_display
from backend.llm.hardware_profile import get_hardware_profile
from backend.memory.pg_memory import (
    get_or_create_user_state,
    update_user_state,
    increment_interaction_count,
    log_self_correction,
    save_proactive_suggestion,
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
BASE_RANK_CPU = "base_qwen_7b_q4"  # GGUF: 5-8x faster than HF on CPU
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

_BASE_PROMPT_MAX_CHUNKS = 4
_BASE_PROMPT_PER_CHUNK_CHARS = 900
_BASE_PROMPT_TOTAL_CONTEXT_CHARS = 2800
_BASE_EXTRACTIVE_TOP_K = 3
_BASE_EXTRACTIVE_PER_PASSAGE_CHARS = 650
_BASE_EXTRACTIVE_TOTAL_CHARS = 2200

# Hardware-adaptive context limits (override static values on CPU)
def _get_base_context_limits() -> Dict[str, int]:
    """Get hardware-adaptive context limits for Base mode prompt."""
    try:
        hw = get_hardware_profile()
        hw_gen = hw.get("generation", {})
        max_chars = hw_gen.get("base_context_max_chars", _BASE_PROMPT_TOTAL_CONTEXT_CHARS)
    except Exception:
        max_chars = _BASE_PROMPT_TOTAL_CONTEXT_CHARS

    # Scale chunk count and per-chunk budget proportionally
    ratio = max_chars / _BASE_PROMPT_TOTAL_CONTEXT_CHARS
    max_chunks = max(2, min(6, int(_BASE_PROMPT_MAX_CHUNKS * ratio)))
    per_chunk = max(400, min(1200, int(_BASE_PROMPT_PER_CHUNK_CHARS * ratio)))

    return {
        "max_chunks": max_chunks,
        "per_chunk_chars": per_chunk,
        "total_chars": max_chars,
    }


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


def _get_mode_generation_config(mode: str) -> Dict[str, Any]:
    """
    Get mode-specific generation configuration, adapted to hardware.
    
    Lite: 80% extractive context + 20% generation (no token limit, relies on prompt stopping)
    Base: 50-75% extractive context + 25-50% generation (hardware-adaptive)
    Net: 100% generative
    """
    # Get hardware-adaptive generation config
    try:
        hw = get_hardware_profile()
        hw_gen = hw.get("generation", {})
    except Exception:
        hw_gen = {}

    configs = {
        "lite": {
            "max_tokens": hw_gen.get("lite_max_tokens", 512),
            "temperature": 0.0,
            "extractive_ratio": 0.8,
        },
        "base": {
            # Hardware-adaptive: fewer tokens + higher extractive ratio on CPU
            "max_tokens": hw_gen.get("base_max_tokens", 256),
            "temperature": 0.0,
            "extractive_ratio": hw_gen.get("base_extractive_ratio", 0.5),
        },
        "net": {
            "max_tokens": 1024,
            "temperature": 0.0,
            "extractive_ratio": 0.0,
        },
    }
    return configs.get(mode, configs["base"])


def _truncate_prompt_text(text: str, max_chars: int) -> str:
    if not text or max_chars <= 0:
        return ""

    cleaned = str(text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    if len(cleaned) <= max_chars:
        return cleaned

    cutoff = cleaned[:max_chars]
    natural_breaks = [
        cutoff.rfind("\n\n"),
        cutoff.rfind("\n"),
        cutoff.rfind(". "),
        cutoff.rfind("; "),
        cutoff.rfind(", "),
        cutoff.rfind(" "),
    ]
    best_break = max(natural_breaks)
    if best_break >= int(max_chars * 0.6):
        cutoff = cutoff[:best_break]

    return cutoff.rstrip() + " ..."


def _compact_context_chunks_for_base(
    chunks: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if not chunks:
        return []

    # Use hardware-adaptive limits
    limits = _get_base_context_limits()
    max_chunks = limits["max_chunks"]
    per_chunk_chars = limits["per_chunk_chars"]
    total_context_chars = limits["total_chars"]

    compacted: List[Dict[str, Any]] = []
    total_chars = 0

    for chunk in chunks[:max_chunks]:
        remaining = total_context_chars - total_chars
        if remaining <= 0:
            break

        per_chunk_budget = min(per_chunk_chars, remaining)
        trimmed_content = _truncate_prompt_text(chunk.get("content", ""), per_chunk_budget)
        if not trimmed_content:
            continue

        trimmed_chunk = dict(chunk)
        trimmed_chunk["content"] = trimmed_content
        compacted.append(trimmed_chunk)
        total_chars += len(trimmed_content)

    return compacted


def _format_extractive_passages(
    chunks: List[Dict[str, Any]],
    question: str,
    mode: str,
) -> str:
    """
    Format extracted passages for the given mode.
    
    Lite: Simple formatting for context
    Base: Formatted with citation markers
    Net: Not used (pure generative)
    """
    config = _get_mode_generation_config(mode)
    
    if config["extractive_ratio"] == 0:
        return ""
    
    # Extract relevant passages
    passage_top_k = 5
    if mode == "base":
        passage_top_k = _BASE_EXTRACTIVE_TOP_K

    passages = extract_relevant_passages(
        chunks=chunks,
        question=question,
        top_k=passage_top_k,
        min_length=50,
    )
    
    if not passages:
        return ""
    
    if mode == "lite":
        # Lite mode: Simple formatting for LLM context
        formatted = format_passages_for_display(
            passages,
            include_highlights=False,
            question=question,
        )
        return _truncate_prompt_text(formatted, 4000)
    elif mode == "base":
        # Base mode: keep the extractive block compact so CPU prefill stays
        # responsive even when retrieved chunks contain large tables.
        formatted_passages = []
        total_chars = 0
        for idx, passage in enumerate(passages, 1):
            metadata = passage.get("metadata", {}) or {}
            page = metadata.get("page_number", "?")
            section = str(metadata.get("section") or "").strip()
            content = _truncate_prompt_text(
                passage["content"],
                _BASE_EXTRACTIVE_PER_PASSAGE_CHARS,
            )
            chunk_id = passage["chunk_id"]
            header = f"[Source {idx}: {chunk_id} | Page {page}"
            if section:
                header += f" | Section: {section}"
            header += "]\n"
            block = f"{header}{content}"
            remaining = _BASE_EXTRACTIVE_TOTAL_CHARS - total_chars
            if remaining <= 0:
                break
            if len(block) > remaining:
                available_for_content = max(80, remaining - len(header))
                content = _truncate_prompt_text(content, available_for_content)
                block = f"{header}{content}"
            formatted_passages.append(block)
            total_chars += len(block)
        return "\n\n".join(formatted_passages)
    
    return ""


def _first_prompt_echo_index(text: str) -> int:
    idx = -1
    for marker in _PROMPT_ECHO_STOP_MARKERS:
        i = text.find(marker)
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    return idx


def _detect_repetition(text: str) -> bool:
    """
    Detect if the text contains repeated content (common in over-generation).
    Returns True if repetition is detected.
    """
    if not text or len(text) < 50:
        return False
    
    # Split into sentences/lines
    lines = text.split('\n')
    
    # Check for duplicate lines
    if len(lines) > 3:
        # Check if any line is repeated
        seen_lines = set()
        for line in lines:
            stripped = line.strip()
            if stripped and stripped in seen_lines:
                return True
            seen_lines.add(stripped)
    
    # Check for duplicate paragraphs (groups of lines)
    if len(lines) > 6:
        paragraphs = []
        current_paragraph = []
        for line in lines:
            if line.strip():
                current_paragraph.append(line.strip())
            else:
                if current_paragraph:
                    paragraphs.append(' '.join(current_paragraph))
                    current_paragraph = []
        if current_paragraph:
            paragraphs.append(' '.join(current_paragraph))
        
        # Check for duplicate paragraphs
        seen_paragraphs = set()
        for para in paragraphs:
            if para and para in seen_paragraphs:
                return True
            seen_paragraphs.add(para)
    
    return False


def _clean_unwanted_sentences(answer: str, question: str) -> str:
    """
    Post-process the LLM answer to remove unwanted sentences.
    
    This analyzes the answer and removes sentences that:
    - Don't directly answer the question
    - Are repetitive
    - Are irrelevant context or explanations
    """
    if not answer:
        return answer
    
    # Split into sentences
    import re
    sentences = re.split(r'(?<=[.!?])\s+', answer)
    
    if len(sentences) <= 2:
        return answer  # Keep short answers as-is
    
    # Analyze question keywords
    question_lower = question.lower()
    question_terms = set(re.findall(r'\b\w+\b', question_lower))
    stop_words = {"where", "what", "how", "when", "why", "is", "are", "the", "a", "an", "in", "at", "on", "to", "for", "of", "with"}
    question_terms = question_terms - stop_words
    
    # Score each sentence based on relevance to question
    scored_sentences = []
    for idx, sentence in enumerate(sentences):
        sentence_lower = sentence.lower()
        
        # Calculate relevance score
        relevance = 0.0
        if question_terms:
            term_count = sum(1 for term in question_terms if term in sentence_lower)
            relevance = term_count / len(question_terms)
        
        # Penalize sentences that start with transition words (likely unnecessary)
        if any(sentence_lower.startswith(word) for word in ["additionally", "furthermore", "moreover", "however", "also"]):
            relevance *= 0.5
        
        # Penalize sentences that are very short (likely fragments)
        if len(sentence.split()) < 3:
            relevance *= 0.3
        
        scored_sentences.append((relevance, idx, sentence))
    
    # Sort by relevance
    scored_sentences.sort(key=lambda x: x[0], reverse=True)
    
    # Keep top relevant sentences (at least 2, up to 4)
    keep_count = min(4, max(2, len(scored_sentences)))
    kept = scored_sentences[:keep_count]
    
    # Sort back by original order
    kept.sort(key=lambda x: x[1])
    
    # Reconstruct answer
    cleaned = ' '.join([s[2] for s in kept])
    
    return cleaned


def _rephrase_question_with_intent(question: str, policy) -> str:
    """
    Rephrase the question using intent policy for better LLM understanding.
    
    This uses the answer policy to understand the question's intent
    and rephrase it for clarity.
    """
    # Get intent from policy
    intent_str = str(policy).lower() if policy else ""
    
    # Rephrase based on intent
    if "fact_lookup" in intent_str or "lookup" in intent_str:
        # For factual questions, make it explicit
        if question.lower().startswith("what"):
            return f"What specifically is {question[5:].strip()}?"
        elif question.lower().startswith("where"):
            return f"Where exactly is {question[6:].strip()} located?"
        elif question.lower().startswith("how"):
            return f"How exactly does {question[4:].strip()} work?"
    
    # For other intents, return original question
    return question


def _validate_answer_relevance(answer: str, question: str) -> tuple[bool, str]:
    """
    Validate if the answer is relevant to the question.
    
    Returns:
        (is_relevant, cleaned_answer)
    """
    if not answer:
        return False, ""
    
    # Simple relevance check based on keyword overlap
    import re
    question_lower = question.lower()
    question_terms = set(re.findall(r'\b\w+\b', question_lower))
    stop_words = {"where", "what", "how", "when", "why", "is", "are", "the", "a", "an", "in", "at", "on", "to", "for", "of", "with"}
    question_terms = question_terms - stop_words
    
    answer_lower = answer.lower()
    
    # Calculate keyword overlap
    if question_terms:
        overlap_count = sum(1 for term in question_terms if term in answer_lower)
        overlap_ratio = overlap_count / len(question_terms)
        
        # If less than 30% overlap, consider it irrelevant
        if overlap_ratio < 0.3:
            return False, answer
    
    return True, answer


def _score_answer_quality(answer: str, question: str) -> float:
    """
    Score an answer based on quality metrics.
    
    Returns:
        Quality score (0.0 to 1.0)
    """
    if not answer:
        return 0.0
    
    import re
    
    score = 0.0
    
    # 1. Relevance score (keyword overlap)
    question_lower = question.lower()
    question_terms = set(re.findall(r'\b\w+\b', question_lower))
    stop_words = {"where", "what", "how", "when", "why", "is", "are", "the", "a", "an", "in", "at", "on", "to", "for", "of", "with"}
    question_terms = question_terms - stop_words
    
    answer_lower = answer.lower()
    if question_terms:
        overlap_count = sum(1 for term in question_terms if term in answer_lower)
        relevance_score = overlap_count / len(question_terms)
        score += 0.4 * relevance_score  # 40% weight for relevance
    
    # 2. Completeness score (answer length)
    answer_words = len(answer.split())
    if answer_words >= 10:
        score += 0.2  # Good length
    elif answer_words >= 5:
        score += 0.1  # Acceptable length
    else:
        score += 0.05  # Too short
    
    # 3. Clarity score (no repetition, proper grammar indicators)
    sentences = re.split(r'(?<=[.!?])\s+', answer)
    if len(sentences) >= 2 and len(sentences) <= 5:
        score += 0.2  # Good sentence structure
    elif len(sentences) == 1:
        score += 0.1  # Single sentence, acceptable
    
    # 4. No repetition penalty
    if _detect_repetition(answer):
        score -= 0.3  # Heavy penalty for repetition
    
    # 5. No transition word penalty (unless necessary)
    if any(answer_lower.startswith(word) for word in ["additionally", "furthermore", "moreover"]):
        score -= 0.1
    
    # Ensure score is between 0 and 1
    return max(0.0, min(1.0, score))


def _score_sentence(sentence: str, question: str) -> float:
    """
    Score a single sentence based on relevance and quality.
    
    Returns:
        Quality score (0.0 to 1.0)
    """
    if not sentence or len(sentence.strip()) < 5:
        return 0.0
    
    import re
    score = 0.0
    
    # 1. Relevance score (keyword overlap)
    question_lower = question.lower()
    question_terms = set(re.findall(r'\b\w+\b', question_lower))
    stop_words = {"where", "what", "how", "when", "why", "is", "are", "the", "a", "an", "in", "at", "on", "to", "for", "of", "with"}
    question_terms = question_terms - stop_words
    
    sentence_lower = sentence.lower()
    if question_terms:
        overlap_count = sum(1 for term in question_terms if term in sentence_lower)
        relevance_score = overlap_count / len(question_terms)
        score += 0.5 * relevance_score  # 50% weight for relevance
    
    # 2. Length score (not too short, not too long)
    words = len(sentence.split())
    if 5 <= words <= 20:
        score += 0.3  # Good length
    elif 3 <= words <= 30:
        score += 0.2  # Acceptable
    else:
        score += 0.1  # Too short or too long
    
    # 3. Completeness (ends with punctuation)
    if sentence.strip().endswith(('.', '!', '?')):
        score += 0.2  # Complete sentence
    
    return max(0.0, min(1.0, score))


def _llm_evaluate_sentence_meaning(sentence: str, question: str, llm: dict) -> bool:
    """
    Use LLM to evaluate if a sentence has meaning in relation to the question.
    
    Returns:
        True if sentence has meaning in relation to question, False otherwise
    """
    if not sentence or not question:
        return False
    
    # Create evaluation prompt
    eval_prompt = f"""You are evaluating if a sentence has meaning in relation to a question.

Question: "{question}"

Sentence: "{sentence}"

Does this sentence have meaning in relation to the question? 
- If the sentence provides information that answers or relates to the question, respond with "YES"
- If the sentence is unrelated, repetitive of the question, or provides no meaningful information, respond with "NO"

Respond with only "YES" or "NO" (no explanation)."""
    
    try:
        # Get LLM response
        response_parts = []
        for chunk in llm["llm"](
            eval_prompt,
            max_tokens=10,
        ):
            if isinstance(chunk, dict):
                text = chunk.get("choices", [{}])[0].get("text", "")
            elif isinstance(chunk, str):
                text = chunk
            if text:
                response_parts.append(text)
        
        response = "".join(response_parts).strip().upper()
        print(f"[LLM EVALUATION] Sentence: {sentence[:50]}... -> Response: {response}")
        
        return "YES" in response
    except Exception as e:
        print(f"[LLM EVALUATION] Error evaluating sentence: {e}")
        # Fallback to keyword overlap if LLM evaluation fails
        is_relevant, _ = _validate_answer_relevance(sentence, question)
        return is_relevant


def _llm_self_query(question: str, llm: dict) -> str:
    """
    Make LLM understand the prompt and write queries to itself about how to answer.
    
    Returns:
        Self-queries generated by LLM to understand and plan the answer
    """
    if not question:
        return ""
    
    # Create self-query prompt
    self_query_prompt = f"""You are Kavin, a helpful assistant. Before answering, understand the user's question and write a few queries to yourself about how to answer it.

User's question: "{question}"

Write 2-3 queries to yourself about how to approach answering this question. For example:
- "What information is needed to answer this question?"
- "What is the main intent behind this question?"
- "What structure should the answer have?"

Write your self-queries below:"""
    
    try:
        # Get LLM response
        response_parts = []
        for chunk in llm["llm"](
            self_query_prompt,
            max_tokens=100,
        ):
            if isinstance(chunk, dict):
                text = chunk.get("choices", [{}])[0].get("text", "")
            elif isinstance(chunk, str):
                text = chunk
            if text:
                response_parts.append(text)
        
        self_queries = "".join(response_parts).strip()
        print(f"[SELF-QUERY] Generated self-queries:\n{self_queries}")
        
        return self_queries
    except Exception as e:
        print(f"[SELF-QUERY] Error generating self-queries: {e}")
        return ""


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


def _finalize_fact_answer(
    parts: List[str],
    *,
    question: Optional[str] = None,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    verbosity: str,
) -> str:
    raw = clean_model_output("".join(parts or []))
    compact = apply_response_policy(raw, verbosity=verbosity)
    final = (compact or raw or "").strip()
    print(
        f"[FACT FINALIZE] raw_chars={len(raw)} | compact_chars={len(compact)} | "
        f"final_chars={len(final)} | verbosity={verbosity}"
    )
    if not final:
        return ""

    strict_factual = bool(question and infer_answer_policy(question).strict_factual)
    try:
        settings = get_dev_settings()
    except Exception:
        settings = {}

    if question and context_chunks and (
        strict_factual or bool(settings.get("enable_eval_gate", False))
    ):
        try:
            from backend.llm.orchestrator import release_strict_factual_answer

            return release_strict_factual_answer(
                question=question,
                answer=final,
                context_chunks=context_chunks,
                verbosity=verbosity,
            )
        except Exception as exc:
            print(f"[ORCH] Strict factual release fallback: {exc}")

    return final


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
    
    # Get mode-specific configuration
    mode_config = _get_mode_generation_config(model)
    
    # Apply mode-specific max_tokens
    mode_max_tokens = min(max_tokens, mode_config["max_tokens"])

    # --- DEBUG: VERIFY CHUNKS ---
    chunk_count = len(context_chunks) if context_chunks else 0
    print(f"[GENERATE DEBUG] Context chunks = {chunk_count}")
    print(f"[GENERATE DEBUG] Mode = {model}, Config max_tokens = {mode_config['max_tokens']}")
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

    try:
        settings = get_dev_settings()
    except Exception:
        settings = {}

    enable_agent_pipeline = bool(settings.get("enable_agent_pipeline", False))
    enable_eval_gate = bool(settings.get("enable_eval_gate", False))
    enable_extractive_rag = bool(settings.get("enable_extractive_rag", True))

    policy = infer_answer_policy(question)
    style = decide_answer_style(question, context_chunks)
    context_text = _context_to_text(context_chunks)
    # Dynamic temperature based on intent policy
    dynamic_temp = 0.0 if policy.strict_factual else 0.4
    
    enforce_compact_fact_answer = bool(
        policy.strict_factual
        and context_chunks
        and style.verbosity == "one_line"
        and model != "net"
    )

    # Rephrase question using intent policy for better LLM understanding
    rephrased_question = _rephrase_question_with_intent(question, policy)
    if rephrased_question != question:
        print(f"[INTENT] Original question: {question}")
        print(f"[INTENT] Rephrased question: {rephrased_question}")
    else:
        rephrased_question = question  # Use original if no rephrasing needed

    # Apply extractive formatting for Lite and Base modes if enabled
    extractive_text = ""
    if enable_extractive_rag and context_chunks and mode_config["extractive_ratio"] > 0:
        extractive_text = _format_extractive_passages(context_chunks, rephrased_question, model)
        print(f"[EXTRACTIVE DEBUG] Extractive text generated: {len(extractive_text)} chars")
        print(f"[EXTRACTIVE DEBUG] Extractive text preview: {extractive_text[:200] if extractive_text else 'EMPTY'}...")
        # Integrate extractive passages into context for LLM
        if extractive_text:
            context_text = f"{context_text}\n\nEXTRACTED PASSAGES:\n{extractive_text}"

    prompt_context_chunks = context_chunks
    if model == "base" and context_chunks:
        prompt_context_chunks = _compact_context_chunks_for_base(context_chunks)
        raw_context_chars = sum(len(str(chunk.get("content") or "")) for chunk in context_chunks)
        compact_context_chars = sum(
            len(str(chunk.get("content") or "")) for chunk in (prompt_context_chunks or [])
        )
        print(
            f"[BASE PROMPT] Context compacted | chunks={len(context_chunks)}->{len(prompt_context_chunks)} "
            f"| chars={raw_context_chars}->{compact_context_chars} | extractive_chars={len(extractive_text)}"
        )

    if model in ("lite", "base") and policy.strict_factual and context_chunks:
        try:
            from backend.llm.orchestrator import resolve_strict_factual_answer

            deterministic = resolve_strict_factual_answer(
                question=question,
                context_chunks=context_chunks,
                verbosity=style.verbosity,
            )
            if deterministic and deterministic.get("final_answer"):
                resolved_mode = str(deterministic.get("mode") or "support_passage")
                print(
                    f"[{model.upper()} FACT] Deterministic factual answer resolved before model generation "
                    f"| mode={resolved_mode}"
                )
                yield str(deterministic["final_answer"])
                return
        except Exception as exc:
            print(f"[{model.upper()} FACT] Deterministic factual fallback failed: {exc}")

    # Hardware-adaptive extractive-only decision for Base mode
    # On CPU (medium/low tier), prefer extractive-only for short answers to skip LLM generation
    try:
        hw_prefer_extractive = get_hardware_profile().get("generation", {}).get("prefer_extractive_only", False)
    except Exception:
        hw_prefer_extractive = not HAS_GPU

    base_use_extractive_only = bool(
        model == "base"
        and extractive_text
        and (
            policy.strict_factual
            or (hw_prefer_extractive and style.verbosity in ("one_line", "short"))
        )
    )
    base_prompt_context_chunks = prompt_context_chunks
    if base_use_extractive_only:
        base_prompt_context_chunks = None
        print(
            f"[BASE PROMPT] Using extractive-only prompt | strict_factual={policy.strict_factual} "
            f"| hw_prefer_extractive={hw_prefer_extractive}"
        )

    # Targeted factual questions (e.g. "In Section 4.4, what specific factor...")
    # now resolve to one_line + strict_factual. Without a tight cap, Lite/GGUF often
    # continues with unrelated table/pressure context.
    if policy.strict_factual and style.verbosity == "one_line" and context_chunks:
        mode_max_tokens = min(mode_max_tokens, 160)
    if model == "base" and policy.strict_factual and not HAS_GPU:
        mode_max_tokens = min(mode_max_tokens, 96)

    if enable_eval_gate and enforce_compact_fact_answer:
        mode_max_tokens = min(mode_max_tokens, 96)
        try:
            from backend.llm.orchestrator import resolve_strict_factual_answer

            deterministic = resolve_strict_factual_answer(
                question=question,
                context_chunks=context_chunks,
                verbosity=style.verbosity,
            )
            if deterministic and deterministic.get("final_answer"):
                yield str(deterministic["final_answer"])
                return
        except Exception as exc:
            print(f"[ORCH] Strict factual heuristic fallback: {exc}")

    if model in ("lite", "base") and enable_agent_pipeline and not enforce_compact_fact_answer:
        try:
            from backend.llm.orchestrator import run_agentic_review_pipeline

            reviewed = run_agentic_review_pipeline(
                question=question,
                requested_model_id=model_id,
                context_chunks=base_prompt_context_chunks if model == "base" else prompt_context_chunks,
                chat_history=chat_history,
                verbosity=style.verbosity,
                session_id=session_id,
            )
            if reviewed and reviewed.get("final_answer"):
                yield str(reviewed["final_answer"])
                return
        except Exception as exc:
            print(f"[ORCH] Agentic review fallback: {exc}")

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
            if enable_agent_pipeline and not enforce_compact_fact_answer:
                prompt = build_prompt_cot(
                    question,
                    base_prompt_context_chunks if model == "base" else prompt_context_chunks,
                    chat_history,
                )
            else:
                # Use specialized prompt builders for extractive RAG modes
                if enable_extractive_rag and extractive_text and model == "lite":
                    prompt = build_prompt_lite_formatting(question, extractive_text)
                elif enable_extractive_rag and extractive_text and model == "base":
                    prompt = build_prompt_base_citation(
                        question,
                        base_prompt_context_chunks,
                        extractive_text,
                        chat_history,
                        style,
                    )
                else:
                    prompt = build_prompt_hf(
                        question,
                        base_prompt_context_chunks if model == "base" else prompt_context_chunks,
                        chat_history,
                        answer_style=style,
                    )

            try:
                if model == "net":
                    if not session_id:
                        yield UI_EVENT_PREFIX + json.dumps(
                           error_event("Session required for Net mode.")
                          ) + "\n"
                        return
                    try:
                        provider = get_active_net_provider()
                        buffered_parts: List[str] = []

                        for token in generate_net_answer_stream(
                            prompt=prompt,
                            provider=provider,
                            variant="rank_1",
                            max_tokens=min(mode_max_tokens, NET_MAX_TOKENS),
                        ):
                            if is_aborted(session_id):
                                break
                            if token:
                                if enforce_compact_fact_answer:
                                    buffered_parts.append(token)
                                else:
                                    yielded_anything = True
                                    collected.append(token)
                                    yield token

                        if enforce_compact_fact_answer:
                            final = _finalize_fact_answer(
                                buffered_parts,
                                question=question,
                                context_chunks=context_chunks,
                                verbosity=style.verbosity,
                            )
                            if final:
                                yielded_anything = True
                                collected.append(final)
                                yield final
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
                    # Initialize streaming stop detector for base models
                    stop_detector = StreamingStopDetector()
                    stream_text = ""
                    emitted_len = 0
                    buffered_parts: List[str] = []
                    
                    for t in hf_stream_generate(
                        model_id=model_id,
                        prompt=prompt,
                        max_new_tokens=mode_max_tokens,
                        session_id=session_id,
                    ):
                        if session_id and is_aborted(session_id):
                            yield ""  # allow UI to close stream cleanly
                            return
                        if not t:
                            continue

                        stream_text += t
                        
                        # Use new stop detection system
                        should_stop, stop_reason, _ = stop_detector.process_token(t)
                        
                        # Check for prompt echo (legacy compatibility)
                        stop_idx = _first_prompt_echo_index(stream_text)
                        
                        if should_stop or stop_idx >= 0:
                            safe = stream_text[:stop_idx] if stop_idx >= 0 else stream_text
                            safe = clean_llm_response(safe)  # Use new cleaning system
                            delta = safe[emitted_len:]
                            if delta:
                                if enforce_compact_fact_answer:
                                    buffered_parts.append(delta)
                                else:
                                    yielded_anything = True
                                    collected.append(delta)
                                    yield delta
                            if should_stop:
                                print(f"[STOP] Base generation stopped: {stop_reason}")
                            break

                        # Hold back a small tail to avoid leaking partial markers.
                        safe_end = max(0, len(stream_text) - (_PROMPT_ECHO_TAIL - 1))
                        if safe_end > emitted_len:
                            delta = stream_text[emitted_len:safe_end]
                            if delta:
                                if enforce_compact_fact_answer:
                                    buffered_parts.append(delta)
                                else:
                                    yielded_anything = True
                                    collected.append(delta)
                                    yield delta
                            emitted_len = safe_end

                    if emitted_len < len(stream_text):
                        tail = stream_text[emitted_len:]
                        tail = clean_llm_response(tail)  # Use new cleaning system
                        tail_stop_idx = _first_prompt_echo_index(tail)
                        if tail_stop_idx >= 0:
                            tail = tail[:tail_stop_idx]
                        if tail:
                            if enforce_compact_fact_answer:
                                buffered_parts.append(tail)
                            else:
                                yielded_anything = True
                                collected.append(tail)
                                yield tail

                    if enforce_compact_fact_answer:
                        final = _finalize_fact_answer(
                            buffered_parts,
                            question=question,
                            context_chunks=context_chunks,
                            verbosity=style.verbosity,
                        )
                        if final:
                            yielded_anything = True
                            collected.append(final)
                            yield final


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
                if model == "base":
                    yield UI_EVENT_PREFIX + json.dumps(
                        text_event(
                            "Base mode took too long to start on this machine. Try Lite or ask a narrower document question."
                        )
                    ) + "\n"
                else:
                    yield "No answer could be generated from the documents."
                return

            # --- Grounding check (soft warning, non-blocking) ---
            if enable_eval_gate and context_chunks and collected:
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
        mode_max_tokens = min(mode_max_tokens, 128)
    if model == "base":
        mode_max_tokens = min(mode_max_tokens, 512 if context_chunks else 256)

    # -------- Advanced reasoning (optional)
    if enable_agent_pipeline and ADVANCED_REASONING and not _is_conversational(intent):
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
    if model == "lite":
        # 🤖 AGENTIC: Get user state and adjust generation parameters (with error handling)
        try:
            user_state = get_or_create_user_state(session_id or "default")
            
            # Adjust verbosity based on user's preferred detail level
            if user_state.get("preferred_detail_level") == "low":
                style = style._replace(verbosity="concise")
            elif user_state.get("preferred_detail_level") == "high":
                style = style._replace(verbosity="detailed")
            
            # Increment interaction count for learning
            increment_interaction_count(session_id or "default")
            
            print(f"[AGENTIC] User state: expertise={user_state.get('expertise_level')}, detail={user_state.get('preferred_detail_level')}")
        except Exception as e:
            print(f"[AGENTIC] Error loading user state (non-fatal): {e}")
        
        # Use specialized prompt builder for extractive RAG
        if enable_extractive_rag and extractive_text and context_chunks:
            prompt = build_prompt_lite_formatting(rephrased_question, extractive_text)
        elif not context_chunks:
            # Absolute-safe fallback for normal chat (NO indentation inside prompt)
            prompt = build_prompt_gguf(
                question=rephrased_question,
                context_chunks=context_chunks,
                answer_style=style,
            )
        else:
            prompt = build_prompt_gguf(
                question=rephrased_question,
                context_chunks=context_chunks,
                answer_style=style,
            )
    else:
        prompt = _build_prompt(rephrased_question, model, context_chunks, chat_history)
    
    # Post-processing flag for Lite mode
    use_post_processing = bool(
        model == "lite"
        and enable_extractive_rag
        and context_chunks
        and not enforce_compact_fact_answer
    )
    
    prompt = prompt.rstrip() + "\n"
    print(
        f"[PROMPT DEBUG] model={model} | prompt_chars={len(prompt)} | "
        f"context_chunks_for_prompt={len(prompt_context_chunks or []) if 'prompt_context_chunks' in locals() else len(context_chunks or [])}"
    )

    
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
            gguf_started_at = time.time()
            gguf_piece_count = 0
            gguf_char_count = 0
            gguf_finish_reason = "completed"
            gguf_finish_logged = False

            def _log_gguf_finish(reason: Optional[str] = None) -> None:
                nonlocal gguf_finish_reason, gguf_finish_logged
                if gguf_finish_logged:
                    return
                if reason:
                    gguf_finish_reason = reason
                elapsed = time.time() - gguf_started_at
                print(
                    f"[GGUF] Generation finished | model={model_id} | "
                    f"pieces={gguf_piece_count} | chars={gguf_char_count} | "
                    f"elapsed={elapsed:.2f}s | reason={gguf_finish_reason}"
                )
                gguf_finish_logged = True

            print(
                f"[GGUF] Starting generation | model={model_id} | "
                f"prompt_chars={len(prompt)} | max_tokens={mode_max_tokens} | "
                f"post_process={use_post_processing} | compact_fact={enforce_compact_fact_answer}"
            )

            # Use comprehensive stop tokens from new stop generation system
            comprehensive_stop_tokens = get_stop_tokens_for_model("gguf")
            
            # Initialize streaming stop detector
            stop_detector = StreamingStopDetector()
            buffered_parts: List[str] = []
            
            # For post-processing mode, collect full answer first
            if use_post_processing:
                full_answer_parts: List[str] = []
                
                for chunk in llm["llm"](
                    prompt,
                    max_tokens=mode_max_tokens,
                    stop=comprehensive_stop_tokens,
                ):
                    if session_id and is_aborted(session_id):
                        _log_gguf_finish("aborted")
                        yield ""
                        return
                    text = ""
                    if isinstance(chunk, dict):
                        text = chunk.get("choices", [{}])[0].get("text", "")
                    elif isinstance(chunk, str):
                        text = chunk

                    if text:
                        print("TOKEN:", repr(text))
                        gguf_piece_count += 1
                        gguf_char_count += len(text)
                        full_answer_parts.append(text)
                
                # Combine full answer
                full_answer = "".join(full_answer_parts)
                print(f"[POST-PROCESSING] Full answer length: {len(full_answer)} chars")
                print(f"[POST-PROCESSING] Full answer content:\n{full_answer}")
                
                # Split answer into sentences (no limit)
                import re
                sentences = re.split(r'(?<=[.!?])\s+', full_answer)
                sentences = [s.strip() for s in sentences if s.strip()]
                
                # 🤖 AGENTIC: Send step update to frontend
                yield UI_EVENT_PREFIX + json.dumps(agentic_step_event("Splitting into sentences")) + "\n"
                
                print(f"[POST-PROCESSING] Split into {len(sentences)} sentences")
                
                # Split user question into words and store in memory
                question_words = set(re.findall(r'\b\w+\b', rephrased_question.lower()))
                stop_words = {
                    "where", "what", "how", "when", "why", "is", "are", "the", "a", "an",
                    "in", "at", "on", "to", "for", "of", "with", "according", "specific",
                    "technical", "made", "was", "were", "list", "change",
                }
                question_words = question_words - stop_words
                print(f"[WORD-LEVEL] User question words: {question_words}")
                
                # 🤖 AGENTIC: Send step update to frontend
                yield UI_EVENT_PREFIX + json.dumps(agentic_step_event("Analyzing word overlap")) + "\n"
                
                # Calculate word overlap for each sentence
                sentence_overlap_info = []
                for idx, sentence in enumerate(sentences):
                    sentence_words = set(re.findall(r'\b\w+\b', sentence.lower()))
                    matching_words = question_words.intersection(sentence_words)
                    overlap_ratio = len(matching_words) / len(question_words) if question_words else 0
                    sentence_overlap_info.append((idx, sentence, matching_words, overlap_ratio))
                    print(f"[SENTENCE {idx + 1}] Word overlap: {len(matching_words)}/{len(question_words)} ({overlap_ratio:.2f}) - {sentence[:50]}...")
                
                # Use threshold-based selection, but never allow the filter to
                # erase the whole answer. That empty-output failure surfaced as
                # a fake "How can I help you?" fallback in the UI.
                relevant_sentences = []
                removed_sentences = []
                for idx, sentence, matching_words, overlap_ratio in sentence_overlap_info:
                    # Keep sentence if it has decent lexical overlap or enough
                    # direct term hits. This is intentionally more forgiving
                    # than the previous 60% threshold.
                    if overlap_ratio >= 0.3 or len(matching_words) >= 2:
                        relevant_sentences.append((idx, sentence))
                        print(f"[SENTENCE {idx + 1}] MATCHING (keeping) - {sentence[:50]}...")
                    else:
                        removed_sentences.append(sentence)
                        print(f"[SENTENCE {idx + 1}] NOT MATCHING (removing) - {sentence[:50]}...")

                if not relevant_sentences:
                    fallback_scored: List[tuple[float, int, str]] = []
                    for idx, sentence, matching_words, overlap_ratio in sentence_overlap_info:
                        sentence_lower = sentence.lower()
                        score = overlap_ratio
                        if "according to the document" in sentence_lower:
                            score += 0.2
                        if any(ch.isdigit() for ch in sentence):
                            score += 0.1
                        if len(sentence.split()) >= 4:
                            score += 0.05
                        fallback_scored.append((score, idx, sentence))

                    fallback_scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                    best_fallback = fallback_scored[:1]
                    relevant_sentences = [(idx, sentence) for _, idx, sentence in best_fallback if sentence.strip()]
                    print(
                        f"[POST-PROCESSING] No sentences passed overlap filter; "
                        f"falling back to top-scored sentence count={len(relevant_sentences)}"
                    )
                
                # 🤖 AGENTIC: Log self-correction for removed sentences (with error handling)
                if removed_sentences and session_id:
                    try:
                        removed_text = " | ".join([s[:100] for s in removed_sentences])
                        log_self_correction(
                            session_id=session_id,
                            correction_type="sentence_filtering",
                            original_text=full_answer,
                            corrected_text="".join([s[1] for s in relevant_sentences]),
                            reason=f"Removed {len(removed_sentences)} sentences with <60% word overlap: {removed_text}"
                        )
                    except Exception as e:
                        print(f"[AGENTIC] Error logging self-correction (non-fatal): {e}")
                
                print(f"[POST-PROCESSING] Kept {len(relevant_sentences)} matching sentences out of {len(sentences)}")
                
                # 🤖 AGENTIC: Send step update to frontend
                yield UI_EVENT_PREFIX + json.dumps(agentic_step_event("Combining relevant sentences")) + "\n"
                
                # Sort back by original order
                relevant_sentences.sort(key=lambda x: x[0])
                
                # Combine relevant sentences
                combined_answer = ' '.join([s[1] for s in relevant_sentences])
                if not combined_answer.strip():
                    combined_answer = full_answer.strip()
                
                # 🤖 AGENTIC: Send step update to frontend
                yield UI_EVENT_PREFIX + json.dumps(agentic_step_event("Fixing grammar")) + "\n"
                
                # Fix grammar (basic cleanup)
                # Ensure sentences end with proper punctuation
                import re
                grammar_fixed = re.sub(r'\s+([.!?])', r'\1', combined_answer)  # Remove space before punctuation
                grammar_fixed = re.sub(r'([.!?])\s*([a-z])', r'\1 \2', grammar_fixed)  # Ensure space after punctuation
                
                # 🤖 AGENTIC: Log grammar corrections if any changes were made (with error handling)
                if grammar_fixed != combined_answer and session_id:
                    try:
                        log_self_correction(
                            session_id=session_id,
                            correction_type="grammar_fixing",
                            original_text=combined_answer,
                            corrected_text=grammar_fixed,
                            reason="Fixed punctuation and spacing issues"
                        )
                    except Exception as e:
                        print(f"[AGENTIC] Error logging grammar correction (non-fatal): {e}")
                
                print(f"[POST-PROCESSING] Grammar-fixed answer length: {len(grammar_fixed)} chars")
                print(f"[POST-PROCESSING] Grammar-fixed answer content:\n{grammar_fixed}")
                
                # 🤖 AGENTIC: Generate proactive suggestions based on context (with error handling)
                if session_id and context_chunks:
                    try:
                        suggestion_text = f"Based on your question about '{rephrased_question}', you might also be interested in: related information about the document, other similar topics, or more details about the mentioned entities."
                        save_proactive_suggestion(
                            session_id=session_id,
                            suggestion_type="follow_up",
                            suggestion_text=suggestion_text,
                            context={"question": rephrased_question, "chunk_count": len(context_chunks)}
                        )
                    except Exception as e:
                        print(f"[AGENTIC] Error saving proactive suggestion (non-fatal): {e}")
                
                # Stream final answer character by character for UI consistency
                for char in grammar_fixed:
                    if session_id and is_aborted(session_id):
                        _log_gguf_finish("aborted")
                        yield ""
                        return
                    collected.append(char)
                    yield char
            else:
                # Normal streaming mode
                for chunk in llm["llm"](
                    prompt,
                    max_tokens=mode_max_tokens,
                    stop=comprehensive_stop_tokens,
                ):
                    if session_id and is_aborted(session_id):
                        _log_gguf_finish("aborted")
                        yield ""
                        return
                    text = ""
                    if isinstance(chunk, dict):
                        text = chunk.get("choices", [{}])[0].get("text", "")
                    elif isinstance(chunk, str):
                        text = chunk

                    if text:
                        print("TOKEN:", repr(text))  # ✅ NOW SAFE
                        gguf_piece_count += 1
                        gguf_char_count += len(text)
                        
                        # Process through stop detector
                        should_stop, stop_reason, cleaned_text = stop_detector.process_token(text)
                        
                        if should_stop:
                            print(f"[STOP] Generation stopped: {stop_reason}")
                            gguf_finish_reason = f"stop_detector:{stop_reason}"
                            # Emit any remaining cleaned text
                            if cleaned_text and not enforce_compact_fact_answer:
                                remaining = cleaned_text[len("".join(collected)):]
                                if remaining:
                                    collected.append(remaining)
                                    yield remaining
                            break
                        
                        if enforce_compact_fact_answer:
                            buffered_parts.append(text)
                        else:
                            collected.append(text)
                            yield text
            if enforce_compact_fact_answer:
                final = _finalize_fact_answer(
                    buffered_parts,
                    question=question,
                    context_chunks=context_chunks,
                    verbosity=style.verbosity,
                )
                if final:
                    collected.append(final)
                    yield final

            _log_gguf_finish()

        else:
            # Initialize streaming stop detector for HF models
            stop_detector = StreamingStopDetector()
            stream_text = ""
            emitted_len = 0
            buffered_parts: List[str] = []
            
            for t in hf_stream_generate(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=mode_max_tokens,
                session_id=session_id,
            ):
                if session_id and is_aborted(session_id):
                    yield ""  # allow UI to close stream cleanly
                    return
                if not t:
                    continue

                stream_text += t
                
                # Use new stop detection system
                should_stop, stop_reason, _ = stop_detector.process_token(t)
                
                # Check for prompt echo (legacy compatibility)
                stop_idx = _first_prompt_echo_index(stream_text)
                
                if should_stop or stop_idx >= 0:
                    safe = stream_text[:stop_idx] if stop_idx >= 0 else stream_text
                    safe = clean_llm_response(safe)  # Use new cleaning system
                    delta = safe[emitted_len:]
                    if delta:
                        if enforce_compact_fact_answer:
                            buffered_parts.append(delta)
                        else:
                            collected.append(delta)
                            yield delta
                    if should_stop:
                        print(f"[STOP] HF generation stopped: {stop_reason}")
                    break

                safe_end = max(0, len(stream_text) - (_PROMPT_ECHO_TAIL - 1))
                if safe_end > emitted_len:
                    delta = stream_text[emitted_len:safe_end]
                    if delta:
                        if enforce_compact_fact_answer:
                            buffered_parts.append(delta)
                        else:
                            collected.append(delta)
                            yield delta
                    emitted_len = safe_end

            if emitted_len < len(stream_text):
                tail = stream_text[emitted_len:]
                tail = clean_llm_response(tail)  # Use new cleaning system
                tail_stop_idx = _first_prompt_echo_index(tail)
                if tail_stop_idx >= 0:
                    tail = tail[:tail_stop_idx]
                if tail:
                    if enforce_compact_fact_answer:
                        buffered_parts.append(tail)
                    else:
                        collected.append(tail)
                        yield tail

            if enforce_compact_fact_answer:
                final = _finalize_fact_answer(
                    buffered_parts,
                    question=question,
                    context_chunks=context_chunks,
                    verbosity=style.verbosity,
                )
                if final:
                    collected.append(final)
                    yield final


    except Exception as exc:
        if llm and llm.get("type") == "gguf":
            print(f"[GGUF] Generation finished | model={model_id} | reason=exception | error={exc}")
        yield UI_EVENT_PREFIX + json.dumps(
            text_event("Generation failed.")
        ) + "\n"
        return

    if not collected or not "".join(collected).strip():
        if policy.strict_factual and context_chunks:
            try:
                from backend.llm.orchestrator import release_strict_factual_answer

                fallback = release_strict_factual_answer(
                    question=question,
                    answer="",
                    context_chunks=context_chunks,
                    verbosity=style.verbosity,
                )
                if fallback:
                    yield fallback
                    return
            except Exception as exc:
                print(f"[STRICT FACT] Empty-output fallback failed: {exc}")

        yield UI_EVENT_PREFIX + json.dumps(
            text_event("How can I help you?")
        ) + "\n"
        return

    # --- Grounding check for lite mode (soft warning, non-blocking) ---
    if enable_eval_gate and context_chunks and collected:
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
