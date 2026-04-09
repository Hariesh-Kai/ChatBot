from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.rag.metadata import extract_document_metadata


def _element(*, kind: str, text: str, page: int = 1, top: int = 100, bottom: int = 160) -> dict:
    return {
        "type": kind,
        "text": text,
        "metadata": {
            "page_number": page,
            "coordinates": {
                "points": [[50, top], [550, top], [550, bottom], [50, bottom]],
                "layout_height": 1000,
            },
        },
    }


def _extract(elements: list[dict]) -> dict:
    with tempfile.TemporaryDirectory() as temp_dir:
        elements_path = Path(temp_dir) / "elements.json"
        elements_path.write_text(json.dumps(elements), encoding="utf-8")
        return extract_document_metadata(
            elements_file=str(elements_path),
            pdf_path="dummy.pdf",
            company_document_id="doc-1",
            extra_metadata={},
        )


class MetadataExtractionTests(unittest.TestCase):
    def test_extracts_metadata_only_from_title_block_candidates(self) -> None:
        metadata = _extract(
            [
                _element(
                    kind="Title",
                    text="Basis of Design - Agogo West Hub",
                    top=90,
                    bottom=130,
                ),
                _element(
                    kind="NarrativeText",
                    text="The Agogo Development description explains the process philosophy.",
                    top=360,
                    bottom=430,
                ),
                _element(
                    kind="Table",
                    text=(
                        "Project\n"
                        "Agogo Integrated West Hub\n"
                        "Company Document ID\n"
                        "363010BGRB00508\n"
                        "Revision\n"
                        "B"
                    ),
                    top=820,
                    bottom=940,
                ),
            ]
        )

        self.assertEqual(metadata["document_title"]["value"], "Basis of Design - Agogo West Hub")
        self.assertEqual(metadata["document_type"]["value"], "Basis of Design - Agogo West Hub")
        self.assertEqual(metadata["project_name"]["value"], "Agogo Integrated West Hub")
        self.assertEqual(metadata["document_number"]["value"], "363010BGRB00508")
        self.assertEqual(metadata["revision_code"]["value"], "B")

    def test_does_not_infer_project_or_revision_from_body_text(self) -> None:
        metadata = _extract(
            [
                _element(
                    kind="NarrativeText",
                    text="The Agogo Development description explains the operating philosophy.",
                    top=320,
                    bottom=420,
                ),
                _element(
                    kind="NarrativeText",
                    text="Revision activities continue during the engineering study.",
                    top=430,
                    bottom=520,
                ),
                _element(
                    kind="NarrativeText",
                    text="Project: Mid-page note that should not be treated as title block metadata.",
                    top=540,
                    bottom=620,
                ),
            ]
        )

        self.assertIsNone(metadata["project_name"]["value"])
        self.assertIsNone(metadata["document_number"]["value"])
        self.assertIsNone(metadata["revision_code"]["value"])

    def test_missing_explicit_metadata_returns_empty_values(self) -> None:
        metadata = _extract(
            [
                _element(
                    kind="Title",
                    text="Basis of Design Summary",
                    top=100,
                    bottom=140,
                ),
                _element(
                    kind="NarrativeText",
                    text="This page does not contain a title block or labeled metadata fields.",
                    top=260,
                    bottom=340,
                ),
            ]
        )

        self.assertEqual(metadata["document_title"]["value"], "Basis of Design Summary")
        self.assertIsNone(metadata["project_name"]["value"])
        self.assertIsNone(metadata["document_number"]["value"])
        self.assertIsNone(metadata["revision_code"]["value"])


if __name__ == "__main__":
    unittest.main()
