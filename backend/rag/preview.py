from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pypdf import PdfReader, PdfWriter

from backend.rag.chunk import ContextAwareChunker
from backend.rag.chunk_strategy import get_chunk_config
from backend.rag.filtering import (
    FILTER_VERSION,
    element_category,
    element_page,
    element_text,
    filter_element_dicts,
)
from backend.rag.metadata import extract_document_metadata, enrich_chunks
from backend.rag.mode_profiles import DEFAULT_RAG_MODE, normalize_rag_mode
from backend.rag.preprocess import stream_pdf_to_elements
from backend.rag.preprocessor_registry import DEFAULT_RAG_PREPROCESSOR, normalize_rag_preprocessor
from backend.state.dev_settings import get_dev_settings


PreviewScope = Literal["auto", "quick", "full"]
ResolvedPreviewScope = Literal["quick", "full"]

LARGE_PDF_PAGE_THRESHOLD = 50
LARGE_PDF_SIZE_MB_THRESHOLD = 25.0
QUICK_PREVIEW_PAGE_LIMIT = 10
PAGE_PREVIEW_DIRNAME = "page_previews"


PREVIEW_FILES = {
    "page1_preview": "page1_preview.json",
    "raw_elements": "raw_elements.json",
    "filtered_elements": "filtered_elements.json",
    "removed_elements": "removed_elements.json",
    "filter_report": "filter_report.json",
    "chunks": "chunks.json",
    "enriched_chunks": "enriched_chunks.json",
}

QUICK_PREVIEW_FILES = {
    "raw_elements": "quick_raw_elements.json",
    "filtered_elements": "quick_filtered_elements.json",
    "removed_elements": "quick_removed_elements.json",
    "filter_report": "quick_filter_report.json",
    "chunks": "quick_chunks.json",
    "enriched_chunks": "quick_enriched_chunks.json",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _load_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _filter_report_version(path: Path) -> int:
    payload = _load_json(path, {})
    if not isinstance(payload, dict):
        return 0

    try:
        return int(payload.get("filter_version") or 0)
    except Exception:
        return 0


def _artifact_paths(job_dir: Path, *, quick: bool = False) -> Dict[str, Path]:
    base = QUICK_PREVIEW_FILES if quick else PREVIEW_FILES
    paths = {name: job_dir / filename for name, filename in base.items()}
    paths["page1_preview"] = job_dir / PREVIEW_FILES["page1_preview"]
    return paths


def _resolve_settings(
    *,
    extra_metadata: Dict[str, Any],
    rag_mode: Optional[str],
    preprocessor: Optional[str],
) -> Dict[str, Any]:
    try:
        settings = get_dev_settings()
    except Exception:
        settings = {}

    fast_document_processing = bool(
        extra_metadata.get("enable_fast_document_processing")
        if extra_metadata.get("enable_fast_document_processing") is not None
        else settings.get("enable_fast_document_processing", False)
    )

    return {
        "rag_mode": normalize_rag_mode(
            rag_mode
            or extra_metadata.get("rag_ingest_mode")
            or extra_metadata.get("rag_mode")
            or (DEFAULT_RAG_MODE if fast_document_processing else "high_fidelity")
        ),
        "preprocessor": normalize_rag_preprocessor(
            preprocessor
            or extra_metadata.get("rag_preprocessor")
            or (DEFAULT_RAG_PREPROCESSOR if fast_document_processing else "unstructured")
        ),
        "fast_document_processing": fast_document_processing,
    }


def _document_stats(pdf_path: str) -> Dict[str, Any]:
    try:
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
    except Exception as exc:
        raise RuntimeError(f"Failed to inspect PDF for preview: {exc}") from exc

    size_mb = round((Path(pdf_path).stat().st_size / (1024 * 1024)), 2)
    is_large = page_count > LARGE_PDF_PAGE_THRESHOLD or size_mb > LARGE_PDF_SIZE_MB_THRESHOLD
    return {
        "page_count": page_count,
        "file_size_mb": size_mb,
        "is_large": is_large,
        "large_page_threshold": LARGE_PDF_PAGE_THRESHOLD,
        "large_size_mb_threshold": LARGE_PDF_SIZE_MB_THRESHOLD,
        "quick_page_limit": QUICK_PREVIEW_PAGE_LIMIT,
    }


def _resolve_scope(
    scope: PreviewScope,
    stats: Dict[str, Any],
    *,
    fast_document_processing: bool,
) -> ResolvedPreviewScope:
    if scope == "quick":
        return "quick"
    if scope == "full":
        return "full"
    if not fast_document_processing:
        return "full"
    return "quick" if bool(stats.get("is_large")) else "full"


def _stream_elements(
    *,
    pdf_path: str,
    output_json: str,
    rag_mode: str,
    pipeline_mode: str,
    preprocessor: str,
    stop_after_first_nonempty_batch: bool,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    all_elements: List[Dict[str, Any]] = []
    for batch in stream_pdf_to_elements(
        pdf_path,
        output_json,
        rag_mode=rag_mode,
        pipeline_mode=pipeline_mode,
        preprocessor=preprocessor,
    ):
        if max_pages is not None:
            batch_page = 0
            for item in batch:
                if isinstance(item, dict):
                    batch_page = max(batch_page, element_page(item))
            if batch_page and batch_page > max_pages:
                break
        all_elements.extend(batch)
        if stop_after_first_nonempty_batch and all_elements:
            break
    return all_elements


def _load_chunk_content_sample(elements_path: Path, *, max_chars: int = 1500) -> str:
    raw = _load_json(elements_path, [])
    parts: List[str] = []
    total = 0
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return " ".join(parts)[:max_chars]


def _extract_bbox(element: Dict[str, Any]) -> str:
    metadata = element.get("metadata") if isinstance(element, dict) else None
    if not isinstance(metadata, dict):
        return ""

    bbox = metadata.get("bbox")
    if bbox:
        if isinstance(bbox, str):
            return bbox
        try:
            return json.dumps(bbox)
        except Exception:
            return ""

    coordinates = metadata.get("coordinates")
    if isinstance(coordinates, dict):
        try:
            return json.dumps(coordinates)
        except Exception:
            return ""
    return ""


def _shape_element(element: Dict[str, Any]) -> Dict[str, Any]:
    metadata = element.get("metadata") if isinstance(element, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": str(element.get("element_id") or ""),
        "type": element_category(element),
        "page": element_page(element),
        "text": element_text(element),
        "bbox": _extract_bbox(element),
        "html": str(metadata.get("text_as_html") or ""),
        "metadata": metadata,
        "discard_reason": str(element.get("_discard_reason") or ""),
    }


def _shape_chunks(chunks: Any, enriched_chunks: Any) -> List[Dict[str, Any]]:
    shaped_chunks = []
    enriched_by_content: Dict[str, List[Dict[str, Any]]] = {}
    seen_chunk_ids: Dict[str, int] = {}
    for item in enriched_chunks if isinstance(enriched_chunks, list) else []:
        if not isinstance(item, dict):
            continue
        content_key = str(item.get("page_content") or "").strip()
        if not content_key:
            continue
        enriched_by_content.setdefault(content_key, []).append(item)

    for index, item in enumerate(chunks if isinstance(chunks, list) else []):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        metadata_block = item.get("metadata")
        if not isinstance(metadata_block, dict):
            metadata_block = {}

        matches = enriched_by_content.get(content) or []
        match = matches[0] if matches else None
        if matches:
            enriched_by_content[content] = matches[1:]

        enriched_meta = match.get("metadata") if isinstance(match, dict) else {}
        if not isinstance(enriched_meta, dict):
            enriched_meta = {}

        base_chunk_id = str((match or {}).get("chunk_id") or f"chunk-{index}")
        duplicate_count = int(seen_chunk_ids.get(base_chunk_id, 0))
        seen_chunk_ids[base_chunk_id] = duplicate_count + 1
        preview_chunk_id = (
            base_chunk_id
            if duplicate_count == 0
            else f"{base_chunk_id}-dup-{duplicate_count}"
        )

        shaped_chunks.append(
            {
                "id": preview_chunk_id,
                "content": content,
                "page": int(
                    enriched_meta.get("page_number")
                    or metadata_block.get("page_number")
                    or 1
                ),
                "bbox": str(
                    enriched_meta.get("bbox")
                    or metadata_block.get("bbox")
                    or ""
                ),
                "section": str(
                    enriched_meta.get("section")
                    or metadata_block.get("section")
                    or ""
                ),
                "chunk_type": str(
                    enriched_meta.get("chunk_type")
                    or metadata_block.get("type")
                    or "text"
                ),
                "parent_id": str(
                    (match or {}).get("parent_id")
                    or metadata_block.get("parent_id")
                    or ""
                ),
                "doc_id": str(
                    (match or {}).get("doc_id")
                    or metadata_block.get("doc_id")
                    or ""
                ),
            }
        )

    return shaped_chunks


def _page_numbers(items: Any) -> List[int]:
    pages = set()
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                pages.add(int(element_page(item)))
    return sorted(page for page in pages if page > 0)


def _ensure_metadata_preview(
    *,
    pdf_path: str,
    job_path: Path,
    rag_mode: str,
    preprocessor: str,
) -> Path:
    path = job_path / PREVIEW_FILES["page1_preview"]
    if not path.exists():
        preview_elements = _stream_elements(
            pdf_path=pdf_path,
            output_json=str(path),
            rag_mode=rag_mode,
            pipeline_mode="metadata",
            preprocessor=preprocessor,
            stop_after_first_nonempty_batch=True,
        )
        if not preview_elements:
            raise RuntimeError("Preprocessing preview could not extract page metadata.")
        _write_json(path, preview_elements)
    return path


def _ensure_scope_artifacts(
    *,
    pdf_path: str,
    job_path: Path,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
    rag_mode: str,
    preprocessor: str,
    resolved_scope: ResolvedPreviewScope,
) -> Dict[str, Path]:
    paths = _artifact_paths(job_path, quick=resolved_scope == "quick")
    max_pages = QUICK_PREVIEW_PAGE_LIMIT if resolved_scope == "quick" else None

    if not paths["raw_elements"].exists():
        raw_elements = _stream_elements(
            pdf_path=pdf_path,
            output_json=str(paths["raw_elements"]),
            rag_mode=rag_mode,
            pipeline_mode="commit",
            preprocessor=preprocessor,
            stop_after_first_nonempty_batch=False,
            max_pages=max_pages,
        )
        if not raw_elements:
            raise RuntimeError("Preprocessing preview could not extract document elements.")
        _write_json(paths["raw_elements"], raw_elements)

    filters_refreshed = False
    if (
        not paths["filtered_elements"].exists()
        or not paths["removed_elements"].exists()
        or not paths["filter_report"].exists()
        or _filter_report_version(paths["filter_report"]) != FILTER_VERSION
    ):
        raw_elements = _load_json(paths["raw_elements"], [])
        if not isinstance(raw_elements, list) or not raw_elements:
            raise RuntimeError("Raw preprocessing artifacts are missing or invalid.")
        filter_result = filter_element_dicts(raw_elements)
        _write_json(paths["filtered_elements"], filter_result["filtered_elements"])
        _write_json(paths["removed_elements"], filter_result["removed_elements"])
        _write_json(paths["filter_report"], filter_result["summary"])
        filters_refreshed = True

    if not paths["chunks"].exists() or filters_refreshed:
        chunk_config = get_chunk_config(
            filename=str(extra_metadata.get("source_file") or pdf_path),
            document_title=str(
                extra_metadata.get("document_title")
                or extra_metadata.get("document_type")
                or ""
            ),
            content_sample=_load_chunk_content_sample(paths["filtered_elements"]),
            metadata=extra_metadata,
        )
        chunker = ContextAwareChunker(
            chunk_size=chunk_config.chunk_size,
            chunk_overlap=chunk_config.chunk_overlap,
        )
        chunker.process(
            input_file=str(paths["filtered_elements"]),
            output_file=str(paths["chunks"]),
        )

    if not paths["enriched_chunks"].exists() or filters_refreshed:
        enrich_chunks(
            chunks_file=str(paths["chunks"]),
            output_file=str(paths["enriched_chunks"]),
            pdf_path=pdf_path,
            company_document_id=company_document_id,
            extra_metadata=extra_metadata,
        )

    return paths


def _single_page_pdf(pdf_path: str, page_number: int) -> str:
    reader = PdfReader(str(pdf_path))
    if page_number < 1 or page_number > len(reader.pages):
        raise RuntimeError(f"Requested page {page_number} is out of range.")

    writer = PdfWriter()
    writer.add_page(reader.pages[page_number - 1])

    temp_file = tempfile.NamedTemporaryFile(
        suffix=f"_page_{page_number}.pdf",
        delete=False,
    )
    temp_file.close()
    with open(temp_file.name, "wb") as handle:
        writer.write(handle)
    return temp_file.name


def _normalize_page_elements(elements: List[Dict[str, Any]], page_number: int) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        cloned = dict(item)
        metadata = cloned.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        metadata["page_number"] = page_number
        cloned["metadata"] = metadata
        normalized.append(cloned)
    return normalized


def ensure_page_preview_artifacts(
    *,
    pdf_path: str,
    job_dir: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
    page_number: int,
    rag_mode: Optional[str] = None,
    preprocessor: Optional[str] = None,
) -> Dict[str, Path]:
    job_path = Path(job_dir)
    page_dir = job_path / PAGE_PREVIEW_DIRNAME / f"page_{page_number:04d}"
    page_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "raw_elements": page_dir / "raw_elements.json",
        "filtered_elements": page_dir / "filtered_elements.json",
        "removed_elements": page_dir / "removed_elements.json",
        "filter_report": page_dir / "filter_report.json",
        "chunks": page_dir / "chunks.json",
        "enriched_chunks": page_dir / "enriched_chunks.json",
    }

    settings = _resolve_settings(
        extra_metadata=extra_metadata,
        rag_mode=rag_mode,
        preprocessor=preprocessor,
    )

    if not paths["raw_elements"].exists():
        temp_pdf = _single_page_pdf(pdf_path, page_number)
        try:
            raw_elements = _stream_elements(
                pdf_path=temp_pdf,
                output_json=str(paths["raw_elements"]),
                rag_mode=settings["rag_mode"],
                pipeline_mode="commit",
                preprocessor=settings["preprocessor"],
                stop_after_first_nonempty_batch=False,
            )
        finally:
            if os.path.exists(temp_pdf):
                try:
                    os.remove(temp_pdf)
                except Exception:
                    pass

        raw_elements = _normalize_page_elements(raw_elements, page_number)
        if not raw_elements:
            raise RuntimeError(f"No preview data could be extracted for page {page_number}.")
        _write_json(paths["raw_elements"], raw_elements)

    filters_refreshed = False
    if (
        not paths["filtered_elements"].exists()
        or not paths["removed_elements"].exists()
        or not paths["filter_report"].exists()
        or _filter_report_version(paths["filter_report"]) != FILTER_VERSION
    ):
        raw_elements = _load_json(paths["raw_elements"], [])
        if not isinstance(raw_elements, list) or not raw_elements:
            raise RuntimeError("Page preview artifacts are missing or invalid.")
        filter_result = filter_element_dicts(raw_elements)
        _write_json(paths["filtered_elements"], filter_result["filtered_elements"])
        _write_json(paths["removed_elements"], filter_result["removed_elements"])
        _write_json(paths["filter_report"], filter_result["summary"])
        filters_refreshed = True

    if not paths["chunks"].exists() or filters_refreshed:
        chunk_config = get_chunk_config(
            filename=str(extra_metadata.get("source_file") or pdf_path),
            document_title=str(
                extra_metadata.get("document_title")
                or extra_metadata.get("document_type")
                or ""
            ),
            content_sample=_load_chunk_content_sample(paths["filtered_elements"]),
            metadata=extra_metadata,
        )
        chunker = ContextAwareChunker(
            chunk_size=chunk_config.chunk_size,
            chunk_overlap=chunk_config.chunk_overlap,
        )
        chunker.process(
            input_file=str(paths["filtered_elements"]),
            output_file=str(paths["chunks"]),
        )

    if not paths["enriched_chunks"].exists() or filters_refreshed:
        enrich_chunks(
            chunks_file=str(paths["chunks"]),
            output_file=str(paths["enriched_chunks"]),
            pdf_path=pdf_path,
            company_document_id=company_document_id,
            extra_metadata=extra_metadata,
        )

    return paths


def ensure_preview_artifacts(
    *,
    pdf_path: str,
    job_dir: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
    rag_mode: Optional[str] = None,
    preprocessor: Optional[str] = None,
    scope: PreviewScope = "auto",
) -> Dict[str, Any]:
    job_path = Path(job_dir)
    job_path.mkdir(parents=True, exist_ok=True)
    settings = _resolve_settings(
        extra_metadata=extra_metadata,
        rag_mode=rag_mode,
        preprocessor=preprocessor,
    )
    stats = _document_stats(pdf_path)
    resolved_scope = _resolve_scope(
        scope,
        stats,
        fast_document_processing=bool(settings.get("fast_document_processing", False)),
    )
    page1_preview = _ensure_metadata_preview(
        pdf_path=pdf_path,
        job_path=job_path,
        rag_mode=settings["rag_mode"],
        preprocessor=settings["preprocessor"],
    )
    paths = _ensure_scope_artifacts(
        pdf_path=pdf_path,
        job_path=job_path,
        company_document_id=company_document_id,
        extra_metadata=extra_metadata,
        rag_mode=settings["rag_mode"],
        preprocessor=settings["preprocessor"],
        resolved_scope=resolved_scope,
    )
    return {
        "paths": paths,
        "page1_preview": page1_preview,
        "scope": resolved_scope,
        "document_stats": stats,
    }


def build_preprocessing_preview(
    *,
    pdf_path: str,
    job_dir: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
    rag_mode: Optional[str] = None,
    preprocessor: Optional[str] = None,
    scope: PreviewScope = "auto",
) -> Dict[str, Any]:
    artifact_bundle = ensure_preview_artifacts(
        pdf_path=pdf_path,
        job_dir=job_dir,
        company_document_id=company_document_id,
        extra_metadata=extra_metadata,
        rag_mode=rag_mode,
        preprocessor=preprocessor,
        scope=scope,
    )
    paths = artifact_bundle["paths"]
    resolved_scope = artifact_bundle["scope"]
    document_stats = artifact_bundle["document_stats"]

    metadata = extract_document_metadata(
        elements_file=str(artifact_bundle["page1_preview"]),
        pdf_path=pdf_path,
        company_document_id=company_document_id,
        extra_metadata=extra_metadata,
    )
    metadata_evidence_raw = _load_json(artifact_bundle["page1_preview"], [])
    raw_elements = _load_json(paths["raw_elements"], [])
    filtered_elements = _load_json(paths["filtered_elements"], [])
    removed_elements = _load_json(paths["removed_elements"], [])
    filter_report = _load_json(paths["filter_report"], {})
    chunks = _load_json(paths["chunks"], [])
    enriched_chunks = _load_json(paths["enriched_chunks"], [])

    tables = [
        _shape_element(item)
        for item in filtered_elements
        if isinstance(item, dict) and element_category(item) == "Table"
    ]
    shaped_chunks = _shape_chunks(chunks, enriched_chunks)
    indexed_pages = _page_numbers(filtered_elements)

    return {
        "job_id": str(Path(job_dir).name),
        "pdf_path": pdf_path,
        "company_document_id": company_document_id,
        "revision_number": str(extra_metadata.get("revision_number") or ""),
        "source_file": str(extra_metadata.get("source_file") or Path(pdf_path).name),
        "preview_mode": resolved_scope,
        "requested_scope": scope,
        "can_load_full": resolved_scope == "quick" and bool(document_stats.get("is_large")),
        "document_stats": document_stats,
        "indexed_pages": indexed_pages,
        "metadata_candidates": metadata,
        "metadata_evidence": [
            _shape_element(item)
            for item in metadata_evidence_raw
            if isinstance(item, dict)
        ],
        "tables": tables,
        "chunks": shaped_chunks,
        "removed_elements": [
            _shape_element(item)
            for item in removed_elements
            if isinstance(item, dict)
        ],
        "summary": {
            "raw_elements": len(raw_elements) if isinstance(raw_elements, list) else 0,
            "filtered_elements": len(filtered_elements) if isinstance(filtered_elements, list) else 0,
            "removed_elements": len(removed_elements) if isinstance(removed_elements, list) else 0,
            "tables": len(tables),
            "chunks": len(shaped_chunks),
            "filter_report": filter_report if isinstance(filter_report, dict) else {},
            "indexed_page_count": len(indexed_pages),
            "remaining_page_count": max(int(document_stats.get("page_count") or 0) - len(indexed_pages), 0),
        },
    }


def build_preprocessing_page_preview(
    *,
    pdf_path: str,
    job_dir: str,
    company_document_id: str,
    extra_metadata: Dict[str, Any],
    page_number: int,
    rag_mode: Optional[str] = None,
    preprocessor: Optional[str] = None,
    scope: PreviewScope = "auto",
) -> Dict[str, Any]:
    artifact_bundle = ensure_preview_artifacts(
        pdf_path=pdf_path,
        job_dir=job_dir,
        company_document_id=company_document_id,
        extra_metadata=extra_metadata,
        rag_mode=rag_mode,
        preprocessor=preprocessor,
        scope=scope,
    )
    paths = artifact_bundle["paths"]
    resolved_scope = artifact_bundle["scope"]
    document_stats = artifact_bundle["document_stats"]

    page_count = int(document_stats.get("page_count") or 0)
    if page_number < 1 or page_number > page_count:
        raise RuntimeError(f"Requested page {page_number} is out of range.")

    filtered_elements = _load_json(paths["filtered_elements"], [])
    removed_elements = _load_json(paths["removed_elements"], [])
    chunks = _load_json(paths["chunks"], [])
    enriched_chunks = _load_json(paths["enriched_chunks"], [])

    page_filtered = (
        [
            item
            for item in filtered_elements
            if isinstance(item, dict) and element_page(item) == page_number
        ]
        if isinstance(filtered_elements, list)
        else []
    )
    page_removed = (
        [
            item
            for item in removed_elements
            if isinstance(item, dict) and element_page(item) == page_number
        ]
        if isinstance(removed_elements, list)
        else []
    )
    page_chunks = [
        item for item in _shape_chunks(chunks, enriched_chunks)
        if int(item.get("page") or 0) == page_number
    ]

    available_in_scope = bool(page_filtered or page_removed or page_chunks)
    source_scope = resolved_scope

    if not available_in_scope:
        page_paths = ensure_page_preview_artifacts(
            pdf_path=pdf_path,
            job_dir=job_dir,
            company_document_id=company_document_id,
            extra_metadata=extra_metadata,
            page_number=page_number,
            rag_mode=rag_mode,
            preprocessor=preprocessor,
        )
        page_filtered = _load_json(page_paths["filtered_elements"], [])
        page_removed = _load_json(page_paths["removed_elements"], [])
        page_chunks = _shape_chunks(
            _load_json(page_paths["chunks"], []),
            _load_json(page_paths["enriched_chunks"], []),
        )
        source_scope = "page"

    page_tables = [
        _shape_element(item)
        for item in page_filtered
        if isinstance(item, dict) and element_category(item) == "Table"
    ]

    return {
        "job_id": str(Path(job_dir).name),
        "page": page_number,
        "requested_scope": scope,
        "preview_mode": resolved_scope,
        "source_scope": source_scope,
        "available_in_scope": available_in_scope,
        "elements": [
            _shape_element(item)
            for item in page_filtered
            if isinstance(item, dict)
        ],
        "tables": page_tables,
        "chunks": page_chunks,
        "removed_elements": [
            _shape_element(item)
            for item in page_removed
            if isinstance(item, dict)
        ],
        "summary": {
            "elements": len(page_filtered),
            "tables": len(page_tables),
            "chunks": len(page_chunks),
            "removed_elements": len(page_removed),
            "page_count": page_count,
        },
    }
