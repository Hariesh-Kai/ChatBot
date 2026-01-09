import sys
from collections import Counter
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


def filter_document_elements(input_file, output_file):
    print(f"📂 Loading elements from: {input_file}")

    try:
        elements = elements_from_json(filename=input_file)
    except FileNotFoundError:
        print("❌ Error: input file not found. Please run partition.py first.")
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
        print(f"   ❌ {cat}: {count} removed")

    # Save
    print(f"\n💾 Saving filtered elements to: {output_file}")
    elements_to_json(filtered_elements, filename=output_file)
    print("✅ Filtering complete. Ready for chunking.")


if __name__ == "__main__":
    # Usage:
    # python filter.py <elements.json> <filtered_elements.json>

    if len(sys.argv) != 3:
        print("❌ Usage: python filter.py <elements.json> <filtered_elements.json>")
        sys.exit(1)

    input_json = sys.argv[1]
    output_json = sys.argv[2]

    filter_document_elements(input_json, output_json)
