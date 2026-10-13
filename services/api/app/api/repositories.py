from fastapi import APIRouter

from app.repository.scanner import create_repository_scanner
from app.schemas.repositories import RepositoryScanRequest, RepositoryScanResponse


router = APIRouter(prefix="/api/repositories", tags=["repositories"])


@router.post("/scan", response_model=RepositoryScanResponse)
def scan_repository(body: RepositoryScanRequest) -> RepositoryScanResponse:
    """Run disk reads in FastAPI's worker pool; return metadata and actual counts."""
    result = create_repository_scanner().scan(body.repository)
    return RepositoryScanResponse.from_result(result)
