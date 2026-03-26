# backend/rag/preprocess.py

import os
import gc
import torch
from pathlib import Path
from typing import List, Generator

# PDF & System Libraries
from pypdf import PdfReader, PdfWriter
from unstructured.partition.pdf import partition_pdf

# Resource Planner (The Traffic Cop)
# Ensure you created backend/rag/resource_planner.py as discussed!
from backend.rag.resource_planner import get_optimal_strategy, limit_cpu_usage
from backend.rag.mode_profiles import normalize_rag_mode, get_preprocess_profile

def stream_pdf_to_elements(
    pdf_path: str,
    output_json: str,
    *,
    rag_mode: str = "balanced",
    pipeline_mode: str = "commit",
) -> Generator[List[dict], None, None]:
    """
    Generator that processes a PDF page-by-page to save RAM.
    
    Instead of loading the whole PDF into memory (which crashes RAM),
    this extracts 1 page -> processes it -> yields it -> deletes it.
    
    Args:
        pdf_path (str): Path to the source PDF.
        output_json (str): Target path (used to determine where to save images).
        
    Yields:
        List[dict]: A batch of processed elements (e.g., one page worth).
    """
    print(f"[PREPROCESS] Starting PDF parse: {pdf_path}")

    pdf_path = Path(pdf_path)
    output_json = Path(output_json)
    resolved_rag_mode = normalize_rag_mode(rag_mode)
    profile = get_preprocess_profile(
        resolved_rag_mode,
        pipeline_mode=str(pipeline_mode or "commit"),
    )
    
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # 1. Setup Image Output Directory (All pages save images here)
    image_output_dir = output_json.parent / "images"
    image_output_dir.mkdir(parents=True, exist_ok=True)

    # 2. Resource Planning
    file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
    strategy, cores, batch_size = get_optimal_strategy(file_size_mb)
    
    print(f"[PREPROCESS] Strategy: {strategy} | Cores: {cores} | Processing {file_size_mb:.2f} MB")
    print(
        "[PREPROCESS] RAG profile: "
        f"mode={resolved_rag_mode} pipeline_mode={pipeline_mode} "
        f"ocr_strategy={profile.get('strategy')}"
    )
    
    # 3. Pin CPU Cores (Prevents Windows Freeze)
    limit_cpu_usage(cores)

    # 4. Check Hardware Acceleration
    if torch.cuda.is_available() and not profile.get("prefer_quantized_hi_res", False):
        model_name = "yolox"
        print(f"[PREPROCESS] GPU detected. Using model: '{model_name}'")
    else:
        model_name = "yolox_quantized"
        print(f"[PREPROCESS] No GPU/high-speed mode. Using model: '{model_name}'")

    # 5. Open PDF Stream
    try:
        reader = PdfReader(str(pdf_path))
        total_pages = len(reader.pages)
        print(f"[PREPROCESS] Document has {total_pages} pages. Starting stream...")
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF: {e}") from e

    elements_buffer = []
    extracted_pages = 0
    failed_pages = 0

    # 6. Page-by-Page Processing Loop
    for i in range(total_pages):
        # A. Create a temporary single-page PDF
        page_writer = PdfWriter()
        page_writer.add_page(reader.pages[i])
        
        temp_filename = pdf_path.parent / f"temp_processing_{pdf_path.stem}_page_{i+1}.pdf"
        
        try:
            with open(temp_filename, "wb") as f:
                page_writer.write(f)
            
            # B. Process ONLY this small file (Low RAM usage)
            # This is the heavy lifting step.
            partition_kwargs = {
                "filename": str(temp_filename),
                "strategy": profile.get("strategy", "hi_res"),
                "languages": ["eng"],
            }

            if partition_kwargs["strategy"] == "hi_res":
                partition_kwargs["infer_table_structure"] = bool(
                    profile.get("infer_table_structure", True)
                )
                partition_kwargs["hi_res_model_name"] = model_name

                if profile.get("extract_images_in_pdf", False):
                    partition_kwargs["extract_images_in_pdf"] = True
                    partition_kwargs["extract_image_block_types"] = (
                        profile.get("extract_image_block_types") or []
                    )
                    partition_kwargs["extract_image_block_output_dir"] = str(image_output_dir)
                    partition_kwargs["extract_image_block_to_payload"] = False

            page_elements = partition_pdf(**partition_kwargs)
            
            # C. Enrich Metadata (Add correct page number)
            # Since we split the PDF, 'page_number' will always be 1. We must fix it.
            for el in page_elements:
                el_dict = el.to_dict()
                if "metadata" not in el_dict:
                    el_dict["metadata"] = {}
                
                # Override page number with the REAL loop index
                el_dict["metadata"]["page_number"] = i + 1
                elements_buffer.append(el_dict)
            
            # D. Yield if buffer is full (or simple page-by-page yield)
            # Yielding every page ensures the frontend sees progress fast.
            yield elements_buffer
            elements_buffer = [] 
            extracted_pages += 1
            
            # E. Force RAM Cleanup
            gc.collect()

        except Exception as e:
            print(f"Error processing page {i+1}: {e}")
            # Don't crash the whole job for one bad page
            failed_pages += 1
            continue
            
        finally:
            # F. Delete temp file immediately
            if temp_filename.exists():
                try:
                    temp_filename.unlink()
                except Exception:
                    pass
        
    # 7. Final Cleanup

    print(
        "[PREPROCESS] Streaming complete | "
        f"processed_pages={extracted_pages} failed_pages={failed_pages}"
    )
    gc.collect()
