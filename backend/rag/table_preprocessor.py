from __future__ import annotations

import os
import json
import re
import uuid
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional


_TABLE_CAPTION_RE = re.compile(r"^\s*(?:table|tab\.?)\b", re.IGNORECASE)
_CONTINUATION_RE = re.compile(r"\b(?:continued|cont\.?|contd\.?)\b", re.IGNORECASE)
_TABLE_NOISE_RE = re.compile(
    r"Sheet of Sheets|Revision Number|Validity Status|CD-FE|Page \d+",
    re.IGNORECASE,
)
_MARKDOWN_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


class TablePreprocessor:
    """
    Docling-backed table extractor that can emit:
    - page-batched element dictionaries compatible with the existing RAG pipeline
    - a consolidated markdown artifact for manual inspection / offline workflows

    The Docling dependency is imported lazily so the rest of the backend can
    still import this module in environments where Docling is not installed.
    """

    def __init__(
        self,
        output_dir: str | os.PathLike[str] = "rag_processed_tables",
        *,
        enable_ocr: bool = True,
        enable_table_structure: bool = True,
        enable_cell_matching: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.enable_ocr = bool(enable_ocr)
        self.enable_table_structure = bool(enable_table_structure)
        self.enable_cell_matching = bool(enable_cell_matching)
        self._converter: Any = None
        self._doc_item_label: Any = None

    def _triage_page(self, pdf_path: str) -> Dict[str, Any]:
        return {
            "pdf_path": str(pdf_path),
            "type": "hybrid",
            "needs_ocr": bool(self.enable_ocr),
            "table_structure": bool(self.enable_table_structure),
        }

    def _route_table_roi(self, page_analysis: Dict[str, Any], table_bbox: List[float]) -> str:
        del page_analysis, table_bbox
        return "docling"

    def _ensure_converter(self) -> None:
        if self._converter is not None:
            return

        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling_core.types.doc.labels import DocItemLabel
        except Exception as exc:
            raise RuntimeError(
                "TablePreprocessor requires the optional dependencies 'docling' "
                "and 'docling_core'. Install them into the active environment first."
            ) from exc

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = self.enable_table_structure
        pipeline_options.do_ocr = self.enable_ocr

        table_options = getattr(pipeline_options, "table_structure_options", None)
        if table_options is not None and hasattr(table_options, "do_cell_matching"):
            table_options.do_cell_matching = self.enable_cell_matching

        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
        self._doc_item_label = DocItemLabel

    def _convert_document(self, pdf_path: str) -> Any:
        self._ensure_converter()
        source = Path(pdf_path)
        if not source.exists():
            raise FileNotFoundError(f"File not found: {source}")
        return self._converter.convert(str(source))

    def _safe_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _label_name(self, element: Any) -> str:
        label = getattr(element, "label", None)
        if hasattr(label, "name"):
            return str(label.name or "").strip().upper()
        return str(label or "").strip().upper()

    def _looks_like_table_caption(self, text: str) -> bool:
        clean = self._safe_text(text)
        if not clean:
            return False
        return bool(_TABLE_CAPTION_RE.match(clean) or _CONTINUATION_RE.search(clean))

    def _extract_caption_text(self, element: Any) -> str:
        caption = getattr(element, "caption", None)
        if caption is None:
            return ""
        if isinstance(caption, str):
            return caption.strip()
        return self._safe_text(getattr(caption, "text", "") or caption)

    def _extract_page_number(self, element: Any, *, default: int = 1) -> int:
        direct_candidates = (
            "page_no",
            "page_number",
            "page",
            "page_idx",
            "page_index",
        )
        for attr in direct_candidates:
            value = getattr(element, attr, None)
            try:
                page_number = int(value)
            except Exception:
                continue
            if page_number > 0:
                return page_number

        prov = getattr(element, "prov", None)
        if isinstance(prov, (list, tuple)):
            for item in prov:
                page_number = self._extract_page_number(item, default=0)
                if page_number > 0:
                    return page_number
        elif prov is not None and prov is not element:
            page_number = self._extract_page_number(prov, default=0)
            if page_number > 0:
                return page_number

        bbox = getattr(element, "bbox", None)
        if bbox is not None and bbox is not element:
            for attr in ("page_no", "page_number", "page"):
                value = getattr(bbox, attr, None)
                try:
                    page_number = int(value)
                except Exception:
                    continue
                if page_number > 0:
                    return page_number

        return max(int(default or 1), 1)

    def _bbox_points_from_value(self, value: Any) -> List[List[float]]:
        if value is None:
            return []

        if hasattr(value, "to_list"):
            try:
                value = value.to_list()
            except Exception:
                value = None

        if isinstance(value, dict):
            keys = ("x0", "y0", "x1", "y1")
            if all(key in value for key in keys):
                value = [value["x0"], value["y0"], value["x1"], value["y1"]]
            else:
                keys = ("l", "t", "r", "b")
                if all(key in value for key in keys):
                    value = [value["l"], value["t"], value["r"], value["b"]]

        if isinstance(value, (list, tuple)) and len(value) == 4:
            try:
                x0, y0, x1, y1 = [float(part) for part in value]
            except Exception:
                return []
            return [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
            ]

        attrs = (
            ("x0", "y0", "x1", "y1"),
            ("l", "t", "r", "b"),
            ("left", "top", "right", "bottom"),
        )
        for x0_key, y0_key, x1_key, y1_key in attrs:
            if not all(hasattr(value, key) for key in (x0_key, y0_key, x1_key, y1_key)):
                continue
            try:
                x0 = float(getattr(value, x0_key))
                y0 = float(getattr(value, y0_key))
                x1 = float(getattr(value, x1_key))
                y1 = float(getattr(value, y1_key))
            except Exception:
                continue
            return [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
            ]

        return []

    def _extract_bbox_payload(self, element: Any) -> Optional[Dict[str, Any]]:
        for candidate in (
            getattr(element, "bbox", None),
            getattr(getattr(element, "prov", None), "bbox", None),
        ):
            points = self._bbox_points_from_value(candidate)
            if points:
                return {"points": points}
        return None

    def _call_export(self, element: Any, method_name: str, *, document: Any) -> str:
        method = getattr(element, method_name, None)
        if method is None:
            return ""

        call_patterns = (
            {"doc": document},
            {"document": document},
            {},
        )
        for kwargs in call_patterns:
            try:
                exported = method(**kwargs)
            except TypeError:
                continue
            except Exception:
                return ""
            if exported is None:
                continue
            return str(exported).strip()
        return ""

    def _split_markdown_row(self, line: str) -> List[str]:
        text = str(line or "").strip()
        if not text:
            return []
        if text.startswith("|"):
            text = text[1:]
        if text.endswith("|"):
            text = text[:-1]
        return [cell.strip() for cell in text.split("|")]

    def _is_markdown_separator_row(self, cells: Iterable[str]) -> bool:
        parsed = [str(cell or "").strip() for cell in cells]
        if not parsed:
            return False
        return all(_MARKDOWN_SEPARATOR_CELL_RE.fullmatch(cell or "") for cell in parsed)

    def _wrap_html_caption(self, html_text: str, caption: str) -> str:
        clean_html = str(html_text or "").strip()
        clean_caption = self._safe_text(caption)
        if not clean_html or not clean_caption:
            return clean_html
        if "<caption" in clean_html.lower():
            return clean_html

        table_match = re.search(r"<table\b[^>]*>", clean_html, flags=re.IGNORECASE)
        if not table_match:
            return clean_html

        insert_at = table_match.end()
        return (
            f"{clean_html[:insert_at]}"
            f"<caption>{escape(clean_caption)}</caption>"
            f"{clean_html[insert_at:]}"
        )

    def _markdown_table_to_html(self, table_markdown: str, *, caption: str = "") -> str:
        lines = [line.strip() for line in str(table_markdown or "").splitlines() if line.strip()]
        if len(lines) < 2:
            return ""

        header = self._split_markdown_row(lines[0])
        separator = self._split_markdown_row(lines[1])
        if not header or not self._is_markdown_separator_row(separator):
            return ""

        body_rows = [
            self._split_markdown_row(line)
            for line in lines[2:]
            if self._split_markdown_row(line)
        ]

        expected_columns = len(header)
        normalized_rows: List[List[str]] = []
        for row in body_rows:
            if len(row) < expected_columns:
                row = row + [""] * (expected_columns - len(row))
            elif len(row) > expected_columns:
                row = row[:expected_columns]
            normalized_rows.append(row)

        parts: List[str] = ["<table>"]
        clean_caption = self._safe_text(caption)
        if clean_caption:
            parts.append(f"<caption>{escape(clean_caption)}</caption>")
        parts.append("<thead><tr>")
        parts.extend(f"<th>{escape(cell)}</th>" for cell in header)
        parts.append("</tr></thead>")
        parts.append("<tbody>")
        for row in normalized_rows:
            parts.append("<tr>")
            parts.extend(f"<td>{escape(cell)}</td>" for cell in row)
            parts.append("</tr>")
        parts.append("</tbody></table>")
        return "".join(parts)

    def _post_process_table(self, table_markdown: str) -> str:
        lines = str(table_markdown or "").splitlines()
        cleaned_lines = [line for line in lines if not _TABLE_NOISE_RE.search(line)]
        return "\n".join(cleaned_lines).strip()

    def _make_element(
        self,
        *,
        element_type: str,
        text: str,
        page_number: int,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {"page_number": int(page_number)}
        if extra_metadata:
            metadata.update(extra_metadata)
        return {
            "type": element_type,
            "element_id": str(uuid.uuid4()),
            "text": str(text or "").strip(),
            "metadata": metadata,
        }

    def iter_page_elements(self, pdf_path: str) -> Generator[List[Dict[str, Any]], None, None]:
        result = self._convert_document(pdf_path)
        document = getattr(result, "document", result)

        iterate_items = getattr(document, "iterate_items", None)
        if not callable(iterate_items):
            raise RuntimeError("Docling document does not expose iterate_items().")

        page_buckets: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        current_caption = ""
        caption_page = 1
        emitted_caption_keys: set[tuple[int, str]] = set()

        for raw_item in iterate_items():
            element = raw_item[0] if isinstance(raw_item, tuple) else raw_item
            label_name = self._label_name(element)
            page_number = self._extract_page_number(element, default=caption_page)

            if label_name == "CAPTION":
                caption_text = self._safe_text(getattr(element, "text", "") or self._extract_caption_text(element))
                if self._looks_like_table_caption(caption_text):
                    current_caption = caption_text
                    caption_page = page_number
                    caption_key = (page_number, current_caption)
                    if caption_key not in emitted_caption_keys:
                        page_buckets[page_number].append(
                            self._make_element(
                                element_type="NarrativeText",
                                text=current_caption,
                                page_number=page_number,
                            )
                        )
                        emitted_caption_keys.add(caption_key)
                continue

            if label_name != "TABLE":
                continue

            element_caption = self._extract_caption_text(element)
            if self._looks_like_table_caption(element_caption):
                current_caption = element_caption
                caption_page = page_number
                caption_key = (page_number, current_caption)
                if caption_key not in emitted_caption_keys:
                    page_buckets[page_number].append(
                        self._make_element(
                            element_type="NarrativeText",
                            text=current_caption,
                            page_number=page_number,
                        )
                    )
                    emitted_caption_keys.add(caption_key)

            table_markdown = self._call_export(element, "export_to_markdown", document=document)
            if not table_markdown:
                table_markdown = self._safe_text(getattr(element, "text", ""))
            cleaned_markdown = self._post_process_table(table_markdown)
            if not cleaned_markdown:
                continue

            clean_caption = current_caption if self._looks_like_table_caption(current_caption) else ""
            table_html = self._call_export(element, "export_to_html", document=document)
            if table_html:
                table_html = self._wrap_html_caption(table_html, clean_caption)
            else:
                table_html = self._markdown_table_to_html(cleaned_markdown, caption=clean_caption)

            extra_metadata: Dict[str, Any] = {}
            if clean_caption:
                extra_metadata["table_caption"] = clean_caption
            if table_html:
                extra_metadata["text_as_html"] = table_html

            bbox = self._extract_bbox_payload(element)
            if bbox:
                extra_metadata["bbox"] = bbox

            page_buckets[page_number].append(
                self._make_element(
                    element_type="Table",
                    text=cleaned_markdown,
                    page_number=page_number,
                    extra_metadata=extra_metadata,
                )
            )

        for page_number in sorted(page_buckets):
            batch = [
                item
                for item in page_buckets[page_number]
                if str(item.get("text") or "").strip()
            ]
            if batch:
                yield batch

    def _merge_table_fragments(self, fragments: List[str]) -> str:
        merged_lines: List[str] = []
        for index, fragment in enumerate(fragments):
            lines = [line for line in str(fragment or "").splitlines() if line.strip()]
            if not lines:
                continue
            if index == 0:
                merged_lines.extend(lines)
                continue
            if len(lines) > 2:
                merged_lines.extend(lines[2:])
            else:
                merged_lines.extend(lines)
        return "\n".join(merged_lines).strip()

    def _serialize_table_to_json(self, table_element: Any) -> Dict[str, Any]:
        if isinstance(table_element, dict):
            metadata = dict(table_element.get("metadata") or {})
            return {
                "bbox": metadata.get("bbox") or [],
                "caption": metadata.get("table_caption"),
                "markdown": str(table_element.get("text") or "").strip(),
                "html": metadata.get("text_as_html"),
                "page_number": metadata.get("page_number"),
            }

        caption = self._extract_caption_text(table_element) or None
        bbox = self._extract_bbox_payload(table_element) or {}
        markdown = self._post_process_table(
            self._call_export(table_element, "export_to_markdown", document=None)
            or self._safe_text(getattr(table_element, "text", ""))
        )
        html = self._call_export(table_element, "export_to_html", document=None) or None
        if html and caption:
            html = self._wrap_html_caption(html, caption)
        elif not html and markdown:
            html = self._markdown_table_to_html(markdown, caption=caption or "")

        return {
            "bbox": bbox or [],
            "caption": caption,
            "markdown": markdown,
            "html": html,
            "page_number": self._extract_page_number(table_element, default=1),
        }

    def _transform_for_rag(self, table_data: str | Dict[str, Any]) -> Dict[str, str]:
        if isinstance(table_data, dict):
            caption = self._safe_text(table_data.get("caption"))
            markdown = self._safe_text(table_data.get("markdown") or table_data.get("text"))
            canonical_json = json.dumps(table_data, ensure_ascii=False, indent=2)
        else:
            caption = ""
            markdown = self._safe_text(table_data)
            canonical_json = json.dumps({"markdown": markdown}, ensure_ascii=False, indent=2)

        lines = [line for line in markdown.splitlines() if line.strip()]
        parent_lines: List[str] = []
        if caption:
            parent_lines.append(f"Caption: {caption}")
        if markdown:
            parent_lines.append(markdown)

        row_lines: List[str] = []
        if len(lines) >= 3:
            headers = self._split_markdown_row(lines[0])
            separator = self._split_markdown_row(lines[1])
            if headers and self._is_markdown_separator_row(separator):
                for row_index, row_line in enumerate(lines[2:], start=1):
                    values = self._split_markdown_row(row_line)
                    if not values:
                        continue
                    pairs = [
                        f"{header}={value}"
                        for header, value in zip(headers, values)
                        if str(header or "").strip() or str(value or "").strip()
                    ]
                    if pairs:
                        prefix = f"Table={caption} | " if caption else ""
                        row_lines.append(f"{prefix}Row {row_index} | " + " | ".join(pairs))

        return {
            "table_parent_text": "\n".join(parent_lines).strip(),
            "row_key_value_text": "\n".join(row_lines).strip(),
            "canonical_table_json": canonical_json,
        }

    def preprocess_pdf(
        self,
        pdf_path: str,
        output_filename: str = "consolidated_tables_final.md",
    ) -> Path:
        merged_groups: List[Dict[str, Any]] = []
        current_group = {"caption": "Unknown Header", "content": []}

        for batch in self.iter_page_elements(pdf_path):
            for element in batch:
                element_type = str(element.get("type") or "").strip()
                text = self._safe_text(element.get("text"))
                if not text:
                    continue

                if element_type == "NarrativeText" and self._looks_like_table_caption(text):
                    if current_group["content"]:
                        merged_groups.append(current_group)
                    current_group = {"caption": text, "content": []}
                    continue

                if element_type == "Table":
                    current_group["content"].append(text)

        if current_group["content"]:
            merged_groups.append(current_group)

        final_file = self.output_dir / output_filename
        with open(final_file, "w", encoding="utf-8") as handle:
            for group in merged_groups:
                caption = self._safe_text(group.get("caption")) or "Unknown Header"
                merged_table = self._merge_table_fragments(list(group.get("content") or []))
                if not merged_table:
                    continue
                handle.write(f"\n### CAPTION: {caption}\n")
                handle.write(merged_table)
                handle.write("\n---\n")

        return final_file
