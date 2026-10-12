"""KI-Aufrufstellen der API: Ziel vorbereiten und Fehlschlaege als HTTP.

Wie eine Antwort des Modells verwertet wird (abgeschnitten, JSON aus content
oder dem Denktext derselben Antwort, Vertrag, Protokollzeile markieren,
ends_run, Stapel nach Fensterplatz), steht EINMAL im KI-Kern:
app/llm/answers.py - fuer API und Worker gleich. Hier steht nur, was die API
darueber hinaus braucht:

* prepare(): Einstellungen ueber ai_settings_service laden und Server, Modell
  und Fenster suchen (llm.resolve_target: bei ausgeschalteter KI ohne
  Serverkontakt ai_disabled). Auch ein Ausgang OHNE Modellaufruf bekommt eine
  llm_calls-Zeile mit benanntem Grund (Refused).
* require_target(): dasselbe Ziel fuer Aufrufstellen, deren Fehlschlag ein
  HTTP-Fehler ist (AIStepError).
* AIStepError: ein benannter Grund des Kerns als HTTP 503 mit
  {"detail": {"code": ..., "message": ...}}; das Web liest beide Felder
  (apps/web/src/lib/api.ts, ApiError.code/.detail).
* warning(): benannter Befund fuer eine Antwort, die trotzdem erfolgreich ist.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm import reasons
from app.llm.answers import Ready
from app.schemas.ai import AIWarning
from app.services import ai_settings_service
from app.services.ai_settings_service import ResolvedAISettings

__all__ = ["AIStepError", "Ready", "Refused", "prepare", "require_target", "warning"]


class AIStepError(HTTPException):
    """Ein KI-Schritt ist mit benanntem Grund gescheitert.

    Unterklasse von HTTPException, damit ein nicht abgefangener Fehler
    trotzdem als 503 mit Grund beim Client ankommt. Wer die llm_calls-Zeilen
    des gescheiterten Laufs behalten will (das ist der Normalfall), faengt
    den Fehler ab und antwortet mit response(): eine geworfene Ausnahme
    rollt die Request-Transaktion zurueck - und mit ihr das Protokoll."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": reason_code, "message": message},
        )
        self.reason_code = reason_code
        self.message = message

    def response(self) -> JSONResponse:
        """Dieselbe Fehlerantwort, ohne dass die Ausnahme die Transaktion
        verlaesst (die Protokollzeilen werden mit dem Request committet)."""
        return JSONResponse(status_code=self.status_code, content={"detail": self.detail})


def warning(code: str, message: str | None = None) -> AIWarning:
    """Benannter Befund fuer eine Antwort, die trotzdem erfolgreich ist
    (Teilergebnis, weggelassene Formulierung)."""
    return AIWarning(
        code=code, message=message or reasons.describe(code), level=reasons.level_of(code)
    )


async def require_target(settings: llm.LLMSettings) -> llm.Target:
    """Server, Modell und Fenster - oder AIStepError mit dem Grund.

    Bei ausgeschalteter KI spricht llm.resolve_target keinen Server an
    (ai_disabled). Ohne bekanntes Fenster wird nicht gesendet
    (model_context_unknown); ein gewaehltes, aber verschwundenes Modell wird
    nicht still ersetzt (model_missing)."""
    found = await llm.resolve_target(settings)
    if not found.ready or found.target is None:
        code = found.reason_code or reasons.MODEL_UNAVAILABLE
        raise AIStepError(code, found.message or reasons.describe(code))
    return found.target


@dataclass(frozen=True)
class Refused:
    """Kein Modellaufruf moeglich - mit benanntem Grund und Klartext. Die
    llm_calls-Zeile ist bereits geschrieben."""

    settings: ResolvedAISettings
    reason_code: str
    message: str
    model: str | None = None


async def prepare(
    session: AsyncSession, household_id: UUID, *, stage: str, prompt_version: str
) -> Ready | Refused:
    """Einstellungen, Ziel und Protokoll einer Aufrufstelle, deren Fehlschlag
    ein Ergebnis mit benanntem Grund ist (kein HTTP-Fehler)."""
    settings = await ai_settings_service.resolve(session, household_id)
    scope = llm.CallScope(
        session=session, household_id=household_id, stage=stage, prompt_version=prompt_version
    )
    found = await llm.resolve_target(settings)
    if found.ready and found.target is not None:
        return Ready(settings, found.target, scope)
    code = found.reason_code or reasons.MODEL_UNAVAILABLE
    message = found.message or reasons.describe(code)
    model = found.target.model if found.target is not None else None
    await llm.answers.log_without_call(
        scope,
        code,
        message,
        model=model,
        window=found.target.window if found.target is not None else None,
        base_url=found.base_url,
    )
    return Refused(settings, code, message, model)
