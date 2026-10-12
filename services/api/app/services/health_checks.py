import time
from datetime import UTC, datetime

import httpx
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.services.ai_service import get_candidate_urls
from app.services.health_document_storage import HealthDocumentStorage

CHECK_TIMEOUT_SECONDS = 2.0
WORKER_HEARTBEAT_KEY = "worker:heartbeat"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def check_database(settings: Settings) -> dict[str, str]:
    # SQLAlchemy's "postgresql+psycopg" dialect (psycopg 3) supports async
    # engines natively, no separate async driver name required.
    try:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return {"service": "database", "status": "ok", "checkedAt": utc_now_iso()}
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        return {
            "service": "database",
            "status": "down",
            "detail": str(exc),
            "checkedAt": utc_now_iso(),
        }


async def check_redis(settings: Settings) -> dict[str, str]:
    try:
        client = redis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, socket_timeout=CHECK_TIMEOUT_SECONDS
        )
        await client.ping()
        await client.aclose()
        return {"service": "redis", "status": "ok", "checkedAt": utc_now_iso()}
    except Exception as exc:  # noqa: BLE001
        return {
            "service": "redis",
            "status": "down",
            "detail": str(exc),
            "checkedAt": utc_now_iso(),
        }


async def check_worker(settings: Settings) -> dict[str, str]:
    try:
        client = redis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, socket_timeout=CHECK_TIMEOUT_SECONDS
        )
        raw = await client.get(WORKER_HEARTBEAT_KEY)
        await client.aclose()
        if raw is None:
            return {
                "service": "worker",
                "status": "down",
                "detail": "kein Heartbeat gefunden",
                "checkedAt": utc_now_iso(),
            }
        age_seconds = time.time() - float(raw)
        stale_after = settings.worker_heartbeat_interval_seconds * 3
        if age_seconds > stale_after:
            return {
                "service": "worker",
                "status": "degraded",
                "detail": f"letzter Heartbeat vor {age_seconds:.0f}s",
                "checkedAt": utc_now_iso(),
            }
        return {"service": "worker", "status": "ok", "checkedAt": utc_now_iso()}
    except Exception as exc:  # noqa: BLE001
        return {
            "service": "worker",
            "status": "down",
            "detail": str(exc),
            "checkedAt": utc_now_iso(),
        }


async def check_lm_studio(settings: Settings) -> dict[str, str]:
    # Dieselbe Kandidatenliste wie Klassifikation und KI-Statusseite
    # (ai_service.get_candidate_urls): konfigurierte URL zuerst, dann
    # host.docker.internal/127.0.0.1/localhost. Vorher pruefte nur diese
    # Funktion ausschliesslich settings.lm_studio_base_url - lauschte LM Studio
    # bloss auf 127.0.0.1, meldete der Systemstatus deshalb "degraded",
    # waehrend die KI-Seite ueber die Discovery laengst "bereit" sagte und
    # Klassifikationslaeufe normal durchliefen. Zwei Statusanzeigen, die sich
    # widersprechen, sind schlimmer als eine, die fehlt.
    last_error = "keine Kandidaten-URL konfiguriert"
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        for base_url in get_candidate_urls():
            try:
                response = await client.get(f"{base_url.rstrip('/')}/models")
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - naechste Kandidaten-URL
                last_error = f"{base_url}: {exc}"
                continue
            detail = (
                None if base_url == settings.lm_studio_base_url else f"erreichbar ueber {base_url}"
            )
            entry = {"service": "lm_studio", "status": "ok", "checkedAt": utc_now_iso()}
            if detail is not None:
                # Die abweichende URL bleibt sichtbar: der Dienst laeuft, aber
                # LM_STUDIO_BASE_URL zeigt woanders hin.
                entry["detail"] = detail
            return entry

    # LM Studio ist optional: deterministische Regeln funktionieren ohne KI
    # weiter (Architekturplan Abschnitt 19, Risiko "LM Studio ist nicht
    # verfuegbar"). Ein fehlender Dienst ist daher "degraded", nicht "down".
    return {
        "service": "lm_studio",
        "status": "degraded",
        "detail": last_error,
        "checkedAt": utc_now_iso(),
    }


async def check_storage(settings: Settings) -> dict[str, str]:
    status, detail = await HealthDocumentStorage(settings).check()
    return {
        "service": "storage",
        "status": status,
        "detail": detail,
        "checkedAt": utc_now_iso(),
    }
