"""Globale Abbildung von Datenbankfehlern auf HTTP-Statuscodes.

Alle Requests laufen unter `SET LOCAL ROLE authenticated` (siehe
app/auth/dependencies.py), RLS greift also wirklich - so ist es in
docs/SICHERHEIT.md auch als gewaehltes Mittel beschrieben. Lehnt Postgres
einen Zugriff ab, kommt das als SQLSTATE 42501 (insufficient_privilege) hoch,
u. a. bei "new row violates row-level security policy". Ohne Handler wuerde
daraus ein 500, obwohl es fachlich ein 403 ist.

Bewusst wird nur der Statuscode uebersetzt und eine feste, kurze Kennung
ausgeliefert: die Originalmeldung von Postgres nennt Tabellen, Spalten und
teilweise Zeileninhalte (also potenziell Kontodaten) und darf den Client
nicht erreichen. Fuer die Diagnose bleibt der Logeintrag, der ueber
app/core/logging.py durch die PII-Redaction laeuft.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# Postgres-Fehlerklasse 42501: der aktive Rollen-/RLS-Kontext erlaubt die
# Operation nicht.
INSUFFICIENT_PRIVILEGE_SQLSTATE = "42501"

# Fehlerkennung fuer den Client - bewusst generisch und frei von Details
# darueber, welche Tabelle oder welche Zeile betroffen war.
INSUFFICIENT_PRIVILEGE_DETAIL = "insufficient_privilege"


def extract_sqlstate(exc: BaseException) -> str | None:
    """Liest den SQLSTATE aus einem von SQLAlchemy verpackten Treiberfehler.

    psycopg3 fuehrt ihn als `sqlstate`, psycopg2 als `pgcode` - beide werden
    beruecksichtigt, damit die Abbildung nicht an einem Treiberwechsel haengt.
    """
    original: Any = getattr(exc, "orig", None)
    if original is None:
        original = exc
    for attribute in ("sqlstate", "pgcode"):
        value = getattr(original, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


async def database_permission_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Uebersetzt eine Ablehnung durch RLS bzw. fehlende Tabellenrechte in 403.

    Jeder andere Datenbankfehler wird unveraendert weitergereicht und bleibt
    damit ein 500 - hier wird bewusst nichts geschluckt, was auf einen echten
    Defekt hindeutet.
    """
    if extract_sqlstate(exc) != INSUFFICIENT_PRIVILEGE_SQLSTATE:
        raise exc

    logger.warning(
        "Datenbank hat den Zugriff verweigert (SQLSTATE %s).",
        INSUFFICIENT_PRIVILEGE_SQLSTATE,
        extra={"http_method": request.method, "http_path": request.url.path},
    )
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": INSUFFICIENT_PRIVILEGE_DETAIL},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Haengt die Handler an die Anwendung (aufgerufen aus create_app())."""
    app.add_exception_handler(DBAPIError, database_permission_error_handler)
