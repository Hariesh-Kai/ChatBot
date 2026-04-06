import json
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple
from unstructured.staging.base import elements_from_json, elements_to_json


KEEP_CATEGORIES = [
    "Title",
    "NarrativeText",
    "Table",
    "ListItem",
    "UncategorizedText"
]

DISCARD_CATEGORIES = [
    "Header",
    "Footer",
    "Image",
    "FigureCaption",
    "PageBreak"
]

MIN_TEXT_LENGTH = 3


def _normalize_category(raw_category: Any) -> str:
    value = str(raw_category or "").strip()
    if not value:
        return "Unknown"
    return value.rsplit(".", 1)[-1]


def _element_category(element: Dict[str, Any]) -> str:
    if not isinstance(element, dict):
        return "Unknown"

    metadata = element.get("metadata")
    if isinstance(metadata, dict):
        for key in ("category", "type"):
            value = metadata.get(key)
            if value:
                return _normalize_category(value)

    return _normalize_category(element.get("type") or element.get("category"))


def _element_text(element: Dict[str, Any]) -> str:
    if not isinstance(element, dict):
        return ""
    return str(element.get("text") or element.get("content") or "").strip()


def _element_page(element: Dict[str, Any]) -> int:
    metadata = element.get("metadata") if isinstance(element, dict) else None
    if isinstance(metadata, dict):
        try:
            return int(metadata.get("page_number") or 1)
        except Exception:
            return 1
    return 1


def _remove_reason(category: str, text: str) -> str:
    if category in KEEP_CATEGORIES and len(text.strip()) < MIN_TEXT_LENGTH:
        return f"Too Short (<{MIN_TEXT_LENGTH} chars)"
    return category or "Unknown"


def filter_element_dicts(elements: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    filtered_elements: List[Dict[str, Any]] = []
    removed_elements: List[Dict[str, Any]] = []
    discard_stats = Counter()
    kept_stats = Counter()
    page_stats: Dict[int, Dict[str, int]] = {}

    for raw_element in elements or []:
        if not isinstance(raw_element, dict):
            continue

        element = dict(raw_element)
        category = _element_category(element)
        text = _element_text(element)
        page_number = _element_page(element)

        page_entry = page_stats.setdefault(
            page_number,
            {
                "raw": 0,
                "kept": 0,
                "removed": 0,
            },
        )
        page_entry["raw"] += 1

        if category in KEEP_CATEGORIES and len(text) >= MIN_TEXT_LENGTH:
            filtered_elements.append(element)
            kept_stats[category] += 1
            page_entry["kept"] += 1
            continue

        reason = _remove_reason(category, text)
        discard_stats[reason] += 1
        page_entry["removed"] += 1
        removed_elements.append(
            {
                **element,
                "_preview_category": category,
                "_discard_reason": reason,
            }
        )

    summary = {
        "raw_count": sum(page["raw"] for page in page_stats.values()),
        "kept_count": len(filtered_elements),
        "removed_count": len(removed_elements),
        "kept_breakdown": dict(kept_stats),
        "removed_breakdown": dict(discard_stats),
        "page_stats": {
            str(page): stats
            for page, stats in sorted(page_stats.items(), key=lambda item: item[0])
        },
        "keep_categories": list(KEEP_CATEGORIES),
        "discard_categories": list(DISCARD_CATEGORIES),
        "min_text_length": MIN_TEXT_LENGTH,
    }

    return {
        "filtered_elements": filtered_elements,
        "removed_elements": removed_elements,
        "summary": summary,
    }


def filter_document_elements(input_file, output_file):
    print(f"📂 Loading elements from: {input_file}")

    try:
        elements = elements_from_json(filename=input_file)
    except FileNotFoundError:
        print(" Error: input file not found. Please run partition.py first.")
        sys.exit(1)

    print(f"   Loaded {len(elements)} raw elements.")

    filtered_elements = []
    discard_stats = Counter()

    print("\n🧹 Filtering noise...")

    for element in elements:
        category = element.category

        if category in KEEP_CATEGORIES:
            # Remove very small OCR artifacts
            if element.text and len(element.text.strip()) > 2:
                filtered_elements.append(element)
            else:
                discard_stats["Too Short (<2 chars)"] += 1
        else:
            discard_stats[category] += 1

    # Report
    print("\n📊 Filtering Report:")
    print(f"   Original Count: {len(elements)}")
    print(f"   Final Count:    {len(filtered_elements)}")
    print(f"   Removed:        {len(elements) - len(filtered_elements)}")

    print("\n   Discarded Items Breakdown:")
    for cat, count in discard_stats.items():
        print(f"    {cat}: {count} removed")

    # Save
    print(f"\n💾 Saving filtered elements to: {output_file}")
    elements_to_json(filtered_elements, filename=output_file)
    print(" Filtering complete. Ready for chunking.")


if __name__ == "__main__":
    # Usage:
    # python filter.py <elements.json> <filtered_elements.json>

    if len(sys.argv) != 3:
        print(" Usage: python filter.py <elements.json> <filtered_elements.json>")
        sys.exit(1)

    input_json = sys.argv[1]
    output_json = sys.argv[2]

    filter_document_elements(input_json, output_json)
