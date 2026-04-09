from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.rag.block_builder import build_grouped_blocks
from backend.rag.chunk import ContextAwareChunker
from backend.rag.table_normalization import normalize_html_table


TABLE_HTML = """
<table>
  <thead>
    <tr><th>Name</th><th>Value</th></tr>
  </thead>
  <tbody>
    <tr><td>Flow</td><td>10</td></tr>
    <tr><td>Pressure</td><td>20</td></tr>
  </tbody>
</table>
""".strip()


def _run_chunker(payload, *, chunk_size: int = 3000, chunk_overlap: int = 400, grouped_blocks=None):
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "filtered_elements.json"
        output_path = Path(temp_dir) / "chunks.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        grouped_blocks_path = None
        if grouped_blocks is not None:
            grouped_blocks_path = Path(temp_dir) / "grouped_blocks.json"
            grouped_blocks_path.write_text(json.dumps(grouped_blocks), encoding="utf-8")

        chunker = ContextAwareChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunker.process(
            str(input_path),
            str(output_path),
            grouped_blocks_file=str(grouped_blocks_path) if grouped_blocks_path else None,
        )

        return json.loads(output_path.read_text(encoding="utf-8"))


def _text_chunks(chunks):
    return [item for item in chunks if (item.get("metadata") or {}).get("type") == "text"]


def _paragraph(label: str, *, words: int = 110) -> str:
    return " ".join(f"{label}word{index}" for index in range(words)) + "."


class ChunkingTests(unittest.TestCase):
    def test_section_fallback_uses_explicit_section_when_title_is_missing(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "Operating envelope paragraph one.",
                    "metadata": {"page_number": 1, "section": "Operating Envelope"},
                },
                {
                    "type": "NarrativeText",
                    "text": "Operating envelope paragraph two remains in the same section.",
                    "metadata": {"page_number": 1},
                },
            ]
        )

        text_chunks = _text_chunks(chunks)
        self.assertEqual(len(text_chunks), 1)
        self.assertEqual(text_chunks[0]["metadata"]["section"], "Operating Envelope")

    def test_noisy_title_does_not_reset_section_grouping(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "Title",
                    "text": "Process Overview",
                    "metadata": {"page_number": 1},
                },
                {
                    "type": "NarrativeText",
                    "text": "The first paragraph belongs to the process overview section.",
                    "metadata": {"page_number": 1},
                },
                {
                    "type": "Title",
                    "text": "text",
                    "metadata": {"page_number": 1},
                },
                {
                    "type": "NarrativeText",
                    "text": "The second paragraph should stay in the same section.",
                    "metadata": {"page_number": 1},
                },
            ]
        )

        text_chunks = _text_chunks(chunks)
        self.assertEqual(len(text_chunks), 1)
        self.assertEqual(text_chunks[0]["metadata"]["section"], "Process Overview")
        self.assertNotIn("### Title:", text_chunks[0]["content"])

    def test_ocr_line_merging_rejoins_soft_wrapped_paragraphs(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "This OCR paragraph was split\nacross a soft line break for no reason.",
                    "metadata": {"page_number": 1, "section": "Overview"},
                }
            ]
        )

        content = _text_chunks(chunks)[0]["content"]
        self.assertIn("split across a soft line break", content)
        self.assertNotIn("split\nacross", content)

    def test_paragraph_and_list_stay_together(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "Implementation phases:",
                    "metadata": {"page_number": 1, "section": "Phases"},
                },
                {
                    "type": "ListItem",
                    "text": "1. Phase 1 - Collect data",
                    "metadata": {"page_number": 1, "section": "Phases"},
                },
                {
                    "type": "ListItem",
                    "text": "2. Phase 2 - Review data",
                    "metadata": {"page_number": 1, "section": "Phases"},
                },
            ]
        )

        text_chunks = _text_chunks(chunks)
        self.assertEqual(len(text_chunks), 1)
        self.assertIn("Implementation phases:", text_chunks[0]["content"])
        self.assertIn("1. Phase 1 - Collect data", text_chunks[0]["content"])
        self.assertIn("2. Phase 2 - Review data", text_chunks[0]["content"])

    def test_hyphenated_ocr_words_are_rejoined(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "The maximum pres-\nsure shall remain below the design limit.",
                    "metadata": {"page_number": 1, "section": "Limits"},
                }
            ]
        )

        self.assertIn("pressure shall remain", _text_chunks(chunks)[0]["content"])

    def test_large_paragraph_fallback_split_creates_multiple_chunks(self) -> None:
        long_paragraph = " ".join(
            f"Sentence {index} describes a long paragraph with enough detail to exercise the fallback splitter properly."
            for index in range(1, 31)
        )
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": long_paragraph,
                    "metadata": {"page_number": 1, "section": "Long Form"},
                }
            ]
        )

        text_chunks = _text_chunks(chunks)
        self.assertGreater(len(text_chunks), 1)
        for chunk in text_chunks:
            self.assertLessEqual(len(chunk["content"].split()), 400)

    def test_noise_lines_are_removed_from_chunk_content(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "Map to page\nPage 1\ntext\nUseful operating data remains here.",
                    "metadata": {"page_number": 1, "section": "Noise Cleanup"},
                }
            ]
        )

        content = _text_chunks(chunks)[0]["content"]
        self.assertEqual(content, "Useful operating data remains here.")

    def test_semantic_overlap_repeats_whole_tail_unit_within_same_section(self) -> None:
        paragraph_one = _paragraph("alpha")
        paragraph_two = _paragraph("beta")
        paragraph_three = _paragraph("gamma")
        paragraph_four = _paragraph("delta")

        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "\n\n".join(
                        [paragraph_one, paragraph_two, paragraph_three, paragraph_four]
                    ),
                    "metadata": {"page_number": 1, "section": "Overlap Section"},
                }
            ]
        )

        text_chunks = _text_chunks(chunks)
        self.assertGreaterEqual(len(text_chunks), 2)
        self.assertIn(paragraph_three, text_chunks[0]["content"])
        self.assertIn(paragraph_three, text_chunks[1]["content"])
        self.assertNotIn(paragraph_two, text_chunks[1]["content"])
        self.assertTrue(text_chunks[1]["metadata"]["has_semantic_overlap"])
        self.assertGreater(text_chunks[1]["metadata"]["semantic_overlap_words"], 0)

    def test_semantic_overlap_does_not_cross_section_boundary(self) -> None:
        section_a = "\n\n".join([_paragraph("sectiona", words=115) for _ in range(4)])
        section_b = "\n\n".join([_paragraph("sectionb", words=105) for _ in range(2)])

        chunks = _run_chunker(
            [
                {"type": "Title", "text": "Section A", "metadata": {"page_number": 1}},
                {"type": "NarrativeText", "text": section_a, "metadata": {"page_number": 1}},
                {"type": "Title", "text": "Section B", "metadata": {"page_number": 1}},
                {"type": "NarrativeText", "text": section_b, "metadata": {"page_number": 1}},
            ]
        )

        text_chunks = _text_chunks(chunks)
        section_b_chunks = [chunk for chunk in text_chunks if chunk["metadata"]["section"] == "Section B"]
        self.assertTrue(section_b_chunks)
        for chunk in section_b_chunks:
            self.assertNotIn("sectionaword", chunk["content"])

    def test_protected_technical_sentence_stays_whole_and_can_overlap(self) -> None:
        lead_in = _paragraph("lead", words=150)
        middle = _paragraph("middle", words=150)
        protected_sentence = (
            "The separator design pressure is 45 bar and the operating temperature is 65 deg C "
            "for the gas dehydration train and compressor manifold."
        )
        closing = _paragraph("close", words=150)

        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": "\n\n".join([lead_in, middle, protected_sentence, closing]),
                    "metadata": {"page_number": 1, "section": "Protected"},
                }
            ]
        )

        text_chunks = _text_chunks(chunks)
        self.assertGreaterEqual(len(text_chunks), 2)
        for chunk in text_chunks:
            if "design pressure" in chunk["content"]:
                self.assertIn(protected_sentence, chunk["content"])

        protected_chunks = [chunk for chunk in text_chunks if protected_sentence in chunk["content"]]
        self.assertGreaterEqual(len(protected_chunks), 2)
        self.assertTrue(all(chunk["metadata"]["protected_sentence_count"] >= 1 for chunk in protected_chunks))

    def test_title_block_metadata_is_removed_from_content_and_kept_in_metadata(self) -> None:
        chunks = _run_chunker(
            [
                {
                    "type": "NarrativeText",
                    "text": (
                        "Company Document ID: CD-FE-12345\n"
                        "Revision: B\n"
                        "Process overview paragraph explains the operating philosophy clearly."
                    ),
                    "metadata": {"page_number": 1, "section": "Overview"},
                }
            ]
        )

        text_chunk = _text_chunks(chunks)[0]
        self.assertNotIn("Company Document ID", text_chunk["content"])
        self.assertNotIn("Revision: B", text_chunk["content"])
        self.assertEqual(text_chunk["metadata"]["document_number"], "CD-FE-12345")
        self.assertEqual(text_chunk["metadata"]["revision_code"], "B")

    def test_table_parent_and_child_contracts_remain_compatible(self) -> None:
        normalized = normalize_html_table(html=TABLE_HTML, table_id="table-1")
        chunks = _run_chunker(
            [
                {
                    "type": "Table",
                    "element_id": "table-1",
                    "text": "Table text",
                    "metadata": {
                        "page_number": 1,
                        "section": "Operating Data",
                        "text_as_html": TABLE_HTML,
                        "normalized_table": normalized,
                    },
                }
            ]
        )

        parent_chunks = [item for item in chunks if (item.get("metadata") or {}).get("type") == "parent"]
        child_chunks = [item for item in chunks if (item.get("metadata") or {}).get("type") == "child"]

        self.assertEqual(len(parent_chunks), 1)
        self.assertEqual(len(child_chunks), 2)
        self.assertEqual(parent_chunks[0]["chunk_type"], "table")
        self.assertEqual(child_chunks[0]["chunk_type"], "table_row")
        self.assertIn("content", parent_chunks[0])
        self.assertIn("metadata", parent_chunks[0])
        self.assertIn("section", parent_chunks[0])
        self.assertIn("page_number", parent_chunks[0])

    def test_grouped_blocks_become_text_source_when_provided(self) -> None:
        payload = [
            {
                "type": "NarrativeText",
                "text": "Company Document ID\nPage 1\nRaw OCR text that should not survive.",
                "metadata": {"page_number": 1, "section": "Overview"},
            }
        ]
        grouped = build_grouped_blocks(payload)["blocks"]
        self.assertEqual(len(grouped), 1)

        chunks = _run_chunker(payload, grouped_blocks=grouped)
        text_chunk = _text_chunks(chunks)[0]
        self.assertNotIn("Company Document ID", text_chunk["content"])
        self.assertNotIn("Page 1", text_chunk["content"])
        self.assertIn("Raw OCR text that should not survive.", text_chunk["content"])

    def test_grouped_blocks_are_not_recleaned_or_reparsed_for_metadata(self) -> None:
        payload = [
            {
                "type": "NarrativeText",
                "text": "Placeholder text from the raw OCR path.",
                "metadata": {"page_number": 1, "section": "Overview"},
            }
        ]
        grouped_blocks = [
            {
                "block_id": "block-1",
                "page_number": 1,
                "section": "Overview",
                "heading": "Overview",
                "content": "### Overview\n\nCompany Document ID: CD-FE-12345\n\nUseful narrative body.",
                "source_element_ids": ["idx:0"],
                "source_categories": ["NarrativeText"],
                "bbox": "",
            }
        ]

        chunks = _run_chunker(payload, grouped_blocks=grouped_blocks)
        text_chunk = _text_chunks(chunks)[0]
        self.assertIn("Company Document ID: CD-FE-12345", text_chunk["content"])
        self.assertNotIn("document_number", text_chunk["metadata"])

    def test_grouped_blocks_keep_table_chunks_and_semantic_chunking(self) -> None:
        long_grouped_text = "\n\n".join(
            [
                _paragraph("lead", words=140),
                _paragraph("middle", words=140),
                "The separator design pressure is 45 bar and the operating temperature is 65 deg C for the gas dehydration train.",
                _paragraph("close", words=140),
            ]
        )
        normalized = normalize_html_table(html=TABLE_HTML, table_id="table-1")
        payload = [
            {"type": "Title", "text": "Operating Data", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "Placeholder text that grouped blocks will replace.",
                "metadata": {"page_number": 1, "section": "Operating Data"},
            },
            {
                "type": "Table",
                "element_id": "table-1",
                "text": "Table text",
                "metadata": {
                    "page_number": 1,
                    "section": "Operating Data",
                    "text_as_html": TABLE_HTML,
                    "normalized_table": normalized,
                },
            },
        ]
        grouped_blocks = [
            {
                "block_id": "block-1",
                "page_number": 1,
                "section": "Operating Data",
                "heading": "Operating Data",
                "content": f"### Operating Data\n\n{long_grouped_text}",
                "source_element_ids": ["idx:0", "idx:1"],
                "source_categories": ["Title", "NarrativeText"],
                "bbox": "",
            }
        ]

        chunks = _run_chunker(payload, grouped_blocks=grouped_blocks)
        text_chunks = _text_chunks(chunks)
        parent_chunks = [item for item in chunks if (item.get("metadata") or {}).get("type") == "parent"]
        child_chunks = [item for item in chunks if (item.get("metadata") or {}).get("type") == "child"]

        self.assertGreaterEqual(len(text_chunks), 2)
        self.assertEqual(len(parent_chunks), 1)
        self.assertEqual(len(child_chunks), 2)
        protected_chunks = [chunk for chunk in text_chunks if "design pressure" in chunk["content"]]
        self.assertTrue(protected_chunks)
        self.assertTrue(all(chunk["metadata"]["protected_sentence_count"] >= 1 for chunk in protected_chunks))


if __name__ == "__main__":
    unittest.main()
