from __future__ import annotations

import tempfile
import unittest

from backend.rag.preprocessor_registry import normalize_rag_preprocessor
from backend.rag.table_preprocessor import TablePreprocessor


class TablePreprocessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.preprocessor = TablePreprocessor(output_dir=self.temp_dir.name)

    def test_markdown_table_to_html_includes_caption_and_cells(self) -> None:
        markdown = """
        | Metric | Value |
        | --- | --- |
        | Flow | 100 |
        | Pressure | 20 |
        """.strip()

        html = self.preprocessor._markdown_table_to_html(
            markdown,
            caption="Table 4. Flow Summary",
        )

        self.assertIn("<table>", html)
        self.assertIn("<caption>Table 4. Flow Summary</caption>", html)
        self.assertIn("<th>Metric</th>", html)
        self.assertIn("<td>100</td>", html)
        self.assertIn("<td>20</td>", html)

    def test_post_process_table_removes_known_document_noise(self) -> None:
        markdown = """
        | Metric | Value |
        | --- | --- |
        | Flow | 100 |
        Page 2
        Revision Number A
        """.strip()

        cleaned = self.preprocessor._post_process_table(markdown)

        self.assertIn("| Flow | 100 |", cleaned)
        self.assertNotIn("Page 2", cleaned)
        self.assertNotIn("Revision Number", cleaned)

    def test_merge_table_fragments_skips_repeated_headers(self) -> None:
        fragment_one = """
        | Metric | Value |
        | --- | --- |
        | Flow | 100 |
        """.strip()
        fragment_two = """
        | Metric | Value |
        | --- | --- |
        | Pressure | 20 |
        """.strip()

        merged = self.preprocessor._merge_table_fragments([fragment_one, fragment_two])

        self.assertIn("| Flow | 100 |", merged)
        self.assertIn("| Pressure | 20 |", merged)
        self.assertEqual(merged.count("| Metric | Value |"), 1)

    def test_preprocessor_registry_accepts_table_preprocessor_aliases(self) -> None:
        self.assertEqual(normalize_rag_preprocessor("docling_table"), "table_preprocessor")
        self.assertEqual(normalize_rag_preprocessor("docling_tables"), "table_preprocessor")
        self.assertEqual(normalize_rag_preprocessor("tablepreprocessor"), "table_preprocessor")


if __name__ == "__main__":
    unittest.main()
