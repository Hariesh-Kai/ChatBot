# backend/rag/corrective_loop.py

import json
from typing import Any, Dict, List, Optional
from langchain_core.documents import Document
from backend.rag.evaluator_runtime import evaluate_answer

def run_corrective_retrieval(
    question: str,
    initial_chunks: List[Dict[str, Any]],
    vector_store: Any,
    metadata_filter: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Agentic RAG / Corrective Loop.
    1. Grade the retrieved chunks using the CPU intent logic (or Lite LLM).
    2. If the score is low, attempt a secondary search using extracted keywords.
    3. Return augmented or original chunks.
    """
    
    if not initial_chunks:
        return initial_chunks

    # Use internal heuristic evaluator first
    combined_context = "\n".join(c["content"] for c in initial_chunks[:3])
    
    # We will score the available context against the question. 
    # For now, let's use a quick local heuristic or LLM call.
    try:
        from backend.llm.orchestrator import _run_model_once
        from backend.llm.model_selector import resolve_model_id
        
        model_id = resolve_model_id("lite")
        prompt = f"""You are a Retrieval Grader.
Judge if the following Document Context has the exact answer to the User's Question.
Output ONLY 'YES' or 'NO'.

User Question: {question}

Document Context:
{combined_context[:2000]}

Has Answer:"""

        result = _run_model_once(
            model_id=model_id,
            prompt=prompt,
            session_id=None,
            max_tokens=8,
            role="crag_grader"
        )
        
        if "NO" in result.upper():
            print(f"[CRAG] Initial retrieval failed for '{question}'. Re-querying...")
            
            # Formulate rewrite
            rewrite_prompt = f"""The user asked '{question}', but our search failed.
Extract the 3 most crucial technical nouns or equipment tags from this question to use as a fallback keyword search.
Output ONLY keywords separated by spaces."""
            
            keywords = _run_model_once(
                model_id=model_id,
                prompt=rewrite_prompt,
                session_id=None,
                max_tokens=16,
                role="crag_rewriter"
            )
            
            keywords = keywords.strip().replace('"', '').replace("'", "")
            print(f"[CRAG] Fallback keywords: {keywords}")
            
            # Execute secondary search
            if keywords and len(keywords) > 3:
                from backend.rag.retrieve import retrieve_rag_context
                fallback_chunks = retrieve_rag_context(
                    question=keywords,
                    vector_store=vector_store,
                    company_document_id=metadata_filter.get("company_document_id", ""),
                    revision_number=metadata_filter.get("revision_number", ""),
                    rag_mode="fast",
                    force_detailed=True,
                    enable_hybrid_retrieval=True
                )
                
                if fallback_chunks:
                    print(f"[CRAG] Re-retrieved {len(fallback_chunks)} chunks.")
                    
                    # Merge sets, avoiding duplicates
                    seen_ids = {c.get("id") for c in initial_chunks}
                    for fb in fallback_chunks:
                        if fb.get("id") not in seen_ids:
                            initial_chunks.append(fb)
                            seen_ids.add(fb.get("id"))
                            
                    # Re-sort descending score
                    initial_chunks.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    except Exception as e:
        print(f"[CRAG] Corrective loop error: {e}")

    return initial_chunks[:8]  # cap to top 8 after merging
