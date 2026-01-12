# backend/rag/chunk.py

import json
import sys
import re
import uuid
import pandas as pd
from io import StringIO
from unstructured.staging.base import elements_from_json
from langchain_core.documents import Document


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
    def __init__(self):
        self.current_section = "General / Introduction"
        self.text_buffer = []
        self.current_buffer_page = 1 # Track the page number for the buffer

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

    # --------------------------------------------------------
    # TEXT FLUSHER (HELPER)
    # --------------------------------------------------------

    def _flush_text_buffer(self, docs_list):
        if not self.text_buffer:
            return

        content = "\n".join(self.text_buffer).strip()
        content = normalize_numbers(content)
        
        # Standard text chunk (Direct indexing)
        docs_list.append(Document(
            page_content=f"### Section: {self.current_section}\n{content}",
            metadata={
                "type": "text", 
                "section": self.current_section, 
                "is_parent": False,
                # ✅ Save Page Number (from the buffer tracking)
                "page_number": self.current_buffer_page
            }
        ))
        self.text_buffer = []

    # --------------------------------------------------------
    # MAIN PROCESSOR
    # --------------------------------------------------------

    def process(self, input_file: str, output_file: str):
        print(f"📂 Loading filtered elements from: {input_file}")
        elements = elements_from_json(filename=input_file)

        final_documents = []
        print("⚙️ Processing elements with Parent-Child chunking...")

        for element in elements:
            category = element.category
            text = normalize_numbers(element.text or "")
            
            # ✅ Safely Extract Metadata (Page + Coordinates)
            meta = getattr(element, "metadata", None)
            page_num = meta.page_number if meta else 1

            # ✅ Extract Coordinates for Source Viewer
            # Unstructured returns points as tuple of tuples: ((x1, y1), (x2, y2), ...)
            # We store it as a JSON string for lightweight DB storage
            bbox_json = ""
            if meta and hasattr(meta, "coordinates") and meta.coordinates:
                 # Convert tuple points to list for JSON serialization
                try:
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
                self.text_buffer.append(text)
                # Reset buffer page tracker to current title's page
                self.current_buffer_page = page_num
                continue

            # ------------------------------------------------
            # 2️⃣ TABLES (PARENT-CHILD LOGIC)
            # ------------------------------------------------
            if category == "Table":
                self._flush_text_buffer(final_documents)

                html = getattr(element.metadata, "text_as_html", "")
                markdown = self.html_to_markdown(html) if html else text
                
                # --- A. CREATE PARENT CHUNK (The Whole Table) ---
                parent_id = str(uuid.uuid4())
                
                parent_doc = Document(
                    page_content=f"### Table: {self.current_section}\n{markdown}",
                    metadata={
                        "type": "parent",
                        "section": self.current_section,
                        "doc_id": parent_id,  # Unique ID for linking
                        "is_parent": True,    # Flag to identify parent
                        "page_number": page_num, # ✅ Save Page
                        "bbox": bbox_json     # ✅ Save Highlight Box
                    }
                )
                final_documents.append(parent_doc)

                # --- B. CREATE CHILD CHUNKS (The Rows) ---
                rows = markdown.split("\n")
                
                if len(rows) > 2:
                    headers = rows[:2]
                    for row in rows[2:]:
                        if not row.strip():
                            continue
                            
                        # Reconstruct row with headers for context
                        row_content = "\n".join(headers + [row])
                        
                        child_doc = Document(
                            page_content=f"Context: {self.current_section}\n{row_content}",
                            metadata={
                                "type": "child",
                                "section": self.current_section,
                                "parent_id": parent_id,  # Link back to parent
                                "is_parent": False,
                                "page_number": page_num, # ✅ Child inherits Page
                                "bbox": bbox_json     # ✅ Child inherits Box
                            }
                        )
                        final_documents.append(child_doc)
                continue

            # ------------------------------------------------
            # 3️⃣ NARRATIVE / LIST TEXT
            # ------------------------------------------------
            if category in ("NarrativeText", "UncategorizedText", "ListItem"):
                # If buffer is empty, start tracking page from this element
                if not self.text_buffer:
                    self.current_buffer_page = page_num
                
                self.text_buffer.append(text)

                # Semantic boundary: paragraph/list end
                if text.endswith(".") or text.endswith(":"):
                    self._flush_text_buffer(final_documents)

        # Final flush
        self._flush_text_buffer(final_documents)

        print(f"\n✅ Created {len(final_documents)} chunks (Parents + Children).")
        
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

        print(f"💾 Saved chunks to: {output_file}")


# ============================================================
# CLI ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    # Usage:
    # python chunk.py <filtered_elements.json> <chunks.json>

    if len(sys.argv) != 3:
        print("❌ Usage: python chunk.py <filtered_elements.json> <chunks.json>")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    chunker = ContextAwareChunker()
    chunker.process(input_file, output_file)
    print("✅ Chunking completed.")