# backend/rag/extractive_rag.py

"""
Extractive RAG Module
Extracts relevant passages from retrieved chunks for hybrid RAG approach.
"""

from typing import List, Dict, Any, Optional
import re


def extract_relevant_passages(
    chunks: List[Dict[str, Any]],
    question: str,
    top_k: int = 5,
    min_length: int = 50,
) -> List[Dict[str, Any]]:
    """
    Extract and rank relevant passages from retrieved chunks.
    
    Args:
        chunks: Retrieved RAG chunks
        question: User question for relevance scoring
        top_k: Number of top passages to return
        min_length: Minimum passage length to include
    
    Returns:
        List of extracted passages with metadata
    """
    if not chunks:
        return []
    
    # Score each chunk based on relevance to question
    scored_chunks = []
    for chunk in chunks:
        score = _calculate_relevance_score(chunk, question)
        
        # Filter by minimum length
        content = str(chunk.get("content", ""))
        if len(content) < min_length:
            continue
        
        scored_chunks.append({
            "chunk": chunk,
            "score": score,
            "content": content,
            "metadata": chunk.get("metadata", {}),
            "chunk_id": chunk.get("id", ""),
        })
    
    # Sort by relevance score (descending)
    scored_chunks.sort(key=lambda x: x["score"], reverse=True)
    
    # Return top_k passages
    top_passages = scored_chunks[:top_k]
    
    # Format passages for output
    passages = []
    for item in top_passages:
        passages.append({
            "content": item["content"],
            "chunk_id": item["chunk_id"],
            "score": item["score"],
            "metadata": {
                "page_number": item["metadata"].get("page_number", 1),
                "section": item["metadata"].get("section", ""),
                "source_file": item["metadata"].get("source_file", ""),
            }
        })
    
    return passages


def _calculate_relevance_score(chunk: Dict[str, Any], question: str) -> float:
    """
    Calculate relevance score of a chunk to the question.
    
    Combines:
    - Vector similarity score (from retrieval)
    - Keyword overlap with question
    - Question term frequency in chunk
    - Entity extraction bonus for "what fields", "which companies", etc.
    """
    # Base score from retrieval
    base_score = float(chunk.get("score", 0.0))
    
    # Keyword overlap score
    content = str(chunk.get("content", "")).lower()
    question_lower = question.lower()
    
    # Extract question terms (remove stop words)
    question_terms = set(re.findall(r'\b\w+\b', question_lower))
    stop_words = {"where", "what", "how", "when", "why", "is", "are", "the", "a", "an", "in", "at", "on", "to", "for", "of", "with"}
    question_terms = question_terms - stop_words
    
    # Calculate term overlap
    overlap_score = 0.0
    if question_terms:
        overlap_count = sum(1 for term in question_terms if term in content)
        overlap_score = overlap_count / len(question_terms)
    
    # Entity extraction bonus for "what fields", "which companies", etc.
    entity_bonus = 0.0
    if any(phrase in question_lower for phrase in ["what fields", "which fields", "what companies", "which companies", "what items", "which items"]):
        # For entity extraction questions, prioritize chunks with concise lists or entity names
        # Bonus for chunks that contain field/company/item names (typically capitalized words or specific patterns)
        import string
        words = content.split()
        capitalized_words = sum(1 for w in words if w and w[0].isupper() and w not in ["The", "A", "An"])
        # Bonus for chunks with multiple capitalized words (likely entity names)
        if capitalized_words >= 2:
            entity_bonus = 0.2
        # Additional bonus for chunks that are concise (more likely to be entity lists)
        if len(content) < 300:
            entity_bonus += 0.1

    table_bonus = 0.0
    chunk_meta = chunk.get("metadata", {}) or {}
    chunk_type = str(
        chunk_meta.get("chunk_type")
        or chunk.get("chunk_type")
        or chunk_meta.get("type")
        or ""
    ).strip().lower()
    if any(
        phrase in question_lower
        for phrase in (
            "table",
            "row",
            "column",
            "revision list",
            "revision history",
            "schedule",
            "matrix",
            "datasheet",
        )
    ):
        if chunk_type == "parent":
            table_bonus += 0.25
        elif chunk_type == "child":
            table_bonus += 0.35
        if "row index:" in content or "column path" in content:
            table_bonus += 0.15
        if "rows:" in content and "context:" in content:
            table_bonus += 0.1

    revision_bonus = 0.0
    if any(phrase in question_lower for phrase in ["revision list", "revision history", "technical change", "change made"]):
        if "revision list" in content or "revision history" in content:
            revision_bonus += 0.35
        revision_match = re.search(r"\brevision\s+(\d+)\b", question_lower)
        if revision_match and revision_match.group(1) in content:
            revision_bonus += 0.2
        if "injection water" in question_lower and "injection water" in content:
            revision_bonus += 0.2
        if "design temperature" in content:
            revision_bonus += 0.1
    
    # Combined score (60% retrieval, 30% keyword overlap, bonuses for query-specific matches)
    combined_score = (0.6 * base_score) + (0.3 * overlap_score) + entity_bonus + table_bonus + revision_bonus

    return combined_score


def highlight_key_terms(
    passage: str,
    question: str,
    max_highlights: int = 5,
) -> str:
    """
    Highlight key terms from the question in the passage.
    
    Args:
        passage: Passage text
        question: User question
        max_highlights: Maximum number of terms to highlight
    
    Returns:
        Passage with highlighted terms (using **term** format)
    """
    question_lower = question.lower()
    question_terms = set(re.findall(r'\b\w+\b', question_lower))
    
    # Remove stop words
    stop_words = {"where", "what", "how", "when", "why", "is", "are", "the", "a", "an", "in", "at", "on", "to", "for", "of", "with"}
    question_terms = question_terms - stop_words
    
    # Sort terms by length (longer terms first)
    sorted_terms = sorted(question_terms, key=len, reverse=True)[:max_highlights]
    
    # Highlight terms in passage
    highlighted = passage
    for term in sorted_terms:
        # Case-insensitive replacement
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        highlighted = pattern.sub(f"**{term}**", highlighted)
    
    return highlighted


def format_passages_for_display(
    passages: List[Dict[str, Any]],
    include_highlights: bool = False,
    question: str = "",
) -> str:
    """
    Format extracted passages for display to user.
    
    Args:
        passages: Extracted passages
        include_highlights: Whether to highlight key terms
        question: Question for highlighting (if enabled)
    
    Returns:
        Formatted string of passages
    """
    if not passages:
        return "No relevant passages found."
    
    formatted_lines = []
    for idx, passage in enumerate(passages, 1):
        content = passage["content"]
        
        if include_highlights and question:
            content = highlight_key_terms(content, question)
        
        metadata = passage.get("metadata", {})
        page = metadata.get("page_number", "?")
        section = metadata.get("section", "")
        
        formatted_lines.append(f"**Passage {idx}** [Page {page}")
        if section:
            formatted_lines.append(f"Section: {section}")
        formatted_lines.append(f"]")
        formatted_lines.append(content)
        formatted_lines.append("")  # Empty line between passages
    
    return "\n".join(formatted_lines)


def merge_duplicate_passages(
    passages: List[Dict[str, Any]],
    similarity_threshold: float = 0.9,
) -> List[Dict[str, Any]]:
    """
    Merge duplicate or highly similar passages.
    
    Args:
        passages: Extracted passages
        similarity_threshold: Threshold for considering passages as duplicates
    
    Returns:
        Deduplicated passages
    """
    if len(passages) <= 1:
        return passages
    
    deduplicated = []
    seen_contents = set()
    
    for passage in passages:
        content = passage["content"]
        
        # Simple deduplication by content hash
        content_hash = hash(content.lower())
        if content_hash in seen_contents:
            continue
        
        seen_contents.add(content_hash)
        deduplicated.append(passage)
    
    return deduplicated
