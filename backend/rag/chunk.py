# backend/rag/chunk.py

import json
import sys
import re
import uuid
import pandas as pd
from io import StringIO
from unstructured.staging.base import elements_from_json
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

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


# ============================================================
# CONTEXT-AWARE CHUNKER (PARENT-CHILD)
# ============================================================

class ContextAwareChunker:
    def __init__(self, *, chunk_size: int = 3000, chunk_overlap: int = 400):
        self.current_section = "General / Introduction"
        self.text_buffer = []
        self.current_buffer_page = 1

        safe_chunk_size = max(int(chunk_size or 3000), 200)
        safe_chunk_overlap = max(int(chunk_overlap or 0), 0)
        if safe_chunk_overlap >= safe_chunk_size:
            safe_chunk_overlap = max(safe_chunk_size // 5, 0)

        self.chunk_size = safe_chunk_size
        self.chunk_overlap = safe_chunk_overlap

        # Splitter configuration for text content.
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""]
        )

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

    def _resolve_buffer_element_type(self) -> str:
        if not self.text_buffer:
            return "NarrativeText"

        priority_order = {
            "Title": 5,
            "NarrativeText": 4,
            "ListItem": 3,
            "Table": 2,
            "UncategorizedText": 1,
        }

        counts = {}
        for item in self.text_buffer:
            element_type = str(item.get("element_type") or "NarrativeText").strip() or "NarrativeText"
            counts[element_type] = counts.get(element_type, 0) + 1

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
                        f"### Table Row: {self.current_section}\n"
                        f"Table ID: {parent_id}\n"
                        f"Headers: {' | '.join(header_cells)}\n"
                        "Row Values:\n"
                        f"{chr(10).join(row_pairs)}\n"
                        f"Original Row: {row.strip()}"
                    ),
                    metadata={
                        "type": "child",
                        "section": self.current_section,
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
    # TEXT FLUSHER (IMPROVED SPLITTING)
    # --------------------------------------------------------

    def _flush_text_buffer(self, docs_list):
        """
        Flushes collected text tokens into Documents.
        Now uses Recursive Character Splitting to prevent massive chunks.
        """
        if not self.text_buffer:
            return

        full_content = "\n".join(
            str(item.get("text") or "").strip()
            for item in self.text_buffer
            if str(item.get("text") or "").strip()
        ).strip()
        full_content = normalize_numbers(full_content)
        
        if not full_content:
            self.text_buffer = []
            return

        primary_meta = dict(self.text_buffer[0] or {})
        primary_meta["element_type"] = self._resolve_buffer_element_type()

        #  NEW: Split massive sections into smaller overlapping chunks
        # This prevents the embedding model from truncating important data
        chunks = self.splitter.split_text(full_content)

        for i, chunk_text in enumerate(chunks):
            docs_list.append(Document(
                page_content=f"### Section: {self.current_section}\n{chunk_text}",
                metadata={
                    "type": "text", 
                    "section": self.current_section, 
                    "is_parent": False,
                    #  Add index to keep order intact during retrieval
                    "chunk_index": i,
                    **self._shared_element_metadata(
                        meta=primary_meta,
                        element_type=primary_meta.get("element_type") or "NarrativeText",
                        page_num=self.current_buffer_page,
                    ),
                }
            ))
        
        self.text_buffer = []

    # --------------------------------------------------------
    # MAIN PROCESSOR
    # --------------------------------------------------------

    def process(self, input_file: str, output_file: str):
        print(f"[CHUNK] Loading filtered elements from: {input_file}")
        elements = elements_from_json(filename=input_file)

        final_documents = []
        print("[CHUNK] Processing elements with parent-child chunking...")

        for element in elements:
            category = element.category
            raw_text = element.text or ""
            # Avoid OCR normalization on tables to preserve IDs / codes
            text = raw_text if category == "Table" else normalize_numbers(raw_text)
            
            #  Safely Extract Metadata (Page + Coordinates)
            meta = getattr(element, "metadata", None)
            page_num = meta.page_number if meta else 1

            #  Extract Coordinates for Source Viewer
            # We store it as a JSON string for lightweight DB storage
            bbox_json = ""
            if meta and hasattr(meta, "coordinates") and meta.coordinates:
                try:
                    # Unstructured returns points as tuple of tuples: ((x1, y1), (x2, y2), ...)
                    points = list(meta.coordinates.points)
                    bbox_json = json.dumps(points)
                except Exception:
                    pass

            # ------------------------------------------------
            # 1️⃣ SECTION TITLES
            # ------------------------------------------------
            if category == "Title":
                self._flush_text_buffer(final_documents)
                self.current_section = text.strip()
                # Reset buffer page tracker to current title's page
                self.current_buffer_page = page_num
                final_documents.append(
                    Document(
                        page_content=f"### Title: {self.current_section}",
                        metadata={
                            "type": "text",
                            "section": self.current_section,
                            "is_parent": False,
                            **self._shared_element_metadata(
                                meta=meta,
                                element_type="Title",
                                page_num=page_num,
                                bbox_json=bbox_json,
                            ),
                        }
                    )
                )
                continue

            # ------------------------------------------------
            # 2️⃣ TABLES (PARENT-CHILD LOGIC)
            # ------------------------------------------------
            if category == "Table":
                self._flush_text_buffer(final_documents)

                html = getattr(element.metadata, "text_as_html", "")
                markdown = self.html_to_markdown(html) if html else ""
                if not (markdown or "").strip():
                    markdown = text
                
                # --- A. CREATE PARENT CHUNK (The Whole Table) ---
                parent_id = str(uuid.uuid4())
                
                parent_doc = Document(
                    page_content=(
                        f"### Table: {self.current_section}\n"
                        "Table Structure:\n"
                        f"{markdown}"
                    ),
                    metadata={
                        "type": "parent",
                        "section": self.current_section,
                        "doc_id": parent_id,  # Unique ID for linking
                        "is_parent": True,    # Flag to identify parent
                        **self._shared_element_metadata(
                            meta=meta,
                            element_type="Table",
                            page_num=page_num,
                            bbox_json=bbox_json,
                        ),
                    }
                )
                final_documents.append(parent_doc)

                final_documents.extend(
                    self._build_table_child_documents(
                        markdown=markdown,
                        parent_id=parent_id,
                        page_num=page_num,
                        bbox_json=bbox_json,
                        meta=meta,
                    )
                )
                continue

            # ------------------------------------------------
            # 3️⃣ NARRATIVE / LIST TEXT
            # ------------------------------------------------
            if category in ("NarrativeText", "UncategorizedText", "ListItem"):
                # If buffer is empty, start tracking page from this element
                if not self.text_buffer:
                    self.current_buffer_page = page_num
                
                self.text_buffer.append(
                    {
                        "text": text,
                        "element_type": category,
                        "extraction_backend": self._metadata_attr(meta, "extraction_backend", ""),
                        "extraction_source": self._metadata_attr(meta, "extraction_source", ""),
                        "source_weight_key": self._metadata_attr(meta, "source_weight_key", ""),
                        "ocr_used": bool(self._metadata_attr(meta, "ocr_used", False)),
                    }
                )

                # Semantic boundary: paragraph/list end
                if text.endswith(".") or text.endswith(":"):
                    self._flush_text_buffer(final_documents)

        # Final flush
        self._flush_text_buffer(final_documents)

        print(f"\n Created {len(final_documents)} chunks (Parents + Children).")
        
        # ----------------------------------------------------
        # SAVE OUTPUT
        # ----------------------------------------------------
        output_data = [
            {
                "content": d.page_content,
                "metadata": d.metadata
            }
            for d in final_documents
        ]

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
