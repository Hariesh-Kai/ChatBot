from __future__ import annotations

import importlib.util
from typing import Any, Literal


RagPreprocessor = Literal["unstructured", "pypdf_text", "pymupdf4llm", "docling"]


def _detect_default_rag_preprocessor() -> RagPreprocessor:
    """
    Prefer the parser that is most reliable for local/offline installs.
    PyMuPDF4LLM works well on CPU-only Windows systems without extra model
    downloads, while Unstructured / Docling often need additional artifacts.
    """
    if importlib.util.find_spec("pymupdf4llm") is not None:
        return "pymupdf4llm"
    if importlib.util.find_spec("unstructured") is not None:
        return "unstructured"
    return "pypdf_text"


DEFAULT_RAG_PREPROCESSOR: RagPreprocessor = _detect_default_rag_preprocessor()
ALLOWED_RAG_PREPROCESSORS = {"unstructured", "pypdf_text", "pymupdf4llm", "docling"}


def normalize_rag_preprocessor(value: Any) -> RagPreprocessor:
    raw = str(value or "").strip().lower().replace("-", "_")

    aliases = {
        "unstructured_auto": "unstructured",
        "unstructured_hi_res": "unstructured",
        "unstructured_fast": "unstructured",
        "pypdf": "pypdf_text",
        "pdf_text": "pypdf_text",
        "pymupdf": "pymupdf4llm",
        "pymupdf_4_llm": "pymupdf4llm",
        "fitz4llm": "pymupdf4llm",
        "ibm_docling": "docling",
    }
    resolved = aliases.get(raw, raw)

    if resolved in ALLOWED_RAG_PREPROCESSORS:
        return resolved  # type: ignore[return-value]

    return DEFAULT_RAG_PREPROCESSOR


def get_rag_preprocessor_options() -> dict[str, str]:
    return {
        "unstructured": "Unstructured PDF parsing with adaptive fast/hi_res modes.",
        "pypdf_text": "Lightweight text-only extraction using pypdf.",
        "pymupdf4llm": "PyMuPDF4LLM markdown extraction with layout-aware OCR support.",
        "docling": "Docling document conversion with page-wise markdown export.",
    }
