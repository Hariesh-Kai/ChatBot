import json
from pathlib import Path
from typing import List

from unstructured.partition.pdf import partition_pdf


def pdf_to_elements(pdf_path: str, output_json: str) -> List[dict]:
    """
    Convert a PDF file into Unstructured elements JSON.

    This function is binary-safe and MUST be used before chunking.
    It prevents UTF-8 decode errors caused by reading PDFs as text.

    Args:
        pdf_path (str): Path to the PDF file
        output_json (str): Path where filtered_elements.json will be written

    Returns:
        List[dict]: List of element dictionaries
    """

    pdf_path = Path(pdf_path)
    output_json = Path(output_json)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    print(f"📄 Parsing PDF with Unstructured: {pdf_path.name}")

    # ----------------------------------
    # 1️⃣ Parse PDF (binary-safe)
    # ----------------------------------

    elements = partition_pdf(
        filename=str(pdf_path),
        strategy="hi_res",                # Best layout detection
        infer_table_structure=True,       # Required for tables
        extract_images_in_pdf=False,      # Faster, safe default
    )

    # ----------------------------------
    # 2️⃣ Serialize elements to JSON
    # ----------------------------------

    element_dicts = [el.to_dict() for el in elements]

    output_json.parent.mkdir(parents=True, exist_ok=True)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(
            element_dicts,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"✅ Extracted {len(element_dicts)} elements")
    print(f"💾 Saved filtered elements → {output_json}")

    return element_dicts
