"""KI-EINSCHAETZUNG zu gemeldeten Dublettenkandidaten.

ABGRENZUNG - was dieses Modul NICHT tut
---------------------------------------
Die Dublettenerkennung selbst bleibt vollstaendig deterministisch
(worker/imports/dedup.py): Referenz, Fingerabdruck, Verwendungszweck, Empfaenger.
Sie entscheidet, WELCHE Zeilen ueberhaupt in der Review-Inbox landen. Ein
Sprachmodell waere dafuer der falsche Weg - es muesste jedes Paar ansehen, waere
langsam, teuer und beantwortete dieselbe Frage bei jedem Lauf anders.

Dieses Modul setzt danach an, genau bei den Faellen, die die Erkennung selbst
nicht aufloesen kann: sind

    "1000000000001/PP.1234.PP/. Musterdienst, Ihr Einkauf bei Musterdienst"
    "PP.1234.PP MUSTERDIENST MEMBERSHIP"

derselbe Vorgang? Das ist eine Frage an einen Text, keine Rechenfrage - und es
sind die paar gemeldeten Zeilen, nicht jedes Paar.

VERBINDLICHE EIGENSCHAFTEN (Muster: import_mapping_ai.py)
---------------------------------------------------------
1. Die Einschaetzung wird NIE angewendet. Dieses Modul schreibt weder
   import_rows noch transactions - der einzige Schreibzugriff ist das
   Protokoll in llm_calls (schreibt der Gateway des KI-Kerns). Der Nutzer
   entscheidet weiter selbst; die Einschaetzung steht daneben und nennt ihren
   Grund.
2. Faellt das Modell aus oder liefert unbrauchbares JSON, ist das KEIN Fehler
   der Review-Inbox: es kommt `available=False` mit dem benannten Grund des
   Kerns (model_unavailable, model_input_too_large, model_budget_exhausted,
   model_output_unusable, ...), HTTP 200, und die Liste bleibt bedienbar.
3. Der Modellaufruf laeuft ausschliesslich ueber den Gateway (app/llm):
   Antwortbudget aus dem geladenen Fenster, Reasoning nach der
   Haushaltseinstellung, strukturierte Ausgabe mit {name, strict,
   schema}-Huelle und dem bewussten Rueckfall ohne Schema.
4. KEINE festen Grenzen fuer Paare oder Textlaengen: die angefragten Paare
   gehen in so wenige Aufrufe wie moeglich - je Stapel so viele, wie samt
   Antwortreserve ins Fenster passen. Ein Paar, das allein nicht passt, wird
   nicht gekuerzt, sondern mit model_input_too_large benannt. Wie viele Paare
   eine Anfrage hoechstens nennen darf, regelt die Eingabe
   (DuplicateAssessmentInput) - eine Bediengrenze, kein Budget.
5. Scheitert ein Stapel, bleiben die schon beurteilten Paare gueltig; die
   uebrigen kommen als 'unsure' mit dem Grund zurueck, warum sie nicht
   beurteilt wurden. Kein automatisches Wiederholen.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm.answers import (
    Message,
    Ready,
    ask,
    batches_by_fit,
    find_object,
    json_schema_format,
)
from app.llm.classification import mask_pii_text
from app.schemas.review import (
    DuplicateAssessment,
    DuplicateAssessmentResult,
    DuplicateVerdict,
)
from app.services.ai_answers import Refused, prepare

# Eigener Vertrag, eigene Version: aendert sich der Prompt, muss sich die
# Version mitaendern, sonst sind zwei unterschiedliche Fragen in llm_calls nicht
# mehr auseinanderzuhalten. v2: Schema mit {name, strict, schema}-Huelle (in v1
# griff es nie), Antwortform im Systemprompt, Felder ungekuerzt, Stapel nach
# Passung.
DUPLICATE_PROMPT_VERSION: Final = "dup-v2"
LLM_CALL_STAGE: Final = "duplicate_assess"
SCHEMA_NAME: Final = "duplicate_assessment"


def build_system_prompt() -> str:
    return (
        "Du pruefst Buchungen eines privaten Haushaltsbuchs auf Doppelerfassung.\n"
        "\n"
        "Du bekommst Paare: A ist eine neu eingelesene Zeile aus einem "
        "Kontoauszug, B eine bereits vorhandene Buchung. Beurteile fuer jedes "
        "Paar, ob es DERSELBE Zahlungsvorgang ist, der versehentlich zweimal "
        "erfasst wurde.\n"
        "\n"
        "Antworte je Paar mit einem Urteil:\n"
        "- 'same': derselbe Vorgang, A ist eine Doppelerfassung von B.\n"
        "- 'different': zwei echte, verschiedene Zahlungen.\n"
        "- 'unsure': aus den Angaben nicht zu entscheiden.\n"
        "\n"
        "Leitlinien:\n"
        "- Betrag und Datum sind bei allen Paaren gleich - sie unterscheiden "
        "nichts. Entscheidend sind Verwendungszweck und Empfaenger.\n"
        "- Dieselbe Gegenseite wird je nach Ausgabeformat unterschiedlich lang "
        "geschrieben; eine gekuerzte Schreibweise ist KEIN Unterschied.\n"
        "- Enthalten beide dieselbe Referenz-, Rechnungs- oder Mandatsnummer, "
        "ist es derselbe Vorgang.\n"
        "- Wiederkehrende Zahlungen gleicher Hoehe an verschiedene Personen "
        "(Taschengeld, Miete an zwei Vermieter) sind VERSCHIEDEN, auch wenn der "
        "Zweck aehnlich klingt. Nenne im Zweifel 'unsure' statt zu raten.\n"
        "- Im Zweifel 'unsure'. Ein falsches 'same' laesst eine echte Buchung "
        "verschwinden - das ist der teurere Fehler.\n"
        "\n"
        "Die Begruendung ist EIN kurzer deutscher Satz, der das entscheidende "
        "Merkmal nennt. Keine Floskeln, keine Wiederholung der Eingabe.\n"
        "\n"
        "Antworte ausschliesslich als JSON-Objekt in dieser Form, mit genau einem "
        'Eintrag je Paar: {"urteile":[{"nummer":1,"urteil":"same",'
        '"begruendung":"Dieselbe Referenz in beiden Zeilen.","konfidenz":0.9}]}'
    )


def build_user_prompt(paare: Sequence[dict[str, Any]]) -> str:
    """Paare eines Aufrufs. `nummer` ist die Position im Aufruf (1, 2, ...) -
    die Zuordnung zur Zeile passiert im Code, nicht im Modell."""
    zeilen = []
    for nummer, paar in enumerate(paare, start=1):
        a, b = paar["a"], paar["b"]
        zeilen.append(
            f"Paar {nummer}:\n"
            f"  A (neu):       Empfaenger={a['payee'] or '-'} | Zweck={a['purpose'] or '-'}\n"
            f"  B (vorhanden): Empfaenger={b['payee'] or '-'} | Zweck={b['purpose'] or '-'}\n"
            f"  beide: {a['booked_date']} | {a['amount_minor'] / 100:.2f} EUR"
        )
    return (
        f"{len(paare)} Paare. Beurteile jedes und antworte als JSON nach Schema.\n\n"
        + "\n\n".join(zeilen)
    )


def build_messages(paare: Sequence[dict[str, Any]]) -> list[Message]:
    return [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": build_user_prompt(paare)},
    ]


def build_json_schema(anzahl: int) -> dict[str, Any]:
    """Das Schema des Antwortobjekts - per response_format erzwungen, nicht
    erhofft (Huelle: json_schema_format).

    `nummer` statt der echten Zeilen-ID: eine UUID im Prompt kostet Token, laedt
    zu Tippfehlern ein und muesste ohnehin wieder zugeordnet werden. Die Nummer
    ist die Position im Aufruf - die Zuordnung passiert hier im Code.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["urteile"],
        "properties": {
            "urteile": {
                "type": "array",
                "minItems": anzahl,
                "maxItems": anzahl,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["nummer", "urteil", "begruendung", "konfidenz"],
                    "properties": {
                        "nummer": {"type": "integer"},
                        "urteil": {"type": "string", "enum": ["same", "different", "unsure"]},
                        "begruendung": {"type": "string"},
                        "konfidenz": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            }
        },
    }


def response_format_for(anzahl: int) -> dict[str, Any]:
    return json_schema_format(SCHEMA_NAME, build_json_schema(anzahl))


def _nicht_verfuegbar(
    grund: str, meldung: str, model_name: str | None = None
) -> DuplicateAssessmentResult:
    return DuplicateAssessmentResult(
        available=False,
        reason=grund,
        message=meldung,
        model_name=model_name,
        prompt_version=DUPLICATE_PROMPT_VERSION,
        assessments=[],
    )


def _maskiert(wert: str | None) -> str:
    """Ganzer Text, IBAN/Karten-/Kontonummern maskiert. Gekuerzt wird nicht:
    passt ein Paar nicht ins Fenster, sagt das der Gateway."""
    return mask_pii_text((wert or "").strip())


async def _lade_paare(
    session: AsyncSession, household_id: UUID, import_row_ids: list[UUID]
) -> list[dict[str, Any]]:
    """Holt je Zeile beide Seiten - die Staging-Zeile und die Buchung, gegen die
    sie als Dublette gilt.

    Nur offene Zeilen mit noch vorhandener Gegenbuchung: eine bereits
    entschiedene Zeile braucht keine Einschaetzung mehr, und ohne Gegenstueck
    gibt es nichts zu vergleichen.
    """
    rows = (
        await session.execute(
            text(
                "select r.id, r.booked_date, r.amount_minor, r.payee_raw, r.purpose, "
                "t.payee_label as gegen_empfaenger, t.purpose_raw as gegen_zweck "
                "from import_rows r "
                "join transactions t on t.id = r.duplicate_of_transaction_id "
                "  and t.household_id = r.household_id and not t.is_archived "
                "where r.household_id = :household_id "
                "and r.id = any(cast(:ids as uuid[])) "
                "and r.status = 'duplicate_possible' "
                "order by r.booked_date, r.id"
            ),
            {"household_id": str(household_id), "ids": [str(i) for i in import_row_ids]},
        )
    ).all()

    return [
        {
            "import_row_id": row.id,
            "a": {
                "payee": _maskiert(row.payee_raw),
                "purpose": _maskiert(row.purpose),
                "booked_date": row.booked_date,
                "amount_minor": row.amount_minor or 0,
            },
            "b": {
                "payee": _maskiert(row.gegen_empfaenger),
                "purpose": _maskiert(row.gegen_zweck),
            },
        }
        for row in rows
    ]


def _urteile_aus(objekt: dict[str, Any]) -> list[dict[str, Any]] | None:
    urteile = objekt.get("urteile")
    if not isinstance(urteile, list):
        return None
    return [eintrag for eintrag in urteile if isinstance(eintrag, dict)]


def lies_urteile(inhalt: str) -> list[dict[str, Any]] | None:
    """Liest die Urteilsliste aus einem Modelltext.

    Toleriert eine in Text eingebettete JSON-Struktur (ohne erzwungenes Schema
    liefern Modelle gern noch einen Satz davor), aber KEINE inhaltliche
    Abweichung - was nicht als Liste unter "urteile" ankommt, gilt als
    unbrauchbar.
    """
    if not isinstance(inhalt, str):
        return None
    return find_object(inhalt, _urteile_aus)


def _urteil(wert: object) -> DuplicateVerdict:
    """Alles, was nicht ausdruecklich 'same' oder 'different' ist, wird
    'unsure'. Ein unbekannter Wert darf nicht als Entscheidung durchgehen."""
    return wert if wert in ("same", "different") else "unsure"  # type: ignore[return-value]


def _konfidenz(wert: object) -> float:
    try:
        return max(0.0, min(1.0, float(wert)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _einschaetzungen(
    stapel: Sequence[dict[str, Any]], urteile: list[dict[str, Any]]
) -> list[DuplicateAssessment]:
    nach_nummer: dict[int, dict[str, Any]] = {}
    for urteil in urteile:
        nummer = urteil.get("nummer")
        if isinstance(nummer, str) and nummer.strip().isdigit():
            # Ohne erzwungenes Schema schreiben Modelle die Nummer gern als Text.
            nummer = int(nummer.strip())
        if isinstance(nummer, int) and not isinstance(nummer, bool):
            nach_nummer[nummer] = urteil
    ergebnis = []
    for nummer, paar in enumerate(stapel, start=1):
        urteil = nach_nummer.get(nummer, {})
        ergebnis.append(
            DuplicateAssessment(
                import_row_id=paar["import_row_id"],
                verdict=_urteil(urteil.get("urteil")),
                reason=str(urteil.get("begruendung") or "").strip()
                or "Das Modell hat zu diesem Paar nichts geantwortet.",
                confidence=_konfidenz(urteil.get("konfidenz")),
            )
        )
    return ergebnis


def _nicht_beurteilt(paar: dict[str, Any], meldung: str) -> DuplicateAssessment:
    return DuplicateAssessment(
        import_row_id=paar["import_row_id"],
        verdict="unsure",
        reason=f"Nicht beurteilt: {meldung}",
        confidence=0.0,
    )


async def assess_duplicates(
    session: AsyncSession, household_id: UUID, import_row_ids: list[UUID]
) -> DuplicateAssessmentResult:
    """Beurteilt gemeldete Dublettenkandidaten. Jeder KI-seitige Fehlschlag
    wird zu available=False mit benanntem Grund, damit die Review-Inbox davon
    unberuehrt bleibt; scheitert erst ein spaeterer Stapel, bleiben die schon
    beurteilten Paare erhalten (available=True, reason = Grund des Abbruchs)."""
    paare = await _lade_paare(session, household_id, import_row_ids)
    if not paare:
        return _nicht_verfuegbar(
            "no_candidates",
            "Zu den ausgewaehlten Zeilen gibt es nichts zu beurteilen: sie sind "
            "bereits entschieden, oder die verglichene Buchung steht nicht mehr "
            "im Bestand.",
        )

    bereit = await prepare(
        session, household_id, stage=LLM_CALL_STAGE, prompt_version=DUPLICATE_PROMPT_VERSION
    )
    if isinstance(bereit, Refused):
        return _nicht_verfuegbar(bereit.reason_code, bereit.message, bereit.model)
    return await _beurteile(bereit, paare)


async def _beurteile(bereit: Ready, paare: list[dict[str, Any]]) -> DuplicateAssessmentResult:
    modell = bereit.target.model
    messung = await llm.measurement_for(bereit.target, bereit.settings, bereit.scope)
    stapelliste = batches_by_fit(paare, build_messages, ready=bereit, measurement=messung)

    einschaetzungen: list[DuplicateAssessment] = []
    beurteilt = 0
    abbruch: tuple[str, str] | None = None
    for stapel in stapelliste:
        if abbruch is not None:
            einschaetzungen.extend(_nicht_beurteilt(paar, abbruch[1]) for paar in stapel)
            continue
        antwort = await ask(
            bereit,
            llm.ChatRequest(
                messages=build_messages(stapel),
                response_format=response_format_for(len(stapel)),
            ),
            accept=_urteile_aus,
            measurement=messung,
        )
        if antwort.value is None:
            abbruch = (
                antwort.reason_code or llm.reasons.MODEL_OUTPUT_UNUSABLE,
                antwort.message or llm.reasons.describe(llm.reasons.MODEL_OUTPUT_UNUSABLE),
            )
            einschaetzungen.extend(_nicht_beurteilt(paar, abbruch[1]) for paar in stapel)
            continue
        einschaetzungen.extend(_einschaetzungen(stapel, antwort.value))
        beurteilt += len(stapel)

    if abbruch is None:
        return DuplicateAssessmentResult(
            available=True,
            reason="ok",
            message=f"{len(einschaetzungen)} Paare beurteilt.",
            model_name=modell,
            prompt_version=DUPLICATE_PROMPT_VERSION,
            assessments=einschaetzungen,
        )
    if beurteilt == 0:
        return _nicht_verfuegbar(abbruch[0], abbruch[1], modell)
    return DuplicateAssessmentResult(
        available=True,
        reason=abbruch[0],
        message=(
            f"{beurteilt} von {len(paare)} Paaren beurteilt (aufgeteilt in "
            f"{len(stapelliste)} Stapel nach Platz im Kontextfenster); die uebrigen "
            f"nicht: {abbruch[1]}"
        ),
        model_name=modell,
        prompt_version=DUPLICATE_PROMPT_VERSION,
        assessments=einschaetzungen,
    )
