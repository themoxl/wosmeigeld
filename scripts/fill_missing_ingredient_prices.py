"""Fehlende Zutatenpreise aus dem Scrape-Export (latest-canonical.csv) mit
ECHTEN Ladenpreisen befuellen und als SQL ausgeben.

Ausgangslage: der Gerichte-Katalog enthaelt Zutaten, fuer die es in
ingredient_prices keine Zeile gibt (veganes Sortiment, Asia-Zutaten, Gewuerze
jenseits der Standards). Ohne Preis faellt die Zutat in der Wochenschaetzung
still unter den Tisch.

Warum nicht geschaetzt: ein erfundener Richtwert sieht in der Oberflaeche
genauso aus wie ein echter und wandert in jede Wochensumme. Die Datei, aus der
auch vorhandene Preise stammen,
enthaelt echte Preise deutscher Ketten - die werden hier weiterverwendet.

Die Ausgabe wird als SQL-Datei erzeugt und kann auf dem Zielserver mit
psql eingespielt werden.

Aufruf:
    python scripts/fill_missing_ingredient_prices.py ^
        --csv "private/latest-canonical.csv" ^
        --namen scripts/_fehlende_zutaten.txt ^
        --haushalt HOUSEHOLD_UUID ^
        --sql-out scripts/_zutatenpreise.sql

    --bericht schreibt zusaetzlich eine Liste "Zutat -> gewaehltes Produkt",
    damit vor dem Einspielen sichtbar ist, WORAUF jeder Preis beruht.

Auswahlregel je Zutat:
  1. Nur Zeilen der deutschen Ketten (reweDe/dmDe/muellerDe).
  2. Treffer heisst: alle Wortstaemme des Zutatennamens kommen im Produktnamen
     vor (dieselbe Heuristik wie im vorhandenen Importskript). Fuer Zutaten,
     deren Alltagsname nicht im Produktnamen steht ("Sahne vegan"), gibt es
     unten SUCHBEGRIFFE.
  3. Je Produkt zaehlt die juengste Sichtung.
  4. Bio-Produkte fliegen raus, solange es normale gibt - sonst zoege ein
     Bio-Zufallstreffer den Preis der ganzen Zutat hoch.
  5. Aus den verbleibenden Produkten gewinnt der MEDIAN, nicht das billigste:
     das billigste Suchergebnis ist bei einer Wortsuche regelmaessig eine
     Fehlzuordnung (Miniportion, Zubehoer), der Median haelt dagegen.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Deutsche Ketten in der Datei. Die oesterreichischen (billa/spar/hofer/...)
# bleiben aussen vor, ihre Preise gelten hier nicht.
DEFAULT_STORES = ("reweDe", "dmDe", "muellerDe")

_STOPWORDS = {
    "und", "in", "im", "am", "zu", "aus", "auf", "mit", "fuer", "für", "the",
    "type", "je", "der", "die", "das",
}
_STEM_OVERRIDES = {
    "äpfel": "apfel", "nüsse": "nuss", "kräuter": "kraut", "eier": "ei",
    "bohnen": "bohne", "gurken": "gurke",
}
_PAREN_RE = re.compile(r"\([^)]*\)")
_PERCENT_RE = re.compile(r"\d+([.,]\d+)?\s*%")
_NON_WORD_RE = re.compile(r"[^a-zA-ZäöüÄÖÜß]+")

# Zutaten, deren Name im Regal anders lautet. Links steht der Name aus dem
# Rezept, rechts der Text, mit dem gesucht wird. Bewusst von Hand gepflegt:
# eine automatische Umschreibung waere genau die stille Falschannahme, die das
# Preismodul an keiner Stelle trifft.
SUCHBEGRIFFE = {
    "Sahne vegan": "hafer cuisine",
    "Butter vegan": "vegane margarine",
    "Joghurt vegan": "sojajoghurt",
    "Streukäse vegan": "veganer reibegenuss",
    "Frischkäse vegan": "simply frischcreme",
    "Tofu natur": "tofu natur",
    "Paprika Chunks vegan": "vegane chunks",
    "Teigtaschen TK vegan": "gyoza",
    "Paprikapulver geräuchert": "paprika geraeuchert",
    "Pfefferkörner schwarz": "pfefferkoerner schwarz",
    "Senfkörner schwarz": "senfkoerner",
    "Chili getrocknet": "chilischoten",
    "Kashmiri-Chilis getrocknet": "chilischoten",
    "Zimtstange": "zimtstangen",
    "Nelken": "gewuerznelken",
    "Bockshornkleesamen": "bockshornkleesaat",
    "Bockshornkleeblätter": "bockshornkleesaat",
    "Massaman-Currypaste": "currypaste",
    "Miso hell": "miso paste",
    "Udon-Nudeln": "udon nudeln",
    "Tomaten getrocknet in Öl": "getrocknete tomaten",
    "Zucker braun": "brauner zucker",
    "Zucker": "zucker",
    "Kardamom": "kardamom gemahlen",
    "Butter vegan": "alsan",
    "Joghurt vegan": "joghurtalternative",
    "Frischkäse vegan": "frischcreme",
    "Streukäse vegan": "vegan reibe",
    "Sesam": "sesam",
    "Limette": "limette stueck",
    "Tortilla Wraps groß": "tortilla wraps",
    "Tortilla Wraps klein": "tortilla wraps",
    "Koriander frisch": "koriander topf",
    "Koriander gemahlen": "koriander gemahlen",
    "Koriandersamen": "koriander gemahlen",
    "Gemüsebrühe": "gemuesebruehe",
    "Kartoffelstärke": "speisestaerke",
    "Sojagranulat": "soja granulat",
    "Schweineschnitzel": "schweineschnitzel",
    "Rote Zwiebeln": "zwiebeln rot",
    "Tamarindenpaste": "tamarinde",
    "Mehl": "weizenmehl",
    "Nudeln (z.B. Spaghetti oder Penne)": "spaghetti",
    "Röstzwiebeln": "roestzwiebeln",
    "Walnüsse": "walnusskerne",
    "Suppengrün": "suppengemuese",
    "Chilipulver": "chili gemahlen",
}

# Produktnamen, die ein Wort der Zutat enthalten, aber nie die Zutat selbst
# sind: Fertiggerichte, Gebaeck, Suessware. Ohne diese Liste wird aus "Zucker"
# eine Konfituere und aus "Sesam" ein Knaeckebrot - beides echte Treffer der
# Wortsuche und beides als Zutatenpreis unbrauchbar.
AUSSCHLUSS = {
    "terrine", "knusperbrot", "knaeckebrot", "ciabatta", "cracker", "riegel",
    "bonbon", "keks", "konfituere", "marmelade", "limonade", "limo", "brei",
    "fertiggericht", "wasa", "aufschnitt", "eiscreme", "schokolade", "praline",
    "kuchen", "muesli", "sandwich", "pizzabaguette", "toast", "chips",
    "aperitif", "likoer", "erfrischungsgetraenk", "lemonaid", "waffel",
    "waffelett", "knoedel", "onigiri", "sushi", "dekorieren", "duft",
    "black", "drink", "schorle", "energy", "tonic", "sirup", "schogetten",
    "schuettelbrot", "knaeck", "brotchips",
}

# Zutaten, die unter einem ANDEREN Namen laengst einen Preis haben ("Milch"
# neben "Vollmilch", "Brot" neben "Mischbrot"). Die brauchen keine zweite
# Preiszeile, sondern eine Schreibweisen-Zuordnung - das ist eine Entscheidung
# des Nutzers, nicht dieses Skripts. Ausserdem: zwei Tippfehler aus alten
# Gerichten, fuer die eine Preiszeile die Schreibweise nur festschreiben
# wuerde.
VARIANTEN = {
    "Milch", "Brot", "Mais (Dose)", "Chilliflocken", "Kirchererbensmehl",
}

# Umlaut-unempfindlich vergleichen: im Scrape stehen Produktnamen mal mit,
# mal ohne Umlaut ("Gemuesebruehe" neben "Gemüsebrühe").
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _stem(word: str) -> str:
    if word in _STEM_OVERRIDES:
        return _STEM_OVERRIDES[word]
    for suffix in ("en", "er", "n", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def canonical_stems(name: str) -> frozenset[str]:
    cleaned = _PERCENT_RE.sub(" ", _PAREN_RE.sub(" ", name))
    words = [w.lower().translate(_UMLAUTE) for w in _NON_WORD_RE.split(cleaned) if len(w) >= 3]
    words = [w for w in words if w not in _STOPWORDS]
    return frozenset(_stem(w) for w in words)


def fix_mojibake(value: str) -> str:
    try:
        return value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


@dataclass
class Sichtung:
    produkt: str
    preis: float
    menge: float | None
    einheit: str | None
    stand: date
    laden: str
    bio: bool
    staemme: frozenset[str]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True, help="Pfad zur latest-canonical.csv")
    p.add_argument("--namen", required=True, help="Textdatei, eine Zutat je Zeile")
    p.add_argument("--haushalt", required=True, help="Ziel-Haushalt (UUID)")
    p.add_argument("--sql-out", required=True, help="Zieldatei fuer das SQL")
    p.add_argument("--bericht", help="Optionale Zieldatei fuer den Herkunftsbericht")
    p.add_argument("--stores", default=",".join(DEFAULT_STORES))
    return p.parse_args()


def lade_zutaten(pfad: Path) -> list[str]:
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    namen = [z.strip() for z in zeilen if z.strip() and not z.startswith("#")]
    uebersprungen = [n for n in namen if n in VARIANTEN]
    if uebersprungen:
        print("Uebersprungen (Schreibweise, kein fehlender Preis): "
              + ", ".join(uebersprungen), file=sys.stderr)
    return [n for n in namen if n not in VARIANTEN]


def scanne(
    csv_pfad: Path, zutaten: list[str], stores: set[str]
) -> dict[str, dict[str, Sichtung]]:
    """Liefert je Zutat die juengste Sichtung je Produkt-ID."""
    # Suchmuster je Zutat vorbereiten.
    muster: list[tuple[str, frozenset[str]]] = []
    for zutat in zutaten:
        begriff = SUCHBEGRIFFE.get(zutat, zutat)
        stamm = canonical_stems(begriff)
        if stamm:
            muster.append((zutat, stamm))

    treffer: dict[str, dict[str, Sichtung]] = defaultdict(dict)
    gelesen = 0

    with csv_pfad.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        leser = csv.DictReader(fh, delimiter=";")
        for zeile in leser:
            gelesen += 1
            if gelesen % 250_000 == 0:
                print(f"  ... {gelesen:,} Zeilen", file=sys.stderr)

            laden = (zeile.get("store") or "").strip()
            if laden not in stores:
                continue

            roh_name = fix_mojibake((zeile.get("name") or "").strip())
            if not roh_name:
                continue
            produkt_staemme = canonical_stems(roh_name)
            if not produkt_staemme:
                continue
            # Fertiggericht/Gebaeck/Suessware aussortieren, bevor ueberhaupt
            # verglichen wird - siehe AUSSCHLUSS.
            if any(_stem(w) in produkt_staemme for w in AUSSCHLUSS):
                continue

            passende = [z for z, stamm in muster if stamm <= produkt_staemme]
            if not passende:
                continue

            try:
                preis = float((zeile.get("price") or "").replace(",", "."))
            except ValueError:
                continue
            if preis <= 0:
                continue

            stand_text = (zeile.get("priceDate") or "").strip()
            try:
                stand = datetime.strptime(stand_text, "%Y-%m-%d").date()
            except ValueError:
                continue

            try:
                menge = float((zeile.get("quantity") or "").replace(",", "."))
            except ValueError:
                menge = None
            einheit = (zeile.get("unit") or "").strip() or None
            produkt_id = (zeile.get("id") or roh_name).strip()
            bio = (zeile.get("isBio") or "").strip().lower() == "true"

            s = Sichtung(roh_name, preis, menge, einheit, stand, laden, bio, produkt_staemme)
            for zutat in passende:
                vorher = treffer[zutat].get(produkt_id)
                if vorher is None or vorher.stand < stand:
                    treffer[zutat][produkt_id] = s

    print(f"  {gelesen:,} Zeilen gelesen", file=sys.stderr)
    return treffer


def waehle(sichtungen: list[Sichtung], ziel: frozenset[str]) -> Sichtung | None:
    """Median-Produkt aus den Kandidaten - siehe Auswahlregel im Modulkopf.

    Vorgeschaltet: je weniger Zusatzwoerter ein Produktname gegenueber dem
    Suchbegriff hat, desto eher ist es das Produkt selbst und nicht etwas, in
    dem es nur vorkommt. "Zucker" vs. "50% weniger Zucker Himbeere" - beides
    enthaelt das Wort, nur eines ist Zucker.
    """
    if not sichtungen:
        return None
    knappste = min(len(s.staemme - ziel) for s in sichtungen)
    kandidaten = [s for s in sichtungen if len(s.staemme - ziel) <= knappste + 1]
    ohne_bio = [s for s in kandidaten if not s.bio]
    kandidaten = ohne_bio or kandidaten
    # Ohne Packungsangabe laesst sich kein Grundpreis bilden; solche Zeilen
    # nur nehmen, wenn es gar nichts anderes gibt.
    mit_menge = [s for s in kandidaten if s.menge and s.menge > 0 and s.einheit]
    kandidaten = mit_menge or kandidaten
    if not kandidaten:
        return None
    kandidaten.sort(key=lambda s: s.preis)
    return kandidaten[len(kandidaten) // 2]


def sql_text(wert: str) -> str:
    return "'" + wert.replace("'", "''") + "'"


def main() -> int:
    args = parse_args()
    csv_pfad = Path(args.csv)
    if not csv_pfad.exists():
        print(f"Datei fehlt: {csv_pfad}", file=sys.stderr)
        return 1

    zutaten = lade_zutaten(Path(args.namen))
    stores = {s.strip() for s in args.stores.split(",") if s.strip()}
    print(f"{len(zutaten)} Zutaten, Ketten: {', '.join(sorted(stores))}", file=sys.stderr)

    treffer = scanne(csv_pfad, zutaten, stores)

    zeilen_sql: list[str] = []
    bericht: list[str] = []
    ohne = []

    for zutat in zutaten:
        kandidaten = list(treffer.get(zutat, {}).values())
        gewaehlt = waehle(kandidaten, canonical_stems(SUCHBEGRIFFE.get(zutat, zutat)))
        if gewaehlt is None:
            ohne.append(zutat)
            bericht.append(f"{zutat}\tKEIN TREFFER")
            continue

        preis_minor = int(round(gewaehlt.preis * 100))
        menge = gewaehlt.menge if (gewaehlt.menge and gewaehlt.menge > 0) else 1.0
        einheit = gewaehlt.einheit or "Stk"
        notiz = (
            f"{gewaehlt.laden}: {gewaehlt.produkt} (Stand {gewaehlt.stand.isoformat()}, "
            f"Median aus {len(kandidaten)} Produkten)"
        )
        zeilen_sql.append(
            "  ({haushalt}::uuid, {name}, {preis}, {menge}, {einheit}, {notiz})".format(
                haushalt=sql_text(args.haushalt),
                name=sql_text(zutat),
                preis=preis_minor,
                menge=f"{menge:.2f}",
                einheit=sql_text(einheit),
                notiz=sql_text(notiz),
            )
        )
        bericht.append(
            f"{zutat}\t{gewaehlt.preis:.2f} EUR / {menge:g} {einheit}\t{gewaehlt.produkt}"
            f"\t{gewaehlt.laden}\t{gewaehlt.stand.isoformat()}\t{len(kandidaten)} Kandidaten"
        )

    kopf = """-- Erzeugt von scripts/fill_missing_ingredient_prices.py aus latest-canonical.csv.
-- Jede Zeile traegt in der Notiz das Produkt, aus dem der Preis stammt.
-- Vorhandene Preise werden NICHT ueberschrieben (on conflict do nothing):
-- eine von Hand gepflegte Zeile ist mehr wert als ein Scrape-Treffer.

\\set ON_ERROR_STOP on
set client_encoding = 'UTF8';

begin;

insert into ingredient_prices
  (household_id, name, price_minor, pack_quantity, pack_unit, note, price_source)
select v.household_id, v.name, v.price_minor, v.pack_quantity, v.pack_unit, v.note, 'manual'
from (values
{{werte}}
) as v(household_id, name, price_minor, pack_quantity, pack_unit, note)
on conflict (household_id, lower(name)) do nothing;

commit;

select count(*) || ' Preiszeilen im Haushalt' from ingredient_prices
where household_id = {haushalt}::uuid;
""".format(haushalt=sql_text(args.haushalt))

    Path(args.sql_out).write_text(
        kopf.replace("{werte}", ",\n".join(zeilen_sql)), encoding="utf-8"
    )
    if args.bericht:
        Path(args.bericht).write_text("\n".join(bericht) + "\n", encoding="utf-8")

    print(f"{len(zeilen_sql)} Preise gefunden, {len(ohne)} ohne Treffer", file=sys.stderr)
    if ohne:
        print("Ohne Treffer: " + ", ".join(ohne), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
