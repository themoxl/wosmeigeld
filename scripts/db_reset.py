"""Setzt die Entwicklungsdatenbank auf einen leeren, aktuellen Stand zurueck.

Verwirft das Schema `public` samt allen Daten und baut es aus
supabase/migrations/0001_schema.sql neu auf. Das ist der echte Neuanfang -
nicht "meinen Haushalt leeren" (dafuer gibt es den Knopf in der App), sondern
"alles weg, auch die 22.000 Testhaushalte".

    python scripts/db_reset.py              # fragt nach, zeigt vorher was weg ist
    python scripts/db_reset.py --yes        # ohne Rueckfrage
    python scripts/db_reset.py --snapshot   # sichert vorher automatisch

Warum ueberhaupt: `supabase db reset` ist auf diesem Rechner nicht verfuegbar
(kein Supabase-CLI), und ein Schema von Hand zu verwerfen ist genau die Art
Befehl, die man nicht in einer Kommandozeile zusammenbaut, wenn echte Daten
danebenliegen.

Die Rueckfrage zeigt vorher an, was verloren geht - insbesondere, ob echte
Haushalte betroffen sind. Ein Haushalt gilt als echt, wenn ihm mindestens ein
Mitglied angehoert, dessen Anmeldung keine Testadresse ist; dasselbe Kriterium
wie in scripts/cleanup_test_data.py und scripts/db_status.py.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "0001_schema.sql"
SNAPSHOT_SCRIPT = Path(__file__).resolve().parent / "db_snapshot.py"
TEST_DOMAINS = ("@finanzplaner.test", "@example.test")

_DOMAIN_FILTER = " or ".join(f"u.email like '%{domain}'" for domain in TEST_DOMAINS)


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _bestandsaufnahme(dsn: str) -> tuple[list[tuple[str, int]], int, str]:
    """Was ist drin - echte Haushalte namentlich, Testhaushalte als Zahl."""
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute("select pg_size_pretty(pg_database_size(current_database()))")
        groesse = cur.fetchone()[0]
        cur.execute(f"""
            select h.name,
                   (select count(*) from transactions t where t.household_id = h.id)
            from households h
            where exists (
                select 1 from household_members m
                join auth.users u on u.id = m.user_id
                where m.household_id = h.id and not ({_DOMAIN_FILTER})
            )
            order by 2 desc, 1
        """)
        echte = [(zeile[0], zeile[1]) for zeile in cur.fetchall()]
        cur.execute("select count(*) from households")
        gesamt = cur.fetchone()[0]
    return echte, gesamt, groesse


def _psql(dsn: str, *, befehl: str | None = None, datei: bytes | None = None) -> None:
    """psql ueber das gepinnte Docker-Image - auf dem Host muss nichts liegen."""
    argumente = ["--quiet", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-d",
                 dsn.replace("127.0.0.1", "host.docker.internal").replace(
                     "localhost", "host.docker.internal")]
    argumente += ["-c", befehl] if befehl else ["-f", "-"]
    ergebnis = subprocess.run(
        ["docker", "run", "--rm", "-i", "--add-host=host.docker.internal:host-gateway",
         "postgres:15-alpine", "psql", *argumente],
        input=datei,
        capture_output=True,
    )
    if ergebnis.returncode != 0:
        raise SystemExit("psql fehlgeschlagen:\n" + ergebnis.stderr.decode("utf-8", "replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--yes", action="store_true", help="Ohne Rueckfrage")
    parser.add_argument(
        "--snapshot", action="store_true",
        help="Vorher automatisch sichern (scripts/db_snapshot.py save)",
    )
    args = parser.parse_args()

    if not SCHEMA_FILE.exists():
        raise SystemExit(f"{SCHEMA_FILE} fehlt - ohne Schema gibt es nichts aufzubauen.")

    dsn = _dsn()
    echte, gesamt, groesse = _bestandsaufnahme(dsn)

    print(f"Datenbank: {dsn.rsplit('@', 1)[-1]} ({groesse})")
    print(f"Haushalte: {gesamt} insgesamt, davon {len(echte)} mit echter Anmeldung\n")
    if echte:
        print("ECHTE HAUSHALTE - diese Daten sind danach weg:")
        for name, buchungen in echte:
            print(f"  {name:40} {buchungen:>7} Buchungen")
        print()

    if args.snapshot:
        print("Sichere vorher ...")
        lauf = subprocess.run([sys.executable, str(SNAPSHOT_SCRIPT), "save"])
        if lauf.returncode != 0:
            raise SystemExit("Sicherung fehlgeschlagen - es wird nichts verworfen.")
        print()

    if not args.yes:
        # Bei echten Haushalten den Namen tippen lassen, sonst reicht "ja".
        # Ein Klick bei einer leeren Testdatenbank ist etwas anderes als bei
        # zwei Jahren Buchungen, und das darf sich nicht gleich anfuehlen.
        if echte:
            erwartet = echte[0][0]
            print(f"Zum Bestaetigen den Namen des groessten Haushalts eintippen: {erwartet}")
            if input("> ").strip() != erwartet:
                print("Abgebrochen - nichts geaendert.")
                return 1
        else:
            frage = 'Schema verwerfen und neu aufbauen? Dafuer "ja" eintippen: '
            if input(frage).strip().lower() != "ja":
                print("Abgebrochen - nichts geaendert.")
                return 1

    print("Verwerfe Schema public ...")
    _psql(dsn, befehl="drop schema if exists public cascade; create schema public;")
    print("Spiele 0001_schema.sql ein ...")
    _psql(dsn, datei=SCHEMA_FILE.read_bytes())

    _, neu_gesamt, neue_groesse = _bestandsaufnahme(dsn)
    print(f"\nFertig. {neu_gesamt} Haushalte, {neue_groesse}.")
    print("Die Dienste neu starten - offene Verbindungen halten alte Zustaende.")
    print("Testdaten anlegen: python scripts/seed_dev.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
