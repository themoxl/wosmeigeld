"""KI-gestuetzter Spalten-Mapping-VORSCHLAG fuer Importlaeufe.

Abgrenzung zur bestehenden Formaterkennung: das Erkennen und Parsen der
Bankdatei bleibt vollstaendig deterministisch (worker/imports/parsers/* plus
gespeicherte Mapping-Profile). Dieses Modul aendert daran nichts. Es fragt
lediglich das lokale Modell, welche Quellspalte zu welchem kanonischen Feld
passt, und liefert das Ergebnis als Vorschlag zurueck.

Verbindliche Eigenschaften:

1. Der Vorschlag wird NIE automatisch angewendet. Diese Datei schreibt weder
   import_mapping_profiles noch import_runs noch import_rows - der einzige
   Schreibzugriff ist das Protokoll in llm_calls. Der Nutzer bestaetigt den
   Vorschlag im Mapping-Dialog; deterministischer Parser und passendes
   gespeichertes Profil behalten den Vorrang als Vorbelegung.
2. Faellt das Modell aus oder liefert unbrauchbares JSON, ist das KEIN Fehler
   des Imports: es wird `available=False` mit dem benannten Grund des
   KI-Kerns geliefert (model_unavailable, model_input_too_large,
   model_budget_exhausted, model_output_unusable, ...), HTTP-Status bleibt
   200, der bisherige Ablauf funktioniert unveraendert.
3. Der Modellaufruf laeuft ausschliesslich ueber den Gateway des KI-Kerns
   (app/llm): Antwortbudget aus dem GELADENEN Fenster und dem Reserveanteil
   der Haushaltseinstellung, strukturierte Ausgabe mit dem bewussten
   Rueckfall ohne Schema, llm_calls-Zeile mit eigener prompt_version.
4. Reasoning nach der Haushaltseinstellung - ohne eigenes Denkbudget, ohne
   Eskalation und ohne Formatierer-Zweitaufruf mit ausgeschaltetem Denken
   (E1/E2). Steht das JSON nur im Denktext DERSELBEN Antwort, wird es daraus
   uebernommen (llm.answers.ask); reicht der freie Platz im
   Fenster nicht, heisst das model_budget_exhausted mit Hinweis auf die
   Einstellung.
5. Die Spaltenliste wird nicht gekuerzt: passt sie samt drei Beispielzeilen
   nicht ins Fenster, lehnt der Gateway mit model_input_too_large und
   Aufschluesselung ab. Fest sind nur die Datensparsamkeits-Grenzen unten.
"""

import json
from datetime import datetime
from typing import Any, Final, cast, get_args
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm.answers import (
    Message,
    Ready,
    ask,
    find_object,
    json_schema_format,
    log_without_call,
)
from app.llm.classification import mask_pii_text
from app.schemas.import_platform import (
    ImportMappingSuggestion,
    MappingSuggestionReason,
)
from app.services.ai_answers import Refused, prepare
from app.services.import_service import ImportRunNotFoundError

# Eigene Promptversion: der Vertrag hier ist ein anderer als der
# Klassifikationsvertrag (ai_service.PROMPT_VERSION). Beide landen in
# llm_calls.prompt_version, unterscheidbar zusaetzlich ueber llm_calls.stage.
MAPPING_PROMPT_VERSION = "map-v1"
LLM_CALL_STAGE = "mapping_suggest"

# PII-Budget. Ein Bankexport ist hochsensibel, deshalb gehen so wenige echte
# Daten wie moeglich an das Modell:
#
# - GENAU 3 Beispielzeilen. Gefragt ist die STRUKTUR (welche Spalte ist das
#   Datum, welche der Betrag), nicht der Inhalt. Eine einzelne Zeile reicht
#   dafuer nicht: "01.02.2026" ist mit einer Zeile nicht von "%m.%d.%Y" zu
#   unterscheiden, und eine einzelne Zeile zeigt oft nur ein Vorzeichen bzw.
#   keinen Nachkommaanteil. Drei Zeilen loesen beide Mehrdeutigkeiten in der
#   Praxis auf (verschiedene Tageszahlen, meist Soll UND Haben). Jede weitere
#   Zeile fuegt keine Strukturinformation mehr hinzu, sondern nur weitere
#   echte Buchungen - also mehr Risiko ohne Nutzen.
# - Jede Zelle laeuft durch mask_pii_text() (IBANs, Konto-/Kartennummern) und
#   wird auf MAX_CELL_CHARS gekuerzt; lange Verwendungszwecke tragen zur
#   Spaltenerkennung nichts bei und sind weitere echte Kontodaten.
# Beides sind Datensparsamkeits-Grenzen (E8), kein Antwort- oder Promptbudget.
MAX_SAMPLE_ROWS = 3  # fest: Datensparsamkeit - drei Beispielzeilen reichen fuer die Struktur
MAX_CELL_CHARS = 120  # fest: Datensparsamkeit - echte Kontodaten je Zelle nur so viel wie noetig

# Antwortfelder des Modells -> kanonisches Mappingfeld.
_COLUMN_KEYS: dict[str, str] = {
    "booked_date_column": "booked_date",
    "amount_column": "amount",
    "payee_column": "payee",
    "purpose_column": "purpose",
}

# Ohne Datum UND Betrag ist ein Mapping nicht verwendbar (siehe
# parse_with_mapping in den Parsern) - dann gilt der Vorschlag als
# unvollstaendig.
_REQUIRED_FIELDS = ("booked_date", "amount")

_NO_COLUMN = ""  # Enum-Wert fuer "keine passende Spalte"


# Antwortnamen fuer response_format (json_schema.name).
SCHEMA_NAME: Final = "import_column_mapping"

# Jeder Grund des KI-Kerns ist auch ein Grund dieses Vorschlags (der Test
# test_mapping_reasons_cover_every_core_reason haelt das fest).
_REASONS: Final = frozenset(get_args(MappingSuggestionReason))


# ---------------------------------------------------------------------------
# Prompt- und Schemavertrag
# ---------------------------------------------------------------------------


# Antwortform im Klartext. Auf dem Rueckfall ohne Schema (Server lehnt
# response_format ab) ist sie die einzige Vorgabe.
_JSON_STRUCTURE_HINT = (
    "Antworte AUSSCHLIESSLICH als gueltiges JSON-Objekt ohne weiteren Text mit "
    "folgender Struktur:\n"
    "{\n"
    '  "booked_date_column": "Buchungstag",\n'
    '  "amount_column": "Betrag",\n'
    '  "payee_column": "Empfaenger",\n'
    '  "purpose_column": "Verwendungszweck",\n'
    '  "currency": "EUR",\n'
    '  "date_format": "%d.%m.%Y",\n'
    '  "decimal_separator": ",",\n'
    '  "confidence": 0.9\n'
    "}"
)


def build_mapping_system_prompt(reasoning_enabled: bool) -> str:
    """Systemprompt. Beschreibt die JSON-Struktur explizit, weil sie auf dem
    Rueckfall ohne Schema die einzige Vorgabe ist.

    `reasoning_enabled` schaltet die Anti-Nochmal-pruefen-Anweisung zu. Sie
    steht bewusst NUR bei aktivem Reasoning im Prompt: ohne Denken gibt es
    keine Selbstpruefungsschleife, die sie abbrechen koennte - sie waere dort
    reiner Fuelltext. Mit Reasoning ist sie dagegen der Unterschied zwischen
    einer Antwort und einem Antwortbudget, das im Durchdenken der
    Spaltenkandidaten aufgebraucht wird. Sie ist Prompttext, kein
    KI-Parameter: ob gedacht wird, entscheidet die Haushaltseinstellung."""
    rules = [
        "Du bist ein Assistent fuer den Import von Bankumsaetzen in eine "
        "Haushaltsbuch-App.",
        "Du bekommst die Spaltennamen einer Umsatzdatei und wenige, maskierte "
        "Beispielzeilen. Ordne den kanonischen Feldern die jeweils passende "
        "Quellspalte zu.",
        "Regeln:",
        "- Verwende als Spaltennamen AUSSCHLIESSLICH Werte aus der uebergebenen "
        'Spaltenliste. Gibt es keine passende Spalte, antworte mit "".',
        "- date_format ist ein Python-strftime-Muster passend zu den Datumswerten "
        '(z. B. "%d.%m.%Y" oder "%Y-%m-%d").',
        '- decimal_separator ist "," oder "." - so, wie es in der Betragsspalte '
        "vorkommt.",
        '- currency ist ein ISO-4217-Code (z. B. "EUR") oder "", falls die Datei '
        "keine Waehrung nennt.",
        "- confidence ist deine Sicherheit zwischen 0 und 1.",
    ]
    if reasoning_enabled:
        rules.append(
            "- Entscheide jede Spalte genau EINMAL und pruefe eine bereits "
            "getroffene Zuordnung NICHT ein zweites Mal nach. Sobald jedes Feld "
            "eine Spalte hat, hoere auf zu ueberlegen und gib sofort "
            "ausschliesslich das geforderte JSON-Objekt aus."
        )
    rules.append(_JSON_STRUCTURE_HINT)
    return "\n".join(rules)


def build_mapping_user_prompt(columns: list[str], masked_rows: list[dict[str, str]]) -> str:
    """Userprompt aus BEREITS maskierten Zeilen - die Maskierung passiert in
    build_masked_samples(), damit kein Aufrufer sie versehentlich umgeht."""
    return (
        "Spalten der Datei:\n"
        f"{json.dumps(columns, ensure_ascii=False)}\n\n"
        f"Beispielzeilen (maskiert, {len(masked_rows)} Stueck):\n"
        f"{json.dumps(masked_rows, ensure_ascii=False)}"
    )


def build_mapping_messages(
    columns: list[str], masked_rows: list[dict[str, str]], *, reasoning_enabled: bool
) -> list[Message]:
    return [
        {"role": "system", "content": build_mapping_system_prompt(reasoning_enabled)},
        {"role": "user", "content": build_mapping_user_prompt(columns, masked_rows)},
    ]


def build_mapping_json_schema(columns: list[str]) -> dict[str, Any]:
    """JSON-Schema des Antwortobjekts (Huelle: mapping_response_format). Die
    Spaltenfelder sind als Enum ueber die tatsaechlich vorhandenen Spalten
    (plus "" fuer 'keine') modelliert - dieselbe Invariante wie bei der
    category_id in der Klassifikation: ein Server mit Grammar-Constraints kann
    gar keine erfundene Spalte liefern."""
    column_enum = {"type": "string", "enum": [*columns, _NO_COLUMN]}
    return {
        "type": "object",
        "properties": {
            "booked_date_column": dict(column_enum),
            "amount_column": dict(column_enum),
            "payee_column": dict(column_enum),
            "purpose_column": dict(column_enum),
            "currency": {"type": "string"},
            "date_format": {"type": "string"},
            "decimal_separator": {"type": "string", "enum": [",", ".", _NO_COLUMN]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "booked_date_column",
            "amount_column",
            "payee_column",
            "purpose_column",
            "currency",
            "date_format",
            "decimal_separator",
            "confidence",
        ],
        "additionalProperties": False,
    }


def mapping_response_format(columns: list[str]) -> dict[str, Any]:
    return json_schema_format(SCHEMA_NAME, build_mapping_json_schema(columns))


def _mapping_object(candidate: dict[str, Any]) -> dict[str, Any] | None:
    """Ein JSON-Objekt gilt als Antwort, wenn es mindestens ein Spaltenfeld des
    Vertrags nennt - ein Denktext enthaelt auch andere Objekte (z. B. eine
    zitierte Beispielzeile)."""
    return candidate if any(key in candidate for key in _COLUMN_KEYS) else None


def parse_mapping_content(content: str) -> dict[str, Any] | None:
    """Rohtext -> Antwortobjekt nach Mappingvertrag, sonst None."""
    return find_object(content, _mapping_object)


# ---------------------------------------------------------------------------
# Beispielzeilen und Validierung
# ---------------------------------------------------------------------------


def collect_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Spaltennamen in der Reihenfolge, in der sie in den Beispielzeilen
    auftauchen, ohne Duplikate und ohne Leernamen - vollstaendig: ob die
    Liste ins Fenster passt, prueft der Gateway (model_input_too_large).

    Hinweis: bei gestagten Zeilen ist das NICHT die Dateireihenfolge -
    import_rows.raw_data ist jsonb, und jsonb normalisiert die
    Schluesselreihenfolge (kurze Schluessel zuerst). Fuer die Aufgabe ist das
    unerheblich (Spaltennamen und Beispielwerte bleiben einander korrekt
    zugeordnet); die Reihenfolge ist dadurch aber stabil und reproduzierbar."""
    columns: list[str] = []
    for row in rows:
        for key in row:
            name = str(key).strip()
            if name and name not in columns:
                columns.append(name)
    return columns


def build_masked_samples(
    rows: list[dict[str, Any]], columns: list[str]
) -> list[dict[str, str]]:
    """Beispielzeilen fuer den Prompt: nur die bekannten Spalten, jede Zelle
    durch die bestehende PII-Maskierung und auf MAX_CELL_CHARS gekuerzt
    (Datensparsamkeit, siehe oben)."""
    masked: list[dict[str, str]] = []
    for row in rows[:MAX_SAMPLE_ROWS]:
        entry: dict[str, str] = {}
        for column in columns:
            value = row.get(column)
            if value is None:
                continue
            entry[column] = mask_pii_text(str(value))[:MAX_CELL_CHARS]
        masked.append(entry)
    return masked


def _resolve_column(raw_value: Any, columns: list[str]) -> str | None:
    """Invariante: es wird NIE eine Spalte vorgeschlagen, die es in der Datei
    nicht gibt. Exakte Treffer zuerst, danach tolerant gegen Gross-/
    Kleinschreibung und Randleerzeichen (der Rueckfall ohne Schema hat keine
    Grammar-Constraints und formatiert Spaltennamen gelegentlich um)."""
    if not isinstance(raw_value, str):
        return None
    candidate = raw_value.strip()
    if not candidate:
        return None
    if candidate in columns:
        return candidate
    lowered = {c.lower(): c for c in columns}
    return lowered.get(candidate.lower())


def _validated_date_format(
    raw_value: Any, rows: list[dict[str, Any]], date_column: str | None
) -> str | None:
    """Uebernimmt das vorgeschlagene strftime-Muster nur, wenn es mindestens
    einen der echten Datumswerte tatsaechlich parst - geprueft wird gegen die
    unmaskierten Originalwerte, die Maskierung betrifft Datumsangaben nicht.
    Ohne diese Pruefung wuerde ein plausibel klingendes, aber falsches Muster
    im Mapping-Dialog als Vorbelegung landen."""
    if not isinstance(raw_value, str):
        return None
    fmt = raw_value.strip()
    if not fmt or "%" not in fmt:
        return None
    if date_column is None:
        return fmt
    for row in rows[:MAX_SAMPLE_ROWS]:
        value = row.get(date_column)
        if value is None:
            continue
        try:
            datetime.strptime(str(value).strip(), fmt)
        except (ValueError, TypeError):
            continue
        return fmt
    return None


def _validated_currency(raw_value: Any) -> str | None:
    if not isinstance(raw_value, str):
        return None
    code = raw_value.strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def _validated_separator(raw_value: Any) -> str | None:
    return raw_value if raw_value in (",", ".") else None


# ---------------------------------------------------------------------------
# Ergebnisaufbau
# ---------------------------------------------------------------------------


def _reason(code: str) -> MappingSuggestionReason:
    """Grund des KI-Kerns als Grund dieses Vertrags. Der Vertrag fuehrt alle
    Gruende des Kerns (Test); ein unbekannter Code bleibt sichtbar ein Fehler
    der Antwortpruefung statt still umbenannt zu werden."""
    if code not in _REASONS:
        raise ValueError(f"Grund {code!r} fehlt in MappingSuggestionReason.")
    return cast(MappingSuggestionReason, code)


def _unavailable(
    reason: MappingSuggestionReason,
    message: str,
    columns: list[str],
    sample_count: int,
    model_name: str | None = None,
) -> ImportMappingSuggestion:
    return ImportMappingSuggestion(
        available=False,
        reason=reason,
        message=message,
        source_columns=columns,
        sample_row_count=min(sample_count, MAX_SAMPLE_ROWS),
        model_name=model_name,
        prompt_version=MAPPING_PROMPT_VERSION,
    )


def build_suggestion(
    parsed: dict[str, Any],
    columns: list[str],
    raw_rows: list[dict[str, Any]],
    model_name: str,
) -> ImportMappingSuggestion:
    """Modellantwort -> geprueftes Vorschlagsobjekt. Alles, was nicht zur Datei
    passt, wird verworfen statt weitergereicht."""
    column_mapping: dict[str, str] = {}
    for answer_key, field in _COLUMN_KEYS.items():
        resolved = _resolve_column(parsed.get(answer_key), columns)
        if resolved is not None:
            column_mapping[field] = resolved

    date_column = column_mapping.get("booked_date")
    date_format = _validated_date_format(parsed.get("date_format"), raw_rows, date_column)

    try:
        confidence = min(max(float(parsed.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0

    complete = all(field in column_mapping for field in _REQUIRED_FIELDS)
    if complete:
        message = (
            "Vorschlag der lokalen KI - bitte im Mapping-Dialog pruefen und "
            "bestaetigen. Er wird nicht automatisch angewendet."
        )
    else:
        message = (
            "Die KI konnte keine eindeutige Datums- und Betragsspalte benennen. "
            "Bitte das Mapping manuell setzen."
        )

    return ImportMappingSuggestion(
        available=complete,
        reason="ok" if complete else "suggestion_incomplete",
        message=message,
        source_columns=columns,
        sample_row_count=min(len(raw_rows), MAX_SAMPLE_ROWS),
        column_mapping=column_mapping,
        date_format=date_format,
        decimal_separator=_validated_separator(parsed.get("decimal_separator")),
        currency=_validated_currency(parsed.get("currency")),
        confidence=confidence,
        model_name=model_name,
        prompt_version=MAPPING_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------


async def load_sample_rows(
    session: AsyncSession, import_run_id: UUID, client_rows: list[dict[str, str]] | None
) -> list[dict[str, Any]]:
    """Beispielzeilen in fester Rangfolge: gestagte Zeilen des Laufs
    (import_rows.raw_data), sonst die beim Erkennen gemerkten Rohzeilen
    (import_runs.sample_raw), sonst vom Client mitgeschickte Zeilen. Die Datei
    selbst wird serverseitig nicht aufbewahrt.

    Ohne den sample_raw-Zwischenschritt bliebe der Vorschlag ausgerechnet im
    Zustand 'needs_mapping' nutzlos: dort gibt es per Definition noch keine
    import_rows, und genau dort wird die Spaltenzuordnung gebraucht."""
    rows = (
        await session.execute(
            text(
                "select raw_data from import_rows where import_run_id = :run_id "
                "order by row_number limit :limit"
            ),
            {"run_id": str(import_run_id), "limit": MAX_SAMPLE_ROWS},
        )
    ).all()
    staged = [dict(r.raw_data) for r in rows if isinstance(r.raw_data, dict)]
    if staged:
        return staged

    sample_raw = (
        await session.execute(
            text("select sample_raw from import_runs where id = :run_id"),
            {"run_id": str(import_run_id)},
        )
    ).scalar()
    if isinstance(sample_raw, list):
        detected = [dict(entry) for entry in sample_raw if isinstance(entry, dict)]
        if detected:
            return detected[:MAX_SAMPLE_ROWS]

    return [dict(r) for r in (client_rows or [])][:MAX_SAMPLE_ROWS]


def reasoning_active(ready: Ready) -> bool:
    """Denkt das Modell bei diesem Aufruf? Nur fuer den Prompttext
    (build_mapping_system_prompt) - entschieden wird das Denken vom Gateway
    nach der Haushaltseinstellung, nicht hier."""
    capability = ready.target.window.reasoning_capability.kind
    if capability == "none":
        return False
    effort = ready.settings.reasoning_effort()
    if effort is None:
        # Modellstandard: ein Modell, das immer denkt, denkt auch hier.
        return capability == "always"
    return effort != "none"


async def suggest_column_mapping(
    session: AsyncSession,
    household_id: UUID,
    import_run_id: UUID,
    client_rows: list[dict[str, str]] | None = None,
) -> ImportMappingSuggestion:
    """Erzeugt einen Spalten-Mapping-Vorschlag. Wirft ausschliesslich
    ImportRunNotFoundError (unbekannter Lauf) - jeder KI-seitige Fehlschlag
    wird zu einem Ergebnis mit available=False und dem benannten Grund des
    KI-Kerns, damit der Import davon unberuehrt bleibt."""
    run_row = (
        await session.execute(
            text(
                "select id from import_runs where id = :id and household_id = :household_id"
            ),
            {"id": str(import_run_id), "household_id": str(household_id)},
        )
    ).first()
    if run_row is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    raw_rows = await load_sample_rows(session, import_run_id, client_rows)
    columns = collect_columns(raw_rows)
    if not columns:
        # Auch dieser Ausgang bekommt eine Zeile in llm_calls: er war der Fall,
        # den Nutzer als "die KI weigert sich" erleben, und ohne Zeile war er
        # von einem stillen Fehlschlag nicht zu unterscheiden.
        await log_without_call(
            llm.CallScope(
                session=session,
                household_id=household_id,
                stage=LLM_CALL_STAGE,
                prompt_version=MAPPING_PROMPT_VERSION,
            ),
            "no_sample_rows",
            "no_sample_rows: weder Staging-Zeilen noch gemerkte Rohzeilen noch "
            "mitgeschickte Beispielzeilen - es wurde kein Modell angefragt.",
        )
        return _unavailable(
            "no_sample_rows",
            "Fuer diesen Importlauf liegen keine Beispielzeilen vor, aus denen ein "
            "Spaltenvorschlag abgeleitet werden koennte.",
            [],
            0,
        )

    ready = await prepare(
        session, household_id, stage=LLM_CALL_STAGE, prompt_version=MAPPING_PROMPT_VERSION
    )
    if isinstance(ready, Refused):
        return _unavailable(
            _reason(ready.reason_code),
            f"Kein KI-Vorschlag: {ready.message} Das Mapping laesst sich wie bisher "
            "manuell setzen.",
            columns,
            len(raw_rows),
            ready.model,
        )

    messages = build_mapping_messages(
        columns,
        build_masked_samples(raw_rows, columns),
        reasoning_enabled=reasoning_active(ready),
    )
    # Das Spalten-Enum bleibt auch bei aktivem Reasoning bestehen: die
    # Vermutung, Grammar-Constraint plus Reasoning loese eine
    # Wiederholungsschleife aus, wurde direkt gegen LM Studio geprueft und
    # widerlegt (Enum mit reasoning=high lieferte in 8,9 s gueltiges JSON). Es
    # ist die einzige harte Zusicherung gegen erfundene Spaltennamen.
    answer = await ask(
        ready,
        llm.ChatRequest(messages=messages, response_format=mapping_response_format(columns)),
        accept=_mapping_object,
    )
    model = ready.target.model
    if answer.value is None:
        code = answer.reason_code or llm.reasons.MODEL_OUTPUT_UNUSABLE
        return _unavailable(
            _reason(code),
            f"Kein KI-Vorschlag: {answer.message or llm.reasons.describe(code)} Das "
            "Mapping laesst sich wie bisher manuell setzen.",
            columns,
            len(raw_rows),
            model,
        )
    return build_suggestion(answer.value, columns, raw_rows, model)
