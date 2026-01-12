# backend/api/render.py

import io
import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Response
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw

from backend.storage.minio_client import get_minio_client

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
    bucket = "kavin-documents" # Ensure this matches your upload bucket
    
    # 1. Construct Path
    object_path = f"{company_doc_id}/v{revision}/{file}"
    
    # 2. Download PDF (In-Memory)
    try:
        response = client.get_object(bucket, object_path)
        pdf_bytes = response.read()
        response.close()
        response.release_conn()
    except Exception as e:
        raise HTTPException(404, f"File not found: {e}")

    # 3. Convert Page to Image
    try:
        # 150 DPI is a good balance for screen viewing
        images = convert_from_bytes(
            pdf_bytes, 
            first_page=page, 
            last_page=page,
            fmt="png",
            dpi=150
        )
        if not images:
            raise ValueError("Page out of range")
        image = images[0]
    except Exception as e:
        raise HTTPException(500, f"Rendering failed: {e}")

    # 4. Draw Highlight
    if bbox:
        try:
            # Unstructured uses 72 DPI (Points) usually
            # We rendered at 150 DPI, so scale factor is ~2.08
            scale_factor = 150 / 72 
            
            points = json.loads(bbox)
            # Flatten points [[x,y], [x,y]] -> [(x,y), (x,y)]
            scaled_points = []
            for point in points:
                # Handle both [x,y] lists and (x,y) tuples
                x, y = point[0], point[1]
                scaled_points.append((x * scale_factor, y * scale_factor))
            
            draw = ImageDraw.Draw(image, "RGBA")
            # Draw Red Box
            draw.polygon(scaled_points, outline="red", width=5)
            # Optional: Semi-transparent fill
            # draw.polygon(scaled_points, fill=(255, 0, 0, 30))
            
        except Exception as e:
            print(f"⚠️ Highlight failed: {e}")

    # 5. Return Image
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)

    return Response(content=img_byte_arr.getvalue(), media_type="image/png")