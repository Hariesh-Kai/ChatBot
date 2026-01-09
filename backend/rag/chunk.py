import json
import sys
import re
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
# CONTEXT-AWARE CHUNKER
# ============================================================

class ContextAwareChunker:
    def __init__(self):
        self.current_section = "General / Introduction"
        self.text_buffer = []

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
    # TABLE → ROW-LEVEL CHUNKS
    # --------------------------------------------------------

    def split_table(self, markdown_text: str, section_title: str):
        """
        Each table row becomes ONE chunk with header context.
        """
        lines = markdown_text.split("\n")
        if len(lines) < 3:
            return []

        headers = lines[:2]        # column names + separator
        rows = lines[2:]
        chunks = []

        for row in rows:
            if not row.strip():
                continue

            content = "\n".join(headers + [row])
            content = normalize_numbers(content)

            enriched = (
                f"FACT CONTEXT:\n"
                f"This table lists parameters related to {section_title}.\n\n"
                f"### Section: {section_title}\n"
                f"{content}"
            )

            chunks.append(enriched)

        return chunks

    # --------------------------------------------------------
    # TEXT BUFFER → DOCUMENT
    # --------------------------------------------------------

    def flush_text_buffer(self):
        if not self.text_buffer:
            return None

        content = "\n".join(self.text_buffer).strip()
        content = normalize_numbers(content)

        enriched_content = (
            f"FACT CONTEXT:\n"
            f"This section discusses {self.current_section}.\n\n"
            f"### Section: {self.current_section}\n"
            f"{content}"
        )

        doc = Document(
            page_content=enriched_content,
            metadata={
                "type": "text",
                "section": self.current_section,
            },
        )

        self.text_buffer = []
        return doc

    # --------------------------------------------------------
    # MAIN PROCESSOR
    # --------------------------------------------------------

    def process(self, input_file: str, output_file: str):
        print(f"📂 Loading filtered elements from: {input_file}")
        elements = elements_from_json(filename=input_file)

        final_documents = []
        print("⚙️ Processing elements with improved chunking...")

        for element in elements:
            category = element.category
            text = normalize_numbers(element.text or "")

            # ------------------------------------------------
            # 1️⃣ SECTION TITLES
            # ------------------------------------------------
            if category == "Title":
                if self.text_buffer:
                    doc = self.flush_text_buffer()
                    if doc:
                        final_documents.append(doc)

                self.current_section = text.strip()
                self.text_buffer.append(text)
                continue

            # ------------------------------------------------
            # 2️⃣ TABLES
            # ------------------------------------------------
            if category == "Table":
                if self.text_buffer:
                    doc = self.flush_text_buffer()
                    if doc:
                        final_documents.append(doc)

                html = (
                    element.metadata.text_as_html
                    if hasattr(element.metadata, "text_as_html")
                    else ""
                )

                markdown = (
                    self.html_to_markdown(html)
                    if html else text
                )

                table_chunks = self.split_table(markdown, self.current_section)

                for chunk in table_chunks:
                    final_documents.append(
                        Document(
                            page_content=chunk,
                            metadata={
                                "type": "table",
                                "section": self.current_section,
                            },
                        )
                    )
                continue

            # ------------------------------------------------
            # 3️⃣ NARRATIVE / LIST TEXT
            # ------------------------------------------------
            if category in ("NarrativeText", "UncategorizedText", "ListItem"):
                self.text_buffer.append(text)

                # Semantic boundary: paragraph/list end
                if text.endswith(".") or text.endswith(":"):
                    doc = self.flush_text_buffer()
                    if doc:
                        final_documents.append(doc)

        # ----------------------------------------------------
        # FINAL FLUSH
        # ----------------------------------------------------
        if self.text_buffer:
            doc = self.flush_text_buffer()
            if doc:
                final_documents.append(doc)

        print(f"\n✅ Created {len(final_documents)} high-quality chunks.")

        # ----------------------------------------------------
        # SAVE OUTPUT
        # ----------------------------------------------------
        output_data = [
            {
                "content": d.page_content,
                "metadata": d.metadata,
            }
            for d in final_documents
        ]

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"💾 Saved chunks to: {output_file}")


# ============================================================
# PIPELINE WRAPPER
# ============================================================

def chunk_pdf(pdf_path: str, output_path: str):
    """
    Pipeline-compatible wrapper for ContextAwareChunker.

    NOTE:
    pdf_path must point to a filtered_elements.json file,
    NOT a raw PDF.
    """
    chunker = ContextAwareChunker()
    chunker.process(pdf_path, output_path)


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