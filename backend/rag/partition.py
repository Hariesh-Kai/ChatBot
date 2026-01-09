import time
import sys
from collections import Counter
from unstructured.partition.pdf import partition_pdf
from unstructured.staging.base import elements_to_json


def partition_document(input_file, output_file):
    print(f"🚀 Starting High-Resolution Partitioning for: {input_file}")
    print("   (This involves Computer Vision and OCR, so it may take a few minutes...)")

    start_time = time.time()

    try:
        elements = partition_pdf(
            filename=input_file,
            strategy="hi_res",
            infer_table_structure=True,
            chunking_strategy=None,
            hi_res_model_name="yolox"
        )

        duration = time.time() - start_time
        print(f"\n✅ Success! Processed {len(elements)} elements in {duration:.2f} seconds.")

        analyze_elements(elements)
        save_elements(elements, output_file)

    except Exception as e:
        print(f"\n❌ Error during partitioning: {e}")
        sys.exit(1)


def analyze_elements(elements):
    if not elements:
        return

    category_counts = Counter(el.category for el in elements)

    print("\n📊 Element Breakdown:")
    for category, count in category_counts.items():
        print(f"   - {category}: {count}")

    tables = [el for el in elements if el.category == "Table"]
    if tables:
        has_html = (
            hasattr(tables[0].metadata, "text_as_html")
            and tables[0].metadata.text_as_html
        )
        print(f"\n👀 Tables detected: {len(tables)}")
        print(f"   - HTML table structure present? {'✅ Yes' if has_html else '❌ No'}")
    else:
        print("\n⚠️ No tables detected in this document.")


def save_elements(elements, output_file):
    print(f"\n💾 Saving extracted elements to: {output_file}")
    elements_to_json(elements, filename=output_file)
    print("✅ elements.json saved successfully.")


if __name__ == "__main__":
    # Usage:
    # python partition.py <input_pdf_path> <output_json_path>

    if len(sys.argv) != 3:
        print("❌ Usage: python partition.py <input_pdf_path> <output_json_path>")
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_json = sys.argv[2]

    partition_document(input_pdf, output_json)
