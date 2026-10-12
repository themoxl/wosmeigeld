"""Sicherung und Wiederherstellung der Entwicklungsdatenbank - der Rueckweg
vor einem Experiment.

Abgrenzung zu infra/backup/: das dort ist der Produktivapparat aus Phase 10 -
age-verschluesselt, mit Manifest, Aufbewahrungsregel und automatisiertem
Restoretest, gedacht fuer "der Server ist gestorben". Dieses Skript beantwortet
eine viel kleinere Frage: "ich will etwas ausprobieren und danach zurueck".
Deshalb keine Verschluesselung, keine Aufbewahrungsregel, keine Manifeste -
eine Datei rein, eine Datei raus.

    python scripts/db_snapshot.py save                  # sichert nach db-snapshots/
    python scripts/db_snapshot.py save --name vor-import
    python scripts/db_snapshot.py list
    python scripts/db_snapshot.py restore vor-import    # fragt nach
    python scripts/db_snapshot.py restore vor-import --yes

Gesichert werden ausschliesslich die Anwendungsdaten (`public`, nur Daten).
Das Schema kommt aus supabase/migrations/0001_schema.sql und wird beim
Zurueckspielen NICHT aus der Sicherung genommen - sonst haette man beim
naechsten Schemastand einen eingefrorenen Altstand im Ruecken. Dieselbe
Entscheidung wie in infra/backup/backup.sh, und aus demselben Grund.

Einzige Voraussetzung ist Docker: `pg_dump`/`pg_restore` laufen ueber
postgres:15-alpine, exakt die Version des Servers (15.8) und dieselbe, die
auch docker-compose.yml pinnt. Auf dem Host muss nichts installiert sein.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"
# Exakt die Version aus docker-compose.yml. Ein neueres pg_dump gegen einen
# aelteren Server ist harmlos, andersherum nicht - und pg_restore verweigert
# ein Archiv aus einer neueren Version rundheraus.
PG_IMAGE = "postgres:15-alpine"
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "db-snapshots"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "0001_schema.sql"

# Namen landen als Dateiname und in einer Docker-Kommandozeile. Nur das, was
# unter beiden harmlos ist.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,60}$")


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _docker_dsn(dsn: str) -> str:
    """Aus Sicht des Containers ist `localhost` der Container selbst. Der
    Host heisst dort host.docker.internal - dafuer sorgt --add-host beim
    Aufruf."""
    return dsn.replace("127.0.0.1", "host.docker.internal").replace(
        "localhost", "host.docker.internal"
    )


def _run_pg(subcommand: str, args: list[str], *, stdin: bytes | None = None) -> bytes:
    befehl = [
        "docker", "run", "--rm", "-i",
        "--add-host=host.docker.internal:host-gateway",
        PG_IMAGE, subcommand, *args,
    ]
    ergebnis = subprocess.run(befehl, input=stdin, capture_output=True)
    if ergebnis.returncode != 0:
        fehler = ergebnis.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"{subcommand} fehlgeschlagen:\n{fehler}")
    # pg_restore meldet auch bei Erfolg auf stderr (uebersprungene Objekte).
    hinweise = ergebnis.stderr.decode("utf-8", "replace").strip()
    if hinweise and subcommand != "pg_dump":
        print(f"  ({hinweise.splitlines()[-1][:120]})")
    return ergebnis.stdout


def _pruefe_name(name: str) -> str:
    if not _NAME_PATTERN.match(name):
        raise SystemExit(
            f"„{name}“ ist als Name nicht zulaessig. Erlaubt sind Buchstaben, "
            "Ziffern, Punkt, Bindestrich und Unterstrich (bis 60 Zeichen)."
        )
    return name


def speichern(name: str | None) -> int:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    dateiname = _pruefe_name(name) if name else stempel
    ziel = SNAPSHOT_DIR / f"{dateiname}.dump"
    if ziel.exists():
        raise SystemExit(f"{ziel.name} gibt es schon. Anderen Namen waehlen oder loeschen.")

    print(f"Sichere nach {ziel.name} ...")
    daten = _run_pg(
        "pg_dump",
        ["--format=custom", "--data-only", "--schema=public", "--no-owner",
         "--no-privileges", _docker_dsn(_dsn())],
    )
    ziel.write_bytes(daten)
    print(f"Fertig: {ziel} ({len(daten) / 1024 / 1024:.1f} MB)")
    return 0


def auflisten() -> int:
    if not SNAPSHOT_DIR.exists() or not any(SNAPSHOT_DIR.glob("*.dump")):
        print("Noch keine Sicherung. Anlegen mit: python scripts/db_snapshot.py save")
        return 0
    print(f"{'Name':32} {'Groesse':>10}  Erstellt")
    for datei in sorted(SNAPSHOT_DIR.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = datei.stat()
        erstellt = datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
        print(f"{datei.stem:32} {stat.st_size / 1024 / 1024:>7.1f} MB  {erstellt}")
    return 0


def wiederherstellen(name: str, bestaetigt: bool) -> int:
    quelle = SNAPSHOT_DIR / f"{_pruefe_name(name)}.dump"
    if not quelle.exists():
        raise SystemExit(
            f"{quelle.name} gibt es nicht. Vorhandene zeigt: python scripts/db_snapshot.py list"
        )
    if not SCHEMA_FILE.exists():
        raise SystemExit(f"{SCHEMA_FILE} fehlt - ohne Schema laesst sich nichts einspielen.")

    if not bestaetigt:
        print(f"Das ersetzt ALLE Daten in {_dsn().rsplit('@', 1)[-1]} durch {quelle.name}.")
        print("Der aktuelle Stand ist danach weg, auch fuer andere Haushalte.")
        if input("Weiter? Dafuer „ja“ eintippen: ").strip().lower() != "ja":
            print("Abgebrochen.")
            return 1

    # Reihenfolge: erst Schema neu, dann Daten. Das Schema aus der Datei und
    # nicht aus der Sicherung - siehe Modulkopf.
    print("Verwerfe Schema und baue es aus 0001_schema.sql neu auf ...")
    _run_pg(
        "psql",
        ["--quiet", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-d", _docker_dsn(_dsn()),
         "-c", "drop schema if exists public cascade; create schema public;"],
    )
    _run_pg(
        "psql",
        ["--quiet", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-d", _docker_dsn(_dsn()), "-f", "-"],
        stdin=SCHEMA_FILE.read_bytes(),
    )

    # 0001_schema.sql legt Saatdaten an (Kategorienvorlagen, Taxonomie-
    # versionen, Anbietervorlagen). Der Dump enthaelt dieselben Zeilen, weil er
    # ganz `public` umfasst - ohne Leeren kollidiert das erste COPY mit einem
    # Schluesselkonflikt. Der Schnappschuss ist die Wahrheit, nicht die Saat.
    print("Leere die frisch angelegten Saatdaten ...")
    _run_pg(
        "psql",
        ["--quiet", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-d", _docker_dsn(_dsn()), "-c",
         "do $$ declare befehl text; begin "
         "  select 'truncate table ' || string_agg(format('%I.%I', schemaname, tablename), ', ') "
         "         || ' restart identity cascade' "
         "    into befehl from pg_tables where schemaname = 'public'; "
         "  if befehl is not null then execute befehl; end if; end $$;"],
    )

    print(f"Spiele {quelle.name} ein ...")
    # Die Daten kommen in Dump-Reihenfolge, nicht in Fremdschluessel-
    # reihenfolge - ohne abgeschaltete Pruefung scheitert jede Zeile, deren
    # Elternzeile noch nicht da ist. Abschalten ist hier zulaessig, weil der
    # Stand aus derselben Datenbank stammt und damit in sich stimmig ist.
    #
    # Warum nicht `pg_restore --disable-triggers`: das setzt
    # `alter table ... disable trigger all` ab und verlangt dafuer echte
    # Superuser-Rechte. Der postgres-Rollenname bei Supabase ist KEIN
    # Superuser (usesuper = false), der Lauf bricht mit "permission denied:
    # ... is a system trigger" ab. `session_replication_role = replica` darf
    # die Rolle dagegen setzen und schaltet Fremdschluessel- und Nutzertrigger
    # gleichermassen ab.
    #
    # Ohne Dateiargument liest pg_restore von stdin. Ein "-" waere hier KEIN
    # Platzhalter fuer stdin, sondern ein Dateiname - genau dieser Unterschied
    # zu psql kostet sonst einen Fehlversuch.
    anweisungen = _run_pg(
        "pg_restore",
        ["--data-only", "--no-owner", "--no-privileges", "--file=-"],
        stdin=quelle.read_bytes(),
    )
    strom = (
        b"set session_replication_role = replica;\nbegin;\n"
        + anweisungen
        + b"\ncommit;\nset session_replication_role = origin;\n"
    )
    _run_pg(
        "psql",
        ["--quiet", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-d", _docker_dsn(_dsn()), "-f", "-"],
        stdin=strom,
    )
    print("Fertig. Die Dienste sollten neu gestartet werden (Verbindungen halten alte Zustaende).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sicherung und Wiederherstellung der Entwicklungsdatenbank.",
        epilog="Fuer den Produktivbetrieb gibt es infra/backup/ - das hier ist der kurze Weg.",
    )
    unter = parser.add_subparsers(dest="befehl", required=True)

    speichern_parser = unter.add_parser("save", help="Aktuellen Stand sichern")
    speichern_parser.add_argument("--name", help="Name der Sicherung (sonst Zeitstempel)")

    unter.add_parser("list", help="Vorhandene Sicherungen zeigen")

    zurueck = unter.add_parser("restore", help="Stand zurueckspielen (ersetzt alles)")
    zurueck.add_argument("name", help="Name der Sicherung")
    zurueck.add_argument("--yes", action="store_true", help="Ohne Rueckfrage")

    args = parser.parse_args()
    if args.befehl == "save":
        return speichern(args.name)
    if args.befehl == "list":
        return auflisten()
    return wiederherstellen(args.name, args.yes)


if __name__ == "__main__":
    sys.exit(main())
