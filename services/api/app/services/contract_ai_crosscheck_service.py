"""Gegenpruefung durch das lokal eingebundene Sprachmodell.

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.11 (P5.3). Der Dienst kennt zwei
Modi, die sich Vorfilterung, Belegaufbereitung, Maskierung, Antwortpruefung und
Modellanbindung teilen:

* **Vertragspaare** (`crosscheck_household`): ist das fachlich EIN Vertrag
  (dieselbe Leistung nach einer Preisaenderung) oder sind es ZWEI getrennte
  Vertraege desselben Anbieters?
* **Freie Buchungen** (`check_transactions_for_contract`): gehoert diese noch
  keinem Vertrag zugeordnete Buchung zu DIESEM Vertrag? Das ist die Nachlese zu
  Abschnitt 19.7 - der Nutzer hat die fehlenden Buchungen von Hand gesucht und
  laesst den Rest suchen. Auch hier gilt: das Modell schlaegt vor, zugeordnet
  wird ueber den bestehenden `reassign`-Weg durch den Nutzer.

Der Rest dieser Beschreibung gilt fuer beide Modi.

Warum es das ueberhaupt braucht
-------------------------------
Die deterministische Duplikatabfrage
(`rules_service.get_contract_duplicate_suggestions`) verlangt
`older.last_seen_date is not null` und `newer.first_seen_date is not null`. In
einem migrierten Bestand sind beide Felder bei allen Legacy-Uebernahmen leer
und saemtliche Buchungen tragen `payee_id is null` - die Abfrage kann dort
nichts vorschlagen, obwohl drei Zeilen desselben Anbieters fachlich zwei
Vertraege sind.
Genau diese Restmenge ist der Fall, fuer den Abschnitt 19.11 das Modell
zulaesst.

Was das Modell darf und was nicht
---------------------------------
Es beurteilt und begruendet in Klartext. Es schreibt kein Feld, fuehrt keinen
Merge aus und liefert keine Prozent-"Konfidenz" (Leitsatz 8 und Abschnitt 18).
Das Antwortschema kennt deshalb gar kein Konfidenzfeld, und eine Begruendung
mit Prozentangabe wird verworfen statt gekuerzt. Das Ergebnis wird nicht
persistiert; bestaetigt wird ueber die bestehende Merge-Vorschau.

Deterministische Vorfilterung vor jedem Modellaufruf
----------------------------------------------------
1. Nur nicht zusammengefuehrte Vertraege (`merged_into_id is null`) mit
   gleicher Richtung und gleichem normalisiertem Anbieter/Empfaenger.
2. Zwei vorhandene, VERSCHIEDENE `contract_key` schliessen das Paar hart aus
   (Leitsatz 4) - solche Paare werden nicht einmal gefragt.
3. Vom Nutzer verneinte Paare bleiben verneint. Die Bedingung ist woertlich
   dieselbe wie in `rules_service` (contract_events kind='dismissed', Feld
   `payload->>'duplicate_of'`, symmetrisch in beide Richtungen) - eine zweite,
   leicht abweichende Formulierung waere der Weg, auf dem ein verworfenes Paar
   ueber einen anderen Endpunkt zurueckkommt.
4. Hoechstens `MAX_PAIRS` Paare je Lauf (Bediengrenze), und das Abschneiden
   wird gemeldet.

Modellaufruf und Belegmaterial
------------------------------
Jede Frage geht ueber den Gateway des KI-Kerns (app/llm): das Antwortbudget
kommt aus dem GELADENEN Fenster und dem Reserveanteil der Haushaltseinstellung,
Reasoning und Sampling unveraendert aus derselben Einstellung. Diese Datei setzt
weder ein Ausgabebudget noch einen Reasoning-Wert und kuerzt kein Belegmaterial
auf eine Zeichenzahl - frueher liefen hier `max_output_tokens=min(..., 512)` und
erzwungenes `reasoning="off"`. Passt eine Frage nicht ins Fenster, sagt der
Gateway das mit `model_input_too_large`. Die Antwort prueft der KI-Kern
(llm.answers.ask: abgeschnitten, JSON aus content oder dem Denktext derselben
Antwort, Protokollzeile markieren); jeder Fehlschlag wird mit seinem Grund
gezaehlt und in `meldung` genannt (`_ModelRun`).

Je Vertrag gehen hoechstens zwei Buchungsbeispiele an das Modell
(Datensparsamkeit, siehe `_SAMPLES_PER_CONTRACT`).

Der Verwendungszweck laeuft durch die vorhandene Maskierung
(`ai_service.mask_pii_text`) - eine zweite Maskierung gibt es bewusst nicht.
Diese Maskierung ersetzt allerdings jede 10- bis 16-stellige Ziffernfolge, und
genau darunter faellt die Referenz, an der der Fall haengt ("Vertragskonto
1122334455" gegen "Kundenkonto 0011223344"). Ohne sie waeren beide Belege
ununterscheidbar und die Frage nicht beantwortbar. Deshalb steht die Referenz
zusaetzlich als eigene Zeile im Prompt - nicht aus dem Text herausgeschnitten,
sondern von `contract_detection_service.extract_mandate_key` deterministisch
abgeleitet, also genau der Wert, der auch `contracts.contract_key` fuellt. Es
ist eine Vertragsreferenz, keine Konto- oder Kartennummer. Beim lokalen
Anbieter verlaesst sie den Rechner nicht; ist Anthropic gewaehlt
(app/llm/providers.py), geht sie mit dem Prompt dorthin - die Einstellungen
sagen das ausdruecklich.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm import answers, reasons
from app.schemas.contract import (
    Contract,
    ContractAICrosscheckItem,
    ContractAICrosscheckPartner,
    ContractAICrosscheckResponse,
    ContractAICrosscheckSkip,
    ContractAICrosscheckSkipReason,
    ContractAITransactionCheckResponse,
    ContractAITransactionItem,
    ContractAITransactionSkip,
    ContractAITransactionSkipReason,
    ContractEvidenceTransaction,
    ContractFreeTransactionFilter,
)
from app.services import ai_service, ai_settings_service, contract_service
from app.services.contract_detection_service import (
    extract_mandate_key,
    normalize_purpose_for_key,
)
from app.services.contract_matching_service import amount_tolerance_minor, payee_key

# Obergrenze je Lauf: ein Lauf ist ein Knopfdruck bzw. eine Aufgabe der
# KI-Warteschlange, kein Abarbeiten des ganzen Bestands. Alles darueber meldet
# der Endpunkt als `truncated`, statt es stillschweigend wegzulassen. Keine
# Kontextgrenze - jedes Paar ist ein eigener Aufruf mit Budget aus dem Fenster.
# fest: Bediengrenze - hoechstens zwoelf Paare je Lauf, der Rest wird gemeldet
MAX_PAIRS: Final = 12

# Belegmaterial je Vertrag. Zwei Beispiele zeigen Referenz und Betragsniveau;
# ein drittes wiederholt nur beides und gibt eine weitere Buchung des
# Haushalts an das Modell, ohne die Frage besser beantwortbar zu machen.
# fest: Datensparsamkeit - zwei Buchungsbeispiele je Vertrag
_SAMPLES_PER_CONTRACT: Final = 2

# Antwortvertrag: "ein bis zwei kurze, sachliche Saetze". Eine laengere
# Begruendung ist kein Urteil im vereinbarten Format und gilt als nicht
# beurteilt - keine Grenze fuer Promptmaterial und kein Antwortbudget.
# fest: Antwortvertrag (Anzeige) - Begruendung hoechstens 400 Zeichen
_MAX_REASON_CHARS: Final = 400

# Rueckblick fuer die Ersatzsuche nach Buchungsbelegen. Deckungsgleich mit dem
# Legacy-Reparaturlauf (`_REPAIR_MONTHS_BACK`), damit beide dieselben Buchungen
# sehen. Grenzen einer Datenbankabfrage, nicht des Modells.
# fest: Abfragezeitraum der Belegsuche (wie der Reparaturlauf)
_POOL_DAYS_BACK: Final = 36 * 31
# fest: Zeilen der Belegsuche in der Datenbank, an das Modell gehen nur zwei je Vertrag
_POOL_LIMIT: Final = 2000

# llm_calls: Ort und Promptfassung der beiden Modi.
CROSSCHECK_STAGE: Final = "contract_crosscheck"
CROSSCHECK_PROMPT_VERSION: Final = "contract-crosscheck-v2"
TRANSACTION_STAGE: Final = "contract_transaction_check"
TRANSACTION_PROMPT_VERSION: Final = "contract-transaction-check-v2"

CROSSCHECK_SUBJECT: Final = "Die Gegenprüfung"
CROSSCHECK_OUTCOME: Final = "An den Verträgen wurde nichts verändert."

AI_DISABLED_MESSAGE: Final = (
    "Die KI-Unterstützung ist für diesen Haushalt ausgeschaltet. Die Gegenprüfung "
    "lässt sich erst nutzen, wenn sie in den KI-Einstellungen wieder eingeschaltet "
    "ist."
)

# 'not_a_contract' fehlt bewusst: eine als "kein Vertrag" markierte Zeile ist
# keine Haelfte eines Duplikatpaares. Gleiche Auswahl wie in der
# deterministischen Duplikatabfrage.
_CONTRACT_SQL: Final = """
select id, name, provider_label, payee_id, direction, amount_minor, frequency,
       due_day, first_seen_date, last_seen_date, contract_key
from contracts
where household_id = :household_id
  and merged_into_id is null
  and status in ('active', 'paused', 'cancelled', 'ended')
  {extra}
order by name, id
"""

_DISMISSED_SQL: Final = """
select contract_id, payload ->> 'duplicate_of' as duplicate_of
from contract_events
where household_id = :household_id
  and kind = 'dismissed'
  and payload ->> 'duplicate_of' is not null
"""

# Zugeordnete Buchungen ueber die Faelligkeiten. `lateral` deckelt je Vertrag
# statt insgesamt - ein Vertrag mit vielen Treffern darf einem anderen die
# Beispiele nicht wegnehmen.
_OCCURRENCE_SAMPLE_SQL: Final = """
select c.id as contract_id, s.booked_date, s.amount_minor, s.purpose_raw
from contracts c
join lateral (
  select t.booked_date, t.amount_minor, t.purpose_raw
  from contract_occurrences o
  join transactions t on t.id = o.transaction_id and t.household_id = o.household_id
  where o.household_id = c.household_id
    and o.contract_id = c.id
    and o.transaction_id is not null
    and t.purpose_raw is not null
    and not t.is_archived
  order by o.due_date desc
  limit :samples
) s on true
where c.household_id = :household_id
  and c.id = any(cast(:ids as uuid[]))
"""

# Ersatzsuche fuer genau den Fall, der diesen Dienst noetig macht: Legacy-Zeilen
# ohne Faelligkeiten. Bewusst EINE Abfrage fuer alle betroffenen Vertraege - die
# Zuordnung passiert danach in Python ueber denselben Empfaengerschluessel und
# dieselbe Betragstoleranz wie der Abgleich.
_POOL_SQL: Final = """
select t.booked_date, t.amount_minor, t.purpose_raw,
       coalesce(p.name, t.payee_label) as payee_label
from transactions t
left join payees p on p.id = t.payee_id and p.household_id = t.household_id
where t.household_id = :household_id
  and not t.is_archived
  and not t.is_transfer
  and t.purpose_raw is not null
  and t.booked_date >= :from_date
order by t.booked_date desc
limit :pool_limit
"""

_FREQUENCY_LABEL: Final[dict[str, str]] = {
    "weekly": "wöchentlich",
    "monthly": "monatlich",
    "quarterly": "vierteljährlich",
    "semiannual": "halbjährlich",
    "yearly": "jährlich",
}


@dataclass(frozen=True)
class ContractRow:
    """Die Felder, die fuer Vorfilterung und Prompt gebraucht werden."""

    id: UUID
    name: str
    provider_label: str | None
    payee_id: UUID | None
    direction: str
    amount_minor: int
    frequency: str
    due_day: int | None
    first_seen_date: date | None
    last_seen_date: date | None
    contract_key: str | None

    @property
    def provider(self) -> str:
        return self.provider_label or self.name


@dataclass(frozen=True)
class EvidenceSample:
    """Eine Buchung als Beleg - Zweck maskiert, Referenz getrennt daneben."""

    booked_date: date
    amount_minor: int
    purpose_masked: str
    reference: str | None


# ---------------------------------------------------------------------------
# Bestand und Vorfilterung
# ---------------------------------------------------------------------------


async def _load_contracts(
    session: AsyncSession, household_id: UUID, contract_ids: Sequence[UUID] | None
) -> list[ContractRow]:
    params: dict[str, Any] = {"household_id": str(household_id)}
    extra = ""
    if contract_ids:
        extra = "and id = any(cast(:ids as uuid[]))"
        params["ids"] = [str(item) for item in contract_ids]
    result = await session.execute(text(_CONTRACT_SQL.format(extra=extra)), params)
    return [
        ContractRow(
            id=UUID(str(row.id)),
            name=str(row.name),
            provider_label=row.provider_label,
            payee_id=UUID(str(row.payee_id)) if row.payee_id is not None else None,
            direction=str(row.direction),
            amount_minor=int(row.amount_minor),
            frequency=str(row.frequency),
            due_day=int(row.due_day) if row.due_day is not None else None,
            first_seen_date=row.first_seen_date,
            last_seen_date=row.last_seen_date,
            contract_key=row.contract_key,
        )
        for row in result
    ]


async def _load_dismissed_pairs(
    session: AsyncSession, household_id: UUID
) -> set[frozenset[UUID]]:
    """Vom Nutzer verneinte Paare, symmetrisch.

    Ein `dismissed`-Ereignis haengt an einem der beiden Vertraege und nennt den
    anderen in `payload->>'duplicate_of'`. Welche Seite das Ereignis traegt,
    haengt davon ab, wo der Nutzer geklickt hat - deshalb wird das Paar
    ungeordnet gemerkt (dieselbe Zusicherung wie die beiden or-verknuepften
    Bedingungen in rules_service).
    """
    result = await session.execute(text(_DISMISSED_SQL), {"household_id": str(household_id)})
    pairs: set[frozenset[UUID]] = set()
    for row in result:
        try:
            other = UUID(str(row.duplicate_of))
        except ValueError:
            # Ein Ereignis mit unbrauchbarem Payload darf den Lauf nicht
            # kippen; es kann nur kein Paar bezeichnen.
            continue
        pairs.add(frozenset({UUID(str(row.contract_id)), other}))
    return pairs


def _same_provider(left: ContractRow, right: ContractRow) -> bool:
    """Gleiche Gegenpartei - dieselbe oder-Regel wie die Duplikatabfrage.

    `payee_id` gewinnt, wenn beide Seiten einen haben; ansonsten entscheidet der
    normalisierte Anbietername. Im Migrationsbestand ist `payee_id` durchgehend leer,
    der Namensweg ist dort also der einzige - und `payee_key` ist derselbe
    Normalisierer, den Erkennung und Abgleich verwenden.
    """
    if left.payee_id is not None and left.payee_id == right.payee_id:
        return True
    key = payee_key(left.provider)
    return bool(key) and key == payee_key(right.provider)


def _ordered_pair(
    left: ContractRow, right: ContractRow
) -> tuple[ContractRow, ContractRow]:
    """Stabile Reihenfolge: aelterer bzw. teurerer Vertrag zuerst.

    Rein fuer die Lesbarkeit von Prompt und Anzeige - die Frage selbst ist
    symmetrisch. Ohne feste Regel bekaeme derselbe Fall bei jedem Lauf eine
    andere Reihenfolge, und zwei Antworten waeren nicht mehr vergleichbar.
    """

    def sort_key(row: ContractRow) -> tuple[int, str, int, str]:
        return (
            0 if row.first_seen_date is not None else 1,
            row.first_seen_date.isoformat() if row.first_seen_date else "",
            -row.amount_minor,
            str(row.id),
        )

    return (left, right) if sort_key(left) <= sort_key(right) else (right, left)


def candidate_pairs(contracts: Sequence[ContractRow]) -> list[tuple[ContractRow, ContractRow]]:
    """Alle Paare gleicher Richtung und gleichen Anbieters, jedes genau einmal.

    Quadratisch ueber die Vertraege eines Haushalts - das sind Dutzende, keine
    Tausende, und die or-Regel aus `_same_provider` laesst sich in SQL nicht
    ohne Verrenkungen als Gruppierung schreiben.
    """
    pairs: list[tuple[ContractRow, ContractRow]] = []
    for index, left in enumerate(contracts):
        for right in contracts[index + 1 :]:
            if left.direction != right.direction:
                continue
            if not _same_provider(left, right):
                continue
            pairs.append(_ordered_pair(left, right))
    return pairs


def prefilter_reason(
    left: ContractRow, right: ContractRow, dismissed: set[frozenset[UUID]]
) -> ContractAICrosscheckSkipReason | None:
    """Warum ein Paar gar nicht erst gefragt wird - oder None."""
    if (
        left.contract_key is not None
        and right.contract_key is not None
        and left.contract_key != right.contract_key
    ):
        return "different_contract_key"
    if frozenset({left.id, right.id}) in dismissed:
        return "dismissed_by_user"
    return None


# ---------------------------------------------------------------------------
# Belegmaterial
# ---------------------------------------------------------------------------


def _sample(booked_date: date, amount_minor: int, purpose_raw: str | None) -> EvidenceSample:
    """Rohzweck -> Beleg fuer den Prompt.

    Reihenfolge ist wichtig: die Referenz wird VOR der Maskierung aus dem
    Rohtext abgeleitet (sonst waere sie bereits ersetzt), der Anzeigetext
    danach maskiert. Gekuerzt wird er nicht mehr: ob die Frage ins Fenster
    passt, rechnet der Gateway aus dem geladenen Fenster.
    """
    reference = extract_mandate_key(purpose_raw)
    normalized = normalize_purpose_for_key(purpose_raw)
    masked = " ".join(ai_service.mask_pii_text(normalized).split())
    return EvidenceSample(
        booked_date=booked_date,
        amount_minor=amount_minor,
        purpose_masked=masked,
        reference=reference,
    )


def _pool_matches(row: ContractRow, payee_label: str | None, amount_minor: int) -> bool:
    if row.direction == "expense" and amount_minor >= 0:
        return False
    if row.direction == "income" and amount_minor <= 0:
        return False
    key = payee_key(row.provider)
    if not key or key != payee_key(payee_label):
        return False
    absolute = abs(amount_minor)
    return abs(row.amount_minor - absolute) <= amount_tolerance_minor(absolute)


async def load_evidence(
    session: AsyncSession, household_id: UUID, contracts: Sequence[ContractRow]
) -> dict[UUID, list[EvidenceSample]]:
    """Belege je Vertrag: erst die zugeordneten Buchungen, sonst die Ersatzsuche.

    Die Ersatzsuche ist kein Notbehelf, sondern der Regelfall dieses Dienstes:
    Legacy-Vertraege haben keine `contract_occurrences`, und ohne Belege waere
    die Frage an das Modell inhaltsleer.
    """
    if not contracts:
        return {}

    samples: dict[UUID, list[EvidenceSample]] = {contract.id: [] for contract in contracts}
    result = await session.execute(
        text(_OCCURRENCE_SAMPLE_SQL),
        {
            "household_id": str(household_id),
            "ids": [str(contract.id) for contract in contracts],
            "samples": _SAMPLES_PER_CONTRACT,
        },
    )
    for row in result:
        samples[UUID(str(row.contract_id))].append(
            _sample(row.booked_date, int(row.amount_minor), row.purpose_raw)
        )

    missing = [contract for contract in contracts if not samples[contract.id]]
    if not missing:
        return samples

    pool = await session.execute(
        text(_POOL_SQL),
        {
            "household_id": str(household_id),
            "from_date": date.today() - timedelta(days=_POOL_DAYS_BACK),
            "pool_limit": _POOL_LIMIT,
        },
    )
    pool_rows = pool.fetchall()
    for contract in missing:
        for candidate in pool_rows:
            if len(samples[contract.id]) >= _SAMPLES_PER_CONTRACT:
                break
            if not _pool_matches(contract, candidate.payee_label, int(candidate.amount_minor)):
                continue
            samples[contract.id].append(
                _sample(
                    candidate.booked_date, int(candidate.amount_minor), candidate.purpose_raw
                )
            )
    return samples


# ---------------------------------------------------------------------------
# Prompt und Modellaufruf
# ---------------------------------------------------------------------------


def _amount_eur(amount_minor: int) -> str:
    # Deutsches Dezimalkomma: der ganze Prompt ist deutsch, ein Punkt liest sich
    # dort wie ein Tausendertrenner.
    return f"{abs(amount_minor) / 100:.2f} EUR".replace(".", ",")


CROSSCHECK_JSON_SCHEMA: Final[dict[str, Any]] = {
    "name": "contract_crosscheck",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "gehoert_zusammen": {"type": "boolean"},
            "begruendung": {"type": "string"},
        },
        # Kein Konfidenzfeld, und `additionalProperties: false` verhindert, dass
        # das Modell eines dazuerfindet. Das ist die erste Durchsetzung von
        # Leitsatz 8, die Pruefung in `parse_crosscheck_answer` die zweite.
        "required": ["gehoert_zusammen", "begruendung"],
        "additionalProperties": False,
    },
}

CROSSCHECK_SYSTEM_PROMPT: Final = (
    "Du bist ein nüchterner deutschsprachiger Prüfassistent für die Verträge "
    "eines Privathaushalts. Du bekommst ZWEI Vertragszeilen desselben Anbieters "
    "und beantwortest GENAU EINE Frage: Ist das fachlich EIN Vertrag - zum "
    "Beispiel derselbe Anschluss nach einer Preisänderung - oder sind es ZWEI "
    "getrennte Verträge?\n\n"
    "Woran du es erkennst:\n"
    "- Verschiedene Vertrags- oder Kundenkonten bzw. verschiedene "
    "Mandatsreferenzen bedeuten ZWEI Verträge, auch bei gleichem Anbieter und "
    "ähnlichem Betrag.\n"
    "- Dieselbe Referenz mit unterschiedlichen Beträgen ist EIN Vertrag mit "
    "einer Preisänderung.\n"
    "- Überlappende Zeiträume - beide werden im selben Monat bezahlt - bedeuten "
    "ZWEI Verträge.\n"
    "- Unterschiedliche Leistungen im Verwendungszweck (Festnetz gegen "
    "Mobilfunk) sprechen für zwei Verträge.\n"
    "- Geben die Belege nichts Eindeutiges her, antworte mit "
    "gehoert_zusammen=false. Ein falsches Zusammenführen kostet Daten, ein "
    "übersehener Fall nur einen zweiten Blick.\n\n"
    "Regeln für die Begründung:\n"
    "- Ein bis zwei kurze, sachliche Sätze auf Deutsch.\n"
    "- Nenne den Beleg, auf den du dich stützt: Referenz, Zeitraum oder "
    "Leistung.\n"
    "- KEINE Prozentangabe, keine Konfidenz, keine Wahrscheinlichkeit. Solche "
    "Zahlen wären erfunden und machen die Antwort unbrauchbar.\n"
    "- Du entscheidest nichts endgültig; ein Mensch bestätigt oder verwirft "
    "deinen Hinweis.\n\n"
    "Antworte ausschließlich mit einem JSON-Objekt der Form "
    '{"gehoert_zusammen": true oder false, "begruendung": "..."}.'
)


def _contract_block(
    label: str, row: ContractRow, samples: Sequence[EvidenceSample]
) -> list[str]:
    frequency = _FREQUENCY_LABEL.get(row.frequency, row.frequency)
    head = f"{label}: {_amount_eur(row.amount_minor)} | {frequency}"
    if row.due_day is not None:
        head += f" | fällig am {row.due_day}."
    lines = [head]

    references = list(dict.fromkeys(item.reference for item in samples if item.reference))
    lines.append(
        "  Referenz im Verwendungszweck: "
        + (", ".join(references) if references else "keine gefunden")
    )
    if row.first_seen_date and row.last_seen_date:
        lines.append(f"  Bekannter Zeitraum: {row.first_seen_date} bis {row.last_seen_date}")
    else:
        lines.append("  Bekannter Zeitraum: nicht hinterlegt")

    if samples:
        lines.append("  Buchungen:")
        lines.extend(
            f"  - {item.booked_date} | {_amount_eur(item.amount_minor)} | {item.purpose_masked}"
            for item in samples
        )
    else:
        lines.append("  Buchungen: keine zugeordnet")
    return lines


def build_crosscheck_user_prompt(
    left: ContractRow,
    right: ContractRow,
    evidence: dict[UUID, list[EvidenceSample]],
) -> str:
    lines = [f"Anbieter: {ai_service.mask_pii_text(left.provider)}"]
    lines.extend(_contract_block("Vertrag A", left, evidence.get(left.id, [])))
    lines.extend(_contract_block("Vertrag B", right, evidence.get(right.id, [])))
    return "\n".join(lines)


# Die Durchsetzung von "keine Prozent-Konfidenz" (Leitsatz 8). Gleiche Bauart
# wie die Ziffernsperre in `insights_service._advice_is_clean`: der Prompt
# verbietet es, diese Pruefung macht es verbindlich.
#
# Bewusst eng gefasst. Eine vollstaendige Ziffernsperre waere hier falsch - die
# Vertragsreferenz zu nennen IST die geforderte Begruendung. Und ein
# sprachlicher Vorbehalt ("vermutlich derselbe Anschluss") bleibt erlaubt: der
# Plan verlangt eine vorsichtige Formulierung, verboten ist nur die
# scheingenaue Zahl.
_FORBIDDEN_IN_REASON: Final = re.compile(
    r"%|\bprozent\w*|\bkonfidenz\w*|\bconfidence\w*|\bscore\w*",
    re.IGNORECASE,
)


def _parse_verdict(answer: dict[str, Any] | None, field: str) -> tuple[bool, str] | None:
    """Modellantwort -> (Urteil, Begruendung), sonst None.

    None heisst "nicht beurteilt" und ausdruecklich nicht "nein": ein
    unbrauchbares Urteil darf weder als Treffer noch als Freispruch durchgehen.
    Streng bis zum Typ - `isinstance(1, bool)` ist False, eine 1 statt true
    faellt also durch.

    Beide Modi teilen sich diese Pruefung. Eine zweite, leicht abweichende
    Fassung waere der Weg, auf dem die Prozentsperre in einem der beiden Modi
    stillschweigend fehlt.
    """
    if not isinstance(answer, dict):
        return None
    verdict = answer.get(field)
    if not isinstance(verdict, bool):
        return None
    reason = " ".join(str(answer.get("begruendung") or "").split())
    if not reason or len(reason) > _MAX_REASON_CHARS:
        return None
    if _FORBIDDEN_IN_REASON.search(reason):
        return None
    return verdict, reason


def parse_crosscheck_answer(answer: dict[str, Any] | None) -> tuple[bool, str] | None:
    """Antwort des Paar-Modus: (gehoert_zusammen, Begruendung) oder None."""
    return _parse_verdict(answer, "gehoert_zusammen")


Verdict = tuple[bool, str]

_CONTRACT_BREACH: Final = (
    "Die Antwort erfüllt den Antwortvertrag nicht (Urteil als Wahrheitswert, ein bis "
    "zwei Sätze Begründung ohne Prozent- oder Konfidenzangabe)."
)


class _ModelRun:
    """Buchfuehrung mehrerer Einzelfragen eines Laufs an dasselbe Modell.

    Wie eine einzelne Antwort verwertet wird (Gateway, abgeschnitten, JSON aus
    content oder dem Denktext derselben Antwort, Protokollzeile markieren),
    entscheidet der KI-Kern (llm.answers.ask). Dieser Lauf zaehlt nur:

    * Lehnt der Server die strukturierte Ausgabe ab (`chat_structured` fragt
      dann einmal ohne Schema), fragt der Rest des Laufs gleich ohne Schema.
    * Jeder Fehlschlag wird mit dem Grund des Kerns gezaehlt; ein Grund, der
      den Zustand von Server, Modell oder Einstellung beschreibt
      (llm.answers.ends_run), beendet den Lauf (`stopped_by`).
    """

    def __init__(
        self,
        session: AsyncSession,
        household_id: UUID,
        settings: llm.LLMSettings,
        target: llm.Target,
        *,
        stage: str,
        prompt_version: str,
    ) -> None:
        self._ready = answers.Ready(
            settings,
            target,
            llm.CallScope(
                session=session,
                household_id=household_id,
                stage=stage,
                prompt_version=prompt_version,
            ),
        )
        self._use_schema = True
        self.asked = 0
        self.failures: Counter[str] = Counter()
        self._first_message: dict[str, str] = {}
        self.stopped_by: str | None = None

    async def ask(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        parse: Callable[[dict[str, Any] | None], Verdict | None],
    ) -> Verdict | None:
        """Eine Frage. None heisst "nicht beurteilt" - der Grund steht dann in
        `failures`; ein einzelnes stummes Paar laesst den Lauf nicht scheitern."""
        self.asked += 1
        answer = await answers.ask(
            self._ready,
            llm.ChatRequest(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=(
                    {"type": "json_schema", "json_schema": json_schema}
                    if self._use_schema
                    else None
                ),
            ),
            accept=parse,
            breach=_CONTRACT_BREACH,
        )
        if answer.result.schema_fallback:
            self._use_schema = False
        if answer.value is not None:
            return answer.value
        code = answer.reason_code or reasons.MODEL_OUTPUT_UNUSABLE
        self.failures[code] += 1
        self._first_message.setdefault(code, answer.message or reasons.describe(code))
        if answers.ends_run(code):
            self.stopped_by = code
        return None

    def note(self, *, noun: str, judged: int, not_asked: int, outcome: str) -> str | None:
        """Klartext fuer `meldung`, wenn mindestens eine Frage ohne verwertbare
        Antwort blieb - sonst None. Nennt die Gruende des Kerns, damit eine
        leere Ergebnisliste nie wie "nichts gefunden" aussieht."""
        if not self.failures:
            return None
        failed = sum(self.failures.values())
        codes = ", ".join(
            f"{code} ×{count}" if count > 1 else code
            for code, count in self.failures.most_common()
        )
        main = self.failures.most_common(1)[0][0]
        parts = [
            f"Für {failed} von {self.asked} {noun} kam vom Sprachmodell keine verwertbare "
            f"Antwort ({codes}).",
            self._first_message[main],
        ]
        if self.stopped_by is not None and not_asked:
            parts.append(
                f"Der Lauf wurde deshalb beendet; {not_asked} weitere {noun} wurden nicht "
                "mehr gefragt."
            )
        parts.append(f"{judged} {noun} beurteilt. {outcome}")
        return " ".join(parts)


def _unavailable_message(subject: str, found: llm.TargetResult, outcome: str) -> str:
    """Klartext, wenn kein Modell nutzbar ist (model_unavailable, model_missing)."""
    detail = found.message or reasons.describe(found.reason_code or reasons.MODEL_UNAVAILABLE)
    return (
        f"{subject} braucht das lokale Sprachmodell und kann es gerade nicht nutzen. "
        f"{detail} {outcome}"
    )


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------


def _partner(row: ContractRow) -> ContractAICrosscheckPartner:
    return ContractAICrosscheckPartner(
        id=row.id, name=row.name, amount_minor=row.amount_minor
    )


def _skip(
    left: ContractRow, right: ContractRow, reason: ContractAICrosscheckSkipReason
) -> ContractAICrosscheckSkip:
    return ContractAICrosscheckSkip(
        contract_a=_partner(left), contract_b=_partner(right), grund=reason
    )


def _result(
    items: list[ContractAICrosscheckItem],
    skipped: list[ContractAICrosscheckSkip],
    truncated: bool,
    *,
    modell_name: str | None = None,
    meldung: str | None = None,
) -> ContractAICrosscheckResponse:
    return ContractAICrosscheckResponse(
        items=items,
        geprueft=len(items),
        uebersprungen=len(skipped),
        uebersprungen_gruende=skipped,
        truncated=truncated,
        modell_name=modell_name,
        meldung=meldung,
    )


async def crosscheck_household(
    session: AsyncSession,
    household_id: UUID,
    *,
    contract_ids: Sequence[UUID] | None = None,
) -> ContractAICrosscheckResponse:
    """Laesst das Modell die verbliebenen Kandidatenpaare beurteilen.

    Schreibt nichts und fuehrt nichts zusammen. Die Reihenfolge ist bindend:
    erst die vollstaendige deterministische Vorfilterung, dann die Begrenzung,
    dann erst der erste Modellaufruf. So haengt die Menge der hart
    ausgeschlossenen Paare nicht davon ab, ob ein Modell erreichbar war.
    """
    contracts = await _load_contracts(session, household_id, contract_ids)
    dismissed = await _load_dismissed_pairs(session, household_id)

    pairs: list[tuple[ContractRow, ContractRow]] = []
    skipped: list[ContractAICrosscheckSkip] = []
    for left, right in candidate_pairs(contracts):
        reason = prefilter_reason(left, right, dismissed)
        if reason is not None:
            skipped.append(_skip(left, right, reason))
            continue
        pairs.append((left, right))

    truncated = len(pairs) > MAX_PAIRS
    pairs = pairs[:MAX_PAIRS]

    # Ohne Kandidaten wird das Modell gar nicht erst gesucht: eine
    # Erreichbarkeitsmeldung waere hier irrefuehrend, denn es gab nichts zu tun.
    if not pairs:
        return _result([], skipped, truncated)

    # Die Haushaltseinstellung gilt unveraendert - auch Reasoning. Frueher lief
    # hier ein "sparsamer" Sonderweg (reasoning="off", hoechstens 512 Token);
    # das Antwortbudget rechnet jetzt der Gateway aus dem geladenen Fenster.
    settings = await ai_settings_service.resolve(session, household_id)
    found = await llm.resolve_target(settings)
    if found.reason_code == reasons.AI_DISABLED:
        return _result([], skipped, truncated, meldung=AI_DISABLED_MESSAGE)
    if found.target is None:
        return _result(
            [],
            skipped,
            truncated,
            meldung=_unavailable_message(CROSSCHECK_SUBJECT, found, CROSSCHECK_OUTCOME),
        )
    # Ein unbekanntes Fenster (model_context_unknown) laesst der Gateway beim
    # ersten Paar mit benanntem Grund und llm_calls-Zeile scheitern; der Lauf
    # endet dann dort (llm.answers.ends_run).
    target = found.target

    involved = {row.id: row for pair in pairs for row in pair}
    evidence = await load_evidence(session, household_id, list(involved.values()))

    run = _ModelRun(
        session,
        household_id,
        settings,
        target,
        stage=CROSSCHECK_STAGE,
        prompt_version=CROSSCHECK_PROMPT_VERSION,
    )
    items: list[ContractAICrosscheckItem] = []
    not_asked = 0
    for index, (left, right) in enumerate(pairs):
        parsed = await run.ask(
            CROSSCHECK_SYSTEM_PROMPT,
            build_crosscheck_user_prompt(left, right, evidence),
            CROSSCHECK_JSON_SCHEMA,
            parse_crosscheck_answer,
        )
        if parsed is None:
            skipped.append(_skip(left, right, "no_model_answer"))
            if run.stopped_by is not None:
                not_asked = len(pairs) - index - 1
                break
            continue
        belongs_together, begruendung = parsed
        items.append(
            ContractAICrosscheckItem(
                contract_a=_partner(left),
                contract_b=_partner(right),
                gehoert_zusammen=belongs_together,
                begruendung=begruendung,
            )
        )

    return _result(
        items,
        skipped,
        truncated or not_asked > 0,
        modell_name=target.model,
        meldung=run.note(
            noun="Paar(e)",
            judged=len(items),
            not_asked=not_asked,
            outcome=CROSSCHECK_OUTCOME,
        ),
    )


# ---------------------------------------------------------------------------
# Zweiter Modus: freie Buchungen zu EINEM Vertrag (Abschnitt 19.7)
# ---------------------------------------------------------------------------
#
# Der Ablauf ist derselbe wie oben, nur ist die Frage eine andere. Die
# deterministische Vorauswahl kommt vollstaendig aus
# `contract_service.search_free_transactions` - derselben Abfrage, die der
# Nutzer im Dialog "freie Buchungen suchen" sieht. Das ist Absicht: was das
# Modell zu sehen bekommt, muss der Nutzer vorher selbst haben nachschlagen
# koennen, und eine zweite, leicht andere Kandidatenmenge waere unerklaerbar.

# Obergrenze je Lauf, gleiche Ueberlegung wie MAX_PAIRS: ein Knopfdruck bzw.
# eine Aufgabe, kein Abarbeiten des Bestands; jede Buchung ist ein eigener
# Aufruf mit Budget aus dem Fenster.
# fest: Bediengrenze - hoechstens zwoelf Buchungen je Lauf, der Rest wird gemeldet
MAX_TRANSACTIONS: Final = 12

TRANSACTION_SUBJECT: Final = "Die Suche nach passenden Buchungen"
TRANSACTION_OUTCOME: Final = "Es wurde nichts zugeordnet."

TRANSACTION_AI_DISABLED_MESSAGE: Final = (
    "Die KI-Unterstützung ist für diesen Haushalt ausgeschaltet. Die Suche nach "
    "passenden Buchungen lässt sich erst nutzen, wenn sie in den KI-Einstellungen "
    "wieder eingeschaltet ist."
)

# Rohzwecke der Kandidaten. Bewusst nicht der gekuerzte `purpose_excerpt` aus
# der Belegliste: die Mandatsreferenz steht im MT940-Zweck weit hinten,
# und genau an ihr haengt die Frage.
_CANDIDATE_PURPOSE_SQL: Final = """
select t.id, t.purpose_raw
from transactions t
where t.household_id = :household_id
  and t.id = any(cast(:ids as uuid[]))
"""

TRANSACTION_JSON_SCHEMA: Final[dict[str, Any]] = {
    "name": "contract_transaction_check",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "gehoert_zum_vertrag": {"type": "boolean"},
            "begruendung": {"type": "string"},
        },
        # Wie im Paar-Modus: kein Konfidenzfeld, und nichts dazuerfinden.
        "required": ["gehoert_zum_vertrag", "begruendung"],
        "additionalProperties": False,
    },
}

TRANSACTION_SYSTEM_PROMPT: Final = (
    "Du bist ein nüchterner deutschsprachiger Prüfassistent für die Verträge "
    "eines Privathaushalts. Du bekommst EINEN Vertrag mit seinen bereits "
    "zugeordneten Buchungen und EINE weitere Buchung, die noch keinem Vertrag "
    "zugeordnet ist. Du beantwortest GENAU EINE Frage: Ist diese Buchung eine "
    "Zahlung für DIESEN Vertrag?\n\n"
    "Woran du es erkennst:\n"
    "- Dieselbe Vertrags-, Kunden- oder Mandatsreferenz wie der Vertrag ist der "
    "stärkste Beleg - auch dann, wenn der Betrag abweicht: ein Preis darf sich "
    "ändern.\n"
    "- Eine ANDERE Referenz bedeutet nein, auch bei gleichem Anbieter und "
    "ähnlichem Betrag.\n"
    "- Passt die Buchung in den Rhythmus des Vertrags (monatlich zum selben "
    "Termin, gleiche Leistung im Verwendungszweck), spricht das dafür.\n"
    "- Fällt die Buchung in einen Zeitraum, in dem der Vertrag bereits eine "
    "andere Zahlung hat, spricht das dagegen.\n"
    "- Geben die Belege nichts Eindeutiges her, antworte mit "
    "gehoert_zum_vertrag=false. Eine falsche Zuordnung verfälscht den "
    "Preisverlauf, ein übersehener Fall kostet nur einen zweiten Blick.\n\n"
    "Regeln für die Begründung:\n"
    "- Ein bis zwei kurze, sachliche Sätze auf Deutsch.\n"
    "- Nenne den Beleg, auf den du dich stützt: Referenz, Termin, Betrag oder "
    "Leistung.\n"
    "- KEINE Prozentangabe, keine Konfidenz, keine Wahrscheinlichkeit. Solche "
    "Zahlen wären erfunden und machen die Antwort unbrauchbar.\n"
    "- Du ordnest nichts zu; ein Mensch entscheidet über deinen Hinweis.\n\n"
    "Antworte ausschließlich mit einem JSON-Objekt der Form "
    '{"gehoert_zum_vertrag": true oder false, "begruendung": "..."}.'
)


def parse_transaction_answer(answer: dict[str, Any] | None) -> tuple[bool, str] | None:
    """Antwort des Buchungs-Modus: (gehoert_zum_vertrag, Begruendung) oder None."""
    return _parse_verdict(answer, "gehoert_zum_vertrag")


def row_from_contract(contract: Contract) -> ContractRow:
    """Vertragsmodell -> Promptzeile.

    Statt eines zweiten SELECT auf dieselbe Zeile: der Aufrufer hat den Vertrag
    bereits geladen (und damit auch die 404-Pruefung erledigt).
    """
    return ContractRow(
        id=contract.id,
        name=contract.name,
        provider_label=contract.provider_label,
        payee_id=contract.payee_id,
        direction=contract.direction,
        amount_minor=contract.amount_minor,
        frequency=contract.frequency,
        due_day=contract.due_day,
        first_seen_date=contract.first_seen_date,
        last_seen_date=contract.last_seen_date,
        contract_key=contract.contract_key,
    )


def build_transaction_user_prompt(
    row: ContractRow,
    contract_samples: Sequence[EvidenceSample],
    candidate: EvidenceSample,
) -> str:
    """Vertrag samt Belegen, darunter die eine zu beurteilende Buchung."""
    lines = [f"Anbieter: {ai_service.mask_pii_text(row.provider)}"]
    lines.extend(_contract_block("Vertrag", row, contract_samples))
    lines.append("Zu prüfende, noch nicht zugeordnete Buchung:")
    lines.append(
        f"  - {candidate.booked_date} | {_amount_eur(candidate.amount_minor)} | "
        f"{candidate.purpose_masked}"
    )
    lines.append(
        "  Referenz im Verwendungszweck: "
        + (candidate.reference if candidate.reference else "keine gefunden")
    )
    return "\n".join(lines)


async def _candidate_samples(
    session: AsyncSession,
    household_id: UUID,
    candidates: Sequence[ContractEvidenceTransaction],
) -> dict[UUID, EvidenceSample]:
    """Belege der Kandidaten - Referenz aus dem Rohzweck, Anzeigetext maskiert.

    Genau dieselbe Aufbereitung wie fuer die Vertragsbelege (`_sample`), damit
    im Prompt Gleiches mit Gleichem verglichen wird.
    """
    if not candidates:
        return {}
    purposes: dict[UUID, str | None] = {}
    result = await session.execute(
        text(_CANDIDATE_PURPOSE_SQL),
        {
            "household_id": str(household_id),
            "ids": [str(item.transaction_id) for item in candidates],
        },
    )
    for row in result:
        purposes[UUID(str(row.id))] = row.purpose_raw
    return {
        item.transaction_id: _sample(
            item.booked_date, item.amount_minor, purposes.get(item.transaction_id)
        )
        for item in candidates
    }


def _transaction_skip(
    item: ContractEvidenceTransaction | None,
    transaction_id: UUID,
    reason: ContractAITransactionSkipReason,
) -> ContractAITransactionSkip:
    return ContractAITransactionSkip(
        transaction_id=transaction_id,
        booked_date=item.booked_date if item is not None else None,
        amount_minor=item.amount_minor if item is not None else None,
        grund=reason,
    )


def _transaction_result(
    contract_id: UUID,
    items: list[ContractAITransactionItem],
    skipped: list[ContractAITransactionSkip],
    truncated: bool,
    candidate_total: int,
    filter_: ContractFreeTransactionFilter,
    *,
    modell_name: str | None = None,
    meldung: str | None = None,
) -> ContractAITransactionCheckResponse:
    return ContractAITransactionCheckResponse(
        contract_id=contract_id,
        items=items,
        geprueft=len(items),
        uebersprungen=len(skipped),
        uebersprungen_gruende=skipped,
        truncated=truncated,
        kandidaten_gesamt=candidate_total,
        vorauswahl=filter_,
        modell_name=modell_name,
        meldung=meldung,
    )


async def check_transactions_for_contract(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    *,
    transaction_ids: Sequence[UUID] | None = None,
) -> ContractAITransactionCheckResponse:
    """Laesst das Modell freie Buchungen gegen EINEN Vertrag beurteilen.

    Schreibt nichts und ordnet nichts zu. Die Reihenfolge ist dieselbe wie im
    Paar-Modus und ebenso bindend: erst die vollstaendige deterministische
    Vorauswahl (frei, Richtung, Zeitraum, Betragsband, Anbieterwort), dann die
    Deckelung, dann erst der erste Modellaufruf. Eine vom Nutzer geloeste
    Buchung ist damit schon vor dem Modell unsichtbar (Leitsatz 5), und die
    Kandidatenmenge haengt nicht davon ab, ob ein Modell erreichbar war.
    """
    contract = await contract_service.require_contract(session, household_id, contract_id)
    filter_ = await contract_service.build_free_transaction_filter(
        session, household_id, contract
    )
    # Bei benannten Buchungen wird zuerst die ganze Auswahl geholt und erst
    # danach gedeckelt. Sonst waeren die abgeschnittenen Buchungen von den
    # tatsaechlich nicht freien nicht mehr zu unterscheiden, und die Antwort
    # meldete "nicht frei" fuer etwas, das nur nicht mehr drangekommen ist.
    pool = await contract_service.search_free_transactions(
        session,
        household_id,
        contract,
        filter_,
        limit=len(transaction_ids) if transaction_ids else MAX_TRANSACTIONS,
        transaction_ids=transaction_ids,
    )

    preselected = {item.transaction_id for item in pool.items}
    skipped: list[ContractAITransactionSkip] = [
        _transaction_skip(None, item, "not_free")
        for item in (transaction_ids or [])
        if item not in preselected
    ]
    candidates = pool.items[:MAX_TRANSACTIONS]
    truncated = pool.truncated or len(pool.items) > MAX_TRANSACTIONS

    def result(
        items: list[ContractAITransactionItem],
        *,
        modell_name: str | None = None,
        meldung: str | None = None,
    ) -> ContractAITransactionCheckResponse:
        return _transaction_result(
            contract_id,
            items,
            skipped,
            truncated,
            pool.total,
            filter_,
            modell_name=modell_name,
            meldung=meldung,
        )

    # Ohne Kandidaten wird das Modell gar nicht erst gesucht - eine
    # Erreichbarkeitsmeldung waere hier irrefuehrend (wie im Paar-Modus).
    if not candidates:
        return result([])

    # Haushaltseinstellung unveraendert, Budget aus dem Fenster (wie im
    # Paar-Modus; frueher reasoning="off" und hoechstens 512 Token).
    settings = await ai_settings_service.resolve(session, household_id)
    found = await llm.resolve_target(settings)
    if found.reason_code == reasons.AI_DISABLED:
        return result([], meldung=TRANSACTION_AI_DISABLED_MESSAGE)
    if found.target is None:
        return result(
            [], meldung=_unavailable_message(TRANSACTION_SUBJECT, found, TRANSACTION_OUTCOME)
        )
    target = found.target

    row = row_from_contract(contract)
    evidence = await load_evidence(session, household_id, [row])
    samples = await _candidate_samples(session, household_id, candidates)

    run = _ModelRun(
        session,
        household_id,
        settings,
        target,
        stage=TRANSACTION_STAGE,
        prompt_version=TRANSACTION_PROMPT_VERSION,
    )
    items: list[ContractAITransactionItem] = []
    not_asked = 0
    for index, candidate in enumerate(candidates):
        parsed = await run.ask(
            TRANSACTION_SYSTEM_PROMPT,
            build_transaction_user_prompt(
                row, evidence.get(row.id, []), samples[candidate.transaction_id]
            ),
            TRANSACTION_JSON_SCHEMA,
            parse_transaction_answer,
        )
        if parsed is None:
            skipped.append(
                _transaction_skip(candidate, candidate.transaction_id, "no_model_answer")
            )
            if run.stopped_by is not None:
                not_asked = len(candidates) - index - 1
                break
            continue
        belongs, begruendung = parsed
        items.append(
            ContractAITransactionItem(
                transaction_id=candidate.transaction_id,
                booked_date=candidate.booked_date,
                amount_minor=candidate.amount_minor,
                payee_label=candidate.payee_label,
                gehoert_zum_vertrag=belongs,
                begruendung=begruendung,
            )
        )

    truncated = truncated or not_asked > 0
    return result(
        items,
        modell_name=target.model,
        meldung=run.note(
            noun="Buchung(en)",
            judged=len(items),
            not_asked=not_asked,
            outcome=TRANSACTION_OUTCOME,
        ),
    )
