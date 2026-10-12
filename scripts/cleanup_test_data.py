"""Entfernt die Hinterlassenschaften vergangener Testlaeufe aus der Datenbank.

Hintergrund: die Testsuiten legten ihre Haushalte ueber `POST /v1/households`
bzw. direkt per SQL an, ohne sie je wieder abzubauen (`return` statt `yield`,
kein Teardown). Ueber wenige Entwicklungstage sind so ueber 22.000 Haushalte
und ueber 1 Million Kategorien aufgelaufen - bei einer Handvoll echter Daten.

Die Ursache ist in packages/test-fixtures/dbcleanup.py behoben: seither raeumt
eine autouse-Fixture nach jedem Test auf. Dieses Skript ist fuer die Altlast,
die davor entstanden ist - ein einmaliger Lauf, der danach nichts mehr findet.

    python scripts/cleanup_test_data.py            # Trockenlauf, aendert nichts
    python scripts/cleanup_test_data.py --apply    # loescht wirklich

ERHALTUNGSREGEL: ein Haushalt bleibt, wenn ihm mindestens ein Mitglied
angehoert, dessen Anmeldung KEINE Testadresse ist. Das ist bewusst keine
Namensliste - ein Testhaushalt kann jeden beliebigen Namen tragen ("Zuhause",
"Familie A"), aber er haengt immer an einer erzeugten Testanmeldung. Was
gehalten wird, druckt das Skript vor jeder Aenderung aus.
"""

import argparse
import os
import sys

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"

# Mailadressen, die die Testsuiten erzeugen (uuid4-Praefix + feste Domaene).
TEST_DOMAINS = ("@finanzplaner.test", "@example.test")

_KEEP_HOUSEHOLDS = """
    select distinct m.household_id
    from household_members m
    join auth.users u on u.id = m.user_id
    where not ({domain_filter})
"""
_DOMAIN_FILTER = " or ".join(f"u.email like '%{d}'" for d in TEST_DOMAINS)


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _tabellen_mit_household(cur: psycopg.Cursor) -> set[str]:
    cur.execute(
        "select table_name from information_schema.columns "
        "where column_name = 'household_id' and table_schema = 'public'"
    )
    return {row[0] for row in cur.fetchall()}


def _blockierende_kanten(cur: psycopg.Cursor) -> list[tuple[str, str]]:
    """Fremdschluessel, die vor dem Ziel abgeraeumt sein muessen.

    'no action' und 'restrict' verhindern das Loeschen offensichtlich. 'set
    null' zaehlt hier ebenfalls dazu, obwohl es sich formal von selbst
    aufloest: bei einem mehrspaltigen Fremdschluessel setzt PostgreSQL ALLE
    beteiligten Spalten auf NULL, und ist eine davon 'not null', scheitert
    genau daran das Loeschen. Raeumt man die verweisende Tabelle vorher ab,
    entsteht die Situation gar nicht erst - und es spart nebenbei ein
    UPDATE ueber die halbe Tabelle.

    'cascade' bleibt aussen vor, das erledigt sich zuverlaessig selbst.
    Selbstbezuege ebenfalls: die loest eine einzige mengenbasierte Anweisung,
    weil PostgreSQL erst an deren Ende prueft."""
    cur.execute(
        "select con.conrelid::regclass::text, con.confrelid::regclass::text "
        "from pg_constraint con "
        "where con.contype = 'f' and con.confdeltype in ('a', 'r', 'n') "
        "and con.connamespace = 'public'::regnamespace"
    )
    return [(quelle, ziel) for quelle, ziel in cur.fetchall() if quelle != ziel]


def _loeschreihenfolge(tabellen: set[str], kanten: list[tuple[str, str]]) -> list[str]:
    """Ordnet die Tabellen so, dass jede vor der steht, auf die sie zeigt.

    Ohne diese Ordnung muesste man sich auf die Kaskade verlassen - und die
    arbeitet Zeile fuer Zeile: PostgreSQL setzt jede Fremdschluesselregel als
    eigene Anweisung 'delete from kind where household_id = $1' je geloeschtem
    Elternsatz um. Bei 22.000 Haushalten und einer Million Kategorien laeuft
    das stundenlang, mengenbasiert je Tabelle sind es Sekunden.

    Verfahren nach Kahn: zuerst die Tabellen, auf die niemand mehr zeigt."""
    eingang = dict.fromkeys(tabellen, 0)
    ausgang: dict[str, list[str]] = {t: [] for t in tabellen}
    for quelle, ziel in kanten:
        if quelle in tabellen and ziel in tabellen:
            ausgang[quelle].append(ziel)
            eingang[ziel] += 1

    bereit = sorted(t for t in tabellen if eingang[t] == 0)
    reihenfolge: list[str] = []
    while bereit:
        tabelle = bereit.pop(0)
        reihenfolge.append(tabelle)
        for ziel in ausgang[tabelle]:
            eingang[ziel] -= 1
            if eingang[ziel] == 0:
                bereit.append(ziel)
        bereit.sort()

    # Ein Zyklus waere hier nicht aufloesbar; die uebrigen Tabellen kommen
    # zum Schluss und werden notfalls von der Kaskade des Haushalts erledigt.
    reihenfolge.extend(sorted(tabellen - set(reihenfolge)))
    return reihenfolge


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Aenderungen wirklich ausfuehren"
    )
    parser.add_argument(
        "--skip-vacuum",
        action="store_true",
        help="kein VACUUM FULL (schneller, gibt aber keinen Plattenplatz zurueck)",
    )
    args = parser.parse_args()

    with psycopg.connect(_dsn(), autocommit=True) as con, con.cursor() as cur:
        cur.execute("select pg_size_pretty(pg_database_size(current_database()))")
        groesse_vorher = cur.fetchone()[0]

        keep_sql = _KEEP_HOUSEHOLDS.format(domain_filter=_DOMAIN_FILTER)
        cur.execute(f"""
            select h.id, h.name,
                   (select count(*) from transactions t where t.household_id = h.id)
            from households h
            where h.id in ({keep_sql})
            order by 3 desc
        """)
        behalten = cur.fetchall()

        cur.execute(f"select count(*) from households where id not in ({keep_sql})")
        zu_loeschen = cur.fetchone()[0]

        cur.execute(
            f"select count(*) from auth.users where {_DOMAIN_FILTER.replace('u.email', 'email')}"
        )
        nutzer_zu_loeschen = cur.fetchone()[0]

        print(f"Datenbankgroesse aktuell: {groesse_vorher}\n")
        print("BLEIBT ERHALTEN:")
        for hid, name, tx in behalten:
            print(f"  {str(name)[:40]:40} {tx:>6} Buchungen   {hid}")
        print(f"\nWIRD GELOESCHT: {zu_loeschen} Haushalte, {nutzer_zu_loeschen} Testanmeldungen")

        if not args.apply:
            print("\nTrockenlauf - es wurde nichts geaendert.")
            print("Zum Ausfuehren: python scripts/cleanup_test_data.py --apply")
            return 0

        if not behalten:
            print("\nABBRUCH: kein einziger Haushalt wuerde erhalten bleiben.")
            print("Das deutet auf einen Fehler in der Erhaltungsregel hin, nicht auf")
            print("eine leere Datenbank - es wird nichts geloescht.")
            return 1

        reihenfolge = _loeschreihenfolge(
            _tabellen_mit_household(cur), _blockierende_kanten(cur)
        )
        print(f"\nLeere {len(reihenfolge)} Tabellen mengenbasiert ...")
        for tabelle in reihenfolge:
            cur.execute(
                f"delete from {tabelle} where household_id not in ({keep_sql})"  # noqa: S608
            )
            if cur.rowcount:
                print(f"  {tabelle:34} {cur.rowcount:>9} Zeilen")

        print("Loesche Haushalte ...")
        cur.execute(f"delete from households where id not in ({keep_sql})")
        print(f"  {cur.rowcount} Haushalte entfernt (Restdaten haengen per cascade daran)")

        print("Loesche Testanmeldungen ...")
        cur.execute(
            f"delete from auth.users where {_DOMAIN_FILTER.replace('u.email', 'email')}"
        )
        print(f"  {cur.rowcount} Anmeldungen entfernt")

        if not args.skip_vacuum:
            print("VACUUM FULL (holt den Plattenplatz zurueck, dauert einen Moment) ...")
            cur.execute("vacuum full")

        cur.execute("select pg_size_pretty(pg_database_size(current_database()))")
        print(f"\nDatenbankgroesse jetzt: {cur.fetchone()[0]} (vorher {groesse_vorher})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
