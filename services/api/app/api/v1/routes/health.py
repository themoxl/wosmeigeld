from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Reine Liveness-Probe ohne Abhaengigkeiten, fuer Container-Healthchecks."""
    return {"status": "ok", "service": "api"}
