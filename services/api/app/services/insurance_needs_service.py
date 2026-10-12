"""Bedarfsabgleich und Deckungsluecken (docs/VERSICHERUNGEN-PLAN.md, P6).

Dieser Dienst ist die MASCHINE, nicht der Regelsatz. Die Regeln liegen als
Datenzeilen in `app/data/insurance_rules.py`; hier steht ausschliesslich, wie
sie ausgewertet werden. Fuenf Eigenschaften erklaeren fast jede Entscheidung in
dieser Datei:

1. **Regelbasiert und nachvollziehbar, kein Modell.** Jeder Befund nennt die
   Regel, die Angaben, auf denen sie beruht, die beruecksichtigten Policen und
   die Quelle mit Pruefdatum. Formuliert wird hier nichts, was eine Wertung
   traegt - die einzigen hier erzeugten Saetze sagen, WARUM dieses Ergebnis
   herauskam, und welche Angabe dafuer fehlt.
2. **Kein Score.** Es gibt Anzahlen je Ergebniszustand, aber keine Note, keinen
   Balken und keine Gesamtzahl ueber alle Risikofelder.
3. **"Ich weiss es nicht" ist ein Ergebnis.** Die Bedingungen werden dreiwertig
   ausgewertet (wahr / falsch / unbekannt). Fehlt eine Angabe, entsteht
   `unclear` samt Namen des fehlenden Feldes - niemals eine Luecke. Ein Befund
   ueber einen ungepflegten Bestand ist schaedlicher als kein Befund.
4. **Der Nutzer schlaegt die Regel.** Eine gespeicherte Entscheidung
   (`insurance_rule_decisions`) gewinnt immer; nur die Wiedervorlage
   (`remind_on`) bringt den Befund zurueck.
5. **Doppelversicherung ist eine Katalogaussage.** Sie kommt aus
   `insurance_lines.covered_by_keys` und nicht aus Sonderlogik: "Glasbruch
   steckt schon in Hausrat und Gebaeude" steht im Katalog, nicht hier.

Gespeichert wird nichts von alledem. Befunde werden bei jedem Aufruf frisch
gerechnet (Muster `rules_service.get_review_inbox`); persistiert ist nur, was
der Nutzer entschieden hat.

Die Schnittstelle zum Katalog ist `KNOWN_FACTS` dort und `_household_facts` /
`_person_facts` hier: der Katalog darf ausschliesslich auf Merkmale zeigen, die
diese beiden Funktionen fuellen. Zeigt er auf ein unbekanntes Merkmal, entsteht
`unclear` und ein Protokolleintrag - nie eine Luecke.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final, cast
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.insurance import (
    DoubleInsuranceFinding,
    InsuranceRule,
    NeedsCheck,
    NeedsCompleteness,
    NeedsCompletenessItem,
    NeedsFinding,
    NeedsOutcome,
    NeedsProfile,
    NeedsProfileInput,
    PersonNeedsProfile,
    PersonNeedsProfileInput,
    RuleDecision,
    RuleDecisionRequest,
)
from app.services import contract_service, insurance_coverage_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fehlercodes
# ---------------------------------------------------------------------------

INSURANCE_PERSON_PROFILE_FORBIDDEN: Final = "insurance_person_profile_forbidden"
INSURANCE_RULE_UNKNOWN: Final = "insurance_rule_unknown"

# ---------------------------------------------------------------------------
# Feste Texte
# ---------------------------------------------------------------------------

# P6-Abnahme: "Der Disclaimer steht im API-Body, nicht nur in der Fusszeile."
DISCLAIMER: Final = (
    "Dieser Abgleich ist keine Beratung und kein Angebot. Er prueft Ihre Angaben "
    "gegen einen festen Regelkatalog mit Quelle und Pruefdatum. Er nennt keine "
    "Anbieter, keine Tarife und keinen Wechsel, er bewertet Ihre Absicherung "
    "nicht mit einer Note - und er kennt ausschliesslich das, was Sie hier "
    "erfasst haben. Fuer eine Einschaetzung Ihrer konkreten Vertraege sind die "
    "Verbraucherzentrale oder ein Versicherungsberater zustaendig."
)

# Die Rechtsfolge, nicht ein Rat (Plan 10.4).
DOUBLE_INSURANCE_RATIONALE: Final = (
    "Bei einer Doppelversicherung haften zwar beide Versicherer, ersetzt wird "
    "der Schaden aber nur einmal (Paragraph 78 VVG); wer sie nachweist, kann "
    "die Aufhebung des spaeter geschlossenen Vertrages verlangen (Paragraph 79 "
    "VVG). Welche der beiden Policen die passendere ist, sagt dieses Werkzeug "
    "nicht."
)

# ---------------------------------------------------------------------------
# Merkmale: Klartextnamen und Herkunft
# ---------------------------------------------------------------------------
# Die Namen stehen hier und nicht in der Oberflaeche, weil die Rueckfrage "dazu
# fehlt eine Angabe" den Feldnamen nennt und ein roher Spaltenname dort keine
# Frage ist. Kommt im Regelkatalog ein Merkmal dazu, faellt an genau einer
# Stelle auf, dass die Uebersetzung fehlt.

FACT_LABELS: Final[dict[str, str]] = {
    # Haushalt, beantwortbar
    "housing_form": "Wohnform",
    "living_area_sqm": "Wohnflaeche",
    "elemental_zone": "Hochwasserzone",
    "has_oil_tank": "Oeltank",
    "construction_active": "laufendes Bauvorhaben",
    "has_photovoltaic": "Photovoltaikanlage",
    "liquid_reserve_minor": "verfuegbare Ruecklage",
    "contents_value_minor": "Wert des Hausrats",
    "has_open_loans": "offene Darlehen",
    "children_count": "Kinder im Haushalt",
    "pets": "Tiere im Haushalt",
    "travel_frequency": "Reisen ins Ausland",
    "vehicle_count": "Fahrzeuge",
    "side_business": "Nebenerwerb",
    "volunteering": "Ehrenamt",
    "has_drone": "Drohne",
    "has_hunting_license": "Jagdschein",
    "has_boat": "Boot",
    # Person, beantwortbar
    "employment_status": "Erwerbsstatus",
    "net_income_minor": "Nettoeinkommen",
    "health_coverage": "Krankenversicherung",
    "beihilfe_rate_bp": "Beihilfebemessungssatz",
    "has_dependents": "unterhaltsberechtigte Angehoerige",
    "education_until": "Erstausbildung bis",
    "moved_out_on": "ausgezogen am",
    "died_on": "verstorben am",
    # Abgeleitet - keine Frage, sondern ein Blick in Bestand oder Angaben
    "open_debt_minor": "offene Darlehen aus dem Vermoegensmodul",
    "vehicle_max_age_years": "Alter des aeltesten erfassten Fahrzeugs",
    "youngest_person_age_years": "Alter der juengsten erfassten Person",
    "person_age_years": "Alter",
    "education_ended": "Erstausbildung beendet",
    "moved_out": "eigener Hausstand",
    "is_deceased": "verstorben",
}

# Welche BEANTWORTBARE Frage hinter einem abgeleiteten Merkmal steht. Ohne diese
# Zuordnung stuende in der Onboarding-Fuehrung "Erstausbildung beendet fehlt" -
# ein Satz, auf den niemand antworten kann.
FACT_SOURCES: Final[dict[str, tuple[str, ...]]] = {
    "education_ended": ("education_until",),
    "moved_out": ("moved_out_on",),
    "is_deceased": ("died_on",),
}

# Die Fragen des Haushaltsbogens - Grundlage von "sieben von zwoelf Angaben".
HOUSEHOLD_QUESTIONS: Final[tuple[str, ...]] = (
    "housing_form",
    "living_area_sqm",
    "elemental_zone",
    "has_oil_tank",
    "construction_active",
    "has_photovoltaic",
    "liquid_reserve_minor",
    "contents_value_minor",
    "has_open_loans",
    "children_count",
    "pets",
    "travel_frequency",
    "vehicle_count",
    "side_business",
    "volunteering",
    "has_drone",
    "has_hunting_license",
    "has_boat",
)

PERSON_QUESTIONS: Final[tuple[str, ...]] = (
    "employment_status",
    "net_income_minor",
    "health_coverage",
)

# Alles, was zu einer PERSON gehoert - fuer die Frage, ob eine fehlende Angabe
# im Bogen des Haushalts oder in dem einer Person steht.
PERSON_FACT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "employment_status",
        "net_income_minor",
        "health_coverage",
        "beihilfe_rate_bp",
        "has_dependents",
        "education_until",
        "moved_out_on",
        "died_on",
        "person_age_years",
        "education_ended",
        "moved_out",
        "is_deceased",
    }
)

# Welcher Policenstatus als vorhandener Schutz zaehlt, wenn eine Regel nichts
# anderes sagt. Nur 'active': eine ruhend gestellte Police erfuellt die
# Versicherungspflicht nicht, und eine beitragsfrei gestellte zaehlt nur dort,
# wo der Katalog es ausdruecklich sagt (`special = 'policy_counts'`).
COUNTING_STATUS: Final[frozenset[str]] = frozenset({"active"})

# Wiederbeschaffungswert je Quadratmeter in Minor Units (650 Euro, Plan 10.1).
CONTENTS_VALUE_PER_SQM_MINOR: Final = 65000

_OUTCOME_RANK: Final[dict[str, int]] = {
    "gap": 0,
    "underinsured": 1,
    "unclear": 2,
    "overinsured": 3,
    "fulfilled": 4,
    "dismissed": 5,
    "not_applicable": 6,
}
_TIER_RANK: Final[dict[str, int]] = {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4}


# ---------------------------------------------------------------------------
# Regelkatalog laden
# ---------------------------------------------------------------------------
# Der Katalog ist ein Datenpaket eines anderen Moduls. Er kann fehlen (dann gibt
# es keine Befunde) und er kann fehlerhafte Zeilen enthalten (dann fallen genau
# diese aus). Beides ist die sichere Richtung: eine fehlende Regel meldet keine
# Luecke, eine kaputte Regel darf keine erfinden - und keiner der beiden Faelle
# darf den Rest der Seite mitnehmen.


@dataclass(frozen=True)
class RuleCatalog:
    """Der geladene Regelsatz samt der Zeilen, die dabei durchgefallen sind."""

    rules: tuple[InsuranceRule, ...] = ()
    problems: tuple[str, ...] = ()


_CATALOG: RuleCatalog | None = None
_RULES_MODULE: Final = "app.data.insurance_rules"
_RULES_ATTRIBUTES: Final = ("INSURANCE_RULES", "RULES", "RULE_CATALOG")


def rule_catalog(*, refresh: bool = False) -> RuleCatalog:
    """Laedt `app/data/insurance_rules.py` einmal und merkt sich das Ergebnis.

    `refresh` ist fuer den Seed-Test da: er prueft den Katalog gegen den
    Auswerter, ohne von der Reihenfolge der Testfaelle abzuhaengen.
    """
    global _CATALOG
    if _CATALOG is not None and not refresh:
        return _CATALOG
    _CATALOG = _load_catalog()
    return _CATALOG


def _load_catalog() -> RuleCatalog:
    try:
        module = importlib.import_module(_RULES_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _RULES_MODULE:
            # Der Regelsatz existiert, importiert aber etwas Fehlendes. Das ist
            # ein echter Fehler und wird nicht als "kein Katalog" verkleidet.
            raise
        logger.warning(
            "Regelkatalog %s fehlt - der Bedarfsabgleich meldet keine Befunde.", _RULES_MODULE
        )
        return RuleCatalog(problems=("insurance_rules_missing",))

    raw: Any = None
    for attribute in _RULES_ATTRIBUTES:
        raw = getattr(module, attribute, None)
        if raw is not None:
            break
    if not isinstance(raw, list | tuple):
        logger.warning("Regelkatalog %s liefert keine Regelliste.", _RULES_MODULE)
        return RuleCatalog(problems=("insurance_rules_empty",))

    rules: list[InsuranceRule] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        try:
            rule = (
                entry
                if isinstance(entry, InsuranceRule)
                else InsuranceRule.model_validate(entry, from_attributes=True)
            )
        except ValidationError as exc:
            key = getattr(entry, "rule_key", index)
            problems.append(f"{key}: {exc.errors()[0].get('msg', 'ungueltig')}")
            continue
        if rule.rule_key in seen:
            problems.append(f"{rule.rule_key}: doppelter rule_key")
            continue
        seen.add(rule.rule_key)
        rules.append(rule)
    if problems:
        logger.warning("Regelkatalog mit %d unbrauchbaren Zeilen: %s", len(problems), problems)
    return RuleCatalog(rules=tuple(rules), problems=tuple(problems))


# ---------------------------------------------------------------------------
# Der Bestand, wie die Maschine ihn sieht
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageFacts:
    coverage_key: str
    is_included: bool
    sum_insured_minor: int | None
    benefit_rate_bp: int | None


@dataclass(frozen=True)
class PolicyFacts:
    """Eine Police, reduziert auf das, was eine Regel von ihr wissen darf."""

    id: UUID
    line_key: str
    line_label: str
    label: str
    status: str
    starts_on: date | None
    ends_on: date | None
    sum_insured_minor: int | None
    benefit_rate_bp: int | None
    covered_by_keys: tuple[str, ...]
    parent_policy_id: UUID | None
    predecessor_policy_id: UUID | None
    line_deferred: bool
    yearly_minor: int | None
    coverages: Mapping[str, CoverageFacts]
    person_ids: frozenset[UUID]
    object_ids: frozenset[UUID]

    @property
    def has_coverages(self) -> bool:
        return bool(self.coverages)

    def covers(self, coverage_key: str) -> bool:
        coverage = self.coverages.get(coverage_key)
        return coverage is not None and coverage.is_included

    def in_force(self, today: date, statuses: frozenset[str] = COUNTING_STATUS) -> bool:
        """Status UND Laufzeit - eine abgelaufene Police ist kein Schutz, auch
        wenn niemand ihren Status nachgezogen hat."""
        if self.status not in statuses:
            return False
        if self.ends_on is not None and self.ends_on < today:
            return False
        return not (self.starts_on is not None and self.starts_on > today)


@dataclass(frozen=True)
class PersonFacts:
    id: UUID
    display_name: str
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class NeedsContext:
    """Alles, was ein Regeldurchlauf braucht - einmal geladen, nie nachgeladen."""

    today: date
    household: Mapping[str, Any]
    persons: tuple[PersonFacts, ...]
    policies: tuple[PolicyFacts, ...]
    line_labels: Mapping[str, str]
    line_tiers: Mapping[str, str | None]
    coverage_labels: Mapping[str, str]
    decisions: Mapping[tuple[str, UUID | None], RuleDecision]
    profile_exists: bool
    last_checked_on: date | None

    @property
    def stock_empty(self) -> bool:
        """Kein einziger Policeneintrag - dann zeigt der Abgleich "nichts
        erfasst" und nicht dreissig Luecken (Plan 10.2)."""
        return not self.policies


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

_PROFILE_COLUMNS: Final = (
    "housing_form, living_area_sqm, elemental_zone, has_oil_tank, construction_active, "
    "has_photovoltaic, liquid_reserve_minor, contents_value_minor, has_open_loans, "
    "children_count, pets, travel_frequency, vehicle_count, side_business, volunteering, "
    "has_drone, has_hunting_license, has_boat, last_checked_on, version, updated_at"
)

_PERSON_PROFILE_COLUMNS: Final = (
    "pr.employment_status, pr.net_income_minor, pr.health_coverage, pr.beihilfe_rate_bp, "
    "pr.has_dependents, pr.education_until, pr.moved_out_on"
)


async def _load_profile_row(session: AsyncSession, household_id: UUID) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    f"select {_PROFILE_COLUMNS} from insurance_needs_profile "
                    "where household_id = :hid"
                ),
                {"hid": str(household_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


async def _load_person_rows(session: AsyncSession, household_id: UUID) -> list[dict[str, Any]]:
    """Personen samt Bedarfsangaben - nur die, die der Aufrufer sehen darf.

    Die Auswahl laeuft ueber `health_insured_persons`; deren Read-Policy
    verlangt bereits die Gesundheitsfreigabe. Es gibt hier ausdruecklich keine
    zweite Rechtepruefung, die versehentlich lockerer sein koennte.
    """
    rows = (
        (
            await session.execute(
                text(
                    "select p.id as insured_person_id, p.display_name, p.birth_date, "
                    "  p.died_on, health_can_manage_person(p.id) as can_manage, "
                    f"  {_PERSON_PROFILE_COLUMNS}, "
                    "  coalesce(pr.version, 1) as version, pr.updated_at, "
                    "  (pr.insured_person_id is not null) as profile_exists "
                    "from health_insured_persons p "
                    "left join insurance_person_profile pr "
                    "  on pr.insured_person_id = p.id and pr.household_id = p.household_id "
                    "where p.household_id = :hid and p.is_active "
                    "order by lower(p.display_name), p.id"
                ),
                {"hid": str(household_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def _load_policy_rows(session: AsyncSession, household_id: UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "select p.id, p.line_key, p.status, p.starts_on, p.ends_on, p.label, "
                    "  p.insurer_name, p.sum_insured_minor, p.attrs, p.parent_policy_id, "
                    "  p.predecessor_policy_id, p.line_deferred_at, p.premium_share_bp, "
                    "  p.contract_id, p.external_premium_minor, p.external_frequency, "
                    "  c.amount_minor as contract_amount_minor, "
                    "  c.frequency as contract_frequency, "
                    "  l.label as line_label, l.covered_by_keys "
                    "from insurance_policies p "
                    "join insurance_lines l on l.line_key = p.line_key "
                    "left join contracts c on c.id = p.contract_id "
                    "  and c.household_id = p.household_id and c.merged_into_id is null "
                    "where p.household_id = :hid and p.status <> 'entered_in_error' "
                    "order by l.sort_order, p.insurer_name, p.id"
                ),
                {"hid": str(household_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _policy_yearly_minor(row: Mapping[str, Any]) -> int | None:
    """Jahresbeitrag der Police, so gut er bekannt ist.

    Bewusst ohne die Fixkostenpruefung aus `insurance_service._premium`: dort
    geht es um die Frage "steht dieser Betrag heute in den Fixkosten", hier um
    "welcher Beitrag haengt an dieser Doppelung". Ein Fremdbeitrag zaehlt
    deshalb mit, obwohl er keine Fixkosten sind.
    """
    share_bp = int(row.get("premium_share_bp") or 10000)
    amount = row.get("contract_amount_minor")
    frequency = row.get("contract_frequency")
    if amount is not None and frequency is not None:
        return round(contract_service.yearly_minor(int(amount), str(frequency)) * share_bp / 10000)
    external = row.get("external_premium_minor")
    external_frequency = row.get("external_frequency")
    if external is not None and external_frequency is not None:
        return contract_service.yearly_minor(int(external), str(external_frequency))
    return None


def _policy_benefit_rate_bp(
    row: Mapping[str, Any], coverages: Sequence[CoverageFacts]
) -> int | None:
    """Erstattungsquote der Police in Basispunkten.

    Die Police selbst hat keine Spalte dafuer - eine Quote ist spartenspezifisch
    und steht deshalb in `attrs` oder am Baustein. Genommen wird der hoechste
    erfasste Wert: bei mehreren Bausteinen ist das der, den eine Mindestquote
    meint.
    """
    attrs = row.get("attrs")
    if isinstance(attrs, Mapping):
        value = attrs.get("benefit_rate_bp")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    rates = [
        cover.benefit_rate_bp
        for cover in coverages
        if cover.is_included and cover.benefit_rate_bp is not None
    ]
    return max(rates) if rates else None


async def load_context(session: AsyncSession, household_id: UUID, *, today: date) -> NeedsContext:
    """Traegt Angaben, Bestand und Entscheidungen zusammen.

    Genau eine Ladefunktion fuer alle drei Endpunkte des Abgleichs: `check`,
    `completeness` und `duplicates` muessen dasselbe sehen, sonst erklaert die
    eine Antwort etwas, das in der anderen nicht steht.
    """
    profile = await _load_profile_row(session, household_id)
    person_rows = await _load_person_rows(session, household_id)
    policy_rows = await _load_policy_rows(session, household_id)

    coverage_rows = (
        (
            await session.execute(
                text(
                    "select policy_id, coverage_key, is_included, sum_insured_minor, "
                    "  benefit_rate_bp from insurance_coverages where household_id = :hid"
                ),
                {"hid": str(household_id)},
            )
        )
        .mappings()
        .all()
    )
    coverages_by_policy: dict[UUID, dict[str, CoverageFacts]] = {}
    for cover in coverage_rows:
        coverages_by_policy.setdefault(cover["policy_id"], {})[cover["coverage_key"]] = (
            CoverageFacts(
                coverage_key=cover["coverage_key"],
                is_included=bool(cover["is_included"]),
                sum_insured_minor=cover["sum_insured_minor"],
                benefit_rate_bp=cover["benefit_rate_bp"],
            )
        )

    persons_by_policy: dict[UUID, set[UUID]] = {}
    for policy_id, person_id in (
        await session.execute(
            text(
                "select policy_id, insured_person_id from insurance_policy_persons "
                "where household_id = :hid"
            ),
            {"hid": str(household_id)},
        )
    ).all():
        persons_by_policy.setdefault(policy_id, set()).add(person_id)

    objects_by_policy: dict[UUID, set[UUID]] = {}
    for policy_id, object_id in (
        await session.execute(
            text(
                "select policy_id, insured_object_id from insurance_policy_objects "
                "where household_id = :hid"
            ),
            {"hid": str(household_id)},
        )
    ).all():
        objects_by_policy.setdefault(policy_id, set()).add(object_id)

    object_rows = (
        (
            await session.execute(
                text(
                    "select kind, build_year from insured_objects "
                    "where household_id = :hid and disposed_on is null and is_active"
                ),
                {"hid": str(household_id)},
            )
        )
        .mappings()
        .all()
    )

    line_rows = (
        (await session.execute(text("select line_key, label, need_tier from insurance_lines")))
        .mappings()
        .all()
    )
    coverage_key_rows = (
        (await session.execute(text("select coverage_key, label from insurance_coverage_keys")))
        .mappings()
        .all()
    )

    open_debt = (
        await session.execute(
            text(
                "select coalesce(sum(current_value_minor), 0) from assets_and_debts "
                "where household_id = :hid and type = 'debt'"
            ),
            {"hid": str(household_id)},
        )
    ).scalar_one()

    decisions = await _load_decisions(session, household_id)

    policies: list[PolicyFacts] = []
    for row in policy_rows:
        coverages = coverages_by_policy.get(row["id"], {})
        policies.append(
            PolicyFacts(
                id=row["id"],
                line_key=row["line_key"],
                line_label=row["line_label"],
                label=row["label"] or row["insurer_name"],
                status=row["status"],
                starts_on=row["starts_on"],
                ends_on=row["ends_on"],
                sum_insured_minor=row["sum_insured_minor"],
                benefit_rate_bp=_policy_benefit_rate_bp(row, list(coverages.values())),
                covered_by_keys=tuple(row["covered_by_keys"] or ()),
                parent_policy_id=row["parent_policy_id"],
                predecessor_policy_id=row["predecessor_policy_id"],
                line_deferred=row["line_deferred_at"] is not None,
                yearly_minor=_policy_yearly_minor(row),
                coverages=coverages,
                person_ids=frozenset(persons_by_policy.get(row["id"], set())),
                object_ids=frozenset(objects_by_policy.get(row["id"], set())),
            )
        )

    persons = tuple(
        PersonFacts(
            id=row["insured_person_id"],
            display_name=row["display_name"],
            facts=_person_facts(row, today=today),
        )
        for row in person_rows
    )

    household = _household_facts(
        profile,
        objects=[dict(row) for row in object_rows],
        persons=persons,
        open_debt_minor=int(open_debt),
        today=today,
    )

    return NeedsContext(
        today=today,
        household=household,
        persons=persons,
        policies=tuple(policies),
        line_labels={row["line_key"]: row["label"] for row in line_rows},
        line_tiers={row["line_key"]: row["need_tier"] for row in line_rows},
        coverage_labels={row["coverage_key"]: row["label"] for row in coverage_key_rows},
        decisions=decisions,
        profile_exists=profile is not None,
        last_checked_on=profile.get("last_checked_on") if profile else None,
    )


async def _load_decisions(
    session: AsyncSession, household_id: UUID
) -> dict[tuple[str, UUID | None], RuleDecision]:
    rows = (
        (
            await session.execute(
                text(
                    "select id, rule_key, policy_id, decision, reason, reason_note, "
                    "  remind_on, decided_at from insurance_rule_decisions "
                    "where household_id = :hid"
                ),
                {"hid": str(household_id)},
            )
        )
        .mappings()
        .all()
    )
    return {
        (row["rule_key"], row["policy_id"]): RuleDecision.model_validate(dict(row))
        for row in rows
    }


# ---------------------------------------------------------------------------
# Merkmale
# ---------------------------------------------------------------------------
# Die Namen dieser Merkmale sind die Schnittstelle zum Regelkatalog. Sie stimmen
# mit `insurance_rules.KNOWN_FACTS` ueberein; der Seed-Test prueft das.


def _age_years(born: date | None, today: date) -> int | None:
    if born is None:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _person_facts(row: Mapping[str, Any], *, today: date) -> dict[str, Any]:
    died_on: date | None = row.get("died_on")
    education_until: date | None = row.get("education_until")
    moved_out_on: date | None = row.get("moved_out_on")
    return {
        "employment_status": row.get("employment_status"),
        "net_income_minor": row.get("net_income_minor"),
        "health_coverage": row.get("health_coverage"),
        "beihilfe_rate_bp": row.get("beihilfe_rate_bp"),
        "has_dependents": row.get("has_dependents"),
        "education_until": education_until,
        "moved_out_on": moved_out_on,
        "died_on": died_on,
        "person_age_years": _age_years(row.get("birth_date"), today),
        # Abgeleitete Wahrheitswerte, nie unbekannt: "kein Datum erfasst" heisst
        # hier "das Ereignis ist nicht eingetreten". Das ist die sichere
        # Richtung - ein unbekanntes Ende der Erstausbildung soll keine
        # Rueckfrage in jeder Familienpolice erzeugen.
        "education_ended": education_until is not None and education_until <= today,
        "moved_out": moved_out_on is not None and moved_out_on <= today,
        "is_deceased": died_on is not None and died_on <= today,
    }


def _household_facts(
    profile: Mapping[str, Any] | None,
    *,
    objects: Sequence[Mapping[str, Any]],
    persons: Sequence[PersonFacts],
    open_debt_minor: int,
    today: date,
) -> dict[str, Any]:
    """Die Angaben des Haushalts plus die drei, die aus dem Bestand kommen.

    Mehr abgeleitete Merkmale gibt es bewusst nicht: jedes weitere waere eine
    Rechnung, die im Befund nicht mehr als Angabe zitierbar ist. Was der Nutzer
    beantwortet hat, steht in `HOUSEHOLD_QUESTIONS`; was hier dazukommt, steht
    ausdruecklich in `FACT_LABELS` als "abgeleitet".
    """
    facts: dict[str, Any] = {name: None for name in HOUSEHOLD_QUESTIONS}
    if profile is not None:
        for name in HOUSEHOLD_QUESTIONS:
            facts[name] = profile.get(name)

    vehicle_ages = [
        today.year - int(obj["build_year"])
        for obj in objects
        if obj.get("kind") == "vehicle" and obj.get("build_year") is not None
    ]
    person_ages = [
        age
        for age in (person.facts.get("person_age_years") for person in persons)
        if isinstance(age, int)
    ]
    facts.update(
        {
            "open_debt_minor": open_debt_minor,
            "vehicle_max_age_years": max(vehicle_ages) if vehicle_ages else None,
            "youngest_person_age_years": min(person_ages) if person_ages else None,
        }
    )
    return facts


# ---------------------------------------------------------------------------
# Der Rechenkern: dreiwertige Bedingungen
# ---------------------------------------------------------------------------
# `None` heisst UNBEKANNT und ist keine Abkuerzung fuer "falsch". Der
# Unterschied ist der ganze Sinn des Abschnitts: aus "falsch" wird
# `not_applicable`, aus "unbekannt" wird `unclear`. Wer beides gleich behandelt,
# baut aus jeder unbeantworteten Frage eine erfundene Luecke.


@dataclass
class _Trace:
    """Was die Auswertung unterwegs gesehen hat - die Herkunft des Befundes."""

    facts: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def seen(self, name: str, value: Any) -> None:
        self.facts[name] = value

    def miss(self, name: str) -> None:
        if name not in self.missing:
            self.missing.append(name)


def _compare(node: Mapping[str, Any], value: Any) -> bool | None:
    if "is_null" in node:
        return (value is None) == bool(node["is_null"])
    if value is None:
        return None
    if "in" in node:
        candidates = list(node["in"])
        if isinstance(value, list | tuple | set | frozenset):
            # Ein mengenwertiges Merkmal trifft, sobald EIN Element passt. Die
            # leere Menge ist dabei eine Antwort und kein Unbekannt.
            return any(item in candidates for item in value)
        return value in candidates
    if "eq" in node:
        return bool(value == node["eq"])
    for operator in ("gt", "lt"):
        if operator in node:
            other = node[operator]
            if isinstance(value, date) and isinstance(other, str):
                other = date.fromisoformat(other)
            if isinstance(value, bool) or isinstance(other, bool):
                return None
            try:
                return bool(value > other) if operator == "gt" else bool(value < other)
            except TypeError:
                return None
    return None


def evaluate_condition(
    node: Mapping[str, Any], facts: Mapping[str, Any], trace: _Trace
) -> bool | None:
    """Wertet eine Bedingung dreiwertig aus (wahr / falsch / unbekannt)."""
    if not node:
        return True
    if "all" in node:
        results = [evaluate_condition(branch, facts, trace) for branch in node["all"]]
        if any(result is False for result in results):
            return False
        return None if any(result is None for result in results) else True
    if "any" in node:
        results = [evaluate_condition(branch, facts, trace) for branch in node["any"]]
        if any(result is True for result in results):
            return True
        return None if any(result is None for result in results) else False

    name = str(node["fact"])
    if name not in facts:
        # Der Katalog zeigt auf ein Merkmal, das die Maschine nicht kennt. Das
        # darf keine Luecke erzeugen, sondern eine Rueckfrage - und es faellt im
        # Protokoll auf.
        logger.warning("Regelkatalog nennt unbekanntes Merkmal %r", name)
        trace.miss(name)
        return None
    value = facts[name]
    trace.seen(name, value)
    result = _compare(node, value)
    if result is None:
        trace.miss(name)
    return result


# ---------------------------------------------------------------------------
# Der Rechenkern: Zielseite
# ---------------------------------------------------------------------------


def _resolve_formula(name: str, facts: Mapping[str, Any], trace: _Trace) -> int | None:
    """Die geschlossene Liste benannter Funktionen aus Plan 10.1."""
    if name == "contents_value_per_sqm":
        area = facts.get("living_area_sqm")
        if area is None:
            trace.miss("living_area_sqm")
            return None
        trace.seen("living_area_sqm", area)
        return int(area) * CONTENTS_VALUE_PER_SQM_MINOR
    if name == "open_debt":
        debt = facts.get("open_debt_minor")
        trace.seen("open_debt_minor", debt)
        return int(debt) if debt is not None else None
    if name == "beihilfe_complement":
        rate = facts.get("beihilfe_rate_bp")
        if rate is None:
            trace.miss("beihilfe_rate_bp")
            return None
        trace.seen("beihilfe_rate_bp", rate)
        return 10000 - int(rate)
    if name == "net_income_annual_x5":
        income = facts.get("net_income_minor")
        if income is None:
            trace.miss("net_income_minor")
            return None
        trace.seen("net_income_minor", income)
        return int(income) * 12 * 5
    logger.warning("Regelkatalog nennt unbekannte Formel %r", name)
    return None


@dataclass
class _Outcome:
    """Das Ergebnis einer Zielpruefung, vor der Nutzerentscheidung."""

    outcome: NeedsOutcome
    reason: str
    matched: list[UUID] = field(default_factory=list)
    considered: list[UUID] = field(default_factory=list)
    target_value_minor: int | None = None
    actual_value_minor: int | None = None
    gap_value_minor: int | None = None
    line_key: str | None = None


def _counting_statuses(rule: InsuranceRule) -> frozenset[str]:
    """Welche Status fuer DIESE Regel als vorhandener Schutz zaehlen.

    Nur die Sonderart "Police zaehlt als vorhanden" erweitert die Menge - und
    zwar genau um die Status, die der Katalog nennt.
    """
    if rule.special == "policy_counts" and rule.policy_status_filter:
        return COUNTING_STATUS | frozenset(rule.policy_status_filter)
    return COUNTING_STATUS


def _visible_policies(
    rule: InsuranceRule, ctx: NeedsContext, person: PersonFacts | None
) -> list[PolicyFacts]:
    """Die Policen, die diese Regel ueberhaupt ansehen darf.

    Bei einer personenbezogenen Regel zaehlt eine Police fuer die Person, wenn
    sie ihr zugeordnet ist ODER ueberhaupt keine Zuordnung traegt. Die zweite
    Haelfte ist Absicht: eine nicht zugeordnete Police soll keine Luecke je
    Person erfinden, nur weil die Beteiligten noch nicht erfasst sind.
    """
    statuses = _counting_statuses(rule)
    found = [p for p in ctx.policies if p.in_force(ctx.today, statuses)]
    if person is not None:
        found = [p for p in found if not p.person_ids or person.id in p.person_ids]
    return found


def _soften_gap(outcome: _Outcome, ctx: NeedsContext) -> _Outcome:
    """Aus "Luecke" wird "ich weiss es nicht", wo der Bestand nichts hergibt.

    Zwei Faelle, beide aus Plan 10.2: ein Haushalt ohne jede erfasste Police
    zeigt "nichts erfasst" statt dreissig Luecken, und eine Police mit noch
    nicht bestaetigter Sparte koennte genau diese Sparte sein.
    """
    if outcome.outcome != "gap":
        return outcome
    if ctx.stock_empty:
        return _Outcome(
            outcome="unclear",
            reason=(
                "Es ist noch keine einzige Police erfasst. Solange der Bestand leer ist, "
                "sagt der Abgleich nicht, dass hier Schutz fehlt."
            ),
            considered=outcome.considered,
            line_key=outcome.line_key,
        )
    unassigned = [
        p.id
        for p in ctx.policies
        if p.in_force(ctx.today) and (p.line_key == "unknown" or p.line_deferred)
    ]
    if unassigned:
        return _Outcome(
            outcome="unclear",
            reason=(
                "Es gibt Policen ohne bestaetigte Sparte. Eine davon koennte genau diese "
                "Sparte sein - bestaetigen Sie die Sparte, dann prueft die Regel weiter."
            ),
            matched=unassigned,
            considered=outcome.considered,
            line_key=outcome.line_key,
        )
    return outcome


def _amount_outcome(
    rule: InsuranceRule,
    matches: list[PolicyFacts],
    *,
    target: int | None,
    rate: bool,
    trace: _Trace,
) -> _Outcome:
    """Deckungssumme oder Quote gegen eine Schwelle - der Kern von R2a und R15a."""
    considered = [p.id for p in matches]
    if not matches:
        # Auch ohne Police gehoert der Zielwert in den Befund, sofern er sich
        # rechnen laesst: Plan 13.3 nennt fuer R7 ausdruecklich "Zielwert
        # open_debt = 180.000 Euro". Ohne ihn bliebe "keine Police erfasst"
        # ohne die Zahl, gegen die geprueft wurde - und damit ohne Grundlage.
        return _Outcome(
            outcome="gap",
            reason="Keine laufende Police dieser Sparte erfasst.",
            considered=considered,
            target_value_minor=target if (not rate and target is not None and target > 0) else None,
        )
    if target is None:
        fehlend = ", ".join(FACT_LABELS.get(name, name) for name in trace.missing)
        return _Outcome(
            outcome="unclear",
            reason=(
                "Der Vergleichswert dieser Regel laesst sich aus Ihren Angaben nicht bilden"
                + (f": {fehlend}." if fehlend else ".")
            ),
            matched=[p.id for p in matches],
            considered=considered,
            line_key=matches[0].line_key,
        )

    def _value(policy: PolicyFacts) -> int | None:
        return policy.benefit_rate_bp if rate else policy.sum_insured_minor

    known = [(p, _value(p)) for p in matches if _value(p) is not None]
    if not known:
        fehlt = "Erstattungsquote" if rate else "Deckungssumme"
        return _Outcome(
            outcome="unclear",
            reason=(
                f"Die {fehlt} ist an keiner der gefundenen Policen erfasst. Ohne sie "
                "prueft die Regel nichts."
            ),
            matched=[p.id for p in matches],
            considered=considered,
            line_key=matches[0].line_key,
        )
    best_policy, best_raw = max(known, key=lambda item: item[1] or 0)
    best_value = cast(int, best_raw)
    if target <= 0:
        # Eine Schwelle von null ist keine Schwelle. Dann prueft die Regel nur
        # noch, dass es die Police gibt - statt "es fehlen 0 Euro" zu melden.
        return _Outcome(
            outcome="fulfilled",
            reason="Police vorhanden; aus Ihren Angaben ergibt sich kein Vergleichswert.",
            matched=[best_policy.id],
            considered=considered,
            line_key=best_policy.line_key,
        )
    money = not rate
    if best_value >= target:
        return _Outcome(
            outcome="fulfilled",
            reason=(
                f"Police vorhanden, erfasst sind {best_value} Basispunkte gegen "
                f"gepruefte {target}."
                if rate
                else "Police vorhanden, die erfasste Deckungssumme erreicht den "
                "geprueften Wert."
            ),
            matched=[best_policy.id],
            considered=considered,
            target_value_minor=target if money else None,
            actual_value_minor=best_value if money else None,
            line_key=best_policy.line_key,
        )
    schaerfer = (
        " Sie liegt auch unter der niedrigeren Schwelle, die der Regelsatz als Warnwert fuehrt."
        if rule.warn_value is not None and best_value < rule.warn_value
        else ""
    )
    return _Outcome(
        outcome="underinsured",
        reason=(
            f"Erfasst sind {best_value} Basispunkte, geprueft wurde gegen {target}."
            if rate
            else "Die erfasste Deckungssumme liegt unter dem geprueften Wert." + schaerfer
        ),
        matched=[best_policy.id],
        considered=considered,
        target_value_minor=target if money else None,
        actual_value_minor=best_value if money else None,
        gap_value_minor=target - best_value if money else None,
        line_key=best_policy.line_key,
    )


def _exists_outcome(rule: InsuranceRule, available: list[PolicyFacts]) -> _Outcome:
    """Vorhandensein, mit den zwei Spartengruppen aus dem Katalog.

    `any_line_keys` sind Sparten, von denen EINE genuegt (gesetzlich oder privat
    krankenversichert). `all_line_keys` sind Sparten, die ZUSAETZLICH bestehen
    muessen (Bauherrenhaftpflicht und Bauleistung). Ohne die Unterscheidung
    waere eine der beiden Katalogzeilen nicht abbildbar.
    """
    found = [p for p in available if p.line_key in rule.any_line_keys]
    extra = {key: [p for p in available if p.line_key == key] for key in rule.all_line_keys}
    missing_extra = [key for key, policies in extra.items() if not policies]
    if found and not missing_extra:
        matched = found + [p for policies in extra.values() for p in policies]
        return _Outcome(
            outcome="fulfilled",
            reason="Police dieser Sparte vorhanden.",
            matched=[p.id for p in matched],
            considered=[p.id for p in matched],
            line_key=found[0].line_key,
        )
    if found and missing_extra:
        return _Outcome(
            outcome="gap",
            reason=(
                "Erfasst ist nur ein Teil des geprueften Schutzes; es fehlt: "
                + ", ".join(missing_extra)
                + "."
            ),
            matched=[p.id for p in found],
            considered=[p.id for p in found],
            line_key=missing_extra[0],
        )
    return _Outcome(outcome="gap", reason="Keine laufende Police dieser Sparte erfasst.")


def _evaluate_target(
    rule: InsuranceRule,
    ctx: NeedsContext,
    person: PersonFacts | None,
    facts: Mapping[str, Any],
    trace: _Trace,
) -> _Outcome:
    """Prueft die Zielseite einer Regel gegen den Bestand."""
    available = _visible_policies(rule, ctx, person)

    if rule.special == "policy_not_counted":
        # Ausschlussart 2 aus Plan 10.3: "Police zaehlt nicht als Erfuellung".
        # Gesucht sind genau die Policen im genannten Status - sie bestehen, aber
        # sie erfuellen nichts, und der Befund sagt das, statt sie zu uebergehen.
        blocked = [
            p
            for p in ctx.policies
            if p.line_key in rule.any_line_keys and p.status in rule.policy_status_filter
        ]
        if not blocked:
            return _Outcome(outcome="not_applicable", reason="Keine solche Police vorhanden.")
        return _Outcome(
            outcome="gap",
            reason=(
                "Die vorhandene Police dieser Sparte hat den Status "
                f"{', '.join(sorted({p.status for p in blocked}))} und zaehlt deshalb nicht "
                "als vorhandener Schutz."
            ),
            matched=[p.id for p in blocked],
            considered=[p.id for p in blocked],
            line_key=blocked[0].line_key,
        )

    if rule.target_metric is None and rule.special == "policy_counts":
        # Das Spiegelbild aus R24: die Police zaehlt als vorhanden. Ein Hinweis,
        # keine Luecke - deshalb `fulfilled` mit der gefundenen Police.
        counted = [p for p in available if p.line_key in rule.any_line_keys]
        if not counted:
            return _Outcome(outcome="not_applicable", reason="Keine solche Police vorhanden.")
        return _Outcome(
            outcome="fulfilled",
            reason=(
                "Die Police zaehlt im Abgleich als vorhanden; massgeblich ist die "
                "erfasste beitragsfreie Leistung."
            ),
            matched=[p.id for p in counted],
            considered=[p.id for p in counted],
            line_key=counted[0].line_key,
        )

    if rule.target_metric == "policy_exists":
        return _exists_outcome(rule, available)

    if rule.target_metric == "not_needed":
        in_line = [p for p in available if p.line_key in rule.any_line_keys]
        if not in_line:
            return _Outcome(outcome="not_applicable", reason="Keine solche Police vorhanden.")
        return _Outcome(
            outcome="overinsured",
            reason="Die Police besteht, obwohl die Bedingung der Regel dagegen spricht.",
            matched=[p.id for p in in_line],
            considered=[p.id for p in in_line],
            line_key=in_line[0].line_key,
        )

    if rule.target_metric == "coverage_included":
        key = rule.target_coverage_key or ""
        # Plan 3.9 Punkt 4: derselbe Sachverhalt als Baustein und als
        # Einzelpolice muss dasselbe Ergebnis liefern. Der Baustein wird deshalb
        # an ALLEN laufenden Policen gesucht und nicht nur an der Zielsparte -
        # der Elementarbaustein haengt an der Wohngebaeudepolice.
        with_coverage = [p for p in available if p.covers(key)]
        standalone = [p for p in available if p.line_key == key]
        if with_coverage or standalone:
            matched = with_coverage + [p for p in standalone if p not in with_coverage]
            source = "als Baustein" if with_coverage else "als eigenstaendige Police"
            return _Outcome(
                outcome="fulfilled",
                reason=f"Der Schutz ist {source} erfasst.",
                matched=[p.id for p in matched],
                considered=[p.id for p in available],
                line_key=matched[0].line_key,
            )
        return _Outcome(
            outcome="gap",
            reason=(
                "Weder als Baustein einer vorhandenen Police noch als eigenstaendige "
                "Police erfasst."
            ),
            considered=[p.id for p in available if p.line_key in rule.any_line_keys],
        )

    in_line = [p for p in available if p.line_key in rule.any_line_keys]
    rate = rule.target_metric in ("benefit_rate_min", "benefit_rate_formula")
    target = (
        _resolve_formula(rule.target_formula, facts, trace)
        if rule.target_formula is not None
        else rule.target_value
    )
    return _amount_outcome(rule, in_line, target=target, rate=rate, trace=trace)


# ---------------------------------------------------------------------------
# Befunde
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    """Merkmalswerte als einfache Werte - `triggered_by` ist eine Zitatzeile.

    Ein verschachteltes Objekt waere ein Hinweis darauf, dass der Server rechnet,
    statt die Angabe zu zitieren, auf der er beruht.
    """
    if isinstance(value, list | tuple | set | frozenset):
        return ", ".join(sorted(str(item) for item in value)) or None
    if isinstance(value, date):
        return value.isoformat()
    return value


def _missing_questions(names: Sequence[str]) -> list[str]:
    """Fehlende Merkmale auf die Fragen zurueckgefuehrt, die sie beantworten."""
    found: list[str] = []
    for name in names:
        for question in FACT_SOURCES.get(name, (name,)):
            if question not in found:
                found.append(question)
    return found


def _missing_sentence(missing: Sequence[str]) -> str:
    labels = [FACT_LABELS.get(name, name) for name in _missing_questions(missing)]
    joined = ", ".join(labels) if labels else "eine Angabe"
    return (
        f"Diese Regel wurde nicht geprueft, weil eine Angabe fehlt: {joined}. "
        "Solange sie fehlt, behauptet der Abgleich hier weder Schutz noch Luecke."
    )


def _decision_for(rule_key: str, matched: Sequence[UUID], ctx: NeedsContext) -> RuleDecision | None:
    """Die Entscheidung, die auf diesen Befund passt.

    Die regelweite Entscheidung (ohne Policenbezug) ist der Normalfall - die
    meisten Regeln zeigen auf eine FEHLENDE Police.
    """
    decision = ctx.decisions.get((rule_key, None))
    if decision is not None:
        return decision
    for policy_id in matched:
        found = ctx.decisions.get((rule_key, policy_id))
        if found is not None:
            return found
    return None


def _apply_decision(
    finding: NeedsFinding, decision: RuleDecision | None, today: date
) -> NeedsFinding:
    """Die Nutzerentscheidung schlaegt das Regelergebnis - immer.

    Ausnahme ist nur die Wiedervorlage: ab `remind_on` gilt die Zurueckstellung
    nicht mehr, der Befund ist zurueck, und die abgelaufene Entscheidung bleibt
    trotzdem in der Antwort, damit die Oberflaeche erklaeren kann, warum.
    """
    if decision is None:
        return finding
    expired = decision.remind_on is not None and decision.remind_on <= today
    finding.decision = decision.model_copy(update={"expired": expired})
    if expired or decision.decision == "accepted":
        return finding
    finding.outcome = "dismissed"
    return finding


def _build_finding(
    rule: InsuranceRule,
    ctx: NeedsContext,
    person: PersonFacts | None,
    outcome: _Outcome,
    trace: _Trace,
    *,
    replace_rationale: bool,
) -> NeedsFinding:
    line_keys = list(rule.any_line_keys) + list(rule.all_line_keys)
    line_key = outcome.line_key or (line_keys[0] if line_keys else None)
    need_tier = ctx.line_tiers.get(line_key) if line_key else None
    finding = NeedsFinding(
        rule_key=rule.rule_key,
        risk_field=rule.risk_field,
        line_key=line_key,
        line_label=ctx.line_labels.get(line_key) if line_key else None,
        line_keys=line_keys,
        line_labels=[ctx.line_labels.get(key, key) for key in line_keys],
        coverage_key=rule.target_coverage_key,
        outcome=outcome.outcome,
        scope=rule.scope,
        priority=rule.priority,
        need_tier=cast(Any, need_tier),
        priority_source="rule" if rule.priority else ("line" if need_tier else None),
        headline=rule.headline,
        # Bei fehlender Angabe steht hier NICHT die Begruendung der Regel: sie
        # behauptet eine Luecke, die gerade nicht geprueft werden konnte.
        rationale=_missing_sentence(trace.missing) if replace_rationale else rule.rationale,
        outcome_reason=outcome.reason,
        triggered_by={name: _scalar(value) for name, value in trace.facts.items()},
        # Nur wo die Auswertung wirklich stehen geblieben ist. Ein
        # "trifft nicht zu" mit einer fehlenden Angabe widerspricht sich selbst:
        # R9a fragte so nach dem Fahrzeugalter, obwohl gar kein Fahrzeug
        # erfasst ist und die Regel deshalb gar nicht greift.
        missing_facts=list(trace.missing) if outcome.outcome == "unclear" else [],
        source_url=rule.source_url,
        source_checked_on=rule.source_checked_on,
        matched_policy_ids=list(outcome.matched),
        considered_policy_ids=list(outcome.considered),
        target_metric=rule.target_metric,
        target_formula=rule.target_formula,
        target_value_minor=outcome.target_value_minor,
        actual_value_minor=outcome.actual_value_minor,
        gap_value_minor=outcome.gap_value_minor,
        person_id=person.id if person else None,
        person_name=person.display_name if person else None,
    )
    return _apply_decision(finding, _decision_for(rule.rule_key, outcome.matched, ctx), ctx.today)


def evaluate(ctx: NeedsContext, rules: Sequence[InsuranceRule]) -> list[NeedsFinding]:
    """Wertet den ganzen Katalog aus - ein Befund je Regel und Person."""
    suspended = _suspended_persons(ctx, rules)
    findings: list[NeedsFinding] = []
    for rule in rules:
        if rule.scope == "person":
            if not ctx.persons:
                # Ohne erfasste Person verschwaende eine personenbezogene Regel
                # sonst spurlos - auch R1, die Krankenversicherungspflicht auf
                # S0. Leitsatz 4: der Abgleich sagt, dass er nichts weiss,
                # statt zu schweigen.
                findings.append(_no_person_finding(rule, ctx))
                continue
            for person in ctx.persons:
                if person.id in suspended:
                    findings.append(_suspended_finding(rule, ctx, person))
                    continue
                findings.append(_evaluate_one(rule, ctx, person))
        else:
            findings.append(_evaluate_one(rule, ctx, None))
    findings.sort(key=_sort_key)
    return findings


def _suspended_persons(ctx: NeedsContext, rules: Sequence[InsuranceRule]) -> set[UUID]:
    """Ausschlussart 1 aus Plan 10.3: "Person setzt aus".

    Sie steht als Regel im Katalog und nicht als Sonderfall im Auswerter - genau
    dafuer ist die Datenzeilenform da. Der Auswerter kennt nur, DASS es diese
    Art gibt, nicht welches Merkmal sie ausloest.
    """
    suspended: set[UUID] = set()
    for rule in rules:
        if rule.special != "person_suspends":
            continue
        for person in ctx.persons:
            facts = {**ctx.household, **person.facts}
            if evaluate_condition(rule.condition, facts, _Trace()) is True:
                suspended.add(person.id)
    return suspended


def _no_person_finding(rule: InsuranceRule, ctx: NeedsContext) -> NeedsFinding:
    """Personenbezogene Regel ohne eine einzige erfasste Person."""
    outcome = _Outcome(
        outcome="unclear",
        reason="Diese Regel prueft je Person; erfasst ist noch keine Person.",
    )
    # Wie beim leeren Bestand bleibt die Regelbegruendung stehen: sie sagt
    # allgemein, wofuer die Sparte da ist, und behauptet ueber diesen Haushalt
    # nichts. Ersetzt wuerde sie nur, wenn eine benennbare Angabe fehlte.
    return _build_finding(rule, ctx, None, outcome, _Trace(), replace_rationale=False)


def _suspended_finding(rule: InsuranceRule, ctx: NeedsContext, person: PersonFacts) -> NeedsFinding:
    trace = _Trace()
    trace.seen("died_on", person.facts.get("died_on"))
    outcome = _Outcome(
        outcome="not_applicable",
        reason="Fuer diese Person werden personenbezogene Regeln nicht geprueft.",
    )
    return _build_finding(rule, ctx, person, outcome, trace, replace_rationale=False)


def _evaluate_one(
    rule: InsuranceRule, ctx: NeedsContext, person: PersonFacts | None
) -> NeedsFinding:
    facts: dict[str, Any] = dict(ctx.household)
    if person is not None:
        facts.update(person.facts)
    trace = _Trace()

    applies = evaluate_condition(rule.condition, facts, trace)
    if applies is False:
        return _build_finding(
            rule,
            ctx,
            person,
            _Outcome(outcome="not_applicable", reason="Die Bedingung der Regel trifft nicht zu."),
            trace,
            replace_rationale=False,
        )
    if applies is None:
        return _build_finding(
            rule,
            ctx,
            person,
            _Outcome(outcome="unclear", reason=_missing_sentence(trace.missing)),
            trace,
            replace_rationale=True,
        )

    # "R2 erfuellt", "Hausrat vorhanden": ohne die Basispolice prueft die
    # Anschlussregel nichts. Ueber die Police und nicht ueber das Ergebnis der
    # Basisregel, weil jene `unclear` sein kann, waehrend die Police unstreitig
    # vorhanden ist.
    if rule.depends_on_line_key is not None:
        base = [
            p
            for p in _visible_policies(rule, ctx, person)
            if p.line_key == rule.depends_on_line_key
        ]
        if not base:
            label = ctx.line_labels.get(rule.depends_on_line_key, rule.depends_on_line_key)
            return _build_finding(
                rule,
                ctx,
                person,
                _Outcome(
                    outcome="not_applicable",
                    reason=(
                        "Diese Regel prueft eine vorhandene Police; erfasst ist keine der "
                        f"Sparte {label}."
                    ),
                ),
                trace,
                replace_rationale=False,
            )

    # Pflichtangaben ZULETZT, nicht zuerst: eine Regel, die diesen Haushalt gar
    # nichts angeht, darf keine Angabe verlangen. Vorgezogen fragte R22 jede
    # gesetzlich versicherte Person nach ihrem Beihilfebemessungssatz und R4a
    # jedes Kind nach seinem Nettoeinkommen - eine Onboarding-Fuehrung, die nach
    # Unzutreffendem fragt, ist genau der ungepflegte Bestand aus Leitsatz 4.
    # Fehlt die Angabe bei einer Regel, die greift, bleibt es `unclear`.
    missing = [name for name in rule.required_facts if facts.get(name) is None]
    if missing:
        for name in missing:
            trace.miss(name)
        return _build_finding(
            rule,
            ctx,
            person,
            _Outcome(outcome="unclear", reason=_missing_sentence(missing)),
            trace,
            replace_rationale=True,
        )

    # Die Regel greift. Ein `unclear` von der Zielseite behaelt die Begruendung
    # der Regel: dort fehlt eine Angabe AM BESTAND ("Deckungssumme nicht
    # erfasst"), und wogegen geprueft werden sollte, bleibt richtig und
    # nachlesbar.
    outcome = _soften_gap(_evaluate_target(rule, ctx, person, facts, trace), ctx)
    return _build_finding(rule, ctx, person, outcome, trace, replace_rationale=False)


def _sort_key(finding: NeedsFinding) -> tuple[int, int, str, str]:
    return (
        _OUTCOME_RANK.get(finding.outcome, 9),
        _TIER_RANK.get(finding.priority or finding.need_tier or "", 9),
        finding.rule_key,
        finding.person_name or "",
    )


# ---------------------------------------------------------------------------
# Endpunktdienste: Abgleich
# ---------------------------------------------------------------------------


async def run_check(session: AsyncSession, household_id: UUID, *, today: date) -> NeedsCheck:
    ctx = await load_context(session, household_id, today=today)
    catalog = rule_catalog()
    findings = evaluate(ctx, catalog.rules)
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.outcome] = counts.get(finding.outcome, 0) + 1
    return NeedsCheck(
        generated_on=today,
        checked_on=today,
        disclaimer=DISCLAIMER,
        counts=counts,
        stock_empty=ctx.stock_empty,
        profile_last_checked_on=ctx.last_checked_on,
        rules_evaluated=len(catalog.rules),
        findings=findings,
    )


async def get_completeness(
    session: AsyncSession, household_id: UUID, *, today: date
) -> NeedsCompleteness:
    """Welche fehlende Angabe blockiert welche Regel - die Onboarding-Fuehrung.

    Die Liste entsteht aus demselben Durchlauf wie die Befunde und nicht aus
    einer zweiten Ableitung: eine Angabe blockiert genau dann, wenn die
    Auswertung ohne sie stehen geblieben ist.
    """
    ctx = await load_context(session, household_id, today=today)
    findings = evaluate(ctx, rule_catalog().rules)

    blocked: dict[tuple[str, UUID | None], list[str]] = {}
    for finding in findings:
        if finding.outcome != "unclear":
            continue
        for fact in _missing_questions(finding.missing_facts):
            scope_person = finding.person_id if fact in PERSON_FACT_NAMES else None
            keys = blocked.setdefault((fact, scope_person), [])
            if finding.rule_key not in keys:
                keys.append(finding.rule_key)

    names = {person.id: person.display_name for person in ctx.persons}
    missing = [
        NeedsCompletenessItem(
            field=fact,
            label=FACT_LABELS.get(fact, fact),
            scope="person" if person_id is not None else "household",
            person_id=person_id,
            person_name=names.get(person_id) if person_id else None,
            blocks_rule_keys=sorted(rule_keys),
        )
        for (fact, person_id), rule_keys in sorted(
            blocked.items(), key=lambda item: (item[0][0], str(item[0][1] or ""))
        )
    ]

    answered = sum(1 for name in HOUSEHOLD_QUESTIONS if ctx.household.get(name) is not None)
    total = len(HOUSEHOLD_QUESTIONS)
    for person in ctx.persons:
        answered += sum(1 for name in PERSON_QUESTIONS if person.facts.get(name) is not None)
        total += len(PERSON_QUESTIONS)

    return NeedsCompleteness(
        answered=answered,
        total=total,
        blocked_rules=len({rule for item in missing for rule in item.blocks_rule_keys}),
        last_checked_on=ctx.last_checked_on,
        missing=missing,
    )


# ---------------------------------------------------------------------------
# Endpunktdienste: Profil
# ---------------------------------------------------------------------------

_PROFILE_JSON_COLUMNS: Final = frozenset({"pets"})


def _profile_placeholder(column: str) -> str:
    return f"cast(:{column} as jsonb)" if column in _PROFILE_JSON_COLUMNS else f":{column}"


def _profile_value(column: str, value: Any) -> Any:
    if column in _PROFILE_JSON_COLUMNS:
        return None if value is None else json.dumps(value)
    return value


async def get_profile(session: AsyncSession, household_id: UUID, *, today: date) -> NeedsProfile:
    """Die Angaben des Haushalts samt der Personen, die der Aufrufer sehen darf.

    Eine fehlende Zeile ist kein Fehler: der Abgleich laeuft trotzdem und meldet
    dann eben ueberall "Angabe fehlt". Ein 404 zwaenge die Oberflaeche, die
    Feldliste ein zweites Mal zu kennen.
    """
    row = await _load_profile_row(session, household_id)
    persons = [
        _as_person_profile(entry) for entry in await _load_person_rows(session, household_id)
    ]
    if row is None:
        return NeedsProfile(exists=False, persons=persons)
    return NeedsProfile.model_validate({**row, "exists": True, "persons": persons})


async def save_profile(
    session: AsyncSession,
    household_id: UUID,
    payload: NeedsProfileInput,
    *,
    today: date,
) -> NeedsProfile:
    """Schreibt genau die gesendeten Felder.

    Ein weggelassenes Feld bleibt unveraendert, ein gesendetes `null` loescht die
    Antwort. Der Unterschied ist hier nicht Geschmackssache: ein Client, der eine
    Frage nicht anbietet, wuerde sonst bei jedem Speichern die Antwort darauf
    loeschen - und aus der geloeschten Antwort wuerde beim naechsten Lauf eine
    Rueckfrage, die der Nutzer laengst beantwortet hatte.
    """
    values = payload.model_dump(exclude_unset=True)
    # Das Speichern der Angaben IST die Pruefung: ohne ausdrueckliches Datum
    # traegt die Wiedervorlage den heutigen Tag.
    values.setdefault("last_checked_on", today)
    columns = list(values)
    assignments = ", ".join(f"{column} = excluded.{column}" for column in columns)
    placeholders = ", ".join(_profile_placeholder(column) for column in columns)
    await session.execute(
        text(
            f"insert into insurance_needs_profile (household_id, {', '.join(columns)}) "
            f"values (:hid, {placeholders}) "
            f"on conflict (household_id) do update set {assignments}, updated_at = now()"
        ),
        {
            "hid": str(household_id),
            **{column: _profile_value(column, values[column]) for column in columns},
        },
    )
    return await get_profile(session, household_id, today=today)


def _as_person_profile(row: Mapping[str, Any]) -> PersonNeedsProfile:
    data = dict(row)
    data["exists"] = bool(data.pop("profile_exists", False))
    data["can_manage"] = bool(data.get("can_manage"))
    return PersonNeedsProfile.model_validate(data)


async def _require_person(
    session: AsyncSession, household_id: UUID, person_id: UUID, *, for_write: bool
) -> dict[str, Any]:
    rows = await _load_person_rows(session, household_id)
    found = next((row for row in rows if row["insured_person_id"] == person_id), None)
    if found is None:
        # 404 und nicht 403: die Existenz einer Person, die der Aufrufer nicht
        # sehen darf, wird nicht verraten.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=insurance_coverage_service.INSURANCE_PERSON_NOT_FOUND,
        )
    if for_write and not found.get("can_manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=INSURANCE_PERSON_PROFILE_FORBIDDEN
        )
    return found


async def get_person_profile(
    session: AsyncSession, household_id: UUID, person_id: UUID
) -> PersonNeedsProfile:
    return _as_person_profile(
        await _require_person(session, household_id, person_id, for_write=False)
    )


async def save_person_profile(
    session: AsyncSession,
    household_id: UUID,
    person_id: UUID,
    payload: PersonNeedsProfileInput,
) -> PersonNeedsProfile:
    """Schreibt die Bedarfsangaben einer Person - und nur ihre.

    `died_on` geht in denselben Aufruf, landet aber in `health_insured_persons`:
    es ist eine Spalte des Gesundheitszweigs, keine zweite Personenliste. Beide
    Schreibwege haengen an derselben Bedingung (`health_can_manage_person`), es
    entsteht also kein zweites Rechtesystem.
    """
    await _require_person(session, household_id, person_id, for_write=True)
    values = payload.model_dump(exclude_unset=True)
    died_on_set = "died_on" in values
    died_on = values.pop("died_on", None)

    if values:
        columns = list(values)
        assignments = ", ".join(f"{column} = excluded.{column}" for column in columns)
        placeholders = ", ".join(f":{column}" for column in columns)
        await session.execute(
            text(
                "insert into insurance_person_profile "
                f"(insured_person_id, household_id, {', '.join(columns)}) "
                f"values (:pid, :hid, {placeholders}) "
                "on conflict (insured_person_id) do update set "
                f"{assignments}, updated_at = now()"
            ),
            {"pid": str(person_id), "hid": str(household_id), **values},
        )
    elif not died_on_set:
        # Leerer Koerper: kein Schreibvorgang, keine hochgezaehlte Version.
        return await get_person_profile(session, household_id, person_id)

    if died_on_set:
        await session.execute(
            text(
                "update health_insured_persons set died_on = :died_on, updated_at = now() "
                "where id = :pid and household_id = :hid"
            ),
            {"died_on": died_on, "pid": str(person_id), "hid": str(household_id)},
        )
    return await get_person_profile(session, household_id, person_id)


# ---------------------------------------------------------------------------
# Endpunktdienste: Entscheidungen
# ---------------------------------------------------------------------------

_DECISION_INSERT: Final = (
    "insert into insurance_rule_decisions "
    "  (household_id, rule_key, policy_id, decision, reason, reason_note, "
    "   remind_on, decided_by) "
    "values (:hid, :rule_key, cast(:policy_id as uuid), :decision, :reason, "
    "  :reason_note, :remind_on, cast(:user_id as uuid)) "
)
_DECISION_UPDATE: Final = (
    "do update set decision = excluded.decision, reason = excluded.reason, "
    "  reason_note = excluded.reason_note, remind_on = excluded.remind_on, "
    "  decided_by = excluded.decided_by, decided_at = now()"
)


async def save_decision(
    session: AsyncSession,
    household_id: UUID,
    rule_key: str,
    payload: RuleDecisionRequest,
    *,
    user_id: UUID,
) -> None:
    """Speichert, was der Nutzer zu einem Befund gesagt hat.

    Der einzige persistierte Teil des Abgleichs. Ein unbekannter `rule_key` wird
    abgewiesen, sobald es ueberhaupt einen Katalog gibt - ohne Katalog waere die
    Pruefung eine Behauptung ueber etwas, das der Server nicht kennt.

    Die Eindeutigkeit haengt an zwei PARTIELLEN Indizes, nicht an einem
    Schluessel. Ein Upsert muss den passenden benennen, samt seiner Bedingung:
    ohne Policenbezug den regelweiten, mit Policenbezug den anderen.
    """
    catalog = rule_catalog()
    if catalog.rules and all(rule.rule_key != rule_key for rule in catalog.rules):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_RULE_UNKNOWN)
    conflict = (
        "on conflict (household_id, rule_key) where policy_id is null "
        if payload.policy_id is None
        else "on conflict (household_id, rule_key, policy_id) where policy_id is not null "
    )
    await session.execute(
        text(_DECISION_INSERT + conflict + _DECISION_UPDATE),
        {
            "hid": str(household_id),
            "rule_key": rule_key,
            "policy_id": str(payload.policy_id) if payload.policy_id else None,
            "decision": payload.decision,
            "reason": payload.reason,
            "reason_note": payload.reason_note,
            "remind_on": payload.remind_on,
            "user_id": str(user_id),
        },
    )


async def clear_decision(
    session: AsyncSession, household_id: UUID, rule_key: str, policy_id: UUID | None
) -> None:
    """Nimmt eine Entscheidung zurueck - idempotent.

    Kein 404, wenn nichts zu loeschen war: der Zustand danach ist genau der
    gewuenschte, und ein Fehler waere fuer die Oberflaeche nur ein zweiter Weg
    zum selben Ergebnis.
    """
    await session.execute(
        text(
            "delete from insurance_rule_decisions "
            "where household_id = :hid and rule_key = :rule_key "
            "  and policy_id is not distinct from cast(:policy_id as uuid)"
        ),
        {
            "hid": str(household_id),
            "rule_key": rule_key,
            "policy_id": str(policy_id) if policy_id else None,
        },
    )


# ---------------------------------------------------------------------------
# Doppelversicherung (Plan 10.4)
# ---------------------------------------------------------------------------


def _related(left: PolicyFacts, right: PolicyFacts) -> bool:
    """Die drei ausgenommenen Paare aus Plan 10.4.

    Ohne sie meldete die Erkennung den Normalfall als Fehler: den Wechsel, das
    Buendel und zwei Personen mit je eigener Police derselben Sparte.
    """
    if left.predecessor_policy_id == right.id or right.predecessor_policy_id == left.id:
        return True
    if left.parent_policy_id == right.id or right.parent_policy_id == left.id:
        return True
    return bool(left.person_ids and right.person_ids and not (left.person_ids & right.person_ids))


def _different_objects(left: PolicyFacts, right: PolicyFacts) -> bool:
    return bool(left.object_ids and right.object_ids and not (left.object_ids & right.object_ids))


async def find_double_insurance(
    session: AsyncSession, household_id: UUID, *, today: date
) -> list[DoubleInsuranceFinding]:
    """Zwei Policen fuer dieselbe Sache.

    Die Erkennung liest `insurance_lines.covered_by_keys` - "Glasbruch steckt
    schon in Hausrat und Gebaeude" ist eine Katalogaussage und keine Sonderlogik
    hier. Ein `contained_in`-Befund wird erst ausgespielt, wenn die Bausteine der
    deckenden Police erfasst sind; vorher lautet er ehrlich "koennte doppelt
    sein, ich weiss es aber nicht".
    """
    ctx = await load_context(session, household_id, today=today)
    in_force = [p for p in ctx.policies if p.in_force(today) and p.line_key != "unknown"]
    findings: list[DoubleInsuranceFinding] = []

    for index, left in enumerate(in_force):
        for right in in_force[index + 1 :]:
            if left.line_key != right.line_key:
                continue
            if _related(left, right) or _different_objects(left, right):
                continue
            findings.append(
                DoubleInsuranceFinding(
                    kind="same_line",
                    certainty="established",
                    policy_id=left.id,
                    policy_label=left.label,
                    other_policy_id=right.id,
                    other_policy_label=right.label,
                    line_key=left.line_key,
                    line_label=left.line_label,
                    other_line_key=right.line_key,
                    other_line_label=right.line_label,
                    yearly_minor=right.yearly_minor or left.yearly_minor,
                    rationale=(
                        f"Zwei laufende Policen der Sparte {left.line_label}. "
                        + DOUBLE_INSURANCE_RATIONALE
                    ),
                )
            )

    for contained in in_force:
        # Nur die Einzelpolicenform eines Bausteins kann in etwas anderem
        # stecken: eine Sparte, die fuer GENAU EINEN Baustein steht, und zwar
        # fuer den gleichnamigen (Glas, Elementar, Fahrrad). Ohne diese
        # Einschraenkung meldete der Katalog auch Wohngebaeude als "in Hausrat
        # enthalten" - beide fuehren Glas und Elementar in `covered_by_keys`,
        # und die eine Liste ist zufaellig eine Teilmenge der anderen. Ein
        # falscher Befund ist hier schlimmer als ein fehlender.
        if contained.covered_by_keys != (contained.line_key,):
            continue
        for carrier in in_force:
            if carrier.id == contained.id or carrier.line_key == contained.line_key:
                continue
            if contained.line_key not in carrier.covered_by_keys:
                continue
            if _related(contained, carrier) or _different_objects(contained, carrier):
                continue
            label = ctx.coverage_labels.get(contained.line_key, contained.line_label)
            if not carrier.has_coverages:
                findings.append(
                    DoubleInsuranceFinding(
                        kind="contained_in",
                        certainty="unclear",
                        policy_id=contained.id,
                        policy_label=contained.label,
                        other_policy_id=carrier.id,
                        other_policy_label=carrier.label,
                        line_key=contained.line_key,
                        line_label=contained.line_label,
                        other_line_key=carrier.line_key,
                        other_line_label=carrier.line_label,
                        coverage_key=contained.line_key,
                        coverage_label=label,
                        yearly_minor=contained.yearly_minor,
                        rationale=(
                            f"{label} ist in der Sparte {carrier.line_label} haeufig schon "
                            "enthalten. Ob das in dieser Police so ist, steht hier nicht - "
                            "die Bausteine sind nicht erfasst."
                        ),
                        hint=f"Bausteine der Police {carrier.label} erfassen.",
                        missing_coverage_policy_ids=[carrier.id],
                    )
                )
                continue
            if not carrier.covers(contained.line_key):
                # Ausdruecklich NICHT enthalten: das ist eine Aussage, und sie
                # sagt "keine Doppelung".
                continue
            findings.append(
                DoubleInsuranceFinding(
                    kind="contained_in",
                    certainty="established",
                    policy_id=contained.id,
                    policy_label=contained.label,
                    other_policy_id=carrier.id,
                    other_policy_label=carrier.label,
                    line_key=contained.line_key,
                    line_label=contained.line_label,
                    other_line_key=carrier.line_key,
                    other_line_label=carrier.line_label,
                    coverage_key=contained.line_key,
                    coverage_label=label,
                    yearly_minor=contained.yearly_minor,
                    rationale=(
                        f"{label} ist als Baustein der Police {carrier.label} erfasst und "
                        "besteht daneben als eigene Police. " + DOUBLE_INSURANCE_RATIONALE
                    ),
                )
            )
    return findings


__all__ = [
    "COUNTING_STATUS",
    "DISCLAIMER",
    "FACT_LABELS",
    "FACT_SOURCES",
    "HOUSEHOLD_QUESTIONS",
    "PERSON_QUESTIONS",
    "NeedsContext",
    "PersonFacts",
    "PolicyFacts",
    "RuleCatalog",
    "clear_decision",
    "evaluate",
    "evaluate_condition",
    "find_double_insurance",
    "get_completeness",
    "get_person_profile",
    "get_profile",
    "load_context",
    "rule_catalog",
    "run_check",
    "save_decision",
    "save_person_profile",
    "save_profile",
]
