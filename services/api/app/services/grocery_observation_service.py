"""Preissichtungen schreiben und daraus den Bewertungspreis ableiten.

Die Trennung, um die es hier geht:

  * ingredient_price_observations ist das GEDAECHTNIS - jede Sichtung mit Ort,
    Zeit, Produktmerkmalen und Herkunft, anhaengend, nie ueberschrieben.
  * ingredient_prices.price_minor ist der BEWERTUNGSPREIS - die eine Zahl, aus
    der Gerichts- und Wochenkosten gerechnet werden. Sie wird aus dem
    Gedaechtnis abgeleitet und dorthin zurueckgeschrieben.

Alles, was der bestehende Rechenkern (grocery_pricing_service) liest, bleibt
damit unveraendert. Er sieht weiterhin genau eine Preiszeile je Zutat und muss
von Sichtungen nichts wissen.

Zwei Regeln, die nicht verhandelbar sind:

1. ANGEBOTE GEHEN NIE IN DEN BEWERTUNGSPREIS. Ein Gericht wird nicht dauerhaft
   billiger, weil Butter diese Woche im Prospekt steht. Angebote wirken auf die
   Einkaufsliste der betroffenen Woche - das ist die Fortschreibung der bereits
   vorhandenen Trennung zwischen cost_for ("was ist die Mahlzeit wert?") und
   purchase_for ("was wird an der Kasse faellig?").
2. HANDEINGABE SCHLAEGT ABLEITUNG. Zeilen mit price_source = 'manual' fasst
   dieses Modul nicht an. Die Sperre steht in der WHERE-Klausel des UPDATE und
   nicht in einem Python-if, damit sie auch ein spaeterer Aufrufer nicht
   versehentlich umgeht.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.grocery_price_sheet_service import ParsedObservation

logger = logging.getLogger(__name__)

DEFAULT_STRATEGY: Final = "latest"
DEFAULT_MAX_AGE_DAYS: Final = 180

_OBSERVATION_COLUMNS: Final = (
    "id, ingredient_key, ingredient_name, store_id, brand, quality_tier, brand_tier, "
    "observed_on, is_promotion, offer_valid_from, offer_valid_to, price_minor, "
    "pack_quantity, pack_unit, base_quantity, base_family, unit_price_micro, "
    "source, source_ref, note, created_at"
)


@dataclass(frozen=True)
class PriceSettings:
    default_strategy: str = DEFAULT_STRATEGY
    preferred_store_id: UUID | None = None
    max_age_days: int = DEFAULT_MAX_AGE_DAYS


@dataclass(frozen=True)
class Candidate:
    """Eine Sichtung, wie sie fuer die Ableitung gebraucht wird."""

    id: UUID
    store_id: UUID | None
    quality_tier: str
    observed_on: date
    is_promotion: bool
    price_minor: int
    pack_quantity: Decimal
    pack_unit: str | None
    base_family: str
    unit_price_micro: int


@dataclass(frozen=True)
class Resolution:
    """Ergebnis der Ableitung fuer EINE Zutat.

    `reason` ist kein Protokolltext, sondern Teil der Antwort an die
    Oberflaeche: "es gibt keinen Preis" und "es gibt nur Angebotssichtungen"
    sind fuer den Nutzer voellig verschiedene Lagen, und beide muessen anders
    aussehen als ein stillschweigend alter Wert."""

    observation: Candidate | None
    is_stale: bool
    reason: str


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def load_settings(session: AsyncSession, household_id: UUID) -> PriceSettings:
    """Haushaltsvorgaben, oder die Standardwerte. Eine fehlende Zeile ist der
    Normalfall und kein Grund, irgendetwas anzulegen."""
    row = (
        await session.execute(
            text(
                "select default_strategy, preferred_store_id, max_age_days "
                "from grocery_price_settings where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).one_or_none()
    if row is None:
        return PriceSettings()
    return PriceSettings(
        default_strategy=row.default_strategy,
        preferred_store_id=row.preferred_store_id,
        max_age_days=row.max_age_days,
    )


async def ensure_store(
    session: AsyncSession,
    household_id: UUID,
    name: str | None,
    location: str | None = None,
) -> UUID | None:
    """Laden nach Name (und optionaler Filiale) holen oder anlegen.

    Ohne Namen bleibt es bei NULL - "irgendwo gesehen" ist eine gueltige
    Angabe, und daraus einen Laden namens "Unbekannt" zu erfinden wuerde den
    Ladenvergleich mit einer Sammelzeile verschmutzen."""
    clean_name = (name or "").strip()
    if not clean_name:
        return None
    clean_location = (location or "").strip() or None

    params = {
        "household_id": str(household_id),
        "name": clean_name,
        "location": clean_location,
    }
    existing = (
        await session.execute(
            text(
                "select id from grocery_stores where household_id = :household_id "
                "and lower(name) = lower(:name) "
                "and lower(coalesce(location, '')) = lower(coalesce(:location, ''))"
            ),
            params,
        )
    ).one_or_none()
    if existing is not None:
        return UUID(str(existing.id))

    created = (
        await session.execute(
            text(
                "insert into grocery_stores (household_id, name, location, store_kind) "
                "values (:household_id, :name, :location, :store_kind) returning id"
            ),
            {**params, "store_kind": _guess_store_kind(clean_name)},
        )
    ).one()
    return UUID(str(created.id))


# Reine Vorbelegung der Kategorie beim erstmaligen Anlegen. Bewusst eine kurze,
# offensichtliche Liste statt einer Heuristik: der Nutzer kann sie jederzeit
# aendern, und danebenzuliegen kostet einen Klick, waehrend eine erratene
# Kategorie fuer einen unbekannten Laden nur Vertrauen kostet.
_DISCOUNTERS: Final = frozenset({"aldi", "aldi süd", "aldi sued", "aldi nord", "lidl",
                                 "netto", "penny", "norma"})
_BIO_MARKETS: Final = frozenset({"alnatura", "denns", "denn's", "bioladen", "biomarkt",
                                 "basic"})


def _guess_store_kind(name: str) -> str:
    lowered = name.strip().lower()
    if lowered in _DISCOUNTERS:
        return "discounter"
    if lowered in _BIO_MARKETS:
        return "biomarkt"
    return "supermarkt"


async def insert_observations(
    session: AsyncSession,
    household_id: UUID,
    observations: list[ParsedObservation],
    *,
    source: str,
    source_ref: str | None = None,
    store_ids: dict[int, UUID | None] | None = None,
) -> tuple[int, int]:
    """Sichtungen anhaengen. Gibt (eingefuegt, uebersprungen) zurueck.

    Uebersprungen heisst: dieselbe Sichtung liegt schon vor (Fingerabdruck).
    Das ist der Normalfall beim versehentlichen Doppelimport und ausdruecklich
    kein Fehler - `on conflict do nothing` statt einer Ausnahme, damit ein
    zweiter Lauf derselben Datei folgenlos bleibt und nicht auf halber Strecke
    abbricht."""
    if not observations:
        return (0, 0)

    resolved_stores = store_ids or {}
    inserted = 0
    for observation in observations:
        store_id = resolved_stores.get(observation.row_number)
        if store_id is None and observation.store_name:
            store_id = await ensure_store(
                session, household_id, observation.store_name, observation.store_location
            )
        result = await session.execute(
            text(
                "insert into ingredient_price_observations ("
                "  household_id, ingredient_key, ingredient_name, store_id, brand, "
                "  quality_tier, brand_tier, observed_on, is_promotion, offer_valid_from, "
                "  offer_valid_to, price_minor, pack_quantity, pack_unit, base_quantity, "
                "  base_family, source, source_ref, fingerprint, note"
                ") values ("
                "  :household_id, :ingredient_key, :ingredient_name, :store_id, :brand, "
                "  :quality_tier, :brand_tier, :observed_on, :is_promotion, :valid_from, "
                "  :valid_to, :price_minor, :pack_quantity, :pack_unit, :base_quantity, "
                "  :base_family, :source, :source_ref, :fingerprint, :note"
                ") on conflict (household_id, fingerprint) where fingerprint is not null "
                "do nothing returning id"
            ),
            {
                "household_id": str(household_id),
                "ingredient_key": observation.ingredient_key,
                "ingredient_name": observation.ingredient_name,
                "store_id": str(store_id) if store_id else None,
                "brand": observation.brand,
                "quality_tier": observation.quality_tier,
                "brand_tier": observation.brand_tier,
                "observed_on": observation.observed_on,
                "is_promotion": observation.is_promotion,
                "valid_from": observation.offer_valid_from,
                "valid_to": observation.offer_valid_to,
                "price_minor": observation.price_minor,
                "pack_quantity": observation.pack_quantity,
                "pack_unit": observation.pack_unit,
                "base_quantity": observation.base_quantity,
                "base_family": observation.base_family,
                "source": source,
                "source_ref": source_ref,
                "fingerprint": observation.fingerprint,
                "note": observation.note,
            },
        )
        if result.one_or_none() is not None:
            inserted += 1

    return (inserted, len(observations) - inserted)


async def load_candidates(
    session: AsyncSession,
    household_id: UUID,
    ingredient_keys: list[str] | None = None,
) -> dict[str, list[Candidate]]:
    """Sichtungen je Zutatenschluessel, juengste zuerst."""
    query = (
        "select ingredient_key, id, store_id, quality_tier, observed_on, is_promotion, "
        "price_minor, pack_quantity, pack_unit, base_family, unit_price_micro "
        "from ingredient_price_observations where household_id = :household_id"
    )
    params: dict[str, Any] = {"household_id": str(household_id)}
    if ingredient_keys is not None:
        if not ingredient_keys:
            return {}
        query += " and ingredient_key = any(:keys)"
        params["keys"] = list(ingredient_keys)
    query += " order by ingredient_key, observed_on desc, created_at desc"

    grouped: dict[str, list[Candidate]] = {}
    for row in (await session.execute(text(query), params)).all():
        grouped.setdefault(row.ingredient_key, []).append(
            Candidate(
                id=UUID(str(row.id)),
                store_id=UUID(str(row.store_id)) if row.store_id else None,
                quality_tier=row.quality_tier,
                observed_on=row.observed_on,
                is_promotion=row.is_promotion,
                price_minor=row.price_minor,
                pack_quantity=_as_decimal(row.pack_quantity),
                pack_unit=row.pack_unit,
                base_family=row.base_family,
                unit_price_micro=row.unit_price_micro,
            )
        )
    return grouped


def resolve(
    candidates: list[Candidate],
    *,
    settings: PriceSettings,
    strategy: str | None = None,
    preferred_store_id: UUID | None = None,
    quality_preference: str | None = None,
    planning_base_family: str | None = None,
    today: date,
) -> Resolution:
    """Waehlt aus Sichtungen die eine, die den Bewertungspreis stellt.

    Reine Funktion ohne Datenbank, damit die Auswahlregel fuer sich testbar
    bleibt - sie ist der Kern des Moduls und die Stelle, an der ein Fehler
    still in jede Gerichtskostenschaetzung durchschlaegt.

    Reihenfolge der Filter: Angebote raus, dann Einheitenfamilie, dann Laden,
    dann Bioanspruch, dann Alter. Jeder Filter, der alles wegnimmt, wird
    zurueckgenommen statt zu einem leeren Ergebnis zu fuehren - lieber eine
    Sichtung aus dem falschen Laden als gar keine Kostenschaetzung, solange
    sichtbar bleibt, welche es geworden ist.
    """
    if not candidates:
        return Resolution(None, is_stale=True, reason="keine_sichtung")

    # 1. Angebote fliegen raus - siehe Modulkopf, Regel 1.
    regular = [item for item in candidates if not item.is_promotion]
    if not regular:
        return Resolution(None, is_stale=True, reason="nur_angebote")

    # 2. Einheitenfamilie: Grundpreise ueber Familiengrenzen zu vergleichen
    #    ("500 g" gegen "1 Pkg") waere geraten. Ohne Vorgabe gewinnt die
    #    Familie der juengsten Sichtung.
    family = planning_base_family or regular[0].base_family
    in_family = [item for item in regular if item.base_family == family]
    if not in_family:
        in_family = regular

    # 3. Bevorzugter Laden. Greift der Filter ins Leere, gilt er nicht -
    #    ein "kein Preis", nur weil man diese Woche woanders war, waere
    #    schlechter als ein Preis mit sichtbarer Herkunft.
    store_id = preferred_store_id or settings.preferred_store_id
    scoped = [item for item in in_family if item.store_id == store_id] if store_id else []
    pool = scoped or in_family

    # 4. Bioanspruch.
    if quality_preference == "organic_only":
        organic = [item for item in pool if item.quality_tier == "organic"]
        if not organic:
            return Resolution(None, is_stale=True, reason="keine_bio_sichtung")
        pool = organic
    elif quality_preference == "prefer_organic":
        organic = [item for item in pool if item.quality_tier == "organic"]
        pool = organic or pool

    # 5. Alter. Zu alte Sichtungen werden NICHT verworfen, sondern nur
    #    gekennzeichnet: der letzte bekannte Preis ist immer noch die beste
    #    verfuegbare Auskunft, er ist nur nicht mehr frisch.
    cutoff = today - timedelta(days=settings.max_age_days)
    fresh = [item for item in pool if item.observed_on >= cutoff]
    is_stale = not fresh
    pool = fresh or pool

    chosen_strategy = strategy or settings.default_strategy
    if chosen_strategy == "cheapest":
        # Bei gleichem Grundpreis die juengere Sichtung - sie ist die
        # wahrscheinlichere Auskunft ueber heute.
        best = min(pool, key=lambda item: (item.unit_price_micro, -item.observed_on.toordinal()))
        reason = "guenstigster"
    else:
        # Bei gleichem Datum der guenstigere Grundpreis: zwei Sichtungen
        # desselben Tages sind kein Widerspruch, sondern zwei Laeden.
        best = min(pool, key=lambda item: (-item.observed_on.toordinal(), item.unit_price_micro))
        reason = "zuletzt_gesehen"

    return Resolution(best, is_stale=is_stale, reason=f"{reason}_alt" if is_stale else reason)


@dataclass(frozen=True)
class PriceRowState:
    """Steuerfelder einer Preiszeile - was die Ableitung wissen muss."""

    name: str
    key: str
    price_source: str
    price_strategy: str | None
    preferred_store_id: UUID | None
    quality_preference: str | None
    planning_base_family: str | None
    pinned_observation_id: UUID | None


async def refresh_valuation_prices(
    session: AsyncSession,
    household_id: UUID,
    *,
    ingredient_keys: list[str] | None = None,
    today: date | None = None,
) -> int:
    """Schreibt den Bewertungspreis aller abgeleiteten Zeilen neu. Gibt die
    Anzahl tatsaechlich geaenderter Zeilen zurueck.

    Aufzurufen nach jeder Aenderung am Gedaechtnis oder an den Vorgaben:
    Sichtung angelegt, Sichtung geloescht, Strategie umgestellt, Einstellungen
    gespeichert. Zeilen mit price_source = 'manual' bleiben unberuehrt."""
    reference_day = today or date.today()
    settings = await load_settings(session, household_id)

    query = (
        "select name, price_source, price_strategy, preferred_store_id, "
        "quality_preference, planning_base_family, price_observation_id "
        "from ingredient_prices where household_id = :household_id "
        "and price_source in ('derived', 'pinned')"
    )
    params: dict[str, Any] = {"household_id": str(household_id)}
    if ingredient_keys is not None:
        if not ingredient_keys:
            return 0
        query += " and lower(btrim(name)) = any(:keys)"
        params["keys"] = list(ingredient_keys)

    rows = (await session.execute(text(query), params)).all()
    if not rows:
        return 0

    states = [
        PriceRowState(
            name=row.name,
            key=row.name.strip().lower(),
            price_source=row.price_source,
            price_strategy=row.price_strategy,
            preferred_store_id=(
                UUID(str(row.preferred_store_id)) if row.preferred_store_id else None
            ),
            quality_preference=row.quality_preference,
            planning_base_family=row.planning_base_family,
            pinned_observation_id=(
                UUID(str(row.price_observation_id)) if row.price_observation_id else None
            ),
        )
        for row in rows
    ]
    grouped = await load_candidates(session, household_id, [state.key for state in states])

    changed = 0
    for state in states:
        candidates = grouped.get(state.key, [])
        if state.price_source == "pinned":
            resolution = _resolve_pinned(state, candidates)
        else:
            resolution = resolve(
                candidates,
                settings=settings,
                strategy=state.price_strategy,
                preferred_store_id=state.preferred_store_id,
                quality_preference=state.quality_preference,
                planning_base_family=state.planning_base_family,
                today=reference_day,
            )
        if await _write_back(session, household_id, state, resolution):
            changed += 1
    return changed


def _resolve_pinned(state: PriceRowState, candidates: list[Candidate]) -> Resolution:
    """Festgenagelte Sichtung. Ist sie geloescht, faellt die Zeile NICHT still
    auf eine andere zurueck - der Nutzer hat sich ausdruecklich fuer eine
    bestimmte entschieden, und ein stiller Ersatz waere eine Entscheidung, die
    niemand getroffen hat."""
    pinned = next((item for item in candidates if item.id == state.pinned_observation_id), None)
    if pinned is None:
        return Resolution(None, is_stale=True, reason="festgenagelte_sichtung_fehlt")
    return Resolution(pinned, is_stale=False, reason="festgenagelt")


async def _write_back(
    session: AsyncSession,
    household_id: UUID,
    state: PriceRowState,
    resolution: Resolution,
) -> bool:
    """Ergebnis in ingredient_prices zuruecklegen.

    Ohne verwertbare Sichtung wird der bisherige Betrag NICHT ueberschrieben,
    sondern nur als alt markiert: eine stille 0 waere falsch, und ein Loeschen
    wuerde jedes Gericht unbewertet machen, das gestern noch eine Schaetzung
    hatte."""
    if resolution.observation is None:
        marked = (
            await session.execute(
                text(
                    "update ingredient_prices set price_is_stale = true, updated_at = now() "
                    "where household_id = :household_id and lower(btrim(name)) = :key "
                    "and price_source <> 'manual' and price_is_stale is distinct from true "
                    "returning id"
                ),
                {"household_id": str(household_id), "key": state.key},
            )
        ).first()
        return marked is not None

    chosen = resolution.observation
    written = (
        await session.execute(
            text(
                "update ingredient_prices set price_minor = :price_minor, "
                "pack_quantity = :pack_quantity, pack_unit = :pack_unit, "
                "price_observation_id = :observation_id, price_observed_on = :observed_on, "
                "price_is_stale = :is_stale, updated_at = now() "
                # Die Sperre gegen Handeingaben steht hier und nicht im Aufrufer.
                "where household_id = :household_id and lower(btrim(name)) = :key "
                "and price_source <> 'manual' "
                # Nur schreiben, wenn sich wirklich etwas aendert - sonst waere
                # jede Neuberechnung eine Aenderung an updated_at ohne Inhalt.
                "and (price_minor is distinct from :price_minor "
                "  or pack_quantity is distinct from :pack_quantity "
                "  or pack_unit is distinct from :pack_unit "
                "  or price_observation_id is distinct from :observation_id "
                "  or price_is_stale is distinct from :is_stale) "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "key": state.key,
                "price_minor": chosen.price_minor,
                "pack_quantity": chosen.pack_quantity,
                "pack_unit": chosen.pack_unit,
                "observation_id": str(chosen.id),
                "observed_on": chosen.observed_on,
                "is_stale": resolution.is_stale,
            },
        )
    ).first()
    return written is not None


async def count_manual_rows_with_observations(
    session: AsyncSession, household_id: UUID
) -> int:
    """Wie viele Zutaten trotz vorhandener Sichtungen weiter mit ihrer von
    Hand getippten Zahl rechnen.

    Gebraucht fuer eine ehrliche Rueckmeldung nach dem Import: alle
    Bestandszeilen stehen auf 'manual' (so ist die Erweiterung gebaut), also
    aendert der erste Import ihren Bewertungspreis absichtlich NICHT. Ohne
    diese Zahl saehe der Lauf aus, als haette er nichts bewirkt - dabei ist
    das Gedaechtnis sehr wohl gefuellt worden.

    Bewusst haushaltsweit statt auf die uebergebenen Schluessel bezogen: eine
    grosse Datei wird in Stuecken uebernommen, und eine je Stueck gezaehlte
    Zahl waere weder summierbar (dieselbe Zutat kaeme mehrfach vor) noch
    einzeln aussagekraeftig."""
    row = (
        await session.execute(
            text(
                "select count(*) as anzahl from ingredient_prices p "
                "where p.household_id = :household_id and p.price_source = 'manual' "
                "and exists (select 1 from ingredient_price_observations o "
                "            where o.household_id = p.household_id "
                "            and o.ingredient_key = lower(btrim(p.name)))"
            ),
            {"household_id": str(household_id)},
        )
    ).one()
    return int(row.anzahl)


async def ensure_price_rows(
    session: AsyncSession,
    household_id: UUID,
    observations: list[ParsedObservation],
) -> int:
    """Legt fuer Zutaten ohne Preiszeile eine an - abgeleitet, ohne Betrag.

    Warum ueberhaupt: die Zuordnung Zutat -> Preis laeuft ueber den Namen. Eine
    Sichtung fuer "Haferflocken" nutzt niemandem, solange es in
    ingredient_prices keine Zeile dieses Namens gibt, die der Rechenkern findet.

    Der Betrag wird hier NICHT gesetzt (0 als Platzhalter, price_is_stale), er
    kommt aus refresh_valuation_prices unmittelbar danach. Damit gibt es genau
    eine Stelle, an der ein Bewertungspreis entsteht."""
    seen: dict[str, ParsedObservation] = {}
    for observation in observations:
        seen.setdefault(observation.ingredient_key, observation)
    if not seen:
        return 0

    created = 0
    for key, observation in seen.items():
        result = await session.execute(
            text(
                "insert into ingredient_prices ("
                "  household_id, name, price_minor, pack_quantity, pack_unit, "
                "  price_source, price_is_stale"
                ") select :household_id, :name, 0, :pack_quantity, :pack_unit, "
                "  'derived', true "
                "where not exists ("
                "  select 1 from ingredient_prices where household_id = :household_id "
                "  and lower(btrim(name)) = :key"
                ") returning id"
            ),
            {
                "household_id": str(household_id),
                "name": observation.ingredient_name,
                "key": key,
                "pack_quantity": observation.pack_quantity,
                "pack_unit": observation.pack_unit,
            },
        )
        if result.one_or_none() is not None:
            created += 1
    return created
