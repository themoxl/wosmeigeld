"""KI-Kategorievorschlaege aus dem vorhandenen Buchungsbestand.

Umsetzung von Planentscheidung 8 aus docs/KATEGORISIERUNGSPLAN.md ("KI darf
vorschlagen, nicht erfinden") und der bis hierhin offenen Frage aus
docs/KI-FINANZASSISTENT-PLAN.md ("Soll die KI neue Kategorien anlegen?
Vorschlagen: ja. Automatisch anlegen: nein.").

Abgrenzung zur Klassifikation
-----------------------------
`ai_service.classify_transaction_with_llm` ordnet EINE Buchung EINER
bestehenden Kategorie zu; ``category_id`` ist dort per JSON-Schema-Enum auf die
vorhandenen IDs festgenagelt. Das bleibt bewusst so: ein halluzinierter Wert
wuerde dort eine Buchung still falsch ablegen.

Dieser Dienst laeuft in die andere Richtung. Er bildet aus den noch nicht
zugeordneten Buchungen Cluster gleicher Empfaenger und fragt das Modell je
Cluster: passt eine vorhandene Kategorie - und wenn nicht, wie muesste die
fehlende heissen? Drei Schutzschichten machen den Unterschied zum blossen
"Modell darf sich was ausdenken":

1. Ein Cluster braucht mindestens ``MIN_SUGGESTION_CLUSTER_SIZE`` Buchungen.
   Aus einem Einzelbeleg entsteht nie eine Kategorie.
2. Jeder Vorschlag wird gegen den Bestand dedupliziert. Schlaegt das Modell
   einen Namen vor, den es schon gibt, wird daraus eine Zuordnung statt einer
   zweiten, fast gleichen Kategorie.
3. Angelegt wird erst nach ausdruecklicher Bestaetigung durch den Nutzer, und
   Name, Gruppe, Budgetbereich und Steuerrelevanz sind dabei ueberschreibbar.

Vorschau und Anwenden nutzen dieselbe Mechanik wie Merge und Paket-Apply
(`category_operations`), damit es keine zweite Job-Infrastruktur gibt.

Modellaufruf
------------
Jeder Cluster ist ein Aufruf ueber den Gateway des KI-Kerns (app/llm): das
Antwortbudget kommt aus dem geladenen Fenster, jeder Aufruf steht in
llm_calls (Stufe ``category_suggest``). Passt die Kategorienliste samt
Antwortreserve nicht ins Fenster, wird sie ohne Synonyme gesendet - die
Kategorien selbst fallen nie weg; passt auch das nicht, scheitert der Lauf
mit model_input_too_large und Aufschluesselung.

Ein Fehlschlag ist nie eine leere Vorschau: liefert kein Cluster eine
verwertbare Antwort, scheitert die Vorschau mit benanntem Grund
(ai_answers.AIStepError). Scheitern nur einzelne Cluster, traegt die Vorschau
die Zahl der nicht beurteilten Cluster und den Grund (``unassessed_clusters``,
``warnings``). Ein Grund, der den Zustand von Server oder Einstellung
beschreibt (Zeitbudget, Fenster, Antwortbudget vom Denken aufgebraucht ...),
beendet den Lauf sofort statt fuer jeden weiteren Cluster erneut zu warten.
"""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final, cast
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm import answers, reasons
from app.schemas.ai import AIWarning
from app.schemas.category import (
    MAX_SUGGESTION_CLUSTERS,
    MIN_SUGGESTION_CLUSTER_SIZE,
    CategorySuggestion,
    CategorySuggestionApply,
    CategorySuggestionPreview,
    CategorySuggestionResult,
    CategorySuggestionResultItem,
    SuggestionSample,
)
from app.services import ai_answers, ai_service, ai_settings_service, category_service
from app.services.audit_service import record_category_event

SUGGESTION_OPERATION: Final = "suggestion_apply"

CATEGORY_OPERATION_NOT_FOUND: Final = "category_operation_not_found"
CATEGORY_PREVIEW_STALE: Final = "category_preview_stale"
SUGGESTION_GROUP_REQUIRED: Final = "category_suggestion_group_required"

# llm_calls: Stufe und Vertragsversion des Vorschlagsprompts.
SUGGESTION_STAGE: Final = "category_suggest"
PROMPT_VERSION: Final = "category-suggest-v1"

# Beispielbuchungen pro Vorschlag. Drei reichen, um einen Vorschlag zu
# beurteilen; die vollstaendige Buchungsliste steht im Vorgang und muss weder
# durch die API-Antwort noch zum Modell.
# fest: drei Beispielbuchungen je Cluster - Datensparsamkeit im Prompt und Anzeige
_SAMPLES_PER_CLUSTER: Final = 3

# Buchungen, die der Dienst ueberhaupt betrachtet. Deckel gegen einen
# Erstimport mit zehntausenden Zeilen; die Auswahl ist nach Datum absteigend,
# junge Buchungen sind fuer eine Kategorienstruktur die aussagekraeftigeren.
# fest: Lastgrenze der Clusterbildung (juengste offene Buchungen), kein Promptmaterial
_MAX_SCANNED_TRANSACTIONS: Final = 5000

# Rechtsform- und Zahlungsdienstrauschen. Diese Tokens tragen nichts zur
# fachlichen Unterscheidung bei und wuerden denselben Haendler auf mehrere
# Cluster verteilen ("REWE MARKT GMBH" vs. "REWE SAGT DANKE").
_NOISE_TOKENS: Final = frozenset(
    {
        "AG",
        "CO",
        "DANKE",
        "DE",
        "EUR",
        "EU",
        "GMBH",
        "KG",
        "KGAA",
        "LTD",
        "MBH",
        "OHG",
        "SA",
        "SARL",
        "SCA",
        "SE",
        "SP",
        "UG",
        "ZO",
        "AND",
        "UND",
        "DER",
        "DIE",
        "DAS",
        "VON",
        "FUER",
        "KAUF",
        "KARTENZAHLUNG",
        "LASTSCHRIFT",
        "SEPA",
        "ELV",
        "GIROCARD",
        "DEBITK",
        "NR",
    }
)

# Tokens des Empfaengernamens, die den Clusterschluessel bilden.
_KEY_TOKEN_LIMIT: Final = 3


class SuggestionCluster:
    """Buchungen eines Empfaengers, die noch keine Kategorie haben."""

    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label
        self.transaction_ids: list[UUID] = []
        self.total_amount_minor = 0
        self.first_booked_date: date | None = None
        self.last_booked_date: date | None = None
        self.samples: list[SuggestionSample] = []

    def add(self, row: Any) -> None:
        transaction_id = UUID(str(row.transaction_id))
        self.transaction_ids.append(transaction_id)
        self.total_amount_minor += int(row.amount_minor)
        booked: date = row.booked_date
        if self.first_booked_date is None or booked < self.first_booked_date:
            self.first_booked_date = booked
        if self.last_booked_date is None or booked > self.last_booked_date:
            self.last_booked_date = booked
        if len(self.samples) < _SAMPLES_PER_CLUSTER:
            self.samples.append(
                SuggestionSample(
                    transaction_id=transaction_id,
                    booked_date=booked,
                    amount_minor=int(row.amount_minor),
                    payee_label=row.payee_label,
                    note=row.note,
                )
            )

    @property
    def size(self) -> int:
        return len(self.transaction_ids)


def cluster_key(payee_label: str | None) -> str:
    """Vergleichsschluessel eines Empfaengernamens.

    Normalisiert werden nur Schreibweise, Satzzeichen, Ziffernblöcke und
    Rechtsform-/Zahlungsrauschen. Aehnlich klingende Empfaenger werden
    ausdruecklich NICHT heuristisch zusammengelegt - dieselbe Zusicherung
    trifft `rules_service._recurring_payee_key`, und sie ist hier wichtiger:
    ein zu grosszuegiger Schluessel wuerde zwei Haendler zu einer erfundenen
    Kategorie verschmelzen.
    """
    if not payee_label:
        return ""
    upper = re.sub(r"[^\w]+", " ", payee_label.upper(), flags=re.UNICODE)
    tokens: list[str] = []
    for token in upper.split():
        # Reine Ziffernfolgen sind Belegnummern, Terminal-IDs oder Daten.
        if token.isdigit():
            continue
        # Gemischte Tokens mit Ziffern (z. B. "PP.3972.PP") verlieren die
        # Ziffern, statt den Schluessel zu zerlegen.
        cleaned = re.sub(r"\d+", "", token)
        if len(cleaned) < 2 or cleaned in _NOISE_TOKENS:
            continue
        tokens.append(cleaned)
        if len(tokens) >= _KEY_TOKEN_LIMIT:
            break
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Bestand einlesen und clustern
# ---------------------------------------------------------------------------

_UNCATEGORIZED_SQL: Final = (
    "select t.id as transaction_id, t.booked_date, t.amount_minor, "
    "coalesce(p.name, t.payee_label) as payee_label, t.note "
    "from transactions t "
    "join transaction_splits s on s.transaction_id = t.id "
    "left join payees p on p.id = t.payee_id "
    "where t.household_id = :household_id "
    "and s.household_id = :household_id "
    "and s.category_id is null "
    "and not t.is_archived "
    # Transfers haben fachlich keine Ausgabenkategorie; ein Vorschlag dafuer
    # waere immer falsch.
    "and not t.is_transfer "
    # Nur Buchungen mit genau einem Split: eine bereits geteilte Buchung
    # bekommt keine Gesamtkategorie aufgedraengt (dieselbe Regel wie in
    # schemas/category.py::TransactionCategoryUpdate).
    "and (select count(*) from transaction_splits x where x.transaction_id = t.id) = 1 "
    "order by t.booked_date desc "
    "limit :scan_limit"
)


async def load_clusters(
    session: AsyncSession, household_id: UUID
) -> tuple[list[SuggestionCluster], int, int]:
    """Liest die nicht zugeordneten Buchungen und gruppiert sie.

    Rueckgabe: (Cluster ab Mindestgroesse, Gesamtzahl offener Buchungen,
    Anzahl Buchungen in zu kleinen Clustern)."""
    result = await session.execute(
        text(_UNCATEGORIZED_SQL),
        {"household_id": str(household_id), "scan_limit": _MAX_SCANNED_TRANSACTIONS},
    )
    rows = result.fetchall()

    grouped: dict[str, SuggestionCluster] = {}
    for row in rows:
        key = cluster_key(row.payee_label)
        if not key:
            continue
        cluster = grouped.get(key)
        if cluster is None:
            cluster = SuggestionCluster(key, str(row.payee_label or key))
            grouped[key] = cluster
        cluster.add(row)

    usable = [c for c in grouped.values() if c.size >= MIN_SUGGESTION_CLUSTER_SIZE]
    skipped = sum(c.size for c in grouped.values() if c.size < MIN_SUGGESTION_CLUSTER_SIZE)

    # Nach fachlichem Gewicht sortieren, nicht nach Anzahl allein: 12 Buchungen
    # a 3 EUR sind fuer eine Kategorienstruktur weniger relevant als 3
    # Buchungen a 400 EUR.
    usable.sort(key=lambda c: (abs(c.total_amount_minor), c.size), reverse=True)
    # fest: Bediengrenze - hoechstens MAX_SUGGESTION_CLUSTERS Vorschlaege in einer Pruefansicht
    return usable[:MAX_SUGGESTION_CLUSTERS], len(rows), skipped


# ---------------------------------------------------------------------------
# Modellaufruf
# ---------------------------------------------------------------------------


def _amount_eur(amount_minor: int) -> str:
    return f"{amount_minor / 100:.2f} EUR"


def build_suggestion_system_prompt(
    categories: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    with_synonyms: bool = True,
) -> str:
    """Systemprompt des Vorschlagslaufs.

    Anders als beim Klassifikationsprompt ist "keine passende Kategorie" hier
    kein Endergebnis, sondern der Ausloeser fuer den eigentlichen Auftrag.
    Die Synonyme stehen vollstaendig darin; ``with_synonyms=False`` ist die
    knappe Fassung, wenn das Fenster die volle nicht fasst (_Prompts)."""
    category_lines = "\n".join(
        f"- {entry['id']} | {entry['path']}"
        + (
            f" | Synonyme: {', '.join(entry['synonyms'])}"
            if with_synonyms and entry.get("synonyms")
            else ""
        )
        for entry in categories
    )
    group_lines = "\n".join(f"- {group['id']} | {group['name']}" for group in groups)
    return (
        "Du bist ein deutschsprachiger Buchhaltungsassistent fuer private "
        "Haushaltsfinanzen. Du bekommst eine Gruppe gleichartiger Buchungen "
        "desselben Empfaengers, die noch keine Kategorie haben.\n\n"
        "Vorhandene Kategorien (ID | Gruppe - Kategorie):\n"
        f"{category_lines}\n\n"
        "Vorhandene Kategoriegruppen (ID | Name):\n"
        f"{group_lines}\n\n"
        "Aufgabe:\n"
        "1. Passt eine VORHANDENE Kategorie fachlich genau? Dann antworte mit "
        'decision="existing" und ihrer ID.\n'
        "2. Nur wenn keine vorhandene Kategorie fachlich passt: schlage mit "
        'decision="new" eine neue Kategorie vor.\n\n'
        "Regeln fuer einen neuen Vorschlag:\n"
        "- Der Name ist konkret und auf Deutsch, ohne den Haendlernamen selbst "
        '("Software & Online-Dienste", nicht "Adobe" und nicht "Sonstiges").\n'
        "- Er beschreibt eine wiederkehrende Ausgabenart, unter die kuenftig "
        "auch andere Haendler passen.\n"
        "- group_id ist eine der oben genannten Gruppen-IDs.\n"
        "- budget_bucket: needs fuer Notwendiges, wants fuer Verzichtbares, "
        "savings fuer Ruecklagen.\n"
        "- is_tax_deductible nur true, wenn die Ausgabe typischerweise "
        "beruflich veranlasst und absetzbar ist.\n"
        "- Eine Sammelkategorie wie 'Sonstiges', 'Diverses' oder 'Allgemein' "
        "ist NIE eine gueltige Antwort.\n\n"
        "Antworte ausschliesslich mit dem geforderten JSON-Objekt."
    )


def build_suggestion_user_prompt(cluster: SuggestionCluster) -> str:
    lines = [
        f"Empfaenger: {ai_service.mask_pii_text(cluster.label)}",
        f"Anzahl Buchungen: {cluster.size}",
        f"Summe: {_amount_eur(cluster.total_amount_minor)}",
        f"Zeitraum: {cluster.first_booked_date} bis {cluster.last_booked_date}",
        "Beispiele:",
    ]
    for sample in cluster.samples:
        note = ai_service.mask_pii_text(sample.note)
        lines.append(
            f"- {sample.booked_date} | {_amount_eur(sample.amount_minor)}"
            + (f" | {note}" if note else "")
        )
    return "\n".join(lines)


def build_suggestion_json_schema(
    valid_category_ids: set[str], valid_group_ids: set[str]
) -> dict[str, Any]:
    """JSON-Schema des Vorschlagsvertrags.

    ``category_id`` und ``group_id`` sind wie bei der Klassifikation Enums ueber
    die real vorhandenen IDs - erfunden werden darf nur der NAME einer neuen
    Kategorie, niemals eine Referenz."""
    none_value = ai_service.NO_MATCH
    return {
        "name": "category_suggestion",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["existing", "new"]},
                "category_id": {
                    "type": "string",
                    "enum": [*sorted(valid_category_ids), none_value],
                },
                "new_name": {"type": "string"},
                "group_id": {"type": "string", "enum": [*sorted(valid_group_ids), none_value]},
                "default_type": {"type": "string", "enum": ["expense", "income", "transfer"]},
                "budget_bucket": {
                    "type": "string",
                    "enum": ["needs", "wants", "savings", none_value],
                },
                "is_tax_deductible": {"type": "boolean"},
                "synonyms": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": [
                "decision",
                "category_id",
                "new_name",
                "group_id",
                "default_type",
                "budget_bucket",
                "is_tax_deductible",
                "synonyms",
                "confidence",
                "reason",
            ],
            "additionalProperties": False,
        },
    }


class _Prompts:
    """Systemprompt in zwei Fassungen: mit allen Synonymen und ohne.

    Gesendet wird die volle Fassung; passt sie samt Antwortreserve nicht ins
    Fenster (oder meldet der Server einen Ueberlauf), tauscht der Gateway sie
    ueber den Verdichtungshaken gegen die knappe. Die Kategorien selbst fallen
    nie weg - ohne sie koennte das Modell nicht "vorhanden" sagen."""

    def __init__(self, categories: list[dict[str, Any]], groups: list[dict[str, Any]]) -> None:
        self.full = build_suggestion_system_prompt(categories, groups)
        self.compact = build_suggestion_system_prompt(categories, groups, with_synonyms=False)

    def messages(self, user_prompt: str) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": self.full},
            {"role": "user", "content": user_prompt},
        ]

    async def compact_hook(
        self, messages: list[dict[str, Any]], budget: llm.Budget
    ) -> list[dict[str, Any]] | None:
        del budget
        if self.compact == self.full or not messages or messages[0].get("content") != self.full:
            return None
        return [{"role": "system", "content": self.compact}, *messages[1:]]


_SANE_NAME = re.compile(r"^[\wÄÖÜäöüß0-9 &.,\-/()+]{2,80}$", re.UNICODE)

# Sammelbegriffe, die als Kategoriename nichts erklaeren. Der Prompt verbietet
# sie bereits; diese Liste ist die Durchsetzung davon.
_FORBIDDEN_NAMES: Final = frozenset(
    {
        "sonstiges",
        "sonstige",
        "diverses",
        "allgemein",
        "verschiedenes",
        "unbekannt",
        "andere",
        "rest",
        "misc",
        "other",
    }
)


def _is_usable_new_name(name: str) -> bool:
    if not _SANE_NAME.match(name):
        return False
    return category_service.normalize_category_name(name) not in _FORBIDDEN_NAMES


CancelCheck = Callable[[], Awaitable[bool]]


@dataclass
class _SuggestionRun:
    """Ergebnis der Modellschleife ueber die Cluster."""

    suggestions: list[CategorySuggestion] = field(default_factory=list)
    model_name: str | None = None
    unassessed_clusters: int = 0
    warnings: list[AIWarning] = field(default_factory=list)


async def _suggest(
    session: AsyncSession,
    household_id: UUID,
    clusters: list[SuggestionCluster],
    *,
    ai_task_id: UUID | None,
    is_cancelled: CancelCheck | None,
) -> _SuggestionRun:
    """Fragt das Modell je Cluster ueber den Gateway.

    * Einzelne unbrauchbare Antwort (kein JSON, Vertragsbruch): der Cluster
      bleibt unbeurteilt, die Protokollzeile wird markiert, der Lauf geht
      weiter.
    * Jeder andere Grund (llm.answers.ends_run) beendet den Lauf.
    * Ohne einen einzigen brauchbaren Vorschlag scheitert der Lauf mit dem
      Grund (AIStepError) - eine leere Vorschau hiesse "es fehlt nichts".
    * Ein Abbruch der KI-Aufgabe wird zwischen zwei Clustern geprueft: der
      laufende Aufruf endet regulaer, danach wird niemand mehr gefragt."""
    settings = await ai_settings_service.resolve(session, household_id)
    target = await ai_answers.require_target(settings)

    candidates = await ai_service.load_categories(session, household_id)
    groups = await _load_groups(session, household_id)
    category_lookup = await _load_active_category_names(session, household_id)
    category_names_by_id = {
        str(entry["id"]): str(entry["path"]).split(" · ")[-1] for entry in candidates.entries
    }
    group_names_by_id = {group["id"]: group["name"] for group in groups}

    prompts = _Prompts(candidates.entries, groups)
    response_format = {
        "type": "json_schema",
        "json_schema": build_suggestion_json_schema(
            set(candidates.valid_ids), set(group_names_by_id)
        ),
    }
    scope = llm.CallScope(
        session=session,
        household_id=household_id,
        stage=SUGGESTION_STAGE,
        prompt_version=PROMPT_VERSION,
        ai_task_id=ai_task_id,
    )
    hooks = llm.ChatHooks(compact=prompts.compact_hook)
    ready = answers.Ready(settings, target, scope)

    run = _SuggestionRun()
    rejected: list[answers.Answer[Any]] = []
    stop: tuple[str, str] | None = None
    asked = 0
    use_schema = True
    for cluster in clusters:
        if is_cancelled is not None and await is_cancelled():
            stop = (
                reasons.MODEL_CANCELLED,
                f"Lauf abgebrochen - {len(clusters) - asked} von {len(clusters)} Clustern "
                "wurden nicht mehr gefragt.",
            )
            break
        asked += 1
        answer: answers.Answer[Any] = await answers.ask(
            ready,
            llm.ChatRequest(
                messages=prompts.messages(build_suggestion_user_prompt(cluster)),
                response_format=response_format if use_schema else None,
            ),
            hooks=hooks,
        )
        if answer.result.schema_fallback:
            # Der Server kann kein response_format: die weiteren Cluster
            # gleich auf dem Prompt+Parse-Weg, statt jedes Mal abgelehnt zu
            # werden.
            use_schema = False
        if not answer.ok:
            if answers.ends_run(answer.reason_code):
                stop = (
                    answer.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
                    answer.message or "",
                )
                break
            rejected.append(answer)
            continue
        run.model_name = target.model
        try:
            parsed = answers.json_object(answer.text)
        except ValueError as exc:
            rejected.append(
                await answers.reject(scope, answer.result, reasons.MODEL_OUTPUT_UNUSABLE, str(exc))
            )
            continue
        suggestion = _suggestion_from_answer(
            cluster, parsed, category_lookup, category_names_by_id, group_names_by_id
        )
        if suggestion is None:
            rejected.append(
                await answers.reject(
                    scope,
                    answer.result,
                    reasons.MODEL_OUTPUT_UNUSABLE,
                    "Die Antwort hält den Vorschlagsvertrag nicht ein (Entscheidung, "
                    "Kategorie-ID oder Name unbrauchbar).",
                )
            )
            continue
        run.suggestions.append(suggestion)
        # Zwei Cluster duerfen nicht zweimal dieselbe neue Kategorie
        # vorschlagen - der zweite wird zur Zuordnung an die erste.
        if suggestion.kind == "new_category" and suggestion.proposed_name:
            category_lookup.setdefault(
                category_service.normalize_category_name(suggestion.proposed_name),
                (uuid4(), suggestion.proposed_name),
            )

    cancelled = stop is not None and stop[0] == reasons.MODEL_CANCELLED
    if not run.suggestions and not cancelled:
        if stop is not None:
            raise ai_answers.AIStepError(*stop)
        first = rejected[0]
        raise ai_answers.AIStepError(
            first.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
            f"Keiner der {len(clusters)} Cluster bekam eine verwertbare Antwort. "
            f"{first.message or ''}".strip(),
        )

    run.unassessed_clusters = len(clusters) - len(run.suggestions)
    if rejected:
        run.warnings.append(
            ai_answers.warning(
                reasons.MODEL_OUTPUT_UNUSABLE,
                f"{len(rejected)} von {len(clusters)} Clustern ohne verwertbare Antwort. "
                f"{rejected[0].message or ''}".strip(),
            )
        )
    if stop is not None:
        code, message = stop
        run.warnings.append(
            ai_answers.warning(
                code,
                message
                if cancelled
                else f"Lauf beim {asked}. von {len(clusters)} Clustern beendet: {message}",
            )
        )
    return run


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


async def _load_groups(session: AsyncSession, household_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            "select id, name from category_groups where household_id = :household_id "
            "and not is_archived order by sort_order, name"
        ),
        {"household_id": str(household_id)},
    )
    return [{"id": str(row.id), "name": str(row.name)} for row in result]


async def _load_active_category_names(
    session: AsyncSession, household_id: UUID
) -> dict[str, tuple[UUID, str]]:
    """Normalisierter Name -> (ID, Anzeigename) aller aktiven Kategorien.

    Grundlage der Deduplizierung: ein Modellvorschlag, den es schon gibt, wird
    zur Zuordnung herabgestuft statt als zweite Kategorie angelegt."""
    result = await session.execute(
        text(
            "select id, name, synonyms from categories where household_id = :household_id "
            "and not is_archived"
        ),
        {"household_id": str(household_id)},
    )
    lookup: dict[str, tuple[UUID, str]] = {}
    for row in result:
        category_id = UUID(str(row.id))
        name = str(row.name)
        lookup.setdefault(category_service.normalize_category_name(name), (category_id, name))
        for synonym in row.synonyms or []:
            lookup.setdefault(
                category_service.normalize_category_name(str(synonym)), (category_id, name)
            )
    return lookup


def _suggestion_from_answer(
    cluster: SuggestionCluster,
    answer: dict[str, Any] | None,
    category_lookup: dict[str, tuple[UUID, str]],
    category_names_by_id: dict[str, str],
    group_names_by_id: dict[str, str],
) -> CategorySuggestion | None:
    """Modellantwort -> geprueften Vorschlag. Alles, was der Vertrag nicht
    hergibt, faellt hier heraus statt spaeter beim Anlegen aufzuschlagen."""
    if answer is None:
        return None

    def build(**variable: Any) -> CategorySuggestion:
        """Die Clusterfelder sind in allen drei Antwortformen identisch; nur
        der Vorschlagsteil unterscheidet sich."""
        return CategorySuggestion(
            cluster_key=cluster.key,
            payee_label=cluster.label,
            transaction_count=cluster.size,
            total_amount_minor=cluster.total_amount_minor,
            first_booked_date=cast(date, cluster.first_booked_date),
            last_booked_date=cast(date, cluster.last_booked_date),
            samples=cluster.samples,
            **variable,
        )

    try:
        confidence = min(1.0, max(0.0, float(answer.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    # fest: Ausgabepruefung - Begruendung des Modells hoechstens 400 Zeichen in der Anzeige
    reason = str(answer.get("reason") or "").strip()[:400]

    decision = str(answer.get("decision") or "")
    raw_category_id = str(answer.get("category_id") or "")

    if decision == "existing" and raw_category_id in category_names_by_id:
        return build(
            kind="existing_category",
            confidence=confidence,
            reason=reason,
            existing_category_id=UUID(raw_category_id),
            existing_category_name=category_names_by_id[raw_category_id],
        )

    if decision != "new":
        return None

    name = " ".join(str(answer.get("new_name") or "").split())
    if not _is_usable_new_name(name):
        return None

    # Deduplizierung gegen den Bestand: gibt es den Namen (oder ein Synonym)
    # bereits, ist es keine neue Kategorie, sondern eine Zuordnung.
    existing = category_lookup.get(category_service.normalize_category_name(name))
    if existing is not None:
        return build(
            kind="existing_category",
            confidence=confidence,
            reason=(
                f"{reason} (Vorschlag '{name}' entspricht der vorhandenen Kategorie "
                f"'{existing[1]}'.)"
            ).strip(),
            existing_category_id=existing[0],
            existing_category_name=existing[1],
        )

    raw_group_id = str(answer.get("group_id") or "")
    group_id = UUID(raw_group_id) if raw_group_id in group_names_by_id else None
    bucket = str(answer.get("budget_bucket") or "")
    default_type = str(answer.get("default_type") or "expense")
    raw_synonyms = answer.get("synonyms")
    # fest: Ausgabepruefung - hoechstens 12 Synonyme je neu vorgeschlagener Kategorie
    synonyms = [
        " ".join(str(item).split())
        for item in (raw_synonyms if isinstance(raw_synonyms, list) else [])
        if str(item).strip()
    ][:12]

    return build(
        kind="new_category",
        confidence=confidence,
        reason=reason,
        proposed_name=name,
        proposed_group_id=group_id,
        proposed_group_name=group_names_by_id.get(raw_group_id),
        proposed_default_type=cast(
            Any,
            default_type if default_type in ("expense", "income", "transfer") else "expense",
        ),
        proposed_budget_bucket=(
            cast(Any, bucket) if bucket in ("needs", "wants", "savings") else None
        ),
        proposed_synonyms=synonyms,
        proposed_is_tax_deductible=bool(answer.get("is_tax_deductible", False)),
    )


def _fingerprint(impact: dict[str, Any]) -> str:
    canonical = json.dumps(impact, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def build_preview(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    *,
    ai_task_id: UUID | None = None,
    is_cancelled: CancelCheck | None = None,
) -> CategorySuggestionPreview:
    """Clustert den offenen Bestand, laesst das Modell je Cluster urteilen und
    legt das Ergebnis als kurzlebige Vorschau ab.

    Laeuft die Vorschau als KI-Aufgabe (Worker), traegt jede llm_calls-Zeile
    ``ai_task_id`` und ``is_cancelled`` wird vor jedem Cluster gefragt.
    Wirft ai_answers.AIStepError, wenn das Modell nichts Verwertbares
    lieferte - die Protokollzeilen behaelt nur, wer den Fehler abfaengt und
    die Transaktion committet (Route: AIStepError.response())."""
    clusters, uncategorized_total, skipped_singletons = await load_clusters(session, household_id)
    revision = await category_service.get_taxonomy_revision(session, household_id)

    run = (
        await _suggest(
            session, household_id, clusters, ai_task_id=ai_task_id, is_cancelled=is_cancelled
        )
        if clusters
        else _SuggestionRun()
    )
    suggestions = run.suggestions
    model_name = run.model_name

    impact: dict[str, Any] = {
        "taxonomy_revision": revision,
        "model_name": model_name,
        "uncategorized_transactions": uncategorized_total,
        "clustered_transactions": sum(c.size for c in clusters),
        "skipped_singletons": skipped_singletons,
        "unassessed_clusters": run.unassessed_clusters,
        "warnings": [item.model_dump(mode="json") for item in run.warnings],
        "suggestions": [item.model_dump(mode="json") for item in suggestions],
        # Vollstaendige Buchungsliste je Cluster: das Anwenden ordnet daraus zu,
        # die API-Antwort traegt nur die Beispiele.
        "cluster_transaction_ids": {
            cluster.key: [str(tid) for tid in cluster.transaction_ids] for cluster in clusters
        },
    }

    result = await session.execute(
        text(
            "insert into category_operations (household_id, operation, status, created_by, "
            "taxonomy_revision, fingerprint, impact) "
            "values (:household_id, :operation, 'preview', :created_by, :revision, "
            ":fingerprint, cast(:impact as jsonb)) returning id, expires_at"
        ),
        {
            "household_id": str(household_id),
            "operation": SUGGESTION_OPERATION,
            "created_by": str(actor_user_id) if actor_user_id else None,
            "revision": revision,
            "fingerprint": _fingerprint(impact),
            "impact": json.dumps(impact, ensure_ascii=False),
        },
    )
    row = result.one()

    return CategorySuggestionPreview(
        operation_id=UUID(str(row.id)),
        expires_at=row.expires_at,
        taxonomy_revision=revision,
        model_name=model_name,
        uncategorized_transactions=uncategorized_total,
        clustered_transactions=int(impact["clustered_transactions"]),
        skipped_singletons=skipped_singletons,
        unassessed_clusters=run.unassessed_clusters,
        warnings=run.warnings,
        suggestions=suggestions,
    )


# ---------------------------------------------------------------------------
# Anwenden
# ---------------------------------------------------------------------------


async def _find_applied_result(
    session: AsyncSession, household_id: UUID, request_id: str
) -> CategorySuggestionResult | None:
    result = await session.execute(
        text(
            "select result from category_operations where household_id = :household_id "
            "and operation = :operation and request_id = :request_id and status = 'applied'"
        ),
        {
            "household_id": str(household_id),
            "operation": SUGGESTION_OPERATION,
            "request_id": request_id,
        },
    )
    row = result.first()
    if row is None or row.result is None:
        return None
    stored = dict(row.result)
    stored["idempotent_replay"] = True
    return CategorySuggestionResult(**stored)


async def _find_active_category_in_group(
    session: AsyncSession, household_id: UUID, group_id: UUID, name: str
) -> tuple[UUID, str] | None:
    """Gleichnamige aktive Kategorie in der Zielgruppe. Zwischen Vorschau und
    Anwenden kann sie neu entstanden sein - dann wird zugeordnet statt an einem
    Unique-Konflikt zu scheitern."""
    result = await session.execute(
        text(
            "select id, name from categories where household_id = :household_id "
            "and group_id = :group_id and not is_archived "
            "and category_normalize_name(name) = :normalized limit 1"
        ),
        {
            "household_id": str(household_id),
            "group_id": str(group_id),
            "normalized": category_service.normalize_category_name(name),
        },
    )
    row = result.first()
    return (UUID(str(row.id)), str(row.name)) if row is not None else None


async def _assign_transactions(
    session: AsyncSession, household_id: UUID, category_id: UUID, transaction_ids: list[UUID]
) -> int:
    """Ordnet die Buchungen des Clusters zu.

    Bewusst eng gefasst: nur Splits, die immer noch ohne Kategorie sind. Eine
    Buchung, die der Nutzer zwischen Vorschau und Anwenden selbst zugeordnet
    hat, wird nicht ueberschrieben."""
    if not transaction_ids:
        return 0
    result = await session.execute(
        text(
            "update transaction_splits s set category_id = :category_id "
            "from transactions t "
            "where s.transaction_id = t.id "
            "and s.household_id = :household_id and t.household_id = :household_id "
            "and s.transaction_id = any(cast(:ids as uuid[])) "
            "and s.category_id is null and not t.is_archived"
        ),
        {
            "category_id": str(category_id),
            "household_id": str(household_id),
            "ids": [str(tid) for tid in transaction_ids],
        },
    )
    return cast(CursorResult[Any], result).rowcount


async def apply(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    payload: CategorySuggestionApply,
) -> CategorySuggestionResult:
    """Legt die bestaetigten Kategorien an und ordnet die Cluster zu."""
    if payload.request_id is not None:
        replay = await _find_applied_result(session, household_id, payload.request_id)
        if replay is not None:
            return replay

    operation = await session.execute(
        text(
            "select id, status, expires_at, impact from category_operations "
            "where id = :id and household_id = :household_id and operation = :operation "
            "for update"
        ),
        {
            "id": str(payload.operation_id),
            "household_id": str(household_id),
            "operation": SUGGESTION_OPERATION,
        },
    )
    row = operation.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_OPERATION_NOT_FOUND
        )
    if row.status != "preview" or row.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_PREVIEW_STALE)

    impact = dict(row.impact)
    stored = {
        str(item["cluster_key"]): item for item in impact.get("suggestions", [])
    }
    cluster_ids: dict[str, list[str]] = impact.get("cluster_transaction_ids", {})

    correlation_id = uuid4()
    items: list[CategorySuggestionResultItem] = []
    created_categories = 0
    linked_existing = 0
    assigned_total = 0
    skipped_total = 0

    for accept in payload.accepted:
        suggestion = stored.get(accept.cluster_key)
        if suggestion is None:
            # Cluster gehoert nicht zu dieser Vorschau - stillschweigend
            # anzulegen waere schlimmer als es zu uebergehen.
            continue

        transaction_ids = [UUID(tid) for tid in cluster_ids.get(accept.cluster_key, [])]
        target_id: UUID | None = None
        target_name = ""
        created = False

        explicit_existing = accept.use_existing_category_id
        suggested_existing = (
            UUID(str(suggestion["existing_category_id"]))
            if suggestion.get("existing_category_id")
            else None
        )
        reuse_id = explicit_existing or (
            suggested_existing if suggestion["kind"] == "existing_category" else None
        )

        if reuse_id is not None:
            existing = await category_service.load_category(session, household_id, reuse_id)
            if existing is None or existing["is_archived"]:
                continue
            target_id = reuse_id
            target_name = str(existing["name"])
            linked_existing += 1
        else:
            name = accept.name or str(suggestion.get("proposed_name") or "")
            group_id = accept.group_id or (
                UUID(str(suggestion["proposed_group_id"]))
                if suggestion.get("proposed_group_id")
                else None
            )
            if not name or group_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=SUGGESTION_GROUP_REQUIRED,
                )
            await category_service.assert_group_active(session, household_id, group_id)

            collision = await _find_active_category_in_group(
                session, household_id, group_id, name
            )
            if collision is not None:
                target_id, target_name = collision
                linked_existing += 1
            else:
                bucket = accept.budget_bucket or suggestion.get("proposed_budget_bucket")
                tax_flag = (
                    accept.is_tax_deductible
                    if accept.is_tax_deductible is not None
                    else bool(suggestion.get("proposed_is_tax_deductible", False))
                )
                sort_result = await session.execute(
                    text(
                        "select coalesce(max(sort_order) + 1, 0) as next from categories "
                        "where household_id = :household_id and group_id = :group_id"
                    ),
                    {"household_id": str(household_id), "group_id": str(group_id)},
                )
                inserted = await session.execute(
                    text(
                        "insert into categories (household_id, group_id, name, default_type, "
                        "budget_bucket, is_budgeted, icon, synonyms, sort_order, origin, "
                        "is_tax_deductible, description) "
                        "values (:household_id, :group_id, :name, :default_type, :budget_bucket, "
                        "true, :icon, cast(:synonyms as text[]), :sort_order, 'user', "
                        ":is_tax_deductible, :description) returning id"
                    ),
                    {
                        "household_id": str(household_id),
                        "group_id": str(group_id),
                        "name": name,
                        "default_type": suggestion.get("proposed_default_type") or "expense",
                        "budget_bucket": bucket,
                        "icon": suggestion.get("proposed_icon"),
                        "synonyms": list(suggestion.get("proposed_synonyms") or []),
                        "sort_order": int(sort_result.one().next),
                        "is_tax_deductible": tax_flag,
                        "description": (
                            "Von der KI aus "
                            f"{suggestion['transaction_count']} Buchungen von "
                            f"{suggestion['payee_label']} vorgeschlagen."
                        )[:400],  # fest: Anzeigetext der Kategorie, kein Promptmaterial
                    },
                )
                target_id = UUID(str(inserted.one().id))
                target_name = name
                created = True
                created_categories += 1

        assigned = 0
        if payload.assign_transactions and target_id is not None:
            assigned = await _assign_transactions(
                session, household_id, target_id, transaction_ids
            )
        skipped = len(transaction_ids) - assigned if payload.assign_transactions else 0
        assigned_total += assigned
        skipped_total += skipped

        items.append(
            CategorySuggestionResultItem(
                cluster_key=accept.cluster_key,
                category_id=target_id,
                category_name=target_name,
                created=created,
                assigned_transactions=assigned,
                skipped_transactions=skipped,
            )
        )

    result = CategorySuggestionResult(
        created_categories=created_categories,
        linked_existing=linked_existing,
        assigned_transactions=assigned_total,
        skipped_transactions=skipped_total,
        items=items,
    )

    await session.execute(
        text(
            "update category_operations set status = 'applied', applied_at = now(), "
            "request_id = coalesce(:request_id, request_id), "
            "result = cast(:result as jsonb) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "request_id": payload.request_id,
            "result": json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
            "id": str(payload.operation_id),
            "household_id": str(household_id),
        },
    )

    # Eine Queue-Aufgabe ist nur der Vorschlagslauf. Erst dieser bestaetigte
    # Fachschritt schliesst sie ab; die KI selbst legt weiterhin nichts an.
    await session.execute(
        text(
            "update ai_tasks set status = 'completed', current_stage = 'completed', "
            "status_message = 'Kategorienvorschlaege wurden bestaetigt', "
            "completed_at = coalesce(completed_at, now()), updated_at = now() "
            "where household_id = :household_id and category_operation_id = :operation_id "
            "and status = 'awaiting_confirmation'"
        ),
        {"household_id": str(household_id), "operation_id": str(payload.operation_id)},
    )
    await session.execute(
        text(
            "insert into ai_task_events (task_id, household_id, stage, message) "
            "select id, household_id, 'completed', "
            "'Kategorienvorschlaege wurden bestaetigt und angewendet.' from ai_tasks "
            "where household_id = :household_id and category_operation_id = :operation_id "
            "on conflict do nothing"
        ),
        {"household_id": str(household_id), "operation_id": str(payload.operation_id)},
    )

    # Wie beim Paket-Apply: die Operation betrifft den Baum als Ganzes, als
    # Entitaet wird deshalb der Haushalt gefuehrt.
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        household_id,
        "suggestion_apply",
        after=result.model_dump(mode="json"),
        correlation_id=correlation_id,
        metadata={
            "operation_id": str(payload.operation_id),
            "model_name": impact.get("model_name"),
            "assign_transactions": payload.assign_transactions,
        },
    )
    return result
