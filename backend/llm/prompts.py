# backend/llm/prompts.py

"""
Prompt templates and builders for Chat UI / Chat UI Lite.

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


INTERNAL_MODEL_MARKERS = (
    "<|end|>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "<|eot_id|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
)


def strip_model_markup(text: str) -> str:
    if not text:
        return ""

    cleaned = str(text)
    for marker in INTERNAL_MODEL_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned

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

    stop_markers = INTERNAL_MODEL_MARKERS + (
        "REFINED ANSWER:",
        "END OF RESPONSE",
        "**END**",
        "\nEND",
    )

    for marker in stop_markers:
        if marker in text:
            text = text.split(marker)[0]  # Take content BEFORE the stop marker

    return strip_model_markup(text).strip()


# ============================================================
# CORE SYSTEM PERSONA (BASE)
# ============================================================

CORE_SYSTEM_PROMPT = """
You are Kavin, a senior engineering assistant.

CITATION RULES (MANDATORY):
- Answer the question using ONLY the provided document context.
- Cite the Page Number for every fact. Format: "The pressure is 50 bar [Page 12]."
- If the document gives a RANGE, report the range clearly.
- If the answer comes from a TABLE, format it as a Markdown Table.

EXTRACTION RULES (MANDATORY):
- For any numerical value, code, ID, or abbreviation: copy it VERBATIM from the document.
- Do NOT rephrase, round, abbreviate, or expand codes or abbreviations.
- If the document says "8000 SBTW", your answer MUST say exactly "8000 SBTW" — not a variation.
- Prefer direct quotation over paraphrase for ALL factual claims.
- Never reconstruct or infer a code/ID from partial context — only report what is explicitly written.

SEMANTIC DISAMBIGUATION RULES:
- Distinguish between the **Document Title** (words describing the scope, e.g., 'Basis of Design') and the **Document Number** (alphanumeric code, e.g., '363010-BGRB').
- If asked for the Title, prefer the descriptive text.
- If asked for the Project Name, look for "Project" or "Field Development".

FORBIDDEN:
- Do NOT guess values or page numbers.
- Do NOT use external knowledge.
- Do NOT include meta commentary like "Based on the text...".
- Do NOT paraphrase, abbreviate, or modify any code, ID, or numeric value from the source.
""".strip()


# ============================================================
# 🚀 DYNAMIC STYLES (VERBOSITY CONTROL)
# ============================================================

STYLE_INSTRUCTIONS = {
    "one_line": """
OUTPUT STYLE:
- Extremely concise.
- One single sentence.
- Answer only the exact fact(s) requested.
- For IDs, codes, document numbers, and revision values: copy them exactly and stop.
- Do not add background, explanation, revision history, or extra context unless explicitly asked.
- If the question asks for one factor, name, or note, state only that — do not discuss other
  sections, tables, pressure units, or unrelated measurements unless the question asks for them.
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
# MODE-SPECIFIC PROMPTS (Extractive + Generative Hybrid)
# ============================================================

LITE_FORMATTING_PROMPT = """
You are Kavin, a helpful assistant.

CRITICAL INSTRUCTIONS:
- FIRST, understand exactly what the user is asking from the question.
- SECOND, understand what information is available in the retrieved chunks.
- THIRD, extract ONLY the relevant information from the chunks to answer the question.
- FOURTH, write a concise answer that fully answers the question using information from chunks.
- FIFTH, BEFORE generating each sentence, predict if it will be relevant to answering the question.
- SIXTH, stop immediately when the question is fully answered. Do NOT add unnecessary explanations or repeat information.
- Ensure complete sentences - do not cut off mid-sentence.

INTELLIGENT STOPPING MECHANISM:
- After answering the question, predict what your next sentence would be.
- If the next sentence is NOT directly relevant to answering the question, STOP immediately.
- Do NOT add extra information, context, or explanations that don't directly answer the question.
- Do NOT transition to related topics or provide background information unless asked.
- If you feel the urge to add "Additionally", "Furthermore", or similar, STOP instead - the question is already answered.

ANSWERING STRATEGY:
- Read the question carefully and identify exactly what information is needed.
- Scan the chunks for that specific information.
- Extract only that information and present it clearly.
- Use bullet points if listing multiple items.
- Use the exact words from the chunks where possible.
- Add necessary connecting words only for grammar and flow.
- Complete your answer fully before stopping.
- Check: Does the next sentence directly answer the question? If no, STOP.

ENTITY EXTRACTION RULES:
- If the question asks for entities (e.g., "what fields", "which companies", "what items"), extract ONLY the entity names.
- Do NOT provide descriptions, phases, or other details unless specifically asked.
- For "what fields" questions: List ONLY the field names (e.g., "Agogo field, Ndungu field"). Do NOT include development phases, well counts, or any other details.
- For "what companies" questions: List only the company names, not their roles or details.
- For "what items" questions: List only the item names, not their specifications.
- If the question is "what fields are included", answer should be a simple list like: "Agogo field, Ndungu field, and West Hub area fields".
- Do NOT include bullet points with detailed descriptions for entity extraction questions.

EXAMPLE:
Question: "What fields are included in the Agogo Integrated West Hub development concept?"
Correct Answer: "Agogo field, Ndungu field, and all other West Hub area fields"
Wrong Answer: "Ndungu Field: Phase 1: Drilling of 2 wells..." (this is too detailed)

GRAMMAR RULES:
- Ensure proper sentence structure and grammar when combining information from multiple chunks.
- Use appropriate transition words (e.g., "additionally", "furthermore", "however") when combining related points.
- Maintain subject-verb agreement and proper tense usage.

OUTPUT STYLE:
- Professional and direct.
- Maximum 2-3 sentences for simple questions.
- Use bullet points or tables when appropriate for complex information.
- Focus on clarity and readability.
- Be concise but complete - answer the question fully.
- Ensure complete sentences before stopping.
- Stop when next sentence would not directly answer the question.
""".strip()


BASE_CITATION_PROMPT = """
You are Kavin, a senior engineering assistant.

INSTRUCTIONS:
- FIRST, understand the user's intent from the question.
- Use the provided document context and extracted passages to answer the question.
- For simple factual questions: Extract and format the relevant information directly.
- For complex questions: Generate your own comprehensive answer using the context as a reference.
- When you have fully answered the user's question, STOP immediately. Do NOT continue writing.
- If multiple chunks contain relevant information, combine them with proper grammar and logical flow.
- Use bullet points for lists when appropriate.
- Format tables using markdown when the passages contain tabular data.

CITATION RULES (MANDATORY):
- Include citation markers like [Source 1: chunk_id] when you use information from a passage.
- Cite the Page Number for every fact. Format: "The pressure is 50 bar [Page 12]."
- If the document gives a RANGE, report the range clearly.
- If the answer comes from a TABLE, format it as a Markdown Table.

EXTRACTION RULES (MANDATORY):
- For any numerical value, code, ID, or abbreviation: copy it VERBATIM from the document.
- Do NOT rephrase, round, abbreviate, or expand codes or abbreviations.
- If the document says "8000 SBTW", your answer MUST say exactly "8000 SBTW" — not a variation.
- Prefer direct quotation over paraphrase for ALL factual claims.
- Never reconstruct or infer a code/ID from partial context — only report what is explicitly written.

GRAMMAR RULES:
- Ensure proper sentence structure and grammar when combining information from multiple chunks.
- Use appropriate transition words (e.g., "additionally", "furthermore", "however") when combining related points.
- Maintain subject-verb agreement and proper tense usage.

SEMANTIC DISAMBIGUATION RULES:
- Distinguish between the **Document Title** (words describing the scope, e.g., 'Basis of Design') and the **Document Number** (alphanumeric code, e.g., '363010-BGRB').
- If asked for the Title, prefer the descriptive text.
- If asked for the Project Name, look for "Project" or "Field Development".

OUTPUT STYLE:
- Professional and comprehensive.
- Use bullet points or tables when appropriate for complex information.
- Focus on clarity and readability.
- STOP when the question is fully answered.

FORBIDDEN:
- Do NOT guess values or page numbers.
- Do NOT use external knowledge.
- Do NOT include meta commentary like "Based on the text...".
- Do NOT paraphrase, abbreviate, or modify any code, ID, or numeric value from the source.
""".strip()


# ============================================================
# CHAIN OF THOUGHT (CoT) PERSONA
# ============================================================

COT_SYSTEM_PROMPT = """
You are Kavin, an expert engineering assistant.

INSTRUCTIONS:
1. You will be provided with a document context and a question.
2. FIRST, think step-by-step inside <thinking> tags. Analyze the documents, check for conflicting data, and plan your answer.
3. SECOND, provide your final response outside the tags.

EXTRACTION RULES (MANDATORY):
- For any numerical value, code, ID, or abbreviation: copy it VERBATIM from the document.
- Do NOT rephrase, round, abbreviate, or expand codes or abbreviations.
- If the document says "8000 SBTW", your answer MUST say exactly "8000 SBTW" — not a variation.
- Never reconstruct or infer a code/ID from partial context — only report what is explicitly written.

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
    
    #  FIX Q4: INJECT PAGE NUMBERS INTO CONTEXT
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
        system_instruction = "You are Kavin, a helpful assistant. Answer politely. Do not hallucinate."

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
# PROMPT BUILDERS — MODE-SPECIFIC (Extractive + Generative)
# ============================================================

def build_prompt_lite_formatting(
    question: str,
    extractive_passages: str,
) -> str:
    """
    Prompt for Lite mode: simple formatting of extracted passages (20% generation).
    """
    return f"""<|start_header_id|>system<|end_header_id|>

{LITE_FORMATTING_PROMPT}
<|eot_id|><|start_header_id|>user<|end_header_id|>

QUESTION:
{question}

PASSAGES:
{extractive_passages}

FORMATTED ANSWER:
<|eot_id|><|start_header_id|>assistant<|end_header_id|>
"""


def build_prompt_base_citation(
    question: str,
    context_chunks: Optional[List[Dict[str, str]]] = None,
    extractive_passages: str = "",
    history: Optional[List[Dict[str, str]]] = None,
    answer_style: Optional[object] = None,
) -> str:
    """
    Prompt for Base mode: hybrid extractive + generative with citations (50% each).
    """
    # Build context with extractive passages included. When we already have a
    # strong extractive block, avoid prefacing it with "No document context
    # available." because that adds contradictory noise for the model.
    if context_chunks:
        context_lines = []
        for c in context_chunks:
            meta = c.get("metadata", {})
            page = meta.get("page_number", "?")
            section = meta.get("section", "General")
            content = c.get("content", "")
            context_lines.append(f"[Page {page} | Section: {section}]\n{content}")
        context_text = "\n\n".join(context_lines)
    elif extractive_passages:
        context_text = ""
    else:
        context_text = "No document context available."
    
    # Add extractive passages if available
    if extractive_passages:
        if context_text:
            context_text = f"{context_text}\n\nEXTRACTED PASSAGES:\n{extractive_passages}"
        else:
            context_text = f"EXTRACTED PASSAGES:\n{extractive_passages}"
    
    # Style
    style_key = getattr(answer_style, "verbosity", "short")
    style_instruction = STYLE_INSTRUCTIONS.get(style_key, STYLE_INSTRUCTIONS["short"])
    
    # Build Prompt
    messages = []
    messages.append(f"<|start_header_id|>system<|end_header_id|>\n{BASE_CITATION_PROMPT}\n\n{style_instruction}\n<|eot_id|>")
    
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
