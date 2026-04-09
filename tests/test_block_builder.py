from __future__ import annotations

import unittest

from backend.rag.block_builder import build_grouped_blocks


class BlockBuilderTests(unittest.TestCase):
    def test_merges_broken_fragments_and_keeps_list_with_parent(self) -> None:
        payload = [
            {"type": "NarrativeText", "text": "There are 3 main", "metadata": {"page_number": 1}},
            {"type": "UncategorizedText", "text": "phases:", "metadata": {"page_number": 1}},
            {"type": "ListItem", "text": "• Collect data", "metadata": {"page_number": 1}},
            {"type": "ListItem", "text": "• Review data", "metadata": {"page_number": 1}},
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        content = result["blocks"][0]["content"]
        self.assertIn("There are 3 main phases:", content)
        self.assertIn("- Collect data", content)
        self.assertIn("- Review data", content)

    def test_phase_boundaries_split_into_separate_blocks(self) -> None:
        payload = [
            {"type": "NarrativeText", "text": "Phase 1", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "Gather the input data for the study.", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "Phase 2", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "Validate the design conditions.", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "Phase 3", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "Finalize the integrated operating case.", "metadata": {"page_number": 1}},
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 3)
        self.assertTrue(result["blocks"][0]["content"].startswith("### Phase 1"))
        self.assertTrue(result["blocks"][1]["content"].startswith("### Phase 2"))
        self.assertTrue(result["blocks"][2]["content"].startswith("### Phase 3"))

    def test_real_title_becomes_exact_heading(self) -> None:
        payload = [
            {"type": "Title", "text": "Agogo Development description", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "This section explains the development concept in detail.",
                "metadata": {"page_number": 1},
            },
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertTrue(result["blocks"][0]["content"].startswith("### Agogo Development description"))

    def test_two_line_split_heading_merges_before_section_detection(self) -> None:
        payload = [
            {"type": "Title", "text": "Agogo Development", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "Description", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "This section explains the development concept in detail.",
                "metadata": {"page_number": 1},
            },
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertTrue(result["blocks"][0]["content"].startswith("### Agogo Development Description"))
        self.assertEqual(result["summary"]["merged_heading_fragments"], 1)

    def test_three_line_split_heading_merges_before_section_detection(self) -> None:
        payload = [
            {"type": "Title", "text": "Agogo Integrated", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "West Hub", "metadata": {"page_number": 1}},
            {"type": "UncategorizedText", "text": "Overview", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "The overview summarizes the design philosophy.",
                "metadata": {"page_number": 1},
            },
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertTrue(result["blocks"][0]["content"].startswith("### Agogo Integrated West Hub Overview"))
        self.assertEqual(result["summary"]["merged_heading_fragments"], 2)

    def test_noise_and_legal_footer_are_removed(self) -> None:
        payload = [
            {
                "type": "NarrativeText",
                "text": (
                    "Company Document ID\n"
                    "Page 1\n"
                    "This document is property of ACME and shall neither be shown to third parties.\n"
                    "Useful operating data remains here."
                ),
                "metadata": {"page_number": 1},
            }
        ]

        result = build_grouped_blocks(payload)
        content = result["blocks"][0]["content"]
        self.assertEqual(content, "Useful operating data remains here.")
        self.assertGreaterEqual(result["summary"]["noise_lines_removed"], 3)

    def test_label_leak_lines_are_removed(self) -> None:
        payload = [
            {
                "type": "NarrativeText",
                "text": "NarrativeText\nUseful technical paragraph.\nListItem\nUncategorizedText",
                "metadata": {"page_number": 1},
            }
        ]

        result = build_grouped_blocks(payload)
        content = result["blocks"][0]["content"]
        self.assertEqual(content, "Useful technical paragraph.")
        self.assertGreaterEqual(result["summary"]["label_lines_removed"], 3)

    def test_uncategorized_fragments_and_post_list_text_stay_in_one_block(self) -> None:
        payload = [
            {"type": "NarrativeText", "text": "There are 3 main", "metadata": {"page_number": 1}},
            {"type": "UncategorizedText", "text": "phases:", "metadata": {"page_number": 1}},
            {"type": "ListItem", "text": "1. Phase 1 - Collect field data", "metadata": {"page_number": 1}},
            {"type": "ListItem", "text": "2. Phase 2 - Validate design inputs", "metadata": {"page_number": 1}},
            {
                "type": "UncategorizedText",
                "text": "Phase 3 concludes the final engineering review.",
                "metadata": {"page_number": 1},
            },
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        content = result["blocks"][0]["content"]
        self.assertIn("There are 3 main phases:", content)
        self.assertIn("- Phase 1 - Collect field data", content)
        self.assertIn("- Phase 2 - Validate design inputs", content)
        self.assertIn("Phase 3 concludes the final engineering review.", content)
        self.assertGreaterEqual(result["summary"]["merged_text_fragments"], 1)

    def test_trailing_heading_fragment_is_not_emitted_as_its_own_block(self) -> None:
        payload = [
            {"type": "Title", "text": "Operating Conditions", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "The operating envelope is defined below.",
                "metadata": {"page_number": 1},
            },
            {"type": "NarrativeText", "text": "Additional Notes", "metadata": {"page_number": 1}},
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertIn("Additional Notes", result["blocks"][0]["content"])
        self.assertFalse(result["blocks"][0]["content"].startswith("### Additional"))
        self.assertEqual(result["summary"]["orphan_heading_candidates_downgraded"], 1)

    def test_lowercase_continuation_line_is_not_promoted_to_heading(self) -> None:
        payload = [
            {"type": "NarrativeText", "text": "phase 3", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "starts with a lowercase fragment and must remain plain text.",
                "metadata": {"page_number": 1},
            },
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertFalse(result["blocks"][0]["content"].startswith("###"))
        self.assertIn("phase 3", result["blocks"][0]["content"])

    def test_sentence_continuation_fragment_is_not_used_as_heading(self) -> None:
        payload = [
            {"type": "NarrativeText", "text": "Operating philosophy", "metadata": {"page_number": 1}},
            {"type": "NarrativeText", "text": "for the process design", "metadata": {"page_number": 1}},
            {
                "type": "NarrativeText",
                "text": "remains conservative throughout the study.",
                "metadata": {"page_number": 1},
            },
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertFalse(result["blocks"][0]["content"].startswith("###"))
        self.assertIn("Operating philosophy for the process design remains conservative throughout the study.", result["blocks"][0]["content"])

    def test_useful_figure_caption_attaches_and_bare_caption_does_not(self) -> None:
        filtered = [
            {
                "type": "NarrativeText",
                "text": "The layout is illustrated below.",
                "metadata": {"page_number": 1},
            },
            {
                "type": "NarrativeText",
                "text": "Process routing remains unchanged.",
                "metadata": {"page_number": 2},
            },
        ]
        raw = [
            {
                "type": "NarrativeText",
                "text": "The layout is illustrated below.",
                "metadata": {"page_number": 1},
            },
            {
                "type": "FigureCaption",
                "text": "Figure 2 - Separator arrangement and manifold tie-in",
                "metadata": {"page_number": 1},
            },
            {
                "type": "NarrativeText",
                "text": "Process routing remains unchanged.",
                "metadata": {"page_number": 2},
            },
            {
                "type": "FigureCaption",
                "text": "Figure 3",
                "metadata": {"page_number": 2},
            },
        ]

        result = build_grouped_blocks(filtered, raw_elements=raw)
        self.assertEqual(len(result["blocks"]), 2)
        self.assertIn("Separator arrangement and manifold tie-in", result["blocks"][0]["content"])
        self.assertNotIn("Figure 3", result["blocks"][1]["content"])
        self.assertEqual(result["summary"]["figure_captions_attached"], 1)

    def test_tables_are_excluded_from_grouped_blocks(self) -> None:
        payload = [
            {"type": "NarrativeText", "text": "Operating notes remain important.", "metadata": {"page_number": 1}},
            {"type": "Table", "text": "Name | Value", "metadata": {"page_number": 1}},
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertNotIn("Name | Value", result["blocks"][0]["content"])

    def test_orphan_heading_candidate_without_body_stays_plain_text(self) -> None:
        payload = [
            {"type": "Title", "text": "Standalone Summary", "metadata": {"page_number": 1}},
        ]

        result = build_grouped_blocks(payload)
        self.assertEqual(len(result["blocks"]), 1)
        self.assertEqual(result["blocks"][0]["content"], "Standalone Summary")
        self.assertEqual(result["summary"]["orphan_heading_candidates_downgraded"], 1)


if __name__ == "__main__":
    unittest.main()
