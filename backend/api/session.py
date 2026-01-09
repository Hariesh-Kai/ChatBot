from fastapi import APIRouter
import uuid

router = APIRouter(prefix="/session", tags=["Session"])

@router.post("/new")
def new_session():
    return {"session_id": str(uuid.uuid4())}
