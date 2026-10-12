"""Loescht genau eine von E2E-Tests erzeugte lokale Identitaet.

Das Skript ist absichtlich eng: nur Adressen der reservierten Testdomains
werden akzeptiert. Haushalte werden nur entfernt, wenn kein echtes Mitglied
darin steckt. Playwright ruft es im ``finally`` seiner Fixtures auf, damit
auch fehlgeschlagene Browsertests den Entwicklungs-Postgres nicht fuellen.
"""

import argparse
import os
import sys

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"
TEST_DOMAINS = ("@finanzplaner.test", "@example.test")


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _is_test_email(email: str) -> bool:
    normalized = email.strip().lower()
    return any(normalized.endswith(domain) for domain in TEST_DOMAINS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    email = args.email.strip().lower()
    if not _is_test_email(email):
        print("ABBRUCH: Es werden ausschliesslich reservierte Testadressen geloescht.")
        return 2

    with psycopg.connect(_dsn()) as connection, connection.cursor() as cur:
        cur.execute("select id from auth.users where lower(email) = %s", (email,))
        user = cur.fetchone()
        if user is None:
            return 0
        user_id = user[0]

        # created_by deckt den normalen Onboarding-Weg ab, die Mitgliedschaft
        # auch spaeter eingeladene Testnutzer. Ein Haushalt mit irgendeinem
        # Nicht-Testmitglied ist tabu, selbst wenn der Testnutzer ihn erzeugt
        # haben sollte.
        domain_sql = " or ".join("lower(u.email) like %s" for _ in TEST_DOMAINS)
        domain_values = tuple(f"%{domain}" for domain in TEST_DOMAINS)
        cur.execute(
            f"""
            select distinct h.id
            from households h
            left join household_members own on own.household_id = h.id
            where (h.created_by = %s or own.user_id = %s)
              and not exists (
                select 1 from household_members real_member
                join auth.users u on u.id = real_member.user_id
                where real_member.household_id = h.id
                  and not ({domain_sql})
              )
            """,  # noqa: S608 - nur feste Domain-Klauseln
            (user_id, user_id, *domain_values),
        )
        household_ids = [row[0] for row in cur.fetchall()]
        if household_ids:
            cur.execute("delete from households where id = any(%s)", (household_ids,))
        cur.execute("delete from auth.users where id = %s", (user_id,))
        connection.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
