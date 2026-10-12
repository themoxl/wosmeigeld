"""Exportiert genau EINEN Haushalt als einspielbare .sql-Datei.

Gedacht fuer den Umzug der Entwicklungsdatenbank auf einen echten Server
(siehe infra/nas/README.md): dort soll der reale Haushalt landen, nicht die
Testhaushalte, die daneben liegen.

    python scripts/export_household.py --household <uuid> --out umzug.sql
    python scripts/export_household.py --list

Abgrenzung zu scripts/db_snapshot.py: das sichert die GANZE Datenbank fuer
"ich will etwas ausprobieren und danach zurueck". Hier geht es um einen
Ausschnitt, der woanders wieder eingespielt wird.

AUFBAU DER AUSGABE
Erzeugt wird pg_dumps Format: je Tabelle ein `COPY ... FROM stdin` mit
expliziter Spaltenliste, gefolgt von den Daten. Einspielen mit

    psql -U postgres -d postgres -f umzug.sql

WARUM DIE REIHENFOLGE DER TABELLEN EGAL IST
Die Datei stellt `session_replication_role = replica` voran. In dieser
Betriebsart prueft PostgreSQL weder Fremdschluessel noch fuehrt es Trigger
aus. Ohne das muesste der Export die 114 Tabellen topologisch sortieren, und
schon ein Zyklus im Schema (die es gibt: Buchungen zeigen auf Vertraege,
Vertraege ueber contract_events zurueck auf Buchungen) haette keine gueltige
Reihenfolge. Nebenbei bleiben so auch `updated_at`-Trigger stumm, sodass der
Zielbestand die Zeitstempel des Originals traegt statt der des Umzugs.

WAS MITKOMMT
  * alle Tabellen mit household_id: nur Zeilen dieses Haushalts
  * households/profiles/device_push_tokens: nur der Haushalt bzw. seine
    Mitglieder
  * aus dem auth-Schema: die Konten der Mitglieder samt Passwort-Hash, damit
    die Anmeldung am Zielserver unveraendert funktioniert

WAS NICHT MITKOMMT
Stammdaten ohne Haushaltsbezug (Kategorienvorlagen, Versicherungssparten,
Anbietervorlagen und dergleichen): die stehen als insert-Anweisungen in
supabase/migrations/0001_schema.sql und sind am Ziel schon da, sobald das
Schema eingespielt ist.

Ebenso wenig Sitzungen und Aktualisierungstoken (auth.sessions,
auth.refresh_tokens). Die sind an den alten Server gebunden; am neuen meldet
man sich einmal neu an.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"

# Tabellen ohne household_id, die trotzdem personenbezogen sind und deshalb
# ueber die Mitglieder des Haushalts eingegrenzt werden muessen.
_NUTZERBEZOGEN = {
    "profiles": "user_id",
    "device_push_tokens": "user_id",
}

# Aus dem auth-Schema nur diese Spalten. Die Schemata von Supabase Auth
# unterscheiden sich zwischen Versionen erheblich (die lokale Instanz kennt
# bereits webauthn- und oauth-Tabellen, ein selbstgehostetes GoTrue oft noch
# nicht). Eine feste, seit Jahren stabile Spaltenauswahl uebersteht diesen
# Unterschied, ein `select *` nicht.
_AUTH_USERS_SPALTEN = [
    "instance_id", "id", "aud", "role", "email", "encrypted_password",
    "email_confirmed_at", "invited_at", "confirmation_sent_at",
    "recovery_sent_at", "last_sign_in_at", "raw_app_meta_data",
    "raw_user_meta_data", "is_super_admin", "created_at", "updated_at",
]
_AUTH_IDENTITIES_SPALTEN = [
    "provider_id", "user_id", "identity_data", "provider", "last_sign_in_at",
    "created_at", "updated_at", "id",
]


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _spalten(cur: psycopg.Cursor, schema: str, tabelle: str) -> list[str]:
    cur.execute(
        "select column_name from information_schema.columns "
        "where table_schema = %s and table_name = %s "
        # Erzeugte Spalten kann man nicht beschreiben; PostgreSQL weist ein
        # COPY mit einer solchen Spalte rundheraus ab.
        "and is_generated = 'NEVER' and identity_generation is null "
        "order by ordinal_position",
        (schema, tabelle),
    )
    return [r[0] for r in cur.fetchall()]


def _copy_block(
    cur: psycopg.Cursor,
    ziel,
    schema: str,
    tabelle: str,
    spalten: list[str],
    where: str,
    parameter: tuple,
) -> int:
    spaltenliste = ", ".join(f'"{s}"' for s in spalten)
    abfrage = f'copy (select {spaltenliste} from {schema}."{tabelle}" {where}) to stdout'

    puffer = bytearray()
    with cur.copy(abfrage, parameter) as copy:
        for daten in copy:
            puffer += daten

    if not puffer:
        return 0

    ziel.write(f'copy {schema}."{tabelle}" ({spaltenliste}) from stdin;\n')
    ziel.write(puffer.decode("utf-8"))
    ziel.write("\\.\n\n")
    return puffer.count(b"\n")


def _haushalte_auflisten(cur: psycopg.Cursor) -> None:
    cur.execute(
        "select h.id, h.name, "
        "(select count(*) from transactions t where t.household_id = h.id) "
        "from households h order by 3 desc, 2"
    )
    for kennung, name, buchungen in cur.fetchall():
        print(f"  {kennung}  {name!r:40} {buchungen:>6} Buchungen")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--household", help="UUID des zu exportierenden Haushalts")
    parser.add_argument("--out", help="Zieldatei (.sql)")
    parser.add_argument("--list", action="store_true", help="nur die Haushalte auflisten")
    args = parser.parse_args()

    with psycopg.connect(_dsn()) as con, con.cursor() as cur:
        if args.list:
            _haushalte_auflisten(cur)
            return 0

        if not args.household or not args.out:
            parser.error("--household und --out werden gebraucht (oder --list)")

        cur.execute("select name from households where id = %s", (args.household,))
        zeile = cur.fetchone()
        if not zeile:
            print(f"Haushalt {args.household} existiert nicht. Vorhanden:", file=sys.stderr)
            _haushalte_auflisten(cur)
            return 1
        haushaltsname = zeile[0]

        cur.execute(
            "select user_id from household_members where household_id = %s",
            (args.household,),
        )
        mitglieder = [r[0] for r in cur.fetchall()]
        if not mitglieder:
            print("Warnung: dieser Haushalt hat kein Mitglied - am Ziel kann sich niemand anmelden.", file=sys.stderr)

        cur.execute(
            "select t.table_name, exists ("
            "  select 1 from information_schema.columns c "
            "  where c.table_schema = 'public' and c.table_name = t.table_name "
            "  and c.column_name = 'household_id') "
            "from information_schema.tables t "
            "where t.table_schema = 'public' and t.table_type = 'BASE TABLE' "
            "order by 1"
        )
        tabellen = cur.fetchall()

        ziel_pfad = Path(args.out)
        gesamt = 0
        with ziel_pfad.open("w", encoding="utf-8", newline="\n") as ziel:
            ziel.write(f"-- Haushalt {haushaltsname!r} ({args.household})\n")
            ziel.write(f"-- Mitglieder: {len(mitglieder)}\n")
            ziel.write("-- Erzeugt von scripts/export_household.py\n\n")
            ziel.write("begin;\n")
            ziel.write("set session_replication_role = replica;\n\n")

            for tabelle, hat_household in tabellen:
                spalten = _spalten(cur, "public", tabelle)
                if not spalten:
                    continue

                if tabelle == "households":
                    where, parameter = "where id = %s", (args.household,)
                elif hat_household:
                    where, parameter = "where household_id = %s", (args.household,)
                elif tabelle in _NUTZERBEZOGEN:
                    if not mitglieder:
                        continue
                    where = f"where {_NUTZERBEZOGEN[tabelle]} = any(%s)"
                    parameter = (mitglieder,)
                else:
                    # Uebrig bleiben ausschliesslich Stammdaten:
                    # Kategorienvorlagen, Taxonomieversionen, Anbieter- und
                    # Projektvorlagen, Versicherungssparten. Die stehen als
                    # insert-Anweisungen IN 0001_schema.sql und sind am Ziel
                    # bereits da, sobald das Schema eingespielt ist. Sie
                    # trotzdem mitzunehmen, war der erste Anlauf - er endete
                    # mit
                    #   ERROR: duplicate key value violates unique constraint
                    #   "category_group_templates_taxonomy_version_semantic_key_key"
                    # und liess wegen der umschliessenden Transaktion die
                    # gesamte Uebernahme zurueckrollen.
                    continue

                zeilen = _copy_block(cur, ziel, "public", tabelle, spalten, where, parameter)
                if zeilen:
                    gesamt += zeilen
                    print(f"  public.{tabelle:38} {zeilen:>6}")

            if mitglieder:
                for tabelle, spalten_wunsch, spalte in (
                    ("users", _AUTH_USERS_SPALTEN, "id"),
                    ("identities", _AUTH_IDENTITIES_SPALTEN, "user_id"),
                ):
                    vorhanden = set(_spalten(cur, "auth", tabelle))
                    spalten = [s for s in spalten_wunsch if s in vorhanden]
                    zeilen = _copy_block(
                        cur, ziel, "auth", tabelle, spalten,
                        f"where {spalte} = any(%s)", (mitglieder,),
                    )
                    gesamt += zeilen
                    print(f"  auth.{tabelle:40} {zeilen:>6}")

            if mitglieder:
                # Leerstrings statt NULL in den Token-Spalten von auth.users.
                #
                # Supabase Auth liest diese Spalten in Go-Felder vom Typ
                # `string` - nicht `*string`. Ein NULL laesst dort schon das
                # SUCHEN des Kontos scheitern, lange bevor ein Passwort
                # geprueft wird:
                #   error finding user: sql: Scan error on column index 3,
                #   name "confirmation_token": converting NULL to string is
                #   unsupported
                # Nach aussen sieht das aus wie ein Serverfehler ("500:
                # Database error querying schema"), im Browser wie ein
                # unerreichbarer Anmeldedienst - der Weg zur eigentlichen
                # Ursache ist entsprechend lang.
                #
                # Die Spalten stehen bewusst NICHT in _AUTH_USERS_SPALTEN:
                # sie tragen abgelaufene Einmal-Token, die am Ziel nichts
                # verloren haben. Sie muessen nur leer sein statt NULL.
                #
                # Der Umweg ueber information_schema haelt das gegen
                # Versionsunterschiede offen - welche dieser Spalten es gibt,
                # haengt von der Version von Supabase Auth am ZIEL ab.
                ziel.write(
                    "do $$\n"
                    "declare spalte text;\n"
                    "begin\n"
                    "  foreach spalte in array array[\n"
                    "    'confirmation_token', 'recovery_token',\n"
                    "    'email_change_token_new', 'email_change',\n"
                    "    'email_change_token_current', 'phone_change',\n"
                    "    'phone_change_token', 'reauthentication_token'\n"
                    "  ] loop\n"
                    "    if exists (select 1 from information_schema.columns\n"
                    "               where table_schema = 'auth' and table_name = 'users'\n"
                    "                 and column_name = spalte and is_nullable = 'YES') then\n"
                    "      execute format('update auth.users set %I = %L where %I is null',\n"
                    "                     spalte, '', spalte);\n"
                    "    end if;\n"
                    "  end loop;\n"
                    "  if exists (select 1 from information_schema.columns\n"
                    "             where table_schema = 'auth' and table_name = 'users'\n"
                    "               and column_name = 'email_change_confirm_status') then\n"
                    "    update auth.users set email_change_confirm_status = 0\n"
                    "     where email_change_confirm_status is null;\n"
                    "  end if;\n"
                    "end $$;\n\n"
                )

            # Fortlaufende Zaehler nachziehen. Die meisten Schluessel sind
            # UUIDs, aber wo es eine Sequenz gibt, stuende sie am Ziel sonst
            # auf 1 und die naechste Einfuegung liefe in einen Konflikt.
            ziel.write(
                "do $$\n"
                "declare r record;\n"
                "begin\n"
                "  for r in\n"
                "    select c.table_name, c.column_name,\n"
                "           pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) as seq\n"
                "    from information_schema.columns c\n"
                "    where c.table_schema = 'public'\n"
                "      and pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) is not null\n"
                "  loop\n"
                "    execute format(\n"
                "      'select setval(%L, coalesce((select max(%I) from public.%I), 1), true)',\n"
                "      r.seq, r.column_name, r.table_name);\n"
                "  end loop;\n"
                "end $$;\n\n"
            )
            ziel.write("set session_replication_role = origin;\n")
            ziel.write("commit;\n")

        groesse = ziel_pfad.stat().st_size
        print()
        print(f"{gesamt} Zeilen -> {ziel_pfad} ({groesse / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
