"""Pflegt Beschreibungen und Synonyme der Kategorien (Kategorisierung, Schritt 1).

Hintergrund
-----------
Die KI-Kategorisierung baut ihre Kandidatenliste aus `categories` - Pfad,
semantischer Schluessel, Typ, Bucket, Synonyme und (falls vorhanden)
Beschreibung. In einem gewachsenen Bestand hatten 62 von 62 Kategorien KEINE
Beschreibung, und die Synonyme waren lueckenhaft: "Elektronik & Technik"
kannte mediamarkt/saturn, aber weder "foto" noch "kamera" - deshalb lieferte
das Modell fuer "WWW.MUSTERFOTO.DE" folgerichtig `no_match`. Die
Standardkategorie "Lebensmittel" hatte ueberhaupt keine Synonyme, also auch
kein einziges Supermarkt-Wort.

Die Vorlagen in `category_templates` (Quelle fuer NEUE Haushalte) helfen dem
Bestand nicht: `install_category_taxonomy()` ueberspringt jeden bereits
vorhandenen `semantic_key`, und `category_taxonomy_service.apply()` fasst
"already_present"-Kategorien nie wieder an. Bestandshaushalte brauchen deshalb
diesen Nachtrag - dieses Skript ist die reproduzierbare Fassung davon.

Was das Skript anfasst
----------------------
AUSSCHLIESSLICH `categories.description` und `categories.synonyms` (optional
zusaetzlich `category_templates`, siehe --templates). Nichts anderes.

Schutz vor Datenverlust
-----------------------
* Beschreibungen werden nur gesetzt, wenn das Feld leer ist. `--force-description`
  ueberschreibt bewusst auch gepflegte Texte.
* Synonyme werden NUR ERGAENZT, nie ersetzt: bestehende Eintraege bleiben in
  ihrer Reihenfolge stehen, neue kommen hinten dran (Vergleich case-insensitiv).
Damit ist der Lauf idempotent: ein zweiter Durchgang findet nichts mehr zu tun.

Der Trockenlauf fuehrt die UPDATEs in einer Transaktion aus und setzt sie am
Ende zurueck. Nur so kann er die Laenge der Kandidatenliste NACH der Pflege
ehrlich melden, statt sie zu schaetzen - geschrieben wird trotzdem nichts.

Aufruf (PowerShell)
-------------------
    # Vorschau fuer einen Haushalt (aendert nichts)
    services\\api\\.venv\\Scripts\\python.exe scripts\\seed_category_semantics.py `
        --household-id 00000000-0000-0000-0000-000000000001

    # Tatsaechlich schreiben
    services\\api\\.venv\\Scripts\\python.exe scripts\\seed_category_semantics.py `
        --household-id 00000000-0000-0000-0000-000000000001 --apply

    # Alle Haushalte + die Vorlagen fuer neue Haushalte
    services\\api\\.venv\\Scripts\\python.exe scripts\\seed_category_semantics.py `
        --all-households --templates --apply

Jeder Lauf meldet zum Schluss, wie lang die Kandidatenliste im Prompt danach
waere - mit und ohne Beschreibungen. Welche Fassung gesendet wird, entscheidet
im Produktivpfad das geladene Kontextfenster (app/llm/classification.py,
select_variant): voll, ohne Beschreibungen, ohne Synonyme.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"


@dataclass(frozen=True)
class Semantik:
    """Pflegedaten einer Kategorie.

    `beschreibung`: ein Satz, der dem Modell sagt, was hineingehoert und was
    NICHT - die Abgrenzung ist der eigentliche Nutzen, nicht die Wiederholung
    des Namens. Bewusst kurz (Richtwert 90 Zeichen), weil die gesamte
    Kandidatenliste in ein Prompt-Budget passen muss.

    `synonyme`: Alltagsvokabular UND typische Haendlernamen, klein und ohne
    Umlaute geschrieben wie der Bestand. Mehrdeutige Haendler (z. B. "amazon")
    fehlen absichtlich: ein erzwungener Fehltreffer ist schlechter als ein
    ehrliches `no_match`.
    """

    beschreibung: str
    synonyme: tuple[str, ...]


# ---------------------------------------------------------------------------
# Standardpaket - Schluessel ist categories.semantic_key (identisch mit
# category_templates.semantic_key)
# ---------------------------------------------------------------------------

STANDARD_SEMANTIK: dict[str, Semantik] = {
    # --- Einnahmen ---------------------------------------------------------
    "salary": Semantik(
        "Lohn und Gehalt vom Arbeitgeber; nicht Selbstständigkeit, Rente oder Erstattung.",
        (
            "gehalt",
            "lohn",
            "gehaltszahlung",
            "lohnzahlung",
            "bezuege",
            "arbeitgeber",
            "verguetung",
            "entgeltabrechnung",
            "urlaubsgeld",
            "weihnachtsgeld",
            "bonuszahlung",
        ),
    ),
    "self_employment": Semantik(
        "Einnahmen aus eigener Tätigkeit: Honorar, Gewerbe, Nebenjob; kein Arbeitgeberlohn.",
        (
            "selbststaendigkeit",
            "gewerbe",
            "auftraggeber",
            "kundenzahlung",
            "nebenverdienst",
            "minijob",
            "werkvertrag",
            "honorarzahlung",
        ),
    ),
    "pension": Semantik(
        "Laufende Alters-, Hinterbliebenen- und Betriebsrenten; keine Vorsorgebeiträge.",
        (
            "deutsche rentenversicherung",
            "rentenzahlung",
            "betriebsrente",
            "versorgungsbezuege",
            "witwenrente",
            "pensionskasse",
        ),
    ),
    "public_benefits": Semantik(
        "Erhaltene staatliche Leistungen und Unterhalt; gezahlter Unterhalt ist eine Ausgabe.",
        (
            "arbeitslosengeld",
            "agentur fuer arbeit",
            "jobcenter",
            "krankengeld",
            "mutterschaftsgeld",
            "pflegegeld",
            "unterhaltsvorschuss",
            "sozialleistung",
        ),
    ),
    "refunds": Semantik(
        "Zurückerhaltenes Geld: Retoure, Storno, Steuer- oder Nebenkostenerstattung.",
        (
            "rueckerstattung",
            "storno",
            "reklamation",
            "ruecklastschrift",
            "versicherungsleistung",
            "schadenregulierung",
            "guthaben abrechnung",
        ),
    ),
    "capital_income": Semantik(
        "Zinsen, Dividenden und Ausschüttungen; keine Umbuchung auf das eigene Depot.",
        (
            "zinsgutschrift",
            "ertragsgutschrift",
            "fondsausschuettung",
            "tagesgeldzinsen",
            "wertpapierverkauf",
        ),
    ),
    # --- Lebensmittel & Gastronomie ---------------------------------------
    "groceries": Semantik(
        "Lebensmitteleinkäufe in Supermärkten, Discountern, Bio- und Getränkemärkten; nicht Gastronomie.",
        (
            "supermarkt",
            "discounter",
            "wocheneinkauf",
            "lebensmitteleinkauf",
            "rewe",
            "edeka",
            "aldi",
            "lidl",
            "kaufland",
            "penny",
            "netto",
            "norma",
            "tegut",
            "marktkauf",
            "globus",
            "famila",
            "nahkauf",
            "denns",
            "alnatura",
            "bioladen",
            "getraenkemarkt",
            "metzgerei",
            "wochenmarkt",
            "hofladen",
        ),
    ),
    # --- Transfers ---------------------------------------------------------
    "internal_transfer": Semantik(
        "Geld zwischen eigenen Konten sowie Bargeld am Automaten; keine echte Ausgabe.",
        (
            "eigenes konto",
            "umbuchung tagesgeld",
            "bargeld",
            "auszahlung geldautomat",
            "kontouebertrag",
        ),
    ),
    "card_settlement": Semantik(
        "Ausgleich von Kreditkarten- oder PayPal-Konto; der Einkauf zählt woanders.",
        (
            "kartenabrechnung",
            "visa abrechnung",
            "mastercard abrechnung",
            "ausgleich kreditkartenkonto",
        ),
    ),
    "saving_investing": Semantik(
        "Überweisung in Sparen, Depot oder Kredittilgung; das Vermögen bleibt erhalten.",
        (
            "wertpapierkauf",
            "trade republic",
            "scalable capital",
            "festgeld",
            "tagesgeld",
            "bausparen",
            "sondertilgung",
            "vermoegenswirksame leistungen",
        ),
    ),
    # --- Wohnen & Haushalt -------------------------------------------------
    "rent": Semantik(
        "Miete, Hausgeld und Stellplatzmiete an Vermieter oder Hausverwaltung.",
        (
            "mietzahlung",
            "warmmiete",
            "hausverwaltung",
            "vermieter",
            "garagenmiete",
            "stellplatz",
        ),
    ),
    "service_charges": Semantik(
        "Wasser, Müll, Grundsteuer und Hausnebenkosten; Strom und Gas zählen separat.",
        (
            "wasserwerke",
            "abfallwirtschaft",
            "muellgebuehr",
            "schornsteinfeger",
            "strassenreinigung",
            "hausmeisterservice",
        ),
    ),
    "energy": Semantik(
        "Strom, Gas, Fernwärme, Heizöl und Pellets samt Abschlägen des Versorgers.",
        (
            "eon",
            "vattenfall",
            "enbw",
            "rwe",
            "lichtblick",
            "yello",
            "abschlag strom",
            "energieversorger",
            "pellets",
        ),
    ),
    "telecom": Semantik(
        "Internet, Festnetz, Mobilfunk und Prepaid-Guthaben; keine Gerätekäufe.",
        (
            "1und1",
            "congstar",
            "pyur",
            "aldi talk",
            "festnetz",
            "telefonrechnung",
            "prepaid guthaben",
        ),
    ),
    "broadcast_fee": Semantik(
        "Rundfunkbeitrag an den Beitragsservice von ARD, ZDF und Deutschlandradio.",
        ("rundfunk",),
    ),
    "furnishing": Semantik(
        "Möbel, Baumarkt, Deko und Handwerker für die Wohnung; keine Elektronik.",
        (
            "obi",
            "bauhaus",
            "hornbach",
            "toom",
            "hagebau",
            "poco",
            "xxxlutz",
            "roller moebel",
            "moemax",
            "teppich",
            "lampen",
            "sanitaer",
        ),
    ),
    "household_supplies": Semantik(
        "Drogerie, Wasch- und Putzmittel, Körperpflege und Haushaltswaren.",
        (
            "drogerie mueller",
            "budni",
            "koerperpflege",
            "kosmetik",
            "zahnpflege",
            "reinigungsmittel",
            "muellbeutel",
        ),
    ),
    # --- Lebensmittel & Gastronomie ---------------------------------------
    "bakery_cafe": Semantik(
        "Bäckerei, Konditorei, Eiscafé und Kaffee unterwegs; kein Supermarkteinkauf.",
        (
            "backwaren",
            "broetchen",
            "kamps",
            "backwerk",
            "eiscafe",
            "eisdiele",
            "espresso",
        ),
    ),
    "restaurant": Semantik(
        "Essen und Trinken vor Ort: Restaurant, Imbiss, Bar; keine Lieferung nach Hause.",
        (
            "doener",
            "burger",
            "mcdonalds",
            "burger king",
            "kfc",
            "subway",
            "sushi",
            "brauhaus",
            "biergarten",
            "gasthaus",
            "bistro",
            "trinkgeld",
        ),
    ),
    "delivery_canteen": Semantik(
        "Lieferdienst nach Hause sowie Kantine, Mensa und Betriebsverpflegung.",
        (
            "essenslieferung",
            "pizza lieferung",
            "dominos",
            "betriebsrestaurant",
            "studentenwerk",
            "just eat",
        ),
    ),
    # --- Mobilität ---------------------------------------------------------
    "public_transport": Semantik(
        "Bus, Bahn und Nahverkehr im Alltag; dienstliche Reisen zählen separat.",
        (
            "bahncard",
            "flixbus",
            "rmv",
            "vgn",
            "kvb",
            "verkehrsverbund",
        ),
    ),
    "fuel_charging": Semantik(
        "Kraftstoff und Ladestrom an Tankstelle oder Ladesäule fürs eigene Fahrzeug.",
        (
            "esso",
            "total",
            "avia",
            "star tankstelle",
            "hem tankstelle",
            "supercharger",
            "ladestrom",
            "adblue",
        ),
    ),
    "parking_toll": Semantik(
        "Parkgebühren, Parkhaus, Maut, Vignette und Fähre unterwegs.",
        (
            "easypark",
            "parkster",
            "tiefgarage",
            "parkgebuehren",
            "faehrticket",
        ),
    ),
    "car_service": Semantik(
        "Werkstatt, Inspektion, HU, Reifen, Autowäsche und Ersatzteile.",
        (
            "atu",
            "pitstop",
            "vergoelst",
            "dekra",
            "hauptuntersuchung",
            "abgasuntersuchung",
            "waschanlage",
            "ersatzteile",
            "autohaus service",
        ),
    ),
    "car_tax_insurance": Semantik(
        "Kfz-Steuer, Kfz-Haftpflicht, Kasko und Zulassung des eigenen Fahrzeugs.",
        (
            "kfz haftpflicht",
            "teilkasko",
            "vollkasko",
            "hauptzollamt",
            "zulassungsstelle",
        ),
    ),
    "taxi_sharing": Semantik(
        "Taxi, Fahrdienst, Car-, Bike- und Scootersharing sowie Mietwagen.",
        (
            "freenow",
            # "tier"/"voi" waeren als blosse Marke zu kurz: "Tier" ist im
            # Deutschen das Tier und zoege Buchungen der Haustier-Kategorie an.
            "tier mobility",
            "lime",
            "nextbike",
            "share now",
            "europcar",
            "hertz",
            "avis",
        ),
    ),
    # --- Gesundheit, Versicherung & Vorsorge -------------------------------
    "doctor": Semantik(
        "Arzt-, Zahnarzt- und Klinikrechnungen samt Zuzahlung; keine Medikamente.",
        (
            "hausarzt",
            "kieferorthopaede",
            "augenarzt",
            "hautarzt",
            "radiologie",
            "labor",
            "krankenhaus",
            "heilpraktiker",
            "privatliquidation",
            "pvs",
        ),
    ),
    "pharmacy": Semantik(
        "Apotheke und Versandapotheke: Medikamente, Rezeptgebühr, Arzneimittel.",
        (
            "docmorris",
            "shop apotheke",
            "versandapotheke",
            "rezeptgebuehr",
            "arzneimittel",
        ),
    ),
    "therapy_care": Semantik(
        "Therapie, Pflegedienst, Optiker, Hörgeräte und medizinische Hilfsmittel.",
        (
            "ergotherapie",
            "logopaedie",
            "osteopathie",
            "massage",
            "optiker",
            "fielmann",
            "apollo optik",
            "hoerakustik",
            "pflegedienst",
        ),
    ),
    "health_insurance": Semantik(
        "Beiträge zur Kranken- und Pflegeversicherung, gesetzlich wie privat.",
        (
            "dak",
            "ikk",
            "kkh",
            "bkk",
            "hkk",
            "debeka",
            "barmenia",
            "signal iduna",
            "beitrag krankenkasse",
        ),
    ),
    "property_insurance": Semantik(
        "Privathaftpflicht, Hausrat, Rechtsschutz und Gebäudeversicherung; nicht Kfz.",
        (
            "privathaftpflicht",
            "hausratversicherung",
            "rechtsschutzversicherung",
            "arag",
            "glasversicherung",
            "fahrradversicherung",
        ),
    ),
    "risk_insurance": Semantik(
        "Beiträge für Berufsunfähigkeit, Unfall- und Risikolebensversicherung.",
        (
            "berufsunfaehigkeitsversicherung",
            "risikolebensversicherung",
            "sterbegeldversicherung",
            "unfallschutz",
        ),
    ),
    "private_pension": Semantik(
        "Beiträge zur privaten oder betrieblichen Altersvorsorge, Riester und Rürup.",
        (
            "privatrente",
            "basisrente",
            "direktversicherung",
            "entgeltumwandlung",
        ),
    ),
    "supplements": Semantik(
        "Vitamine, Mineralstoffe und Eiweißpulver ohne ärztliche Verordnung.",
        (
            "nahrungsergaenzungsmittel",
            "magnesium",
            "omega 3",
            "vitamin d",
            "eiweisspulver",
            "myprotein",
        ),
    ),
    # --- Familie, Bildung & Haustiere --------------------------------------
    "childcare_school": Semantik(
        "Kita, Krippe, Hort, Schul- und Betreuungsbeiträge einschließlich Essensgeld.",
        (
            "kindertagesstaette",
            "krippe",
            "elternbeitrag",
            "ganztagsbetreuung",
            "babysitter",
            "essensgeld",
        ),
    ),
    "child_supplies": Semantik(
        "Kleidung, Spielzeug, Schulbedarf und Taschengeld für Kinder.",
        (
            "kinderwagen",
            "babymarkt",
            "schulranzen",
            "schulbedarf",
            "kinderschuhe",
            "spielwaren",
            "mytoys",
        ),
    ),
    "alimony_paid": Semantik(
        "Gezahlter Unterhalt an Kinder oder Ex-Partner; erhaltener ist eine Einnahme.",
        (
            "trennungsunterhalt",
            "unterhaltszahlung monatlich",
        ),
    ),
    "education": Semantik(
        "Kurse, Studium, Nachhilfe und private Weiterbildung; nicht beruflich veranlasst.",
        (
            "volkshochschule",
            "vhs",
            "sprachkurs",
            "nachhilfe",
            "studiengebuehr",
            "musikschule",
            "fahrschule",
            "udemy",
        ),
    ),
    "pets": Semantik(
        "Futter, Tierarzt, Zubehör und Hundesteuer für eigene Tiere.",
        (
            "zooplus",
            "das futterhaus",
            "tierklinik",
            "tierheim",
            "katzenstreu",
            "hundesteuer",
        ),
    ),
    # --- Freizeit, Konsum & Reisen -----------------------------------------
    "clothing": Semantik(
        "Kleidung, Schuhe und Wäsche für Erwachsene; Kindersachen zählen separat.",
        (
            "c und a",
            "peek cloppenburg",
            "deichmann",
            "snipes",
            "about you",
            "tk maxx",
            "primark",
            "kik",
            "ernstings family",
            "sneaker",
            "waesche",
        ),
    ),
    "electronics": Semantik(
        "Geräte und Zubehör: Computer, Handy, Foto, Audio; auch Reparaturen.",
        (
            "foto",
            "kamera",
            "fotostudio",
            "fotograf",
            "fotoservice",
            "objektiv",
            "notebook",
            "laptop",
            "smartphone",
            "tablet",
            "kopfhoerer",
            "fernseher",
            "hifi",
            "cyberport",
            "notebooksbilliger",
            "alternate",
            "conrad",
            "expert",
            "euronics",
            "apple store",
            "festplatte",
            "monitor",
            "ladekabel",
        ),
    ),
    "hobby_gaming": Semantik(
        "Spiele, Sammel- und Bastelbedarf sowie Ausrüstung für eigene Hobbys.",
        (
            "nintendo",
            "xbox",
            "epic games",
            "gamestop",
            "bastelbedarf",
            "brettspiele",
            "lego",
            "sammelkarten",
            "musikinstrument",
        ),
    ),
    "streaming": Semantik(
        "Laufende Abos für Musik, Video und digitale Medien im Privatbereich.",
        (
            "prime video",
            "dazn",
            # "sky" deckt das Angebot mit; "wow" allein waere als Synonym
            # zu beliebig.
            "sky",
            "paramount",
            "rtl plus",
            "joyn",
            "deezer",
            "crunchyroll",
            "apple tv",
        ),
    ),
    "sports_clubs": Semantik(
        "Fitnessstudio, Vereinsbeitrag, Schwimmbad und Sportausrüstung.",
        (
            "mcfit",
            "fitx",
            "clever fit",
            "urban sports",
            "sportverein",
            "kletterhalle",
            "therme",
            "decathlon",
            "sportgeschaeft",
        ),
    ),
    "culture_events": Semantik(
        "Eintritt für Kino, Konzert, Theater, Museum, Zoo und Freizeitparks.",
        (
            "cinemaxx",
            "cineplex",
            "uci kino",
            "ticketmaster",
            "reservix",
            "musical",
            "zoo",
            "freizeitpark",
            "eintritt",
        ),
    ),
    "travel": Semantik(
        "Urlaubsreisen: Unterkunft, Flug, Pauschalreise und Camping; nicht dienstlich.",
        (
            "lufthansa",
            "ryanair",
            "eurowings",
            "tui",
            "expedia",
            "hrs",
            "hostel",
            "campingplatz",
            "reisebuero",
            "kreuzfahrt",
            "ferienhaus",
        ),
    ),
    "gifts": Semantik(
        "Geschenke, Blumen und Gutscheine für andere; keine eigenen Anschaffungen.",
        (
            "blumenladen",
            "fleurop",
            "gutschein",
            "hochzeitsgeschenk",
            "floristik",
        ),
    ),
    "books_press": Semantik(
        "Bücher, Hörbücher, Zeitungen und Zeitschriften für privat.",
        (
            "buchhandlung",
            "tageszeitung",
            "zeitschriftenabo",
            "kindle",
            "ebook",
            "weltbild",
            "osiander",
            "mayersche",
        ),
    ),
    # --- Finanzen & Verpflichtungen ----------------------------------------
    "bank_fees": Semantik(
        "Kontoführung, Kartenentgelte und Gebühren der Bank; keine Zinsen.",
        (
            "kontofuehrungsentgelt",
            "kartenentgelt",
            "jahresgebuehr kreditkarte",
            "fremdwaehrungsentgelt",
            "rechnungsabschluss",
        ),
    ),
    "loan_interest": Semantik(
        "Zinsen und Kosten für Kredite und Dispo; die Tilgung ist ein Transfer.",
        (
            "ueberziehungszinsen",
            "darlehenszinsen",
            "bauzinsen",
            "zinsabschluss",
        ),
    ),
    "taxes_authorities": Semantik(
        "Steuern, Buß- und Verwaltungsgebühren an Finanzamt, Stadt und Behörden.",
        (
            "einkommensteuer",
            "umsatzsteuer",
            "vorauszahlung finanzamt",
            "kirchensteuer",
            "ordnungsamt",
            "verwarngeld",
            "stadtkasse",
            "gebuehrenbescheid",
        ),
    ),
    "donations": Semantik(
        "Freiwillige Spenden, Patenschaften und Förderbeiträge an Organisationen.",
        (
            "foerderverein",
            "patenschaft",
            "unicef",
            "aerzte ohne grenzen",
            "greenpeace",
            "wwf",
        ),
    ),
    "installments": Semantik(
        "Raten und Leasing für finanzierte Käufe; reine Zinsen zählen separat.",
        (
            "monatsrate",
            "leasingrate",
            "leasing",
            "santander",
            "consors finanz",
            "easycredit",
            "paypal ratenzahlung",
        ),
    ),
    # --- Beruf & Arbeitsmittel ---------------------------------------------
    "work_software": Semantik(
        "Beruflich genutzte Software, Cloud, Hosting und KI-Dienste samt Lizenzen.",
        (
            "google workspace",
            "dropbox",
            "notion",
            "canva",
            "figma",
            "hetzner",
            "ionos",
            "strato",
            "domain",
            "webhosting",
            "cursor",
            "perplexity",
        ),
    ),
    "work_equipment": Semantik(
        "Beruflich genutzte Arbeitsmittel, Büromaterial und Fachliteratur.",
        (
            "bueromaterial",
            "druckerpatrone",
            "toner",
            "fachzeitschrift",
            "schreibtisch",
            "arbeitskleidung",
            "schutzausruestung",
        ),
    ),
    "work_travel": Semantik(
        "Fahrt, Übernachtung und Spesen aus dienstlichem Anlass; nichts Privates.",
        (
            "tagegeld",
            "kilometergeld",
            "hotel dienstlich",
            "flug dienstlich",
            "seminarreise",
        ),
    ),
    "work_fees": Semantik(
        "Kammer-, Verbands- und Gewerkschaftsbeiträge sowie berufliche Versicherungen.",
        (
            "berufsgenossenschaft",
            "handwerkskammer",
            "aerztekammer",
            "verdi",
            "gewerkschaftsbeitrag",
            "kuenstlersozialkasse",
            "ksk",
            "betriebshaftpflicht",
        ),
    ),
}


# ---------------------------------------------------------------------------
# Alt-Sammelkategorien aus dem fruehen Fuenf-Gruppen-Seed. Sie haben KEINEN
# semantic_key und lassen sich deshalb nur am Namen erkennen. Sie bekommen
# bewusst NUR eine abgrenzende Beschreibung und KEINE Synonyme: sie
# konkurrieren sonst mit den genaueren Standardkategorien und werden genau zu
# dem Auffangbecken, das der Systemprompt ausdruecklich verbietet.
# ---------------------------------------------------------------------------

LEGACY_SEMANTIK: dict[str, Semantik] = {
    "Wohnen": Semantik(
        "Alt-Sammelkategorie; nur wählen, wenn keine genauere Wohnkategorie passt.",
        (),
    ),
    "Freizeit": Semantik(
        "Alt-Sammelkategorie; nur wählen, wenn keine genauere Freizeitkategorie passt.",
        (),
    ),
    "Sparziele": Semantik(
        "Alt-Sammelkategorie für Sparbeträge; bevorzugt die Transfer-Kategorien nutzen.",
        (),
    ),
    "Interne Uebertragung": Semantik(
        "Alt-Sammelkategorie; für Umbuchungen bevorzugt Interne Umbuchung wählen.",
        (),
    ),
}


# ---------------------------------------------------------------------------
# Datenbankzugriff
# ---------------------------------------------------------------------------

_CATEGORY_SQL = """
select c.id, c.name, c.semantic_key, c.origin, c.description, c.synonyms
from categories c
where c.household_id = %s and not c.is_archived
order by c.sort_order, c.name
"""

# Genau die Abfrage aus ai_service._CANDIDATE_SQL (ohne die Taxonomieversion,
# die fuer die Laenge keine Rolle spielt) - nur so misst das Skript wirklich
# das, was spaeter im Prompt landet.
_PROMPT_SQL = """
select c.id, c.name, c.semantic_key, c.description, c.synonyms,
       c.default_type, c.budget_bucket, cg.name as group_name
from categories c
join category_groups cg on cg.id = c.group_id and cg.household_id = c.household_id
where c.household_id = %s and not c.is_archived and not cg.is_archived
order by cg.sort_order, cg.name, c.sort_order, c.name
"""

_HOUSEHOLDS_SQL = "select id from households order by created_at"


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _semantik_fuer(semantic_key: str | None, name: str, origin: str | None) -> Semantik | None:
    """Pflegedaten einer Kategorie oder None, wenn sie nicht angefasst wird.

    Kategorien mit origin='user' bleiben grundsaetzlich unberuehrt - sie
    gehoeren dem Nutzer, ihre Bedeutung kennt nur er."""
    if semantic_key:
        return STANDARD_SEMANTIK.get(semantic_key)
    if origin == "legacy":
        return LEGACY_SEMANTIK.get(name)
    return None


def _ergaenzte_synonyme(vorhanden: list[str], neu: tuple[str, ...]) -> list[str]:
    """Bestand + Neues, ohne Doppelte und ohne die Reihenfolge zu verwerfen.

    Der Vergleich ist case-insensitiv und ignoriert Randleerraum, damit ein
    bereits gepflegtes 'Rewe' kein zweites 'rewe' nach sich zieht."""
    ergebnis = list(vorhanden)
    bekannt = {eintrag.strip().casefold() for eintrag in vorhanden}
    for eintrag in neu:
        schluessel = eintrag.strip().casefold()
        if schluessel and schluessel not in bekannt:
            ergebnis.append(eintrag)
            bekannt.add(schluessel)
    return ergebnis


@dataclass
class Aenderung:
    kategorie_id: str
    name: str
    beschreibung_neu: str | None
    synonyme_neu: list[str] | None
    synonyme_zuwachs: int


def _plane_haushalt(
    cur: psycopg.Cursor, household_id: str, *, force_description: bool
) -> tuple[list[Aenderung], int, int]:
    """Was der Lauf an diesem Haushalt aendern wuerde. Schreibt nichts."""
    cur.execute(_CATEGORY_SQL, (household_id,))
    zeilen = cur.fetchall()
    aenderungen: list[Aenderung] = []
    ohne_pflegedaten = 0
    for kategorie_id, name, semantic_key, origin, beschreibung, synonyme in zeilen:
        semantik = _semantik_fuer(semantic_key, name, origin)
        if semantik is None:
            ohne_pflegedaten += 1
            continue
        beschreibung_neu: str | None = None
        if semantik.beschreibung and (force_description or not (beschreibung or "").strip()):
            if (beschreibung or "") != semantik.beschreibung:
                beschreibung_neu = semantik.beschreibung
        vorhanden = [str(eintrag) for eintrag in (synonyme or [])]
        ergaenzt = _ergaenzte_synonyme(vorhanden, semantik.synonyme)
        synonyme_neu = ergaenzt if ergaenzt != vorhanden else None
        if beschreibung_neu is None and synonyme_neu is None:
            continue
        aenderungen.append(
            Aenderung(
                kategorie_id=str(kategorie_id),
                name=str(name),
                beschreibung_neu=beschreibung_neu,
                synonyme_neu=synonyme_neu,
                synonyme_zuwachs=len(ergaenzt) - len(vorhanden),
            )
        )
    return aenderungen, len(zeilen), ohne_pflegedaten


def _schreibe(cur: psycopg.Cursor, aenderungen: list[Aenderung]) -> None:
    """Setzt AUSSCHLIESSLICH description und synonyms. Version, updated_at und
    die Taxonomie-Revision zieht die Datenbank per Trigger selbst nach."""
    for aenderung in aenderungen:
        felder = []
        werte: list[object] = []
        if aenderung.beschreibung_neu is not None:
            felder.append("description = %s")
            werte.append(aenderung.beschreibung_neu)
        if aenderung.synonyme_neu is not None:
            felder.append("synonyms = %s")
            werte.append(aenderung.synonyme_neu)
        werte.append(aenderung.kategorie_id)
        cur.execute(f"update categories set {', '.join(felder)} where id = %s", werte)


def _kandidateneintrag(zeile: tuple[Any, ...], *, mit_beschreibung: bool) -> dict[str, object]:
    """Nachbau von ai_service._candidate_entry - Feldreihenfolge und
    Auslassungen muessen gleich sein, sonst misst das Skript die falsche
    Laenge."""
    kategorie_id, name, semantic_key, beschreibung, synonyme, typ, bucket, gruppe = zeile
    eintrag: dict[str, object] = {"id": str(kategorie_id), "path": f"{gruppe} · {name}"}
    if semantic_key:
        eintrag["key"] = semantic_key
    if typ:
        eintrag["type"] = typ
    if bucket:
        eintrag["bucket"] = bucket
    liste = [str(eintrag_syn) for eintrag_syn in (synonyme or [])]
    if liste:
        eintrag["synonyms"] = liste
    if mit_beschreibung and beschreibung:
        eintrag["description"] = str(beschreibung)
    return eintrag


def _promptlaenge(cur: psycopg.Cursor, household_id: str) -> tuple[int, int, int]:
    """(Anzahl Kandidaten, Zeichen mit Beschreibung, Zeichen ohne Beschreibung)."""
    cur.execute(_PROMPT_SQL, (household_id,))
    zeilen = cur.fetchall()
    mit = json.dumps(
        [_kandidateneintrag(zeile, mit_beschreibung=True) for zeile in zeilen],
        ensure_ascii=False,
    )
    ohne = json.dumps(
        [_kandidateneintrag(zeile, mit_beschreibung=False) for zeile in zeilen],
        ensure_ascii=False,
    )
    return len(zeilen), len(mit), len(ohne)


def _pflege_templates(cur: psycopg.Cursor, *, force_description: bool) -> int:
    """Dieselben Daten in category_templates - die Quelle fuer NEUE Haushalte.

    ACHTUNG: supabase/migrations/0001_schema.sql wird in dieser Projektphase
    direkt bearbeitet und wiederholt eingespielt; dabei setzt der dortige
    Seed-INSERT die Vorlagen zurueck. Nach jedem erneuten Einspielen des
    Schemas muss dieser Lauf also wiederholt werden."""
    cur.execute(
        "select id, semantic_key, description, synonyms from category_templates "
        "order by taxonomy_version, sort_order"
    )
    geaendert = 0
    for template_id, semantic_key, beschreibung, synonyme in cur.fetchall():
        semantik = STANDARD_SEMANTIK.get(semantic_key)
        if semantik is None:
            continue
        felder = []
        werte: list[object] = []
        if semantik.beschreibung and (force_description or not (beschreibung or "").strip()):
            if (beschreibung or "") != semantik.beschreibung:
                felder.append("description = %s")
                werte.append(semantik.beschreibung)
        vorhanden = [str(eintrag) for eintrag in (synonyme or [])]
        ergaenzt = _ergaenzte_synonyme(vorhanden, semantik.synonyme)
        if ergaenzt != vorhanden:
            felder.append("synonyms = %s")
            werte.append(ergaenzt)
        if not felder:
            continue
        geaendert += 1
        werte.append(template_id)
        cur.execute(
            f"update category_templates set {', '.join(felder)} where id = %s",
            werte,
        )
    return geaendert


def _haushalte(cur: psycopg.Cursor, args: argparse.Namespace) -> list[str]:
    if args.all_households:
        cur.execute(_HOUSEHOLDS_SQL)
        return [str(zeile[0]) for zeile in cur.fetchall()]
    return [args.household_id]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    ziel = parser.add_mutually_exclusive_group(required=True)
    ziel.add_argument("--household-id", help="UUID des Haushalts")
    ziel.add_argument("--all-households", action="store_true", help="alle Haushalte")
    parser.add_argument("--apply", action="store_true", help="wirklich schreiben")
    parser.add_argument(
        "--templates",
        action="store_true",
        help="zusaetzlich category_templates pflegen (Quelle fuer neue Haushalte)",
    )
    parser.add_argument(
        "--force-description",
        action="store_true",
        help="auch bereits gepflegte Beschreibungen ueberschreiben",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        # Die Beschreibungen tragen Umlaute; eine cp1252-Konsole soll den Lauf
        # nicht mit einem UnicodeEncodeError abbrechen.
        sys.stdout.reconfigure(errors="replace")

    modus = "SCHREIBEN" if args.apply else "TROCKENLAUF (nichts wird geaendert)"
    print(f"Modus: {modus}")
    print(f"Pflegedaten: {len(STANDARD_SEMANTIK)} Standard-, {len(LEGACY_SEMANTIK)} Alt-Kategorien")

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            if args.templates:
                anzahl = _pflege_templates(cur, force_description=args.force_description)
                print(f"category_templates: {anzahl} Vorlage(n) betroffen")

            for household_id in _haushalte(cur, args):
                aenderungen, gesamt, ohne_pflegedaten = _plane_haushalt(
                    cur, household_id, force_description=args.force_description
                )
                if not aenderungen and not args.all_households:
                    print(f"\n=== Haushalt {household_id} ===")
                    print(f"  {gesamt} Kategorien, nichts zu tun")
                elif aenderungen:
                    print(f"\n=== Haushalt {household_id} ===")
                    print(
                        f"  {gesamt} Kategorien, {len(aenderungen)} zu aendern, "
                        f"{ohne_pflegedaten} ohne Pflegedaten"
                    )
                    for aenderung in aenderungen:
                        teile = []
                        if aenderung.beschreibung_neu is not None:
                            teile.append("Beschreibung")
                        if aenderung.synonyme_zuwachs:
                            teile.append(f"+{aenderung.synonyme_zuwachs} Synonyme")
                        print(f"    - {aenderung.name}: {', '.join(teile)}")
                if aenderungen:
                    _schreibe(cur, aenderungen)

                anzahl, mit, ohne = _promptlaenge(cur, household_id)
                if aenderungen or not args.all_households:
                    zustand = "danach" if args.apply else "waere danach"
                    # Eine feste Grenze gibt es nicht mehr: der Produktivpfad
                    # (app/llm/classification.select_variant) waehlt die
                    # vollstaendigste Fassung, die ins GELADENE Fenster passt -
                    # voll, ohne Beschreibungen, ohne Synonyme.
                    print(
                        f"  Kandidatenliste {zustand}: {anzahl} Kategorien, "
                        f"{mit} Zeichen mit / {ohne} Zeichen ohne Beschreibung"
                    )

            if args.apply:
                conn.commit()
                print("\nGeschrieben.")
            else:
                conn.rollback()
                print("\nTrockenlauf beendet - nichts geschrieben. Mit --apply anwenden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
