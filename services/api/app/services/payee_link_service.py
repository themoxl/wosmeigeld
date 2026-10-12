"""Verknuepfung von Buchungen mit dem Empfaenger-Stammsatz (`payees`).

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.2. Befund aus einem migrierten
Bestand: `transactions.payee_id` ist dort in saemtlichen Buchungen leer.
Weder der Importpfad (`import_service.commit_single_row`) noch die Handanlage
(`routes/transactions.create_transaction`) hat den Bankempfaenger jemals gegen
`payees` aufgeloest - gefuellt wurde `payee_id` bisher ausschliesslich durch
eine Klassifikationsregel mit `action_payee_id`, und davon hat der Haushalt
keine.

Das ist nicht nur ein leeres Feld. `contract_matching_service.identity_score`
vergibt 0,80, wenn Buchung und Vertrag DIESELBE `payee_id` tragen, aber nur
0,60, wenn allein die Namensnormalform uebereinstimmt. Die
Preisaenderungserkennung verlangt `_PRICE_CHANGE_MIN_IDENTITY = 0,80`
(`classify_hit`) - ohne Verknuepfung kann sie also grundsaetzlich nie greifen,
und jede Betragsaenderung ausserhalb der Toleranz faellt stillschweigend unter
den Tisch, statt als Preisstufe im Verlauf zu landen.

Drei Festlegungen praegen den Aufbau:

**1. Nur verknuepfen, nie anlegen.** Ein fehlender Empfaenger wird NICHT
automatisch erzeugt. `payees` ist ein Stammsatz mit `payee_type`,
`default_category_id`, `is_active` und `unique (household_id, name)` - kein
Protokoll der Bankschreibweisen. Der gemessene Bestand zeigt, warum
automatisches Anlegen hier schadet statt zu helfen: die Bank kuerzt den
Gegenparteitext hart bei 27 Zeichen (ein grosser Teil der beschrifteten
Buchungen ist genau so lang und endet mitten im Namen). Ein Anlegen aus
diesem Text erzeugte einen zweiten Empfaenger NEBEN dem vorhandenen, voll
ausgeschriebenen - also genau die Karteileiche, und schlimmer: zwei
Empfaenger fuer denselben Partner zerstoeren das Identitaetssignal, das dieser
Dienst anheben soll. Fuer die Buchungen ohne jeden Gegenparteitext koennte
ein Anlegen ohnehin nichts ausrichten. Die Luecke bleibt damit teilweise
bestehen - der vorgesehene Weg, sie ohne Heuristik zu schliessen, ist
`payee_aliases` (raw_label -> payee_id, je Haushalt eindeutig): dort steht eine
ausdrueckliche Aussage "dieser Banktext bedeutet diesen Empfaenger" statt einer
Vermutung. Die Tabelle existiert seit 0001, wurde bisher von niemandem gelesen
und wird hier zum ersten Mal ausgewertet.

**2. Exakter Schluessel, keine Aehnlichkeit.** Verknuepft wird nur bei exakter
Gleichheit der Normalform aus `contract_matching_service.payee_key` - bewusst
KEINE Praefix- oder Aehnlichkeitssuche fuer die gekuerzten Banktexte. Grund ist
nicht Bequemlichkeit, sondern Richtungssicherheit: `fetch_candidates` bildet den
Schluessel einer Buchung aus `coalesce(p.name, t.payee_label, t.payee_raw)`.
Stimmt der Schluessel des Empfaengernamens exakt mit dem des Banktexts ueberein,
aendert die Verknuepfung an diesem Schluessel NICHTS - sie kann die Identitaet
also nur von 0,60 auf 0,80 heben. Eine unscharfe Zuordnung wuerde den
Schluessel dagegen durch einen ANDEREN ersetzen und einen Vertrag ohne eigene
`payee_id` von 0,60 auf 0,00 druecken, also Treffer vernichten statt welche zu
gewinnen. Ein Aliastreffer kann den Schluessel ebenfalls veraendern; das ist der
bewusst in Kauf genommene Preis einer ausdruecklichen Nutzeraussage.

**3. Eine gesetzte `payee_id` gewinnt immer.** Kein Schreibpfad hier
ueberschreibt eine vorhandene Zuordnung: der Import nimmt zuerst
`action_payee_id` der Regel, der Nachtragslauf filtert auf `payee_id is null`.
Eine Zuordnung des Nutzers schlaegt die Automatik (Plan 19.6, Leitsatz 5).

Wie `contract_matching_service` laeuft das Modul in zwei Umgebungen: im
API-Prozess als `authenticated` mit RLS und im Nachtragsskript als `postgres`
ohne RLS. Deshalb steht `household_id` in jeder Abfrage und in jeder
WHERE-Klausel ausdruecklich. Committet wird nichts - das erledigt der Aufrufer.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.contract_matching_service import payee_key

# Wie viele Buchungs-IDs hoechstens in EIN update ... = any(...) gehen. Ein
# Haushalt mit zehntausenden Buchungen desselben Empfaengers soll keinen
# Parameter im Megabytebereich erzeugen.
_UPDATE_BATCH_SIZE: Final = 500


# Warum eine Buchung verknuepft wurde bzw. warum nicht. Die Werte sind stabile
# Kennungen fuer Skriptausgabe und Tests; der deutsche Klartext steht in
# `REASON_TEXTS` und ist reine Darstellung.
Reason = Literal[
    "linked_alias",
    "linked_name",
    "already_linked",
    "no_label",
    "unknown_payee",
    "ambiguous_alias",
    "ambiguous_name",
]

REASON_TEXTS: Final[dict[Reason, str]] = {
    "linked_alias": "ueber einen hinterlegten Alias",
    "linked_name": "ueber den Empfaengernamen",
    "already_linked": "bereits verknuepft",
    "no_label": "kein Empfaengertext in der Buchung",
    "unknown_payee": "kein Empfaenger mit diesem Namen im Haushalt",
    "ambiguous_alias": "mehrere Aliase mit derselben Normalform",
    "ambiguous_name": "mehrere Empfaenger mit derselben Normalform",
}

# Teilmenge von `Reason`, bei der ein Schluessel mehrere Empfaenger trifft.
_AMBIGUOUS_REASONS: Final[frozenset[str]] = frozenset({"ambiguous_alias", "ambiguous_name"})


# ---------------------------------------------------------------------------
# Reine Zuordnungslogik (kein DB-Zugriff)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayeeIndex:
    """Die Empfaenger EINES Haushalts, nach Vergleichsschluessel gebuendelt.

    Bewusst kein Pydantic-Modell und bewusst haushaltsgebunden: `household_id`
    ist Teil des Index, damit ein versehentlich weitergereichter Index eines
    anderen Haushalts auffaellt, statt still eine haushaltsfremde `payee_id` zu
    liefern.
    """

    household_id: UUID
    # payee_key(payees.name) -> Empfaenger. Mehr als einer ist moeglich, weil
    # `unique (household_id, name)` die Rohschreibweise sichert, nicht die
    # Normalform ("A&B GmbH" und "A B GmbH" ergeben denselben Schluessel).
    by_name_key: Mapping[str, tuple[UUID, ...]]
    # payee_key(payee_aliases.raw_label) -> Empfaenger, gleiche Ueberlegung.
    by_alias_key: Mapping[str, tuple[UUID, ...]]
    names: Mapping[UUID, str]

    def name_of(self, payee_id: UUID) -> str:
        return self.names.get(payee_id, str(payee_id))


@dataclass(frozen=True)
class PayeeResolution:
    """Ergebnis der Zuordnung GENAU EINES Empfaengertexts."""

    payee_id: UUID | None
    reason: Reason
    key: str
    # Bei Mehrdeutigkeit die beteiligten Empfaenger, damit der Nachtragslauf
    # einen Grund nennen kann, mit dem der Nutzer etwas anfangen kann.
    candidates: tuple[UUID, ...] = ()

    @property
    def is_linked(self) -> bool:
        return self.payee_id is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.reason in _AMBIGUOUS_REASONS


def build_payee_index(
    household_id: UUID,
    payees: Iterable[tuple[UUID, str]],
    aliases: Iterable[tuple[str, UUID]] = (),
) -> PayeeIndex:
    """Baut den Index aus fertigen Datensaetzen - ohne laufendes Supabase
    pruefbar, genau wie der Kern des Vertragsabgleichs."""
    by_name: dict[str, list[UUID]] = defaultdict(list)
    names: dict[UUID, str] = {}
    for payee_id, name in payees:
        names[payee_id] = name
        key = payee_key(name)
        if not key:
            # `payees.name` ist per CHECK nie leer; nach der Normalisierung
            # koennte ein reiner Sonderzeichenname es trotzdem sein ("---").
            # Ein leerer Schluessel wuerde jede Buchung ohne Empfaengertext
            # treffen und ist deshalb kein Eintrag.
            continue
        if payee_id not in by_name[key]:
            by_name[key].append(payee_id)

    by_alias: dict[str, list[UUID]] = defaultdict(list)
    for raw_label, payee_id in aliases:
        key = payee_key(raw_label)
        if not key:
            continue
        if payee_id not in by_alias[key]:
            by_alias[key].append(payee_id)

    return PayeeIndex(
        household_id=household_id,
        by_name_key={key: tuple(value) for key, value in by_name.items()},
        by_alias_key={key: tuple(value) for key, value in by_alias.items()},
        names=names,
    )


def resolve_payee(index: PayeeIndex, label: str | None) -> PayeeResolution:
    """Normalisierter Empfaengertext -> Empfaenger. Reine Funktion.

    Reihenfolge: Alias vor Name. Ein Alias ist eine ausdrueckliche Aussage
    ("dieser Banktext bedeutet diesen Empfaenger"), ein Namenstreffer nur eine
    Uebereinstimmung der Schreibweise - und eine ausdrueckliche Zuordnung
    schlaegt die Automatik (Plan 19.6, Leitsatz 5).

    Trifft ein Schluessel mehrere Empfaenger, wird NICHT verknuepft. Ein
    geratener Empfaenger waere hier schlimmer als gar keiner: er wuerde den
    Vergleichsschluessel der Buchung dauerhaft auf den falschen Namen umlenken
    (siehe Festlegung 2 im Modul-Docstring).
    """
    key = payee_key(label)
    if not key:
        return PayeeResolution(payee_id=None, reason="no_label", key="")

    alias_hits = index.by_alias_key.get(key, ())
    if len(alias_hits) == 1:
        return PayeeResolution(alias_hits[0], "linked_alias", key, alias_hits)
    if len(alias_hits) > 1:
        return PayeeResolution(None, "ambiguous_alias", key, alias_hits)

    name_hits = index.by_name_key.get(key, ())
    if len(name_hits) == 1:
        return PayeeResolution(name_hits[0], "linked_name", key, name_hits)
    if len(name_hits) > 1:
        return PayeeResolution(None, "ambiguous_name", key, name_hits)

    return PayeeResolution(payee_id=None, reason="unknown_payee", key=key)


# ---------------------------------------------------------------------------
# Lesen aus der Datenbank
# ---------------------------------------------------------------------------

# Auch inaktive Empfaenger gehoeren in den Index: `is_active = false` heisst
# "nicht mehr zur Auswahl anbieten", nicht "diese Zahlung war nie an ihn". Eine
# Altbuchung mit exakt diesem Namen gehoert weiterhin zu ihm - und der Vertrag,
# der auf ihn zeigt, braucht die Identitaet genauso.
_PAYEES_SQL: Final = (
    "select id, name from payees where household_id = :household_id order by name, id"
)

_ALIASES_SQL: Final = (
    "select raw_label, payee_id from payee_aliases "
    "where household_id = :household_id order by raw_label, payee_id"
)


async def load_payee_index(session: AsyncSession, household_id: UUID) -> PayeeIndex:
    """Alle Empfaenger und Aliase eines Haushalts als Index.

    Feste Sortierung, damit die Kandidatenliste einer Mehrdeutigkeit zwischen
    zwei Laeufen gleich aussieht - sonst waere die Begruendung im Bericht nicht
    reproduzierbar.
    """
    params = {"household_id": str(household_id)}
    payee_rows = (await session.execute(text(_PAYEES_SQL), params)).all()
    alias_rows = (await session.execute(text(_ALIASES_SQL), params)).all()
    return build_payee_index(
        household_id,
        [(UUID(str(row.id)), str(row.name)) for row in payee_rows],
        [(str(row.raw_label), UUID(str(row.payee_id))) for row in alias_rows],
    )


async def resolve_payee_id(
    session: AsyncSession,
    household_id: UUID,
    label: str | None,
    *,
    index: PayeeIndex | None = None,
) -> UUID | None:
    """Empfaenger-ID zu einem Bankempfaengertext, oder `None`.

    `index` erspart dem Stapelimport das Nachladen je Zeile; ohne ihn (oder mit
    dem Index eines anderen Haushalts) wird er fuer diesen einen Aufruf geholt.
    Ein Text, der schon vor der Abfrage leer normalisiert, spart sie ganz - in
    migrierten Bestaenden betrifft das einen erheblichen Teil der Buchungen.
    """
    if not payee_key(label):
        return None
    usable = index if index is not None and index.household_id == household_id else None
    if usable is None:
        usable = await load_payee_index(session, household_id)
    return resolve_payee(usable, label).payee_id


# ---------------------------------------------------------------------------
# Nachtraegliche Verknuepfung des Bestands
# ---------------------------------------------------------------------------

# Dieselbe Reihenfolge wie `contract_matching_service._CANDIDATE_COLUMNS`:
# zuerst die Anzeigeform der Bank, dann ihr Rohtext. `p.name` fehlt hier
# bewusst - fuer eine noch unverknuepfte Buchung gibt es keinen.
_SCAN_SQL: Final = """
select t.id,
       t.payee_id,
       coalesce(nullif(btrim(t.payee_label), ''), nullif(btrim(t.payee_raw), '')) as payee_label
from transactions t
where t.household_id = :household_id
order by t.id
"""

# Der Filter `payee_id is null` ist die gesamte Idempotenz und zugleich der
# Schutz der manuellen Zuordnung: ein zweiter Lauf trifft keine Zeile mehr, und
# eine vom Nutzer gesetzte Verknuepfung wird nie ueberschrieben. Die
# exists-Pruefung ist ein zweites Netz gegen Haushaltsvermischung -
# `transactions.payee_id` traegt zwar einen Fremdschluessel auf `payees`, der
# aber den Haushalt nicht mitprueft.
_LINK_SQL: Final = """
update transactions set payee_id = cast(:payee_id as uuid)
where household_id = :household_id
  and payee_id is null
  and id = any(cast(:transaction_ids as uuid[]))
  and exists (
    select 1 from payees p
    where p.id = cast(:payee_id as uuid) and p.household_id = :household_id
  )
"""

_HOUSEHOLDS_SQL: Final = """
select distinct t.household_id from transactions t
where t.payee_id is null
  and exists (select 1 from payees p where p.household_id = t.household_id)
order by t.household_id
"""


@dataclass(frozen=True)
class LinkGroup:
    """Alle Buchungen, die an denselben Empfaenger gehen wuerden."""

    payee_id: UUID
    payee_name: str
    reason: Reason
    transaction_ids: tuple[UUID, ...]

    @property
    def transactions(self) -> int:
        return len(self.transaction_ids)


@dataclass(frozen=True)
class UnchangedGroup:
    """Ein Empfaengertext, der zu keiner Verknuepfung fuehrt, mit Grund."""

    reason: Reason
    label: str
    transactions: int
    candidates: tuple[UUID, ...] = ()

    def describe(self) -> str:
        text_ = REASON_TEXTS[self.reason]
        if self.label:
            return f"{text_}: {self.label!r}"
        return text_


@dataclass
class PayeeLinkPlan:
    """Ergebnis eines Laufs ueber EINEN Haushalt.

    `ambiguous` ist eine Teilmenge von `unchanged`; die uebrigen unveraenderten
    Buchungen haben entweder keinen Empfaengertext oder keinen passenden
    Empfaenger. Es gilt immer:
    `transactions_scanned = already_linked + linkable + unchanged`.
    """

    household_id: UUID
    transactions_scanned: int = 0
    already_linked: int = 0
    linkable: int = 0
    ambiguous: int = 0
    unchanged: int = 0
    # Nur bei `apply_household` gesetzt: tatsaechlich geschriebene Zeilen. Kann
    # kleiner als `linkable` sein, wenn zwischen Analyse und Schreiben jemand
    # anderes eine `payee_id` gesetzt hat - dann gewinnt dessen Zuordnung.
    linked: int = 0
    link_groups: list[LinkGroup] = field(default_factory=list)
    unchanged_groups: list[UnchangedGroup] = field(default_factory=list)


def _plan_from_rows(
    household_id: UUID, index: PayeeIndex, rows: Sequence[Any]
) -> PayeeLinkPlan:
    """Baut den Schreibplan aus fertigen Zeilen - der rechnende Teil von
    `analyze_household`, ohne DB und damit einzeln pruefbar."""
    plan = PayeeLinkPlan(household_id=household_id, transactions_scanned=len(rows))

    by_payee: dict[tuple[UUID, Reason], list[UUID]] = defaultdict(list)
    unchanged: dict[tuple[Reason, str], list[UUID]] = defaultdict(list)
    unchanged_candidates: dict[tuple[Reason, str], tuple[UUID, ...]] = {}

    for row in rows:
        if row.payee_id is not None:
            plan.already_linked += 1
            continue
        label = row.payee_label
        resolution = resolve_payee(index, label)
        transaction_id = UUID(str(row.id))
        if resolution.payee_id is not None:
            by_payee[(resolution.payee_id, resolution.reason)].append(transaction_id)
            continue
        bucket = (resolution.reason, (label or "") if resolution.reason != "no_label" else "")
        unchanged[bucket].append(transaction_id)
        if resolution.candidates:
            unchanged_candidates[bucket] = resolution.candidates
        if resolution.is_ambiguous:
            plan.ambiguous += 1

    plan.link_groups = sorted(
        (
            LinkGroup(
                payee_id=payee_id,
                payee_name=index.name_of(payee_id),
                reason=reason,
                transaction_ids=tuple(transaction_ids),
            )
            for (payee_id, reason), transaction_ids in by_payee.items()
        ),
        key=lambda group: (-group.transactions, group.payee_name, str(group.payee_id)),
    )
    plan.unchanged_groups = sorted(
        (
            UnchangedGroup(
                reason=reason,
                label=label,
                transactions=len(transaction_ids),
                candidates=unchanged_candidates.get((reason, label), ()),
            )
            for (reason, label), transaction_ids in unchanged.items()
        ),
        key=lambda group: (-group.transactions, group.reason, group.label),
    )
    plan.linkable = sum(group.transactions for group in plan.link_groups)
    plan.unchanged = sum(group.transactions for group in plan.unchanged_groups)
    return plan


async def analyze_household(session: AsyncSession, household_id: UUID) -> PayeeLinkPlan:
    """Reiner Lesevorgang fuer den Dry-Run. Schreibt nichts."""
    index = await load_payee_index(session, household_id)
    rows = (
        await session.execute(text(_SCAN_SQL), {"household_id": str(household_id)})
    ).all()
    return _plan_from_rows(household_id, index, rows)


def _batched(items: Sequence[UUID], size: int) -> Iterable[Sequence[UUID]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def apply_household(
    session: AsyncSession, household_id: UUID, *, batch_size: int = _UPDATE_BATCH_SIZE
) -> PayeeLinkPlan:
    """Schreibt die Verknuepfungen aus `analyze_household`.

    Transaktional ueber den gesamten Haushalt - Commit und Rollback klammert der
    Aufrufer. Idempotent: ein zweiter Lauf findet dieselben Buchungen bereits
    verknuepft vor (`already_linked`) und schreibt nichts mehr.

    Hinweis fuer den Betrieb: `transactions_bump_version` zaehlt bei jeder
    fachlichen Aenderung die Version hoch - ein Nachtragslauf entwertet also das
    If-Match offener Clients fuer die betroffenen Buchungen. Das ist gewollt:
    die Zuordnung IST eine fachliche Aenderung. Der Trigger wird hier bewusst
    nicht abgeschaltet (anders als bei der reinen SQL-Rueckbefuellung in
    0001_schema.sql), weil das Skript neben laufenden Schreibzugriffen laeuft.
    """
    plan = await analyze_household(session, household_id)
    for group in plan.link_groups:
        for chunk in _batched(group.transaction_ids, batch_size):
            result = cast(
                CursorResult[Any],
                await session.execute(
                    text(_LINK_SQL),
                    {
                        "household_id": str(household_id),
                        "payee_id": str(group.payee_id),
                        "transaction_ids": [str(item) for item in chunk],
                    },
                ),
            )
            plan.linked += result.rowcount
    return plan


async def list_linkable_households(session: AsyncSession) -> list[UUID]:
    """Haushalte mit mindestens einer unverknuepften Buchung und mindestens
    einem Empfaenger. Laeuft ohne RLS-Kontext und darf deshalb nicht auf
    `current_household_id()` bauen."""
    result = await session.execute(text(_HOUSEHOLDS_SQL))
    return [UUID(str(row.household_id)) for row in result]
