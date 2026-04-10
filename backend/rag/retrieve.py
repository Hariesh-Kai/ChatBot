# backend/rag/retrieve.py

import json
import re as _re
from collections import Counter
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_postgres import PGVector
from sqlalchemy import text

from backend.rag.keyword_search import keyword_search
from backend.rag.rerank import rerank_documents
from backend.rag.mode_profiles import normalize_rag_mode, get_retrieval_profile

# ============================================================
# CONFIG
# ============================================================

RAG_MAX_K = 8
RAG_CANDIDATE_K = 25
MAX_CONTEXT_CHUNKS = 5

# Reciprocal Rank Fusion constant - higher k = less aggressive fusion
RRF_K = 60

ELEMENT_WEIGHTS = {
    "Title": 1.0,
    "NarrativeText": 0.9,
    "ListItem": 0.7,
    "Table": 0.6,
    "Image": 0.75,
    "UncategorizedText": 0.3,
}

SOURCE_WEIGHTS = {
    "pymupdf": 1.0,
    "table_preprocessor": 0.95,
    "docling": 0.9,
    "unstructured_hi_res": 0.8,
    "unstructured_fast": 0.6,
    "ocr": 0.5,
    "pypdf_text": 0.7,
}

CHILD_CHUNK_PENALTY = 0.5
OCR_NOISE_PENALTY = 0.6

_METADATA_LOOKUP_TERMS = (
    "company document id",
    "document id",
    "document number",
    "revision number",
    "current revision",
    "basis of design",
    "company standard",
)

_TABLE_QUERY_TERMS = (
    "table",
    "row",
    "column",
    "parameter",
    "schedule",
    "matrix",
    "datasheet",
    "specification",
    "value",
    "tag",
    "factor",
    "design pressure",
)

_IMAGE_QUERY_TERMS = (
    "image",
    "figure",
    "diagram",
    "schematic",
    "drawing",
    "plot",
    "chart",
    "graphic",
    "p&id",
    "pid",
    "pfd",
    "flow diagram",
)

_SUMMARY_QUERY_TERMS = (
    "summary",
    "summarize",
    "overview",
    "all",
    "entire",
    "full",
    "compare",
    "comparison",
)

_FACT_QUERY_TERMS = (
    "what is",
    "what are",
    "which",
    "show",
    "find",
    "give",
    "state",
    "list",
)

_EXPLANATION_QUERY_TERMS = (
    "why",
    "how",
    "explain",
)

_SECTION_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "section",
    "table",
    "general",
    "introduction",
}


# ============================================================
# METADATA / SCORING HELPERS
# ============================================================

def _normalize_chunk_type(metadata: Optional[Dict[str, Any]]) -> str:
    raw = str((metadata or {}).get("chunk_type") or (metadata or {}).get("type") or "text").strip().lower()
    if raw in {"parent", "text", "child", "image"}:
        return raw
    return "text"


def _normalize_element_type(metadata: Optional[Dict[str, Any]]) -> str:
    raw = str((metadata or {}).get("element_type") or "").strip()
    if raw in ELEMENT_WEIGHTS:
        return raw

    chunk_type = _normalize_chunk_type(metadata)
    if chunk_type in {"parent", "child"}:
        return "Table"
    if chunk_type == "image":
        return "Image"
    return "NarrativeText"


def _normalize_section_name(value: Any) -> str:
    section = str(value or "").strip()
    return section or "Unknown"


def _normalize_doc_link(metadata: Optional[Dict[str, Any]]) -> str:
    meta = metadata or {}
    return str(meta.get("doc_id") or meta.get("parent_id") or "").strip()


def _normalize_source_weight_key(metadata: Optional[Dict[str, Any]]) -> str:
    meta = metadata or {}
    raw = str(meta.get("source_weight_key") or meta.get("extraction_source") or "").strip().lower()
    if raw in SOURCE_WEIGHTS:
        return raw

    if raw in {"pymupdf4llm", "pymupdf"}:
        return "pymupdf"
    if raw in {"table_preprocessor"}:
        return "table_preprocessor"
    if raw in {"docling"}:
        return "docling"
    if raw in {"unstructured", "unstructured_hi_res"}:
        return "unstructured_hi_res"
    if raw in {"unstructured_fast"}:
        return "unstructured_fast"
    if raw in {"pypdf", "pypdf_text"}:
        return "pypdf_text"
    return "unstructured_fast"


def _query_terms(text: str) -> set[str]:
    return {
        token
        for token in _re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
        if token not in _SECTION_STOPWORDS
    }


def classify_query_profile(question: str) -> Dict[str, Any]:
    q = str(question or "").strip().lower()
    if not q:
        return {
            "name": "general",
            "allow_element_types": None,
            "exclude_element_types": set(),
            "exclude_ocr": False,
            "element_multipliers": {},
            "lane_multipliers": {"parent": 1.0, "text": 1.0, "image": 0.95, "child": 0.8},
            "top_k": MAX_CONTEXT_CHUNKS,
        }

    if any(term in q for term in _METADATA_LOOKUP_TERMS):
        return {
            "name": "metadata",
            "allow_element_types": {"Title", "NarrativeText"},
            "exclude_element_types": {"Table", "ListItem", "UncategorizedText"},
            "exclude_ocr": True,
            "element_multipliers": {
                "Title": 1.8,
                "NarrativeText": 1.2,
                "ListItem": 0.2,
                "Table": 0.05,
                "Image": 0.2,
                "UncategorizedText": 0.1,
            },
            "lane_multipliers": {"parent": 0.1, "text": 1.2, "image": 0.1, "child": 0.1},
            "top_k": MAX_CONTEXT_CHUNKS,
        }

    if any(term in q for term in _IMAGE_QUERY_TERMS):
        return {
            "name": "image_reference",
            "allow_element_types": {"Image", "NarrativeText", "Title"},
            "exclude_element_types": {"Table", "UncategorizedText"},
            "exclude_ocr": False,
            "element_multipliers": {
                "Title": 0.9,
                "NarrativeText": 1.0,
                "ListItem": 0.45,
                "Table": 0.2,
                "Image": 1.3,
                "UncategorizedText": 0.1,
            },
            "lane_multipliers": {"parent": 0.2, "text": 0.95, "image": 1.35, "child": 0.1},
            "top_k": MAX_CONTEXT_CHUNKS,
        }

    if any(term in q for term in _TABLE_QUERY_TERMS):
        return {
            "name": "table_data",
            "allow_element_types": {"Table", "NarrativeText"},
            "exclude_element_types": {"UncategorizedText"},
            "exclude_ocr": False,
            "element_multipliers": {
                "Title": 0.75,
                "NarrativeText": 1.0,
                "ListItem": 0.6,
                "Table": 1.15,
                "Image": 0.35,
                "UncategorizedText": 0.4,
            },
            "lane_multipliers": {"parent": 1.25, "text": 0.95, "image": 0.45, "child": 0.75},
            "top_k": MAX_CONTEXT_CHUNKS,
        }

    if any(term in q for term in _EXPLANATION_QUERY_TERMS):
        return {
            "name": "explanation",
            "allow_element_types": None,
            "exclude_element_types": {"UncategorizedText"},
            "exclude_ocr": False,
            "element_multipliers": {
                "Title": 0.95,
                "NarrativeText": 1.25,
                "ListItem": 0.85,
                "Table": 0.55,
                "Image": 0.8,
                "UncategorizedText": 0.2,
            },
            "lane_multipliers": {"parent": 0.7, "text": 1.2, "image": 0.9, "child": 0.4},
            "top_k": MAX_CONTEXT_CHUNKS,
        }

    if any(term in q for term in _SUMMARY_QUERY_TERMS):
        return {
            "name": "summary",
            "allow_element_types": None,
            "exclude_element_types": set(),
            "exclude_ocr": False,
            "element_multipliers": {
                "Title": 1.0,
                "NarrativeText": 1.1,
                "ListItem": 0.9,
                "Table": 0.85,
                "Image": 0.8,
                "UncategorizedText": 0.35,
            },
            "lane_multipliers": {"parent": 0.95, "text": 1.1, "image": 0.8, "child": 0.5},
            "top_k": MAX_CONTEXT_CHUNKS,
        }

    default_name = "factual" if len(_query_terms(q)) <= 6 or any(term in q for term in _FACT_QUERY_TERMS) else "general"
    return {
        "name": default_name,
        "allow_element_types": None,
        "exclude_element_types": {"UncategorizedText"} if default_name == "factual" else set(),
        "exclude_ocr": False,
        "element_multipliers": {
            "Title": 1.05,
            "NarrativeText": 1.0,
            "ListItem": 0.85,
            "Table": 0.8,
            "Image": 0.9,
            "UncategorizedText": 0.35,
        },
        "lane_multipliers": {"parent": 0.95, "text": 1.05, "image": 0.9, "child": 0.6},
        "top_k": MAX_CONTEXT_CHUNKS,
    }


def _quality_multiplier(metadata: Optional[Dict[str, Any]]) -> float:
    try:
        quality_score = float((metadata or {}).get("quality_score") or 0.5)
    except Exception:
        quality_score = 0.5
    quality_score = max(0.1, min(quality_score, 1.0))
    return round(0.55 + (0.45 * quality_score), 6)


def _section_match_multiplier(
    *,
    question: str,
    section: str,
) -> float:
    q_terms = _query_terms(question)
    s_terms = _query_terms(section)
    if not q_terms or not s_terms:
        return 1.0

    overlap = q_terms.intersection(s_terms)
    if not overlap:
        return 1.0

    return round(1.0 + min(0.08, 0.02 * len(overlap)), 6)


def _compute_scoring_multiplier(
    *,
    metadata: Optional[Dict[str, Any]],
    query_profile: Dict[str, Any],
    lane: str,
    question: str,
) -> Dict[str, float]:
    meta = metadata or {}
    element_type = _normalize_element_type(meta)
    source_key = _normalize_source_weight_key(meta)
    element_weight = float(ELEMENT_WEIGHTS.get(element_type, 0.5))
    source_weight = float(SOURCE_WEIGHTS.get(source_key, 0.6))
    quality_weight = _quality_multiplier(meta)
    child_penalty = CHILD_CHUNK_PENALTY if _normalize_chunk_type(meta) == "child" else 1.0
    ocr_penalty = OCR_NOISE_PENALTY if bool(meta.get("ocr_used", False)) else 1.0
    query_multiplier = float((query_profile.get("element_multipliers") or {}).get(element_type, 1.0))
    lane_multiplier = float((query_profile.get("lane_multipliers") or {}).get(lane, 1.0))
    section_multiplier = _section_match_multiplier(
        question=question,
        section=_normalize_section_name(meta.get("section")),
    )

    if query_profile.get("name") == "metadata" and element_type == "Title":
        section_multiplier = round(section_multiplier * 1.2, 6)

    multiplier = round(
        element_weight
        * source_weight
        * quality_weight
        * child_penalty
        * ocr_penalty
        * query_multiplier
        * lane_multiplier
        * section_multiplier,
        8,
    )

    return {
        "element_weight": round(element_weight, 4),
        "source_weight": round(source_weight, 4),
        "quality_multiplier": round(quality_weight, 4),
        "child_penalty": round(child_penalty, 4),
        "ocr_penalty": round(ocr_penalty, 4),
        "query_multiplier": round(query_multiplier, 4),
        "lane_multiplier": round(lane_multiplier, 4),
        "section_multiplier": round(section_multiplier, 4),
        "multiplier": multiplier,
    }


def _passes_query_profile(
    doc: Document,
    *,
    query_profile: Dict[str, Any],
) -> bool:
    meta = doc.metadata or {}
    element_type = _normalize_element_type(meta)

    allowed = query_profile.get("allow_element_types")
    if allowed and element_type not in allowed:
        return False

    if element_type in set(query_profile.get("exclude_element_types") or set()):
        return False

    if bool(query_profile.get("exclude_ocr", False)) and bool(meta.get("ocr_used", False)):
        return False

    return True


def _doc_score(doc: Document) -> float:
    meta = doc.metadata or {}
    for key in ("final_score", "retrieval_score", "rerank_score", "rrf_score", "vector_score"):
        value = meta.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return 0.0


def _doc_sort_key(doc: Document) -> tuple:
    chunk_type = _normalize_chunk_type(doc.metadata)
    type_order = {"parent": 4, "image": 3, "text": 2, "child": 1}.get(chunk_type, 0)
    return (_doc_score(doc), type_order, len(doc.page_content or ""))


def _ensure_chunk_metadata(
    doc: Document,
    *,
    metadata_filter: Dict[str, str],
) -> Document:
    meta = dict(doc.metadata or {})
    meta["company_document_id"] = str(
        meta.get("company_document_id") or metadata_filter.get("company_document_id") or ""
    )
    meta["revision_number"] = str(
        meta.get("revision_number") or metadata_filter.get("revision_number") or ""
    )
    meta["chunk_type"] = _normalize_chunk_type(meta)
    meta["section"] = _normalize_section_name(meta.get("section"))
    meta["element_type"] = _normalize_element_type(meta)
    meta["source_weight_key"] = _normalize_source_weight_key(meta)
    meta["extraction_source"] = str(
        meta.get("extraction_source") or meta["source_weight_key"] or "unstructured_fast"
    ).strip() or "unstructured_fast"
    if "ocr_used" not in meta:
        meta["ocr_used"] = False

    chunk_id = str(meta.get("chunk_id") or "").strip()
    if chunk_id:
        meta["chunk_id"] = chunk_id

    doc_id = _normalize_doc_link(meta)
    meta["doc_id"] = doc_id or None

    parent_id = str(meta.get("parent_id") or "").strip()
    if not parent_id and meta["chunk_type"] == "child" and doc_id:
        parent_id = doc_id
    meta["parent_id"] = parent_id or None

    if "page_number" not in meta or meta.get("page_number") in (None, ""):
        meta["page_number"] = 1
    if "quality_score" not in meta or meta.get("quality_score") in (None, ""):
        meta["quality_score"] = 0.5
    if "quality_tier" not in meta or not meta.get("quality_tier"):
        meta["quality_tier"] = "medium"

    doc.metadata = meta
    return doc


def _annotate_candidate(
    doc: Document,
    *,
    question: str,
    query_profile: Dict[str, Any],
    lane: str,
    metadata_filter: Dict[str, str],
    rank: int,
    source: str,
) -> Document:
    doc = _ensure_chunk_metadata(doc, metadata_filter=metadata_filter)
    if not _passes_query_profile(doc, query_profile=query_profile):
        return doc

    meta = doc.metadata or {}
    base_similarity = float(meta.get("rrf_score") or meta.get("vector_score") or 0.0)
    multiplier_meta = _compute_scoring_multiplier(
        metadata=meta,
        query_profile=query_profile,
        lane=lane,
        question=question,
    )
    retrieval_score = round(base_similarity * multiplier_meta["multiplier"], 8)

    meta.update(
        {
            "retrieval_lane": lane,
            "retrieval_source": source,
            "candidate_rank": rank,
            "base_similarity": round(base_similarity, 6),
            "element_weight": multiplier_meta["element_weight"],
            "source_weight": multiplier_meta["source_weight"],
            "quality_multiplier": multiplier_meta["quality_multiplier"],
            "child_penalty": multiplier_meta["child_penalty"],
            "ocr_penalty": multiplier_meta["ocr_penalty"],
            "query_multiplier": multiplier_meta["query_multiplier"],
            "lane_multiplier": multiplier_meta["lane_multiplier"],
            "section_multiplier": multiplier_meta["section_multiplier"],
            "scoring_multiplier": multiplier_meta["multiplier"],
            "retrieval_score": retrieval_score,
        }
    )
    doc.metadata = meta
    return doc


def _dedupe_by_chunk_id(docs: List[Document]) -> List[Document]:
    best_by_chunk_id: Dict[str, Document] = {}
    for doc in docs:
        chunk_id = str((doc.metadata or {}).get("chunk_id") or "").strip()
        if not chunk_id:
            continue
        existing = best_by_chunk_id.get(chunk_id)
        if existing is None or _doc_sort_key(doc) > _doc_sort_key(existing):
            best_by_chunk_id[chunk_id] = doc

    deduped = list(best_by_chunk_id.values())
    deduped.sort(key=_doc_sort_key, reverse=True)
    return deduped


def _apply_section_consensus(docs: List[Document]) -> List[Document]:
    section_counts = Counter(
        _normalize_section_name((doc.metadata or {}).get("section")).lower()
        for doc in docs
        if (doc.metadata or {}).get("section")
    )

    for doc in docs:
        section = _normalize_section_name((doc.metadata or {}).get("section")).lower()
        count = int(section_counts.get(section, 0))
        if count <= 1:
            continue

        consensus_boost = min(0.05, 0.015 * (count - 1))
        doc.metadata["section_consensus_boost"] = round(consensus_boost, 4)
        doc.metadata["retrieval_score"] = round(_doc_score(doc) * (1.0 + consensus_boost), 8)

    docs.sort(key=_doc_sort_key, reverse=True)
    return docs


def _get_retrieval_mix(
    *,
    query_profile: Dict[str, Any],
    search_k: int,
    final_k: int,
) -> Dict[str, int]:
    """
    Parent-first budgets fix the table-row domination issue without removing
    hybrid retrieval. Child rows remain optional evidence, not the primary lane.
    """
    base = max(int(search_k or 0), int(final_k or 0) + 2)
    query_name = str(query_profile.get("name") or "general")

    if query_name == "metadata":
        return {
            "parent": 0,
            "text": max(final_k + 4, int(base * 0.75)),
            "image": 0,
            "child": 0,
        }

    if query_name == "image_reference":
        return {
            "parent": 0,
            "text": max(3, int(base * 0.30)),
            "image": max(final_k + 4, int(base * 0.55)),
            "child": 0,
        }

    if query_name == "table_data":
        return {
            "parent": max(final_k + 4, int(base * 0.55)),
            "text": max(4, int(base * 0.30)),
            "image": max(1, int(base * 0.05)),
            "child": max(2, int(base * 0.15)),
        }

    if query_name == "summary":
        return {
            "parent": max(4, int(base * 0.35)),
            "text": max(final_k + 2, int(base * 0.55)),
            "image": max(1, int(base * 0.10)),
            "child": 0,
        }

    if query_name == "explanation":
        return {
            "parent": max(2, int(base * 0.15)),
            "text": max(final_k + 4, int(base * 0.65)),
            "image": max(1, int(base * 0.12)),
            "child": 0,
        }

    if query_name == "factual":
        return {
            "parent": max(3, int(base * 0.30)),
            "text": max(final_k + 2, int(base * 0.50)),
            "image": max(1, int(base * 0.08)),
            "child": max(2, int(base * 0.12)),
        }

    return {
        "parent": max(4, int(base * 0.40)),
        "text": max(final_k + 2, int(base * 0.45)),
        "image": max(1, int(base * 0.10)),
        "child": max(0, int(base * 0.08)),
    }


# ============================================================
# HYBRID SEARCH HELPERS
# ============================================================

def _reciprocal_rank_fusion(
    vector_docs: List[Document],
    keyword_results: List[tuple],  # List of (Document, score)
    *,
    rrf_k: int = RRF_K,
) -> List[Document]:
    """
    Fuses vector and keyword ranks while preserving chunk IDs for dedupe.
    """
    fused_scores: Dict[str, float] = {}
    doc_map: Dict[str, Document] = {}

    for rank, doc in enumerate(vector_docs):
        cid = str((doc.metadata or {}).get("chunk_id") or "").strip()
        if not cid:
            continue
        doc_map[cid] = doc
        fused_scores[cid] = fused_scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)

    sorted_kw = sorted(keyword_results, key=lambda item: item[1], reverse=True)
    for rank, (doc, kw_score) in enumerate(sorted_kw):
        cid = str((doc.metadata or {}).get("chunk_id") or "").strip()
        if not cid:
            continue
        if cid not in doc_map:
            doc_map[cid] = doc
        fused_scores[cid] = fused_scores.get(cid, 0.0) + (
            1.0 / (rrf_k + rank + 1) + 0.01 * float(kw_score or 0.0)
        )

    result: List[Document] = []
    for cid in sorted(fused_scores.keys(), key=lambda key: fused_scores[key], reverse=True):
        doc = doc_map[cid]
        doc.metadata["rrf_score"] = round(fused_scores[cid], 4)
        result.append(doc)
    return result


# ============================================================
# DETERMINISTIC PARENT RESOLUTION
# ============================================================

def _search_chunk_lane(
    *,
    question: str,
    vector_store: PGVector,
    metadata_filter: Dict[str, str],
    chunk_type: str,
    vector_k: int,
    keyword_k: int,
    use_keyword: bool,
    rrf_k: int,
    query_profile: Dict[str, Any],
) -> List[Document]:
    """
    Run one chunk-type lane. This is the core fix that stops child rows from
    competing directly with parent tables in the same first-pass search.
    """
    lane_filter = dict(metadata_filter)
    lane_filter["chunk_type"] = chunk_type

    vector_docs: List[Document] = []
    if vector_k > 0:
        vector_docs = vector_store.similarity_search(
            question,
            k=int(vector_k),
            filter=lane_filter,
        )

    keyword_results = []
    if use_keyword and keyword_k > 0:
        keyword_results = keyword_search(
            question=question,
            vector_store=vector_store,
            metadata_filter=lane_filter,
            limit=int(keyword_k),
        )

    if keyword_results:
        candidates = _reciprocal_rank_fusion(
            vector_docs,
            keyword_results,
            rrf_k=rrf_k,
        )
        source = "hybrid"
    else:
        candidates = list(vector_docs)
        source = "vector"
        for rank, doc in enumerate(candidates):
            doc.metadata["rrf_score"] = round(1.0 / (rrf_k + rank + 1), 4)

    prepared = [
        _annotate_candidate(
            doc,
            question=question,
            query_profile=query_profile,
            lane=chunk_type,
            metadata_filter=metadata_filter,
            rank=rank,
            source=source,
        )
        for rank, doc in enumerate(candidates)
    ]
    prepared = [
        doc
        for doc in prepared
        if _passes_query_profile(doc, query_profile=query_profile)
    ]
    prepared.sort(key=_doc_sort_key, reverse=True)
    return prepared


def _merge_candidate_streams(*streams: List[Document]) -> List[Document]:
    merged: List[Document] = []
    for docs in streams:
        merged.extend(docs)
    return _apply_section_consensus(_dedupe_by_chunk_id(merged))


def _fetch_parent_docs_by_doc_id(
    *,
    vector_store: PGVector,
    metadata_filter: Dict[str, str],
    doc_ids: List[str],
) -> Dict[str, Document]:
    unique_doc_ids = [doc_id for doc_id in dict.fromkeys(doc_ids) if doc_id]
    if not unique_doc_ids:
        return {}

    params: Dict[str, Any] = {
        "collection_name": str(getattr(vector_store, "collection_name", "") or ""),
        "company_document_id": str(metadata_filter.get("company_document_id") or ""),
        "revision_number": str(metadata_filter.get("revision_number") or ""),
    }
    placeholders: List[str] = []
    for idx, doc_id in enumerate(unique_doc_ids):
        key = f"doc_id_{idx}"
        params[key] = doc_id
        placeholders.append(f":{key}")

    try:
        engine = vector_store._engine
        sql = text(
            f"""
            SELECT e.document, e.cmetadata, e.cmetadata->>'doc_id' AS doc_id
            FROM langchain_pg_embedding AS e
            JOIN langchain_pg_collection AS c
              ON e.collection_id = c.uuid
            WHERE c.name = :collection_name
              AND e.cmetadata->>'company_document_id' = :company_document_id
              AND e.cmetadata->>'revision_number' = :revision_number
              AND COALESCE(e.cmetadata->>'chunk_type', e.cmetadata->>'type', '') = 'parent'
              AND e.cmetadata->>'doc_id' IN ({', '.join(placeholders)})
            ORDER BY LENGTH(e.document) DESC
            """
        )
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        print(f"[RETRIEVE] deterministic parent lookup failed: {exc}")
        return {}

    parent_docs: Dict[str, Document] = {}
    for row in rows or []:
        try:
            page_content = row[0]
            metadata = row[1] or {}
            doc_id = str(row[2] or metadata.get("doc_id") or "").strip()
            if not page_content or not doc_id or doc_id in parent_docs:
                continue
            parent_doc = Document(page_content=page_content, metadata=metadata)
            parent_docs[doc_id] = _ensure_chunk_metadata(
                parent_doc,
                metadata_filter=metadata_filter,
            )
        except Exception:
            continue

    return parent_docs


def resolve_parent_chunks(
    docs: List[Document],
    *,
    vector_store: PGVector,
    metadata_filter: Dict[str, str],
) -> List[Document]:
    """
    Replace child rows with their parent table deterministically.

    Child chunks remain useful evidence, but we promote the full table back
    into the candidate set before reranking so the LLM sees structure instead
    of a single isolated row.
    """
    if not docs:
        return []

    passthrough_docs: List[Document] = []
    parent_docs_by_id: Dict[str, Document] = {}
    child_support_scores: Dict[str, float] = {}
    ordered_parent_ids: List[str] = []

    for raw_doc in docs:
        doc = _ensure_chunk_metadata(raw_doc, metadata_filter=metadata_filter)
        meta = doc.metadata or {}
        chunk_type = _normalize_chunk_type(meta)
        doc_id = _normalize_doc_link(meta)
        if chunk_type in {"parent", "child"} and doc_id and doc_id not in ordered_parent_ids:
            ordered_parent_ids.append(doc_id)

        if chunk_type == "child" and doc_id:
            child_support_scores[doc_id] = max(child_support_scores.get(doc_id, 0.0), _doc_score(doc))
            continue

        if chunk_type == "parent" and doc_id:
            existing = parent_docs_by_id.get(doc_id)
            if existing is None or _doc_sort_key(doc) > _doc_sort_key(existing):
                parent_docs_by_id[doc_id] = doc
            continue

        passthrough_docs.append(doc)

    missing_parent_ids = [doc_id for doc_id in ordered_parent_ids if doc_id not in parent_docs_by_id]
    parent_docs_by_id.update(
        _fetch_parent_docs_by_doc_id(
            vector_store=vector_store,
            metadata_filter=metadata_filter,
            doc_ids=missing_parent_ids,
        )
    )

    resolved_parents: List[Document] = []
    for doc_id in ordered_parent_ids:
        parent_doc = parent_docs_by_id.get(doc_id)
        if parent_doc is None:
            continue

        support_score = child_support_scores.get(doc_id, 0.0)
        if support_score > 0.0:
            base_score = max(_doc_score(parent_doc), support_score)
            parent_doc.metadata["resolved_from_child"] = True
            parent_doc.metadata["child_support_score"] = round(support_score, 4)
            parent_doc.metadata["retrieval_score"] = round(
                base_score * 1.08,
                8,
            )

        resolved_parents.append(parent_doc)

    combined = resolved_parents + passthrough_docs
    combined = _dedupe_by_chunk_id(combined)
    combined.sort(key=_doc_sort_key, reverse=True)
    return combined


def _is_metadata_lookup(question: str) -> bool:
    q = str(question or "").strip().lower()
    return any(term in q for term in _METADATA_LOOKUP_TERMS)


def _fetch_metadata_anchor_docs(
    *,
    vector_store: PGVector,
    metadata_filter: Dict[str, str],
    limit: int = 6,
) -> List[Document]:
    try:
        engine = vector_store._engine
        sql = text(
            """
            SELECT e.document, e.cmetadata
            FROM langchain_pg_embedding AS e
            JOIN langchain_pg_collection AS c
              ON e.collection_id = c.uuid
            WHERE c.name = :collection_name
              AND e.cmetadata->>'company_document_id' = :company_document_id
              AND e.cmetadata->>'revision_number' = :revision_number
              AND (
                    e.cmetadata->>'page_number' IN ('1', '2')
                 OR COALESCE(e.cmetadata->>'document_number', '') <> ''
                 OR COALESCE(e.cmetadata->>'revision_code', '') <> ''
                 OR COALESCE(e.cmetadata->>'document_type', '') <> ''
                 OR COALESCE(e.cmetadata->>'document_title', '') <> ''
                 OR COALESCE(e.cmetadata->>'project_name', '') <> ''
                 OR COALESCE(e.cmetadata->>'document_validity', '') <> ''
                 OR e.document ILIKE '%Company Document ID%'
              )
              AND (
                    COALESCE(e.cmetadata->>'document_number', '') <> ''
                 OR COALESCE(e.cmetadata->>'revision_code', '') <> ''
                 OR COALESCE(e.cmetadata->>'document_type', '') <> ''
                 OR COALESCE(e.cmetadata->>'document_title', '') <> ''
                 OR COALESCE(e.cmetadata->>'project_name', '') <> ''
                 OR COALESCE(e.cmetadata->>'document_validity', '') <> ''
                 OR
                    e.document ILIKE '%Company Document ID%'
                 OR e.document ILIKE '%Revision%'
                 OR e.document ILIKE '%Validity%'
                 OR e.document ILIKE '%CD-FE%'
                 OR e.document ILIKE '%File Name:%'
              )
            ORDER BY
                CASE
                    WHEN COALESCE(e.cmetadata->>'document_number', '') <> ''
                      OR COALESCE(e.cmetadata->>'revision_code', '') <> ''
                      OR COALESCE(e.cmetadata->>'document_type', '') <> ''
                      OR COALESCE(e.cmetadata->>'document_title', '') <> ''
                      OR COALESCE(e.cmetadata->>'project_name', '') <> ''
                      OR COALESCE(e.cmetadata->>'document_validity', '') <> ''
                    THEN 0 ELSE 1
                END,
                CASE WHEN e.cmetadata->>'page_number' = '1' THEN 0 ELSE 1 END,
                CASE WHEN COALESCE(e.cmetadata->>'chunk_type', e.cmetadata->>'type', '') = 'text' THEN 0 ELSE 1 END,
                LENGTH(e.document) DESC
            LIMIT :limit
            """
        )
        params = {
            "collection_name": str(getattr(vector_store, "collection_name", "") or ""),
            "company_document_id": str(metadata_filter.get("company_document_id") or ""),
            "revision_number": str(metadata_filter.get("revision_number") or ""),
            "limit": int(limit),
        }
        with engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        print(f"[RETRIEVE] metadata anchor fetch failed: {exc}")
        return []

    docs: List[Document] = []
    seen: set[str] = set()
    for row in rows or []:
        try:
            page_content = row[0]
            metadata = row[1] or {}
            chunk_id = str(metadata.get("chunk_id") or "").strip()
            if not page_content or (chunk_id and chunk_id in seen):
                continue
            if chunk_id:
                seen.add(chunk_id)
            docs.append(Document(page_content=page_content, metadata=metadata))
        except Exception:
            continue
    return docs


# ============================================================
# FINAL CONTEXT CLEANING
# ============================================================

def _finalize_context_selection(
    docs: List[Document],
    *,
    final_k: int,
    query_profile: Dict[str, Any],
) -> List[Document]:
    """
    Final cleaning pass before prompt assembly.

    Rules:
    - never duplicate chunk IDs
    - avoid parent+child duplication
    - keep sections diverse unless we need a relaxed refill pass
    """
    if not docs:
        return []

    relaxed_buffer: List[Document] = []
    selected: List[Document] = []
    seen_chunk_ids: set[str] = set()
    seen_content_signatures: set[str] = set()
    selected_parent_ids: set[str] = set()
    section_counts: Dict[str, int] = {}
    query_name = str(query_profile.get("name") or "general")
    max_per_section = 2 if query_name == "summary" else 1

    for doc in docs:
        meta = doc.metadata or {}
        chunk_id = str(meta.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id in seen_chunk_ids:
            continue

        chunk_type = _normalize_chunk_type(meta)
        doc_id = _normalize_doc_link(meta)
        section = _normalize_section_name(meta.get("section"))
        content_signature = " ".join(
            _re.findall(r"[a-z0-9]+", str(doc.page_content or "").lower())
        )[:220]

        if chunk_type == "child" and doc_id and doc_id in selected_parent_ids:
            continue
        if content_signature and content_signature in seen_content_signatures:
            continue

        if section_counts.get(section, 0) >= max_per_section:
            relaxed_buffer.append(doc)
            continue

        selected.append(doc)
        seen_chunk_ids.add(chunk_id)
        if content_signature:
            seen_content_signatures.add(content_signature)
        if chunk_type == "parent" and doc_id:
            selected_parent_ids.add(doc_id)
        section_counts[section] = section_counts.get(section, 0) + 1

        if len(selected) >= final_k:
            return selected

    for doc in relaxed_buffer:
        if len(selected) >= final_k:
            break

        meta = doc.metadata or {}
        chunk_id = str(meta.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id in seen_chunk_ids:
            continue

        chunk_type = _normalize_chunk_type(meta)
        doc_id = _normalize_doc_link(meta)
        content_signature = " ".join(
            _re.findall(r"[a-z0-9]+", str(doc.page_content or "").lower())
        )[:220]
        if chunk_type == "child" and doc_id and doc_id in selected_parent_ids:
            continue
        if content_signature and content_signature in seen_content_signatures:
            continue

        selected.append(doc)
        seen_chunk_ids.add(chunk_id)
        if content_signature:
            seen_content_signatures.add(content_signature)
        if chunk_type == "parent" and doc_id:
            selected_parent_ids.add(doc_id)

    return selected[:final_k]


# ============================================================
# MAIN RETRIEVAL FUNCTION
# ============================================================

def retrieve_rag_context(
    question: str,
    vector_store: PGVector,
    company_document_id: str,
    revision_number: str,
    rag_mode: str = "balanced",
    force_detailed: bool = False,
    extra_context_ids: Optional[List[str]] = None,
    enable_hybrid_retrieval: bool = True,
) -> List[Dict[str, Any]]:
    """
    Production retrieval pipeline with parent-first table handling.

    Flow:
    1. enforce revision/document filters
    2. retrieve parent chunks first
    3. retrieve supporting text chunks
    4. optionally retrieve child chunks for refinement only
    5. merge + dedupe candidates
    6. deterministically resolve any child hit back to its parent
    7. rerank with structural priority (parent > text > child)
    8. clean and cap final context
    """
    metadata_filter = {
        "company_document_id": str(company_document_id or ""),
        "revision_number": str(revision_number or ""),
    }
    if not metadata_filter["company_document_id"] or not metadata_filter["revision_number"]:
        return []

    del extra_context_ids  # kept for API compatibility until direct chunk injection is reintroduced

    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_retrieval_profile(
        resolved_rag_mode,
        force_detailed=force_detailed,
    )

    query_profile = classify_query_profile(question)
    query_focus = str(query_profile.get("name") or "general")
    final_k = min(
        MAX_CONTEXT_CHUNKS,
        int(query_profile.get("top_k") or profile.get("final_k", RAG_MAX_K)),
    )
    search_k = (
        int(profile.get("candidate_k", RAG_CANDIDATE_K))
        if enable_hybrid_retrieval
        else final_k
    )
    retrieval_mix = _get_retrieval_mix(
        query_profile=query_profile,
        search_k=search_k,
        final_k=final_k,
    )
    keyword_limit = int(profile.get("keyword_limit", 12))
    use_keyword = bool(profile.get("use_keyword", True)) and enable_hybrid_retrieval
    rrf_k = int(profile.get("rrf_k", RRF_K))

    parent_docs = _search_chunk_lane(
        question=question,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        chunk_type="parent",
        vector_k=int(retrieval_mix.get("parent", 0)),
        keyword_k=min(keyword_limit, max(0, int(retrieval_mix.get("parent", 0)))),
        use_keyword=use_keyword,
        rrf_k=rrf_k,
        query_profile=query_profile,
    )
    text_docs = _search_chunk_lane(
        question=question,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        chunk_type="text",
        vector_k=int(retrieval_mix.get("text", 0)),
        keyword_k=min(keyword_limit, max(0, int(retrieval_mix.get("text", 0)))),
        use_keyword=use_keyword,
        rrf_k=rrf_k,
        query_profile=query_profile,
    )
    image_docs = _search_chunk_lane(
        question=question,
        vector_store=vector_store,
        metadata_filter=metadata_filter,
        chunk_type="image",
        vector_k=int(retrieval_mix.get("image", 0)),
        keyword_k=min(keyword_limit, max(0, int(retrieval_mix.get("image", 0)))),
        use_keyword=use_keyword,
        rrf_k=rrf_k,
        query_profile=query_profile,
    )

    child_docs: List[Document] = []
    child_budget = int(retrieval_mix.get("child", 0))
    if child_budget > 0:
        child_docs = _search_chunk_lane(
            question=question,
            vector_store=vector_store,
            metadata_filter=metadata_filter,
            chunk_type="child",
            vector_k=child_budget,
            keyword_k=min(max(2, keyword_limit // 2), child_budget),
            use_keyword=use_keyword,
            rrf_k=rrf_k,
            query_profile=query_profile,
        )

    candidates = _merge_candidate_streams(parent_docs, text_docs, image_docs, child_docs)

    # Compatibility fallback: older revisions may not have chunk_type populated.
    if not candidates:
        fallback_docs = vector_store.similarity_search(
            question,
            k=max(search_k, final_k),
            filter=metadata_filter,
        )
        fallback_candidates: List[Document] = []
        for rank, doc in enumerate(fallback_docs):
            doc.metadata["vector_score"] = round(1.0 / (rank + 1), 4)
            fallback_candidates.append(
                _annotate_candidate(
                    doc,
                    question=question,
                    query_profile=query_profile,
                    lane=_normalize_chunk_type(doc.metadata),
                    metadata_filter=metadata_filter,
                    rank=rank,
                    source="vector_fallback",
                )
            )
        candidates = _merge_candidate_streams(
            [
                doc
                for doc in fallback_candidates
                if _passes_query_profile(doc, query_profile=query_profile)
            ]
        )

    if _is_metadata_lookup(question):
        anchor_docs = _fetch_metadata_anchor_docs(
            vector_store=vector_store,
            metadata_filter=metadata_filter,
            limit=6,
        )
        prepared_anchor_docs: List[Document] = []
        for rank, doc in enumerate(anchor_docs):
            doc.metadata["rrf_score"] = round(0.30 - (0.02 * rank), 4)
            prepared = _annotate_candidate(
                doc,
                question=question,
                query_profile=query_profile,
                lane=_normalize_chunk_type(doc.metadata),
                metadata_filter=metadata_filter,
                rank=rank,
                source="metadata_anchor",
            )
            prepared.metadata["retrieval_score"] = max(
                float(prepared.metadata.get("retrieval_score") or 0.0),
                round((0.42 - (0.02 * rank)) * float(prepared.metadata.get("scoring_multiplier") or 1.0), 8),
            )
            if _passes_query_profile(prepared, query_profile=query_profile):
                prepared_anchor_docs.append(prepared)
        candidates = _merge_candidate_streams(prepared_anchor_docs, candidates)

    if bool(profile.get("use_parent_resolution", True)):
        candidates = resolve_parent_chunks(
            candidates,
            vector_store=vector_store,
            metadata_filter=metadata_filter,
        )

    rerank_pool = candidates[: max(final_k * 3, final_k + 6)]
    if rerank_pool and bool(profile.get("use_rerank", True)):
        reranked_docs = rerank_documents(
            question,
            rerank_pool,
            top_k=max(final_k * 2, final_k + 4),
            query_profile=query_profile,
        )
    else:
        reranked_docs = rerank_pool

    final_docs = _finalize_context_selection(
        reranked_docs,
        final_k=final_k,
        query_profile=query_profile,
    )

    rag_chunks = []
    for doc in final_docs:
        metadata = doc.metadata or {}
        chunk_id = str(metadata.get("chunk_id") or "").strip()
        if not chunk_id:
            continue

        bbox_raw = metadata.get("bbox")
        bbox_data = []
        try:
            if isinstance(bbox_raw, str) and bbox_raw.strip().startswith("["):
                bbox_data = json.loads(bbox_raw)
            elif isinstance(bbox_raw, list):
                bbox_data = bbox_raw
        except Exception:
            bbox_data = []

        rag_chunks.append(
            {
                "id": chunk_id,
                "content": doc.page_content,
                "section": metadata.get("section"),
                "chunk_type": _normalize_chunk_type(metadata),
                "score": metadata.get(
                    "final_score",
                    metadata.get(
                        "retrieval_score",
                        metadata.get(
                            "rerank_score",
                            metadata.get("rrf_score", metadata.get("vector_score", 0.0)),
                        ),
                    ),
                ),
                "metadata": {
                    "page_number": int(metadata.get("page_number", 1)),
                    "bbox": bbox_data,
                    "source_file": metadata.get("source_file", ""),
                    "section": metadata.get("section", ""),
                    "doc_id": metadata.get("doc_id"),
                    "chunk_type": _normalize_chunk_type(metadata),
                    "element_type": _normalize_element_type(metadata),
                    "extraction_source": metadata.get("extraction_source"),
                    "quality_score": metadata.get("quality_score"),
                },
            }
        )

    return rag_chunks


# ============================================================
# CONVERSATION-AWARE QUERY AUGMENTATION (Phase 4)
# ============================================================

_CONV_STOPWORDS = {
    "what", "is", "the", "are", "a", "an", "of", "in", "for", "how",
    "much", "many", "does", "can", "which", "who", "when", "where",
    "i", "me", "my", "this", "that", "it", "its", "give", "tell",
    "show", "find", "get", "please", "explain", "describe",
}

def _extract_context_keywords(messages: List[Dict]) -> List[str]:
    """Extract domain-relevant keywords from recent conversation turns."""
    keywords: list = []
    for msg in messages:
        content = msg.get("content", "")
        tokens = _re.findall(r"[a-zA-Z0-9\-\.]{3,}", content)
        for t in tokens:
            if t.lower() not in _CONV_STOPWORDS and t not in keywords:
                keywords.append(t)
    return keywords[:12]  # max 12 context keywords


def augment_query_with_context(
    question: str,
    recent_messages: List[Dict],
) -> str:
    """
    Augment a vague follow-up question with conversation context keywords.

    Examples:
        Q: "what about its material?"
        Context keywords: ["pressure", "valve", "DN200", "P-101A"]
        Result: "what about its material? [context: pressure valve DN200 P-101A]"

    Only augments if the question is short / ambiguous (< 8 tokens).
    Always returns at least the original question.
    Never raises.
    """
    try:
        if not recent_messages:
            return question

        q_tokens = question.strip().split()
        if len(q_tokens) >= 8:
            # Question is specific enough - no augmentation needed
            return question

        ctx_keywords = _extract_context_keywords(recent_messages)
        if not ctx_keywords:
            return question

        augmented = f"{question} [context: {' '.join(ctx_keywords[:6])}]"
        print(f"[CONV-AWARE] Augmented short query: {augmented[:100]}")
        return augmented

    except Exception as e:
        print(f"[CONV-AWARE] augment_query_with_context failed (non-fatal): {e}")
        return question
