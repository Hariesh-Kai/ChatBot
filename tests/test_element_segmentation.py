from __future__ import annotations

import unittest

from backend.rag.element_segmentation import (
    build_stage_segregation_summary,
    element_family_from_category,
    segregate_elements_by_type,
)
from backend.rag.filtering import filter_element_dicts


class ElementSegmentationTests(unittest.TestCase):
    def test_element_family_from_category_maps_table_text_and_image(self) -> None:
        self.assertEqual(element_family_from_category("Table"), "table")
        self.assertEqual(element_family_from_category("NarrativeText"), "text")
        self.assertEqual(element_family_from_category("Image"), "image")
        self.assertEqual(element_family_from_category("FigureCaption"), "image")
        self.assertEqual(element_family_from_category("UnknownThing"), "other")

    def test_segregate_elements_by_type_groups_items(self) -> None:
        elements = [
            {"type": "Table", "text": "A"},
            {"type": "NarrativeText", "text": "B"},
            {"type": "Image", "text": "C"},
            {"type": "CustomBlock", "text": "D"},
        ]

        grouped = segregate_elements_by_type(elements)

        self.assertEqual(len(grouped["table"]), 1)
        self.assertEqual(len(grouped["text"]), 1)
        self.assertEqual(len(grouped["image"]), 1)
        self.assertEqual(len(grouped["other"]), 1)

    def test_build_stage_segregation_summary_counts_each_stage(self) -> None:
        summary = build_stage_segregation_summary(
            raw_elements=[
                {"type": "Table", "text": "T"},
                {"type": "Image", "text": "I"},
                {"type": "NarrativeText", "text": "N"},
            ],
            filtered_elements=[
                {"type": "Table", "text": "T"},
                {"type": "NarrativeText", "text": "N"},
            ],
            removed_elements=[
                {"type": "Image", "text": "I"},
            ],
        )

        self.assertEqual(summary["raw"]["table"], 1)
        self.assertEqual(summary["raw"]["image"], 1)
        self.assertEqual(summary["filtered"]["text"], 1)
        self.assertEqual(summary["removed"]["image"], 1)

    def test_filtering_summary_includes_element_groups(self) -> None:
        result = filter_element_dicts(
            [
                {"type": "Table", "text": "A | B", "metadata": {"page_number": 1}},
                {"type": "NarrativeText", "text": "Useful text", "metadata": {"page_number": 1}},
                {"type": "Image", "text": "Picture [100 x 100] omitted", "metadata": {"page_number": 1}},
            ]
        )

        groups = result["summary"].get("element_groups") or {}
        self.assertEqual(groups["raw"]["table"], 1)
        self.assertEqual(groups["raw"]["text"], 1)
        self.assertEqual(groups["raw"]["image"], 1)
        self.assertEqual(groups["filtered"]["table"], 1)
        self.assertEqual(groups["filtered"]["text"], 1)
        self.assertEqual(groups["removed"]["image"], 1)

    def test_filtering_keeps_visual_images_without_text(self) -> None:
        result = filter_element_dicts(
            [
                {
                    "type": "Image",
                    "text": "",
                    "metadata": {
                        "page_number": 1,
                        "coordinates": {
                            "points": [[10, 10], [100, 10], [100, 80], [10, 80]],
                            "layout_height": 1000,
                        },
                    },
                }
            ]
        )

        self.assertEqual(len(result["filtered_elements"]), 1)
        self.assertEqual(result["summary"]["element_groups"]["filtered"]["image"], 1)
        self.assertEqual(len(result["removed_elements"]), 0)


if __name__ == "__main__":
    unittest.main()
