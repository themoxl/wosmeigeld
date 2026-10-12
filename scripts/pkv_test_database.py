"""Isolierte PostgreSQL-Datenbank fuer den PKV-PDF-Abnahmetest.

Kein Kopieren oder Bereinigen von Nutzerdaten. Nur selbst erzeugte und
markierte Datenbanken mit dem festen Testpraefix lassen sich entfernen.
"""

import argparse
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

ADMIN_DSN = os.environ.get("PKV_TEST_ADMIN_DSN",
                           "postgresql://postgres:postgres@127.0.0.1:55322/postgres")
PREFIX = "pkv_pdf_verify_"
MARKER = "finanzplaner:isolated-pkv-pdf-test"
ROOT = Path(__file__).resolve().parents[1]


def create() -> str:
    name = PREFIX + uuid4().hex[:12]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute("""do $$ begin
          if not exists (select 1 from pg_roles where rolname = 'authenticated') then
            create role authenticated nologin;
          end if;
          if not exists (select 1 from pg_roles where rolname = 'anon') then
            create role anon nologin;
          end if;
        end $$;""")
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        admin.execute(sql.SQL("comment on database {} is {}").format(
            sql.Identifier(name), sql.Literal(MARKER)))
    dsn = ADMIN_DSN.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(dsn) as connection:
        connection.execute("""
            create schema auth;
            create table auth.users (
              id uuid primary key, email text, raw_user_meta_data jsonb default '{}'
            );
            create function auth.uid() returns uuid language sql stable as $$
              select (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
            $$;
            create function auth.role() returns text language sql stable as $$
              select nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role'
            $$;
            grant usage on schema auth to authenticated;
        """)
        connection.execute((ROOT / "supabase/migrations/0001_schema.sql").read_text("utf-8"))
    return name


def drop(name: str) -> None:
    if not name.startswith(PREFIX) or not name[len(PREFIX):].isalnum():
        raise ValueError("Kein gueltiger PKV-Testdatenbankname")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        row = admin.execute(
            "select shobj_description(oid, 'pg_database') from pg_database where datname = %s",
            (name,),
        ).fetchone()
        if row is None or row[0] != MARKER:
            raise ValueError("Die Datenbank traegt nicht die PKV-Testmarkierung")
        admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop")
    args = parser.parse_args()
    if args.drop:
        drop(args.drop)
        print("Isolierte PKV-Testdatenbank entfernt.")
    else:
        print(create())
