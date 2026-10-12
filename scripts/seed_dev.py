"""Entwicklungs-Seed: befuellt einen Haushalt mit vorfuehrbaren Beispieldaten.

Ziel ist ein Haushalt, in dem JEDER Menuepunkt der Web-App etwas Sinnvolles
anzeigt (Uebersicht, Budget, Alle Buchungen, Review-Inbox, Konten, Berichte,
Fixkosten, Sparziele, Wunschliste, Lebensmittel, Vermoegen, Schulden, Importe,
Regeln, Einstellungen) - inklusive der interessanten Randzustaende
(ueberschrittene Budgetkategorie, positiver Rollover, Kaufampel gruen/gelb/rot,
Budgetueberschreitung in einer Essenswoche, offene Review-Vorschlaege).

Grundsaetze
-----------
1. Zugangsdaten kommen AUSSCHLIESSLICH aus Umgebungsvariablen (SEED_EMAIL,
   SEED_PASSWORD). Im Repository steht kein Passwort - auch nicht als
   Beispiel oder Kommentar.
2. Der Weg in die Datenbank fuehrt bevorzugt ueber die echte API
   (http://127.0.0.1:8882) mit einem per Supabase-Login geholten Token. Damit
   laeuft der Seed durch dieselben Validierungen wie ein Nutzer und ist
   gleichzeitig ein Rauchtest. Nur wo es keinen Endpunkt gibt, wird direkt per
   psycopg geschrieben - jede dieser Stellen ist unten mit Begruendung
   markiert und wird am Ende als API-Luecke gemeldet.
3. Nicht destruktiv: bestehende Konten und Buchungen bleiben unangetastet. Der
   Seed legt eigene, klar als Beispiel erkennbare Konten an (Namenszusatz
   " (Demo)") und bucht ausschliesslich auf diese.
4. Mehrfach ausfuehrbar: wiedererkennbare Namen bzw. der natuerliche Schluessel
   (Konto, Kalendermonat, Betrag, Empfaenger) verhindern Verdopplungen. Es wird
   nie "blind geloescht, um neu anzulegen".
5. Alle Beispieldaten sind erkennbar erfunden: keine echten Personen, keine
   echten Haendler, keine IBANs.

Aufruf (PowerShell)
-------------------
    $env:SEED_EMAIL = "..."
    $env:SEED_PASSWORD = "..."
    services\\api\\.venv\\Scripts\\python.exe scripts\\seed_dev.py

Weitere Betriebsarten siehe --help bzw. README.md (Abschnitt
"Entwicklungs-Seed").
"""

import argparse
import calendar
import csv
import io
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import psycopg

# ---------------------------------------------------------------------------
# Voreinstellungen (alle per Umgebungsvariable ueberschreibbar)
# ---------------------------------------------------------------------------

DEFAULT_API_URL = "http://127.0.0.1:8882"
DEFAULT_SUPABASE_URL = "http://127.0.0.1:55321"
# Der anon-Key des lokalen Supabase-Stacks ist kein Geheimnis: `supabase start`
# erzeugt ihn fuer JEDES lokale Projekt identisch, er steht so auch in
# .env.example. Fuer andere Umgebungen per SEED_SUPABASE_ANON_KEY setzen.
DEFAULT_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9."
    "CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
)
DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"

# Namenszusatz aller vom Seed angelegten Objekte. Er macht die Daten im UI als
# Beispiel erkennbar und ist gleichzeitig der stabile Wiedererkennungsschluessel
# fuer den zweiten Lauf und fuer --reset.
DEMO = " (Demo)"

# ---------------------------------------------------------------------------
# Fachliche Beispieldaten
# ---------------------------------------------------------------------------

KONTO_GIRO = "Girokonto" + DEMO
KONTO_SPAR = "Sparkonto" + DEMO
KONTO_KASSE = "Haushaltskasse" + DEMO

ZIEL_NOTGROSCHEN = "Notgroschen" + DEMO
ZIEL_URLAUB = "Urlaub Nordsee" + DEMO
ZIEL_LASTENRAD = "Lastenrad" + DEMO

# Zusaetzliche Kategorien je bestehender Kategoriegruppe. Die Gruppen legt
# bereits create_household() an (supabase/migrations/0001_schema.sql).
# (Gruppe, Kategoriename, default_type, budget_bucket)
NEUE_KATEGORIEN: tuple[tuple[str, str, str, str | None], ...] = (
    ("Einnahmen", "Nebeneinkuenfte", "income", None),
    ("Beduerfnisse", "Mobilitaet", "expense", "needs"),
    ("Beduerfnisse", "Energie", "expense", "needs"),
    ("Beduerfnisse", "Versicherungen", "expense", "needs"),
    ("Beduerfnisse", "Gesundheit", "expense", "needs"),
    ("Wuensche", "Restaurant und Cafe", "expense", "wants"),
    ("Wuensche", "Abonnements", "expense", "wants"),
    ("Wuensche", "Kleidung", "expense", "wants"),
)

# Erfundene Haendler/Zahlungspartner - bewusst keine realen Unternehmen.
BEISPIEL_PAYEES: tuple[tuple[str, str | None], ...] = (
    ("Arbeitgeber Muster GmbH", "Gehalt"),
    ("Hausverwaltung Ankerplatz", "Wohnen"),
    ("Supermarkt Nordstern", "Lebensmittel"),
    ("Discounter Sparfuchs", "Lebensmittel"),
    ("Wochenmarkt Musterstadt", "Lebensmittel"),
    ("Baecker Kornfeld", "Lebensmittel"),
    ("Stadtwerke Musterstadt", "Energie"),
    ("Tankstelle Mobilo", "Mobilitaet"),
    ("Verkehrsverbund Musterland", "Mobilitaet"),
    ("Versicherung Sicher und Fair", "Versicherungen"),
    ("Streamdienst Filmwelt", "Abonnements"),
    ("Musikdienst Klangwelt", "Abonnements"),
    ("Trattoria Bella Notte", "Restaurant und Cafe"),
    ("Cafe Milchschaum", "Restaurant und Cafe"),
    ("Kletterhalle Nordwand", "Freizeit"),
    ("Modehaus Fadenschlag", "Kleidung"),
    ("Zahnarztpraxis Lindenhof", "Gesundheit"),
)


@dataclass(frozen=True)
class BuchungsMuster:
    """Eine Buchung innerhalb eines Kalendermonats.

    ``tag`` ist der Tag im Monat; faellt er auf einen Tag nach heute, wird die
    Buchung im laufenden Monat bewusst ausgelassen (keine Buchungen in der
    Zukunft).
    """

    tag: int
    betrag_minor: int
    empfaenger: str
    kategorie: str
    ziel: str | None = None
    notiz: str | None = None


# Monatsrhythmus. Die Reihenfolge ist bewusst nach Tagen sortiert und vorne
# lastig: so zeigt auch ein Lauf am Monatsanfang bereits Gehalt, Miete und
# erste Ausgaben im laufenden Monat.
MONATS_BUCHUNGEN: tuple[BuchungsMuster, ...] = (
    BuchungsMuster(1, 320000, "Arbeitgeber Muster GmbH", "Gehalt", notiz="Monatsgehalt"),
    BuchungsMuster(
        1, -115000, "Hausverwaltung Ankerplatz", "Wohnen", notiz="Miete inklusive Nebenkosten"
    ),
    BuchungsMuster(2, -25000, "Dauerauftrag Notgroschen", "Sparziele"),
    BuchungsMuster(2, -5000, "Dauerauftrag Urlaubskasse", "Sparziele"),
    BuchungsMuster(2, -8950, "Supermarkt Nordstern", "Lebensmittel", notiz="Wocheneinkauf"),
    BuchungsMuster(2, -1299, "Streamdienst Filmwelt", "Abonnements"),
    BuchungsMuster(2, -999, "Musikdienst Klangwelt", "Abonnements"),
    BuchungsMuster(
        5, -4200, "Versicherung Sicher und Fair", "Versicherungen", notiz="Monatsbeitrag"
    ),
    BuchungsMuster(6, -6500, "Tankstelle Mobilo", "Mobilitaet", notiz="Tanken"),
    BuchungsMuster(9, -10450, "Supermarkt Nordstern", "Lebensmittel", notiz="Wocheneinkauf"),
    BuchungsMuster(12, -3900, "Kletterhalle Nordwand", "Freizeit", notiz="Monatskarte Bouldern"),
    BuchungsMuster(15, -12300, "Stadtwerke Musterstadt", "Energie", notiz="Abschlag Strom"),
    BuchungsMuster(16, -8990, "Discounter Sparfuchs", "Lebensmittel", notiz="Wocheneinkauf"),
    BuchungsMuster(18, -3450, "Trattoria Bella Notte", "Restaurant und Cafe"),
    BuchungsMuster(20, -4900, "Verkehrsverbund Musterland", "Mobilitaet", notiz="Monatskarte"),
    BuchungsMuster(23, -11200, "Supermarkt Nordstern", "Lebensmittel", notiz="Grosseinkauf"),
    BuchungsMuster(24, -2790, "Cafe Milchschaum", "Restaurant und Cafe"),
)

# Unregelmaessige Posten: (Modul, Rest, Muster) - greift, wenn der Monatsindex
# i (0 = aeltester Monat) die Bedingung i % Modul == Rest erfuellt. Der
# Vormonat (i = ANZAHL_MONATE - 2) bleibt dadurch bewusst frei von Kleidung
# und Gesundheit, damit dort ein positiver Uebertrag entsteht.
PERIODISCHE_BUCHUNGEN: tuple[tuple[int, int, BuchungsMuster], ...] = (
    (3, 0, BuchungsMuster(17, -2400, "Zahnarztpraxis Lindenhof", "Gesundheit")),
    (4, 0, BuchungsMuster(26, -7900, "Modehaus Fadenschlag", "Kleidung")),
    (
        3,
        1,
        BuchungsMuster(
            27,
            18000,
            "Nachbarschaftshilfe Musterstadt",
            "Nebeneinkuenfte",
            notiz="Honorar Nebentaetigkeit",
        ),
    ),
)

# Nur im laufenden Monat: eine Jahrespraemie, die das Monatsbudget der
# Kategorie "Versicherungen" deutlich sprengt - das ist der geforderte
# ueberschrittene Budgettopf im Standardmonat der Budgetansicht.
LAUFENDER_MONAT_EXTRA: tuple[BuchungsMuster, ...] = (
    BuchungsMuster(
        1,
        -48000,
        "Versicherung Sicher und Fair",
        "Versicherungen",
        notiz="Jahresbeitrag Hausrat und Haftpflicht",
    ),
)

# Einkaeufe der beiden Essensplan-Wochen: (Wochen zurueck, Tage ab Montag,
# Betrag, Haendler). Diese Buchungen werden spaeter mit den Einkaufslisten
# verknuepft und liefern die Istkosten der Woche.
WOCHEN_EINKAEUFE: tuple[tuple[int, int, int, str], ...] = (
    (0, 1, -4950, "Supermarkt Nordstern"),
    (0, 3, -4900, "Wochenmarkt Musterstadt"),
    (1, 1, -6820, "Supermarkt Nordstern"),
    (1, 4, -6600, "Discounter Sparfuchs"),
)

# Sparzieleinzahlungen liegen auf dem SPARKONTO und sind dort positiv - genau
# so leitet die API den Sparzielstand ab (goal_service.fetch_goal_deposits_by_goal:
# starting_amount_minor plus Summe der zugeordneten Splits). Ein negativer
# Ausgabesplit wuerde den Stand senken statt ihn zu erhoehen.
# Die Gegenbuchung auf dem Girokonto steht oben im Monatsrhythmus und traegt
# die Kategorie "Sparziele" - sie fuellt den Spar-Anteil der 50/30/20-Auswertung.
# Das Sparkonto ist bewusst off-budget, deshalb verfaelschen diese Zufluesse
# die Plan/Ist-Rechnung nicht.
# (Tag im Monat, Betrag, Empfaenger, Sparziel)
SPARKONTO_EINZAHLUNGEN: tuple[tuple[int, int, str, str], ...] = (
    (2, 25000, "Einzahlung Notgroschen", ZIEL_NOTGROSCHEN),
    (2, 5000, "Einzahlung Urlaubskasse", ZIEL_URLAUB),
)

# Bewusst OHNE Kategorie gebuchte Positionen. Sie erscheinen dadurch als
# "unklassifiziert" in der Review-Inbox - der haeufigste offene Vorschlag dort.
# (Tage zurueck, Betrag, Empfaenger)
UNKLASSIFIZIERTE_BUCHUNGEN: tuple[tuple[int, int, str], ...] = (
    (8, -4790, "Onlinehandel Kistenweise"),
    (9, -2350, "Parkhaus Zentrum"),
    (10, 9900, "Erstattung Reisekosten"),
)

# Bargeldkasse: kleine Betraege, die anschliessend abgestimmt (reconciled)
# werden. Bewusst ein eigenes Konto, damit die Abstimmung nicht den Status der
# echten Bestandsbuchungen des Nutzers veraendert.
KASSEN_BUCHUNGEN: tuple[tuple[int, int, str, str], ...] = (
    (6, -1250, "Baecker Kornfeld", "Lebensmittel"),
    (4, -980, "Kiosk am Park", "Lebensmittel"),
    (2, -3400, "Wochenmarkt Musterstadt", "Lebensmittel"),
)

KASSE_ANFANGSSALDO = 20000
GIRO_ANFANGSSALDO = 250000
SPAR_ANFANGSSALDO = 480000

ANZAHL_MONATE = 12

# Monatsplan je Kategorie (Kategoriename -> (planned_minor, rollover_rule)).
BUDGETPLAN: tuple[tuple[str, int, str], ...] = (
    ("Gehalt", 320000, "none"),
    ("Nebeneinkuenfte", 6000, "none"),
    ("Wohnen", 115000, "none"),
    ("Energie", 13000, "none"),
    ("Lebensmittel", 42000, "none"),
    ("Mobilitaet", 12000, "none"),
    ("Versicherungen", 4500, "none"),
    ("Gesundheit", 3000, "none"),
    ("Restaurant und Cafe", 8000, "accumulate"),
    ("Abonnements", 2500, "none"),
    ("Kleidung", 8000, "accumulate"),
    ("Freizeit", 6000, "none"),
    ("Sparziele", 30000, "none"),
)

# Vermoegen und Schulden: (Name, type, asset_class, Wert, Zins, Rate)
VERMOEGEN: tuple[tuple[str, str, str, int, float, int], ...] = (
    ("Eigentumswohnung Musterstadt" + DEMO, "asset", "real_estate", 28500000, 0.0, 0),
    ("Wertpapierdepot Weitblick" + DEMO, "asset", "depot", 1420000, 0.0, 0),
    ("Immobilienkredit Ankerplatz" + DEMO, "debt", "loan", 19800000, 3.4, 98000),
    ("Autokredit Mobilo" + DEMO, "debt", "credit", 465000, 5.9, 21000),
)

# Bewertungshistorie des Depots (Monate zurueck -> Wert). Wird direkt per
# psycopg geschrieben, siehe Begruendung in seed_vermoegen().
DEPOT_HISTORIE: tuple[tuple[int, int], ...] = (
    (6, 1180000),
    (5, 1205000),
    (4, 1244000),
    (3, 1219000),
    (2, 1301000),
    (1, 1365000),
)

# Essensplan: Gerichte je Wochentag (Montag = 0).
GERICHTE: tuple[tuple[str, int], ...] = (
    ("Linsensuppe mit Brot", 850),
    ("Gemuesepfanne mit Reis", 920),
    ("Kartoffelgratin", 1080),
    ("Nudeln mit Tomatensauce", 640),
    ("Ofengemuese mit Feta", 1150),
    ("Selbstgemachte Pizza", 1340),
    ("Sonntagsbraten mit Klossen", 1890),
)

EINKAUFSARTIKEL: tuple[tuple[str, str, str, int], ...] = (
    ("Kartoffeln", "Obst und Gemuese", "kg", 320),
    ("Zwiebeln", "Obst und Gemuese", "kg", 180),
    ("Aepfel", "Obst und Gemuese", "kg", 290),
    ("Vollmilch", "Molkerei", "l", 130),
    ("Butter", "Molkerei", "Stueck", 240),
    ("Feta", "Molkerei", "Stueck", 210),
    ("Vollkornbrot", "Backwaren", "Stueck", 350),
    ("Nudeln", "Vorrat", "Packung", 190),
    ("Passierte Tomaten", "Vorrat", "Packung", 150),
    ("Rote Linsen", "Vorrat", "Packung", 260),
)

# Fixkosten als Vertraege. (Name, Betrag, Frequenz, Tage bis faellig)
FIXKOSTEN: tuple[tuple[str, int, str, int], ...] = (
    ("Streamdienst Filmwelt" + DEMO, -1299, "monthly", 3),
    ("Stadtwerke Musterstadt" + DEMO, -12300, "monthly", 13),
    ("Versicherung Sicher und Fair" + DEMO, -12600, "quarterly", 46),
    ("Hausverwaltung Ankerplatz" + DEMO, -115000, "monthly", 30),
)

ABONNEMENTS: tuple[tuple[str, str, int, int, int, int], ...] = (
    ("Streamdienst Filmwelt" + DEMO, "Familie", 1299, 15588, 30, 34),
    ("Musikdienst Klangwelt" + DEMO, "Duo", 999, 11988, 30, 12),
    ("Cloudspeicher Datenhort" + DEMO, "200 GB", 299, 3588, 14, 76),
)

# Klassifikationsregeln:
# (Name, match_payee_raw, Zielkategorie, Zielzahlungspartner, Transfer setzen)
REGELN: tuple[tuple[str, str, str | None, str | None, bool], ...] = (
    (
        "Supermarkt Nordstern zu Lebensmittel" + DEMO,
        "Nordstern",
        "Lebensmittel",
        "Supermarkt Nordstern",
        False,
    ),
    (
        "Discounter Sparfuchs zu Lebensmittel" + DEMO,
        "Sparfuchs",
        "Lebensmittel",
        "Discounter Sparfuchs",
        False,
    ),
    (
        "Tankstelle Mobilo zu Mobilitaet" + DEMO,
        "Mobilo",
        "Mobilitaet",
        "Tankstelle Mobilo",
        False,
    ),
    (
        "Arbeitgeber zu Gehalt" + DEMO,
        "Arbeitgeber Muster",
        "Gehalt",
        "Arbeitgeber Muster GmbH",
        False,
    ),
    ("Uebertraege als Transfer markieren" + DEMO, "Uebertrag", None, None, True),
)

IMPORT_DATEINAME = "beispielbank-auszug-demo.csv"
MAPPING_PROFIL = "Beispielbank CSV" + DEMO

# Muster fuer --cleanup-test-data. Nur Haushalte, deren Name EXAKT auf eines
# dieser (Postgres-)Regexe passt, kommen ueberhaupt in Frage. Alles andere
# bleibt unangetastet.
TESTHAUSHALT_MUSTER: tuple[tuple[str, str], ...] = (
    ("Phasen-Testhaushalte", r"^Phase-[0-9]+-Testhaushalt$"),
    ("Budget-Referenzhaushalte", r"^Budget-Referenzhaushalt$"),
    ("Import-Testhaushalte", r"^Import-Test-Haushalt$"),
    ("KI-Lauf-Testhaushalte", r"^KI-Lauf-Testhaushalt$"),
    ("RLS-Testhaushalte", r"^RLS-Testhaushalt( [A-Z])?$"),
    ("Rollen-Testhaushalte", r"^Rollen-Haushalt$"),
    ("Owner-Testhaushalte", r"^owner[0-9]*-Haushalt$"),
    ("Generische A/B-Testhaushalte", r"^Haushalt [A-Z]$"),
    ("Gegenhaushalte aus Zugriffstests", r"^(Anderer Haushalt|Fremder Haushalt|Fremdhaushalt)$"),
    ("Default-Haushalte aus Onboarding-Tests", r"^Zuhause$"),
    ("E2E-/Live-Testhaushalte", r"^(E2E|Live-Test|LM-Check)$"),
)


# ---------------------------------------------------------------------------
# Fehler und Protokoll
# ---------------------------------------------------------------------------


class SeedError(RuntimeError):
    """Abbruchgrund, der dem Aufrufer verstaendlich gemeldet wird."""


class ApiError(SeedError):
    """Die API hat einen Aufruf abgelehnt."""

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status_code}: {body[:500]}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body


@dataclass
class Report:
    """Sammelt, was angelegt bzw. uebersprungen wurde, und wo die API
    nicht durchgelassen hat."""

    angelegt: dict[str, int] = field(default_factory=dict)
    uebersprungen: dict[str, int] = field(default_factory=dict)
    api_luecken: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    def add(self, bereich: str, anzahl: int = 1) -> None:
        self.angelegt[bereich] = self.angelegt.get(bereich, 0) + anzahl

    def skip(self, bereich: str, anzahl: int = 1) -> None:
        self.uebersprungen[bereich] = self.uebersprungen.get(bereich, 0) + anzahl

    def luecke(self, text: str) -> None:
        if text not in self.api_luecken:
            self.api_luecken.append(text)

    def hinweis(self, text: str) -> None:
        if text not in self.hinweise:
            self.hinweise.append(text)


# ---------------------------------------------------------------------------
# API-Zugriff
# ---------------------------------------------------------------------------


class ApiClient:
    """Duenner Wrapper um httpx mit Bearer-Token und X-Household-Id."""

    def __init__(self, base_url: str, token: str, report: Report) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._report = report
        self.household_id: str | None = None
        self._client = httpx.Client(timeout=120.0)

    def close(self) -> None:
        self._client.close()

    def _headers(self, mit_haushalt: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if mit_haushalt:
            if self.household_id is None:
                raise SeedError("Haushaltskontext ist noch nicht gesetzt.")
            headers["X-Household-Id"] = self.household_id
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        mit_haushalt: bool = True,
    ) -> Any:
        url = f"{self._base_url}/v1{path}"
        response = self._client.request(
            method,
            url,
            json=json_body,
            params=params,
            files=files,
            data=data,
            headers=self._headers(mit_haushalt),
        )
        if response.status_code >= 400:
            raise ApiError(method, path, response.status_code, response.text)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)


def supabase_login(supabase_url: str, anon_key: str, email: str, password: str) -> str:
    """Holt ein Zugriffstoken per Passwort-Login - derselbe Weg, den auch die
    Web-App geht."""
    url = f"{supabase_url.rstrip('/')}/auth/v1/token"
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            url,
            params={"grant_type": "password"},
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
    if response.status_code >= 400:
        raise SeedError(
            "Supabase-Login fehlgeschlagen "
            f"(HTTP {response.status_code}): {response.text[:300]}\n"
            "Pruefe SEED_EMAIL/SEED_PASSWORD und ob Supabase unter "
            f"{supabase_url} laeuft."
        )
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise SeedError("Supabase-Login lieferte kein access_token.")
    return token


# ---------------------------------------------------------------------------
# Datums- und Betragshilfen
# ---------------------------------------------------------------------------


def monatsanfang(basis: date, monate_zurueck: int) -> date:
    """Erster Tag des Monats, der ``monate_zurueck`` vor ``basis`` liegt."""
    gesamt = basis.year * 12 + (basis.month - 1) - monate_zurueck
    return date(gesamt // 12, gesamt % 12 + 1, 1)


def tag_im_monat(monatsstart: date, tag: int) -> date:
    """Datum im Monat, auf das Monatsende begrenzt (31. im Februar -> 28./29.)."""
    letzter = calendar.monthrange(monatsstart.year, monatsstart.month)[1]
    return date(monatsstart.year, monatsstart.month, min(tag, letzter))


def wochenstart(basis: date, wochen_zurueck: int) -> date:
    """Montag der Woche, die ``wochen_zurueck`` vor der Woche von ``basis`` liegt."""
    return basis - timedelta(days=basis.weekday()) - timedelta(weeks=wochen_zurueck)


def auf_euro_runden(minor: int) -> int:
    """Rundet einen Minor-Betrag auf volle Euro (fuer erzeugte Wunschpreise)."""
    return max(int(round(minor / 100.0)) * 100, 100)


def euro(minor: int) -> str:
    return f"{minor / 100:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    api_url: str
    supabase_url: str
    anon_key: str
    dsn: str
    email: str
    password: str
    household_id: str | None
    household_name: str | None
    neuer_haushalt: str | None


def load_config(args: argparse.Namespace) -> Config:
    email = os.environ.get("SEED_EMAIL", "").strip()
    password = os.environ.get("SEED_PASSWORD", "")
    if not email or not password:
        raise SeedError(
            "SEED_EMAIL und SEED_PASSWORD muessen gesetzt sein.\n"
            "PowerShell:\n"
            '  $env:SEED_EMAIL = "deine.adresse@example.org"\n'
            '  $env:SEED_PASSWORD = "..."\n'
            "Die Zugangsdaten werden bewusst nur aus der Umgebung gelesen und "
            "nie im Repository abgelegt."
        )
    return Config(
        api_url=os.environ.get("SEED_API_URL", DEFAULT_API_URL),
        supabase_url=os.environ.get("SEED_SUPABASE_URL", DEFAULT_SUPABASE_URL),
        anon_key=os.environ.get("SEED_SUPABASE_ANON_KEY", DEFAULT_SUPABASE_ANON_KEY),
        dsn=os.environ.get("SEED_DATABASE_URL", DEFAULT_DSN),
        email=email,
        password=password,
        household_id=args.household_id or os.environ.get("SEED_HOUSEHOLD_ID") or None,
        household_name=args.household_name or os.environ.get("SEED_HOUSEHOLD_NAME") or None,
        neuer_haushalt=args.new_household,
    )


def resolve_household(api: ApiClient, cfg: Config, report: Report) -> tuple[str, str]:
    """Waehlt den Zielhaushalt: explizite ID, expliziter Name, neu angelegt
    oder - wenn der Nutzer genau einen Haushalt hat - dieser eine."""
    if cfg.neuer_haushalt is not None:
        neu = api.post(
            "/households", json_body={"name": cfg.neuer_haushalt}, mit_haushalt=False
        )
        report.add("Haushalte (neu angelegt)")
        return str(neu["id"]), str(neu["name"])

    haushalte: list[dict[str, Any]] = api.get("/households", mit_haushalt=False)
    if cfg.household_id:
        for h in haushalte:
            if str(h["id"]) == cfg.household_id:
                return str(h["id"]), str(h["name"])
        raise SeedError(
            f"Haushalt {cfg.household_id} gehoert nicht zu {cfg.email} "
            "(oder existiert nicht)."
        )
    if cfg.household_name:
        treffer = [h for h in haushalte if str(h["name"]) == cfg.household_name]
        if len(treffer) != 1:
            raise SeedError(
                f"Haushaltsname {cfg.household_name!r} passt auf {len(treffer)} Haushalte. "
                "Bitte --household-id verwenden."
            )
        return str(treffer[0]["id"]), str(treffer[0]["name"])
    if not haushalte:
        raise SeedError(
            "Der Nutzer ist in keinem Haushalt Mitglied. Mit --new-household NAME "
            "einen frischen Haushalt anlegen."
        )
    if len(haushalte) > 1:
        auswahl = ", ".join(f"{h['name']} ({h['id']})" for h in haushalte)
        raise SeedError(
            "Der Nutzer ist in mehreren Haushalten Mitglied - bitte per "
            f"--household-id waehlen. Verfuegbar: {auswahl}"
        )
    return str(haushalte[0]["id"]), str(haushalte[0]["name"])


# ---------------------------------------------------------------------------
# Seed-Schritte
# ---------------------------------------------------------------------------


def seed_kategorien(conn: psycopg.Connection[Any], household_id: str, report: Report) -> None:
    """Legt die zusaetzlichen Kategorien an.

    Direkt per psycopg: die API hat fuer Kategorien und Kategoriegruppen nur
    GET /v1/categories, keinen Schreibendpunkt. Das ist eine echte Luecke -
    ueber die App laesst sich derzeit keine eigene Kategorie anlegen.
    """
    report.luecke(
        "Kategorien/Kategoriegruppen: kein Schreibendpunkt (nur GET /v1/categories) - "
        "per psycopg angelegt."
    )
    with conn.cursor() as cur:
        cur.execute(
            "select id, name from category_groups where household_id = %s", (household_id,)
        )
        gruppen = {str(name): gid for gid, name in cur.fetchall()}

        for gruppe, name, default_type, bucket in NEUE_KATEGORIEN:
            gruppen_id = gruppen.get(gruppe)
            if gruppen_id is None:
                report.hinweis(
                    f"Kategoriegruppe {gruppe!r} fehlt im Haushalt - Kategorie "
                    f"{name!r} wurde ausgelassen."
                )
                continue
            cur.execute(
                "insert into categories "
                "(household_id, group_id, name, default_type, budget_bucket, sort_order) "
                "values (%s, %s, %s, %s, %s, 50) "
                "on conflict (household_id, group_id, name) do nothing returning id",
                (household_id, gruppen_id, name, default_type, bucket),
            )
            if cur.fetchone() is None:
                report.skip("Kategorien")
            else:
                report.add("Kategorien")
    conn.commit()


def seed_payees(conn: psycopg.Connection[Any], household_id: str, report: Report) -> None:
    """Legt Zahlungspartner an.

    Direkt per psycopg: es gibt keinen Endpunkt fuer payees - weder Anlegen
    noch Auflisten. Auch das ist eine echte Luecke.
    """
    report.luecke("Zahlungspartner (payees): kein Endpunkt vorhanden - per psycopg angelegt.")
    with conn.cursor() as cur:
        cur.execute(
            "select c.name, c.id from categories c where c.household_id = %s", (household_id,)
        )
        kategorien = {str(name): cid for name, cid in cur.fetchall()}
        for name, kategorie in BEISPIEL_PAYEES:
            cur.execute(
                "insert into payees (household_id, name, default_category_id) "
                "values (%s, %s, %s) on conflict (household_id, name) do nothing returning id",
                (household_id, name, kategorien.get(kategorie or "")),
            )
            if cur.fetchone() is None:
                report.skip("Zahlungspartner")
            else:
                report.add("Zahlungspartner")
    conn.commit()


def seed_konten(api: ApiClient, heute: date, report: Report) -> dict[str, str]:
    """Legt die drei Demo-Konten an (bestehende Konten bleiben unberuehrt)."""
    vorhanden: dict[str, str] = {
        str(k["name"]): str(k["id"]) for k in api.get("/accounts")
    }
    start = monatsanfang(heute, ANZAHL_MONATE - 1)
    wunsch: tuple[tuple[str, str, str, bool, int], ...] = (
        (KONTO_GIRO, "checking", "Beispielbank Musterstadt", True, GIRO_ANFANGSSALDO),
        # Sparkonto bewusst ausserhalb des Budgets: seine Zinsgutschrift soll
        # die Plan/Ist-Rechnung nicht verfaelschen, im Nettovermoegen zaehlt es
        # trotzdem mit (fetch_net_worth_report beruecksichtigt alle Konten).
        (KONTO_SPAR, "savings", "Beispielbank Musterstadt", False, SPAR_ANFANGSSALDO),
        (KONTO_KASSE, "cash", "Haushaltskasse", True, KASSE_ANFANGSSALDO),
    )
    konten: dict[str, str] = {}
    for name, typ, institut, on_budget, saldo in wunsch:
        if name in vorhanden:
            konten[name] = vorhanden[name]
            report.skip("Konten")
            continue
        angelegt = api.post(
            "/accounts",
            json_body={
                "name": name,
                "account_type": typ,
                "institution": institut,
                "currency": "EUR",
                "on_budget": on_budget,
                "opening_balance_minor": saldo,
                "opening_balance_date": start.isoformat(),
            },
        )
        konten[name] = str(angelegt["id"])
        report.add("Konten")
    return konten


def seed_sparziele(api: ApiClient, heute: date, report: Report) -> dict[str, str]:
    """Drei Sparziele: fast erreicht, am Anfang, vollstaendig gedeckt."""
    vorhanden = {str(z["name"]): str(z["id"]) for z in api.get("/goals")}
    wunsch: tuple[tuple[str, int, int, date | None], ...] = (
        # Zieldatum bewusst in der Zukunft, damit monthly_required sinnvoll ist.
        (ZIEL_NOTGROSCHEN, 500000, 150000, monatsanfang(heute, -4)),
        (ZIEL_URLAUB, 240000, 0, monatsanfang(heute, -11)),
        (ZIEL_LASTENRAD, 180000, 180000, monatsanfang(heute, -2)),
    )
    ziele: dict[str, str] = {}
    for name, ziel_minor, start_minor, zieldatum in wunsch:
        if name in vorhanden:
            ziele[name] = vorhanden[name]
            report.skip("Sparziele")
            continue
        angelegt = api.post(
            "/goals",
            json_body={
                "name": name,
                "target_amount_minor": ziel_minor,
                "starting_amount_minor": start_minor,
                "target_date": zieldatum.isoformat() if zieldatum else None,
            },
        )
        ziele[name] = str(angelegt["id"])
        report.add("Sparziele")
    return ziele


def lade_kategorien(api: ApiClient) -> dict[str, str]:
    kategorien: dict[str, str] = {}
    for gruppe in api.get("/categories"):
        for kategorie in gruppe["categories"]:
            kategorien[str(kategorie["name"])] = str(kategorie["id"])
    return kategorien


def vorhandene_buchungsschluessel(
    conn: psycopg.Connection[Any], household_id: str
) -> set[tuple[str, str, str, int]]:
    """Natuerlicher Schluessel je Buchung: (Konto, Jahr-Monat, Empfaenger, Betrag).

    Damit erkennt ein zweiter Lauf seine eigenen Buchungen wieder, ohne dass
    ein technisches Merkmal in die Notiz geschrieben werden muesste. Der Seed
    stellt sicher, dass diese Kombination innerhalb seiner Daten eindeutig ist
    (siehe pruefe_schluessel_eindeutig).
    """
    with conn.cursor() as cur:
        cur.execute(
            "select t.account_id, to_char(t.booked_date, 'YYYY-MM'), "
            "coalesce(t.payee_label, ''), t.amount_minor "
            "from transactions t where t.household_id = %s and not t.is_archived",
            (household_id,),
        )
        return {
            (str(konto), str(monat), str(payee), int(betrag))
            for konto, monat, payee, betrag in cur.fetchall()
        }


@dataclass(frozen=True)
class GeplanteBuchung:
    konto_id: str
    datum: date
    betrag_minor: int
    empfaenger: str
    kategorie_id: str | None
    ziel_id: str | None
    notiz: str | None


def baue_buchungsplan(
    heute: date,
    konten: dict[str, str],
    kategorien: dict[str, str],
    ziele: dict[str, str],
) -> list[GeplanteBuchung]:
    """Baut die vollstaendige Buchungsliste der letzten ANZAHL_MONATE Monate."""
    giro = konten[KONTO_GIRO]
    spar = konten[KONTO_SPAR]
    kasse = konten[KONTO_KASSE]
    sparziele_kategorie = kategorien.get("Sparziele")
    plan: list[GeplanteBuchung] = []

    def hinzu(muster: BuchungsMuster, monatsstart: date) -> None:
        datum = tag_im_monat(monatsstart, muster.tag)
        if datum > heute:
            # Keine Buchungen in der Zukunft: der laufende Monat ist bewusst
            # nur bis heute befuellt.
            return
        plan.append(
            GeplanteBuchung(
                konto_id=giro,
                datum=datum,
                betrag_minor=muster.betrag_minor,
                empfaenger=muster.empfaenger,
                kategorie_id=kategorien.get(muster.kategorie),
                ziel_id=ziele.get(muster.ziel) if muster.ziel else None,
                notiz=muster.notiz,
            )
        )

    for i in range(ANZAHL_MONATE):
        monatsstart = monatsanfang(heute, ANZAHL_MONATE - 1 - i)
        for muster in MONATS_BUCHUNGEN:
            hinzu(muster, monatsstart)
        for modul, rest, muster in PERIODISCHE_BUCHUNGEN:
            if i % modul == rest:
                hinzu(muster, monatsstart)
        if i == ANZAHL_MONATE - 1:
            for muster in LAUFENDER_MONAT_EXTRA:
                hinzu(muster, monatsstart)
        for tag, betrag, empfaenger, ziel in SPARKONTO_EINZAHLUNGEN:
            datum = tag_im_monat(monatsstart, tag)
            if datum > heute:
                continue
            plan.append(
                GeplanteBuchung(
                    konto_id=spar,
                    datum=datum,
                    betrag_minor=betrag,
                    empfaenger=empfaenger,
                    kategorie_id=sparziele_kategorie,
                    ziel_id=ziele.get(ziel),
                    notiz="Zufluss aus dem Dauerauftrag",
                )
            )

    # Wocheneinkaeufe der beiden Essensplan-Wochen
    for wochen_zurueck, tag_offset, betrag, haendler in WOCHEN_EINKAEUFE:
        datum = wochenstart(heute, wochen_zurueck) + timedelta(days=tag_offset)
        if datum > heute:
            continue
        plan.append(
            GeplanteBuchung(
                konto_id=giro,
                datum=datum,
                betrag_minor=betrag,
                empfaenger=haendler,
                kategorie_id=kategorien.get("Lebensmittel"),
                ziel_id=None,
                notiz="Wocheneinkauf Essensplan",
            )
        )

    # Ohne Kategorie: Nachschub fuer die Review-Inbox
    for tage_zurueck, betrag, haendler in UNKLASSIFIZIERTE_BUCHUNGEN:
        plan.append(
            GeplanteBuchung(
                konto_id=giro,
                datum=heute - timedelta(days=tage_zurueck),
                betrag_minor=betrag,
                empfaenger=haendler,
                kategorie_id=None,
                ziel_id=None,
                notiz="Noch keiner Kategorie zugeordnet",
            )
        )

    # Bargeldkasse (wird anschliessend abgestimmt)
    for tage_zurueck, betrag, haendler, kategorie in KASSEN_BUCHUNGEN:
        plan.append(
            GeplanteBuchung(
                konto_id=kasse,
                datum=heute - timedelta(days=tage_zurueck),
                betrag_minor=betrag,
                empfaenger=haendler,
                kategorie_id=kategorien.get(kategorie),
                ziel_id=None,
                notiz="Barzahlung",
            )
        )

    return plan


def pruefe_schluessel_eindeutig(plan: list[GeplanteBuchung]) -> None:
    """Stellt sicher, dass der Idempotenzschluessel innerhalb des Seeds
    eindeutig ist - sonst wuerde ein zweiter Lauf still Buchungen auslassen."""
    gesehen: set[tuple[str, str, str, int]] = set()
    for buchung in plan:
        schluessel = (
            buchung.konto_id,
            f"{buchung.datum.year:04d}-{buchung.datum.month:02d}",
            buchung.empfaenger,
            buchung.betrag_minor,
        )
        if schluessel in gesehen:
            raise SeedError(
                "Interner Fehler: Buchungsschluessel nicht eindeutig "
                f"({buchung.empfaenger}, {buchung.datum}, {buchung.betrag_minor}). "
                "Betrag oder Empfaenger im Muster anpassen."
            )
        gesehen.add(schluessel)


def seed_buchungen(
    api: ApiClient,
    conn: psycopg.Connection[Any],
    household_id: str,
    plan: list[GeplanteBuchung],
    report: Report,
) -> None:
    bekannt = vorhandene_buchungsschluessel(conn, household_id)
    for buchung in plan:
        schluessel = (
            buchung.konto_id,
            f"{buchung.datum.year:04d}-{buchung.datum.month:02d}",
            buchung.empfaenger,
            buchung.betrag_minor,
        )
        if schluessel in bekannt:
            report.skip("Buchungen")
            continue
        api.post(
            "/transactions",
            json_body={
                "account_id": buchung.konto_id,
                "booked_date": buchung.datum.isoformat(),
                "amount_minor": buchung.betrag_minor,
                "category_id": buchung.kategorie_id,
                "goal_id": buchung.ziel_id,
                "payee_label": buchung.empfaenger,
                "note": buchung.notiz,
            },
        )
        bekannt.add(schluessel)
        report.add("Buchungen")


def seed_splitbuchung(
    api: ApiClient,
    conn: psycopg.Connection[Any],
    household_id: str,
    konten: dict[str, str],
    kategorien: dict[str, str],
    heute: date,
    report: Report,
) -> None:
    """Eine Buchung, die auf zwei Kategorien aufgeteilt ist."""
    giro = konten[KONTO_GIRO]
    datum = heute - timedelta(days=6)
    betrag = -18450
    empfaenger = "Baumarkt Heimwerk"
    schluessel = (giro, f"{datum.year:04d}-{datum.month:02d}", empfaenger, betrag)
    if schluessel in vorhandene_buchungsschluessel(conn, household_id):
        report.skip("Splitbuchungen")
        return

    angelegt = api.post(
        "/transactions",
        json_body={
            "account_id": giro,
            "booked_date": datum.isoformat(),
            "amount_minor": betrag,
            "payee_label": empfaenger,
            "note": "Regal und Farbe, aufgeteilt",
        },
    )
    api.put(
        f"/transactions/{angelegt['id']}/splits",
        json_body={
            "amount_minor": betrag,
            "splits": [
                {
                    "category_id": kategorien.get("Wohnen"),
                    "amount_minor": -12000,
                    "note": "Regal Arbeitszimmer",
                },
                {
                    "category_id": kategorien.get("Freizeit"),
                    "amount_minor": -6450,
                    "note": "Bastelmaterial",
                },
            ],
        },
    )
    report.add("Splitbuchungen")


def seed_transfer(
    api: ApiClient,
    conn: psycopg.Connection[Any],
    household_id: str,
    konten: dict[str, str],
    kategorien: dict[str, str],
    heute: date,
    report: Report,
) -> None:
    """Transferpaar zwischen Girokonto und Sparkonto plus eine normale
    Sparkonto-Buchung (Gegenstueck fuer den Transfer-Kandidaten im Import)."""
    giro = konten[KONTO_GIRO]
    spar = konten[KONTO_SPAR]
    bekannt = vorhandene_buchungsschluessel(conn, household_id)
    transfer_kat = kategorien.get("Interne Uebertragung")

    datum = heute - timedelta(days=5)
    monat = f"{datum.year:04d}-{datum.month:02d}"
    raus_key = (giro, monat, "Uebertrag auf Sparkonto", -50000)
    rein_key = (spar, monat, "Uebertrag von Girokonto", 50000)

    if raus_key not in bekannt and rein_key not in bekannt:
        raus = api.post(
            "/transactions",
            json_body={
                "account_id": giro,
                "booked_date": datum.isoformat(),
                "amount_minor": -50000,
                "category_id": transfer_kat,
                "payee_label": "Uebertrag auf Sparkonto",
                "note": "Interner Uebertrag",
            },
        )
        rein = api.post(
            "/transactions",
            json_body={
                "account_id": spar,
                "booked_date": datum.isoformat(),
                "amount_minor": 50000,
                "category_id": transfer_kat,
                "payee_label": "Uebertrag von Girokonto",
                "note": "Interner Uebertrag",
            },
        )
        api.post(
            "/transfers/link",
            json_body={
                "outgoing_transaction_id": raus["id"],
                "incoming_transaction_id": rein["id"],
            },
        )
        report.add("Transferpaare")
    else:
        report.skip("Transferpaare")

    zins_datum = heute - timedelta(days=2)
    zins_key = (
        spar,
        f"{zins_datum.year:04d}-{zins_datum.month:02d}",
        "Zinsgutschrift Sparkonto",
        32000,
    )
    if zins_key in bekannt:
        report.skip("Buchungen")
        return
    api.post(
        "/transactions",
        json_body={
            "account_id": spar,
            "booked_date": zins_datum.isoformat(),
            "amount_minor": 32000,
            "category_id": kategorien.get("Nebeneinkuenfte"),
            "payee_label": "Zinsgutschrift Sparkonto",
            "note": "Quartalszinsen",
        },
    )
    report.add("Buchungen")


def seed_abstimmung(
    api: ApiClient, konten: dict[str, str], heute: date, report: Report
) -> None:
    """Stimmt die Bargeldkasse ab: bei Differenz 0 werden ihre Buchungen auf
    'reconciled' gesetzt."""
    kasse = konten[KONTO_KASSE]
    vorhanden = api.get(f"/accounts/{kasse}/reconciliations")
    if any(str(r["as_of_date"]) == heute.isoformat() for r in vorhanden):
        report.skip("Kontoabstimmungen")
        return
    erwartet = KASSE_ANFANGSSALDO + sum(betrag for _, betrag, _, _ in KASSEN_BUCHUNGEN)
    ergebnis = api.post(
        f"/accounts/{kasse}/reconcile",
        json_body={"as_of_date": heute.isoformat(), "statement_balance_minor": erwartet},
    )
    report.add("Kontoabstimmungen")
    if ergebnis["status"] != "completed":
        report.hinweis(
            "Kontoabstimmung der Haushaltskasse ergab eine Differenz von "
            f"{euro(int(ergebnis['difference_minor']))} - die Buchungen wurden "
            "deshalb nicht als abgestimmt markiert."
        )


def seed_budgets(
    api: ApiClient,
    kategorien: dict[str, str],
    heute: date,
    report: Report,
    monat_abschliessen: bool,
) -> None:
    """Setzt Monatsplaene fuer alle Seed-Monate und schliesst den Vormonat ab,
    damit im laufenden Monat ein positiver Uebertrag sichtbar wird."""
    for i in range(ANZAHL_MONATE):
        monatsstart = monatsanfang(heute, ANZAHL_MONATE - 1 - i)
        zusammenfassung = api.get(f"/budgets/{monatsstart.year}/{monatsstart.month}")
        if zusammenfassung["status"] == "closed":
            report.skip("Budgetplaene gesetzt")
            continue
        allocations = [
            {
                "category_id": kategorien[name],
                "planned_minor": geplant,
                "rollover_rule": regel,
            }
            for name, geplant, regel in BUDGETPLAN
            if name in kategorien
        ]
        api.put(
            f"/budgets/{monatsstart.year}/{monatsstart.month}/allocations",
            json_body={"allocations": allocations},
        )
        report.add("Budgetplaene gesetzt")

    vormonat = monatsanfang(heute, 1)
    if not monat_abschliessen:
        report.hinweis(
            "Monatsabschluss uebersprungen (--no-close-month). Ohne ihn entsteht "
            "kein Budgetuebertrag im laufenden Monat."
        )
        return
    status = api.get(f"/budgets/{vormonat.year}/{vormonat.month}")["status"]
    if status == "closed":
        report.skip("Monatsabschluesse")
        return
    ergebnis = api.post(f"/budgets/{vormonat.year}/{vormonat.month}/close")
    report.add("Monatsabschluesse")
    report.add("Budgetuebertraege", int(ergebnis["rollovers_created"]))
    report.hinweis(
        f"Der Vormonat {vormonat.year}-{vormonat.month:02d} wurde abgeschlossen "
        "(Voraussetzung fuer den Uebertrag im laufenden Monat). Das ist die "
        "einzige Aenderung an Bestandsdaten, die der Seed vornimmt; --no-close-month "
        "verhindert sie."
    )


def seed_wunschliste(
    api: ApiClient, ziele: dict[str, str], heute: date, report: Report
) -> None:
    """Drei Wunschlisteneintraege, die gruen, gelb und rot in der Kaufampel
    ergeben. Die Preise werden aus der tatsaechlichen freien Monatskapazitaet
    abgeleitet, damit die Ampelfarben unabhaengig vom Laufdatum stimmen.

    Die Ampel rechnet (siehe app/services/goal_service.evaluate_traffic_light):
      Kapazitaet = max(freies Monatsbudget, 100 EUR)
      Luecke     = Preis - Sparzielguthaben
      Monate     = ceil((Luecke - Kapazitaet) / Kapazitaet)
      gruen bei Monate <= 0, gelb bei 1-2, sonst rot.
    """
    zusammenfassung = api.get(f"/budgets/{heute.year}/{heute.month}")
    kapazitaet = max(int(zusammenfassung["total_remaining_minor"]), 10000)

    gelb_preis = auf_euro_runden(int(kapazitaet * 2.2))
    if gelb_preis <= kapazitaet:
        gelb_preis = auf_euro_runden(kapazitaet * 2)
    rot_preis = auf_euro_runden(int(kapazitaet * 8))

    wunsch: tuple[tuple[str, int, int, str, str | None], ...] = (
        (
            "E-Lastenrad" + DEMO,
            180000,
            1,
            "Vollstaendig aus dem Sparziel gedeckt - Kaufampel muss gruen sein.",
            ZIEL_LASTENRAD,
        ),
        (
            "Waschmaschine Sparsam" + DEMO,
            gelb_preis,
            2,
            "Aus dem freien Monatsbudget in wenigen Monaten machbar.",
            None,
        ),
        (
            "Familienreise Skandinavien" + DEMO,
            rot_preis,
            3,
            "Deutlich ueber der monatlichen Kapazitaet - noch nicht ratsam.",
            None,
        ),
    )

    vorhanden = {str(w["name"]): w for w in api.get("/wishlist")}
    for name, preis, prioritaet, notiz, ziel in wunsch:
        ziel_id = ziele.get(ziel) if ziel else None
        if name in vorhanden:
            # Preis nachziehen: die freie Kapazitaet aendert sich mit den Daten,
            # die Ampelfarbe soll aber stabil bleiben.
            api.patch(
                f"/wishlist/{vorhanden[name]['id']}",
                json_body={"price_minor": preis, "priority": prioritaet, "note": notiz},
            )
            report.skip("Wunschliste")
            continue
        api.post(
            "/wishlist",
            json_body={
                "name": name,
                "price_minor": preis,
                "priority": prioritaet,
                "note": notiz,
                "goal_id": ziel_id,
            },
        )
        report.add("Wunschliste")

    farben = {str(w["name"]): str(w["buy_traffic_light"]) for w in api.get("/wishlist")}
    fehlend = {"green", "yellow", "red"} - set(farben.values())
    if fehlend:
        report.hinweis(
            "Kaufampel deckt nicht alle Farben ab (fehlend: "
            f"{', '.join(sorted(fehlend))}). Freie Monatskapazitaet: "
            f"{euro(kapazitaet)}."
        )


def seed_vermoegen(
    api: ApiClient, conn: psycopg.Connection[Any], household_id: str, heute: date, report: Report
) -> None:
    """Vermoegenswerte und Schulden inklusive Bewertungshistorie."""
    vorhanden = {str(a["name"]): str(a["id"]) for a in api.get("/assets")}
    ids: dict[str, str] = {}
    for name, typ, klasse, wert, zins, rate in VERMOEGEN:
        if name in vorhanden:
            ids[name] = vorhanden[name]
            report.skip("Vermoegen und Schulden")
            continue
        angelegt = api.post(
            "/assets",
            json_body={
                "name": name,
                "type": typ,
                "asset_class": klasse,
                "current_value_minor": wert,
                "interest_rate_percent": zins,
                "monthly_payment_minor": rate,
            },
        )
        ids[name] = str(angelegt["id"])
        report.add("Vermoegen und Schulden")

    # Bewertungshistorie mit RUECKDATIERTEN Stichtagen. Die API schreibt beim
    # Anlegen und beim Aendern zwar automatisch einen Historienpunkt, aber immer
    # mit valued_on = heute (siehe _record_asset_valuation in
    # app/api/v1/routes/goals.py). Ein Verlauf ueber mehrere Monate laesst sich
    # darueber nicht erzeugen - deshalb hier direkt per psycopg.
    report.luecke(
        "asset_valuations: kein Endpunkt zum Nachtragen eines Stichtags "
        "(valued_on ist immer heute) - Historie per psycopg ergaenzt."
    )
    depot_id = ids.get("Wertpapierdepot Weitblick" + DEMO)
    if depot_id is not None:
        with conn.cursor() as cur:
            for monate_zurueck, wert in DEPOT_HISTORIE:
                stichtag = monatsanfang(heute, monate_zurueck)
                cur.execute(
                    "select 1 from asset_valuations "
                    "where asset_id = %s and valued_on = %s and value_minor = %s",
                    (depot_id, stichtag, wert),
                )
                if cur.fetchone() is not None:
                    report.skip("Bewertungspunkte")
                    continue
                cur.execute(
                    "insert into asset_valuations "
                    "(household_id, asset_id, valued_on, value_minor) values (%s, %s, %s, %s)",
                    (household_id, depot_id, stichtag, wert),
                )
                report.add("Bewertungspunkte")
        conn.commit()


def seed_lebensmittel(
    api: ApiClient, konten: dict[str, str], heute: date, report: Report
) -> None:
    """Zwei Essensplanwochen mit Budget, Mahlzeiten, Einkaufsliste und
    verknuepften Buchungen - eine Woche im Rahmen, eine ueberschritten."""
    wochen: tuple[tuple[int, int, str, str], ...] = (
        (0, 12000, "Laufende Woche, Budget wird eingehalten", "Supermarkt Nordstern"),
        (1, 10000, "Vorwoche, Budget ueberschritten", "Discounter Sparfuchs"),
    )
    vorhandene_wochen = {str(w["week_start"]): str(w["id"]) for w in api.get("/meal-weeks")}
    buchungen: list[dict[str, Any]] = api.get(
        "/transactions",
        params={
            "account_id": konten[KONTO_GIRO],
            "from_date": (wochenstart(heute, 1)).isoformat(),
            "to_date": heute.isoformat(),
            "limit": 200,
        },
    )

    for wochen_zurueck, budget, notiz, laden in wochen:
        montag = wochenstart(heute, wochen_zurueck)
        woche_id = vorhandene_wochen.get(montag.isoformat())
        if woche_id is None:
            woche = api.post(
                "/meal-weeks",
                json_body={
                    "week_start": montag.isoformat(),
                    "budget_minor": budget,
                    "note": notiz + DEMO,
                },
            )
            woche_id = str(woche["id"])
            report.add("Essensplanwochen")
        else:
            report.skip("Essensplanwochen")

        vorhandene_mahlzeiten = {
            (str(m["meal_date"]), str(m["meal_type"]))
            for m in api.get("/meals", params={"meal_week_id": woche_id})
        }
        for tag_index, (gericht, kosten) in enumerate(GERICHTE):
            tag = montag + timedelta(days=tag_index)
            if (tag.isoformat(), "dinner") in vorhandene_mahlzeiten:
                report.skip("Mahlzeiten")
                continue
            api.post(
                "/meals",
                json_body={
                    "meal_week_id": woche_id,
                    "meal_date": tag.isoformat(),
                    "meal_type": "dinner",
                    "dish_name": gericht,
                    "servings": 4,
                    "estimated_cost_minor": kosten,
                },
            )
            report.add("Mahlzeiten")

        listen: list[dict[str, Any]] = api.get(
            "/shopping-lists", params={"meal_week_id": woche_id}
        )
        if listen:
            liste = listen[0]
            report.skip("Einkaufslisten")
        else:
            liste = api.post(
                "/shopping-lists",
                json_body={
                    "meal_week_id": woche_id,
                    "store": laden,
                    "status": "shopping" if wochen_zurueck == 0 else "completed",
                },
            )
            report.add("Einkaufslisten")

        vorhandene_artikel = {
            str(a["name"])
            for a in api.get("/shopping-items", params={"shopping_list_id": liste["id"]})
        }
        for name, gruppe, einheit, preis in EINKAUFSARTIKEL:
            if name in vorhandene_artikel:
                report.skip("Einkaufsartikel")
                continue
            api.post(
                "/shopping-items",
                json_body={
                    "shopping_list_id": liste["id"],
                    "name": name,
                    "category": gruppe,
                    "quantity": "1",
                    "unit": einheit,
                    "planned_price_minor": preis,
                    "is_purchased": wochen_zurueck == 1,
                },
            )
            report.add("Einkaufsartikel")

        bereits_verknuepft = {
            str(t["id"]) for t in liste.get("linked_transactions", [])
        }
        for w_zurueck, tag_offset, betrag, haendler in WOCHEN_EINKAEUFE:
            if w_zurueck != wochen_zurueck:
                continue
            datum = wochenstart(heute, w_zurueck) + timedelta(days=tag_offset)
            treffer = [
                t
                for t in buchungen
                if str(t["booked_date"]) == datum.isoformat()
                and int(t["amount_minor"]) == betrag
                and str(t["payee_label"]) == haendler
            ]
            if not treffer:
                continue
            if str(treffer[0]["id"]) in bereits_verknuepft:
                report.skip("Einkaufsverknuepfungen")
                continue
            try:
                api.post(
                    f"/shopping-lists/{liste['id']}/purchase-links",
                    json_body={"transaction_id": treffer[0]["id"]},
                )
                report.add("Einkaufsverknuepfungen")
            except ApiError as exc:
                if exc.status_code == 409:
                    report.skip("Einkaufsverknuepfungen")
                else:
                    raise

        summary = api.get(f"/meal-weeks/{woche_id}/summary")
        if wochen_zurueck == 1 and int(summary["remaining_vs_budget_minor"]) >= 0:
            report.hinweis(
                "Die Vorwoche im Essensplan liegt nicht ueber Budget "
                f"(Rest {euro(int(summary['remaining_vs_budget_minor']))})."
            )


def seed_regeln(
    api: ApiClient,
    conn: psycopg.Connection[Any],
    household_id: str,
    kategorien: dict[str, str],
    heute: date,
    report: Report,
) -> None:
    """Klassifikationsregeln, Fixkosten und Abonnements."""
    with conn.cursor() as cur:
        cur.execute("select name, id from payees where household_id = %s", (household_id,))
        payees = {str(name): str(pid) for name, pid in cur.fetchall()}

    vorhandene_regeln = {str(r["name"]) for r in api.get("/rules")}
    for name, muster, kategorie, partner, transfer in REGELN:
        if name in vorhandene_regeln:
            report.skip("Klassifikationsregeln")
            continue
        try:
            api.post(
                "/rules",
                json_body={
                    "name": name,
                    "priority": 10,
                    "is_active": True,
                    "match_payee_raw": muster,
                    "action_category_id": kategorien.get(kategorie) if kategorie else None,
                    "action_payee_id": payees.get(partner) if partner else None,
                    "action_set_transfer": transfer,
                    "source": "user",
                },
            )
            report.add("Klassifikationsregeln")
        except ApiError as exc:
            # 409: es gibt bereits eine Regel fuer diesen Haendler und diese
            # Kategorie (partieller Unique-Index aus Migration 0002).
            if exc.status_code == 409:
                report.skip("Klassifikationsregeln")
            else:
                raise

    vorhandene_fixkosten = {str(r.get("note") or "") for r in api.get("/recurring")}
    for notiz, betrag, frequenz, tage in FIXKOSTEN:
        if notiz in vorhandene_fixkosten:
            report.skip("Fixkosten")
            continue
        api.post(
            "/recurring",
            json_body={
                "expected_amount_minor": betrag,
                "frequency": frequenz,
                "next_due_date": (heute + timedelta(days=tage)).isoformat(),
                "tolerance_days": 3,
                "tolerance_amount_minor": 500,
                "is_active": True,
                "note": notiz,
            },
        )
        report.add("Fixkosten")

    vorhandene_abos = {str(a["provider"]) for a in api.get("/subscriptions")}
    for anbieter, tarif, monat, jahr, kuendigung, tage in ABONNEMENTS:
        if anbieter in vorhandene_abos:
            report.skip("Abonnements")
            continue
        api.post(
            "/subscriptions",
            json_body={
                "provider": anbieter,
                "plan_name": tarif,
                "monthly_cost_minor": monat,
                "annual_cost_minor": jahr,
                "cancellation_period_days": kuendigung,
                "next_renewal_date": (heute + timedelta(days=tage)).isoformat(),
                "category_id": kategorien.get("Abonnements"),
                "is_active": True,
                "note": "Beispieldatensatz des Entwicklungs-Seeds",
            },
        )
        report.add("Abonnements")


def baue_import_csv(heute: date) -> bytes:
    """Erzeugt eine Beispiel-CSV, die beim Import genau die drei Zustaende
    erzeugt, die die Review-Inbox zeigen soll:

    * eine moegliche Dublette  - gleicher Betrag wie eine bestehende Buchung
      des Zielkontos, Datum innerhalb von drei Tagen, aber anderer
      Verwendungszweck (also anderer Fingerabdruck),
    * einen Transfer-Kandidaten - Gegenbetrag zu einer Buchung auf dem
      Sparkonto innerhalb von drei Tagen,
    * drei neue Zeilen.

    Siehe worker/imports/dedup.classify_row fuer die Regeln.
    """
    puffer = io.StringIO()
    writer = csv.writer(puffer, delimiter=";", lineterminator="\n")
    writer.writerow(["Datum", "Betrag", "Empfaenger", "Verwendungszweck", "Referenz"])
    zeilen: tuple[tuple[date, int, str, str, str], ...] = (
        (
            heute - timedelta(days=1),
            -8950,
            "SUPERMARKT NORDSTERN FIL 12",
            "Kartenzahlung Lebensmittel",
            "DEMO-DUP-01",
        ),
        (
            heute - timedelta(days=1),
            -32000,
            "UEBERTRAG SPARKONTO",
            "Umbuchung eigenes Konto",
            "DEMO-TRF-01",
        ),
        (
            heute - timedelta(days=2),
            -2199,
            "Buchhandlung Seitenwind",
            "Buchkauf",
            "DEMO-NEU-01",
        ),
        (
            heute - timedelta(days=3),
            -1590,
            "Blumenladen Immergruen",
            "Blumenstrauss",
            "DEMO-NEU-02",
        ),
        (
            heute - timedelta(days=4),
            -6390,
            "Baumarkt Heimwerk",
            "Gartenerde und Werkzeug",
            "DEMO-NEU-03",
        ),
    )
    for datum, betrag, empfaenger, zweck, referenz in zeilen:
        writer.writerow(
            [
                datum.strftime("%d.%m.%Y"),
                f"{betrag / 100:.2f}".replace(".", ","),
                empfaenger,
                zweck,
                referenz,
            ]
        )
    return puffer.getvalue().encode("utf-8")


def seed_import(
    api: ApiClient, konten: dict[str, str], heute: date, report: Report
) -> None:
    """Laedt eine Beispieldatei hoch und laesst sie vom Worker analysieren.

    Der Lauf wird bewusst NICHT uebernommen: nur so bleiben Dublette und
    Transfer-Kandidat als offene Vorschlaege in der Review-Inbox stehen und
    die Importe-Seite zeigt einen Lauf im Status 'ready'.
    """
    laeufe: list[dict[str, Any]] = api.get("/imports")
    if any(str(lauf["filename"]) == IMPORT_DATEINAME for lauf in laeufe):
        report.skip("Importlaeufe")
        return

    profile = {str(p["name"]) for p in api.get("/imports/mapping-profiles")}
    if MAPPING_PROFIL not in profile:
        api.post(
            "/imports/mapping-profiles",
            json_body={
                "name": MAPPING_PROFIL,
                "bank_format": "csv_generic",
                "column_mapping": {
                    "booked_date": "Datum",
                    "amount": "Betrag",
                    "payee": "Empfaenger",
                    "purpose": "Verwendungszweck",
                    "reference": "Referenz",
                },
            },
        )
        report.add("Mapping-Profile")
    else:
        report.skip("Mapping-Profile")

    inhalt = baue_import_csv(heute)
    lauf = api.post(
        "/imports/upload",
        files={"file": (IMPORT_DATEINAME, inhalt, "text/csv")},
        data={"account_id": konten[KONTO_GIRO]},
    )
    report.add("Importlaeufe")

    # Der Worker verarbeitet asynchron - bis 'ready' warten (siehe
    # worker/imports/processor.process_import_run).
    lauf_id = str(lauf["id"])
    status = str(lauf["status"])
    for _ in range(60):
        if status in ("ready", "failed", "completed"):
            break
        time.sleep(1.0)
        aktuell = api.get(f"/imports/{lauf_id}")
        status = str(aktuell["status"])
        lauf = aktuell

    if status != "ready":
        report.hinweis(
            f"Der Beispielimport steht auf Status {status!r} statt 'ready'. "
            "Laeuft der Worker (dramatiq) und Redis? Ohne ihn bleiben Dublette "
            "und Transfer-Kandidat in der Review-Inbox leer."
        )
        return

    report.add("Importzeilen", int(lauf["total_rows"]))
    if int(lauf["duplicate_count"]) == 0:
        report.hinweis("Der Beispielimport hat keine moegliche Dublette erzeugt.")
    if int(lauf["transfer_candidate_count"]) == 0:
        report.hinweis("Der Beispielimport hat keinen Transfer-Kandidaten erzeugt.")


# ---------------------------------------------------------------------------
# Zuruecksetzen der Demodaten (nur auf ausdrueckliche Anforderung)
# ---------------------------------------------------------------------------


def reset_demodaten(
    conn: psycopg.Connection[Any], household_id: str, bestaetigt: bool
) -> None:
    """Entfernt ausschliesslich die vom Seed angelegten Demodaten.

    Bewusst NICHT entfernt werden: Kategorien, Zahlungspartner, Budgetplaene
    und Uebertraege. Sie sind Strukturdaten, richten keinen Schaden an und
    ihre Loeschung wuerde ueber Kaskaden auch Bestandsdaten treffen.
    """
    abfragen: tuple[tuple[str, str, tuple[Any, ...]], ...] = (
        (
            "Demo-Konten (mitsamt ihren Buchungen, Splits, Transfers, Abstimmungen)",
            "select name from accounts where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        ),
        (
            "Demo-Sparziele",
            "select name from savings_goals where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        ),
        (
            "Demo-Wunschlisteneintraege",
            "select name from wishlist_items where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        ),
        (
            "Demo-Vermoegen/Schulden",
            "select name from assets_and_debts where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        ),
        (
            "Demo-Essensplanwochen",
            "select week_start::text from meal_weeks where household_id = %s and note like %s",
            (household_id, "%" + DEMO),
        ),
        (
            "Demo-Klassifikationsregeln",
            "select name from classification_rules where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        ),
        (
            # Abo-Vertraege tragen hinter der Demomarke noch den Tarif, deshalb
            # hier mit Platzhalter auf beiden Seiten.
            "Demo-Vertraege",
            "select name from contracts where household_id = %s and name like %s",
            (household_id, "%" + DEMO + "%"),
        ),
        (
            "Demo-Importlaeufe",
            "select filename from import_runs where household_id = %s and filename = %s",
            (household_id, IMPORT_DATEINAME),
        ),
        (
            "Demo-Mappingprofile",
            "select name from import_mapping_profiles where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        ),
    )

    print("\n--- Zuruecksetzen: folgende Demodaten sind betroffen ---")
    gesamt = 0
    with conn.cursor() as cur:
        for beschreibung, sql, params in abfragen:
            cur.execute(sql, params)
            treffer = [str(row[0]) for row in cur.fetchall()]
            gesamt += len(treffer)
            print(f"  {beschreibung}: {len(treffer)}")
            for eintrag in treffer:
                print(f"      - {eintrag}")

        if gesamt == 0:
            print("  Keine Demodaten vorhanden - nichts zu tun.")
            return
        if not bestaetigt:
            print(
                "\nEs wurde NICHTS geloescht. Zum tatsaechlichen Loeschen "
                "zusaetzlich --yes angeben."
            )
            return

        # Reihenfolge egal, alles haengt am Haushalt bzw. kaskadiert.
        cur.execute(
            "delete from accounts where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        )
        cur.execute(
            "delete from savings_goals where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        )
        cur.execute(
            "delete from wishlist_items where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        )
        cur.execute(
            "delete from assets_and_debts where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        )
        cur.execute(
            "delete from meal_weeks where household_id = %s and note like %s",
            (household_id, "%" + DEMO),
        )
        cur.execute(
            "delete from classification_rules where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        )
        cur.execute(
            "delete from contracts where household_id = %s and name like %s",
            (household_id, "%" + DEMO + "%"),
        )
        cur.execute(
            "delete from import_runs where household_id = %s and filename = %s",
            (household_id, IMPORT_DATEINAME),
        )
        cur.execute(
            "delete from import_mapping_profiles where household_id = %s and name like %s",
            (household_id, "%" + DEMO),
        )
    conn.commit()
    print("\nDemodaten entfernt.")


# ---------------------------------------------------------------------------
# Aufraeumen der Testhaushalte (getrennter, ausdruecklich anzufordernder Modus)
# ---------------------------------------------------------------------------


def cleanup_test_data(
    dsn: str, geschuetzte_haushalte: set[str], geschuetzte_email: str | None, bestaetigt: bool
) -> int:
    """Entfernt Haushalte, die eindeutig aus Testlaeufen stammen.

    Sicherheitsnetz:
      * nur Namen, die EXAKT auf eines der Muster in TESTHAUSHALT_MUSTER
        passen (plus Haushalte ganz ohne Mitglied - die kann niemand mehr
        ueber die App erreichen),
      * niemals ein Haushalt, in dem der geschuetzte Nutzer Mitglied ist,
      * niemals ein ausdruecklich geschuetzter Haushalt,
      * ohne --yes wird nur angezeigt, nichts geloescht.

    Die API hat keinen Endpunkt zum Loeschen eines Haushalts, deshalb laeuft
    dieser Modus vollstaendig ueber psycopg.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from households")
        row = cur.fetchone()
        gesamt_vorher = int(row[0]) if row else 0

        geschuetzt = set(geschuetzte_haushalte)
        if geschuetzte_email:
            cur.execute(
                "select m.household_id from household_members m "
                "join auth.users u on u.id = m.user_id where lower(u.email) = lower(%s)",
                (geschuetzte_email,),
            )
            geschuetzt |= {str(r[0]) for r in cur.fetchall()}

        kandidaten: dict[str, tuple[str, str]] = {}
        print("\n--- Aufraeummodus: Testhaushalte ---")
        print(f"Haushalte in der Datenbank: {gesamt_vorher}")
        print(f"Geschuetzt (nie geloescht): {len(geschuetzt)}")

        for beschreibung, muster in TESTHAUSHALT_MUSTER:
            cur.execute("select id, name from households where name ~ %s", (muster,))
            treffer = [(str(hid), str(name)) for hid, name in cur.fetchall()]
            treffer = [t for t in treffer if t[0] not in geschuetzt]
            for hid, name in treffer:
                kandidaten[hid] = (name, beschreibung)
            print(f"  {beschreibung:45s} {len(treffer):5d}  (Muster {muster})")

        cur.execute(
            "select id, name from households h where not exists "
            "(select 1 from household_members m where m.household_id = h.id)"
        )
        waisen = [(str(hid), str(name)) for hid, name in cur.fetchall()]
        waisen = [w for w in waisen if w[0] not in geschuetzt]
        for hid, name in waisen:
            kandidaten.setdefault(hid, (name, "Haushalte ohne jedes Mitglied"))
        print(f"  {'Haushalte ohne jedes Mitglied':45s} {len(waisen):5d}")

        print(f"\nBetroffen waeren insgesamt: {len(kandidaten)} Haushalte")
        print(f"Danach verblieben:          {gesamt_vorher - len(kandidaten)} Haushalte")

        namen = sorted({name for name, _ in kandidaten.values()})
        print(f"Betroffene Namen ({len(namen)} verschiedene): {', '.join(namen[:40])}")

        if not kandidaten:
            return 0
        if not bestaetigt:
            print(
                "\nEs wurde NICHTS geloescht (Standardverhalten). Zum tatsaechlichen "
                "Loeschen zusaetzlich --yes angeben."
            )
            return 0

        cur.execute(
            "delete from households where id = any(%s)", (list(kandidaten.keys()),)
        )
        geloescht = cur.rowcount
        conn.commit()
        print(f"\n{geloescht} Haushalte geloescht (samt aller abhaengigen Daten).")
        print(
            "Hinweis: die zugehoerigen Testnutzer in auth.users bleiben bestehen - "
            "sie werden hier bewusst nicht angefasst."
        )
        return geloescht


# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------


def zaehle_bestand(conn: psycopg.Connection[Any], household_id: str) -> list[tuple[str, int]]:
    """Zaehlt nach dem Lauf, was im Haushalt tatsaechlich liegt."""
    tabellen: tuple[tuple[str, str], ...] = (
        ("Konten", "accounts"),
        ("Buchungen", "transactions"),
        ("Buchungssplits", "transaction_splits"),
        ("Kategorien", "categories"),
        ("Zahlungspartner", "payees"),
        ("Transferverknuepfungen", "transfer_links"),
        ("Kontoabstimmungen", "reconciliations"),
        ("Budgetmonate", "budgets"),
        ("Budgetplanwerte", "budget_allocations"),
        ("Budgetuebertraege", "budget_rollovers"),
        ("Sparziele", "savings_goals"),
        ("Wunschliste", "wishlist_items"),
        ("Vermoegen und Schulden", "assets_and_debts"),
        ("Bewertungspunkte", "asset_valuations"),
        ("Essensplanwochen", "meal_weeks"),
        ("Mahlzeiten", "meals"),
        ("Einkaufslisten", "shopping_lists"),
        ("Einkaufsartikel", "shopping_items"),
        ("Einkaufsverknuepfungen", "shopping_purchase_links"),
        ("Vertraege", "contracts"),
        ("Klassifikationsregeln", "classification_rules"),
        ("Importlaeufe", "import_runs"),
        ("Importzeilen", "import_rows"),
        ("Mapping-Profile", "import_mapping_profiles"),
    )
    ergebnis: list[tuple[str, int]] = []
    with conn.cursor() as cur:
        for label, tabelle in tabellen:
            # Tabellenname stammt aus der festen Liste oben, nicht aus Eingaben.
            cur.execute(
                f"select count(*) from {tabelle} where household_id = %s",
                (household_id,),
            )
            row = cur.fetchone()
            ergebnis.append((label, int(row[0]) if row else 0))
    return ergebnis


def drucke_zusammenfassung(
    report: Report,
    conn: psycopg.Connection[Any],
    household_id: str,
    household_name: str,
    api: ApiClient,
) -> None:
    print("\n" + "=" * 72)
    print(f"Seed abgeschlossen fuer Haushalt {household_name!r} ({household_id})")
    print("=" * 72)

    print("\nAngelegt in diesem Lauf:")
    if report.angelegt:
        for bereich in sorted(report.angelegt):
            print(f"  {bereich:32s} {report.angelegt[bereich]:6d}")
    else:
        print("  nichts (alles war bereits vorhanden)")

    if report.uebersprungen:
        print("\nBereits vorhanden, deshalb uebersprungen:")
        for bereich in sorted(report.uebersprungen):
            print(f"  {bereich:32s} {report.uebersprungen[bereich]:6d}")

    print("\nBestand im Haushalt (direkt aus der Datenbank gezaehlt):")
    for label, anzahl in zaehle_bestand(conn, household_id):
        print(f"  {label:32s} {anzahl:6d}")

    try:
        inbox = api.get("/review")
        print("\nReview-Inbox:")
        print(f"  {'unklassifiziert':32s} {len(inbox['unclassified']):6d}")
        print(f"  {'moegliche Dubletten':32s} {len(inbox['possible_duplicates']):6d}")
        print(f"  {'Transfer-Kandidaten':32s} {len(inbox['transfer_candidates']):6d}")
        print(f"  {'Fixkostenvorschlaege':32s} {len(inbox['recurring_suggestions']):6d}")

        wunsch = api.get("/wishlist")
        print("\nKaufampel:")
        for eintrag in wunsch:
            print(
                f"  {str(eintrag['name']):40s} {str(eintrag['buy_traffic_light']):7s} "
                f"{euro(int(eintrag['price_minor']))}"
            )

        netto = api.get("/reports/net-worth")
        print(
            "\nNettovermoegen: "
            f"{euro(int(netto['net_worth_minor']))} "
            f"(Vermoegen {euro(int(netto['total_assets_minor']))}, "
            f"Schulden {euro(int(netto['total_debts_minor']))})"
        )
    except ApiError as exc:
        print(f"\nAbschliessende Kontrollabfragen fehlgeschlagen: {exc}")

    if report.api_luecken:
        print("\nOhne API-Endpunkt - direkt per psycopg geschrieben:")
        for eintrag in report.api_luecken:
            print(f"  - {eintrag}")

    if report.hinweise:
        print("\nHinweise:")
        for eintrag in report.hinweise:
            print(f"  - {eintrag}")


# ---------------------------------------------------------------------------
# Ablaufsteuerung
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed_dev.py",
        description=(
            "Befuellt einen Haushalt mit vorfuehrbaren Beispieldaten. "
            "Zugangsdaten kommen aus SEED_EMAIL und SEED_PASSWORD."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--household-id",
        default=None,
        help="Ziel-Haushalt (UUID). Alternativ SEED_HOUSEHOLD_ID.",
    )
    parser.add_argument(
        "--household-name",
        default=None,
        help="Ziel-Haushalt ueber seinen Namen waehlen. Alternativ SEED_HOUSEHOLD_NAME.",
    )
    parser.add_argument(
        "--new-household",
        metavar="NAME",
        default=None,
        help="Statt eines bestehenden Haushalts einen neuen mit diesem Namen anlegen.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Vor dem Seed die zuvor angelegten Demodaten entfernen. Zeigt zuerst an, "
            "was betroffen waere; loescht nur zusammen mit --yes. Bestandsdaten "
            "ohne Demo-Kennzeichnung werden nie angefasst."
        ),
    )
    parser.add_argument(
        "--reset-only",
        action="store_true",
        help="Nur zuruecksetzen, danach nicht neu befuellen.",
    )
    parser.add_argument(
        "--no-close-month",
        action="store_true",
        help=(
            "Den Vormonat NICHT abschliessen. Der Monatsabschluss ist die einzige "
            "Aenderung des Seeds an Bestandsdaten (budgets.status) und zugleich die "
            "einzige Moeglichkeit, einen Budgetuebertrag zu erzeugen."
        ),
    )
    parser.add_argument(
        "--cleanup-test-data",
        action="store_true",
        help=(
            "Getrennter Aufraeummodus: entfernt Haushalte aus Testlaeufen. "
            "Laeuft NIE automatisch und ohne --yes nur als Anzeige."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bestaetigt --reset bzw. --cleanup-test-data; ohne diese Angabe wird nur angezeigt.",
    )
    return parser.parse_args(argv)


def run_seed(cfg: Config, args: argparse.Namespace) -> int:
    heute = date.today()
    report = Report()

    token = supabase_login(cfg.supabase_url, cfg.anon_key, cfg.email, cfg.password)
    api = ApiClient(cfg.api_url, token, report)
    try:
        household_id, household_name = resolve_household(api, cfg, report)
        api.household_id = household_id
        print(f"Zielhaushalt: {household_name!r} ({household_id})")
        print(f"Stichtag:     {heute.isoformat()}")

        with psycopg.connect(cfg.dsn) as conn:
            if args.reset or args.reset_only:
                reset_demodaten(conn, household_id, args.yes)
                if args.reset_only:
                    return 0
                if not args.yes:
                    print(
                        "\nHinweis: --reset ohne --yes hat nichts geloescht; der Seed "
                        "ergaenzt jetzt wie ueblich."
                    )

            seed_kategorien(conn, household_id, report)
            seed_payees(conn, household_id, report)
            kategorien = lade_kategorien(api)
            konten = seed_konten(api, heute, report)
            ziele = seed_sparziele(api, heute, report)

            plan = baue_buchungsplan(heute, konten, kategorien, ziele)
            pruefe_schluessel_eindeutig(plan)
            seed_buchungen(api, conn, household_id, plan, report)
            seed_splitbuchung(api, conn, household_id, konten, kategorien, heute, report)
            seed_transfer(api, conn, household_id, konten, kategorien, heute, report)
            seed_abstimmung(api, konten, heute, report)

            seed_budgets(api, kategorien, heute, report, not args.no_close_month)
            seed_wunschliste(api, ziele, heute, report)
            seed_vermoegen(api, conn, household_id, heute, report)
            seed_lebensmittel(api, konten, heute, report)
            seed_regeln(api, conn, household_id, kategorien, heute, report)
            seed_import(api, konten, heute, report)

            drucke_zusammenfassung(report, conn, household_id, household_name, api)
        return 0
    finally:
        api.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.cleanup_test_data:
        dsn = os.environ.get("SEED_DATABASE_URL", DEFAULT_DSN)
        geschuetzt = {
            wert
            for wert in (args.household_id, os.environ.get("SEED_HOUSEHOLD_ID"))
            if wert
        }
        cleanup_test_data(
            dsn, geschuetzt, os.environ.get("SEED_EMAIL", "").strip() or None, args.yes
        )
        return 0

    try:
        cfg = load_config(args)
        return run_seed(cfg, args)
    except ApiError as exc:
        print(f"\nFEHLER: Die API hat den Aufruf abgelehnt.\n  {exc}", file=sys.stderr)
        return 2
    except SeedError as exc:
        print(f"\nFEHLER: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(
            f"\nFEHLER: Die API/Supabase ist nicht erreichbar: {exc}\n"
            "Laeuft die API auf SEED_API_URL und Supabase auf SEED_SUPABASE_URL?",
            file=sys.stderr,
        )
        return 1
    except psycopg.Error as exc:
        print(
            f"\nFEHLER: Datenbankzugriff fehlgeschlagen: {exc}\n"
            "Stimmt SEED_DATABASE_URL und laeuft die lokale Datenbank?",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
