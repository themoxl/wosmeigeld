"""Kostenschaetzung aus der Zutaten-Preisliste.

Der Preis haengt an der Zutat, nicht am Gericht. Ein Gericht kostet, was seine
Zutaten kosten - dieselbe Zutat also ueberall gleich viel, und ein spaeter
nachgetragener Preis wirkt sofort in jedem Rezept, das sie verwendet.

Rechenweg je Zutat:

    Grundpreis = price_minor / (pack_quantity in Basiseinheit)
    Kosten     = Rezeptmenge in derselben Basiseinheit * Grundpreis

Beispiel: "Nudeln, 1,99 EUR fuer 1 kg" ergibt 0,199 Cent je Gramm; ein Rezept
mit 500 g kostet damit 99,5 -> 100 Cent (kaufmaennisch gerundet, siehe
_round_minor).

Was hier BEWUSST nicht passiert: raten. Passen die Einheiten nicht zueinander
("1 Pkg" im Rezept gegen "400 g" in der Preisliste), wird die Zutat als
unbewertet gemeldet, statt eine Zahl zu erfinden. Eine Schaetzung, die still
auf falschen Annahmen beruht, ist schlechter als eine sichtbare Luecke.
"""

import re
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import grocery_unit_defaults

# Einheiten, die sich ineinander umrechnen lassen, samt Faktor auf die
# Basiseinheit ihrer Familie (Masse -> g, Volumen -> ml).
_MASS_TO_GRAM: Final[dict[str, Decimal]] = {
    "g": Decimal(1),
    "gr": Decimal(1),
    "gramm": Decimal(1),
    "kg": Decimal(1000),
    "kilo": Decimal(1000),
    "kilogramm": Decimal(1000),
}
_VOLUME_TO_MILLILITRE: Final[dict[str, Decimal]] = {
    "ml": Decimal(1),
    "milliliter": Decimal(1),
    "cl": Decimal(10),
    "l": Decimal(1000),
    "liter": Decimal(1000),
}

# Schreibweisen, die dasselbe meinen. Alles andere bleibt so stehen, wie es
# eingegeben wurde, und passt dann nur auf sich selbst - "Dose" gegen "Bund"
# gleichzusetzen waere schlicht falsch.
_UNIT_SYNONYMS: Final[dict[str, str]] = {
    "stück": "stk",
    "stueck": "stk",
    "st": "stk",
    "stk.": "stk",
    "packung": "pkg",
    "paket": "pkg",
    "pck": "pkg",
    "pkg.": "pkg",
    "dosen": "dose",
    "gläser": "glas",
    "glaeser": "glas",
    "bunde": "bund",
    "el": "el",
    "tl": "tl",
    # Mehrzahl und ausgeschriebene Formen. Ohne sie stuenden "Zehe" und
    # "Zehen" als zwei Familien nebeneinander, und eine Umrechnung fuer die
    # eine liesse die andere weiter unbewertet.
    "esslöffel": "el",
    "eßlöffel": "el",
    "essloeffel": "el",
    "teelöffel": "tl",
    "teeloeffel": "tl",
    "zehen": "zehe",
    "scheiben": "scheibe",
    "scheibe.": "scheibe",
    "blätter": "blatt",
    "blaetter": "blatt",
    "stangen": "stange",
    "köpfe": "kopf",
    "koepfe": "kopf",
    "rollen": "rolle",
    "msp": "messerspitze",
    "prisen": "prise",
    "tassen": "tasse",
}


def normalize_unit(unit: str | None) -> str:
    """Einheit in Kleinschreibung ohne Rand, Synonyme vereinheitlicht. Eine
    fehlende Einheit gilt als 'stk' - "2 Zwiebeln" ohne Einheit meint Stueck."""
    raw = (unit or "").strip().lower()
    if not raw:
        return "stk"
    return _UNIT_SYNONYMS.get(raw, raw)


def to_base_amount(quantity: Decimal, unit: str | None) -> tuple[Decimal, str]:
    """Menge in ihre Basiseinheit umgerechnet, samt Familienkennung.

    Rueckgabe z. B. (Decimal("500"), "masse") oder (Decimal("2"), "stk").
    Schlaegt nie fehl - eine unbekannte Einheit bildet ihre eigene Familie und
    passt dann nur auf sich selbst.
    """
    normalized = normalize_unit(unit)
    if normalized in _MASS_TO_GRAM:
        return quantity * _MASS_TO_GRAM[normalized], "masse"
    if normalized in _VOLUME_TO_MILLILITRE:
        return quantity * _VOLUME_TO_MILLILITRE[normalized], "volumen"
    # Nicht umrechenbare Einheiten passen nur auf sich selbst.
    return quantity, normalized


_FAMILY_BY_BASE: Final[dict[str, str]] = {"g": "masse", "ml": "volumen"}


@dataclass(frozen=True)
class Conversion:
    """Die Haushaltseinheiten EINER Zutat, alle auf dieselbe Basis bezogen.

    ``base_unit`` ist 'g' oder 'ml' und gilt fuer die ganze Zutat. Das ist
    kein Detail, sondern der Kern der Konstruktion: gaebe es fuer dieselbe
    Zutat eine g- und eine ml-Zeile, entstuende die Kette
    "1 EL -> 15 ml -> 0,93 g", die bei zwei sich widersprechenden Zeilen still
    falsch aufloest. Eine Dichte ist deshalb keine eigene Sorte Eintrag,
    sondern schlicht die ml-Zeile einer Zutat mit Basis g.
    """

    base_unit: str
    # Normalisierte Einheit -> wieviel base_unit EINE davon ergibt.
    factors: dict[str, Decimal]


def to_base_amount_for(
    quantity: Decimal, unit: str | None, conversion: Conversion | None
) -> tuple[Decimal, str]:
    """Wie ``to_base_amount``, aber mit den Umrechnungen einer Zutat.

    Die eingebauten Faktoren gehen vor: 1 kg sind 1000 g, auch wenn jemand
    fuer diese Zutat etwas anderes gespeichert hat. Erst danach greift die
    Zutatentabelle - und nur sie kann Masse und Volumen ueberhaupt verbinden.
    """
    if conversion is None:
        return to_base_amount(quantity, unit)

    normalized = normalize_unit(unit)
    family = _FAMILY_BY_BASE[conversion.base_unit]
    own = _MASS_TO_GRAM if conversion.base_unit == "g" else _VOLUME_TO_MILLILITRE
    if normalized in own:
        return quantity * own[normalized], family
    factor = conversion.factors.get(normalized)
    if factor is not None:
        return quantity * factor, family
    # Keine Zeile fuer diese Einheit: der Konflikt bleibt sichtbar, statt
    # ueber eine fremde Zutat hinweggerechnet zu werden.
    return to_base_amount(quantity, unit)


def _round_minor(value: Decimal) -> int:
    """Kaufmaennisch auf ganze Cent. ROUND_HALF_UP statt Bankers Rounding,
    damit dieselbe Zutat nicht je nach Menge mal auf- und mal abgerundet wird."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class PriceEntry:
    name: str
    price_minor: int
    pack_quantity: Decimal
    pack_unit: str | None
    # Zeile, aus der der Preis stammt. Optional, weil der Rechenkern sie nicht
    # braucht - die Preisluecken-Ansicht schon: bei einem Einheitenkonflikt
    # muss sie genau diese Zeile zum Korrigieren anbieten koennen.
    id: UUID | None = None


@dataclass(frozen=True)
class CostResult:
    minor: int
    # True, wenn erst eine Umrechnung der Zutat die beiden Seiten zusammen-
    # gebracht hat. Die Oberflaeche setzt darauf ihr "ungefaehr"-Zeichen: die
    # Zahl beruht dann auf einem Stueckgewicht, nicht auf der Waage.
    used_conversion: bool


def cost_detail(
    quantity: Decimal | None,
    unit: str | None,
    price: PriceEntry | None,
    conversion: Conversion | None = None,
) -> CostResult | None:
    """Kosten einer Rezeptzutat, samt Angabe ob dafuer umgerechnet wurde."""
    if price is None or quantity is None:
        return None

    recipe_amount, recipe_family = to_base_amount_for(quantity, unit, conversion)
    pack_amount, pack_family = to_base_amount_for(price.pack_quantity, price.pack_unit, conversion)
    if recipe_family != pack_family or pack_amount <= 0:
        return None

    # Ohne Umrechnung passten die Familien schon vorher zusammen; passten sie
    # es nicht, hat genau diese Tabelle die Bruecke geschlagen.
    _, raw_recipe_family = to_base_amount(quantity, unit)
    _, raw_pack_family = to_base_amount(price.pack_quantity, price.pack_unit)
    return CostResult(
        minor=_round_minor(Decimal(price.price_minor) * recipe_amount / pack_amount),
        used_conversion=raw_recipe_family != raw_pack_family,
    )


def cost_for(
    quantity: Decimal | None,
    unit: str | None,
    price: PriceEntry | None,
    conversion: Conversion | None = None,
) -> int | None:
    """Kosten einer Rezeptzutat in Cent, oder None wenn nicht berechenbar.

    None hat drei legitime Ursachen, die der Aufrufer unterscheiden koennen
    muss: kein Preis hinterlegt, keine Menge angegeben ("Salz"), oder
    unvereinbare Einheiten.

    ``conversion`` ist ausdruecklich ein Argument und kein Nachschlagen in der
    Datenbank: die Funktion bleibt rein und ohne Haushaltsbezug, und der
    Aufrufer laedt die Umrechnungen einmal je Anfrage statt einmal je Zeile.
    Ohne Angabe rechnet sie exakt wie vorher.
    """
    result = cost_detail(quantity, unit, price, conversion)
    return None if result is None else result.minor


def format_amount(quantity: Decimal | None, unit: str | None) -> str:
    """Menge fuer Anzeigetexte: "750 g", "1,5 kg", "2 Stk". Ganze Zahlen ohne
    Nachkommastellen, sonst mit Komma."""
    if quantity is None:
        return "—"
    normalized = quantity.normalize()
    text_value = (
        f"{normalized:f}".rstrip("0").rstrip(".") if normalized % 1 else f"{int(normalized)}"
    )
    with_comma = text_value.replace(".", ",")
    return f"{with_comma} {unit}".strip() if unit else with_comma


@dataclass(frozen=True)
class PurchasePlan:
    """Was fuer einen Wochenbedarf tatsaechlich gekauft werden muss."""

    packs: int
    buy_quantity: Decimal
    buy_unit: str | None
    price_minor: int
    # True, wenn die Packungen den Bedarf genau treffen. Der Vergleich muss in
    # der Basiseinheit passieren: "1 kg" und "1000 g" sind DIESELBE Menge,
    # obwohl weder Zahl noch Einheit uebereinstimmen.
    covers_exactly: bool


def purchase_for(
    needed_quantity: Decimal | None,
    needed_unit: str | None,
    price: PriceEntry | None,
    conversion: Conversion | None = None,
) -> PurchasePlan | None:
    """Bedarf -> Einkauf in ganzen Packungen.

    Anders als ``cost_for`` wird hier AUFGERUNDET: eine Packung ist die
    kleinste kaufbare Einheit. Wer 750 g braucht, kauft 1 kg und zahlt 1 kg.

    Die beiden Funktionen beantworten bewusst verschiedene Fragen:
    ``cost_for`` sagt, was eine Mahlzeit wert ist (anteilig - die zweite
    Haelfte der Packung gehoert zur naechsten Mahlzeit), ``purchase_for`` sagt,
    was an der Kasse faellig wird. Beides in eine Zahl zu pressen waere fuer
    jeweils eine der beiden Ansichten falsch.

    Wichtig fuer den Aufrufer: erst den Wochenbedarf ueber ALLE Gerichte
    aufaddieren, dann diese Funktion aufrufen. Andersherum (je Gericht runden
    und dann summieren) kauft man 750 g + 250 g als zwei Kilo statt einem.
    """
    if price is None or needed_quantity is None:
        return None

    need_amount, need_family = to_base_amount_for(needed_quantity, needed_unit, conversion)
    pack_amount, pack_family = to_base_amount_for(price.pack_quantity, price.pack_unit, conversion)
    if need_family != pack_family or pack_amount <= 0:
        return None

    packs = max(1, int((need_amount / pack_amount).to_integral_value(rounding=ROUND_CEILING)))
    return PurchasePlan(
        packs=packs,
        buy_quantity=price.pack_quantity * packs,
        buy_unit=price.pack_unit,
        price_minor=price.price_minor * packs,
        covers_exactly=pack_amount * packs == need_amount,
    )


# ---------------------------------------------------------------------------
# Namensaehnlichkeit
# ---------------------------------------------------------------------------
# Die Zuordnung Zutat -> Preis laeuft ueber den exakten Namen. Sobald Preise
# aus einer Tabelle kommen, treffen die Schreibweisen fast nie: im Rezept
# steht "Stueckige Tomaten (Dose)", in der Preisliste "Tomaten, stueckig
# 400g". Das sieht dann aus wie "kein Preis hinterlegt", obwohl der Preis
# dasteht.
#
# Was hier entsteht, ist ausdruecklich ein VORSCHLAG. Automatisch zuordnen
# waere dieselbe stille Falschannahme, die cost_for bei unpassenden Einheiten
# verweigert - "Salz" und "Salzkartoffeln" fangen nun einmal gleich an.
# Uebernommen wird eine Zuordnung erst, wenn jemand sie bestaetigt.

# Klammerzusaetze sind Notizen zur Darreichung, kein Teil des Namens.
_MATCH_PARENTHESES: Final[re.Pattern[str]] = re.compile(r"\(.*?\)")
# Packungsangaben im Namen ("400g", "1,5 l", "10 Stk") sagen nichts darueber,
# ob es dieselbe Zutat ist - die Menge steht in eigenen Spalten.
_MATCH_PACK_SIZE: Final[re.Pattern[str]] = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:kg|g|ml|l|stk|stück|stueck|st)\b"
)
_MATCH_NON_WORD: Final[re.Pattern[str]] = re.compile(r"[^0-9a-zäöüß]+")

# Ab dieser Laenge gilt ein Wortanfang als Treffer ("stueckig" -> "stueckige").
# Kuerzer waere gefaehrlich: "Salz" ist der Anfang von "Salzkartoffeln".
_MIN_PREFIX_MATCH: Final[int] = 5
_TOKEN_THRESHOLD: Final[float] = 0.6
_RATIO_THRESHOLD: Final[float] = 0.8


def match_tokens(name: str) -> list[str]:
    """Zerlegt einen Zutatennamen in vergleichbare Wortbestandteile."""
    lowered = name.strip().lower()
    lowered = _MATCH_PARENTHESES.sub(" ", lowered)
    lowered = _MATCH_PACK_SIZE.sub(" ", lowered)
    return [token for token in _MATCH_NON_WORD.sub(" ", lowered).split() if len(token) >= 2]


def _tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= _MIN_PREFIX_MATCH and longer.startswith(shorter)


def _token_score(left: list[str], right: list[str]) -> float:
    """Wie stark sich zwei Namen ueberdecken, 0 bis 1.

    Beide Richtungen gemittelt: "Mehl" gegen "Mehl Type 405" deckt einseitig
    vollstaendig ab, ist aber trotzdem nur ein halber Treffer - genau diese
    Asymmetrie unterscheidet den Zusatz vom anderen Produkt.
    """
    if not left or not right:
        return 0.0
    hits_left = sum(1 for token in left if any(_tokens_match(token, other) for other in right))
    hits_right = sum(1 for token in right if any(_tokens_match(token, other) for other in left))
    return (hits_left / len(left) + hits_right / len(right)) / 2


def suggest_price_match(name: str, candidates: list[str]) -> str | None:
    """Bester Preislistenname zu einem Rezeptnamen, oder None.

    None ist der Normalfall und kein Mangel: lieber keine Zuordnung
    vorschlagen als eine falsche, die per Klick uebernommen wird.
    """
    tokens = match_tokens(name)
    normalized = " ".join(tokens)
    best: tuple[float, str] | None = None
    for candidate in candidates:
        candidate_tokens = match_tokens(candidate)
        token_score = _token_score(tokens, candidate_tokens)
        ratio = SequenceMatcher(None, normalized, " ".join(candidate_tokens)).ratio()
        if token_score < _TOKEN_THRESHOLD and ratio < _RATIO_THRESHOLD:
            continue
        score = max(token_score, ratio)
        if best is None or score > best[0]:
            best = (score, candidate)
    return None if best is None else best[1]


@dataclass(frozen=True)
class DishCost:
    """Kostenbild eines Gerichts, bezogen auf seine Standardportionen."""

    computed_cost_minor: int
    priced_ingredients: int
    # Zutaten mit Menge, aber ohne verwertbaren Preis - die Luecke in der
    # Schaetzung. Zutaten ohne Menge ("Salz") zaehlen hier NICHT mit, sie sind
    # absichtlich unbeziffert.
    unpriced_ingredients: int
    unquantified_ingredients: int

    @property
    def is_complete(self) -> bool:
        return self.priced_ingredients > 0 and self.unpriced_ingredients == 0


async def load_price_lookup(
    session: AsyncSession, household_id: UUID
) -> dict[str, PriceEntry]:
    """Preisliste des Haushalts, nach kleingeschriebenem Namen ansprechbar.

    Bestaetigte Zuordnungen sind hier bereits eingerechnet: eine abweichende
    Schreibweise steht als eigener Schluessel auf derselben Preiszeile. So
    wirkt eine einmal bestaetigte Zuordnung ueberall, wo Preise gelesen werden
    (Gerichtskosten, Zutatenzeile, Einkaufsliste), ohne dass jede dieser
    Stellen die Regel nachbauen muesste.
    """
    rows = (
        await session.execute(
            text(
                "select id, name, price_minor, pack_quantity, pack_unit from ingredient_prices "
                "where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    lookup = {
        row.name.strip().lower(): PriceEntry(
            id=row.id,
            name=row.name,
            price_minor=row.price_minor,
            pack_quantity=Decimal(row.pack_quantity),
            pack_unit=row.pack_unit,
        )
        for row in rows
    }

    alias_rows = (
        await session.execute(
            text(
                "select alias_key, price_key from ingredient_price_aliases "
                "where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    for alias in alias_rows:
        # Ein direkter Namenstreffer geht vor. Die Zuordnung ist die Ausnahme
        # fuer abweichende Schreibweisen - sobald es die Zutat unter ihrem
        # eigenen Namen gibt, ist das die genauere Angabe.
        if alias.alias_key in lookup:
            continue
        entry = lookup.get(alias.price_key)
        if entry is not None:
            lookup[alias.alias_key] = entry
    return lookup


async def load_conversions(
    session: AsyncSession, household_id: UUID
) -> dict[str, Conversion]:
    """Umrechnungen des Haushalts, nach kleingeschriebenem Zutatennamen.

    Bestaetigte Schreibweisen sind eingerechnet, genau wie bei der Preisliste:
    steht die Umrechnung unter "Knoblauch" und das Rezept sagt "Knobi", greift
    sie ueber die Zuordnung trotzdem. Ohne diese Faltung wirkte sie
    ausgerechnet dort nicht, wo ueberhaupt Zuordnungen noetig waren.
    """
    rows = (
        await session.execute(
            text(
                "select ingredient_key, unit, factor, base_unit "
                "from ingredient_unit_conversions where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    lookup: dict[str, Conversion] = {}
    for row in rows:
        entry = lookup.get(row.ingredient_key)
        if entry is None:
            entry = Conversion(base_unit=row.base_unit, factors={})
            lookup[row.ingredient_key] = entry
        # Eine abweichende zweite Basis weist der Dienst beim Anlegen ab; kommt
        # sie trotzdem vor (Handeingriff in der Datenbank), gewinnt die zuerst
        # gelesene Zeile, statt eine Kette aufzubauen.
        if row.base_unit == entry.base_unit:
            entry.factors[row.unit] = Decimal(row.factor)

    alias_rows = (
        await session.execute(
            text(
                "select alias_key, price_key from ingredient_price_aliases "
                "where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    for alias in alias_rows:
        if alias.alias_key in lookup:
            continue
        entry = lookup.get(alias.price_key)
        if entry is not None:
            lookup[alias.alias_key] = entry
    return lookup


async def load_dish_costs(
    session: AsyncSession, household_id: UUID
) -> dict[UUID, DishCost]:
    """Berechnete Kosten je Gericht des Haushalts.

    Bewusst zwei Abfragen und die Rechnung in Python statt einer SQL-Akrobatik
    mit Einheitenumrechnung im Statement: die Umrechnungstabellen stehen oben
    im Klartext und sind dort auch testbar.
    """
    prices = await load_price_lookup(session, household_id)
    conversions = await load_conversions(session, household_id)
    rows = (
        await session.execute(
            text(
                "select dish_id, name, quantity, unit from dish_ingredients "
                "where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    totals: dict[UUID, list[int]] = {}
    for row in rows:
        bucket = totals.setdefault(row.dish_id, [0, 0, 0, 0])
        if row.quantity is None:
            bucket[3] += 1
            continue
        key = row.name.strip().lower()
        cost = cost_for(Decimal(row.quantity), row.unit, prices.get(key), conversions.get(key))
        if cost is None:
            bucket[2] += 1
            continue
        bucket[0] += cost
        bucket[1] += 1

    return {
        dish_id: DishCost(
            computed_cost_minor=values[0],
            priced_ingredients=values[1],
            unpriced_ingredients=values[2],
            unquantified_ingredients=values[3],
        )
        for dish_id, values in totals.items()
    }


# ---------------------------------------------------------------------------
# Preisluecken
# ---------------------------------------------------------------------------
# Dieselbe Information wie DishCost.unpriced_ingredients, nur andersherum
# gruppiert: nach ZUTAT statt nach Gericht. Der Unterschied ist der
# Arbeitsablauf. "Hackfleisch" fehlt in Chili und in der Hackfleischsosse -
# einmal bepreist, rechnen beide. Eine nach Gerichten sortierte Liste liesse
# dieselbe Zutat mehrfach anfassen und waere trotzdem nicht schneller fertig.


@dataclass(frozen=True)
class PriceGapDish:
    """Ein Gericht, das an dieser Luecke haengt."""

    dish_id: UUID
    dish_name: str
    quantity: Decimal
    unit: str | None


@dataclass(frozen=True)
class SuggestedConversion:
    """Nachgeschlagener Vorschlag fuer die fehlende Umrechnung einer Luecke."""

    unit: str
    factor: Decimal
    base_unit: str
    confidence: str


def _metric_base(normalized_unit: str) -> grocery_unit_defaults.BaseUnit | None:
    """'g' oder 'ml', wenn die Einheit selbst schon metrisch ist."""
    if normalized_unit in _MASS_TO_GRAM:
        return "g"
    if normalized_unit in _VOLUME_TO_MILLILITRE:
        return "ml"
    return None


def suggest_conversion(
    ingredient_key: str, recipe_unit: str | None, pack_unit: str | None
) -> SuggestedConversion | None:
    """Welche Umrechnung diese Luecke schliessen wuerde, aus der Werteliste.

    Die Basiseinheit kommt von der Seite, die bereits metrisch ist - das ist
    die Seite, an der nicht gerechnet werden muss. Stehen beide metrisch, aber
    in verschiedenen Familien (9 g Sesamoel gegen 250 ml Flasche), ist die
    gesuchte Zahl die Dichte, und die Basis kommt vom Rezept.
    """
    recipe_norm = normalize_unit(recipe_unit)
    pack_norm = normalize_unit(pack_unit)
    recipe_base = _metric_base(recipe_norm)
    pack_base = _metric_base(pack_norm)

    if pack_base is not None and recipe_base is None:
        base, unit = pack_base, recipe_norm
    elif recipe_base is not None and pack_base is None:
        base, unit = recipe_base, pack_norm
    elif recipe_base is not None and pack_base is not None and recipe_base != pack_base:
        base, unit = recipe_base, pack_norm
    else:
        # Beide Seiten zaehlbar ("1 Pkg" gegen "2 Stk") - dazwischen gibt es
        # keine allgemeingueltige Zahl, nur eine Entscheidung des Haushalts.
        return None

    default = grocery_unit_defaults.suggest_default(ingredient_key, unit, base)
    if default is None:
        return None
    return SuggestedConversion(
        unit=default.unit,
        factor=default.factor,
        base_unit=default.base_unit,
        confidence=default.confidence,
    )


@dataclass(frozen=True)
class PriceGap:
    key: str
    name: str
    # 'no_price': die Zutat steht nirgends in der Preisliste - anlegen.
    # 'unit_mismatch': der Preis ist da, nur in einer anderen Einheitenfamilie
    # - eine der beiden Seiten korrigieren. Zwei verschiedene Handgriffe, die
    # als eine Zeile "fehlt" nicht unterscheidbar waeren.
    kind: str
    dishes: tuple[PriceGapDish, ...]
    # Wie oft die betroffenen Gerichte bereits eingeplant wurden. Zusammen mit
    # der Gerichtezahl der Hebel: was oben steht, bringt am meisten.
    planned_count: int
    # Haeufigste Einheit in den Rezepten. Fuellt das Einheitenfeld beim
    # Anlegen vor - damit entsteht der Einheitenkonflikt gar nicht erst.
    recipe_unit: str | None
    price_id: UUID | None
    price_name: str | None
    price_minor: int | None
    price_pack_quantity: Decimal | None
    price_pack_unit: str | None
    suggested_price_name: str | None
    # Nachgeschlagene Umrechnung, die genau diese Luecke schliessen wuerde.
    # None heisst: dafuer gibt es keine allgemeine Zahl - hier muss der
    # Haushalt selbst entscheiden.
    suggested_conversion: SuggestedConversion | None


async def load_price_gaps(session: AsyncSession, household_id: UUID) -> list[PriceGap]:
    """Alle Zutaten mit Menge, aber ohne verwertbaren Preis - nach Hebel sortiert."""
    prices = await load_price_lookup(session, household_id)
    conversions = await load_conversions(session, household_id)
    rows = (
        await session.execute(
            text(
                "select i.dish_id, d.name as dish_name, i.name, i.quantity, i.unit, "
                "(select count(*) from meals m where m.dish_id = d.id)::int as planned_count "
                "from dish_ingredients i join dishes d on d.id = i.dish_id "
                "where i.household_id = :household_id and i.quantity is not null "
                "order by lower(i.name) asc, lower(d.name) asc"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    # Vorschlaege nur gegen Namen, die es wirklich als Preiszeile gibt -
    # bereits zugeordnete Schreibweisen stehen ebenfalls im Lookup und waeren
    # als Vorschlag sinnlos.
    price_names = sorted({entry.name for entry in prices.values()})

    grouped: dict[str, list[tuple[Any, PriceEntry | None]]] = {}
    for row in rows:
        key = row.name.strip().lower()
        price = prices.get(key)
        if cost_for(Decimal(row.quantity), row.unit, price, conversions.get(key)) is not None:
            continue
        grouped.setdefault(key, []).append((row, price))

    gaps: list[PriceGap] = []
    for key, entries in grouped.items():
        rows_only = [row for row, _ in entries]
        price = entries[0][1]
        # Dieselbe Zutat kann in zwei Gerichten verschieden geschrieben sein
        # (Gross-/Kleinschreibung, Leerzeichen). Die haeufigste Schreibweise
        # gewinnt, damit die Zeile so aussieht wie im Rezept.
        display_name = Counter(row.name.strip() for row in rows_only).most_common(1)[0][0]
        units = Counter(row.unit for row in rows_only if row.unit)
        by_dish = {row.dish_id: row for row in rows_only}
        recipe_unit = units.most_common(1)[0][0] if units else None
        gaps.append(
            PriceGap(
                key=key,
                name=display_name,
                kind="unit_mismatch" if price is not None else "no_price",
                dishes=tuple(
                    PriceGapDish(
                        dish_id=row.dish_id,
                        dish_name=row.dish_name,
                        quantity=Decimal(row.quantity),
                        unit=row.unit,
                    )
                    for row in by_dish.values()
                ),
                planned_count=sum(row.planned_count for row in by_dish.values()),
                recipe_unit=recipe_unit,
                price_id=price.id if price else None,
                price_name=price.name if price else None,
                price_minor=price.price_minor if price else None,
                price_pack_quantity=price.pack_quantity if price else None,
                price_pack_unit=price.pack_unit if price else None,
                suggested_price_name=(
                    suggest_price_match(display_name, price_names) if price is None else None
                ),
                # Nur bei vorhandenem Preis: ohne Preiszeile ist nicht die
                # Umrechnung das Problem, sondern der fehlende Preis.
                suggested_conversion=(
                    suggest_conversion(key, recipe_unit, price.pack_unit)
                    if price is not None
                    else None
                ),
            )
        )

    # Hebel zuerst: was in den meisten Gerichten steckt und am haeufigsten
    # geplant war, bringt beim Nachtragen am meisten Abdeckung.
    gaps.sort(key=lambda gap: (-len(gap.dishes), -gap.planned_count, gap.name.lower()))
    return gaps
