"""Nachgeschlagene Umrechnungen fuer Haushaltseinheiten.

Ein Rezept sagt "4 Zehen Knoblauch", die Preisliste sagt "100 g". Damit die
Kostenschaetzung beides zusammenbringt, braucht es eine Zahl dazwischen. Diese
Datei haelt die nachgeschlagenen Werte - und zwar ausdruecklich als VORSCHLAG.

Der Rechenkern benutzt sie NIE von sich aus. Er rechnet ausschliesslich mit
Umrechnungen, die der Haushalt gespeichert hat (Tabelle
``ingredient_unit_conversions``). Diese Liste fuellt nur den Knopf "1 Zehe =
5 g uebernehmen". Der Grund steht im Kopf von ``grocery_pricing_service``:
eine still angenommene Zahl ist schlechter als eine sichtbare Luecke. Dazu
kommt ein praktischer: wuerde die Liste still greifen, verschoebe eine spaetere
Korrektur hier rueckwirkend die Kosten jedes Haushalts, ohne dass jemand etwas
getan haette.

``confidence`` sagt, wie belastbar der Wert ist:

* ``belegt``     - mehrere uebereinstimmende Quellen
* ``plausibel``  - Quellen streuen, der Wert ist der uebliche Mittelwert
* ``grob``       - Groessenordnung; taugt zum Schaetzen, nicht zum Nachwiegen

Nur ``belegt`` und ``plausibel`` gehen in die Sammeluebernahme ein. Bei ``grob``
soll jemand hinschauen, bevor er die Zahl uebernimmt.

Quellen der Loeffel- und Stueckgewichte: Koch-Wiki "Zubereitung:Masse und
Gewichte", Dr. Oetker "Mengenangaben beim Backen umrechnen", gesundesessen.org
(Stueckgewichte Gemuese), grammmlrechner.de und koch-piraten.de (Dichten von
Speiseoelen), Herstellerangaben fuer Packungsgroessen.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

BaseUnit = Literal["g", "ml"]
Confidence = Literal["belegt", "plausibel", "grob"]


@dataclass(frozen=True)
class UnitDefault:
    """Eine nachgeschlagene Umrechnung: 1 ``unit`` = ``factor`` ``base_unit``."""

    unit: str
    factor: Decimal
    base_unit: BaseUnit
    confidence: Confidence


def _d(value: str) -> Decimal:
    return Decimal(value)


# ---------------------------------------------------------------------------
# Zutatenunabhaengige Masse
# ---------------------------------------------------------------------------
# Ein Teeloeffel fasst ueberall 5 ml. In GRAMM wiegt derselbe Loeffel je nach
# Zutat 1 g (Oregano) bis 6 g (Salz) - deshalb stehen hier ausschliesslich
# Volumenwerte. Sie greifen also nur bei Zutaten, deren Preis in ml oder l
# steht; fuer alles andere braucht es die zutateneigene Zeile weiter unten.
GENERIC_DEFAULTS: Final[tuple[UnitDefault, ...]] = (
    UnitDefault("tl", _d("5"), "ml", "belegt"),
    UnitDefault("el", _d("15"), "ml", "belegt"),
    UnitDefault("tasse", _d("150"), "ml", "plausibel"),
    UnitDefault("schuss", _d("5"), "ml", "grob"),
)


def _entries(*items: tuple[str, str, BaseUnit, Confidence]) -> tuple[UnitDefault, ...]:
    return tuple(UnitDefault(unit, _d(factor), base, conf) for unit, factor, base, conf in items)


# ---------------------------------------------------------------------------
# Je Zutat
# ---------------------------------------------------------------------------
# Schluessel ist der kleingeschriebene Zutatenname ohne Rand - dieselbe Form,
# die auch die Preiszuordnung benutzt. Steht eine Zutat nicht hier, gibt es
# keinen Vorschlag; die Luecke bleibt sichtbar, statt geraten zu werden.
#
# Die Basiseinheit ist je Zutat FEST: entweder g oder ml. Sonst entstuende die
# Kette "1 EL -> 15 ml -> 0,93 g", die bei zwei sich widersprechenden Zeilen
# still falsch aufloest. Eine Dichte ist damit einfach die ml-Zeile einer
# Zutat, deren Basis g ist ("1 ml Sesamoel = 0,93 g").
INGREDIENT_DEFAULTS: Final[dict[str, tuple[UnitDefault, ...]]] = {
    # -- Gemuese und Obst, in Stueck gerechnet ------------------------------
    "knoblauch": _entries(("zehe", "5", "g", "plausibel"), ("stk", "5", "g", "plausibel")),
    "zwiebeln": _entries(("stk", "80", "g", "belegt")),
    "rote zwiebeln": _entries(("stk", "80", "g", "plausibel")),
    "frühlingszwiebeln": _entries(("stk", "19", "g", "belegt"), ("bund", "100", "g", "plausibel")),
    "zitronen": _entries(("stk", "100", "g", "plausibel")),
    "tomaten": _entries(("stk", "100", "g", "belegt")),
    "paprika": _entries(("stk", "155", "g", "belegt")),
    "chili": _entries(("stk", "10", "g", "plausibel")),
    "chili getrocknet": _entries(("stk", "1", "g", "plausibel")),
    "kashmiri-chilis getrocknet": _entries(("stk", "2", "g", "grob")),
    "brokkoli": _entries(("stk", "350", "g", "belegt")),
    "mangold": _entries(("stk", "400", "g", "plausibel"), ("bund", "400", "g", "plausibel")),
    "ingwer": _entries(("stk", "20", "g", "grob")),
    "tomaten getrocknet in öl": _entries(("stk", "3", "g", "belegt")),
    # -- Kraeuter, in Bund gerechnet ---------------------------------------
    "koriander frisch": _entries(("bund", "25", "g", "plausibel")),
    "basilikum frisch": _entries(("bund", "30", "g", "plausibel")),
    "dill": _entries(("bund", "25", "g", "plausibel")),
    "petersilie": _entries(("bund", "55", "g", "plausibel")),
    "suppengrün": _entries(("bund", "400", "g", "plausibel")),
    "curryblätter": _entries(("handvoll", "2", "g", "plausibel"), ("stk", "0.1", "g", "grob")),
    # -- Ganze Gewuerze, in Stueck gerechnet --------------------------------
    "nelken": _entries(("stk", "0.2", "g", "grob")),
    "zimtstange": _entries(("stk", "4", "g", "plausibel")),
    "kardamom": _entries(("stk", "0.2", "g", "grob")),
    "lorbeerblätter": _entries(("stk", "0.2", "g", "grob")),
    # -- Gemahlene Gewuerze, in Loeffeln gerechnet --------------------------
    "kreuzkümmel": _entries(("tl", "2", "g", "belegt"), ("el", "6", "g", "belegt")),
    "kurkuma": _entries(("tl", "2.5", "g", "plausibel"), ("el", "7.5", "g", "plausibel")),
    "paprikapulver": _entries(("tl", "2.3", "g", "belegt"), ("el", "7", "g", "belegt")),
    "paprikapulver geräuchert": _entries(
        ("tl", "2.3", "g", "plausibel"), ("el", "7", "g", "plausibel")
    ),
    "chilipulver": _entries(("tl", "2.3", "g", "plausibel"), ("el", "7", "g", "plausibel")),
    "chiliflocken": _entries(("tl", "1.8", "g", "belegt"), ("el", "5", "g", "belegt")),
    "koriander gemahlen": _entries(("tl", "2", "g", "belegt"), ("el", "6", "g", "belegt")),
    "koriandersamen": _entries(("tl", "1.8", "g", "belegt"), ("el", "5", "g", "belegt")),
    "garam masala": _entries(("tl", "2.5", "g", "plausibel"), ("el", "7.5", "g", "plausibel")),
    "currypulver": _entries(("tl", "2.2", "g", "belegt"), ("el", "6.5", "g", "belegt")),
    "muskatnuss": _entries(("tl", "2.2", "g", "belegt")),
    "zimt": _entries(("tl", "2.4", "g", "belegt"), ("el", "7.2", "g", "plausibel")),
    "oregano": _entries(("tl", "1", "g", "plausibel"), ("el", "3", "g", "plausibel")),
    "thymian getrocknet": _entries(("tl", "1.4", "g", "belegt")),
    "pfeffer gemahlen": _entries(("tl", "2.4", "g", "belegt")),
    "pfefferkörner schwarz": _entries(("tl", "2.7", "g", "belegt")),
    "senfkörner schwarz": _entries(("tl", "3.8", "g", "plausibel")),
    "berbere gewürzmischung": _entries(("tl", "2.5", "g", "grob"), ("el", "7.5", "g", "grob")),
    "bockshornkleeblätter": _entries(("tl", "0.5", "g", "belegt"), ("el", "1.6", "g", "belegt")),
    "bockshornkleesamen": _entries(("tl", "4", "g", "plausibel")),
    "salz": _entries(("tl", "6", "g", "belegt"), ("el", "17", "g", "belegt")),
    # -- Trockenes, in Loeffeln gerechnet -----------------------------------
    "zucker braun": _entries(("tl", "4", "g", "belegt"), ("el", "12", "g", "plausibel")),
    "sesam": _entries(("tl", "3", "g", "plausibel"), ("el", "9", "g", "belegt")),
    "hefeflocken": _entries(("tl", "1.7", "g", "grob"), ("el", "5", "g", "grob")),
    "maisstärke": _entries(("tl", "3", "g", "belegt"), ("el", "9", "g", "belegt")),
    "kartoffelstärke": _entries(("tl", "3", "g", "belegt"), ("el", "9", "g", "belegt")),
    # -- Fluessiges: Basis ml, wenn der Preis in l steht ---------------------
    "rapsöl": _entries(("el", "15", "ml", "belegt"), ("tl", "5", "ml", "belegt")),
    "olivenöl": _entries(("el", "15", "ml", "belegt"), ("tl", "5", "ml", "belegt")),
    # -- Fluessiges: Basis g, weil der Preis in g oder ml steht --------------
    # Die ml-Zeile ist die Dichte - derselbe Satz, nur mit ml als Einheit.
    "sesamöl": _entries(
        ("ml", "0.93", "g", "plausibel"),
        ("el", "14", "g", "belegt"),
        ("tl", "4.7", "g", "plausibel"),
    ),
    "sojasauce": _entries(("ml", "1.11", "g", "plausibel"), ("el", "16", "g", "plausibel")),
    "reisessig": _entries(("ml", "1.02", "g", "plausibel"), ("el", "15", "g", "plausibel")),
    "apfelessig": _entries(("ml", "1.01", "g", "belegt"), ("el", "15", "g", "belegt")),
    "sriracha": _entries(("ml", "1.11", "g", "plausibel"), ("el", "17", "g", "plausibel")),
    "worcestersauce": _entries(("ml", "1.15", "g", "plausibel"), ("el", "17", "g", "plausibel")),
    "ahornsirup": _entries(("ml", "1.33", "g", "belegt"), ("el", "20", "g", "belegt")),
    "tahini": _entries(("ml", "1.13", "g", "plausibel"), ("el", "15", "g", "belegt")),
    "tomatenmark": _entries(("ml", "1.1", "g", "plausibel"), ("el", "15", "g", "belegt")),
    "senf": _entries(("ml", "1.05", "g", "grob"), ("el", "15", "g", "belegt")),
    "tamarindenpaste": _entries(("ml", "1.2", "g", "grob"), ("el", "15", "g", "grob")),
    "butter vegan": _entries(("el", "15", "g", "plausibel"), ("pkg", "250", "g", "belegt")),
    "butter": _entries(("el", "15", "g", "plausibel"), ("pkg", "250", "g", "belegt")),
    # -- Fertigware, in Stueck oder Packung gerechnet ------------------------
    "brot": _entries(("scheibe", "40", "g", "plausibel")),
    "toastbrot": _entries(("scheibe", "25", "g", "plausibel")),
    "mischbrot": _entries(("scheibe", "40", "g", "plausibel")),
    "vollkornbrot": _entries(("scheibe", "45", "g", "plausibel")),
    "tortilla wraps groß": _entries(("stk", "62", "g", "belegt")),
    "tortilla wraps klein": _entries(("stk", "41", "g", "belegt")),
    "wiener würstchen": _entries(("stk", "50", "g", "plausibel")),
    "schweineschnitzel": _entries(("stk", "150", "g", "plausibel")),
    "salami": _entries(("pkg", "80", "g", "belegt")),
    "käse": _entries(("scheibe", "25", "g", "plausibel"), ("pkg", "200", "g", "plausibel")),
    "gouda": _entries(("scheibe", "25", "g", "plausibel")),
    "pizzateig (rolle)": _entries(("stk", "400", "g", "belegt"), ("rolle", "400", "g", "belegt")),
    "paprika chunks vegan": _entries(("pkg", "200", "g", "plausibel")),
}

# Bewusst NICHT enthalten: Gemuesebruehe. "360 ml fertige Bruehe" gegen "135 g
# Pulver" ist keine Einheit, sondern eine Ergiebigkeit - wie stark jemand seine
# Bruehe ansetzt, ist eine Rezeptannahme und darf nicht mitgeliefert werden.
# Wer sie fuer seinen Haushalt eintraegt, trifft eine sichtbare Entscheidung.


def defaults_for(ingredient_key: str) -> tuple[UnitDefault, ...]:
    """Alle nachgeschlagenen Umrechnungen einer Zutat, ggf. leer."""
    return INGREDIENT_DEFAULTS.get(ingredient_key, ())


def suggest_default(
    ingredient_key: str, unit: str, base_unit: BaseUnit
) -> UnitDefault | None:
    """Vorschlag fuer (Zutat, Einheit) in der gesuchten Basiseinheit.

    Die zutateneigene Zeile geht vor: "1 EL Sesamoel = 14 g" ist genauer als
    das allgemeine "1 EL = 15 ml", und beide gleichzeitig gelten zu lassen
    waere die Kette, die dieses Modell gerade vermeidet.
    """
    for entry in defaults_for(ingredient_key):
        if entry.unit == unit and entry.base_unit == base_unit:
            return entry
    for entry in GENERIC_DEFAULTS:
        if entry.unit == unit and entry.base_unit == base_unit:
            return entry
    return None
