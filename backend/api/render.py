# backend/api/render.py

import io
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Response
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw

#  NEW: Import _get_config to read bucket from env safely
from backend.storage.minio_client import get_minio_client, _get_config

router = APIRouter(prefix="/render", tags=["Render"])

@router.get("/image")
def render_page_image(
    file: str = Query(..., description="Filename in MinIO"),
    page: int = Query(1, ge=1, description="Page number"),
    bbox: Optional[str] = Query(None, description="JSON coords"),
    company_doc_id: str = Query(..., description="Folder ID"),
    revision: int = Query(..., description="Version number")
):
    """
    Streams a specific PDF page as a PNG with optional highlighting.
    """
    client = get_minio_client()
    
    #  FIX: Get dynamic bucket name from config instead of hardcoding
    try:
        conf = _get_config()
        bucket = conf["bucket"]
    except Exception:
        bucket = "kavin-documents" # Fallback if config fails
    
    # 1. Construct Path
    object_path = f"{company_doc_id}/v{revision}/{file}"
    
    # 2. Download PDF (In-Memory)
    try:
        response = client.get_object(bucket, object_path)
        pdf_bytes = response.read()
        response.close()
        response.release_conn()
    except Exception as e:
        print(f" MinIO Download Error: {e}")
        # Helpful error message for debugging
        raise HTTPException(404, f"File not found in bucket '{bucket}': {object_path}. Error: {e}")

    # 3. Convert Page to Image
    try:
        #  If on Windows without Poppler in PATH, you might need to uncomment and set this:
        # poppler_path = r"C:\Program Files\poppler\bin"
        
        images = convert_from_bytes(
            pdf_bytes, 
            first_page=page, 
            last_page=page,
            fmt="png",
            dpi=150
            # poppler_path=poppler_path # Uncomment if you get "poppler not installed" error
        )
        if not images:
            raise ValueError("Page out of range")
        image = images[0]
    except Exception as e:
        print(f" PDF Rendering Error: {e}")
        raise HTTPException(500, f"Rendering failed. Is Poppler installed and in PATH? Error: {e}")

    # 4. Draw Highlight
    if bbox and bbox != "null" and bbox != "":
        try:
            # Unstructured uses 72 DPI (Points), We rendered at 150 DPI
            scale_factor = 150 / 72 
            
            points = json.loads(bbox)
            
            #  FIX: Handle Nested List structures from Unstructured safely
            # Sometimes it returns [[x,y], [x,y]] or [[x,y,x,y]]
            scaled_points = []
            
            if points and (isinstance(points[0], list) or isinstance(points[0], tuple)):
                for point in points:
                    if len(point) >= 2:
                        x, y = point[0], point[1]
                        scaled_points.append((x * scale_factor, y * scale_factor))
            
            draw = ImageDraw.Draw(image, "RGBA")
            
            # Only draw if we have a valid shape (at least 3 points for a polygon)
            if len(scaled_points) > 2:
                # Draw Red Box with semi-transparent fill
                draw.polygon(scaled_points, outline="red", width=5, fill=(255, 0, 0, 40))
            
        except Exception as e:
            print(f"Highlight failed (rendering image without highlight): {e}")

    # 5. Return Image
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)

    return Response(content=img_byte_arr.getvalue(), media_type="image/png")