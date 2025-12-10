from fastapi import APIRouter

router = APIRouter()


@router.post("/telegram", response_model=str)
def post_telegram(payload):
    print(payload)
    return f"Received : {str(payload)}"
