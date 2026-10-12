import asyncio
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.api.v1.routes import health
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.tracing import configure_tracing
from app.middleware.request_id import RequestIdMiddleware

if sys.platform == "win32":
    # psycopg (async) kann nicht mit Windows' Default-ProactorEventLoop
    # arbeiten. Im Linux-Container ist dies ein No-Op.
    #
    # ACHTUNG - diese Zeile greift nur, wenn dieses Modul importiert wird,
    # BEVOR ein Event-Loop existiert: also beim programmatischen Import
    # (Tests, run_dev.py). Unter "python -m uvicorn app.main:app" ist sie
    # wirkungslos, weil uvicorn seinen Loop erzeugt (Server.run ->
    # asyncio.run), bevor es die Anwendung importiert - die Policy kaeme dann
    # zu spaet und jede DB-Abfrage scheiterte mit
    # "Psycopg cannot use the 'ProactorEventLoop' to run in async mode".
    # Der native Windows-Entwicklungsweg laeuft deshalb ueber
    # services/api/run_dev.py, das die Policy vor uvicorn.run() setzt (siehe
    # services/api/README.md).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="Finanzplaner API",
        version="0.1.0",
        description="BFF fuer Web und Mobile. Siehe FINANZ_APP_ARCHITEKTURPLAN.md.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Zuletzt hinzugefuegt = aeusserste Schicht (Starlette wrappt Middleware
    # in umgekehrter add_middleware()-Reihenfolge) - die Request-ID muss vor
    # CORS/Routing gesetzt sein, damit sie in jeder Logzeile des Requests
    # verfuegbar ist.
    app.add_middleware(RequestIdMiddleware)

    # Bildet eine Ablehnung durch RLS (SQLSTATE 42501) auf 403 statt 500 ab,
    # siehe app/core/errors.py.
    register_exception_handlers(app)

    # Root-level Liveness-Probe fuer Docker-Healthcheck und Reverse Proxy.
    app.include_router(health.router)
    app.include_router(v1_router, prefix="/v1")

    configure_tracing(app, settings)

    return app


app = create_app()
