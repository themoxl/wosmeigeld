"""KI-Kategorievorschlaege: Vorschau und bestaetigtes Anwenden.

Dritter Router unter dem Praefix `/categories` neben `routes/categories.py`
(CRUD) und `routes/category_operations.py` (Lebenszyklus). Begruendung wie
dort: eine gemeinsame Datei waere ein Sammelbecken, obwohl die Mechanik eine
andere ist - hier laeuft ein Modellaufruf gegen LM Studio, der je nach Bestand
mehrere Sekunden dauert und ohne geladenes Modell mit 503 endet.

Beide Endpunkte liegen unter literalen Pfaden (`/suggestions/...`) und koennen
deshalb nicht mit `/categories/{category_id}/...` aus dem Lebenszyklus-Router
kollidieren. Der Router wird trotzdem vor jenem eingebunden, damit die
Reihenfolge auch dann eindeutig bleibt, wenn dort spaeter Musterrouten
hinzukommen.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_household_editor
from app.auth.jwt import AuthenticatedUser
from app.schemas.category import (
    CategorySuggestionApply,
    CategorySuggestionPreview,
    CategorySuggestionResult,
)
from app.services import category_suggestion_service
from app.services.ai_answers import AIStepError

router = APIRouter(prefix="/categories", tags=["categories"])

HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.post("/suggestions/preview", response_model=CategorySuggestionPreview)
async def preview_category_suggestions(
    ctx: HouseholdEditorSession, user: CurrentUser
) -> CategorySuggestionPreview | JSONResponse:
    """Clustert die noch nicht zugeordneten Buchungen und laesst das Modell je
    Cluster entscheiden: vorhandene Kategorie oder Vorschlag fuer eine neue.

    Ohne Modell oder ohne eine einzige verwertbare Modellantwort antwortet der
    Dienst mit 503 und benanntem Grund ({"detail": {"code", "message"}}, Codes
    des KI-Kerns) statt mit einer leeren Vorschlagsliste - eine leere Liste
    waere die Aussage "es fehlt nichts" und damit schlicht falsch. Die
    Fehlerantwort wird zurueckgegeben statt geworfen, damit die
    llm_calls-Zeilen des gescheiterten Laufs mit der Transaktion bestehen
    bleiben."""
    session, household_id = ctx
    try:
        return await category_suggestion_service.build_preview(session, household_id, user.id)
    except AIStepError as exc:
        return exc.response()


@router.post("/suggestions/apply", response_model=CategorySuggestionResult)
async def apply_category_suggestions(
    payload: CategorySuggestionApply, ctx: HouseholdEditorSession, user: CurrentUser
) -> CategorySuggestionResult:
    """Legt die bestaetigten Kategorien an und ordnet die Buchungen der
    jeweiligen Cluster zu. Nicht bestaetigte Vorschlaege bleiben folgenlos."""
    session, household_id = ctx
    return await category_suggestion_service.apply(session, household_id, user.id, payload)
