# backend/llm/prompts.py

"""
Prompt templates and builders for KavinBase / KavinBase Lite.

UPDATED DESIGN PHILOSOPHY:
- LLM-first, RAG-supporting (RAG = evidence, not authority)
- Prefer reasonable, document-supported answers over silence
- Range-based answers ARE allowed if clearly stated
- No hallucination or external knowledge
- Concise, factual, professional output
- No meta commentary in final answer
- CHAIN OF THOUGHT (CoT) enabled for complex reasoning
"""

from typing import List, Dict, Optional

# ============================================================
# UTILS (Moved to top to prevent reference errors)
# ============================================================

def clean_model_output(text: str) -> str:
    """
    Removes model artifacts and meta output safely.
    NOTE: We do NOT strip <thinking> tags here; the frontend handles them.
    """
    if not text:
        return ""

    stop_markers = (
        "<|end|>",
        "<|system|>",
        "<|user|>",
        "<|assistant|>",
        "<|eot_id|>",
        "REFINED ANSWER:",
        "END OF RESPONSE",
    )

    for marker in stop_markers:
        if marker in text:
            text = text.split(marker)[0]  # Take content BEFORE the stop marker

    return text.strip()


# ============================================================
# CORE SYSTEM PERSONA
# ============================================================

CORE_SYSTEM_PROMPT = """
You are KavinBase, a senior engineering assistant.

MANDATORY BEHAVIOR:
- Think silently before answering.
- Answer ONLY the question asked.
- Be concise, factual, and precise.
- Do NOT explain unless explicitly requested.
- Do NOT teach unless explicitly requested.
- Do NOT restate the question.
- Do NOT add filler, summaries, or conclusions.
- Do NOT include meta commentary.

FACT DISCIPLINE:
- Use ONLY the provided document context.
- Do NOT use external knowledge.
- Do NOT fabricate values.

ALLOWED (IMPORTANT):
- If the document gives a RANGE, report the range clearly.
- If the document gives multiple explicit values, summarize them briefly.
- If the document implies a value through a table or specification,
  state it cautiously using the document wording.

FORMATTING RULES:
- If the retrieved information comes from a table, YOU MUST FORMAT YOUR ANSWER AS A MARKDOWN TABLE.
- Do not list tabular data as bullet points. Use a standard Markdown table structure (e.g. | Column 1 | Column 2 |).
- Format numbers clearly (e.g. use "1,000" instead of "1000" if appropriate).

FORBIDDEN:
- Guessing unsupported values
- Introducing new numbers
- Explaining reasoning steps in the final answer
- Self-justifying phrases (e.g., “I inferred”, “no inference was made”)

OUTPUT STYLE:
- Professional
- Technical
- Direct
- One or two sentences unless explicitly asked otherwise (or if a table is required).
""".strip()


# ============================================================
# CHAIN OF THOUGHT (CoT) PERSONA
# ============================================================

COT_SYSTEM_PROMPT = """
You are KavinBase, an expert engineering assistant.

INSTRUCTIONS:
1. You will be provided with a document context and a question.
2. FIRST, think step-by-step inside <thinking> tags. Analyze the documents, check for conflicting data, and plan your answer.
3. SECOND, provide your final response outside the tags.

RULES:
- The user does NOT see the <thinking> section by default, so do not refer to it in your final answer.
- If the document is missing information, admit it in the thinking step, then state it clearly in the answer.
- Keep the final answer professional and concise.
- If the data is tabular, OUTPUT A MARKDOWN TABLE in the final response.

EXAMPLE FORMAT:
<thinking>
The user is asking about X.
Document A mentions X is 500.
Document B mentions X is 505.
I should mention the range.
</thinking>
Based on the documents, X ranges between 500 and 505.
""".strip()


# ============================================================
# DOCUMENT USAGE GUIDANCE
# ============================================================

REASONING_GUIDANCE = """
RULES FOR ANSWERING:
- If the document explicitly states the answer → state it directly.
- If the document provides a clear range → report the range.
- If the document provides tabular values → extract the relevant value.
- If the document partially supports the answer → answer conservatively.
- If the document does not contain the answer → say so briefly.
- Prefer a cautious answer over silence when evidence exists.
""".strip()


# backend/llm/prompts.py

# ============================================================
# PROMPT BUILDERS — Chat Title Naming
# ============================================================

def build_title_prompt(question: str) -> str:
    """
    Zero-shot prompt for summarizing a conversation into a title.
    """
    return f"""<|start_header_id|>system<|end_header_id|>

You are a helpful assistant.
Summarize the user's input into a concise title (maximum 5 words).
Do not answer the question.
Do not use quotes.
Do not use "Title:" prefix.
Just the text.

<|eot_id|><|start_header_id|>user<|end_header_id|>

{question}<|eot_id|><|start_header_id|>assistant<|end_header_id|>
"""


# ============================================================
# PROMPT BUILDERS — HUGGINGFACE (CHAT MODELS)
# ============================================================

def build_prompt_hf(
    question: str,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    history: Optional[List[Dict[str, str]]] = None,
    answer_style: Optional[object] = None,
) -> str:
    """
    Balanced prompt for HF chat models (Standard/Fast Mode).
    """

    if context_chunks:
        context_text = "\n\n".join(
            f"[{c.get('section', 'Unknown')}]\n{c['content']}"
            for c in context_chunks
            if c.get("content")
        )
    else:
        context_text = "Document context is limited or unavailable."

    messages = []

    messages.append(
        f"<|system|>\n{CORE_SYSTEM_PROMPT}\n\n{REASONING_GUIDANCE}\n<|end|>"
    )

    if history:
        for msg in history[-4:]:
            messages.append(
                f"<|{msg['role']}|>\n{msg['content']}\n<|end|>"
            )

    messages.append(
        f"""
<|user|>
DOCUMENT CONTEXT:
{context_text}

QUESTION:
{question}
<|end|>

<|assistant|>
""".strip()
    )

    return "\n".join(messages)


# ============================================================
# PROMPT BUILDERS — CHAIN OF THOUGHT (SMART MODE)
# ============================================================

def build_prompt_cot(
    question: str,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Builds a prompt that forces Chain of Thought reasoning.
    Used for Base/Net models to improve accuracy without multi-call overhead.
    """
    if context_chunks:
        context_text = "\n\n".join(
            f"[{c.get('section', 'Unknown')}]\n{c['content']}"
            for c in context_chunks
            if c.get("content")
        )
    else:
        context_text = "Document context is limited or unavailable."

    # 1. System Prompt (CoT Specific)
    messages = [f"<|system|>\n{COT_SYSTEM_PROMPT}\n<|end|>"]

    # 2. History (Optional context)
    if history:
        for msg in history[-4:]:
            # Clean history to remove previous thinking tags to save context window
            clean_content = clean_model_output(msg['content']) 
            messages.append(f"<|{msg['role']}|>\n{clean_content}\n<|end|>")

    # 3. User Question + Context
    messages.append(
        f"""
<|user|>
DOCUMENT CONTEXT:
{context_text}

QUESTION:
{question}
<|end|>

<|assistant|>
""".strip()
    )

    return "\n".join(messages)


# ============================================================
# PROMPT BUILDERS — GGUF / LLAMA.CPP
# ============================================================

def build_prompt_gguf(
    question: str,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    answer_style: Optional[object] = None,
) -> str:
    """
    Balanced prompt for GGUF models.
    Updated to use strict Llama-3 tokens to prevent infinite looping.
    """

    if context_chunks:
        context_text = "\n".join(
            f"- {c['content']}"
            for c in context_chunks
            if c.get("content")
        )
    else:
        context_text = "No document context provided."

    # 🔥 FIX: Removed <|begin_of_text|> to prevent double-init warning
    return f"""<|start_header_id|>system<|end_header_id|>

You are KavinBase, a senior engineering assistant.
Answer the user's question using ONLY the provided context.
If the answer requires data from a table, FORMAT IT AS A MARKDOWN TABLE.
Be concise. If the answer is not in the context, say so.

CONTEXT:
{context_text}
<|eot_id|><|start_header_id|>user<|end_header_id|>

{question}<|eot_id|><|start_header_id|>assistant<|end_header_id|>
"""


# ============================================================
# REFINEMENT PROMPT (WORDING ONLY)
# ============================================================

def build_refine_prompt(
    question: str,
    draft_answer: str,
) -> str:
    """
    Used ONLY to improve clarity and grammar.
    """

    return f"""
You are a technical editor.

RULES:
- Improve grammar and clarity only.
- Preserve meaning EXACTLY.
- Do NOT add or remove facts.
- Do NOT expand explanations.

QUESTION:
{question}

DRAFT ANSWER:
{draft_answer}

REFINED ANSWER:
""".strip()