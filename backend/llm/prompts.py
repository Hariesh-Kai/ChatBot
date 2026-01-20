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
# CORE SYSTEM PERSONA (BASE)
# ============================================================

CORE_SYSTEM_PROMPT = """
You are KavinBase, a senior engineering assistant.

CITATION RULES (MANDATORY):
- Answer the question using ONLY the provided document context.
- Cite the Page Number for every fact. Format: "The pressure is 50 bar [Page 12]."
- If the document gives a RANGE, report the range clearly.
- If the answer comes from a TABLE, format it as a Markdown Table.

SEMANTIC DISAMBIGUATION RULES:
- Distinguish between the **Document Title** (words describing the scope, e.g., 'Basis of Design') and the **Document Number** (alphanumeric code, e.g., '363010-BGRB').
- If asked for the Title, prefer the descriptive text.
- If asked for the Project Name, look for "Project" or "Field Development".

FORBIDDEN:
- Do NOT guess values or page numbers.
- Do NOT use external knowledge.
- Do NOT include meta commentary like "Based on the text...".
""".strip()


# ============================================================
# 🚀 DYNAMIC STYLES (VERBOSITY CONTROL)
# ============================================================

STYLE_INSTRUCTIONS = {
    "one_line": """
OUTPUT STYLE:
- Extremely concise.
- One single sentence.
- No fluff.
""",
    "short": """
OUTPUT STYLE:
- Professional and direct.
- Maximum 2-3 sentences.
""",
    "normal": """
OUTPUT STYLE:
- Professional and technical.
- Provide a complete answer but remain concise.
- Avoid unnecessary elaboration.
""",
    "detailed": """
OUTPUT STYLE:
- Detailed and comprehensive.
- Explain the concept fully using the document context.
- Break down complex points.
- You may use multiple paragraphs if necessary.
"""
}


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
Document A mentions X is 500 [Page 2].
Document B mentions X is 505 [Page 4].
I should mention the range.
</thinking>
Based on the documents, X ranges between 500 and 505 [Page 2, 4].
""".strip()


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
# SHARED BUILDER (DRY Principle)
# ============================================================

def _build_generic_prompt(question, context_chunks, history, answer_style, is_cot=False):
    
    # ✅ FIX Q4: INJECT PAGE NUMBERS INTO CONTEXT
    if context_chunks:
        context_lines = []
        for c in context_chunks:
            # Extract metadata safely
            meta = c.get("metadata", {})
            page = meta.get("page_number", "?")
            section = meta.get("section", "General")
            content = c.get("content", "")
            
            # Format: [Page 5 | Section: Overview] Content...
            context_lines.append(f"[Page {page} | Section: {section}]\n{content}")
            
        context_text = "\n\n".join(context_lines)
        system_instruction = COT_SYSTEM_PROMPT if is_cot else CORE_SYSTEM_PROMPT
    else:
        # Fallback for "Hi" messages with no docs
        context_text = "No document context available."
        system_instruction = "You are KavinBase, a helpful assistant. Answer politely. Do not hallucinate."

    # Style
    style_key = getattr(answer_style, "verbosity", "short")
    style_instruction = STYLE_INSTRUCTIONS.get(style_key, STYLE_INSTRUCTIONS["short"])

    # Build Prompt
    messages = []
    messages.append(f"<|start_header_id|>system<|end_header_id|>\n{system_instruction}\n\n{style_instruction}\n<|eot_id|>")

    if history:
        for msg in history[-4:]:
            clean_content = clean_model_output(msg['content'])
            role = "user" if msg['role'] == "user" else "assistant"
            messages.append(f"<|start_header_id|>{role}<|end_header_id|>\n{clean_content}<|eot_id|>")

    messages.append(f"""<|start_header_id|>user<|end_header_id|>
CONTEXT:
{context_text}

QUESTION:
{question}<|eot_id|><|start_header_id|>assistant<|end_header_id|>
""")

    return "".join(messages)


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
    return _build_generic_prompt(question, context_chunks, history, answer_style, is_cot=False)


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
    """
    return _build_generic_prompt(question, context_chunks, history, None, is_cot=True)


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
    """
    # GGUF often doesn't need full history or manages it differently, 
    # but we can pass None for history if we want to save context window.
    return _build_generic_prompt(question, context_chunks, None, answer_style, is_cot=False)


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