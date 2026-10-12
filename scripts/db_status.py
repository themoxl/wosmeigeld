"""Zeigt auf einen Blick, was in der Entwicklungsdatenbank steckt.

Entstanden aus einem konkreten Aerger: ueber Tage waren unbemerkt 22.000
Testhaushalte und eine Million Kategorien aufgelaufen, weil die Testsuiten
nichts abbauten - sichtbar wurde das erst, als jemand gezielt nachsah. Wer
keinen SQL-Zugang griffbereit hat, soll den Zustand trotzdem jederzeit
ablesen koennen:

    python scripts/db_status.py

Die Ausgabe trennt echte Haushalte von Testrueckstaenden. Ein Testhaushalt
haengt immer an einer erzeugten Testanmeldung (@finanzplaner.test /
@example.test) - das ist dasselbe Kriterium, nach dem
scripts/cleanup_test_data.py aufraeumt.
"""

import os
import sys

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"
TEST_DOMAINS = ("@finanzplaner.test", "@example.test")

_DOMAIN_FILTER = " or ".join(f"u.email like '%{d}'" for d in TEST_DOMAINS)
_ECHTE_HAUSHALTE = f"""
    select distinct m.household_id from household_members m
    join auth.users u on u.id = m.user_id
    where not ({_DOMAIN_FILTER})
"""


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN).replace(
        "postgresql+psycopg://", "postgresql://"
    )
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute("select pg_size_pretty(pg_database_size(current_database()))")
        print(f"Datenbank: {cur.fetchone()[0]}\n")

        print("ECHTE HAUSHALTE")
        print(f"  {'Name':28} {'Konten':>7} {'Buchungen':>10} {'Vertraege':>10} {'Importe':>8}")
        cur.execute(f"""
            select h.name,
                   (select count(*) from accounts a where a.household_id = h.id),
                   (select count(*) from transactions t
                     where t.household_id = h.id and not t.is_archived),
                   (select count(*) from contracts c where c.household_id = h.id),
                   (select count(*) from import_runs i where i.household_id = h.id)
            from households h where h.id in ({_ECHTE_HAUSHALTE})
            order by 3 desc
        """)
        for name, konten, tx, vertraege, importe in cur.fetchall():
            print(f"  {str(name)[:28]:28} {konten:>7} {tx:>10} {vertraege:>10} {importe:>8}")

        cur.execute(f"select count(*) from households where id not in ({_ECHTE_HAUSHALTE})")
        test_haushalte = cur.fetchone()[0]
        cur.execute(
            f"select count(*) from auth.users u where {_DOMAIN_FILTER}"
        )
        test_nutzer = cur.fetchone()[0]

        print(f"\nTESTRUECKSTAENDE\n  {test_haushalte} Haushalte, {test_nutzer} Anmeldungen")
        if test_haushalte > 50:
            print("  -> Auffaellig viel. Aufraeumen mit:")
            print("     python scripts/cleanup_test_data.py --apply")
        elif test_haushalte:
            print("  -> Unauffaellig (laufender Testlauf oder abgebrochene Sitzung).")
        else:
            print("  -> Sauber.")

        print("\nGROESSTE TABELLEN")
        cur.execute("""
            select c.relname, pg_size_pretty(pg_total_relation_size(c.oid)),
                   coalesce(s.n_live_tup, 0)
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            left join pg_stat_user_tables s on s.relid = c.oid
            where n.nspname = 'public' and c.relkind = 'r'
            order by pg_total_relation_size(c.oid) desc limit 8
        """)
        for relname, groesse, zeilen in cur.fetchall():
            print(f"  {relname:32} {groesse:>10} {zeilen:>10} Zeilen")

        print("\nLETZTE IMPORTLAEUFE")
        cur.execute(f"""
            select i.filename, i.detected_format, i.status, i.total_rows, i.created_at::date
            from import_runs i where i.household_id in ({_ECHTE_HAUSHALTE})
            order by i.created_at desc limit 8
        """)
        zeilen = cur.fetchall()
        if not zeilen:
            print("  (keine)")
        for filename, format_name, status, rows, tag in zeilen:
            print(f"  {tag}  {str(filename)[:38]:38} {str(format_name):13} "
                  f"{str(status):10} {rows:>5} Zeilen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
