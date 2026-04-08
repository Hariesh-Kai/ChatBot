# backend/rag/chunk.py

import json
import sys
import re
import uuid
from io import StringIO
from typing import Any, Dict, List, Tuple

import pandas as pd
from langchain_core.documents import Document
from unstructured.staging.base import elements_from_json

from backend.rag.table_normalization import normalized_table_to_text


DEFAULT_SECTION = "General / Introduction"
MIN_CHUNK_WORDS = 200
TARGET_CHUNK_WORDS = 300
MAX_CHUNK_WORDS = 400
DEFAULT_OVERLAP_RATIO = 0.15
MAX_DEBUG_PREVIEW_CHARS = 120

_WORD_RE = re.compile(r"\S+")
_PAGE_MARKER_RE = re.compile(r"^\s*page\s+\d+(?:\s*(?:of|/)\s*\d+)?\s*$", re.IGNORECASE)
_MAP_TO_PAGE_RE = re.compile(r"^\s*map to page(?:\s*[:\-]?\s*\d+)?\s*$", re.IGNORECASE)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)]|[A-Za-z][.)])\s+")
_TABLE_ROW_RE = re.compile(r"\|")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[;:])\s+")
_WEAK_SECTION_LABELS = {"unknown", "n/a", "na", "text", "page", "map to page"}
_BULLET_CHARS = "-*+\u2022\u2023\u25e6\u2043\u2219\uf0b7"
_LIST_ITEM_RE = re.compile(
    rf"^\s*(?:[{re.escape(_BULLET_CHARS)}]|\d+[.)]|[A-Za-z][.)])\s+"
)
_BULLET_PREFIX_RE = re.compile(rf"^\s*[{re.escape(_BULLET_CHARS)}]\s*")
_STANDALONE_DOCUMENT_NUMBER_RE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9/_\\-]{7,}$")
_TITLE_BLOCK_FIELD_PATTERNS = (
    (
        "document_number",
        re.compile(
            r"^\s*(?:company\s+document\s+id|document\s+(?:id|number)|doc(?:ument)?\s+(?:id|number)|file\s+no\.?)\s*[:\-]?\s*(.+?)\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "revision_code",
        re.compile(r"^\s*(?:revision|rev(?:ision)?\.?)\s*[:\-]?\s*([A-Za-z0-9._/-]+)\s*$", re.IGNORECASE),
    ),
    (
        "project_name",
        re.compile(r"^\s*(?:project(?:\s+name)?|development|field)\s*[:\-]?\s*(.+?)\s*$", re.IGNORECASE),
    ),
    (
        "document_title",
        re.compile(r"^\s*(?:document\s+title|title)\s*[:\-]?\s*(.+?)\s*$", re.IGNORECASE),
    ),
    (
        "document_validity",
        re.compile(r"^\s*validity\s*[:\-]?\s*(.+?)\s*$", re.IGNORECASE),
    ),
    (
        "title_block_source_file",
        re.compile(r"^\s*file\s+name\s*[:\-]?\s*(.+?)\s*$", re.IGNORECASE),
    ),
)
_PROTECTED_UNIT_RE = re.compile(
    r"(?i)(?:\b\d+(?:\.\d+)?\s*(?:bar|kpa|psi|pa|deg\s*c|m3/h|kg/h|ppm|bpd|mmscfd|nm3/h)\b|\b\d+(?:\.\d+)?\s*%|°\s*c|\b(?:deg\s*c|m3/h|kg/h|ppm|bpd|mmscfd|nm3/h)\b)"
)
_PROTECTED_ENTITY_TERMS = (
    "oil",
    "gas",
    "water cut",
    "emulsion",
    "separator",
    "compressor",
    "pipeline",
    "wellhead",
    "fpso",
    "flowline",
    "manifold",
    "slug",
    "dehydration",
    "injection",
    "gas lift",
)
_PROTECTED_PROCESS_TERMS = (
    "design pressure",
    "operating temperature",
    "process description",
    "flow assurance",
    "separation",
    "heating",
    "cooling",
    "treating",
    "inversion point",
)
_PROTECTED_UNIT_RE = re.compile(
    "(?i)(?:\\b\\d+(?:\\.\\d+)?\\s*(?:bar|kpa|psi|pa|deg\\s*c|m3/h|kg/h|ppm|bpd|mmscfd|nm3/h)\\b|\\b\\d+(?:\\.\\d+)?\\s*%|\u00b0\\s*c|\\b(?:deg\\s*c|m3/h|kg/h|ppm|bpd|mmscfd|nm3/h)\\b)"
)

# ============================================================
# OCR / TEXT NORMALIZATION (SAFE)
# ============================================================

def normalize_numbers(text: str) -> str:
    """
    Conservative OCR cleanup:
    - O -> 0 when adjacent to digits
    - l -> 1 when adjacent to digits
    - collapse spaced numbers (1 100 -> 1100)
    """
    if not text:
        return text

    text = re.sub(r"(?<=\d)[Oo](?=\d)", "0", text)
    text = re.sub(r"(?<=\d)[lI](?=\d)", "1", text)
    text = re.sub(r"(\d)\s+(\d)", r"\1\2", text)

    return text


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(str(text or "")))


def _content_preview(text: str, *, max_chars: int = MAX_DEBUG_PREVIEW_CHARS) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars].rstrip()}..."


# ============================================================
# CONTEXT-AWARE CHUNKER (PARENT-CHILD)
# ============================================================

class ContextAwareChunker:
    def __init__(self, *, chunk_size: int = 3000, chunk_overlap: int = 400):
        safe_chunk_size = max(int(chunk_size or 3000), 200)
        safe_chunk_overlap = max(int(chunk_overlap or 0), 0)
        if safe_chunk_overlap >= safe_chunk_size:
            safe_chunk_overlap = max(safe_chunk_size // 5, 0)

        self.chunk_size = safe_chunk_size
        self.chunk_overlap = safe_chunk_overlap
        self.target_chunk_words = TARGET_CHUNK_WORDS
        self.min_chunk_words = MIN_CHUNK_WORDS
        self.max_chunk_words = MAX_CHUNK_WORDS
        raw_overlap_ratio = safe_chunk_overlap / max(safe_chunk_size, 1)
        self.overlap_ratio = min(0.20, max(0.10, raw_overlap_ratio or DEFAULT_OVERLAP_RATIO))

        self.current_title_section = ""
        self.pending_title_heading = ""
        self.last_stable_section_by_page: Dict[int, str] = {}
        self.pending_structured_metadata_by_page: Dict[int, Dict[str, Any]] = {}
        self._reset_text_group()

    # --------------------------------------------------------
    # HTML → Markdown (Tables)
    # --------------------------------------------------------

    def html_to_markdown(self, html_content: str) -> str:
        try:
            dfs = pd.read_html(StringIO(html_content))
            if dfs:
                return dfs[0].to_markdown(index=False)
        except Exception:
            pass
        return ""

    def _metadata_attr(self, meta, key: str, default=None):
        if meta is None:
            return default
        if isinstance(meta, dict):
            return meta.get(key, default)
        return getattr(meta, key, default)

    def _shared_element_metadata(
        self,
        *,
        meta,
        element_type: str,
        page_num: int,
        bbox_json: str = "",
    ):
        extraction_source = str(
            self._metadata_attr(meta, "extraction_source")
            or self._metadata_attr(meta, "source_weight_key")
            or "unstructured_fast"
        ).strip()
        source_weight_key = str(
            self._metadata_attr(meta, "source_weight_key")
            or extraction_source
            or "unstructured_fast"
        ).strip()

        return {
            "element_type": str(element_type or "NarrativeText").strip() or "NarrativeText",
            "extraction_backend": str(self._metadata_attr(meta, "extraction_backend") or "").strip(),
            "extraction_source": extraction_source or "unstructured_fast",
            "source_weight_key": source_weight_key or "unstructured_fast",
            "ocr_used": bool(self._metadata_attr(meta, "ocr_used", False)),
            "page_number": page_num,
            "bbox": bbox_json,
        }

    def _public_chunk_type(self, internal_type: str) -> str:
        normalized = str(internal_type or "text").strip().lower()
        if normalized == "parent":
            return "table"
        if normalized == "child":
            return "table_row"
        return "text"

    def _debug_chunk(self, doc: Document) -> None:
        metadata = doc.metadata or {}
        internal_type = str(metadata.get("type") or "text").strip().lower() or "text"
        public_type = self._public_chunk_type(internal_type)
        section = str(metadata.get("section") or DEFAULT_SECTION).strip() or DEFAULT_SECTION
        page_num = int(metadata.get("page_number") or 1)
        word_count = _word_count(doc.page_content)
        char_count = len(str(doc.page_content or ""))
        overlap_words = int(metadata.get("semantic_overlap_words") or 0)
        protected_sentence_count = int(metadata.get("protected_sentence_count") or 0)
        preview = _content_preview(doc.page_content)
        print(
            "[CHUNK] "
            f"type={internal_type} public={public_type} "
            f"page={page_num} section={section!r} "
            f"words={word_count} chars={char_count} "
            f"overlap_words={overlap_words} protected_sentences={protected_sentence_count} "
            f"preview={preview!r}"
        )

    def _normalize_section_label(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _is_valid_section_label(self, value: Any) -> bool:
        label = self._normalize_section_label(value)
        if not label:
            return False
        lowered = label.lower()
        if lowered in _WEAK_SECTION_LABELS:
            return False
        if _PAGE_MARKER_RE.match(label) or _MAP_TO_PAGE_RE.match(label):
            return False
        return True

    def _resolve_effective_section(self, *, meta, page_num: int) -> str:
        explicit_section = self._normalize_section_label(self._metadata_attr(meta, "section", ""))
        if self._is_valid_section_label(explicit_section):
            self.last_stable_section_by_page[int(page_num)] = explicit_section
            return explicit_section

        if self._is_valid_section_label(self.current_title_section):
            return self.current_title_section

        page_section = self.last_stable_section_by_page.get(int(page_num), "")
        if self._is_valid_section_label(page_section):
            return page_section

        return DEFAULT_SECTION

    def _is_noise_line(self, line: str) -> bool:
        normalized = self._normalize_section_label(line)
        if not normalized:
            return False
        if normalized.lower() == "text":
            return True
        if _PAGE_MARKER_RE.match(normalized):
            return True
        if _MAP_TO_PAGE_RE.match(normalized):
            return True
        return False

    def _normalize_line_text(self, raw_line: str) -> str:
        text = str(raw_line or "")
        text = text.replace("\x00", " ").replace("â€¢", "- ")
        text = text.replace("â€¢", "- ")
        text = text.replace("\r", "")
        text = _BULLET_PREFIX_RE.sub("- ", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _extract_title_block_metadata(self, line: str) -> Tuple[Dict[str, str], bool]:
        normalized = self._normalize_section_label(line)
        if not normalized:
            return {}, False

        for key, pattern in _TITLE_BLOCK_FIELD_PATTERNS:
            match = pattern.match(normalized)
            if not match:
                continue
            value = self._normalize_section_label(match.group(1))
            if not value:
                return {}, True
            return {key: value}, True

        if _STANDALONE_DOCUMENT_NUMBER_RE.match(normalized) and any(ch.isdigit() for ch in normalized):
            return {"document_number": normalized}, True

        return {}, False

    def _merge_structured_metadata(self, target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
        if not incoming:
            return
        for key, value in incoming.items():
            normalized_value = self._normalize_section_label(value)
            if not normalized_value:
                continue
            if not target.get(key):
                target[key] = normalized_value

    def _clean_lines(self, raw_text: str) -> Tuple[List[str], Dict[str, Any]]:
        raw = str(raw_text or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
        cleaned_lines: List[str] = []
        extracted_metadata: Dict[str, Any] = {}

        for raw_line in raw.split("\n"):
            line = self._normalize_line_text(raw_line)
            if not line:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                continue
            if self._is_noise_line(line):
                continue

            extracted_line_metadata, is_title_block = self._extract_title_block_metadata(line)
            if is_title_block:
                self._merge_structured_metadata(extracted_metadata, extracted_line_metadata)
                continue

            if cleaned_lines and line == cleaned_lines[-1]:
                continue

            cleaned_lines.append(line)

        while cleaned_lines and cleaned_lines[0] == "":
            cleaned_lines.pop(0)
        while cleaned_lines and cleaned_lines[-1] == "":
            cleaned_lines.pop()

        return cleaned_lines, extracted_metadata

    def _looks_like_list_line(self, line: str) -> bool:
        return bool(_LIST_ITEM_RE.match(str(line or "")))

    def _looks_like_table_row_line(self, line: str) -> bool:
        text = str(line or "")
        if text.count("|") >= 2:
            return True
        return bool(_TABLE_ROW_RE.search(text) and len(text.split("|")) >= 3)

    def _should_merge_ocr_lines(self, previous_line: str, current_line: str) -> bool:
        previous = str(previous_line or "").rstrip()
        current = str(current_line or "").lstrip()

        if not previous or not current:
            return False
        if self._looks_like_list_line(current) or self._looks_like_table_row_line(current):
            return False
        if previous.endswith(":"):
            return False
        if previous.endswith((".", "!", "?", ";")):
            return False
        if previous.endswith("-") and re.search(r"[A-Za-z]-$", previous) and re.match(r"^[A-Za-z]", current):
            return True
        if current[:1].islower():
            return True
        if current.startswith(("(", "[", "%", ",", ".", ";", ":", ")")):
            return True
        if len(previous) < 100:
            return True
        return False

    def _merge_text_fragments(self, previous_text: str, current_text: str) -> str:
        previous = str(previous_text or "").rstrip()
        current = str(current_text or "").lstrip()
        if previous.endswith("-") and re.search(r"[A-Za-z]-$", previous) and re.match(r"^[A-Za-z]", current):
            return f"{previous[:-1]}{current}"
        return f"{previous} {current}".strip()

    def _prepare_text_blocks(self, *, raw_text: str, category: str) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        lines, extracted_metadata = self._clean_lines(raw_text)
        if not lines:
            return [], extracted_metadata

        if category == "ListItem":
            return (
                [{"kind": "list", "text": line} for line in lines if line],
                extracted_metadata,
            )

        blocks: List[Dict[str, str]] = []
        current_paragraph = ""

        for line in lines:
            if not line:
                if current_paragraph:
                    blocks.append({"kind": "paragraph", "text": current_paragraph})
                    current_paragraph = ""
                continue

            if self._looks_like_list_line(line):
                if current_paragraph:
                    blocks.append({"kind": "paragraph", "text": current_paragraph})
                    current_paragraph = ""
                blocks.append({"kind": "list", "text": line})
                continue

            if self._looks_like_table_row_line(line):
                if current_paragraph:
                    blocks.append({"kind": "paragraph", "text": current_paragraph})
                    current_paragraph = ""
                blocks.append({"kind": "paragraph", "text": line})
                continue

            if current_paragraph and self._should_merge_ocr_lines(current_paragraph, line):
                current_paragraph = self._merge_text_fragments(current_paragraph, line)
            else:
                if current_paragraph:
                    blocks.append({"kind": "paragraph", "text": current_paragraph})
                current_paragraph = line

        if current_paragraph:
            blocks.append({"kind": "paragraph", "text": current_paragraph})

        return [block for block in blocks if str(block.get("text") or "").strip()], extracted_metadata

    def _reset_text_group(self) -> None:
        self.current_text_group: List[Dict[str, str]] = []
        self.current_group_section = DEFAULT_SECTION
        self.current_group_page = 1
        self.current_group_meta = None
        self.current_group_bbox_json = ""
        self.current_group_element_types: List[str] = []
        self.current_group_heading = ""
        self.current_group_structured_metadata: Dict[str, Any] = {}

    def _resolve_group_element_type(self) -> str:
        if not self.current_group_element_types:
            return "NarrativeText"

        priority_order = {
            "Title": 5,
            "NarrativeText": 4,
            "ListItem": 3,
            "Table": 2,
            "UncategorizedText": 1,
        }

        counts: Dict[str, int] = {}
        for element_type in self.current_group_element_types:
            normalized = str(element_type or "NarrativeText").strip() or "NarrativeText"
            counts[normalized] = counts.get(normalized, 0) + 1

        return max(
            counts.keys(),
            key=lambda key: (counts.get(key, 0), priority_order.get(key, 0)),
        )

    def _split_markdown_row(self, row: str):
        stripped = str(row or "").strip()
        if not stripped:
            return []
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    def _is_markdown_separator_row(self, cells):
        if not cells:
            return False
        for cell in cells:
            token = str(cell or "").replace(":", "").replace("-", "").strip()
            if token:
                return False
        return True

    def _build_table_child_documents(
        self,
        *,
        markdown: str,
        parent_id: str,
        page_num: int,
        bbox_json: str,
        section: str,
        meta=None,
    ):
        """
        Child rows stay optional. We only emit them when the table structure is
        parseable so retrieval gets header-aware row context instead of blindly
        split markdown lines.
        """
        rows = [row for row in str(markdown or "").splitlines() if row.strip()]
        if len(rows) < 3:
            return []

        header_cells = self._split_markdown_row(rows[0])
        separator_cells = self._split_markdown_row(rows[1])
        if not header_cells or len(header_cells) < 2 or not self._is_markdown_separator_row(separator_cells):
            return []

        child_docs = []
        for row_index, row in enumerate(rows[2:], start=1):
            value_cells = self._split_markdown_row(row)
            if not value_cells or len(value_cells) != len(header_cells):
                continue

            row_pairs = [
                f"{header}: {value}"
                for header, value in zip(header_cells, value_cells)
                if str(header or "").strip() or str(value or "").strip()
            ]
            if not row_pairs:
                continue

            child_docs.append(
                Document(
                    page_content=(
                        f"### Table Row: {section}\n"
                        f"Table ID: {parent_id}\n"
                        f"Headers: {' | '.join(header_cells)}\n"
                        "Row Values:\n"
                        f"{chr(10).join(row_pairs)}\n"
                        f"Original Row: {row.strip()}"
                    ),
                    metadata={
                        "type": "child",
                        "section": section,
                        "parent_id": parent_id,
                        "doc_id": parent_id,
                        "is_parent": False,
                        "table_row_index": row_index,
                        **self._shared_element_metadata(
                            meta=meta,
                            element_type="Table",
                            page_num=page_num,
                            bbox_json=bbox_json,
                        ),
                    }
                )
            )

        return child_docs

    def _build_table_child_documents_from_normalized(
        self,
        *,
        normalized_table: dict,
        parent_id: str,
        page_num: int,
        bbox_json: str,
        section: str,
        meta=None,
    ):
        cells = normalized_table.get("cells") if isinstance(normalized_table, dict) else []
        if not isinstance(cells, list) or not cells:
            return []

        grouped_rows = {}
        row_order = []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            try:
                row_index = int(cell.get("row_index"))
            except Exception:
                continue
            if row_index not in grouped_rows:
                grouped_rows[row_index] = []
                row_order.append(row_index)
            grouped_rows[row_index].append(
                {
                    "column_path": list(cell.get("column_path") or []),
                    "value": str(cell.get("value") or ""),
                }
            )

        child_docs = []
        for row_index in row_order:
            row_cells = grouped_rows.get(row_index) or []
            if not row_cells:
                continue

            row_pairs = []
            for cell in row_cells:
                column_path = list(cell.get("column_path") or [])
                value = str(cell.get("value") or "")
                path_json = json.dumps(column_path, ensure_ascii=False)
                row_pairs.append(f"Column Path {path_json}: {value}")

            child_docs.append(
                Document(
                    page_content=(
                        f"### Table Row: {section}\n"
                        f"Table ID: {parent_id}\n"
                        f"Row Index: {row_index}\n"
                        "Row Values:\n"
                        f"{chr(10).join(row_pairs)}"
                    ),
                    metadata={
                        "type": "child",
                        "section": section,
                        "parent_id": parent_id,
                        "doc_id": parent_id,
                        "is_parent": False,
                        "table_row_index": row_index,
                        **self._shared_element_metadata(
                            meta=meta,
                            element_type="Table",
                            page_num=page_num,
                            bbox_json=bbox_json,
                        ),
                    }
                )
            )

        return child_docs

    # --------------------------------------------------------
    # STRUCTURE-AWARE TEXT GROUPING / CHUNKING
    # --------------------------------------------------------

    def _should_merge_paragraph_blocks(self, previous_text: str, current_text: str) -> bool:
        previous = str(previous_text or "").rstrip()
        current = str(current_text or "").lstrip()
        if not previous or not current:
            return False
        if previous.endswith("-") and re.search(r"[A-Za-z]-$", previous) and re.match(r"^[A-Za-z]", current):
            return True
        if previous.endswith((".", "!", "?", ";", ":")):
            return False
        if current[:1].islower():
            return True
        return len(previous) < 80

    def _split_sentences(self, text: str) -> List[str]:
        segments = self._split_text_with_pattern(text, _SENTENCE_BOUNDARY_RE)
        if segments:
            return segments
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        return [normalized] if normalized else []

    def _is_protected_sentence(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False

        if _PROTECTED_UNIT_RE.search(normalized):
            return True

        lowered = normalized.lower()
        return any(term in lowered for term in _PROTECTED_ENTITY_TERMS) or any(
            term in lowered for term in _PROTECTED_PROCESS_TERMS
        )

    def _count_protected_sentences(self, text: str) -> int:
        return sum(1 for sentence in self._split_sentences(text) if self._is_protected_sentence(sentence))

    def _make_unit(self, text: str, *, kind: str) -> Dict[str, Any]:
        normalized = str(text or "").strip()
        return {
            "text": normalized,
            "kind": kind,
            "word_count": _word_count(normalized),
            "protected_sentence_count": self._count_protected_sentences(normalized),
        }

    def _join_unit_text(self, units: List[Dict[str, Any]]) -> str:
        return "\n\n".join(
            str(item.get("text") or "").strip()
            for item in units
            if str(item.get("text") or "").strip()
        )

    def _with_optional_heading(self, text: str, *, heading: str, include_heading: bool) -> str:
        cleaned = str(text or "").strip()
        normalized_heading = self._normalize_section_label(heading)
        if not include_heading or not normalized_heading:
            return cleaned
        if cleaned.startswith(f"### {normalized_heading}"):
            return cleaned
        return f"### {normalized_heading}\n\n{cleaned}".strip()

    def _append_group_block(self, block: Dict[str, str]) -> None:
        text = str(block.get("text") or "").strip()
        if not text:
            return

        kind = str(block.get("kind") or "paragraph").strip() or "paragraph"
        if not self.current_text_group:
            self.current_text_group.append({"kind": kind, "text": text})
            return

        previous = self.current_text_group[-1]
        if kind == "list" and previous.get("kind") == "list":
            previous["text"] = f"{previous['text']}\n{text}".strip()
            return

        if (
            kind == "paragraph"
            and previous.get("kind") == "paragraph"
            and self._should_merge_paragraph_blocks(previous.get("text", ""), text)
        ):
            previous["text"] = self._merge_text_fragments(previous.get("text", ""), text)
            return

        self.current_text_group.append({"kind": kind, "text": text})

    def _add_text_element(
        self,
        *,
        category: str,
        text: str,
        meta,
        page_num: int,
        bbox_json: str,
        docs_list: List[Document],
    ) -> None:
        blocks, extracted_metadata = self._prepare_text_blocks(raw_text=text, category=category)
        page_num = int(page_num or 1)
        if extracted_metadata:
            page_structured_metadata = self.pending_structured_metadata_by_page.setdefault(page_num, {})
            self._merge_structured_metadata(page_structured_metadata, extracted_metadata)

        if not blocks:
            if self.current_text_group and page_num == self.current_group_page:
                self._merge_structured_metadata(
                    self.current_group_structured_metadata,
                    self.pending_structured_metadata_by_page.get(page_num, {}),
                )
            return

        effective_section = self._resolve_effective_section(meta=meta, page_num=page_num)

        if self.current_text_group and (
            page_num != self.current_group_page
            or effective_section != self.current_group_section
        ):
            self._flush_text_group(docs_list)

        if not self.current_text_group:
            self.current_group_section = effective_section
            self.current_group_page = page_num
            self.current_group_meta = meta
            self.current_group_bbox_json = bbox_json
            self.current_group_element_types = []
            if (
                self._is_valid_section_label(self.pending_title_heading)
                and self._normalize_section_label(self.pending_title_heading) == effective_section
            ):
                self.current_group_heading = self._normalize_section_label(self.pending_title_heading)
                self.pending_title_heading = ""
            self._merge_structured_metadata(
                self.current_group_structured_metadata,
                self.pending_structured_metadata_by_page.pop(page_num, {}),
            )
        elif not self.current_group_bbox_json and bbox_json:
            self.current_group_bbox_json = bbox_json

        self.current_group_element_types.append(category)
        self.last_stable_section_by_page[page_num] = effective_section
        self._merge_structured_metadata(
            self.current_group_structured_metadata,
            self.pending_structured_metadata_by_page.get(page_num, {}),
        )

        for block in blocks:
            self._append_group_block(block)

    def _split_text_with_pattern(self, text: str, pattern: re.Pattern[str]) -> List[str]:
        return [part.strip() for part in pattern.split(str(text or "").strip()) if part.strip()]

    def _split_on_words(self, text: str) -> List[str]:
        words = str(text or "").split()
        if not words:
            return []

        pieces: List[str] = []
        current_words: List[str] = []
        for word in words:
            if current_words and len(current_words) >= self.max_chunk_words:
                pieces.append(" ".join(current_words).strip())
                current_words = [word]
            else:
                current_words.append(word)
        if current_words:
            pieces.append(" ".join(current_words).strip())
        return pieces

    def _split_large_paragraph(self, text: str, *, level: int = 0) -> List[str]:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return []
        if _word_count(normalized) <= self.max_chunk_words:
            return [normalized]

        if level == 0:
            segments = self._split_sentences(normalized)
        elif level == 1:
            segments = self._split_text_with_pattern(normalized, _CLAUSE_BOUNDARY_RE)
        else:
            return self._split_on_words(normalized)

        if len(segments) <= 1:
            if level >= 2:
                return [normalized]
            return self._split_large_paragraph(normalized, level=level + 1)

        pieces: List[str] = []
        current_parts: List[str] = []
        current_word_total = 0

        for segment in segments:
            segment = re.sub(r"\s+", " ", str(segment or "")).strip()
            if not segment:
                continue
            segment_words = _word_count(segment)
            is_protected = level == 0 and self._is_protected_sentence(segment)
            if segment_words > self.max_chunk_words:
                if current_parts:
                    pieces.append(" ".join(current_parts).strip())
                    current_parts = []
                    current_word_total = 0
                if is_protected:
                    pieces.append(segment)
                else:
                    pieces.extend(self._split_large_paragraph(segment, level=min(level + 1, 2)))
                continue

            if current_parts and current_word_total >= self.min_chunk_words and current_word_total + segment_words > self.max_chunk_words:
                pieces.append(" ".join(current_parts).strip())
                current_parts = [segment]
                current_word_total = segment_words
            elif current_parts and current_word_total + segment_words > self.max_chunk_words:
                pieces.append(" ".join(current_parts).strip())
                current_parts = [segment]
                current_word_total = segment_words
            else:
                current_parts.append(segment)
                current_word_total += segment_words

        if current_parts:
            pieces.append(" ".join(current_parts).strip())

        return [piece for piece in pieces if piece]

    def _expand_large_blocks(self, blocks: List[Dict[str, str]]) -> List[Dict[str, str]]:
        expanded: List[Dict[str, str]] = []
        for block in blocks:
            kind = str(block.get("kind") or "paragraph").strip() or "paragraph"
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            if kind == "paragraph" and _word_count(text) > self.max_chunk_words:
                for piece in self._split_large_paragraph(text):
                    expanded.append({"kind": "sentence_group", "text": piece})
                continue
            expanded.append({"kind": kind, "text": text})
        return expanded

    def _build_semantic_units(self, blocks: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        units: List[Dict[str, Any]] = []
        index = 0
        while index < len(blocks):
            block = blocks[index]
            kind = str(block.get("kind") or "paragraph").strip() or "paragraph"
            text = str(block.get("text") or "").strip()
            if not text:
                index += 1
                continue

            if kind == "paragraph" and index + 1 < len(blocks):
                next_block = blocks[index + 1]
                next_kind = str(next_block.get("kind") or "paragraph").strip() or "paragraph"
                next_text = str(next_block.get("text") or "").strip()
                if next_kind == "list" and next_text:
                    combined = f"{text}\n{next_text}".strip()
                    units.append(self._make_unit(combined, kind="paragraph_list"))
                    index += 2
                    continue

            units.append(self._make_unit(text, kind=kind))
            index += 1

        return units

    def _select_overlap_units(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not units:
            return []

        target_overlap_words = max(1, int(round(self.target_chunk_words * self.overlap_ratio)))
        overlap_units: List[Dict[str, Any]] = []
        accumulated_words = 0
        remaining_units = list(units)

        tail_unit = units[-1]
        tail_words = int(tail_unit.get("word_count") or 0)
        if int(tail_unit.get("protected_sentence_count") or 0) > 0 and tail_words > 0:
            overlap_units = [tail_unit]
            accumulated_words = tail_words
            remaining_units = units[:-1]
            if accumulated_words >= target_overlap_words:
                return overlap_units

        for unit in reversed(remaining_units):
            unit_words = int(unit.get("word_count") or 0)
            if unit_words <= 0:
                continue
            if unit_words > self.target_chunk_words and overlap_units:
                break
            if (
                unit_words > self.max_chunk_words // 2
                and not overlap_units
                and int(unit.get("protected_sentence_count") or 0) <= 0
            ):
                continue
            overlap_units.insert(0, unit)
            accumulated_words += unit_words
            if accumulated_words >= target_overlap_words:
                break

        return overlap_units

    def _chunk_semantic_units(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not units:
            return []

        chunk_entries: List[Dict[str, Any]] = []
        current_units: List[Dict[str, Any]] = []
        current_word_total = 0
        current_has_overlap = False
        current_overlap_words = 0
        index = 0

        while index < len(units):
            unit = units[index]
            unit_words = int(unit.get("word_count") or 0)
            if not current_units:
                current_units = [unit]
                current_word_total = unit_words
                current_has_overlap = False
                current_overlap_words = 0
                index += 1
                continue

            if current_word_total < self.min_chunk_words:
                current_units.append(unit)
                current_word_total += unit_words
                index += 1
                continue

            if current_word_total + unit_words <= self.max_chunk_words:
                current_units.append(unit)
                current_word_total += unit_words
                index += 1
                continue

            chunk_text = self._join_unit_text(current_units)
            if chunk_text:
                chunk_entries.append(
                    {
                        "text": chunk_text,
                        "has_semantic_overlap": current_has_overlap,
                        "semantic_overlap_words": int(current_overlap_words or 0),
                        "protected_sentence_count": sum(
                            int(item.get("protected_sentence_count") or 0)
                            for item in current_units
                        ),
                    }
                )
            overlap_units = self._select_overlap_units(current_units)
            overlap_word_total = sum(int(item.get("word_count") or 0) for item in overlap_units)

            if overlap_units and overlap_word_total + unit_words <= self.max_chunk_words:
                current_units = list(overlap_units)
                current_word_total = overlap_word_total
                current_has_overlap = True
                current_overlap_words = overlap_word_total
            else:
                current_units = []
                current_word_total = 0
                current_has_overlap = False
                current_overlap_words = 0

        if current_units:
            chunk_text = self._join_unit_text(current_units)
            if chunk_text:
                chunk_entries.append(
                    {
                        "text": chunk_text,
                        "has_semantic_overlap": current_has_overlap,
                        "semantic_overlap_words": int(current_overlap_words or 0),
                        "protected_sentence_count": sum(
                            int(item.get("protected_sentence_count") or 0)
                            for item in current_units
                        ),
                    }
                )

        return chunk_entries

    def _flush_text_group(self, docs_list: List[Document]) -> None:
        if not self.current_text_group:
            self._reset_text_group()
            return

        expanded_blocks = self._expand_large_blocks(self.current_text_group)
        semantic_units = self._build_semantic_units(expanded_blocks)
        chunk_entries = self._chunk_semantic_units(semantic_units)
        resolved_element_type = self._resolve_group_element_type()

        for chunk_index, chunk_entry in enumerate(chunk_entries):
            chunk_text = self._with_optional_heading(
                str(chunk_entry.get("text") or "").strip(),
                heading=self.current_group_heading,
                include_heading=chunk_index == 0,
            )
            doc = Document(
                page_content=chunk_text,
                metadata={
                    "type": "text",
                    "section": self.current_group_section,
                    "is_parent": False,
                    "chunk_index": chunk_index,
                    "has_semantic_overlap": bool(chunk_entry.get("has_semantic_overlap", False)),
                    "semantic_overlap_words": int(chunk_entry.get("semantic_overlap_words") or 0),
                    "protected_sentence_count": int(chunk_entry.get("protected_sentence_count") or 0),
                    **self._shared_element_metadata(
                        meta=self.current_group_meta,
                        element_type=resolved_element_type,
                        page_num=self.current_group_page,
                        bbox_json=self.current_group_bbox_json,
                    ),
                    **dict(self.current_group_structured_metadata or {}),
                },
            )
            docs_list.append(doc)
            self._debug_chunk(doc)

        self._reset_text_group()

    # --------------------------------------------------------
    # MAIN PROCESSOR
    # --------------------------------------------------------

    def process(self, input_file: str, output_file: str):
        print(f"[CHUNK] Loading filtered elements from: {input_file}")
        elements = elements_from_json(filename=input_file)

        final_documents: List[Document] = []
        print("[CHUNK] Processing elements with structure-aware chunking...")

        for element in elements:
            category = str(getattr(element, "category", "") or "").strip() or "NarrativeText"
            raw_text = element.text or ""
            text = raw_text if category == "Table" else normalize_numbers(raw_text)

            meta = getattr(element, "metadata", None)
            try:
                page_num = int(self._metadata_attr(meta, "page_number", 1) or 1)
            except Exception:
                page_num = 1

            bbox_json = ""
            coordinates = self._metadata_attr(meta, "coordinates", None)
            if coordinates:
                try:
                    points = list(coordinates.points)
                    bbox_json = json.dumps(points)
                except Exception:
                    bbox_json = ""

            # ------------------------------------------------
            # 1️⃣ SECTION TITLES
            # ------------------------------------------------
            if category == "Title":
                title_text = self._normalize_section_label(text)
                if self._is_valid_section_label(title_text):
                    self._flush_text_group(final_documents)
                    self.current_title_section = title_text
                    self.pending_title_heading = title_text
                    self.last_stable_section_by_page[page_num] = title_text
                continue

            # ------------------------------------------------
            # 2️⃣ TABLES (PARENT-CHILD LOGIC)
            # ------------------------------------------------
            if category == "Table":
                self._flush_text_group(final_documents)

                section = self._resolve_effective_section(meta=meta, page_num=page_num)
                if (
                    self._is_valid_section_label(self.pending_title_heading)
                    and self._normalize_section_label(self.pending_title_heading) == section
                ):
                    self.pending_title_heading = ""
                html = str(self._metadata_attr(meta, "text_as_html", "") or "")
                normalized_table = self._metadata_attr(meta, "normalized_table", None)
                normalized_table_is_primary = bool(self._metadata_attr(meta, "normalized_table_is_primary", False))
                normalized_table_merged_into = str(
                    self._metadata_attr(meta, "normalized_table_merged_into", "") or ""
                ).strip()
                if normalized_table_merged_into and not normalized_table_is_primary:
                    continue
                markdown = self.html_to_markdown(html) if html else ""
                normalized_text = normalized_table_to_text(
                    normalized_table if isinstance(normalized_table, dict) else {}
                )
                if normalized_text:
                    markdown = normalized_text
                if not (markdown or "").strip():
                    markdown = text
                
                parent_id = str(uuid.uuid4())

                parent_doc = Document(
                    page_content=(
                        f"### Table: {section}\n"
                        "Table Structure:\n"
                        f"{markdown}"
                    ),
                    metadata={
                        "type": "parent",
                        "section": section,
                        "doc_id": parent_id,
                        "is_parent": True,
                        **self._shared_element_metadata(
                            meta=meta,
                            element_type="Table",
                            page_num=page_num,
                            bbox_json=bbox_json,
                        ),
                    }
                )
                final_documents.append(parent_doc)
                self._debug_chunk(parent_doc)

                normalized_children = self._build_table_child_documents_from_normalized(
                    normalized_table=normalized_table if isinstance(normalized_table, dict) else {},
                    parent_id=parent_id,
                    page_num=page_num,
                    bbox_json=bbox_json,
                    section=section,
                    meta=meta,
                )
                if normalized_children:
                    for child_doc in normalized_children:
                        final_documents.append(child_doc)
                        self._debug_chunk(child_doc)
                else:
                    for child_doc in self._build_table_child_documents(
                        markdown=markdown,
                        parent_id=parent_id,
                        page_num=page_num,
                        bbox_json=bbox_json,
                        section=section,
                        meta=meta,
                    ):
                        final_documents.append(child_doc)
                        self._debug_chunk(child_doc)
                continue

            # ------------------------------------------------
            # 3️⃣ NARRATIVE / LIST TEXT
            # ------------------------------------------------
            if category in ("NarrativeText", "UncategorizedText", "ListItem"):
                self._add_text_element(
                    category=category,
                    text=text,
                    meta=meta,
                    page_num=page_num,
                    bbox_json=bbox_json,
                    docs_list=final_documents,
                )

        self._flush_text_group(final_documents)

        print(f"[CHUNK] Created {len(final_documents)} chunks (parents + children + text).")

        output_data = []
        for doc in final_documents:
            metadata = dict(doc.metadata or {})
            output_data.append(
                {
                    "content": doc.page_content,
                    "metadata": metadata,
                    "section": str(metadata.get("section") or DEFAULT_SECTION),
                    "page_number": int(metadata.get("page_number") or 1),
                    "chunk_type": self._public_chunk_type(metadata.get("type") or "text"),
                }
            )

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"[CHUNK] Saved chunks to: {output_file}")


# ============================================================
# CLI ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    # Usage:
    # python chunk.py <filtered_elements.json> <chunks.json>

    if len(sys.argv) != 3:
        print(" Usage: python chunk.py <filtered_elements.json> <chunks.json>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    chunker = ContextAwareChunker()
    chunker.process(input_file, output_file)
    print(" Chunking completed.")
