from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.rag.chunk import ContextAwareChunker
from backend.rag.filtering import filter_element_dicts
from backend.rag.table_normalization import extract_explicit_table_signals, normalize_html_table


HIERARCHICAL_TABLE_HTML = """
<table>
  <caption>Table 1. Pressure Data (Units: kPa)</caption>
  <thead>
    <tr>
      <th rowspan="2">Line</th>
      <th colspan="2">Pressure</th>
    </tr>
    <tr>
      <th>Inlet</th>
      <th>Outlet</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>A</td>
      <td>10</td>
      <td>8</td>
    </tr>
    <tr>
      <td>B</td>
      <td>12</td>
      <td>9</td>
    </tr>
  </tbody>
</table>
""".strip()


class TableNormalizationTests(unittest.TestCase):
    def test_normalize_html_table_builds_hierarchy_and_cells(self) -> None:
        normalized = normalize_html_table(
            html=HIERARCHICAL_TABLE_HTML,
            table_id="table-pressure",
        )

        self.assertEqual(normalized["table_id"], "table-pressure")
        self.assertEqual(normalized["caption"], "Table 1. Pressure Data (Units: kPa)")
        self.assertEqual(
            normalized["columns"],
            [
                {"name": "Line", "children": []},
                {
                    "name": "Pressure",
                    "children": [
                        {"name": "Inlet", "children": []},
                        {"name": "Outlet", "children": []},
                    ],
                },
            ],
        )
        self.assertEqual(normalized["metadata"]["units"], "kPa")
        self.assertIsNone(normalized["metadata"]["context"])
        self.assertGreaterEqual(normalized["metadata"]["confidence"]["structure"], 0.7)
        self.assertGreaterEqual(normalized["metadata"]["confidence"]["headers"], 0.8)

        cell_map = {
            (int(cell["row_index"]), tuple(cell["column_path"])): cell["value"]
            for cell in normalized["cells"]
        }
        self.assertEqual(cell_map[(1, ("Line",))], "A")
        self.assertEqual(cell_map[(1, ("Pressure", "Inlet"))], "10")
        self.assertEqual(cell_map[(1, ("Pressure", "Outlet"))], "8")
        self.assertEqual(cell_map[(2, ("Line",))], "B")
        self.assertEqual(cell_map[(2, ("Pressure", "Inlet"))], "12")
        self.assertEqual(cell_map[(2, ("Pressure", "Outlet"))], "9")

    def test_filtering_attaches_normalized_table_and_removes_noisy_rows(self) -> None:
        noisy_html = """
        <table>
          <thead>
            <tr><th>Metric</th><th>Value</th></tr>
          </thead>
          <tbody>
            <tr><td>Page 1 of 2</td><td>Page 1 of 2</td></tr>
            <tr><td>Flow</td><td>10</td></tr>
            <tr><td>Flow</td><td>10</td></tr>
          </tbody>
        </table>
        """.strip()

        result = filter_element_dicts(
            [
                {
                    "type": "Table",
                    "element_id": "table-noisy",
                    "text": "Flow | 10",
                    "metadata": {
                        "page_number": 1,
                        "text_as_html": noisy_html,
                    },
                }
            ]
        )

        filtered = result["filtered_elements"]
        self.assertEqual(len(filtered), 1)

        metadata = filtered[0]["metadata"]
        self.assertEqual(metadata["text_as_html"], noisy_html)
        self.assertEqual(metadata["cleanup"]["normalized_table_rows_removed"], 2)
        self.assertEqual(
            metadata["normalized_table_signals"],
            {"caption": None, "units": None, "context": None},
        )
        self.assertTrue(metadata["normalized_table_is_primary"])

        normalized = metadata["normalized_table"]
        self.assertEqual(
            normalized["cells"],
            [
                {"row_index": 1, "column_path": ["Metric"], "value": "Flow"},
                {"row_index": 1, "column_path": ["Value"], "value": "10"},
            ],
        )
        self.assertIn("metadata", normalized)
        self.assertIn("confidence", normalized["metadata"])

    def test_chunker_prefers_normalized_table_for_child_rows(self) -> None:
        normalized = normalize_html_table(
            html=HIERARCHICAL_TABLE_HTML,
            table_id="table-pressure",
        )

        payload = [
            {
                "type": "Table",
                "element_id": "table-pressure",
                "text": "Pressure table",
                "metadata": {
                    "page_number": 1,
                    "text_as_html": HIERARCHICAL_TABLE_HTML,
                    "normalized_table": normalized,
                },
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "filtered_elements.json"
            output_path = Path(temp_dir) / "chunks.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")

            chunker = ContextAwareChunker()
            chunker.process(str(input_path), str(output_path))

            chunks = json.loads(output_path.read_text(encoding="utf-8"))
            child_chunks = [item for item in chunks if item.get("metadata", {}).get("type") == "child"]

        self.assertEqual(len(child_chunks), 2)
        self.assertIn('Column Path ["Pressure", "Inlet"]: 10', child_chunks[0]["content"])
        self.assertIn('Column Path ["Pressure", "Outlet"]: 8', child_chunks[0]["content"])
        self.assertIn("Row Index: 1", child_chunks[0]["content"])

    def test_extract_explicit_table_signals_detects_caption_units_and_context(self) -> None:
        html = """
        <table>
          <caption>Table 9. Temperature Summary (Units: deg C)</caption>
          <tr><th>Name</th><th>Value</th></tr>
          <tr><td>Inlet</td><td>42</td></tr>
        </table>
        """.strip()

        self.assertEqual(
            extract_explicit_table_signals(
                html,
                nearby_text={"above": "Operating envelope for pump skid", "below": None},
            ),
            {
                "caption": "Table 9. Temperature Summary (Units: deg C)",
                "units": "deg C",
                "context": "Operating envelope for pump skid",
            },
        )

    def test_normalize_html_table_uses_nearby_caption_when_explicit(self) -> None:
        html = """
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Flow</td><td>100</td></tr>
        </table>
        """.strip()

        normalized = normalize_html_table(
            html=html,
            table_id="table-nearby",
            nearby_text={
                "above": "Table 4. Flow Summary",
                "below": "Units: m3/h",
            },
        )

        self.assertEqual(normalized["caption"], "Table 4. Flow Summary")
        self.assertEqual(normalized["metadata"]["units"], "m3/h")
        self.assertIsNone(normalized["metadata"]["context"])

    def test_filtering_merges_clear_continuation_fragments(self) -> None:
        fragment_html_one = """
        <table>
          <thead>
            <tr><th>Metric</th><th>Value</th></tr>
          </thead>
          <tbody>
            <tr><td>Flow</td><td>10</td></tr>
          </tbody>
        </table>
        """.strip()
        fragment_html_two = """
        <table>
          <thead>
            <tr><th>Metric</th><th>Value</th></tr>
          </thead>
          <tbody>
            <tr><td>Pressure</td><td>20</td></tr>
          </tbody>
        </table>
        """.strip()

        result = filter_element_dicts(
            [
                {
                    "type": "Table",
                    "element_id": "table-part-1",
                    "text": "fragment 1",
                    "metadata": {"page_number": 1, "text_as_html": fragment_html_one},
                },
                {
                    "type": "NarrativeText",
                    "element_id": "caption-part-2",
                    "text": "Table 1 (continued)",
                    "metadata": {"page_number": 2},
                },
                {
                    "type": "Table",
                    "element_id": "table-part-2",
                    "text": "fragment 2",
                    "metadata": {"page_number": 2, "text_as_html": fragment_html_two},
                },
            ]
        )

        filtered = result["filtered_elements"]
        first_table = filtered[0]
        second_table = filtered[2]

        first_meta = first_table["metadata"]
        second_meta = second_table["metadata"]

        merged_values = [cell["value"] for cell in first_meta["normalized_table"]["cells"]]
        self.assertEqual(merged_values, ["Flow", "10", "Pressure", "20"])
        self.assertEqual(second_meta["normalized_table_merged_into"], "table-part-1")
        self.assertFalse(second_meta["normalized_table_is_primary"])


if __name__ == "__main__":
    unittest.main()
