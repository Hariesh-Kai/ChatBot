# backend/rag/preprocess.py

import gc
import json
import os
import re
import traceback
import uuid
from pathlib import Path
from typing import Generator, List, Optional

import torch
from pypdf import PdfReader, PdfWriter
from unstructured.partition.pdf import partition_pdf

from backend.rag.mode_profiles import get_preprocess_profile, normalize_rag_mode
from backend.rag.preprocessor_registry import normalize_rag_preprocessor
from backend.rag.resource_planner import get_optimal_strategy, limit_cpu_usage


_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")


def _resolve_extraction_metadata(
    *,
    preprocessor: str,
    rag_mode: str,
    pipeline_mode: str,
) -> dict:
    resolved_preprocessor = normalize_rag_preprocessor(preprocessor)
    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_preprocess_profile(
        resolved_rag_mode,
        pipeline_mode=str(pipeline_mode or "commit"),
    )

    if resolved_preprocessor == "pymupdf4llm":
        source_weight_key = "pymupdf"
        ocr_used = bool(resolved_rag_mode == "high_fidelity")
    elif resolved_preprocessor == "docling":
        source_weight_key = "docling"
        ocr_used = False
    elif resolved_preprocessor == "pypdf_text":
        source_weight_key = "pypdf_text"
        ocr_used = False
    else:
        strategy = str(profile.get("strategy") or "hi_res").strip().lower()
        source_weight_key = "unstructured_hi_res" if strategy == "hi_res" else "unstructured_fast"
        ocr_used = bool(strategy == "hi_res")

    return {
        "extraction_backend": resolved_preprocessor,
        "extraction_source": source_weight_key,
        "source_weight_key": source_weight_key,
        "ocr_used": bool(ocr_used),
        "pipeline_mode": str(pipeline_mode or "commit"),
        "rag_mode": resolved_rag_mode,
    }


def _annotate_batch_extraction_metadata(
    batch: List[dict],
    *,
    extraction_metadata: dict,
) -> List[dict]:
    annotated: List[dict] = []
    for item in batch or []:
        if not isinstance(item, dict):
            continue

        cloned = dict(item)
        metadata = dict(cloned.get("metadata") or {})
        for key, value in extraction_metadata.items():
            metadata[key] = value
        cloned["metadata"] = metadata
        annotated.append(cloned)

    return annotated


def _make_element(
    element_type: str,
    text: str,
    page_number: int,
    *,
    extra_metadata: Optional[dict] = None,
) -> dict:
    metadata = {"page_number": int(page_number)}
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        "type": element_type,
        "element_id": str(uuid.uuid4()),
        "text": text,
        "metadata": metadata,
    }


def _split_markdown_block(
    block_text: str,
    page_number: int,
) -> List[dict]:
    text = (block_text or "").strip()
    if not text:
        return []

    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    first_line = lines[0].strip()

    if first_line.startswith("#"):
        title = re.sub(r"^#{1,6}\s*", "", first_line).strip()
        remainder = "\n".join(lines[1:]).strip()
        out = []
        if title:
            out.append(_make_element("Title", title, page_number))
        if remainder:
            out.extend(_split_markdown_block(remainder, page_number))
        return out

    if len(lines) >= 2 and "|" in lines[0] and _TABLE_SEPARATOR_RE.match(lines[1]):
        return [_make_element("Table", "\n".join(lines), page_number)]

    if all(_LIST_ITEM_RE.match(line.strip()) for line in lines):
        return [
            _make_element("ListItem", line.strip(), page_number)
            for line in lines
            if line.strip()
        ]

    return [_make_element("NarrativeText", "\n".join(lines), page_number)]


def _build_markdown_elements(
    markdown_text: str,
    page_number: int,
) -> List[dict]:
    clean_text = (markdown_text or "").replace("\x00", " ").strip()
    if not clean_text:
        return []

    blocks = re.split(r"\n\s*\n+", clean_text)
    elements: List[dict] = []
    for raw_block in blocks:
        block = raw_block.strip()
        if not block or block == "<!-- image -->":
            continue
        elements.extend(_split_markdown_block(block, page_number))
    return elements


def _stream_unstructured_elements(
    pdf_path: str,
    output_json: str,
    *,
    rag_mode: str = "balanced",
    pipeline_mode: str = "commit",
) -> Generator[List[dict], None, None]:
    """Current production preprocessor using Unstructured."""
    print(f"[PREPROCESS][unstructured] Starting PDF parse: {pdf_path}")

    pdf_path = Path(pdf_path)
    output_json = Path(output_json)
    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_preprocess_profile(
        resolved_rag_mode,
        pipeline_mode=str(pipeline_mode or "commit"),
    )
    
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # 1. Setup Image Output Directory (All pages save images here)
    image_output_dir = output_json.parent / "images"
    image_output_dir.mkdir(parents=True, exist_ok=True)

    # 2. Resource Planning
    file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
    strategy, cores, batch_size = get_optimal_strategy(file_size_mb)
    
    print(f"[PREPROCESS][unstructured] Strategy: {strategy} | Cores: {cores} | Processing {file_size_mb:.2f} MB")
    print(
        "[PREPROCESS][unstructured] RAG profile: "
        f"mode={resolved_rag_mode} pipeline_mode={pipeline_mode} "
        f"ocr_strategy={profile.get('strategy')}"
    )
    
    # 3. Pin CPU Cores (Prevents Windows Freeze)
    limit_cpu_usage(cores)

    # 4. Check Hardware Acceleration
    if torch.cuda.is_available() and not profile.get("prefer_quantized_hi_res", False):
        model_name = "yolox"
        print(f"[PREPROCESS][unstructured] GPU detected. Using model: '{model_name}'")
    else:
        model_name = "yolox_quantized"
        print(f"[PREPROCESS][unstructured] No GPU/high-speed mode. Using model: '{model_name}'")

    # 5. Open PDF Stream
    try:
        reader = PdfReader(str(pdf_path))
        total_pages = len(reader.pages)
        print(f"[PREPROCESS][unstructured] Document has {total_pages} pages. Starting stream...")
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF: {e}") from e

    elements_buffer = []
    extracted_pages = 0
    failed_pages = 0

    # 6. Page-by-Page Processing Loop
    for i in range(total_pages):
        # A. Create a temporary single-page PDF
        page_writer = PdfWriter()
        page_writer.add_page(reader.pages[i])
        
        temp_filename = (
            pdf_path.parent
            / f"temp_processing_page_{i+1}_{uuid.uuid4().hex}.pdf"
        )
        
        try:
            with open(temp_filename, "wb") as f:
                page_writer.write(f)
            
            # B. Process ONLY this small file (Low RAM usage)
            # This is the heavy lifting step.
            partition_kwargs = {
                "filename": str(temp_filename),
                "strategy": profile.get("strategy", "hi_res"),
                "languages": ["eng"],
            }

            if partition_kwargs["strategy"] == "hi_res":
                partition_kwargs["infer_table_structure"] = bool(
                    profile.get("infer_table_structure", True)
                )
                partition_kwargs["hi_res_model_name"] = model_name

                if profile.get("extract_images_in_pdf", False):
                    partition_kwargs["extract_images_in_pdf"] = True
                    partition_kwargs["extract_image_block_types"] = (
                        profile.get("extract_image_block_types") or []
                    )
                    partition_kwargs["extract_image_block_output_dir"] = str(image_output_dir)
                    partition_kwargs["extract_image_block_to_payload"] = False

            page_elements = partition_pdf(**partition_kwargs)
            
            # C. Enrich Metadata (Add correct page number)
            # Since we split the PDF, 'page_number' will always be 1. We must fix it.
            for el in page_elements:
                el_dict = el.to_dict()
                if "metadata" not in el_dict:
                    el_dict["metadata"] = {}
                
                # Override page number with the REAL loop index
                el_dict["metadata"]["page_number"] = i + 1
                elements_buffer.append(el_dict)
            
            # D. Yield if buffer is full (or simple page-by-page yield)
            # Yielding every page ensures the frontend sees progress fast.
            yield elements_buffer
            elements_buffer = [] 
            extracted_pages += 1
            
            # E. Force RAM Cleanup
            gc.collect()

        except Exception as e:
            print(
                "[PREPROCESS][unstructured] Error processing page "
                f"{i+1} temp_file={temp_filename}: {type(e).__name__}: {e}"
            )
            traceback.print_exc()
            # Don't crash the whole job for one bad page
            failed_pages += 1
            continue
            
        finally:
            # F. Delete temp file immediately
            if temp_filename.exists():
                try:
                    temp_filename.unlink()
                except Exception:
                    pass
        
    # 7. Final Cleanup

    print(
        "[PREPROCESS][unstructured] Streaming complete | "
        f"processed_pages={extracted_pages} failed_pages={failed_pages}"
    )
    gc.collect()


def _build_pypdf_page_elements(page_text: str, page_number: int) -> List[dict]:
    clean_text = (page_text or "").replace("\x00", " ").strip()
    if not clean_text:
        return []

    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    if not lines:
        return []

    elements: List[dict] = []
    first_line = lines[0]
    body = "\n".join(lines[1:]).strip()
    base_meta = {"page_number": page_number}

    if len(first_line) <= 120 and any(ch.isalpha() for ch in first_line):
        elements.append(
            {
                "type": "Title",
                "element_id": str(uuid.uuid4()),
                "text": first_line,
                "metadata": dict(base_meta),
            }
        )
        if body:
            elements.append(
                {
                    "type": "NarrativeText",
                    "element_id": str(uuid.uuid4()),
                    "text": body,
                    "metadata": dict(base_meta),
                }
            )
        return elements

    elements.append(
        _make_element("NarrativeText", clean_text, page_number, extra_metadata=base_meta)
    )
    return elements


def _stream_pypdf_text_elements(
    pdf_path: str,
    output_json: str,
    *,
    rag_mode: str = "balanced",
    pipeline_mode: str = "commit",
) -> Generator[List[dict], None, None]:
    """
    Lightweight text-only fallback extractor using pypdf.

    This backend is intentionally simple so we can compare it against
    the Unstructured pipeline while keeping chunking/evaluation identical.
    """
    del output_json, rag_mode, pipeline_mode

    print(f"[PREPROCESS][pypdf_text] Starting PDF parse: {pdf_path}")

    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path_obj}")

    try:
        reader = PdfReader(str(pdf_path_obj))
    except Exception as exc:
        raise RuntimeError(f"Failed to read PDF: {exc}") from exc

    extracted_pages = 0
    empty_pages = 0

    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            print(f"[PREPROCESS][pypdf_text] Error extracting page {idx}: {exc}")
            continue

        batch = _build_pypdf_page_elements(text, idx)
        if not batch:
            empty_pages += 1
            continue

        extracted_pages += 1
        yield batch

    print(
        "[PREPROCESS][pypdf_text] Streaming complete | "
        f"processed_pages={extracted_pages} empty_pages={empty_pages}"
    )


def _stream_pymupdf4llm_elements(
    pdf_path: str,
    output_json: str,
    *,
    rag_mode: str = "balanced",
    pipeline_mode: str = "commit",
) -> Generator[List[dict], None, None]:
    del output_json

    try:
        import pymupdf4llm
    except Exception as exc:
        raise RuntimeError(
            "PyMuPDF4LLM preprocessor requires the optional dependency "
            "'pymupdf4llm'. Install it into the venv first."
        ) from exc

    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_preprocess_profile(
        resolved_rag_mode,
        pipeline_mode=str(pipeline_mode or "commit"),
    )
    print(f"[PREPROCESS][pymupdf4llm] Starting PDF parse: {pdf_path}")

    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path_obj}")

    kwargs = {
        "page_chunks": True,
        # Balanced mode should stay fast on local CPU-only installs.
        # Reserve OCR-heavy parsing for explicit high-fidelity ingestion.
        "use_ocr": bool(resolved_rag_mode == "high_fidelity"),
        "force_ocr": False,
    }

    try:
        page_chunks = pymupdf4llm.to_markdown(str(pdf_path_obj), **kwargs)
    except TypeError:
        # Older versions may not support page_chunks/use_ocr yet.
        page_chunks = pymupdf4llm.to_markdown(str(pdf_path_obj))

    extracted_pages = 0
    empty_pages = 0

    if isinstance(page_chunks, str):
        batch = _build_markdown_elements(page_chunks, 1)
        if batch:
            extracted_pages = 1
            yield batch
        else:
            empty_pages = 1
    else:
        for idx, page_chunk in enumerate(page_chunks or [], start=1):
            if isinstance(page_chunk, dict):
                page_text = str(
                    page_chunk.get("text")
                    or page_chunk.get("md")
                    or page_chunk.get("markdown")
                    or ""
                )
                page_number = int(page_chunk.get("page") or page_chunk.get("page_number") or idx)
            else:
                page_text = str(page_chunk or "")
                page_number = idx

            batch = _build_markdown_elements(page_text, page_number)
            if not batch:
                empty_pages += 1
                continue

            extracted_pages += 1
            yield batch

    print(
        "[PREPROCESS][pymupdf4llm] Streaming complete | "
        f"processed_pages={extracted_pages} empty_pages={empty_pages}"
    )


def _stream_docling_elements(
    pdf_path: str,
    output_json: str,
    *,
    rag_mode: str = "balanced",
    pipeline_mode: str = "commit",
) -> Generator[List[dict], None, None]:
    del output_json

    try:
        from docling.document_converter import DocumentConverter
    except Exception as exc:
        raise RuntimeError(
            "Docling preprocessor requires the optional dependency 'docling'. "
            "Install it into the venv first."
        ) from exc

    resolved_rag_mode = normalize_rag_mode(rag_mode)
    _ = get_preprocess_profile(
        resolved_rag_mode,
        pipeline_mode=str(pipeline_mode or "commit"),
    )

    print(f"[PREPROCESS][docling] Starting PDF parse: {pdf_path}")

    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path_obj}")

    try:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path_obj))
        document = getattr(result, "document", result)
    except Exception as exc:
        raise RuntimeError(f"Docling conversion failed: {exc}") from exc

    pages = getattr(document, "pages", {}) or {}
    raw_page_numbers = sorted(
        int(page_no)
        for page_no in pages.keys()
        if isinstance(page_no, (int, float, str)) and str(page_no).strip()
    )

    if not raw_page_numbers:
        raw_page_numbers = [1]

    page_offset = 1 if raw_page_numbers and min(raw_page_numbers) == 0 else 0
    extracted_pages = 0
    empty_pages = 0

    for raw_page_number in raw_page_numbers:
        normalized_page_number = int(raw_page_number) + page_offset
        try:
            markdown = document.export_to_markdown(
                page_no=raw_page_number,
                traverse_pictures=True,
            )
        except TypeError:
            markdown = document.export_to_markdown(page_no=raw_page_number)
        except Exception as exc:
            print(f"[PREPROCESS][docling] Error exporting page {raw_page_number}: {exc}")
            continue

        batch = _build_markdown_elements(markdown, normalized_page_number)
        if not batch:
            empty_pages += 1
            continue

        extracted_pages += 1
        yield batch

    print(
        "[PREPROCESS][docling] Streaming complete | "
        f"processed_pages={extracted_pages} empty_pages={empty_pages}"
    )


def stream_pdf_to_elements(
    pdf_path: str,
    output_json: str,
    *,
    rag_mode: str = "balanced",
    pipeline_mode: str = "commit",
    preprocessor: str = "unstructured",
) -> Generator[List[dict], None, None]:
    """
    Generator that processes a PDF page-by-page to save RAM.

    Supported preprocessors:
    - unstructured: adaptive Unstructured-based parser (current production path)
    - pypdf_text: lightweight text-only baseline for side-by-side benchmarking
    - pymupdf4llm: layout-aware markdown extraction with optional OCR support
    - docling: Docling conversion with page-wise markdown export
    """
    resolved_preprocessor = normalize_rag_preprocessor(preprocessor)
    print(f"[PREPROCESS] Selected preprocessor: {resolved_preprocessor}")
    extraction_metadata = _resolve_extraction_metadata(
        preprocessor=resolved_preprocessor,
        rag_mode=rag_mode,
        pipeline_mode=pipeline_mode,
    )

    if resolved_preprocessor == "pypdf_text":
        source_stream = _stream_pypdf_text_elements(
            pdf_path,
            output_json,
            rag_mode=rag_mode,
            pipeline_mode=pipeline_mode,
        )
    elif resolved_preprocessor == "pymupdf4llm":
        source_stream = _stream_pymupdf4llm_elements(
            pdf_path,
            output_json,
            rag_mode=rag_mode,
            pipeline_mode=pipeline_mode,
        )
    elif resolved_preprocessor == "docling":
        source_stream = _stream_docling_elements(
            pdf_path,
            output_json,
            rag_mode=rag_mode,
            pipeline_mode=pipeline_mode,
        )
    else:
        source_stream = _stream_unstructured_elements(
            pdf_path,
            output_json,
            rag_mode=rag_mode,
            pipeline_mode=pipeline_mode,
        )

    for batch in source_stream:
        yield _annotate_batch_extraction_metadata(
            batch,
            extraction_metadata=extraction_metadata,
        )
