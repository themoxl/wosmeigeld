import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.services import health_checks

router = APIRouter(prefix="/system", tags=["system"])

_CRITICAL_SERVICES = {"api", "database", "redis"}


@router.get("/instance-config")
async def instance_config(settings: Settings = Depends(get_settings)) -> dict[str, str | None]:
    """Fuer die Mobile-App bei "Eigener Server": liefert die Supabase-Werte
    DIESER Installation, damit der Nutzer nur die API-Adresse eintippen muss
    und nicht auch noch Supabase-URL/Anon-Key von Hand. Unauthentifiziert wie
    /system/health - vor dem Login gibt es noch keine Session, und Anon-Key
    plus Supabase-URL sind ohnehin oeffentlich (siehe Secrets-Management in
    docs/BETRIEB.md)."""
    public_url = settings.supabase_public_url or settings.supabase_url
    if not public_url or not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="instance_not_configured")
    return {
        "supabase_url": public_url,
        "supabase_anon_key": settings.supabase_anon_key,
        "instance_display_name": settings.instance_display_name or None,
    }


@router.get("/health")
async def system_health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    api_status = {"service": "api", "status": "ok", "checkedAt": health_checks.utc_now_iso()}

    database_status, redis_status, worker_status, storage_status, lm_studio_status = (
        await asyncio.gather(
        health_checks.check_database(settings),
        health_checks.check_redis(settings),
        health_checks.check_worker(settings),
        health_checks.check_storage(settings),
        health_checks.check_lm_studio(settings),
    )
    )

    services = [
        api_status,
        database_status,
        redis_status,
        worker_status,
        storage_status,
        lm_studio_status,
    ]

    overall = "ok"
    for entry in services:
        is_critical = entry["service"] in _CRITICAL_SERVICES
        if entry["status"] == "down" and is_critical:
            overall = "down"
            break
        if entry["status"] in ("down", "degraded", "unknown"):
            overall = "degraded"

    return {"status": overall, "services": services}
