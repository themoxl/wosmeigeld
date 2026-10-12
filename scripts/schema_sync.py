"""Zieht eine bestehende Datenbank auf den Stand von 0001_schema.sql nach.

WARUM ES DAS BRAUCHT
--------------------
`supabase/migrations/0001_schema.sql` ist die eine Wahrheit ueber das Schema und
wird direkt bearbeitet (keine nummerierten Migrationen in der Entwicklungsphase).
Fuer eine LEERE Datenbank genuegt das. Eine bestehende Installation laesst sich
damit aber nicht nachziehen: die Datei LEGT AN, sie aendert nicht. `create table
contracts` auf eine Datenbank, in der es die Tabelle schon gibt, ist ein Fehler -
kein "ist ja schon da". Genau daran bleibt jede laufende Installation haengen:
sie faellt Tabelle um Tabelle zurueck, ohne dass es dabei auffaellt.

Ueberall `if not exists` zu ergaenzen loest es nur halb: neue TABELLEN kaemen
durch, neue SPALTEN in bestehenden Tabellen nicht - `create table if not exists`
ueberspringt die Tabelle als Ganzes. Der Drift waere dann unsichtbar statt
sichtbar, also schlimmer.

WIE ES STATTDESSEN GEHT
-----------------------
Vergleichen statt nochmal anlegen:

  1. Eine Wegwerf-Datenbank aus 0001_schema.sql aufbauen - das ist der Sollstand.
  2. Den Iststand der Zieldatenbank als Schema-Abzug holen und in eine zweite
     Wegwerf-Datenbank laden.
  3. Die beiden vergleichen (migra) - das Ergebnis ist genau die Differenz.
  4. Sie anzeigen und auf Datenverlust pruefen.
  5. Erst mit --apply: sichern, anwenden, Stammdaten nachziehen, gegenpruefen.

Der Vergleich erwischt fehlende Tabellen, fehlende Spalten, geaenderte Indizes,
Bedingungen und Zugriffsregeln gleichermassen. Die Schemadatei selbst muss dafuer
nichts koennen ausser eine leere Datenbank aufbauen - was sie ohnehin tut.

DIE STAMMDATEN
--------------
Alle dreissig `insert into`-Anweisungen der Schemadatei sind wiederholbar: jede
hat entweder `on conflict` oder einen `not exists`-Filter. Sie laufen deshalb
nach dem Strukturabgleich einfach mit und fuellen neue Katalogtabellen
(Versicherungssparten, Kategorievorlagen, Foerderprogramme). Das wird beim Lauf
nachgeprueft, statt es zu glauben - siehe _pruefe_stammdaten_wiederholbar.

DATENERHALTENDE VORSTUFEN
-------------------------
Manche Aenderungen sieht ein Strukturvergleich falsch: eine umbenannte Spalte
erscheint als "drop column" + "add column" (der Wert ginge verloren), ein neues
"not null" scheitert an leeren Zellen, die die Schemadatei vorher fuellt. Solche
Bloecke stehen in 0001_schema.sql zwischen

    -- schema_sync: vorstufe <Grund>
    ...
    -- schema_sync: ende

Sie muessen wiederholbar sein. Der Abgleich spielt sie VOR dem Vergleich in die
Kopie des Iststands ein (der Unterschied zeigt also den Stand danach) und beim
Anwenden in DERSELBEN Transaktion vor dem Unterschied. Scheitert etwas, bleibt
die Datenbank unveraendert.

AUFRUF
------
    python scripts/schema_sync.py --target nas
    python scripts/schema_sync.py --target nas --apply
    python scripts/schema_sync.py --target local

Ohne --apply wird nichts geaendert.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "supabase" / "migrations" / "0001_schema.sql"
TOOL_VENV = REPO / "scripts" / ".venv-schema"
# Dasselbe Abbild wie im Stack (infra/nas/docker-compose.nas.yml): ein anderer
# Postgres-Hauptstand haette andere Vorgabewerte und produzierte Unterschiede,
# die es in Wahrheit nicht gibt.
PG_IMAGE = "supabase/postgres:15.8.1.085"
REF_CONTAINER = "finanzplaner-schema-soll"
IST_CONTAINER = "finanzplaner-schema-ist"
_ARBEIT = Path(os.environ.get("TEMP", "/tmp")) / "finanzplaner-schema-sync"
# Wo der Stack auf dem Zielgeraet liegt und wo dort die Docker-Engine steht,
# steht NICHT hier, sondern in infra/nas/nas.conf - derselben Quelle, aus der
# auch infra/nas/lib/Nas.psm1 liest. Ein Pfad im Quelltext waere eine zweite
# Wahrheit neben der Konfiguration und beschriebe nebenbei die Ablagestruktur
# eines fremden Geraets. Vorlage: infra/nas/nas.conf.example.
NAS_CONF = REPO / "infra" / "nas" / "nas.conf"
REF_PORT = 55440
IST_PORT = 55441

# Anweisungen, die Daten vernichten koennen. Sie brechen den Lauf ab, statt
# stillschweigend mitzulaufen - ein Schemaabgleich soll ein Schema angleichen
# und nicht nebenbei eine Spalte leeren.
GEFAEHRLICH = (
    re.compile(r"\bdrop\s+column\b", re.I),
    re.compile(r"^\s*drop\s+table\b", re.I | re.M),
    re.compile(r"^\s*truncate\b", re.I | re.M),
    re.compile(r"\bdrop\s+schema\b", re.I),
)


def lauf(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    # encoding/errors ausdruecklich: unter Windows liest subprocess sonst mit
    # cp1252 und bricht an jedem Umlaut ab, den psql oder plink ausgeben.
    return subprocess.run(  # type: ignore[arg-type]
        cmd, capture_output=True, encoding="utf-8", errors="replace", **kw
    )


def docker(*args: str, check: bool = True) -> str:
    ergebnis = lauf(["docker", *args])
    if check and ergebnis.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} fehlgeschlagen:\n{ergebnis.stderr}")
    return ergebnis.stdout


_NAS_CONF_CACHE: dict[str, str] | None = None


def _nas_conf(schluessel: str) -> str:
    """Einen Wert aus infra/nas/nas.conf holen (Umgebungsvariable geht vor).

    Nur der Zweig `--target nas` braucht das; `--target local` kommt ohne die
    Datei aus. Fehlt sie oder der Eintrag, bricht der Lauf mit einem Hinweis ab,
    statt auf einen im Quelltext hinterlegten Pfad zurueckzufallen - ein solcher
    Rueckfall waere genau die zweite Wahrheit, die es hier nicht geben soll.
    """
    global _NAS_CONF_CACHE

    wert = os.environ.get(schluessel, "").strip()
    if wert:
        return wert.rstrip("/")

    if _NAS_CONF_CACHE is None:
        if not NAS_CONF.exists():
            raise SystemExit(
                f"{NAS_CONF} fehlt. Fuer '--target nas' werden die Ablagepfade des "
                "Zielgeraets daraus gelesen - Vorlage: infra/nas/nas.conf.example. "
                f"Alternativ {schluessel} als Umgebungsvariable setzen."
            )
        conf: dict[str, str] = {}
        for zeile in NAS_CONF.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            name, _, inhalt = zeile.partition("=")
            conf[name.strip()] = inhalt.strip()
        _NAS_CONF_CACHE = conf

    wert = _NAS_CONF_CACHE.get(schluessel, "").strip()
    if not wert:
        raise SystemExit(
            f"{schluessel} fehlt oder ist leer in {NAS_CONF} "
            "(siehe infra/nas/nas.conf.example)."
        )
    return wert.rstrip("/")


def _docker_pfad() -> str:
    """PATH-Voranstellung fuer Docker-Aufrufe auf der NAS.

    Die Engine liegt dort nicht in der Standardumgebung einer
    nicht-interaktiven Sitzung; wo sie liegt, sagt NAS_DOCKER_BIN.
    """
    return f"export PATH=$PATH:{_nas_conf('NAS_DOCKER_BIN')}; "


def _stack_verzeichnis() -> str:
    """Ablageort des Stacks auf der NAS (NAS_STACK_DIR aus nas.conf)."""
    return _nas_conf("NAS_STACK_DIR")


# ---------------------------------------------------------------------------
# Wegwerf-Datenbanken
# ---------------------------------------------------------------------------


def _starte_wegwerf(name: str, port: int) -> None:
    docker("rm", "-f", name, check=False)
    docker(
        "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=postgres",
        "-p", f"{port}:5432", PG_IMAGE,
    )
    # Bewusst eine echte Abfrage statt pg_isready: das Supabase-Abbild faehrt
    # den Server waehrend der Einrichtung zwischendurch herunter und wieder
    # hoch. pg_isready meldet dazwischen "bereit", und der naechste Befehl
    # findet dann keinen Socket mehr. Erst wenn zweimal hintereinander eine
    # Abfrage durchgeht, steht der Server wirklich.
    treffer = 0
    for _ in range(90):
        if _psql(name, "-t", "-c", "select 1").returncode == 0:
            treffer += 1
            if treffer >= 2:
                break
        else:
            treffer = 0
        time.sleep(1)
    else:
        raise SystemExit(f"Wegwerf-Datenbank {name} wurde nicht bereit.")

    # Das Supabase-Abbild bringt Ereignis-Trigger mit (pg_graphql, PostgREST,
    # pg_cron), die bei JEDER Schemaaenderung mitlaufen. Beim Einspielen von
    # zehntausend Zeilen DDL brechen sie ab ("could not open relation with OID
    # ...", weil pg_graphql seinen eigenen Zaehler waehrenddessen neu aufbaut).
    # Fuer eine Wegwerf-Datenbank, die nur als Vergleichsstand dient, haben sie
    # keinen Zweck - der Vergleich sieht ohnehin nur das Schema 'public'.
    _psql(
        name, "-q", "-c",
        "do $$ declare t record; begin "
        "  for t in select evtname from pg_event_trigger loop "
        "    execute format('drop event trigger if exists %I', t.evtname); "
        "  end loop; end $$;",
    )


def _psql(container: str, *args: str, eingabe: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", "postgres", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        input=eingabe,
    )


def _lade_datei(container: str, datei: Path, stop_bei_fehler: bool) -> None:
    # Zeilenenden vereinheitlichen, BEVOR die Datei in die Datenbank geht.
    # Postgres speichert Funktionsruempfe als Text, samt der Wagenrueckläufe aus
    # einer CRLF-Datei - und 0001_schema.sql ist CRLF. Der Vergleich meldete
    # daraufhin fuenf Funktionen als "unterschiedlich", die sich nur in
    # unsichtbaren Zeichen unterschieden, und wurde nie sauber.
    inhalt = datei.read_bytes().replace(b"\r\n", b"\n")
    normalisiert = _ARBEIT / f"laden-{container}.sql"
    normalisiert.parent.mkdir(parents=True, exist_ok=True)
    normalisiert.write_bytes(inhalt)
    docker("cp", str(normalisiert), f"{container}:/tmp/laden.sql")
    args = ["-q", "-f", "/tmp/laden.sql"]
    if stop_bei_fehler:
        args = ["-v", "ON_ERROR_STOP=1", *args]
    ergebnis = _psql(container, *args)
    if stop_bei_fehler and ergebnis.returncode != 0:
        raise SystemExit(f"Laden in {container} fehlgeschlagen:\n{ergebnis.stderr[-2000:]}")
    if not stop_bei_fehler:
        # Der Abzug des Iststands laeuft ohne ON_ERROR_STOP, weil er Dinge
        # enthaelt, die es in der Wegwerf-Datenbank schon gibt. Jeder ANDERE
        # Fehler heisst aber: die Kopie ist unvollstaendig, und der Vergleich
        # meldete Unterschiede, die es nicht gibt (so geschehen mit den
        # Trigramm-Indizes - der Abgleich wollte sie auf der NAS ein zweites
        # Mal anlegen und scheiterte). Deshalb laut statt still.
        fehler = [
            zeile
            for zeile in ergebnis.stderr.splitlines()
            if "ERROR:" in zeile and not any(m.search(zeile) for m in _ERWARTETE_LADEFEHLER)
        ]
        if fehler:
            raise SystemExit(
                f"Laden in {container} unvollstaendig - die Kopie taugt nicht zum Vergleich:\n  "
                + "\n  ".join(fehler[:20])
            )


# Fehler beim Laden eines Abzugs, die nichts ueber den Iststand aussagen.
_ERWARTETE_LADEFEHLER = (re.compile(r'schema "public" already exists'),)


def _erweiterungen_fuer_abzug(container: str) -> None:
    """Die Erweiterungen, die der Abzug voraussetzt, im Schema public anlegen.

    Der Abzug bringt sie nicht mit, seine Indizes brauchen sie aber
    (public.gin_trgm_ops). Das Supabase-Abbild startet den Server waehrend der
    Einrichtung noch einmal neu; ein einzelner Versuch kann genau in diesen
    Neustart fallen und ging dann still verloren. Deshalb: wiederholen, bis
    die Erweiterung nachweislich in public liegt.
    """
    for _ in range(60):
        _psql(
            container, "-q", "-c",
            "create extension if not exists pg_trgm with schema public; "
            "create extension if not exists pgcrypto with schema public;",
        )
        lage = _psql(
            container, "-t", "-A", "-c",
            "select n.nspname from pg_extension e join pg_namespace n "
            "on n.oid = e.extnamespace where e.extname = 'pg_trgm'",
        )
        if lage.returncode == 0 and lage.stdout.strip() == "public":
            return
        time.sleep(1)
    raise SystemExit(f"pg_trgm liess sich in {container} nicht im Schema public anlegen.")


def _tabellenzahl(container: str) -> int:
    ergebnis = _psql(
        container, "-t", "-c",
        "select count(*) from information_schema.tables where table_schema='public'",
    )
    return int(ergebnis.stdout.strip() or 0)


# ---------------------------------------------------------------------------
# Ziel: lokal oder NAS
# ---------------------------------------------------------------------------


def _nas_befehl(befehl: str) -> str:
    """Fuehrt einen Shell-Befehl auf der NAS aus.

    Ueber dieselbe Hilfsbibliothek wie deploy.ps1, damit es genau einen Weg
    dorthin gibt. Der Befehl geht ueber eine Skriptdatei und nicht als Argument:
    er muss durch PowerShell, plink und die Shell der NAS, und jede Ebene hat
    ihre eigenen Sonderzeichen.
    """
    skript = _ARBEIT / "nas-befehl.ps1"
    skript.parent.mkdir(parents=True, exist_ok=True)
    modul = REPO / "infra" / "nas" / "lib" / "Nas.psm1"
    kodiert = base64.b64encode(befehl.encode("utf-8")).decode("ascii")
    zeilen = [
        "$ErrorActionPreference = 'Stop'",
        f"Import-Module '{modul}' -Force",
        "$nas = Get-NasConfig",
        f"$befehl = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{kodiert}'))",
        "Invoke-NasCommand -Nas $nas -Command $befehl",
        # Rueckgabewert des Fernbefehls durchreichen. Ohne das endete das
        # Skript immer mit 0: ein gescheitertes psql auf der NAS galt als
        # Erfolg, seine Meldung verschwand, und erst die Gegenpruefung merkte,
        # dass nichts angewendet war.
        "exit $LASTEXITCODE",
        "",
    ]
    skript.write_text("\n".join(zeilen), encoding="utf-8")
    ergebnis = lauf(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skript)]
    )
    if ergebnis.returncode != 0:
        raise SystemExit(
            "NAS-Befehl fehlgeschlagen:\n"
            + (ergebnis.stderr or "")[-2000:]
            + "\n"
            + (ergebnis.stdout or "")[-1000:]
        )
    return ergebnis.stdout or ""


def _nas_psql(sql: str) -> str:
    """Schickt SQL an die NAS-Datenbank.

    Ueber eine uebertragene DATEI und nicht ueber die Kommandozeile: der
    Strukturunterschied ist gut tausend Zeilen lang, und Windows bricht den
    Aufruf ab ("Der Dateiname oder die Erweiterung ist zu lang"), lange bevor
    plink ihn ueberhaupt sieht.
    """
    stack = _stack_verzeichnis()
    ortsdatei = _ARBEIT / "anwenden.sql"
    ortsdatei.write_text(sql, encoding="utf-8")
    _nas_kopiere(ortsdatei, f"{stack}/anwenden.sql")
    return _nas_befehl(
        _docker_pfad()
        + f"docker cp {stack}/anwenden.sql "
        "finanzplaner-db:/tmp/anwenden.sql && "
        "docker exec finanzplaner-db psql -U postgres -d postgres "
        "-v ON_ERROR_STOP=1 --single-transaction -f /tmp/anwenden.sql && "
        f"rm -f {stack}/anwenden.sql"
    )


def _nas_kopiere(lokal: Path, entfernt: str) -> None:
    """Uebertraegt eine Datei auf die NAS - ueber dieselbe Hilfsbibliothek wie
    deploy.ps1, samt Pruefsumme."""
    skript = _ARBEIT / "nas-kopie.ps1"
    skript.parent.mkdir(parents=True, exist_ok=True)
    modul = REPO / "infra" / "nas" / "lib" / "Nas.psm1"
    zeilen = [
        "$ErrorActionPreference = 'Stop'",
        f"Import-Module '{modul}' -Force",
        "$nas = Get-NasConfig",
        f"Copy-ToNas -Nas $nas -Local '{lokal}' -Remote '{entfernt}'",
        "",
    ]
    skript.write_text("\n".join(zeilen), encoding="utf-8")
    ergebnis = lauf(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(skript)]
    )
    if ergebnis.returncode != 0:
        raise SystemExit(
            "Uebertragung auf die NAS fehlgeschlagen:\n" + (ergebnis.stderr or "")[-2000:]
        )


def hole_ist_schema(ziel: str, nach: Path) -> None:
    if ziel == "local":
        ergebnis = lauf([
            "docker", "exec", "supabase_db_Finanzplaner",
            "pg_dump", "-U", "postgres", "-d", "postgres",
            "--schema-only", "--schema=public",
        ])
        if ergebnis.returncode != 0:
            raise SystemExit(f"Abzug der lokalen Datenbank fehlgeschlagen:\n{ergebnis.stderr}")
        nach.write_text(ergebnis.stdout, encoding="utf-8")
        return

    stack = _stack_verzeichnis()
    _nas_befehl(
        _docker_pfad()
        + "docker exec finanzplaner-db pg_dump -U postgres -d postgres "
        "--schema-only --schema=public -f /tmp/ist.sql && "
        f"docker cp finanzplaner-db:/tmp/ist.sql {stack}/ist-schema.sql"
    )
    skript = (
        "Import-Module '"
        + str(REPO / "infra" / "nas" / "lib" / "Nas.psm1")
        + "' -Force; $nas = Get-NasConfig; "
        + f"Copy-FromNas -Nas $nas -Remote '{stack}/ist-schema.sql' -Local '{nach}'"
    )
    ergebnis = lauf(["powershell.exe", "-NoProfile", "-Command", skript])
    if not nach.exists():
        raise SystemExit(f"Schema-Abzug konnte nicht geholt werden:\n{ergebnis.stderr}")


# ---------------------------------------------------------------------------
# Stammdaten
# ---------------------------------------------------------------------------


VORSTUFE_ANFANG = re.compile(r"^\s*--\s*schema_sync:\s*vorstufe\b\s*(.*)$", re.I)
VORSTUFE_ENDE = re.compile(r"^\s*--\s*schema_sync:\s*ende\b", re.I)


def vorstufen(text: str) -> list[tuple[str, str]]:
    """Die datenerhaltenden Vorstufen der Schemadatei als (Grund, SQL).

    Ein Anfang ohne Ende oder ein verschachtelter Anfang bricht ab - eine halb
    erkannte Vorstufe liefe sonst unvollstaendig oder gar nicht.
    """
    bloecke: list[tuple[str, str]] = []
    grund: str | None = None
    zeilen: list[str] = []
    for nummer, zeile in enumerate(text.split("\n"), start=1):
        zeile = zeile.rstrip("\r")
        anfang = VORSTUFE_ANFANG.match(zeile)
        if anfang:
            if grund is not None:
                raise SystemExit(f"Vorstufe in Zeile {nummer} beginnt, bevor die vorige endet.")
            grund = anfang.group(1).strip() or f"Zeile {nummer}"
            zeilen = []
            continue
        if VORSTUFE_ENDE.match(zeile):
            if grund is None:
                raise SystemExit(f"'schema_sync: ende' in Zeile {nummer} ohne Vorstufe.")
            bloecke.append((grund, "\n".join(zeilen).strip()))
            grund = None
            continue
        if grund is not None:
            zeilen.append(zeile)
    if grund is not None:
        raise SystemExit(f"Vorstufe '{grund}' hat kein 'schema_sync: ende'.")
    return bloecke


def top_level_inserts(text: str) -> list[str]:
    """Die `insert into`-Anweisungen der Schemadatei, ohne die in Funktionen.

    Innerhalb von `$$ ... $$` steht der Rumpf einer Funktion; die laeuft beim
    Aufruf und nicht beim Einspielen.
    """
    zeilen = text.split("\n")
    in_body = False
    anweisungen: list[str] = []
    i = 0
    while i < len(zeilen):
        marken = zeilen[i].count("$$")
        if not in_body and re.match(r"^insert into \w+", zeilen[i]):
            j = i
            while j < len(zeilen) and not zeilen[j].rstrip().endswith(";"):
                j += 1
            anweisungen.append("\n".join(zeilen[i : j + 1]))
            i = j + 1
            continue
        for _ in range(marken):
            in_body = not in_body
        i += 1
    return anweisungen


def _pruefe_stammdaten_wiederholbar(anweisungen: list[str]) -> None:
    """Jede Stammdatenanweisung muss wiederholbar sein.

    Sonst verdoppelte ein zweiter Lauf Kategorievorlagen oder Foerderprogramme.
    Geprueft wird hier und nicht im Kopf: die Schemadatei waechst, und die naechste
    Anweisung ohne Schutz soll den Abgleich stoppen statt Daten zu verdoppeln.
    """
    ungeschuetzt = [
        a.split("\n")[0].strip()
        for a in anweisungen
        if "on conflict" not in a.lower() and "not exists" not in a.lower()
    ]
    if ungeschuetzt:
        raise SystemExit(
            "Diese Stammdatenanweisungen sind nicht wiederholbar (weder 'on "
            "conflict' noch 'not exists'). Ein zweiter Lauf wuerde die Zeilen "
            "verdoppeln:\n  " + "\n  ".join(ungeschuetzt)
        )


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("nas", "local"), default="nas")
    parser.add_argument("--apply", action="store_true", help="Aenderungen wirklich schreiben")
    parser.add_argument(
        "--keep-containers", action="store_true", help="Wegwerf-Datenbanken stehen lassen"
    )
    args = parser.parse_args()

    migra = TOOL_VENV / "Scripts" / "migra.exe"
    if not migra.exists():
        migra = TOOL_VENV / "bin" / "migra"
    if not migra.exists():
        raise SystemExit(
            "Das Vergleichswerkzeug fehlt. Einmalig anlegen:\n"
            f"  python -m venv {TOOL_VENV}\n"
            f"  {TOOL_VENV / 'Scripts' / 'python.exe'} -m pip install "
            'migra psycopg2-binary "setuptools<81"'
        )

    schema_text = SCHEMA.read_text(encoding="utf-8")
    stammdaten = top_level_inserts(schema_text)
    _pruefe_stammdaten_wiederholbar(stammdaten)
    print(f"Stammdaten: {len(stammdaten)} Anweisungen, alle wiederholbar.")
    vorab = vorstufen(schema_text)
    for grund, sql in vorab:
        verboten = [m.pattern for m in GEFAEHRLICH if m.search(sql)]
        if verboten:
            raise SystemExit(
                f"Vorstufe '{grund}' enthaelt Anweisungen, die Daten vernichten koennen: "
                + ", ".join(verboten)
            )
    vorab_sql = "\n\n".join(sql for _, sql in vorab)
    if vorab:
        print(f"Vorstufen: {len(vorab)} ({'; '.join(grund for grund, _ in vorab)}).")

    arbeit = _ARBEIT
    arbeit.mkdir(parents=True, exist_ok=True)
    ist_datei = arbeit / "ist-schema.sql"
    delta_datei = arbeit / "delta.sql"

    try:
        print(f"Sollstand aus {SCHEMA.name} aufbauen ...")
        _starte_wegwerf(REF_CONTAINER, REF_PORT)
        _lade_datei(REF_CONTAINER, SCHEMA, stop_bei_fehler=True)
        soll_tabellen = _tabellenzahl(REF_CONTAINER)

        print(f"Iststand von '{args.target}' holen ...")
        hole_ist_schema(args.target, ist_datei)
        _starte_wegwerf(IST_CONTAINER, IST_PORT)
        # Der Abzug bringt die Erweiterungen nicht mit, seine Indizes brauchen
        # sie aber (gin_trgm_ops). Ohne diesen Schritt fehlten sie im Iststand
        # und der Vergleich meldete Unterschiede, die es nicht gibt.
        _erweiterungen_fuer_abzug(IST_CONTAINER)
        _lade_datei(IST_CONTAINER, ist_datei, stop_bei_fehler=False)
        if vorab_sql:
            # Der Vergleich soll den Stand NACH den Vorstufen sehen - genau den
            # bekommt die Zieldatenbank beim Anwenden in derselben Transaktion.
            vorstufe = _psql(
                IST_CONTAINER, "-v", "ON_ERROR_STOP=1", "--single-transaction", "-q",
                eingabe=vorab_sql,
            )
            if vorstufe.returncode != 0:
                raise SystemExit(
                    "Die Vorstufen laufen auf dem Iststand nicht durch:\n"
                    + vorstufe.stderr[-2000:]
                )
        ist_tabellen = _tabellenzahl(IST_CONTAINER)
        print(f"  Soll {soll_tabellen} Tabellen, Ist {ist_tabellen} Tabellen.")

        print("Vergleichen ...")
        ergebnis = lauf([
            str(migra), "--unsafe", "--schema", "public",
            f"postgresql://postgres:postgres@127.0.0.1:{IST_PORT}/postgres",
            f"postgresql://postgres:postgres@127.0.0.1:{REF_PORT}/postgres",
        ])
        # migra: 0 = kein Unterschied, 2 = Unterschied gefunden, sonst Fehler.
        if ergebnis.returncode not in (0, 2):
            raise SystemExit(f"Vergleich fehlgeschlagen:\n{ergebnis.stderr[-2000:]}")
        delta = ergebnis.stdout
        delta_datei.write_text(delta, encoding="utf-8")

        if not delta.strip():
            print("\nKein Strukturunterschied - die Datenbank ist auf Stand.")
            if not args.apply:
                return 0
        else:
            print(f"\nUnterschied: {len(delta.splitlines())} Zeilen ({delta_datei})")
            _zeige_zusammenfassung(delta)

        gefunden = [m.pattern for m in GEFAEHRLICH if m.search(delta)]
        if gefunden:
            raise SystemExit(
                "\nABBRUCH: Der Unterschied enthaelt Anweisungen, die Daten "
                "vernichten koennen:\n  " + "\n  ".join(gefunden) + "\n\n"
                f"Bitte {delta_datei} von Hand pruefen. Ein Schemaabgleich soll "
                "ein Schema angleichen, nicht nebenbei Daten loeschen."
            )

        if not args.apply:
            print("\nProbelauf - es wurde nichts geaendert. Mit --apply ausfuehren.")
            return 0

        print("\nSichern ...")
        _sichern(args.target)

        if delta.strip() or vorab_sql:
            # Vorstufen und Unterschied in EINER Transaktion: eine umbenannte
            # Spalte ohne den Rest des Schemas waere ein Zwischenstand, mit dem
            # weder der alte noch der neue Code laeuft.
            print("Struktur angleichen ...")
            _anwenden(args.target, vorab_sql + "\n\n" + delta)

        print(f"Stammdaten nachziehen ({len(stammdaten)} Anweisungen) ...")
        _anwenden(args.target, ";\n".join(a.rstrip(";\n") for a in stammdaten) + ";")

        print("Gegenpruefen ...")
        hole_ist_schema(args.target, ist_datei)
        docker("rm", "-f", IST_CONTAINER, check=False)
        _starte_wegwerf(IST_CONTAINER, IST_PORT)
        _erweiterungen_fuer_abzug(IST_CONTAINER)
        _lade_datei(IST_CONTAINER, ist_datei, stop_bei_fehler=False)
        nachher = lauf([
            str(migra), "--unsafe", "--schema", "public",
            f"postgresql://postgres:postgres@127.0.0.1:{IST_PORT}/postgres",
            f"postgresql://postgres:postgres@127.0.0.1:{REF_PORT}/postgres",
        ])
        if nachher.stdout.strip():
            print("\nWARNUNG: Es bleibt ein Unterschied:")
            print(nachher.stdout[:2000])
            return 1
        print(f"\nFertig. {args.target} ist auf dem Stand von {SCHEMA.name}.")
        return 0
    finally:
        if not args.keep_containers:
            docker("rm", "-f", REF_CONTAINER, check=False)
            docker("rm", "-f", IST_CONTAINER, check=False)


def _zeige_zusammenfassung(delta: str) -> None:
    neue_tabellen = sorted(set(re.findall(r'create table "public"\."(\w+)"', delta)))
    neue_spalten = re.findall(r'alter table "public"\."(\w+)" add column "(\w+)"', delta)
    arten: dict[str, int] = {}
    for zeile in delta.splitlines():
        treffer = re.match(r"^\s*(create|drop|alter|comment)\s+(\w+)", zeile, re.I)
        if treffer:
            schluessel = f"{treffer.group(1).lower()} {treffer.group(2).lower()}"
            arten[schluessel] = arten.get(schluessel, 0) + 1
    print("  Anweisungen: " + ", ".join(f"{v}x {k}" for k, v in sorted(arten.items(), key=lambda x: -x[1])))
    if neue_tabellen:
        print(f"  Neue Tabellen ({len(neue_tabellen)}): " + ", ".join(neue_tabellen))
    if neue_spalten:
        print("  Neue Spalten: " + ", ".join(f"{t}.{s}" for t, s in neue_spalten))


def _sichern(ziel: str) -> None:
    stempel = time.strftime("%Y%m%d-%H%M%S")
    if ziel == "local":
        print("  (lokal wird nicht gesichert - die Datenbank ist wegwerfbar)")
        return
    stack = _stack_verzeichnis()
    _nas_befehl(
        _docker_pfad()
        + f"mkdir -p {stack}/backups; "
        f"docker exec finanzplaner-db pg_dump -U postgres -d postgres -Fc -f /tmp/{stempel}.dump && "
        f"docker cp finanzplaner-db:/tmp/{stempel}.dump "
        f"{stack}/backups/vor-schema-{stempel}.dump"
    )
    print(f"  backups/vor-schema-{stempel}.dump")


def _anwenden(ziel: str, sql: str) -> None:
    if ziel == "local":
        ergebnis = subprocess.run(
            ["docker", "exec", "-i", "supabase_db_Finanzplaner", "psql", "-U", "postgres",
             "-d", "postgres", "-v", "ON_ERROR_STOP=1", "--single-transaction"],
            text=True, capture_output=True, input=sql,
        )
        if ergebnis.returncode != 0:
            raise SystemExit(f"Anwenden fehlgeschlagen:\n{ergebnis.stderr[-2000:]}")
        return
    _nas_psql(sql)


if __name__ == "__main__":
    if sys.platform == "win32" and not shutil.which("docker"):
        raise SystemExit("docker wurde nicht gefunden.")
    raise SystemExit(main())
