# backend/rag/metadata.py

import json
import sys
import hashlib
import tiktoken
import logging
import time
import re  #  Added for Regex patterns
from datetime import datetime
from typing import Dict, Any, List, Optional, Sequence, Tuple

from backend.rag.filtering import element_category, element_page

# ============================================================
# TOKENIZER (STATS ONLY — NO MODEL USE)
# ============================================================

_tokenizer = None
_tokenizer_error = None
_tokenizer_warned = False


def _get_tokenizer():
    global _tokenizer, _tokenizer_error
    if _tokenizer is not None:
        return _tokenizer
    if _tokenizer_error is not None:
        return None
    try:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
        return _tokenizer
    except Exception as exc:  # Offline / proxy-safe fallback
        _tokenizer_error = exc
        return None


def count_tokens(text: str) -> int:
    global _tokenizer_warned
    tokenizer = _get_tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text))

    # Fallback: approximate tokens to avoid hard failure in offline environments.
    if not _tokenizer_warned:
        _tokenizer_warned = True
        logging.getLogger("chatui.rag.metadata").warning(
            "tiktoken unavailable; using approximate token counts."
        )
    if not text:
        return 0
    # Rough heuristic: ~4 chars per token
    return max(1, int(len(text) / 4))


# ============================================================
# CHUNK ID (DETERMINISTIC, REVISION-SAFE)
# ============================================================

CHUNK_ID_VERSION = 2


def generate_chunk_id(
    company_document_id: str,
    revision_number: str,
    content: str,
    *,
    page_number: Any = None,
    chunk_type: str = "",
    section: str = "",
    parent_id: Any = None,
    doc_id: Any = None,
    table_row_index: Any = None,
    chunk_index: Any = None,
    occurrence_index: int = 0,
) -> str:
    """
    Deterministic, document-scoped chunk ID.

    Guarantees:
    - Stable across re-ingestion
    - No collision across documents or revisions
    """
    base = "::".join(
        [
            str(company_document_id or "").strip(),
            str(revision_number or "").strip(),
            str(page_number or "").strip(),
            str(chunk_type or "").strip(),
            str(section or "").strip(),
            str(parent_id or "").strip(),
            str(doc_id or "").strip(),
            str(table_row_index if table_row_index is not None else "").strip(),
            str(chunk_index if chunk_index is not None else "").strip(),
            str(int(occurrence_index or 0)),
            str(content or ""),
        ]
    )
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def _default_extraction_source(
    *,
    extra_metadata: Dict[str, Any],
) -> str:
    preprocessor = str(extra_metadata.get("rag_preprocessor") or "unstructured").strip().lower()
    rag_mode = str(extra_metadata.get("rag_ingest_mode") or "balanced").strip().lower()

    if preprocessor == "pymupdf4llm":
        return "pymupdf"
    if preprocessor == "table_preprocessor":
        return "table_preprocessor"
    if preprocessor == "docling":
        return "docling"
    if preprocessor == "pypdf_text":
        return "pypdf_text"
    if rag_mode == "fast":
        return "unstructured_fast"
    return "unstructured_hi_res"


def _score_chunk_quality(content: str) -> Dict[str, Any]:
    """
    Lightweight ingest-time quality signal used later by retrieval.
    """
    default = {"quality_score": 0.5, "quality_tier": "medium"}
    if not content:
        return default

    try:
        length = len(content)
        if length < 50:
            length_score = 0.1
        elif length < 200:
            length_score = 0.5
        elif length <= 1500:
            length_score = 1.0
        elif length <= 3000:
            length_score = 0.7
        else:
            length_score = 0.4

        alpha_chars = sum(1 for ch in content if ch.isalpha())
        density_score = min(alpha_chars / max(length, 1), 1.0)

        words = re.findall(r"[a-zA-Z]{4,}", content.lower())
        distinct_words = len(set(words))
        richness_score = min(distinct_words / 50.0, 1.0)

        overall = round(
            0.3 * length_score + 0.3 * density_score + 0.4 * richness_score,
            3,
        )
        if overall >= 0.70:
            tier = "high"
        elif overall >= 0.40:
            tier = "medium"
        else:
            tier = "low"

        return {"quality_score": overall, "quality_tier": tier}
    except Exception:
        return default


# ============================================================
# METADATA EXTRACTION (PHASE 1 — SMART HEURISTICS)
# ============================================================

_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_MULTISPACE_RE = re.compile(r"\s+")
_TITLE_HINT_RE = re.compile(r"\b(?:basis of design|design basis)\b", re.IGNORECASE)
_LABEL_ONLY_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "document_number",
        re.compile(
            r"^\s*(?:company\s+document\s+id|document\s+(?:id|number)|doc(?:ument)?\s+(?:id|number)|file\s+no\.?|drawing\s+number)\b\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "revision_code",
        re.compile(r"^\s*(?:revision|rev(?:ision)?\.?)\b\s*$", re.IGNORECASE),
    ),
    (
        "project_name",
        re.compile(r"^\s*(?:project(?:\s+name)?|development|field)\b\s*$", re.IGNORECASE),
    ),
    (
        "document_title",
        re.compile(r"^\s*(?:document\s+title|title)\b\s*$", re.IGNORECASE),
    ),
)
_INLINE_FIELD_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "document_number",
        re.compile(
            r"^\s*(?:company\s+document\s+id|document\s+(?:id|number)|doc(?:ument)?\s+(?:id|number)|file\s+no\.?|drawing\s+number)\b\s*(?:[:\-]\s*)?(.+?)\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "revision_code",
        re.compile(r"^\s*(?:revision|rev(?:ision)?\.?)\b\s*(?:[:\-]\s*)?([A-Za-z0-9._/-]+)\s*$", re.IGNORECASE),
    ),
    (
        "project_name",
        re.compile(r"^\s*(?:project(?:\s+name)?|development|field)\b\s*(?:[:\-]\s*)?(.+?)\s*$", re.IGNORECASE),
    ),
    (
        "document_title",
        re.compile(r"^\s*(?:document\s+title|title)\b\s*(?:[:\-]\s*)?(.+?)\s*$", re.IGNORECASE),
    ),
)
_STANDALONE_DOCUMENT_NUMBER_RE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9/_\\-]{7,}$")
_HEADER_ZONE_MAX_RATIO = 0.24
_FOOTER_ZONE_MIN_RATIO = 0.68


def _normalized_metadata_text(value: Any) -> str:
    text = _BREAK_RE.sub("\n", str(value or ""))
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    return text


def _compact_text(value: Any) -> str:
    return _MULTISPACE_RE.sub(" ", str(value or "")).strip()


def _candidate_lines(text: Any) -> List[str]:
    lines: List[str] = []
    for raw_line in _normalized_metadata_text(text).split("\n"):
        normalized_line = _compact_text(raw_line.strip(" |"))
        if not normalized_line:
            continue
        if "|" in normalized_line:
            cells = [_compact_text(cell) for cell in normalized_line.split("|") if _compact_text(cell)]
            if len(cells) >= 2:
                lines.extend(cells)
                continue
        lines.append(normalized_line)
    return lines


def _extract_points(metadata: Dict[str, Any]) -> List[Tuple[float, float]]:
    candidates: List[Any] = []
    coordinates = metadata.get("coordinates")
    if isinstance(coordinates, dict):
        candidates.append(coordinates.get("points"))
    bbox = metadata.get("bbox")
    if isinstance(bbox, dict):
        candidates.append(bbox.get("points"))
        candidates.append(bbox.get("bbox"))
    elif isinstance(bbox, list):
        candidates.append(bbox)

    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        parsed: List[Tuple[float, float]] = []
        for point in candidate:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                parsed.append((float(point[0]), float(point[1])))
            except Exception:
                continue
        if parsed:
            return parsed
    return []


def _layout_height(metadata: Dict[str, Any], points: Sequence[Tuple[float, float]]) -> Optional[float]:
    candidates = [metadata.get("layout_height")]
    coordinates = metadata.get("coordinates")
    if isinstance(coordinates, dict):
        candidates.append(coordinates.get("layout_height"))
    bbox = metadata.get("bbox")
    if isinstance(bbox, dict):
        candidates.append(bbox.get("layout_height"))

    for candidate in candidates:
        try:
            value = float(candidate)
            if value > 0:
                return value
        except Exception:
            continue

    if points:
        max_y = max(point[1] for point in points)
        if max_y > 0:
            return max_y
    return None


def _vertical_ratio(element: Dict[str, Any]) -> Optional[float]:
    metadata = element.get("metadata")
    if not isinstance(metadata, dict):
        return None
    points = _extract_points(metadata)
    if not points:
        return None
    layout_height = _layout_height(metadata, points)
    if not layout_height:
        return None
    avg_y = sum(point[1] for point in points) / len(points)
    ratio = avg_y / layout_height
    if 0 <= ratio <= 1.5:
        return ratio
    return None


def _title_block_zone(element: Dict[str, Any]) -> bool:
    ratio = _vertical_ratio(element)
    if ratio is None:
        return False
    return ratio <= _HEADER_ZONE_MAX_RATIO or ratio >= _FOOTER_ZONE_MIN_RATIO


def _field_label_key(line: str) -> Optional[str]:
    normalized = _compact_text(line)
    if not normalized:
        return None
    for key, pattern in _LABEL_ONLY_PATTERNS:
        if pattern.match(normalized):
            return key
    return None


def _contains_explicit_field_label(lines: Sequence[str]) -> bool:
    for line in lines:
        if _field_label_key(line):
            return True
        for _, pattern in _INLINE_FIELD_PATTERNS:
            if pattern.match(line):
                return True
    return False


def _apply_metadata_value(metadata: Dict[str, Dict[str, Any]], key: str, value: Any, confidence: float) -> None:
    normalized = _compact_text(value)
    if not normalized:
        return
    if float(metadata.get(key, {}).get("confidence") or 0.0) >= confidence:
        return
    metadata[key] = {"value": normalized, "confidence": confidence}


def _extract_explicit_fields(lines: Sequence[str]) -> Dict[str, str]:
    extracted: Dict[str, str] = {}

    for index, line in enumerate(lines):
        for key, pattern in _INLINE_FIELD_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            value = _compact_text(match.group(1))
            if value:
                extracted.setdefault(key, value)

        label_key = _field_label_key(line)
        if not label_key:
            continue

        next_index = index + 1
        while next_index < len(lines):
            candidate = _compact_text(lines[next_index])
            if not candidate:
                next_index += 1
                continue
            if _field_label_key(candidate):
                break
            extracted.setdefault(label_key, candidate)
            break

    return extracted


def _candidate_title_text(element: Dict[str, Any], lines: Sequence[str]) -> Optional[str]:
    category = element_category(element)
    if category == "Title":
        title_text = _compact_text(" ".join(lines))
        return title_text or None

    joined = _compact_text(" ".join(lines))
    if _TITLE_HINT_RE.search(joined):
        return joined
    return None


def _extract_title_block_metadata_from_page(elements: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    metadata = {
        "document_type": {"value": None, "confidence": 0.0},
        "document_title": {"value": None, "confidence": 0.0},
        "revision_code": {"value": None, "confidence": 0.0},
        "project_name": {"value": None, "confidence": 0.0},
        "document_number": {"value": None, "confidence": 0.0},
    }

    for element in elements:
        if element_page(element) != 1:
            continue

        raw_text = element.get("text") or element.get("content") or ""
        lines = _candidate_lines(raw_text)
        if not lines:
            continue

        candidate_title = _candidate_title_text(element, lines)
        if candidate_title:
            confidence = 0.9 if element_category(element) == "Title" else 0.75
            _apply_metadata_value(metadata, "document_title", candidate_title, confidence)
            _apply_metadata_value(metadata, "document_type", candidate_title, confidence)

        title_block_candidate = _title_block_zone(element) or (
            element_category(element) == "Table" and _contains_explicit_field_label(lines)
        )
        if not title_block_candidate:
            continue

        explicit_fields = _extract_explicit_fields(lines)
        for key, value in explicit_fields.items():
            confidence = 0.95 if key in {"document_number", "revision_code"} else 0.9
            _apply_metadata_value(metadata, key, value, confidence)
            if key == "document_title":
                _apply_metadata_value(metadata, "document_type", value, confidence)

        if metadata["document_number"]["value"]:
            continue

        for line in lines:
            if _field_label_key(line):
                continue
            if _STANDALONE_DOCUMENT_NUMBER_RE.match(line) and any(ch.isdigit() for ch in line):
                _apply_metadata_value(metadata, "document_number", line, 0.82)
                break

    return metadata

def extract_document_metadata(
    *,
    elements_file: str,
    pdf_path: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    with open(elements_file, "r", encoding="utf-8") as f:
        elements: List[Dict[str, Any]] = json.load(f)

    metadata = _extract_title_block_metadata_from_page(
        [item for item in elements if isinstance(item, dict)]
    )

    # --------------------------------------------------------
    # AUTHORITATIVE OVERRIDES (NON-IDENTITY ONLY)
    # --------------------------------------------------------

    if "revision_code" in extra_metadata:
        metadata["revision_code"] = {
            "value": extra_metadata["revision_code"],
            "confidence": 1.0,
        }

    # Map generic doc_type to title if title wasn't found automatically
    if "document_type" in extra_metadata:
        if not metadata["document_title"]["value"]:
            metadata["document_title"] = {
                "value": extra_metadata["document_type"],
                "confidence": 1.0,
            }
        metadata["document_type"] = {
            "value": extra_metadata["document_type"],
            "confidence": 1.0,
        }

    return metadata


# ============================================================
# CHUNK ENRICHMENT (PHASE 2 — AUTHORITATIVE)
# ============================================================

def enrich_chunks(
    *,
    chunks_file: str,
    output_file: str,
    pdf_path: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enrich chunk JSON with REQUIRED RAG metadata.
    """

    print(f"[METADATA] Enriching chunks from: {chunks_file}")

    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    #  FIX: Treat revision as String (do not cast to int)
    revision_number = str(extra_metadata.get("revision_number", ""))
    revision_code = extra_metadata.get("revision_code")
    revision_date = extra_metadata.get("revision_date", int(time.time()))
    document_type = extra_metadata.get("document_type")
    document_title = extra_metadata.get("document_title") or document_type
    document_number = extra_metadata.get("document_number")
    project_name = extra_metadata.get("project_name")
    source_file = extra_metadata.get("source_file")

    if not revision_number:
        raise RuntimeError("extra_metadata.revision_number is required")

    if not source_file:
        raise RuntimeError("extra_metadata.source_file is required")

    created_at = int(time.time())
    enriched: List[Dict[str, Any]] = []
    seen_chunk_seeds: Dict[str, int] = {}

    for item in chunks:
        content = item.get("content")
        base_meta = item.get("metadata", {})

        if not content:
            continue

        chunk_type = str(base_meta.get("type") or "text").strip().lower() or "text"
        section = str(base_meta.get("section") or "Unknown").strip() or "Unknown"
        parent_id = str(base_meta.get("parent_id") or "").strip() or None
        doc_id = str(base_meta.get("doc_id") or parent_id or "").strip() or None
        element_type = str(
            base_meta.get("element_type")
            or (
                "Table"
                if chunk_type in {"parent", "child"}
                else "Image" if chunk_type == "image" else "NarrativeText"
            )
        ).strip() or "NarrativeText"
        extraction_source = str(
            base_meta.get("extraction_source")
            or base_meta.get("source_weight_key")
            or _default_extraction_source(extra_metadata=extra_metadata)
        ).strip() or "unstructured_fast"
        source_weight_key = str(
            base_meta.get("source_weight_key")
            or extraction_source
        ).strip() or extraction_source
        quality_meta = _score_chunk_quality(content)
        ocr_used = bool(base_meta.get("ocr_used", False))
        extraction_backend = str(base_meta.get("extraction_backend") or extra_metadata.get("rag_preprocessor") or "").strip()
        page_number = base_meta.get("page_number", 1)
        bbox = base_meta.get("bbox", "")
        table_row_index = base_meta.get("table_row_index")
        chunk_index = base_meta.get("chunk_index")

        chunk_seed = "::".join(
            [
                str(company_document_id or "").strip(),
                str(revision_number or "").strip(),
                str(page_number or "").strip(),
                str(chunk_type or "").strip(),
                str(section or "").strip(),
                str(parent_id or "").strip(),
                str(doc_id or "").strip(),
                str(table_row_index if table_row_index is not None else "").strip(),
                str(chunk_index if chunk_index is not None else "").strip(),
                str(content or ""),
            ]
        )
        occurrence_index = int(seen_chunk_seeds.get(chunk_seed, 0))
        seen_chunk_seeds[chunk_seed] = occurrence_index + 1

        enriched.append(
            {
                "page_content": content,

                # -----------------------------
                # NON-IDENTITY METADATA
                # -----------------------------
                "metadata": {
                    "section": section,
                    "chunk_type": chunk_type,
                    "element_type": element_type,
                    "source_file": source_file,
                    "tokens": count_tokens(content),
                    "created_at": created_at,
                    "extraction_backend": extraction_backend,
                    "extraction_source": extraction_source,
                    "source_weight_key": source_weight_key,
                    "ocr_used": ocr_used,
                    "quality_score": quality_meta["quality_score"],
                    "quality_tier": quality_meta["quality_tier"],
                    
                    #  CRITICAL: Pass Page & BBox to DB for Frontend Highlighting
                    "page_number": page_number,
                    "bbox": bbox,

                    #  Table linkage (required for parent resolution)
                    "parent_id": parent_id,
                    "doc_id": doc_id,
                    "table_row_index": table_row_index,
                    "chunk_index": chunk_index,
                    "chunk_id_version": CHUNK_ID_VERSION,
                },

                # -----------------------------
                # 🔒 RAG IDENTITY (FILTER KEYS)
                # -----------------------------
                "cmetadata": {
                    "company_document_id": company_document_id,
                    "revision_number": revision_number, 
                    "revision_code": revision_code,
                    "revision_date": revision_date,
                    "document_type": document_type,
                    "document_title": document_title,
                    "document_number": document_number,
                    "project_name": project_name,
                },

                # -----------------------------
                # INTERNAL (OPTIONAL)
                # -----------------------------
                "chunk_id": generate_chunk_id(
                    company_document_id,
                    revision_number,
                    content,
                    page_number=page_number,
                    chunk_type=chunk_type,
                    section=section,
                    parent_id=parent_id,
                    doc_id=doc_id,
                    table_row_index=table_row_index,
                    chunk_index=chunk_index,
                    occurrence_index=occurrence_index,
                ),
                "chunk_id_version": CHUNK_ID_VERSION,
                "parent_id": parent_id, # None if parent
                "doc_id": doc_id,
            }
        )

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f" Enriched {len(enriched)} chunks.")
    print(f"[METADATA] Saved to: {output_file}")

    return {
        "company_document_id": company_document_id,
        "revision_number": revision_number,
        "revision_date": revision_date,
        "chunk_count": len(enriched),
        "source_file": source_file,
    }


# ============================================================
# CLI (DEBUG / MANUAL USE ONLY)
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) != 8:
        print("Usage: python metadata.py <chunks.json> <output.json> <pdf_path> <doc_id> <rev_num> <rev_date> <filename>")
        sys.exit(1)

    enrich_chunks(
        chunks_file=sys.argv[1],
        output_file=sys.argv[2],
        pdf_path=sys.argv[3],
        company_document_id=sys.argv[4],
        extra_metadata={
            "revision_number": sys.argv[5],
            "revision_date": sys.argv[6],
            "source_file": sys.argv[7],
        },
    )
    print("Chunk enrichment completed.")
