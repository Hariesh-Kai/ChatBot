# backend/rag/preprocess.py

import json
import os
import torch  # ✅ Required to check for GPU
from pathlib import Path
from typing import List

from unstructured.partition.pdf import partition_pdf


def pdf_to_elements(pdf_path: str, output_json: str) -> List[dict]:
    """
    Convert a PDF file into Unstructured elements JSON.

    AUTO-DETECT MODE:
    - GPU Available? -> Uses 'yolox' (Best accuracy, heavy)
    - CPU Only?      -> Uses 'yolox_quantized' (Best speed, lighter)

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

    # Create a directory for extracted images to keep them organized
    image_output_dir = output_json.parent / "images"
    image_output_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------
    # 0️⃣ Hardware Auto-Detection
    # ----------------------------------
    if torch.cuda.is_available():
        model_name = "yolox"
        print(f"🚀 GPU Detected! Using high-accuracy model: '{model_name}'")
    else:
        model_name = "yolox_quantized"
        print(f"💻 No GPU found. Using CPU-optimized model: '{model_name}'")

    # ----------------------------------
    # 1️⃣ Parse PDF
    # ----------------------------------
    try:
        elements = partition_pdf(
            filename=str(pdf_path),
            
            # 🔥 STRATEGY: "hi_res" is mandatory for table structure
            strategy="hi_res",
            
            # 🔥 MODEL: Auto-selected based on hardware
            hi_res_model_name=model_name,
            
            # 🔥 TABLES: Keep this TRUE. If False, you lose table structure.
            infer_table_structure=True,
            
            # 🔥 OCR: Hint "eng" to reduce noise
            languages=["eng"],
            
            # 🔥 IMAGES: Extract text from images/charts inside the PDF
            extract_images_in_pdf=True,
            extract_image_block_types=["Image", "Table"], 
            extract_image_block_output_dir=str(image_output_dir),
            
            # Optimization: Don't base64 encode images in the JSON (too heavy)
            extract_image_block_to_payload=False,
        )
        print(f"✅ High-Res parsing successful using {model_name}.")

    except Exception as e:
        print(f"⚠️ High-Res Parsing failed ({e}). Falling back to 'fast' strategy...")
        print("   (Note: Tables may not be perfectly detected in fast mode)")
        
        # Fallback: Extremely fast, but breaks tables into text lines
        elements = partition_pdf(
            filename=str(pdf_path),
            strategy="fast",
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