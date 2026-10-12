-- Minimaler Ersatz fuer das von Supabase bereitgestellte "auth"-Schema, nur
-- fuer den isolierten Restoretest (restore_test.sh) gegen ein einfaches
-- postgres:15-alpine ohne echtes Supabase. Deckt exakt die Objekte ab, die
-- supabase/migrations/0001_schema.sql tatsaechlich referenziert (per
-- `grep -n "auth\." supabase/migrations/0001_schema.sql` verifiziert):
-- auth.users(id) als FK-Ziel, die Funktion auth.uid() in RLS-Policies/
-- Helper-Funktionen, sowie die Rollen "anon" und "authenticated".
--
-- Wird das Schema um eine neue auth.*-Abhaengigkeit erweitert (z.B.
-- auth.jwt() oder ein weiteres auth.users-Feld), schlaegt der Restoretest
-- beim Anwenden von 0001_schema.sql mit einer klaren Postgres-Fehlermeldung
-- fehl - das ist ein Feature dieses Stubs, kein Bug: dann muss dieser Stub
-- erweitert werden, bevor der Restoretest wieder gruen wird.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
end
$$;

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid()
);

create or replace function auth.uid() returns uuid
language sql stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
