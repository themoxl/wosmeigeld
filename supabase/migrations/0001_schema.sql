-- ============================================================================
-- Finanzplaner -- Gesamtschema (EINE DATEI, WIRD DIREKT BEARBEITET)
-- ============================================================================
-- Diese Datei ist das vollstaendige Datenmodell. Waehrend der
-- Entwicklungsphase gibt es bewusst KEINE nummerierten Einzelmigrationen:
-- jede Schemaaenderung wird hier direkt eingearbeitet, an einer sinnvollen
-- Stelle nahe den verwandten Anweisungen.
--
-- Warum: das Projekt hat keinen Produktionsstand, dessen Historie zu
-- bewahren waere. Es gibt genau eine Datenbank, den Entwicklungsrechner.
-- Eine wachsende Kette von Einzelmigrationen kostet dort nur Aufmerksamkeit
-- und verdeckt den tatsaechlichen Stand des Schemas. Der Weg zurueck zu
-- nummerierten Migrationen ist ausdruecklich vorgesehen, sobald es echte
-- Produktionsdaten gibt - diese Datei ist dann die neue Baseline.
--
-- Zwei frueher hier stehende Regeln gelten NICHT mehr:
--   * "EINGEFROREN, NICHT MEHR BEARBEITEN" (Stand bis 2026-08-03)
--   * "jede Aenderung als neue Datei 0002_..., 0003_..." (derselbe Stand)
-- Die verbindliche Fassung steht in supabase/README.md.
--
-- Nebenwirkung, die dabei einen echten Fehler behoben hat: das
-- Restore-Werkzeug (infra/backup/lib.sh, SCHEMA_FILE) spielt ausschliesslich
-- diese eine Datei gegen eine leere Datenbank ein. Solange 0002..0014
-- danebenlagen, stellte ein Restore ein Schema her, das dreizehn
-- Migrationen hinterherhinkte.
--
-- Entstehung: Die Datei entstand in drei Zusammenfuehrungen. Zuerst aus elf
-- Einzelmigrationen (0001_init.sql .. 0011_classification_runs.sql), danach
-- am 2026-08-03 aus den inzwischen erneut angewachsenen Dateien
-- 0002_audit_log.sql .. 0014_contracts.sql, zuletzt am 2026-08-04 aus
-- 0015_contract_intelligence.sql. Jedes Mal in unveraenderter
-- Anwendungsreihenfolge; die Abschnittsmarkierungen ("Ehemals: ...") unten
-- entsprechen den ehemaligen Dateigrenzen.
--
-- Achtung: "inhaltlich unveraendert" gilt jeweils nur fuer den Moment der
-- Zusammenfuehrung. Danach wurde und wird direkt hier weitergearbeitet -
-- massgeblich ist ausschliesslich das, was unten tatsaechlich steht.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Ehemals: 0001_init.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Phase 1 Baseline (Supabase/Postgres)
-- Auth, Haushalt und Datenbank. Siehe FINANZ_APP_ARCHITEKTURPLAN.md
-- Abschnitt 6 (Fachliches Datenmodell) und Abschnitt 17 (Phase 1).
--
-- Anwenden: 1) 0001_init.sql  2) 0002_rls.sql
-- Konvention uebernommen aus einem frueheren Supabase-Projekt (0001/0002-Split).
-- ============================================================================

create extension if not exists pgcrypto;

create type household_role as enum ('owner', 'editor', 'viewer', 'child');
create type account_type as enum (
  'checking', 'credit_card', 'savings', 'cash', 'brokerage', 'loan', 'real_estate', 'other'
);
create type transaction_status as enum ('pending', 'booked', 'reconciled');
create type transaction_source as enum ('manual', 'import', 'bank_sync');

-- ---------- 6.1 Identitaet und Haushalt ------------------------------------

create table profiles (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  display_name  text,
  language      text not null default 'de',
  timezone      text not null default 'Europe/Vienna',
  currency      text not null default 'EUR',
  date_format   text not null default 'de' check (date_format in ('iso', 'de', 'us')),
  created_at    timestamptz not null default now()
);

-- Push-Benachrichtigungen (Phase 9): Geraeteregistrierung fuer Expo Push.
-- Nutzer-skopiert statt Haushalts-skopiert, da ein Geraet zu einer Person
-- gehoert, nicht zu einem einzelnen aktiven Haushalts-Request-Kontext (siehe
-- current_household_id()/RLS unten bei den uebrigen Tabellen). Der Worker
-- (services/worker) verbindet sich mit erweiterten Rechten (kein SET LOCAL
-- ROLE authenticated, siehe worker/db.py) und umgeht RLS damit ohnehin -
-- die Policy unten deckt ausschliesslich den API-Zugriff im Namen des
-- angemeldeten Nutzers ab (Registrierung/Abmeldung des eigenen Geraets).
create table device_push_tokens (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  expo_token    text not null unique check (length(btrim(expo_token)) > 0),
  platform      text not null check (platform in ('ios', 'android')),
  device_name   text,
  is_valid      boolean not null default true,
  last_seen_at  timestamptz not null default now(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index device_push_tokens_user_idx on device_push_tokens (user_id) where is_valid;

create table households (
  id                      uuid primary key default gen_random_uuid(),
  name                    text not null check (length(btrim(name)) between 1 and 160),
  base_currency           text not null default 'EUR',
  timezone                text not null default 'Europe/Vienna',
  budget_method           text not null default 'tracking' check (budget_method in ('tracking', 'envelope')),
  fiscal_year_start_month int not null default 1 check (fiscal_year_start_month between 1 and 12),
  status                  text not null default 'active' check (status in ('active', 'archived')),
  version                 int not null default 1,
  created_by              uuid references auth.users(id) on delete set null,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);

create table household_members (
  household_id uuid not null references households(id) on delete cascade,
  user_id      uuid not null references auth.users(id) on delete cascade,
  role         household_role not null default 'editor',
  status       text not null default 'active' check (status in ('invited', 'active', 'removed')),
  invited_by   uuid references auth.users(id) on delete set null,
  invited_at   timestamptz not null default now(),
  joined_at    timestamptz,
  primary key (household_id, user_id)
);
create index household_members_user_idx
  on household_members (user_id, household_id) where status = 'active';

-- ---------- 6.2 Konten ------------------------------------------------------

create table accounts (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade,
  name                  text not null check (length(btrim(name)) between 1 and 160),
  account_type          account_type not null,
  institution           text,
  currency              text not null default 'EUR',
  on_budget             boolean not null default true,
  opening_balance_minor bigint not null default 0,
  opening_balance_date  date,
  is_closed             boolean not null default false,
  iban_last4            text check (iban_last4 is null or iban_last4 ~ '^[0-9]{4}$'),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index accounts_household_idx on accounts (household_id) where not is_closed;

create table account_balance_snapshots (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade,
  account_id    uuid not null references accounts(id) on delete cascade,
  as_of_date    date not null,
  balance_minor bigint not null,
  source        text not null default 'manual' check (source in ('manual', 'import', 'calculated')),
  created_at    timestamptz not null default now(),
  unique (account_id, as_of_date, source)
);

-- ---------- 6.3 Haendler und Kategorien -------------------------------------

create table category_groups (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade,
  name          text not null check (length(btrim(name)) between 1 and 120),
  sort_order    int not null default 0,
  created_at    timestamptz not null default now(),
  unique (household_id, name)
);

create table categories (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade,
  group_id      uuid not null references category_groups(id) on delete cascade,
  name          text not null check (length(btrim(name)) between 1 and 120),
  color         text,
  icon          text,
  is_budgeted   boolean not null default true,
  default_type  text check (default_type in ('income', 'expense', 'transfer')),
  budget_bucket text check (budget_bucket in ('needs', 'wants', 'savings')),
  is_archived   boolean not null default false,
  sort_order    int not null default 0,
  created_at    timestamptz not null default now(),
  unique (household_id, group_id, name)
);
create index categories_household_idx on categories (household_id) where not is_archived;

create table payees (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references households(id) on delete cascade,
  name                text not null check (length(btrim(name)) between 1 and 160),
  payee_type          text,
  default_category_id uuid references categories(id) on delete set null,
  is_active           boolean not null default true,
  created_at          timestamptz not null default now(),
  unique (household_id, name)
);

create table payee_aliases (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade,
  raw_label    text not null,
  payee_id     uuid not null references payees(id) on delete cascade,
  source       text not null default 'manual' check (source in ('import', 'rule', 'user', 'ai')),
  created_at   timestamptz not null default now(),
  unique (household_id, raw_label)
);

-- ---------- 6.4 Buchungen (Basisbuchungen fuer Phase 1) --------------------
-- Splits, Transferverknuepfung und Abstimmung mit vollem UI-Funktionsumfang
-- folgen in Phase 2 (Buchungsregister). Die Tabellen existieren bereits ab
-- Phase 1, damit das Modell von Anfang an kanonisch ist (Abschnitt 6.4:
-- "Ausgaben = kategorisierte Ausgabensplits").

create table transactions (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade,
  account_id        uuid not null references accounts(id) on delete cascade,
  booked_date       date not null,
  value_date        date,
  amount_minor      bigint not null,
  currency          text not null default 'EUR',
  payee_id          uuid references payees(id) on delete set null,
  payee_label       text,
  note              text,
  status            transaction_status not null default 'booked',
  source            transaction_source not null default 'manual',
  is_transfer       boolean not null default false,
  is_archived       boolean not null default false,
  created_by        uuid references auth.users(id) on delete set null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index transactions_household_account_idx
  on transactions (household_id, account_id, booked_date desc) where not is_archived;

create table transaction_splits (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade,
  transaction_id uuid not null references transactions(id) on delete cascade,
  category_id    uuid references categories(id) on delete set null,
  goal_id        uuid,
  amount_minor   bigint not null,
  note           text,
  created_at     timestamptz not null default now()
);
create index transaction_splits_transaction_idx on transaction_splits (transaction_id);
-- goal_id referenziert savings_goals, das erst weiter unten in dieser Datei
-- angelegt wird -> FK-Constraint folgt dort per ALTER TABLE (nach der
-- savings_goals-Definition), keine Vorwaertsreferenz hier moeglich.

-- ============================================================================
-- Haushaltskontext
-- ============================================================================
-- Die API validiert die Mitgliedschaft selbst (Abschnitt 5, Punkt 5) und
-- setzt danach den Haushaltskontext explizit als Session-GUC
-- (`SET LOCAL app.household_id = ...`), zusaetzlich zu den von PostgREST
-- bekannten `request.jwt.claims` (fuer auth.uid()/auth.role()), die die API
-- beim direkten Verbindungsaufbau nachbildet. RLS prueft die Mitgliedschaft
-- serverseitig erneut (zweite Schutzschicht, Abschnitt 5).

create or replace function current_household_id()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('app.household_id', true), '')::uuid
$$;

create or replace function is_household_member(p_household_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from household_members hm
    where hm.household_id = p_household_id
      and hm.user_id = auth.uid()
      and hm.status = 'active'
  )
$$;

create or replace function current_household_role()
returns household_role
language sql
stable
security definer
set search_path = public
as $$
  select hm.role
  from household_members hm
  where hm.household_id = current_household_id()
    and hm.user_id = auth.uid()
    and hm.status = 'active'
$$;

create or replace function can_edit_household()
returns boolean language sql stable as $$
  select current_household_role() in ('owner', 'editor')
$$;

-- Bestehende BFF-Inserts erhalten ihren Kontext als sicheren SQL-Default
-- (uebernommene Konvention): household_id wird, wenn nicht explizit
-- gesetzt, aus dem Session-GUC uebernommen. Erlaubt einfache Inserts aus der
-- API ohne household_id in jedem Statement zu wiederholen.
alter table accounts                  alter column household_id set default current_household_id();
alter table account_balance_snapshots alter column household_id set default current_household_id();
alter table category_groups           alter column household_id set default current_household_id();
alter table categories                alter column household_id set default current_household_id();
alter table payees                    alter column household_id set default current_household_id();
alter table payee_aliases             alter column household_id set default current_household_id();
alter table transactions              alter column household_id set default current_household_id();
alter table transaction_splits        alter column household_id set default current_household_id();

-- ============================================================================
-- Bootstrap: Haushalt anlegen
-- ============================================================================
-- Legt den Haushalt und die Owner-Mitgliedschaft atomar an und seedet die
-- Standard-Kategoriegruppen aus Abschnitt 6.3 ("Beispiele"), damit eine
-- manuelle Buchung sofort moeglich ist, ohne dass Nutzer zuerst ein
-- Kategorienschema von Hand aufbauen muessen.
create or replace function create_household(p_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_household_id uuid;
  v_income_group uuid;
  v_needs_group  uuid;
  v_wants_group  uuid;
  v_savings_group uuid;
  v_transfers_group uuid;
begin
  if auth.uid() is null then
    raise exception using errcode = '42501', message = 'AUTH_REQUIRED';
  end if;
  if btrim(coalesce(p_name, '')) = '' then
    raise exception using errcode = '22023', message = 'INVALID_HOUSEHOLD_NAME';
  end if;

  insert into households (name, created_by)
  values (btrim(p_name), auth.uid())
  returning id into v_household_id;

  insert into household_members (household_id, user_id, role, status, joined_at)
  values (v_household_id, auth.uid(), 'owner', 'active', now());

  insert into category_groups (household_id, name, sort_order) values
    (v_household_id, 'Einnahmen', 0) returning id into v_income_group;
  insert into category_groups (household_id, name, sort_order) values
    (v_household_id, 'Beduerfnisse', 1) returning id into v_needs_group;
  insert into category_groups (household_id, name, sort_order) values
    (v_household_id, 'Wuensche', 2) returning id into v_wants_group;
  insert into category_groups (household_id, name, sort_order) values
    (v_household_id, 'Sparen', 3) returning id into v_savings_group;
  insert into category_groups (household_id, name, sort_order) values
    (v_household_id, 'Transfers', 4) returning id into v_transfers_group;

  insert into categories (household_id, group_id, name, default_type, budget_bucket, sort_order) values
    (v_household_id, v_income_group, 'Gehalt', 'income', null, 0),
    (v_household_id, v_needs_group, 'Lebensmittel', 'expense', 'needs', 0),
    (v_household_id, v_needs_group, 'Wohnen', 'expense', 'needs', 1),
    (v_household_id, v_wants_group, 'Freizeit', 'expense', 'wants', 0),
    (v_household_id, v_savings_group, 'Sparziele', 'expense', 'savings', 0),
    (v_household_id, v_transfers_group, 'Interne Uebertragung', 'transfer', null, 0);

  return v_household_id;
end $$;

-- ---------------------------------------------------------------------------
-- Ehemals: 0002_rls.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Row Level Security fuer Phase 1
-- Nach 0001_init.sql anwenden. Ohne gueltigen Haushaltskontext
-- (app.household_id, siehe current_household_id() in 0001_init.sql) ist kein
-- Fachdatenzugriff moeglich; der Kontext waehlt nur, die aktive
-- Mitgliedschaft berechtigt (uebernommene Konvention).
-- ============================================================================

alter table profiles                  enable row level security;
alter table device_push_tokens        enable row level security;
alter table households                enable row level security;
alter table household_members         enable row level security;
alter table accounts                  enable row level security;
alter table account_balance_snapshots enable row level security;
alter table category_groups           enable row level security;
alter table categories                enable row level security;
alter table payees                    enable row level security;
alter table payee_aliases             enable row level security;
alter table transactions              enable row level security;
alter table transaction_splits        enable row level security;

-- ---------- Identitaet ------------------------------------------------------

create policy profiles_self on profiles for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- device_push_tokens: nutzer-skopiert wie profiles (kein Haushaltskontext,
-- siehe Tabellendefinition oben). Der Worker umgeht diese Policy ohnehin
-- ueber seine privilegierte Verbindung ohne SET LOCAL ROLE authenticated.
create policy device_push_tokens_self on device_push_tokens for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
grant select, insert, update, delete on device_push_tokens to authenticated;

-- ---------- Haushalte und Mitgliedschaft ------------------------------------
-- Haushalte werden ausschliesslich ueber create_household() angelegt (das
-- SECURITY-DEFINER-Bootstrap in 0001_init.sql); dafuer ist keine INSERT-Policy
-- fuer authenticated noetig.

create policy households_member_read on households for select to authenticated
  using (exists (
    select 1 from household_members hm
    where hm.household_id = households.id
      and hm.user_id = auth.uid()
      and hm.status = 'active'
  ));

create policy households_owner_update on households for update to authenticated
  using (
    id = current_household_id()
    and is_household_member(id)
    and current_household_role() = 'owner'
  )
  with check (
    id = current_household_id()
    and is_household_member(id)
    and current_household_role() = 'owner'
  );

create policy household_members_read on household_members for select to authenticated
  using (
    user_id = auth.uid()
    or (household_id = current_household_id() and is_household_member(household_id))
  );

create policy household_members_owner_write on household_members for all to authenticated
  using (
    household_id = current_household_id()
    and is_household_member(household_id)
    and current_household_role() = 'owner'
  )
  with check (
    household_id = current_household_id()
    and is_household_member(household_id)
    and current_household_role() = 'owner'
  );

-- ---------- Haushaltsgebundene Fachdaten ------------------------------------
-- Einheitliches Muster: Lesen fuer alle aktiven Mitglieder des im GUC
-- gesetzten Haushalts, Schreiben nur fuer owner/editor (nicht viewer/child).

create policy accounts_read on accounts for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy accounts_write on accounts for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy balance_snapshots_read on account_balance_snapshots for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy balance_snapshots_write on account_balance_snapshots for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy category_groups_read on category_groups for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy category_groups_write on category_groups for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy categories_read on categories for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy categories_write on categories for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy payees_read on payees for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy payees_write on payees for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy payee_aliases_read on payee_aliases for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy payee_aliases_write on payee_aliases for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy transactions_read on transactions for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy transactions_write on transactions for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy transaction_splits_read on transaction_splits for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy transaction_splits_write on transaction_splits for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

-- ---------- Grants -----------------------------------------------------------
-- Die API verbindet sich als "postgres" und wechselt je Request per
-- `SET LOCAL ROLE authenticated` in die eingeschraenkte Rolle (siehe
-- services/api/app/db/session.py) - RLS greift erst dann tatsaechlich,
-- BYPASSRLS von "postgres" gilt nur fuer die Login-Rolle, nicht fuer den per
-- SET ROLE aktiven current_user.
revoke all on schema public from anon;
grant usage on schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage, select on all sequences in schema public to authenticated;
revoke execute on all functions in schema public from public, anon;
grant execute on all functions in schema public to authenticated;

alter default privileges in schema public
  grant select, insert, update, delete on tables to authenticated;
alter default privileges in schema public
  grant usage, select on sequences to authenticated;
alter default privileges in schema public
  revoke execute on functions from public;
alter default privileges in schema public
  grant execute on functions to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0003_transfers_reconciliation.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Phase 2 (Buchungsregister): Transferverknuepfung und
-- Kontoabstimmung. Siehe FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 6.4
-- (transfer_links, reconciliations) und Abschnitt 17, Phase 2.
-- ============================================================================

create table transfer_links (
  id                       uuid primary key default gen_random_uuid(),
  household_id             uuid not null references households(id) on delete cascade,
  outgoing_transaction_id  uuid not null references transactions(id) on delete cascade,
  incoming_transaction_id  uuid not null references transactions(id) on delete cascade,
  match_status             text not null default 'manual' check (match_status in ('manual', 'suggested', 'matched')),
  match_reason             text,
  created_at               timestamptz not null default now(),
  check (outgoing_transaction_id <> incoming_transaction_id),
  unique (outgoing_transaction_id),
  unique (incoming_transaction_id)
);

create table reconciliations (
  id                       uuid primary key default gen_random_uuid(),
  household_id             uuid not null references households(id) on delete cascade,
  account_id               uuid not null references accounts(id) on delete cascade,
  as_of_date               date not null,
  statement_balance_minor  bigint not null,
  computed_balance_minor   bigint not null,
  difference_minor         bigint not null,
  status                   text not null check (status in ('completed', 'discrepancy')),
  completed_by             uuid references auth.users(id) on delete set null,
  created_at               timestamptz not null default now(),
  unique (account_id, as_of_date)
);
create index reconciliations_account_idx on reconciliations (account_id, as_of_date desc);

alter table transfer_links  alter column household_id set default current_household_id();
alter table reconciliations alter column household_id set default current_household_id();

alter table transfer_links  enable row level security;
alter table reconciliations enable row level security;

create policy transfer_links_read on transfer_links for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy transfer_links_write on transfer_links for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy reconciliations_read on reconciliations for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy reconciliations_write on reconciliations for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

-- Neu angelegte Tabellen erhalten dieselben Grants wie der Rest von public
-- (0002_rls.sql setzt "alter default privileges" fuer die migrationsausfuehrende
-- Rolle bereits global - hier zur Vermeidung stiller Fehlannahmen explizit).
grant select, insert, update, delete on transfer_links, reconciliations to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0004_imports.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Phase 3 (Importplattform): Importlaeufe, Staging-Zeilen,
-- Mapping-Profile. Siehe FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 6.10 und
-- Abschnitt 7 (Importarchitektur).
-- ============================================================================

create table import_mapping_profiles (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade,
  name           text not null check (length(btrim(name)) between 1 and 160),
  bank_format    text not null check (bank_format in ('csv_generic', 'xlsx_generic', 'mt940', 'camt', 'paypal_csv')),
  column_mapping jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  unique (household_id, name)
);

create table import_runs (
  id                       uuid primary key default gen_random_uuid(),
  household_id             uuid not null references households(id) on delete cascade,
  account_id               uuid references accounts(id) on delete set null,
  filename                 text not null,
  file_hash                text not null,
  file_size_bytes          bigint not null,
  detected_format          text,
  -- 'needs_mapping': die Datei wurde gelesen, aber Datum oder Betrag liessen
  -- sich keiner Spalte zuordnen. Frueher war das 'failed' - ein Totalausfall,
  -- aus dem nur ein neuer Upload herausfuehrte, obwohl die Spaltennamen der
  -- Datei bekannt sind und genau hier gebraucht werden. Aus 'needs_mapping'
  -- fuehrt die Zuordnungskorrektur (POST /{id}/reparse) heraus.
  status                   text not null default 'pending' check (
    status in ('pending', 'analysing', 'ready', 'needs_mapping', 'committing',
               'completed', 'failed', 'cancelled')
  ),
  total_rows               int not null default 0,
  new_count                int not null default 0,
  duplicate_count          int not null default 0,
  transfer_candidate_count int not null default 0,
  error_count              int not null default 0,
  error_message            text,
  started_by               uuid references auth.users(id) on delete set null,
  created_at               timestamptz not null default now(),
  -- started_at/heartbeat_at tragen die Haengerkennung (gleiches Muster wie
  -- ai_tasks): ohne Lebenszeichen laesst sich ein Lauf, den niemand abholt,
  -- nicht von einem unterscheiden, der gerade arbeitet.
  started_at               timestamptz,
  heartbeat_at             timestamptz,
  cancel_requested         boolean not null default false,
  -- Die Spaltennamen der Datei und einige Rohzeilen, geschrieben BEVOR das
  -- kanonische Mapping versucht wird. Nur dadurch koennen Vorschau und
  -- KI-Spaltenvorschlag im Fall 'needs_mapping' ueberhaupt etwas anzeigen -
  -- import_rows existieren dann naemlich noch nicht.
  detected_columns         jsonb,
  sample_raw               jsonb,
  -- Vom Nutzer korrigierte Spaltenzuordnung (POST /{id}/reparse). Sie steht am
  -- Lauf und nicht nur in der Queue-Nachricht, weil die hochgeladene Datei
  -- bewusst nirgends aufbewahrt wird: das erneute Einlesen arbeitet auf
  -- import_rows.raw_data, und der Worker muss die Zuordnung auch dann noch
  -- finden, wenn die Nachricht erneut zugestellt wird (Neustart, Wiedervorlage).
  -- Ohne diese Spalte wuerde ein solcher zweiter Anlauf wieder mit der alten,
  -- falschen Zuordnung parsen. Nicht null = "Rohzeilen erneut zuordnen",
  -- deshalb faehrt der Reparse-Pfad die Datei nicht mit.
  manual_column_mapping    jsonb,
  completed_at             timestamptz
);
create index import_runs_household_idx on import_runs (household_id, created_at desc);

create table import_rows (
  id                          uuid primary key default gen_random_uuid(),
  household_id                uuid not null references households(id) on delete cascade,
  import_run_id               uuid not null references import_runs(id) on delete cascade,
  row_number                  int not null,
  raw_data                    jsonb not null,
  external_reference          text,
  booked_date                 date,
  value_date                  date,
  amount_minor                bigint,
  currency                    text,
  payee_raw                   text,
  purpose                     text,
  bank_transaction_code       text,
  fingerprint                 text,
  status                      text not null default 'new' check (
    status in ('new', 'duplicate_exact', 'duplicate_possible', 'transfer_candidate', 'error', 'committed', 'skipped')
  ),
  duplicate_of_transaction_id uuid references transactions(id) on delete set null,
  committed_transaction_id    uuid references transactions(id) on delete set null,
  validation_errors           jsonb,
  created_at                  timestamptz not null default now()
);
create index import_rows_run_idx on import_rows (import_run_id, row_number);

alter table import_mapping_profiles alter column household_id set default current_household_id();
alter table import_runs             alter column household_id set default current_household_id();
alter table import_rows             alter column household_id set default current_household_id();

-- Buchungen, die aus einem Import entstanden sind, tragen die Herkunft und
-- den Dublettenschluessel (Abschnitt 7.4) direkt an der Buchung, damit
-- Folgeimporte ohne Blick in import_rows dagegen pruefen koennen.
alter table transactions add column import_run_id uuid references import_runs(id) on delete set null;
alter table transactions add column external_reference text;
alter table transactions add column fingerprint text;
create index transactions_fingerprint_idx
  on transactions (account_id, fingerprint) where fingerprint is not null;
create index transactions_external_reference_idx
  on transactions (account_id, external_reference) where external_reference is not null;

alter table import_mapping_profiles enable row level security;
alter table import_runs             enable row level security;
alter table import_rows             enable row level security;

create policy import_mapping_profiles_read on import_mapping_profiles for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy import_mapping_profiles_write on import_mapping_profiles for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy import_runs_read on import_runs for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy import_runs_write on import_runs for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy import_rows_read on import_rows for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy import_rows_write on import_rows for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete
  on import_mapping_profiles, import_runs, import_rows
  to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0005_rules_and_budgets.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Migration 0005: Regelengine, Fixkosten & Budgetkern (Phase 4 & 5)
-- ============================================================================

-- ---------- Phase 4: Klassifikationsregeln -----------------------------------

create table classification_rules (
  id                      uuid primary key default gen_random_uuid(),
  household_id            uuid not null references households(id) on delete cascade default current_household_id(),
  name                    text not null check (length(btrim(name)) between 1 and 160),
  priority                int not null default 0,
  is_active               boolean not null default true,
  match_payee_raw         text,
  match_purpose           text,
  match_min_amount_minor  bigint,
  match_max_amount_minor  bigint,
  match_account_id        uuid references accounts(id) on delete set null,
  action_payee_id         uuid references payees(id) on delete set null,
  action_category_id      uuid references categories(id) on delete set null,
  action_set_transfer     boolean not null default false,
  action_note             text,
  source                  text not null default 'user' check (source in ('user', 'ai_confirmed', 'imported')),
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);
create index classification_rules_household_idx on classification_rules (household_id, priority desc) where is_active;

-- Ehemals: 0015_advanced_classification_rules.sql
-- Feinere Trefferbedingungen: Wortgrenzen statt blosser Teilzeichenfolge
-- ("Otto" soll nicht in "Lotto" treffen), ausdrueckliche Ausschluesse und ein
-- Betragsfenster, das sich auch umkehren laesst. Die Spalten werden von
-- rules_service.py und routes/rules.py namentlich gelesen und geschrieben -
-- ohne sie laeuft die Regelengine gegen eine unvollstaendige Tabelle.
alter table classification_rules
  add column if not exists match_payee_exact_word boolean not null default false,
  add column if not exists match_purpose_exact_word boolean not null default false,
  add column if not exists exclude_payee_raw text,
  add column if not exists exclude_purpose text,
  add column if not exists match_amount_mode text not null default 'inside';
alter table classification_rules drop constraint if exists classification_rules_amount_mode_check;
alter table classification_rules add constraint classification_rules_amount_mode_check
  check (match_amount_mode in ('inside', 'outside'));

-- ---------- Phase 4: Wiederkehrende Regelmaessige Zahlungen & Abonnements ---

create table recurring_rules (
  id                     uuid primary key default gen_random_uuid(),
  household_id           uuid not null references households(id) on delete cascade default current_household_id(),
  account_id             uuid references accounts(id) on delete set null,
  payee_id               uuid references payees(id) on delete set null,
  category_id            uuid references categories(id) on delete set null,
  expected_amount_minor  bigint not null,
  frequency              text not null default 'monthly' check (frequency in ('weekly', 'monthly', 'quarterly', 'yearly')),
  next_due_date          date not null,
  tolerance_days         int not null default 3,
  tolerance_amount_minor bigint not null default 0,
  is_active              boolean not null default true,
  note                   text,
  -- Phase 9: Dedup fuer die "Fixkosten in Kuerze"-Push-Benachrichtigung -
  -- verhindert taegliches erneutes Benachrichtigen fuer dasselbe faellige
  -- Vorkommen (siehe services/worker/worker/notifications/due_recurring.py).
  notified_at            timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);
create index recurring_rules_household_idx on recurring_rules (household_id) where is_active;

create table subscriptions (
  id                      uuid primary key default gen_random_uuid(),
  household_id            uuid not null references households(id) on delete cascade default current_household_id(),
  provider                text not null check (length(btrim(provider)) between 1 and 160),
  plan_name               text,
  monthly_cost_minor      bigint not null,
  annual_cost_minor       bigint not null,
  cancellation_period_days int,
  next_renewal_date       date,
  category_id             uuid references categories(id) on delete set null,
  account_id              uuid references accounts(id) on delete set null,
  is_active               boolean not null default true,
  note                    text,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);
create index subscriptions_household_idx on subscriptions (household_id) where is_active;

-- ---------- Phase 5: Budgetkern ----------------------------------------------

create table budgets (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  year         int not null check (year between 2000 and 2100),
  month        int not null check (month between 1 and 12),
  method       text not null default 'tracking' check (method in ('tracking', 'envelope')),
  status       text not null default 'open' check (status in ('open', 'closed')),
  closed_at    timestamptz,
  closed_by    uuid references auth.users(id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (household_id, year, month)
);
create index budgets_household_ym_idx on budgets (household_id, year, month);

create table budget_allocations (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade default current_household_id(),
  budget_id      uuid not null references budgets(id) on delete cascade,
  category_id    uuid not null references categories(id) on delete cascade,
  planned_minor  bigint not null default 0,
  rollover_rule  text not null default 'none' check (rollover_rule in ('none', 'accumulate', 'reset')),
  note           text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (budget_id, category_id)
);
create index budget_allocations_budget_idx on budget_allocations (budget_id);

create table budget_rollovers (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  budget_id    uuid not null references budgets(id) on delete cascade,
  category_id  uuid not null references categories(id) on delete cascade,
  amount_minor bigint not null default 0,
  reason       text,
  created_at   timestamptz not null default now()
);

-- ---------- Row Level Security -----------------------------------------------

alter table classification_rules enable row level security;
alter table recurring_rules      enable row level security;
alter table subscriptions        enable row level security;
alter table budgets              enable row level security;
alter table budget_allocations   enable row level security;
alter table budget_rollovers     enable row level security;

create policy classification_rules_read on classification_rules for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy classification_rules_write on classification_rules for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy recurring_rules_read on recurring_rules for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy recurring_rules_write on recurring_rules for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy subscriptions_read on subscriptions for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy subscriptions_write on subscriptions for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy budgets_read on budgets for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy budgets_write on budgets for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy budget_allocations_read on budget_allocations for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy budget_allocations_write on budget_allocations for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy budget_rollovers_read on budget_rollovers for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy budget_rollovers_write on budget_rollovers for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on classification_rules, recurring_rules, subscriptions, budgets, budget_allocations, budget_rollovers to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0006_ai_and_jobs.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Migration 0006: Lokale KI, Modell-Call-Logs & AI Decisions (Phase 6)
-- ============================================================================

create table classification_decisions (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade default current_household_id(),
  transaction_id uuid references transactions(id) on delete set null,
  payee_name     text,
  category_id    uuid references categories(id) on delete set null,
  confidence     numeric(3,2) not null default 0.00 check (confidence between 0.00 and 1.00),
  reason_code    text not null default 'merchant_and_context_match',
  source         text not null default 'ai' check (source in ('rule', 'history', 'ai', 'manual')),
  model_name     text,
  created_at     timestamptz not null default now()
);
create index classification_decisions_household_idx on classification_decisions (household_id, created_at desc);

create table llm_calls (
  id               uuid primary key default gen_random_uuid(),
  household_id     uuid not null references households(id) on delete cascade default current_household_id(),
  stage            text not null default 'classify',
  model_name       text not null,
  status           text not null check (status in ('success', 'error', 'timeout', 'no_model')),
  duration_ms      int not null default 0,
  tokens_estimated int not null default 0,
  prompt_version   text not null default 'v1',
  error_message    text,
  created_at       timestamptz not null default now()
);
create index llm_calls_household_idx on llm_calls (household_id, created_at desc);

create table ai_household_settings (
  household_id                   uuid primary key references households(id) on delete cascade default current_household_id(),
  is_enabled                     boolean not null default true,
  auto_categorize_min_confidence numeric(3,2) not null default 0.85 check (auto_categorize_min_confidence between 0.00 and 1.00),
  custom_base_url                text,
  updated_at                     timestamptz not null default now()
);

-- ---------- Row Level Security -----------------------------------------------

alter table classification_decisions enable row level security;
alter table llm_calls                enable row level security;
alter table ai_household_settings    enable row level security;

create policy classification_decisions_read on classification_decisions for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy classification_decisions_write on classification_decisions for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy llm_calls_read on llm_calls for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy llm_calls_write on llm_calls for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy ai_household_settings_read on ai_household_settings for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy ai_household_settings_write on ai_household_settings for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on classification_decisions, llm_calls, ai_household_settings to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0007_goals_wishlist_assets.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Migration 0007: Sparziele, Wunschliste, Vermögen & Schulden (Phase 7)
-- ============================================================================

create table savings_goals (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade default current_household_id(),
  name                  text not null,
  target_amount_minor   bigint not null check (target_amount_minor > 0),
  starting_amount_minor bigint not null default 0 check (starting_amount_minor >= 0),
  target_date           date,
  planning_mode         text not null default 'target_date' check (
    planning_mode in ('target_date', 'monthly_amount')
  ),
  monthly_contribution_minor bigint check (monthly_contribution_minor > 0),
  include_in_planning   boolean not null default false,
  priority              int not null default 3 check (priority between 1 and 5),
  account_id            uuid references accounts(id) on delete set null,
  category_id           uuid references categories(id) on delete set null,
  status                text not null default 'active' check (status in ('active', 'reached', 'cancelled')),
  created_at            timestamptz not null default now()
);
create index savings_goals_household_idx on savings_goals (household_id, status);
comment on column savings_goals.starting_amount_minor is
  'Startbetrag vor Beginn der split-basierten Nachverfolgung (Architekturplan 6.7). '
  'Der laufende Stand (current_amount_minor in der API) wird berechnet als '
  'starting_amount_minor + Summe der amount_minor aller transaction_splits mit '
  'goal_id = diesem Sparziel (ohne archivierte Buchungen).';
comment on column savings_goals.include_in_planning is
  'Kalkulatorische Ruecklage fuer die Budget-Planung; erzeugt keine Buchung und veraendert keinen Kontostand.';

-- Additiv fuer laufende Entwicklungsdatenbanken. Legacy-Ziele mit Zieldatum
-- sind vollstaendig bestimmbar und koennen sicher einbezogen werden. Ziele
-- ohne Datum bleiben bewusst unvollstaendig, bis ein Monatsbetrag gewaehlt ist.
alter table savings_goals
  add column if not exists planning_mode text not null default 'target_date';
alter table savings_goals
  add column if not exists monthly_contribution_minor bigint;
alter table savings_goals
  add column if not exists include_in_planning boolean not null default false;
alter table savings_goals
  add column if not exists priority int not null default 3;
update savings_goals
set planning_mode = 'target_date', include_in_planning = true
where target_date is not null;
alter table savings_goals drop constraint if exists savings_goals_planning_mode_check;
alter table savings_goals add constraint savings_goals_planning_mode_check
  check (planning_mode in ('target_date', 'monthly_amount'));
alter table savings_goals drop constraint if exists savings_goals_monthly_contribution_check;
alter table savings_goals add constraint savings_goals_monthly_contribution_check
  check (monthly_contribution_minor is null or monthly_contribution_minor > 0);
alter table savings_goals drop constraint if exists savings_goals_priority_check;
alter table savings_goals add constraint savings_goals_priority_check
  check (priority between 1 and 5);

alter table transaction_splits
  add constraint transaction_splits_goal_id_fkey
  foreign key (goal_id) references savings_goals(id) on delete set null;
create index transaction_splits_goal_idx on transaction_splits (goal_id) where goal_id is not null;

create table wishlist_items (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  name         text not null,
  price_minor  bigint not null check (price_minor > 0),
  priority     int not null default 1,
  note         text,
  goal_id      uuid references savings_goals(id) on delete set null,
  status       text not null default 'open' check (
    status in ('open', 'deferred', 'purchased', 'archived')
  ),
  created_at   timestamptz not null default now()
);

-- Additiv fuer die bereits laufende Entwicklungsdatenbank: neue Installationen
-- haben die Spalte schon aus CREATE TABLE, bestehende erhalten sie hier.
alter table wishlist_items
  add column if not exists status text not null default 'open';
alter table wishlist_items drop constraint if exists wishlist_items_status_check;
alter table wishlist_items
  add constraint wishlist_items_status_check
  check (status in ('open', 'deferred', 'purchased', 'archived'));

create index if not exists wishlist_items_household_idx
  on wishlist_items (household_id, priority);
create index if not exists wishlist_items_household_status_idx
  on wishlist_items (household_id, status, priority);

create table assets_and_debts (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade default current_household_id(),
  name                  text not null,
  type                  text not null check (type in ('asset', 'debt')),
  asset_class           text not null default 'other' check (asset_class in ('real_estate', 'vehicle', 'depot', 'cash', 'loan', 'credit', 'other')),
  current_value_minor   bigint not null default 0 check (current_value_minor >= 0),
  interest_rate_percent numeric(5,2) default 0.00,
  monthly_payment_minor bigint default 0 check (monthly_payment_minor >= 0),
  account_id            uuid references accounts(id) on delete set null,
  updated_at            timestamptz not null default now(),
  created_at            timestamptz not null default now()
);
create index assets_and_debts_household_idx on assets_and_debts (household_id, type);

-- ---------- Row Level Security -----------------------------------------------

alter table savings_goals    enable row level security;
alter table wishlist_items   enable row level security;
alter table assets_and_debts enable row level security;

create policy savings_goals_read on savings_goals for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy savings_goals_write on savings_goals for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy wishlist_items_read on wishlist_items for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy wishlist_items_write on wishlist_items for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy assets_and_debts_read on assets_and_debts for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy assets_and_debts_write on assets_and_debts for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on savings_goals, wishlist_items, assets_and_debts to authenticated;

-- ---------------------------------------------------------------------------
-- Vorhaben & Grossinvestitionen (P0): Datenmodell
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner -- Vorhaben & Grossinvestitionen, Phase P0 (Finanzmathematisches
-- Fundament und Schema). Grundlage: docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md.
-- P0 legt laut Abschnitt 16 "alle Kern-/Katalogtabellen" auf einmal an, damit
-- spaetere Phasen (P1-P5: Web/Mobile-UI, Foerderkatalog-Import, Ist-Kosten,
-- Excel-Import) keine weiteren Schemabrueche mehr brauchen. Fachliche API-
-- Routen und Oberflaechen folgen erst in P1+; hier entsteht ausschliesslich
-- das Datenmodell samt RLS, zusammengesetzten Fremdschluesseln und Backfill.
-- ============================================================================

-- ---------- 0. Zusammengesetzte Eindeutigkeit an bestehenden Tabellen -------
-- accounts/savings_goals/assets_and_debts/transactions werden ab jetzt aus
-- den neuen Vorhaben-Tabellen heraus referenziert (Eigenkapitalquelle,
-- Sparziel-Zweckbindung, Asset-Verkauf, Ist-Kosten-Zuordnung). Damit eine
-- fremde UUID niemals als Referenz durchgeht, brauchen sie denselben
-- zusammengesetzten Unique-Index wie categories/category_groups (Plan 4.14).
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'accounts_id_household_uniq') then
    alter table accounts add constraint accounts_id_household_uniq unique (id, household_id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'savings_goals_id_household_uniq') then
    alter table savings_goals add constraint savings_goals_id_household_uniq unique (id, household_id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'assets_and_debts_id_household_uniq') then
    alter table assets_and_debts add constraint assets_and_debts_id_household_uniq unique (id, household_id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'transactions_id_household_uniq') then
    alter table transactions add constraint transactions_id_household_uniq unique (id, household_id);
  end if;
end $$;

-- ---------- 1. Gemeinsamer Versionstrigger fuer das Vorhaben-Modul ---------
-- Eine Funktion fuer alle fachlichen Schreibobjekte des Moduls (wie
-- bump_category_version fuer categories/category_groups): sie bumpt version
-- nur bei echter fachlicher Aenderung, unabhaengig vom Tabellentyp, weil sie
-- ausschliesslich ueber to_jsonb(new)/to_jsonb(old) vergleicht.
create or replace function bump_project_version()
returns trigger
language plpgsql
as $$
begin
  if (to_jsonb(new) - 'version' - 'updated_at')
     is distinct from (to_jsonb(old) - 'version' - 'updated_at') then
    new.version := old.version + 1;
    new.updated_at := now();
  else
    new.version := old.version;
    new.updated_at := old.updated_at;
  end if;
  return new;
end;
$$;

-- ---------- 2. Foerderkatalog: globale, nur lesbare Kataloge ---------------
-- Zuerst angelegt, weil project_financing_sources spaeter darauf verweist.
-- Gleiches Muster wie contract_provider_templates: RLS erlaubt select fuer
-- authenticated, insert/update/delete wird explizit entzogen -- geschrieben
-- wird ausschliesslich per Migration/Worker-Serviceverbindung (Plan 13.5).

-- `household_id is null` = kuratierter Katalog: gehoert dem Code, fuer jeden
-- lesbar, fuer niemanden schreibbar (die RLS-Policy weiter unten kann fuer NULL
-- nie wahr werden). Gesetzt = eigener Katalog eines Haushalts, entstanden ueber
-- den Paket-/Dateiimport. Das Matching liest beide und bevorzugt bei gleichem
-- `semantic_key` die Haushaltszeile -- sie ist der bewusst eingespielte Stand.
--
-- Bewusst EINE Tabelle statt zweier: `project_financing_sources.funding_program_
-- version_id` zeigt auf `funding_program_versions`. Getrennte Tabellen braeuchten
-- eine zweite Fremdschluesselspalte und einen zweiten Codepfad in der Uebernahme,
-- im Export und in der Kombinationspruefung.
create table if not exists funding_programs (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid references households(id) on delete cascade,
  semantic_key  text not null,
  provider_key  text not null,
  provider_name text not null,
  program_code  text,
  title         text not null,
  country_code  text not null check (country_code ~ '^[A-Z]{2}$'),
  scope         text not null check (scope in ('supranational', 'national', 'regional', 'municipal')),
  region_codes  text[] not null default '{}'::text[],
  official_url  text not null,
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);
alter table funding_programs add column if not exists household_id uuid
  references households(id) on delete cascade;
-- Frueher stand `semantic_key text not null unique` hier; der Schluessel ist jetzt
-- global (household_id is null) UND je Haushalt eindeutig. `drop index` funktioniert
-- dafuer nicht -- der Index gehoert zum Constraint.
alter table funding_programs drop constraint if exists funding_programs_semantic_key_key;
create unique index if not exists funding_programs_global_key_uniq
  on funding_programs (semantic_key) where household_id is null;
create unique index if not exists funding_programs_household_key_uniq
  on funding_programs (household_id, semantic_key) where household_id is not null;
create index if not exists funding_programs_lookup_idx
  on funding_programs (country_code, scope) where is_active;

create table if not exists funding_program_versions (
  id                     uuid primary key default gen_random_uuid(),
  program_id             uuid not null references funding_programs(id) on delete cascade,
  -- Denormalisiert aus `funding_programs`, damit die RLS-Policy ohne Unterabfrage
  -- auskommt. Der Import setzt den Wert; die Herkunft bleibt `program_id`.
  household_id           uuid references households(id) on delete cascade,
  source_version         text not null,
  valid_from             date,
  valid_to               date,
  published_at           timestamptz,
  retrieved_at           timestamptz not null default now(),
  review_due_at          timestamptz not null,
  currency               text not null default 'EUR' check (currency ~ '^[A-Z]{3}$'),
  benefit_type           text not null check (benefit_type in (
                           'grant', 'loan', 'loan_with_grant', 'guarantee', 'tax', 'information')),
  applicant_types        text[] not null default '{}'::text[],
  project_types          text[] not null default '{}'::text[],
  criteria_json          jsonb not null default '{}'::jsonb,
  benefit_json            jsonb not null default '{}'::jsonb,
  loan_terms_json           jsonb not null default '{}'::jsonb,
  combination_rules_json      jsonb not null default '{}'::jsonb,
  manual_checks_json             jsonb not null default '[]'::jsonb,
  source_url                       text not null,
  source_content_hash                text not null,
  license_name                       text,
  license_url                        text,
  attribution                        text,
  normalization_status               text not null default 'discovery_only' check (normalization_status in (
                                       'discovery_only', 'rules_partial', 'rules_complete', 'retired')),
  created_at                          timestamptz not null default now(),
  unique (program_id, source_version, source_content_hash)
);
alter table funding_program_versions add column if not exists household_id uuid
  references households(id) on delete cascade;
create index if not exists funding_program_versions_program_idx
  on funding_program_versions (program_id, retrieved_at desc);
comment on table funding_program_versions is
  'Unveraenderlich: eine veroeffentlichte Version wird nie in-place geaendert '
  '(Plan 4.11). Neue Quelldaten erzeugen immer eine neue Zeile.';

create table if not exists funding_catalog_runs (
  id                   uuid primary key default gen_random_uuid(),
  household_id         uuid references households(id) on delete cascade,
  -- 'adapter' = automatischer Feed-Abruf, 'package' = mitgeliefertes Foerderpaket,
  -- 'file' = vom Nutzer hochgeladene Paketdatei. Trennt Betriebslaeufe von dem,
  -- was ein Haushalt selbst eingespielt hat.
  source               text not null default 'adapter' check (source in ('adapter', 'package', 'file')),
  package_id           text,
  provider_key         text not null,
  status               text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
  started_at           timestamptz not null default now(),
  finished_at          timestamptz,
  etag                 text,
  source_content_hash  text,
  programs_new         int not null default 0,
  programs_changed     int not null default 0,
  programs_retired     int not null default 0,
  programs_unchanged   int not null default 0,
  warnings             jsonb not null default '[]'::jsonb,
  error_message        text,
  created_at           timestamptz not null default now()
);
alter table funding_catalog_runs add column if not exists household_id uuid
  references households(id) on delete cascade;
alter table funding_catalog_runs add column if not exists source text not null default 'adapter';
alter table funding_catalog_runs drop constraint if exists funding_catalog_runs_source_check;
alter table funding_catalog_runs add constraint funding_catalog_runs_source_check
  check (source in ('adapter', 'package', 'file'));
alter table funding_catalog_runs add column if not exists package_id text;
create index if not exists funding_catalog_runs_provider_idx
  on funding_catalog_runs (provider_key, started_at desc);

create table if not exists project_template_versions (
  id             uuid primary key default gen_random_uuid(),
  semantic_key   text not null,
  version        int not null default 1,
  locale         text not null default 'de-DE',
  country_code   text check (country_code is null or country_code ~ '^[A-Z]{2}$'),
  project_type   text not null check (project_type in (
                   'home_build', 'property_purchase', 'renovation', 'vehicle',
                   'solar', 'education', 'business', 'other')),
  name           text not null,
  description    text,
  template_json  jsonb not null,
  is_released    boolean not null default false,
  released_at    timestamptz,
  created_at     timestamptz not null default now(),
  unique (semantic_key, version)
);

alter table funding_programs         enable row level security;
alter table funding_program_versions enable row level security;
alter table funding_catalog_runs     enable row level security;
alter table project_template_versions enable row level security;

-- Der kuratierte Katalog (household_id is null) bleibt fuer jeden lesbar und fuer
-- niemanden schreibbar -- jetzt aber, weil die Schreib-Policy fuer NULL nie wahr
-- werden kann, nicht weil ein Grant fehlt. Eigene Programme eines Haushalts
-- liegen in denselben Tabellen und sind fuer fremde Haushalte unsichtbar.
drop policy if exists funding_programs_read on funding_programs;
create policy funding_programs_read on funding_programs for select to authenticated
  using (household_id is null
         or (household_id = current_household_id() and is_household_member(household_id)));
drop policy if exists funding_programs_write on funding_programs;
create policy funding_programs_write on funding_programs for all to authenticated
  using      (household_id = current_household_id() and can_edit_household())
  with check (household_id = current_household_id() and can_edit_household());

drop policy if exists funding_program_versions_read on funding_program_versions;
create policy funding_program_versions_read on funding_program_versions for select to authenticated
  using (household_id is null
         or (household_id = current_household_id() and is_household_member(household_id)));
drop policy if exists funding_program_versions_write on funding_program_versions;
create policy funding_program_versions_write on funding_program_versions for all to authenticated
  using      (household_id = current_household_id() and can_edit_household())
  with check (household_id = current_household_id() and can_edit_household());

drop policy if exists funding_catalog_runs_read on funding_catalog_runs;
create policy funding_catalog_runs_read on funding_catalog_runs for select to authenticated
  using (household_id is null
         or (household_id = current_household_id() and is_household_member(household_id)));
drop policy if exists funding_catalog_runs_write on funding_catalog_runs;
create policy funding_catalog_runs_write on funding_catalog_runs for all to authenticated
  using      (household_id = current_household_id() and can_edit_household())
  with check (household_id = current_household_id() and can_edit_household());

drop policy if exists project_template_versions_read on project_template_versions;
create policy project_template_versions_read on project_template_versions for select to authenticated using (true);

grant select on funding_programs, funding_program_versions, funding_catalog_runs, project_template_versions
  to authenticated;
grant insert, update, delete on funding_programs, funding_program_versions, funding_catalog_runs
  to authenticated;
revoke insert, update, delete on project_template_versions from authenticated;

-- ---------- 3. major_projects -----------------------------------------------

create table if not exists major_projects (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  name                  text not null check (length(btrim(name)) between 1 and 160),
  project_type          text not null check (project_type in (
                          'home_build', 'property_purchase', 'renovation', 'vehicle',
                          'solar', 'education', 'business', 'other')),
  status                text not null default 'idea' check (status in (
                          'idea', 'planning', 'approved', 'active', 'completed',
                          'cancelled', 'archived')),
  currency              text not null default 'EUR' check (currency ~ '^[A-Z]{3}$'),
  country_code          text not null default 'DE' check (country_code ~ '^[A-Z]{2}$'),
  region_code           text check (region_code is null or region_code ~ '^[A-Z]{2}-[A-Z0-9]{1,3}$'),
  postal_code           text,
  municipality          text,
  planned_start_on      date,
  planned_completion_on date,
  actual_start_on       date,
  actual_completion_on  date,
  result_asset_id       uuid,
  note                  text,
  version               int not null default 1,
  created_by            uuid references auth.users(id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (id, household_id),
  foreign key (result_asset_id, household_id) references assets_and_debts (id, household_id)
    on update cascade deferrable initially deferred,
  constraint major_projects_planned_dates_chk check (
    planned_completion_on is null or planned_start_on is null
    or planned_completion_on >= planned_start_on),
  constraint major_projects_actual_dates_chk check (
    actual_completion_on is null or actual_start_on is null
    or actual_completion_on >= actual_start_on)
);
create index if not exists major_projects_household_status_idx
  on major_projects (household_id, status, updated_at desc);

drop trigger if exists major_projects_bump_version on major_projects;
create trigger major_projects_bump_version
  before update on major_projects
  for each row execute function bump_project_version();

-- ---------- 4. project_scenarios ---------------------------------------------

create table if not exists project_scenarios (
  id                              uuid primary key default gen_random_uuid(),
  household_id                    uuid not null references households(id) on delete cascade
                                    default current_household_id(),
  project_id                      uuid not null,
  name                            text not null check (length(btrim(name)) between 1 and 120),
  scenario_kind                   text not null default 'baseline' check (scenario_kind in (
                                    'baseline', 'optimistic', 'stress', 'custom')),
  status                          text not null default 'draft' check (status in (
                                    'draft', 'selected', 'superseded')),
  source_scenario_id              uuid references project_scenarios(id) on delete set null,
  as_of_date                      date not null default current_date,
  contingency_mode                text not null default 'none' check (contingency_mode in (
                                    'none', 'percent', 'fixed')),
  contingency_rate_bps            int check (contingency_rate_bps is null
                                    or contingency_rate_bps between 0 and 10000),
  contingency_fixed_minor         bigint check (contingency_fixed_minor is null or contingency_fixed_minor >= 0),
  minimum_liquidity_reserve_minor bigint not null default 0 check (minimum_liquidity_reserve_minor >= 0),
  calculation_horizon_months      int not null default 360 check (calculation_horizon_months between 1 and 600),
  calculation_engine_version      text not null default 'v1',
  stress_transform_json           jsonb not null default '{}'::jsonb,
  selected_at                     timestamptz,
  selected_by                     uuid references auth.users(id) on delete set null,
  version                         int not null default 1,
  created_at                      timestamptz not null default now(),
  updated_at                      timestamptz not null default now(),
  unique (id, household_id),
  foreign key (project_id, household_id) references major_projects (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  constraint project_scenarios_contingency_chk check (
    (contingency_mode = 'none' and contingency_rate_bps is null and contingency_fixed_minor is null)
    or (contingency_mode = 'percent' and contingency_rate_bps is not null and contingency_fixed_minor is null)
    or (contingency_mode = 'fixed' and contingency_fixed_minor is not null and contingency_rate_bps is null)
  )
);
comment on column project_scenarios.source_scenario_id is
  'Einfache (nicht zusammengesetzte) Selbstreferenz: welches Szenario beim '
  'Klonen/Stresstest die Quelle war. Gleicher Haushalt/gleiches Projekt wird '
  'vom Service geprueft (project_service.clone_scenario), nicht von der DB --'
  ' analog zur Zykluspruefung bei Prozentpositionen (Plan 4.4).';

create index if not exists project_scenarios_project_idx
  on project_scenarios (project_id, status);
create unique index if not exists project_scenarios_one_selected_idx
  on project_scenarios (project_id) where status = 'selected';

drop trigger if exists project_scenarios_bump_version on project_scenarios;
create trigger project_scenarios_bump_version
  before update on project_scenarios
  for each row execute function bump_project_version();

-- ---------- 5. project_cost_sections / project_cost_items -------------------
-- Bewusst nur zwei Hierarchieebenen (Plan 4.3): eine tiefere Struktur wuerde
-- Bauprojektmanagement nachbauen.

create table if not exists project_cost_sections (
  id               uuid primary key default gen_random_uuid(),
  household_id     uuid not null references households(id) on delete cascade
                     default current_household_id(),
  scenario_id      uuid not null,
  semantic_key     text,
  name             text not null check (length(btrim(name)) between 1 and 160),
  cost_class       text not null default 'direct' check (cost_class in ('direct', 'soft', 'transition', 'other')),
  sort_order       int not null default 0,
  planned_start_on date,
  planned_end_on   date,
  note             text,
  version          int not null default 1,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (id, household_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  constraint project_cost_sections_dates_chk check (
    planned_end_on is null or planned_start_on is null or planned_end_on >= planned_start_on)
);
create unique index if not exists project_cost_sections_semantic_key_uniq
  on project_cost_sections (scenario_id, semantic_key) where semantic_key is not null;
create index if not exists project_cost_sections_scenario_idx
  on project_cost_sections (scenario_id, sort_order);

drop trigger if exists project_cost_sections_bump_version on project_cost_sections;
create trigger project_cost_sections_bump_version
  before update on project_cost_sections
  for each row execute function bump_project_version();

create table if not exists project_cost_items (
  id                        uuid primary key default gen_random_uuid(),
  household_id              uuid not null references households(id) on delete cascade
                              default current_household_id(),
  scenario_id               uuid not null,
  section_id                uuid not null,
  semantic_key              text,
  name                      text not null check (length(btrim(name)) between 1 and 160),
  cost_kind                 text not null default 'cash' check (cost_kind in ('cash', 'non_cash')),
  status                    text not null default 'estimated' check (status in (
                              'estimated', 'quoted', 'committed', 'partially_paid', 'paid', 'cancelled')),
  calculation_mode          text not null default 'fixed' check (calculation_mode in ('fixed', 'quantity', 'percentage')),
  estimate_manual_minor     bigint check (estimate_manual_minor is null or estimate_manual_minor >= 0),
  quantity                  numeric(18,4) check (quantity is null or quantity > 0),
  unit_label                text,
  unit_price_minor          bigint check (unit_price_minor is null or unit_price_minor >= 0),
  percentage_rate_bps       int check (percentage_rate_bps is null or percentage_rate_bps between 0 and 10000),
  percentage_basis          text check (percentage_basis is null or percentage_basis in (
                              'item', 'section_direct', 'project_direct')),
  percentage_basis_item_id  uuid,
  quoted_amount_minor       bigint check (quoted_amount_minor is null or quoted_amount_minor >= 0),
  committed_amount_minor    bigint check (committed_amount_minor is null or committed_amount_minor >= 0),
  contingency_eligible      boolean not null default true,
  funding_tags              text[] not null default '{}'::text[],
  vendor_label              text,
  expected_start_on         date,
  expected_end_on           date,
  sort_order                int not null default 0,
  note                      text,
  version                   int not null default 1,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  unique (id, household_id),
  unique (id, scenario_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (section_id, household_id) references project_cost_sections (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (percentage_basis_item_id, scenario_id) references project_cost_items (id, scenario_id)
    on update cascade deferrable initially deferred,
  constraint project_cost_items_dates_chk check (
    expected_end_on is null or expected_start_on is null or expected_end_on >= expected_start_on),
  constraint project_cost_items_calc_mode_chk check (
    (calculation_mode = 'fixed'
       and estimate_manual_minor is not null
       and quantity is null and unit_price_minor is null
       and percentage_rate_bps is null and percentage_basis is null and percentage_basis_item_id is null)
    or (calculation_mode = 'quantity'
       and quantity is not null and unit_price_minor is not null
       and estimate_manual_minor is null
       and percentage_rate_bps is null and percentage_basis is null and percentage_basis_item_id is null)
    or (calculation_mode = 'percentage'
       and percentage_rate_bps is not null and percentage_basis is not null
       and estimate_manual_minor is null and quantity is null and unit_price_minor is null
       and (percentage_basis = 'item') = (percentage_basis_item_id is not null))
  ),
  constraint project_cost_items_funding_tags_chk check (
    funding_tags <@ array[
      'land_purchase', 'construction', 'planning', 'permits', 'acquisition_fees',
      'energy_efficiency', 'heating', 'renewable_energy', 'accessibility', 'renovation',
      'equipment', 'transition', 'other'
    ]::text[])
);
comment on column project_cost_items.percentage_basis_item_id is
  'Nur bei percentage_basis=item gesetzt. Prozentpositionen duerfen nicht '
  'Basis anderer Prozentpositionen sein -- der azyklische Graph wird vom '
  'Service geprueft (project_calculation_service), nicht per DB-Constraint '
  '(Plan 4.4: "Der Service prueft einen azyklischen Graphen").';
create index if not exists project_cost_items_scenario_idx on project_cost_items (scenario_id, sort_order);
create index if not exists project_cost_items_section_idx on project_cost_items (section_id, sort_order);
create unique index if not exists project_cost_items_semantic_key_uniq
  on project_cost_items (scenario_id, semantic_key) where semantic_key is not null;

drop trigger if exists project_cost_items_bump_version on project_cost_items;
create trigger project_cost_items_bump_version
  before update on project_cost_items
  for each row execute function bump_project_version();

create table if not exists project_cost_schedule_entries (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  cost_item_id  uuid not null,
  due_on        date not null,
  amount_minor  bigint not null check (amount_minor > 0),
  status        text not null default 'planned' check (status in ('planned', 'cancelled')),
  label         text,
  sort_order    int not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (id, household_id),
  foreign key (cost_item_id, household_id) references project_cost_items (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists project_cost_schedule_entries_item_idx
  on project_cost_schedule_entries (cost_item_id, due_on);

create table if not exists project_cost_actuals (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade
                   default current_household_id(),
  cost_item_id   uuid not null,
  source         text not null check (source in ('transaction', 'manual')),
  transaction_id uuid,
  amount_minor   bigint not null,
  occurred_on    date,
  note           text,
  created_by     uuid references auth.users(id) on delete set null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (id, household_id),
  foreign key (cost_item_id, household_id) references project_cost_items (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (transaction_id, household_id) references transactions (id, household_id)
    on update cascade deferrable initially deferred,
  constraint project_cost_actuals_source_chk check (
    (source = 'transaction' and transaction_id is not null and occurred_on is null)
    or (source = 'manual' and transaction_id is null and occurred_on is not null)
  )
);
comment on column project_cost_actuals.amount_minor is
  'Positiv = Kosten, negativ = Erstattung (Plan 4.6). Projekt-Istkosten '
  'haengen bewusst an transaction_id, nicht an einer Split-ID: Splits werden '
  'beim Bearbeiten einer Buchung geloescht und neu angelegt.';
create unique index if not exists project_cost_actuals_txn_item_uniq
  on project_cost_actuals (cost_item_id, transaction_id) where transaction_id is not null;
create index if not exists project_cost_actuals_txn_idx
  on project_cost_actuals (transaction_id) where transaction_id is not null;
create index if not exists project_cost_actuals_item_idx on project_cost_actuals (cost_item_id);

-- ---------- 6. project_financing_sources -------------------------------------

create table if not exists project_financing_sources (
  id                          uuid primary key default gen_random_uuid(),
  household_id                uuid not null references households(id) on delete cascade
                                default current_household_id(),
  scenario_id                  uuid not null,
  name                          text not null check (length(btrim(name)) between 1 and 160),
  source_type                    text not null check (source_type in (
                                   'cash_equity', 'asset_sale', 'commercial_loan', 'subsidized_loan',
                                   'public_grant', 'private_contribution', 'non_cash_equity', 'other')),
  status                          text not null default 'idea' check (status in (
                                   'idea', 'requested', 'likely', 'approved', 'contracted', 'received', 'rejected')),
  planned_amount_minor             bigint not null check (planned_amount_minor >= 0),
  principal_limit_minor             bigint check (principal_limit_minor is null or principal_limit_minor >= 0),
  available_on                       date,
  account_id                          uuid,
  savings_goal_id                      uuid,
  asset_id                              uuid,
  funding_program_version_id             uuid references funding_program_versions(id) on delete set null,
  gross_amount_minor                       bigint check (gross_amount_minor is null or gross_amount_minor >= 0),
  deductions_minor                           bigint check (deductions_minor is null or deductions_minor >= 0),
  source_snapshot                              jsonb not null default '{}'::jsonb,
  sort_order                                    int not null default 0,
  note                                            text,
  version                                          int not null default 1,
  created_at                                        timestamptz not null default now(),
  updated_at                                         timestamptz not null default now(),
  unique (id, household_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (account_id, household_id) references accounts (id, household_id)
    on update cascade deferrable initially deferred,
  foreign key (savings_goal_id, household_id) references savings_goals (id, household_id)
    on update cascade deferrable initially deferred,
  foreign key (asset_id, household_id) references assets_and_debts (id, household_id)
    on update cascade deferrable initially deferred,
  constraint project_financing_sources_cash_equity_chk check (
    source_type <> 'cash_equity' or account_id is not null)
);
comment on column project_financing_sources.savings_goal_id is
  'Nur eine optionale Zweckbindungskennzeichnung -- zaehlt NICHT als '
  'zusaetzliche Quelle (Plan 4.7, Leitsatz 2). Das Geld liegt bereits auf '
  'account_id; das Sparziel darf niemals ein zweites Mal als Eigenkapital '
  'gezaehlt werden.';
create index if not exists project_financing_sources_scenario_idx
  on project_financing_sources (scenario_id, sort_order);

drop trigger if exists project_financing_sources_bump_version on project_financing_sources;
create trigger project_financing_sources_bump_version
  before update on project_financing_sources
  for each row execute function bump_project_version();

-- ---------- 7. debt_plans: gemeinsames Modell fuer Projekt- und Bestandsschulden

create table if not exists debt_plans (
  id                                      uuid primary key default gen_random_uuid(),
  household_id                            uuid not null references households(id) on delete cascade
                                            default current_household_id(),
  project_financing_source_id              uuid,
  asset_debt_id                             uuid,
  name                                       text not null check (length(btrim(name)) between 1 and 160),
  creditor_label                             text,
  currency                                    text not null default 'EUR' check (currency ~ '^[A-Z]{3}$'),
  status                                       text not null default 'draft' check (status in (
                                                'draft', 'offered', 'active', 'paid', 'cancelled')),
  principal_limit_minor                         bigint not null check (principal_limit_minor > 0),
  opening_balance_minor                          bigint not null default 0 check (opening_balance_minor >= 0),
  starts_on                                       date not null,
  payment_interval_months                          int not null default 1 check (payment_interval_months in (1, 3, 6, 12)),
  amortization_mode                                 text not null check (amortization_mode in (
                                                     'fixed_payment', 'initial_repayment', 'full_repayment_term')),
  fixed_payment_minor                                bigint check (fixed_payment_minor is null or fixed_payment_minor > 0),
  initial_repayment_rate_bps                          int check (initial_repayment_rate_bps is null
                                                       or initial_repayment_rate_bps between 0 and 10000),
  target_term_months                                   int check (target_term_months is null or target_term_months between 1 and 600),
  repayment_free_months                                 int not null default 0 check (repayment_free_months between 0 and 120),
  payment_free_months                                    int not null default 0 check (payment_free_months between 0 and 120),
  capitalize_interest_during_payment_free                 boolean not null default true,
  commitment_free_months                                   int not null default 0 check (commitment_free_months between 0 and 120),
  commitment_rate_bps_per_month                             int not null default 0 check (commitment_rate_bps_per_month between 0 and 10000),
  recast_payment_on_rate_change                              boolean not null default false,
  version                                                     int not null default 1,
  created_at                                                   timestamptz not null default now(),
  updated_at                                                    timestamptz not null default now(),
  unique (id, household_id),
  unique (project_financing_source_id),
  unique (asset_debt_id),
  foreign key (project_financing_source_id, household_id) references project_financing_sources (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (asset_debt_id, household_id) references assets_and_debts (id, household_id)
    on update cascade deferrable initially deferred,
  constraint debt_plans_owner_chk check (project_financing_source_id is not null or asset_debt_id is not null),
  constraint debt_plans_amortization_chk check (
    (amortization_mode = 'fixed_payment' and fixed_payment_minor is not null
       and initial_repayment_rate_bps is null and target_term_months is null)
    or (amortization_mode = 'initial_repayment' and initial_repayment_rate_bps is not null
       and fixed_payment_minor is null and target_term_months is null)
    or (amortization_mode = 'full_repayment_term' and target_term_months is not null
       and fixed_payment_minor is null and initial_repayment_rate_bps is null)
  )
);
comment on column debt_plans.repayment_free_months is
  'Tilgungsfrei: Zinsen werden gezahlt, Kapital nicht getilgt. Nicht zu '
  'verwechseln mit payment_free_months (Plan 4.8) -- beide Begriffe duerfen '
  'im UI nie gemeinsam als "tilgungsfrei" beschriftet werden.';
comment on column debt_plans.payment_free_months is
  'Zahlungsfrei: kein Cash-Abfluss; Zinsen werden je nach '
  'capitalize_interest_during_payment_free kapitalisiert oder als offene, '
  'nicht kapitalisierte Zahlung ausgewiesen.';
create index if not exists debt_plans_household_idx on debt_plans (household_id, status);

drop trigger if exists debt_plans_bump_version on debt_plans;
create trigger debt_plans_bump_version
  before update on debt_plans
  for each row execute function bump_project_version();

create table if not exists debt_rate_periods (
  id                          uuid primary key default gen_random_uuid(),
  household_id                uuid not null references households(id) on delete cascade
                                default current_household_id(),
  debt_plan_id                 uuid not null,
  starts_after_month            int not null check (starts_after_month >= 0),
  ends_after_month                int check (ends_after_month is null or ends_after_month >= starts_after_month),
  nominal_rate_bps_annual          int not null check (nominal_rate_bps_annual >= 0),
  effective_rate_bps_annual         int check (effective_rate_bps_annual is null or effective_rate_bps_annual >= 0),
  rate_kind                          text not null default 'contracted' check (rate_kind in ('contracted', 'assumed')),
  label                                text,
  unique (id, household_id),
  foreign key (debt_plan_id, household_id) references debt_plans (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists debt_rate_periods_plan_idx on debt_rate_periods (debt_plan_id, starts_after_month);

create table if not exists debt_draws (
  id                  uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade
                         default current_household_id(),
  debt_plan_id           uuid not null,
  occurs_after_month       int not null check (occurs_after_month >= 0),
  amount_minor               bigint not null check (amount_minor > 0),
  status                       text not null default 'planned' check (status in ('planned', 'actual')),
  unique (id, household_id),
  foreign key (debt_plan_id, household_id) references debt_plans (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists debt_draws_plan_idx on debt_draws (debt_plan_id, occurs_after_month);

create table if not exists debt_fees (
  id                  uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade
                         default current_household_id(),
  debt_plan_id           uuid not null,
  occurs_after_month       int not null check (occurs_after_month >= 0),
  amount_minor               bigint not null check (amount_minor > 0),
  fee_kind                     text not null default 'upfront' check (fee_kind in ('upfront', 'ongoing', 'other')),
  financed                       boolean not null default false,
  label                            text,
  unique (id, household_id),
  foreign key (debt_plan_id, household_id) references debt_plans (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists debt_fees_plan_idx on debt_fees (debt_plan_id, occurs_after_month);

create table if not exists debt_special_repayments (
  id                  uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade
                         default current_household_id(),
  debt_plan_id           uuid not null,
  occurs_after_month       int not null check (occurs_after_month >= 0),
  amount_minor               bigint not null check (amount_minor > 0),
  status                       text not null default 'planned' check (status in ('planned', 'actual')),
  label                           text,
  unique (id, household_id),
  foreign key (debt_plan_id, household_id) references debt_plans (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists debt_special_repayments_plan_idx
  on debt_special_repayments (debt_plan_id, occurs_after_month);

-- ---------- 8. Haushalts-, Ziel- und Folgekostenannahmen --------------------

create table if not exists project_cashflow_assumptions (
  scenario_id                          uuid primary key,
  household_id                          uuid not null references households(id) on delete cascade
                                          default current_household_id(),
  starting_liquidity_minor               bigint not null default 0 check (starting_liquidity_minor >= 0),
  monthly_net_income_minor                bigint not null default 0 check (monthly_net_income_minor >= 0),
  monthly_living_costs_minor               bigint not null default 0 check (monthly_living_costs_minor >= 0),
  monthly_current_housing_minor             bigint not null default 0 check (monthly_current_housing_minor >= 0),
  monthly_existing_debt_service_minor        bigint not null default 0 check (monthly_existing_debt_service_minor >= 0),
  monthly_other_commitments_minor             bigint not null default 0 check (monthly_other_commitments_minor >= 0),
  analysis_lookback_months                     int not null default 12 check (analysis_lookback_months between 3 and 24),
  source                                        text not null default 'manual' check (source in ('manual', 'history', 'mixed')),
  source_snapshot                                jsonb not null default '{}'::jsonb,
  updated_at                                      timestamptz not null default now(),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
comment on column project_cashflow_assumptions.starting_liquidity_minor is
  'Vom Nutzer bestaetigter Kontostand zum Szenario-Stichtag, abzueglich '
  'bereits ausgewaehlter Reservierungen anderer Projekte (Plan 4.9). Getrennt '
  'von monthly_living_costs_minor, damit Miete/Kreditrate/Sparrate nicht '
  'unbemerkt doppelt hineingerechnet werden.';

create table if not exists project_recurring_impacts (
  id                          uuid primary key default gen_random_uuid(),
  household_id                 uuid not null references households(id) on delete cascade
                                 default current_household_id(),
  scenario_id                   uuid not null,
  name                            text not null check (length(btrim(name)) between 1 and 160),
  impact_type                      text not null check (impact_type in ('income', 'expense_increase', 'expense_decrease')),
  amount_minor                       bigint not null check (amount_minor > 0),
  frequency                            text not null check (frequency in ('monthly', 'quarterly', 'semiannual', 'yearly', 'once')),
  starts_on                              date not null,
  ends_on                                  date,
  confidence                                text not null default 'estimated' check (confidence in (
                                             'contracted', 'estimated', 'speculative')),
  include_in_affordability                   boolean not null default true,
  note                                         text,
  version                                       int not null default 1,
  created_at                                     timestamptz not null default now(),
  updated_at                                      timestamptz not null default now(),
  unique (id, household_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  constraint project_recurring_impacts_dates_chk check (ends_on is null or ends_on >= starts_on)
);
comment on column project_recurring_impacts.confidence is
  'speculative ist standardmaessig NICHT in der konservativen Leistbarkeit '
  'enthalten (Plan 5.8) -- include_in_affordability steuert das explizit.';
create index if not exists project_recurring_impacts_scenario_idx on project_recurring_impacts (scenario_id);

drop trigger if exists project_recurring_impacts_bump_version on project_recurring_impacts;
create trigger project_recurring_impacts_bump_version
  before update on project_recurring_impacts
  for each row execute function bump_project_version();

create table if not exists project_goal_assumptions (
  id                                    uuid primary key default gen_random_uuid(),
  household_id                          uuid not null references households(id) on delete cascade
                                          default current_household_id(),
  scenario_id                            uuid not null,
  savings_goal_id                          uuid not null,
  withdrawal_minor                           bigint not null default 0 check (withdrawal_minor >= 0),
  withdrawal_on                                date,
  protected_minimum_minor                        bigint not null default 0 check (protected_minimum_minor >= 0),
  baseline_monthly_contribution_minor              bigint not null default 0 check (baseline_monthly_contribution_minor >= 0),
  project_monthly_contribution_minor                 bigint not null default 0 check (project_monthly_contribution_minor >= 0),
  created_at                                           timestamptz not null default now(),
  updated_at                                             timestamptz not null default now(),
  unique (id, household_id),
  unique (scenario_id, savings_goal_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (savings_goal_id, household_id) references savings_goals (id, household_id)
    on update cascade deferrable initially deferred
);

create table if not exists project_eligibility_profiles (
  project_id      uuid primary key,
  household_id     uuid not null references households(id) on delete cascade
                     default current_household_id(),
  schema_version    int not null default 1,
  facts               jsonb not null default '{}'::jsonb,
  version               int not null default 1,
  updated_by             uuid references auth.users(id) on delete set null,
  updated_at               timestamptz not null default now(),
  foreign key (project_id, household_id) references major_projects (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
comment on table project_eligibility_profiles is
  'Private Foerderkriterien-Eingaben, 1:1 zum Projekt (Plan 4.10). facts wird '
  'von versionierten Pydantic-Modellen validiert, nicht als beliebiges JSON '
  'akzeptiert. Verlaesst die Installation nie an Anbieter oder ein LLM.';

drop trigger if exists project_eligibility_profiles_bump_version on project_eligibility_profiles;
create trigger project_eligibility_profiles_bump_version
  before update on project_eligibility_profiles
  for each row execute function bump_project_version();

-- ---------- 9. Foerdercheck-Laeufe und Berechnungssnapshots -----------------

create table if not exists project_funding_check_runs (
  id                        uuid primary key default gen_random_uuid(),
  household_id               uuid not null references households(id) on delete cascade
                               default current_household_id(),
  scenario_id                 uuid not null,
  scenario_version              int not null,
  input_fingerprint               text not null,
  engine_version                    text not null default 'v1',
  used_program_version_ids            uuid[] not null default '{}'::uuid[],
  combination_results_json               jsonb not null default '{}'::jsonb,
  warnings_json                            jsonb not null default '[]'::jsonb,
  created_by                                 uuid references auth.users(id) on delete set null,
  created_at                                   timestamptz not null default now(),
  unique (id, household_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create unique index if not exists project_funding_check_runs_fingerprint_uniq
  on project_funding_check_runs (scenario_id, input_fingerprint);
create index if not exists project_funding_check_runs_scenario_idx
  on project_funding_check_runs (scenario_id, created_at desc);

create table if not exists project_funding_matches (
  id                          uuid primary key default gen_random_uuid(),
  household_id                 uuid not null references households(id) on delete cascade
                                 default current_household_id(),
  check_run_id                   uuid not null,
  program_version_id                uuid not null references funding_program_versions(id) on delete cascade,
  input_fingerprint                    text not null,
  input_snapshot                          jsonb not null default '{}'::jsonb,
  status                                    text not null check (status in (
                                             'eligible', 'potentially_eligible', 'ineligible', 'unknown')),
  estimated_grant_min_minor                  bigint check (estimated_grant_min_minor is null or estimated_grant_min_minor >= 0),
  estimated_grant_max_minor                    bigint check (estimated_grant_max_minor is null or estimated_grant_max_minor >= 0),
  estimated_loan_min_minor                       bigint check (estimated_loan_min_minor is null or estimated_loan_min_minor >= 0),
  estimated_loan_max_minor                         bigint check (estimated_loan_max_minor is null or estimated_loan_max_minor >= 0),
  criteria_met_json                                  jsonb not null default '[]'::jsonb,
  criteria_violated_json                               jsonb not null default '[]'::jsonb,
  criteria_missing_json                                  jsonb not null default '[]'::jsonb,
  criteria_manual_json                                     jsonb not null default '[]'::jsonb,
  match_engine_version                                      text not null default 'v1',
  checked_at                                                  timestamptz not null default now(),
  catalog_age_days                                              int,
  unique (id, household_id),
  foreign key (check_run_id, household_id) references project_funding_check_runs (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create unique index if not exists project_funding_matches_idempotent_uniq
  on project_funding_matches (check_run_id, program_version_id, input_fingerprint);
create index if not exists project_funding_matches_run_idx on project_funding_matches (check_run_id);

create table if not exists project_calculation_snapshots (
  id                  uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade
                         default current_household_id(),
  scenario_id            uuid not null,
  reason                   text not null check (reason in ('selected', 'approved', 'manual')),
  scenario_version           int not null,
  input_fingerprint             text not null,
  engine_version                   text not null default 'v1',
  summary_json                       jsonb not null default '{}'::jsonb,
  timeline_json                         jsonb not null default '[]'::jsonb,
  warnings_json                            jsonb not null default '[]'::jsonb,
  created_by                                 uuid references auth.users(id) on delete set null,
  created_at                                   timestamptz not null default now(),
  unique (id, household_id),
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
comment on table project_calculation_snapshots is
  'Unveraenderlicher Entscheidungsstand beim Auswaehlen/Bestaetigen eines '
  'Szenarios (Plan 4.12), kein Cache. Liveberechnungen selbst werden nicht '
  'gespeichert.';
create index if not exists project_calculation_snapshots_scenario_idx
  on project_calculation_snapshots (scenario_id, created_at desc);

-- ---------- 10. Row Level Security und Grants: haushaltsgebundene Tabellen --

alter table major_projects                 enable row level security;
alter table project_scenarios               enable row level security;
alter table project_cost_sections            enable row level security;
alter table project_cost_items                enable row level security;
alter table project_cost_schedule_entries       enable row level security;
alter table project_cost_actuals                 enable row level security;
alter table project_financing_sources             enable row level security;
alter table debt_plans                              enable row level security;
alter table debt_rate_periods                         enable row level security;
alter table debt_draws                                  enable row level security;
alter table debt_fees                                     enable row level security;
alter table debt_special_repayments                          enable row level security;
alter table project_cashflow_assumptions                        enable row level security;
alter table project_recurring_impacts                              enable row level security;
alter table project_goal_assumptions                                  enable row level security;
alter table project_eligibility_profiles                                 enable row level security;
alter table project_funding_check_runs                                      enable row level security;
alter table project_funding_matches                                            enable row level security;
alter table project_calculation_snapshots                                         enable row level security;

drop policy if exists major_projects_read on major_projects;
create policy major_projects_read on major_projects for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists major_projects_write on major_projects;
create policy major_projects_write on major_projects for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_scenarios_read on project_scenarios;
create policy project_scenarios_read on project_scenarios for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_scenarios_write on project_scenarios;
create policy project_scenarios_write on project_scenarios for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_cost_sections_read on project_cost_sections;
create policy project_cost_sections_read on project_cost_sections for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_sections_write on project_cost_sections;
create policy project_cost_sections_write on project_cost_sections for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_cost_items_read on project_cost_items;
create policy project_cost_items_read on project_cost_items for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_items_write on project_cost_items;
create policy project_cost_items_write on project_cost_items for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_cost_schedule_entries_read on project_cost_schedule_entries;
create policy project_cost_schedule_entries_read on project_cost_schedule_entries for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_schedule_entries_write on project_cost_schedule_entries;
create policy project_cost_schedule_entries_write on project_cost_schedule_entries for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_cost_actuals_read on project_cost_actuals;
create policy project_cost_actuals_read on project_cost_actuals for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_actuals_write on project_cost_actuals;
create policy project_cost_actuals_write on project_cost_actuals for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_financing_sources_read on project_financing_sources;
create policy project_financing_sources_read on project_financing_sources for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_financing_sources_write on project_financing_sources;
create policy project_financing_sources_write on project_financing_sources for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists debt_plans_read on debt_plans;
create policy debt_plans_read on debt_plans for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists debt_plans_write on debt_plans;
create policy debt_plans_write on debt_plans for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists debt_rate_periods_read on debt_rate_periods;
create policy debt_rate_periods_read on debt_rate_periods for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists debt_rate_periods_write on debt_rate_periods;
create policy debt_rate_periods_write on debt_rate_periods for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists debt_draws_read on debt_draws;
create policy debt_draws_read on debt_draws for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists debt_draws_write on debt_draws;
create policy debt_draws_write on debt_draws for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists debt_fees_read on debt_fees;
create policy debt_fees_read on debt_fees for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists debt_fees_write on debt_fees;
create policy debt_fees_write on debt_fees for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists debt_special_repayments_read on debt_special_repayments;
create policy debt_special_repayments_read on debt_special_repayments for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists debt_special_repayments_write on debt_special_repayments;
create policy debt_special_repayments_write on debt_special_repayments for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_cashflow_assumptions_read on project_cashflow_assumptions;
create policy project_cashflow_assumptions_read on project_cashflow_assumptions for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cashflow_assumptions_write on project_cashflow_assumptions;
create policy project_cashflow_assumptions_write on project_cashflow_assumptions for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_recurring_impacts_read on project_recurring_impacts;
create policy project_recurring_impacts_read on project_recurring_impacts for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_recurring_impacts_write on project_recurring_impacts;
create policy project_recurring_impacts_write on project_recurring_impacts for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_goal_assumptions_read on project_goal_assumptions;
create policy project_goal_assumptions_read on project_goal_assumptions for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_goal_assumptions_write on project_goal_assumptions;
create policy project_goal_assumptions_write on project_goal_assumptions for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_eligibility_profiles_read on project_eligibility_profiles;
create policy project_eligibility_profiles_read on project_eligibility_profiles for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_eligibility_profiles_write on project_eligibility_profiles;
create policy project_eligibility_profiles_write on project_eligibility_profiles for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_funding_check_runs_read on project_funding_check_runs;
create policy project_funding_check_runs_read on project_funding_check_runs for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_funding_check_runs_write on project_funding_check_runs;
create policy project_funding_check_runs_write on project_funding_check_runs for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_funding_matches_read on project_funding_matches;
create policy project_funding_matches_read on project_funding_matches for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_funding_matches_write on project_funding_matches;
create policy project_funding_matches_write on project_funding_matches for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_calculation_snapshots_read on project_calculation_snapshots;
create policy project_calculation_snapshots_read on project_calculation_snapshots for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_calculation_snapshots_write on project_calculation_snapshots;
create policy project_calculation_snapshots_write on project_calculation_snapshots for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on
  major_projects, project_scenarios, project_cost_sections, project_cost_items,
  project_cost_schedule_entries, project_cost_actuals, project_financing_sources,
  debt_plans, debt_rate_periods, debt_draws, debt_fees, debt_special_repayments,
  project_cashflow_assumptions, project_recurring_impacts, project_goal_assumptions,
  project_eligibility_profiles, project_funding_check_runs, project_funding_matches,
  project_calculation_snapshots
  to authenticated;

-- ---------- 11. Legacy-Schulden: Backfill nach debt_plans -------------------
-- Jede bestehende Schuldzeile bekommt einen einfachen aktiven Tilgungsplan,
-- damit debt_plans ab sofort die alleinige Rechengrundlage fuer Restschuld-
-- verlaeufe ist (Plan 14.2). Idempotent ueber unique(asset_debt_id): ein
-- zweiter Lauf der Migration legt keinen zweiten Plan an. Es wird keine reale
-- Zinsbindung/Abrufhistorie erfunden; ohne monthly_payment_minor > 0 bekommt
-- der Plan eine konservative 360-Monats-Fallback-Laufzeit statt einer
-- erratenen Rate. debt_plan_service (P2) markiert den Nutzer-Bestaetigungs-
-- bedarf ueber rate_kind='assumed' der uebernommenen Ratephase.
insert into debt_plans (
  household_id, asset_debt_id, name, currency, status,
  principal_limit_minor, opening_balance_minor, starts_on,
  payment_interval_months, amortization_mode, fixed_payment_minor,
  target_term_months
)
select
  ad.household_id,
  ad.id,
  ad.name,
  'EUR',
  'active',
  greatest(ad.current_value_minor, 1),
  ad.current_value_minor,
  current_date,
  1,
  case when coalesce(ad.monthly_payment_minor, 0) > 0 then 'fixed_payment' else 'full_repayment_term' end,
  case when coalesce(ad.monthly_payment_minor, 0) > 0 then ad.monthly_payment_minor else null end,
  case when coalesce(ad.monthly_payment_minor, 0) > 0 then null else 360 end
from assets_and_debts ad
where ad.type = 'debt'
  and not exists (select 1 from debt_plans dp where dp.asset_debt_id = ad.id);

insert into debt_rate_periods (
  household_id, debt_plan_id, starts_after_month, ends_after_month,
  nominal_rate_bps_annual, rate_kind, label
)
select
  dp.household_id,
  dp.id,
  0,
  null,
  round(coalesce(ad.interest_rate_percent, 0) * 100)::int,
  'assumed',
  'Uebernommen aus assets_and_debts.interest_rate_percent'
from debt_plans dp
join assets_and_debts ad on ad.id = dp.asset_debt_id
where not exists (
  select 1 from debt_rate_periods rp where rp.debt_plan_id = dp.id
);

-- ---------- 12. Vorhaben & Grossinvestitionen (P1): Vorlagen und Wunsch-Link

-- wishlist_items.converted_project_id (Plan 11.2): "Wunsch umwandeln" setzt
-- diese Spalte statt den Wunsch zu loeschen; ein wiederholter Aufruf der
-- Konvertierung ist damit idempotent (project_service.convert_wishlist_to_project).
alter table wishlist_items add column if not exists converted_project_id uuid;

alter table wishlist_items drop constraint if exists wishlist_items_converted_project_household_fkey;
alter table wishlist_items add constraint wishlist_items_converted_project_household_fkey
  foreign key (converted_project_id, household_id) references major_projects (id, household_id)
  on update cascade deferrable initially deferred;

-- Vier Start-Vorlagen aus Plan 2.3. Kategorien/Rechenarten, keine behaupteten
-- Marktpreise: jede Position startet mit calculation_mode='fixed' und
-- estimate_manual_minor=0, der Nutzer traegt reale Zahlen ein.
insert into project_template_versions
  (semantic_key, version, locale, country_code, project_type, name, template_json, is_released, released_at)
values
  ('home_build_de', 1, 'de-DE', 'DE', 'home_build', 'Hausbau Deutschland', cast('{"sections": [{"semantic_key": "grundstueck", "name": "Grundstück/Erwerb", "cost_class": "direct", "items": [{"semantic_key": "grundstueck_kaufpreis", "name": "Grundstückskaufpreis", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "planung_genehmigung", "name": "Planung/Genehmigung", "cost_class": "soft", "items": [{"semantic_key": "planung_architekt", "name": "Planung/Architekt/Genehmigung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "erschliessung_baustelle", "name": "Erschließung/Baustelle", "cost_class": "direct", "items": [{"semantic_key": "erschliessung", "name": "Erschließung/Baustelleneinrichtung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "rohbau", "name": "Rohbau", "cost_class": "direct", "items": [{"semantic_key": "rohbau_position", "name": "Rohbau", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "gebaeudehuelle", "name": "Gebäudehülle", "cost_class": "direct", "items": [{"semantic_key": "gebaeudehuelle_position", "name": "Gebäudehülle (Dach/Fassade/Fenster)", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "haustechnik", "name": "Haustechnik", "cost_class": "direct", "items": [{"semantic_key": "haustechnik_position", "name": "Haustechnik (Heizung/Elektrik/Sanitär)", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "innenausbau", "name": "Innenausbau", "cost_class": "direct", "items": [{"semantic_key": "innenausbau_position", "name": "Innenausbau", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "aussenanlagen", "name": "Außenanlagen", "cost_class": "direct", "items": [{"semantic_key": "aussenanlagen_position", "name": "Außenanlagen", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "ausstattung", "name": "Ausstattung", "cost_class": "direct", "items": [{"semantic_key": "ausstattung_position", "name": "Ausstattung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "uebergangskosten", "name": "Übergangskosten", "cost_class": "transition", "items": [{"semantic_key": "uebergang_position", "name": "Übergangskosten (Miete/Umzug während Bauzeit)", "cost_kind": "cash", "contingency_eligible": true}]}]}' as jsonb), true, now()),
  ('property_purchase_de', 1, 'de-DE', 'DE', 'property_purchase', 'Immobilienkauf Deutschland', cast('{"sections": [{"semantic_key": "kaufpreis", "name": "Kaufpreis", "cost_class": "direct", "items": [{"semantic_key": "kaufpreis_position", "name": "Kaufpreis", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "erwerbsnebenkosten", "name": "Erwerbsnebenkosten", "cost_class": "soft", "items": [{"semantic_key": "erwerbsnebenkosten_position", "name": "Grunderwerbsteuer/Notar/Grundbuch/Makler", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "pruefung_gutachten", "name": "Prüfung/Gutachten", "cost_class": "soft", "items": [{"semantic_key": "pruefung_position", "name": "Gutachten/Bausubstanzprüfung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "modernisierung", "name": "Modernisierung", "cost_class": "direct", "items": [{"semantic_key": "modernisierung_position", "name": "Modernisierung nach Kauf", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "umzug_ausstattung", "name": "Umzug/Ausstattung", "cost_class": "transition", "items": [{"semantic_key": "umzug_position", "name": "Umzug/Ausstattung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "reserve", "name": "Reserve", "cost_class": "other", "items": [{"semantic_key": "reserve_position", "name": "Reserve", "cost_kind": "cash", "contingency_eligible": false}]}]}' as jsonb), true, now()),
  ('renovation_de', 1, 'de-DE', 'DE', 'renovation', 'Sanierung', cast('{"sections": [{"semantic_key": "planung", "name": "Planung", "cost_class": "soft", "items": [{"semantic_key": "planung_position", "name": "Planung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "gebaeudehuelle", "name": "Gebäudehülle", "cost_class": "direct", "items": [{"semantic_key": "gebaeudehuelle_position", "name": "Gebäudehülle", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "heizung_energie", "name": "Heizung/Energie", "cost_class": "direct", "items": [{"semantic_key": "heizung_position", "name": "Heizung/Energie", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "elektrik_sanitaer", "name": "Elektrik/Sanitär", "cost_class": "direct", "items": [{"semantic_key": "elektrik_position", "name": "Elektrik/Sanitär", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "innenausbau", "name": "Innenausbau", "cost_class": "direct", "items": [{"semantic_key": "innenausbau_position", "name": "Innenausbau", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "aussenbereich", "name": "Außenbereich", "cost_class": "direct", "items": [{"semantic_key": "aussenbereich_position", "name": "Außenbereich", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "uebergangskosten", "name": "Übergangskosten", "cost_class": "transition", "items": [{"semantic_key": "uebergang_position", "name": "Übergangskosten", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "reserve", "name": "Reserve", "cost_class": "other", "items": [{"semantic_key": "reserve_position", "name": "Reserve", "cost_kind": "cash", "contingency_eligible": false}]}]}' as jsonb), true, now()),
  ('general_major_purchase', 1, 'de-DE', null, 'other', 'Allgemeine Großanschaffung', cast('{"sections": [{"semantic_key": "anschaffung", "name": "Anschaffung", "cost_class": "direct", "items": [{"semantic_key": "anschaffung_position", "name": "Anschaffung", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "zubehoer", "name": "Zubehör", "cost_class": "direct", "items": [{"semantic_key": "zubehoer_position", "name": "Zubehör", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "gebuehren", "name": "Gebühren", "cost_class": "soft", "items": [{"semantic_key": "gebuehren_position", "name": "Gebühren", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "uebergang", "name": "Übergang", "cost_class": "transition", "items": [{"semantic_key": "uebergang_position", "name": "Übergang", "cost_kind": "cash", "contingency_eligible": true}]}, {"semantic_key": "reserve", "name": "Reserve", "cost_class": "other", "items": [{"semantic_key": "reserve_position", "name": "Reserve", "cost_kind": "cash", "contingency_eligible": false}]}]}' as jsonb), true, now())
on conflict (semantic_key, version) do update
  set name = excluded.name,
      country_code = excluded.country_code,
      project_type = excluded.project_type,
      template_json = excluded.template_json,
      is_released = excluded.is_released;

-- ---------- 13. Vorhaben & Grossinvestitionen (P3): Kuratierte Foerderprogramme
--
-- Vier von Hand recherchierte und mit `normalization_status` ehrlich markierte
-- Programme (Plan 6.5), NICHT aus dem `FoerderdatenbankXmlAdapter` importiert
-- (der ist als Schnittstelle vorbereitet, siehe funding_import_service.py,
-- aber ohne verifizierten Zugriff auf den echten Open-Data-Feed nicht live
-- geschaltet). Stand der recherchierten Eckdaten: 2026-08-04, Quellen unten je
-- Programm. Programme mit unvollstaendig recherchierten Detailregeln (Bayern-
-- Labo, L-Bank) stehen bewusst auf 'rules_partial' statt 'rules_complete' und
-- tragen einen `manual_check` fuer die Einkommensgrenze, statt eine erfundene
-- Zahl als Kriterium zu verwenden -- konsistent mit Plan 6.2: "Nicht abbildbare
-- Regeln werden zu `manual_check`". Vor Produktivbetrieb erneut gegen die
-- offiziellen Quellen zu pruefen (Plan 6.5); `review_due_at` erzwingt das ueber
-- den Rechenkern (nach Ablauf faellt der Treffer auf `unknown` zurueck).
insert into funding_programs
  (semantic_key, provider_key, provider_name, program_code, title, country_code, scope,
   region_codes, official_url)
values
  ('kfw_124_wohneigentumsprogramm', 'kfw', 'KfW', '124', 'KfW-Wohneigentumsprogramm (124)',
   'DE', 'national', '{}'::text[],
   'https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentumsprogramm-(124)/'),
  ('kfw_300_wohneigentum_familien', 'kfw', 'KfW', '300', 'Wohneigentum für Familien (300)',
   'DE', 'national', '{}'::text[],
   'https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentum-f%C3%BCr-Familien-(300)/'),
  ('bayernlabo_eigenwohnraumfoerderung', 'bayernlabo', 'BayernLabo', null,
   'Eigenwohnraumförderung Bayern', 'DE', 'regional', array['DE-BY']::text[],
   'https://bayernlabo.de/eigenwohnraum'),
  ('lbank_z15_wohneigentum_bw', 'lbank', 'L-Bank', 'Z15',
   'Eigentumsfinanzierung Baden-Württemberg (Z15)', 'DE', 'regional', array['DE-BW']::text[],
   'https://www.l-bank.de/produkte/wohnimmobilien/eigentumsfinanzierung-bw-z-15-darlehen.html')
-- `household_id` bleibt NULL: das ist der kuratierte Katalog. Der Konfliktziel-
-- Ausdruck muss den partiellen Index treffen, deshalb die WHERE-Klausel.
on conflict (semantic_key) where household_id is null do update
  set provider_name = excluded.provider_name, title = excluded.title,
      official_url = excluded.official_url;

-- KfW 124: bis 100.000 EUR zinsguenstiger Kredit fuer Kauf/Bau selbstgenutzten
-- Wohneigentums, bis 35 Jahre Laufzeit, bis 5 Jahre tilgungsfrei. Quelle:
-- https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentumsprogramm-(124)/
-- (recherchiert 2026-08-04, effektiver Jahreszins dort taeglich aktualisiert
-- und deshalb bewusst NICHT als "aktuell" fest verdrahtet, Plan 6.5).
insert into funding_program_versions
  (program_id, source_version, valid_from, review_due_at, benefit_type, applicant_types,
   project_types, criteria_json, benefit_json, loan_terms_json, source_url,
   source_content_hash, license_name, normalization_status)
select
  id, 'curated-2026-08-04', date '2026-01-01', timestamptz '2026-11-01',
  'loan', array['private_individual']::text[], array['home_build', 'property_purchase']::text[],
  cast('{"compare": {"field": "self_occupied", "op": "eq", "value": true, '
       '"label": "Selbstnutzung nach Erwerb/Bau"}}' as jsonb),
  cast('{"type": "loan_fixed", "amount_minor": 10000000}' as jsonb),
  cast('{"max_term_months": 420, "max_repayment_free_months": 60, "note": '
       '"Effektiver Jahreszins wird taeglich von der KfW aktualisiert, hier '
       'nicht fest hinterlegt (Plan 6.5)."}' as jsonb),
  'https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentumsprogramm-(124)/',
  'curated-2026-08-04', 'Recherchestand, keine amtliche Lizenz', 'rules_complete'
from funding_programs where household_id is null and semantic_key = 'kfw_124_wohneigentumsprogramm'
on conflict (program_id, source_version, source_content_hash) do update
  set criteria_json = excluded.criteria_json, benefit_json = excluded.benefit_json,
      loan_terms_json = excluded.loan_terms_json, review_due_at = excluded.review_due_at;

-- KfW 300: Einkommensgrenze 90.000 EUR (1 Kind) + 10.000 EUR je weiterem Kind
-- (linear_threshold - Plan-6.2-Musterbeispiel trifft hier exakt zu), Darlehen
-- gestuft nach Kinderzahl. Die Stufenbetraege sind eine vereinfachte Naeherung
-- (1-2 Kinder: 170.000 EUR, 3-4: 220.000 EUR, ab 5: 270.000 EUR) - das echte
-- Merkblatt unterscheidet zusaetzlich nach Effizienzhaus-Standard (Standard
-- vs. QNG-Siegel), was hier aus Zeitgruenden nicht nachgebildet ist. Quelle:
-- https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentum-f%C3%BCr-Familien-(300)/
-- (recherchiert 2026-08-04).
insert into funding_program_versions
  (program_id, source_version, valid_from, review_due_at, benefit_type, applicant_types,
   project_types, criteria_json, benefit_json, loan_terms_json, source_url,
   source_content_hash, license_name, normalization_status, manual_checks_json)
select
  id, 'curated-2026-08-04', date '2026-01-01', timestamptz '2026-11-01',
  'loan', array['private_individual']::text[], array['home_build', 'property_purchase']::text[],
  cast('{"all": ['
       '{"compare": {"field": "self_occupied", "op": "eq", "value": true, '
       '"label": "Selbstnutzung"}}, '
       '{"compare": {"field": "children_count", "op": "gte", "value": 1, '
       '"label": "Mindestens ein Kind im Haushalt"}}, '
       '{"linear_threshold": {"field": "household_income_year_minor", "op": "lte", '
       '"base_minor": 9000000, "per_unit_field": "children_count", "unit_offset": 1, '
       '"per_unit_amount_minor": 1000000, "label": "Haushaltseinkommen (90.000 EUR + '
       '10.000 EUR je weiterem Kind)"}}, '
       '{"manual_check": {"key": "kfw_300_efficiency_standard", "label": '
       '"Neubau erreicht mindestens Effizienzhaus-40-Standard"}}'
       ']}' as jsonb),
  cast('{"type": "loan_tiered", "tier_field": "children_count", "tiers": ['
       '{"min": 1, "max": 2, "amount_minor": 17000000}, '
       '{"min": 3, "max": 4, "amount_minor": 22000000}, '
       '{"min": 5, "amount_minor": 27000000}]}' as jsonb),
  cast('{"note": "Vereinfachte Stufung ohne QNG-Unterscheidung, siehe offizielles '
       'Merkblatt fuer die exakte Staffel."}' as jsonb),
  'https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentum-f%C3%BCr-Familien-(300)/',
  'curated-2026-08-04', 'Recherchestand, keine amtliche Lizenz', 'rules_complete',
  cast('["Nachweis Effizienzhaus-40-Standard"]' as jsonb)
from funding_programs where household_id is null and semantic_key = 'kfw_300_wohneigentum_familien'
on conflict (program_id, source_version, source_content_hash) do update
  set criteria_json = excluded.criteria_json, benefit_json = excluded.benefit_json,
      loan_terms_json = excluded.loan_terms_json, review_due_at = excluded.review_due_at;

-- BayernLabo Eigenwohnraumfoerderung: einkommensgestaffeltes Foerderdarlehen
-- fuer selbstgenutztes Wohneigentum in Bayern. Die genaue Einkommensstaffel
-- nach Haushaltskonstellation und der Darlehensbetrag waren aus den
-- recherchierten Quellen nicht vollstaendig ablesbar (nur ein Beispielwert:
-- Ehepaar mit 2 Kindern ~109.800 EUR Jahresbruttoeinkommen) - deshalb
-- `rules_partial` und `manual_check` statt einer erfundenen Zahl. Quelle:
-- https://bayernlabo.de/eigenwohnraum/eigenheimfinanzierung (recherchiert
-- 2026-08-04).
insert into funding_program_versions
  (program_id, source_version, valid_from, review_due_at, benefit_type, applicant_types,
   project_types, criteria_json, benefit_json, source_url, source_content_hash,
   license_name, normalization_status, manual_checks_json)
select
  id, 'curated-2026-08-04', date '2026-05-01', timestamptz '2026-11-01',
  'loan', array['private_individual']::text[], array['home_build', 'property_purchase']::text[],
  cast('{"all": ['
       '{"compare": {"field": "self_occupied", "op": "eq", "value": true, '
       '"label": "Selbstnutzung"}}, '
       '{"manual_check": {"key": "bayernlabo_income_limit", "label": '
       '"Jahresbruttoeinkommen liegt unter der BayernLabo-Einkommensgrenze fuer '
       'die Haushaltskonstellation (Merkblatt pruefen)"}}, '
       '{"manual_check": {"key": "bayernlabo_before_start", "label": '
       '"Antrag vor Baubeginn/Kaufvertrag bei der Bewilligungsstelle gestellt"}}'
       ']}' as jsonb),
  cast('{"type": "fixed_grant", "amount_minor": 0}' as jsonb),
  'https://bayernlabo.de/eigenwohnraum/eigenheimfinanzierung', 'curated-2026-08-04',
  'Recherchestand, keine amtliche Lizenz', 'rules_partial',
  cast('["Einkommensgrenze nach Haushaltskonstellation", "Antragszeitpunkt"]' as jsonb)
from funding_programs where household_id is null and semantic_key = 'bayernlabo_eigenwohnraumfoerderung'
on conflict (program_id, source_version, source_content_hash) do update
  set criteria_json = excluded.criteria_json, benefit_json = excluded.benefit_json,
      review_due_at = excluded.review_due_at;

-- L-Bank Z15: zinsverbilligtes Darlehen fuer Familien mit Kindern in
-- Baden-Wuerttemberg, Grundbetrag ab 2026 292.000 EUR bei einem Kind (steigt
-- mit weiteren Kindern), 1 % Zinsverbilligung ueber 15 Jahre, anfaengliche
-- Tilgung 2,25 %. Die exakte Einkommensgrenze und Kinderstaffel waren aus den
-- recherchierten Quellen nicht ablesbar - deshalb `rules_partial` mit
-- `manual_check`. Quelle: https://www.l-bank.de/produkte/wohnimmobilien/
-- eigentumsfinanzierung-bw-z-15-darlehen.html (recherchiert 2026-08-04).
insert into funding_program_versions
  (program_id, source_version, valid_from, review_due_at, benefit_type, applicant_types,
   project_types, criteria_json, benefit_json, loan_terms_json, source_url,
   source_content_hash, license_name, normalization_status, manual_checks_json)
select
  id, 'curated-2026-08-04', date '2026-01-01', timestamptz '2026-11-01',
  'loan', array['private_individual']::text[], array['home_build', 'property_purchase']::text[],
  cast('{"all": ['
       '{"compare": {"field": "children_count", "op": "gte", "value": 1, '
       '"label": "Mindestens ein Kind im Haushalt"}}, '
       '{"manual_check": {"key": "lbank_income_limit", "label": '
       '"Haushaltseinkommen liegt unter der L-Bank-Einkommensgrenze laut '
       'Merkblatt"}}'
       ']}' as jsonb),
  cast('{"type": "loan_fixed", "amount_minor": 29200000}' as jsonb),
  cast('{"note": "Grundbetrag fuer ein Kind (Stand 2026); steigt mit weiteren '
       'Kindern laut Merkblatt, hier nicht gestuft nachgebildet. 1% '
       'Zinsverbilligung ueber 15 Jahre, anfaengliche Tilgung 2,25%."}' as jsonb),
  'https://www.l-bank.de/produkte/wohnimmobilien/eigentumsfinanzierung-bw-z-15-darlehen.html',
  'curated-2026-08-04', 'Recherchestand, keine amtliche Lizenz', 'rules_partial',
  cast('["Einkommensgrenze laut Merkblatt"]' as jsonb)
from funding_programs where household_id is null and semantic_key = 'lbank_z15_wohneigentum_bw'
on conflict (program_id, source_version, source_content_hash) do update
  set criteria_json = excluded.criteria_json, benefit_json = excluded.benefit_json,
      loan_terms_json = excluded.loan_terms_json, review_due_at = excluded.review_due_at;

-- ---------- 14. Vorhaben & Grossinvestitionen (P4): Kostenimport und --------
-- ---------- Ueberbuchungsschutz fuer Ist-Zuordnungen ------------------------
--
-- project_cost_import_profiles: haushaltsgebundenes Spalten-Mapping fuer den
-- Excel-/CSV-Kostenplanimport (Plan 10.1), analog zu import_mapping_profiles
-- fuer Bankimporte, aber bewusst eine eigene Tabelle: das Zielschema
-- (Kostenpositionen, nicht Buchungen) und die erlaubten Formate sind
-- unterschiedlich, eine gemeinsame Tabelle wuerde die Formatliste beider
-- Importarten vermischen.
create table if not exists project_cost_import_profiles (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade
                   default current_household_id(),
  name           text not null check (length(btrim(name)) between 1 and 160),
  format         text not null check (format in ('csv_generic', 'xlsx_generic')),
  column_mapping jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (household_id, name)
);

-- project_cost_import_applies: Idempotenz-Ledger fuer Apply-Aufrufe (Plan
-- 10.2, Schritt 5 "Apply mit request_id und Preview-Fingerprint"). Ein
-- wiederholter Aufruf mit derselben request_id (z. B. nach Netzwerk-Timeout)
-- liefert das gespeicherte Ergebnis erneut, statt den Import doppelt
-- anzuwenden. Der Fingerprint wird zusaetzlich mitgefuehrt, damit ein Client
-- nie versehentlich eine veraltete Preview auf inzwischen geaenderte
-- Positionen anwenden kann.
create table if not exists project_cost_import_applies (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references households(id) on delete cascade
                        default current_household_id(),
  project_id          uuid not null,
  scenario_id         uuid not null,
  request_id          text not null check (length(btrim(request_id)) between 1 and 200),
  preview_fingerprint text not null,
  result_json         jsonb not null,
  created_at          timestamptz not null default now(),
  -- scenario_id ist Teil des Schluessels: ein Import laeuft immer gegen ein
  -- Szenario, eine request_id ohne Szenariobezug koennte sonst ueber zwei
  -- verschiedene Szenarien desselben Projekts hinweg kollidieren und beim
  -- zweiten Aufruf faelschlich das Ergebnis des ersten zurueckliefern, ohne
  -- je gegen das zweite Szenario zu laufen.
  unique (household_id, project_id, scenario_id, request_id),
  foreign key (project_id, household_id) references major_projects (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists project_cost_import_applies_project_idx
  on project_cost_import_applies (project_id, created_at desc);

alter table project_cost_import_profiles enable row level security;
alter table project_cost_import_applies  enable row level security;

drop policy if exists project_cost_import_profiles_read on project_cost_import_profiles;
create policy project_cost_import_profiles_read on project_cost_import_profiles for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_import_profiles_write on project_cost_import_profiles;
create policy project_cost_import_profiles_write on project_cost_import_profiles for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists project_cost_import_applies_read on project_cost_import_applies;
create policy project_cost_import_applies_read on project_cost_import_applies for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_import_applies_write on project_cost_import_applies;
create policy project_cost_import_applies_write on project_cost_import_applies for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on
  project_cost_import_profiles, project_cost_import_applies
  to authenticated;

-- Ueberbuchungsschutz (Plan 4.6/P4-Abnahme): eine Buchung darf nie ueber
-- ihren Betrag hinaus auf Projektpositionen alloziert werden - auch nicht
-- durch zwei gleichzeitige Zuordnungsversuche. Der `for update`-Rowlock auf
-- die referenzierte Buchung serialisiert konkurrierende Trigger-Laeufe fuer
-- dieselbe transaction_id, sonst koennten zwei parallele Transaktionen je
-- die alte Summe lesen und beide die Pruefung bestehen (klassisches
-- TOCTOU-Problem bei Summenconstraints ueber mehrere Zeilen).
create or replace function check_project_cost_actuals_no_overbooking()
returns trigger as $$
declare
  txn_amount_minor bigint;
  allocated_minor  bigint;
begin
  if new.transaction_id is null then
    return new;
  end if;

  select amount_minor into txn_amount_minor
    from transactions where id = new.transaction_id
    for update;

  if txn_amount_minor is null then
    return new;
  end if;

  -- Einzelne Zeile darf fuer sich genommen nie groesser sein als der
  -- Buchungsbetrag (unabhaengig vom Vorzeichen) - sonst koennte eine einzelne
  -- "Erstattung" (negativer amount_minor, Plan 4.6) unbegrenzt gross werden,
  -- da die Summenpruefung unten nur nach oben (zu viel Kosten alloziert)
  -- greift, nicht nach unten.
  if abs(new.amount_minor) > abs(txn_amount_minor) then
    raise exception using
      errcode = '23514',
      message = format(
        'project_cost_actuals: Zuordnung ueberschreitet Buchungsbetrag (Zeile %s von %s Minor-Einheiten)',
        abs(new.amount_minor), abs(txn_amount_minor)
      );
  end if;

  select coalesce(sum(amount_minor), 0) into allocated_minor
    from project_cost_actuals
    where transaction_id = new.transaction_id;

  if allocated_minor > abs(txn_amount_minor) then
    raise exception using
      errcode = '23514',
      message = format(
        'project_cost_actuals: Zuordnung ueberschreitet Buchungsbetrag (alloziert %s von %s Minor-Einheiten)',
        allocated_minor, abs(txn_amount_minor)
      );
  end if;

  return new;
end;
$$ language plpgsql;

drop trigger if exists project_cost_actuals_no_overbooking on project_cost_actuals;
create trigger project_cost_actuals_no_overbooking
  after insert or update of amount_minor, transaction_id on project_cost_actuals
  for each row execute function check_project_cost_actuals_no_overbooking();

-- ---------- 15. Vorhaben & Grossinvestitionen: KI-Kostenschaetzung ----------
--
-- Auf Nutzerwunsch nach Abschluss von P0-P5 (kein eigener Plan-Abschnitt,
-- siehe Nachtrag in VORHABEN-GROSSINVESTITIONEN-PLAN.md): eine Freitext-
-- Beschreibung des Vorhabens laesst das lokale KI-Modell Schaetzwerte fuer
-- die VORHANDENEN Kostenpositionen des Szenarios vorschlagen (aus der
-- Vorlage oder manuell angelegt) - keine neuen Positionen/Abschnitte, das
-- waere ein zweiter, riskanterer Funktionsumfang. Dieselbe Vorschau/Anwenden-
-- Mechanik wie bei Kategorienvorschlaegen (category_operations), aber eine
-- eigene Tabelle: die Vorgaenge sind projekt-/szenariogebunden statt
-- baumweit, und `source_id`/`target_id`/`taxonomy_revision` aus
-- category_operations passen fachlich nicht.
create table if not exists project_cost_ai_operations (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),
  project_id   uuid not null,
  scenario_id  uuid not null,
  status       text not null default 'preview'
                 check (status in ('preview', 'applied', 'expired', 'failed')),
  created_by   uuid references auth.users(id) on delete set null,
  request_id   text,
  fingerprint  text not null,
  model_name   text,
  generation_status text not null default 'success'
                 check (generation_status in (
                   'success', 'no_suggestions', 'timeout', 'invalid_response', 'model_error'
                 )),
  error_code    text,
  error_message text,
  impact       jsonb not null default '{}'::jsonb,
  result       jsonb,
  expires_at   timestamptz not null default now() + interval '30 minutes',
  applied_at   timestamptz,
  created_at   timestamptz not null default now(),
  unique (id, household_id),
  foreign key (project_id, household_id) references major_projects (id, household_id)
    on delete cascade on update cascade deferrable initially deferred,
  foreign key (scenario_id, household_id) references project_scenarios (id, household_id)
    on delete cascade on update cascade deferrable initially deferred
);
create index if not exists project_cost_ai_operations_scenario_idx
  on project_cost_ai_operations (scenario_id, created_at desc);
create unique index if not exists project_cost_ai_operations_request_uniq
  on project_cost_ai_operations (household_id, scenario_id, request_id)
  where request_id is not null;

comment on table project_cost_ai_operations is
  'Kurzlebige Vorschauen und idempotente Ergebnisse der KI-Kostenschaetzung '
  '(Freitext -> Schaetzwertvorschlaege fuer bestehende Kostenpositionen).';

alter table project_cost_ai_operations enable row level security;

drop policy if exists project_cost_ai_operations_read on project_cost_ai_operations;
create policy project_cost_ai_operations_read on project_cost_ai_operations for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists project_cost_ai_operations_write on project_cost_ai_operations;
create policy project_cost_ai_operations_write on project_cost_ai_operations for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on project_cost_ai_operations to authenticated;

-- Provenienz je Kostenposition: woher der aktuelle Schaetzwert stammt. Analog
-- zu classification_decisions/llm_calls fuer Buchungen, aber bewusst direkt
-- an der Zeile statt in einer separaten Historie - eine Kostenposition hat
-- nie mehrere gleichzeitig gueltige Schaetzungen.
alter table project_cost_items
  add column if not exists estimate_source text not null default 'manual'
    check (estimate_source in ('manual', 'ai')),
  add column if not exists ai_operation_id uuid,
  add column if not exists ai_confidence numeric(3,2)
    check (ai_confidence is null or ai_confidence between 0.00 and 1.00),
  add column if not exists ai_rationale text;

alter table project_cost_items
  drop constraint if exists project_cost_items_ai_operation_fkey;
alter table project_cost_items
  add constraint project_cost_items_ai_operation_fkey
  foreign key (ai_operation_id, household_id)
  references project_cost_ai_operations (id, household_id)
  -- 'set null (ai_operation_id)' statt eines nackten 'set null': der
  -- Fremdschluessel umfasst zwei Spalten, und ohne die Spaltenangabe setzt
  -- PostgreSQL BEIDE auf NULL - auch household_id, die 'not null' ist. Eine
  -- KI-Kostenoperation liess sich damit nie loeschen, sobald ein Kostenposten
  -- auf sie zeigte: der Loeschversuch scheiterte an
  -- 'null value in column "household_id" violates not-null constraint'.
  -- Die spaltenweise Form gibt es seit PostgreSQL 15.
  on delete set null (ai_operation_id)
  on update cascade deferrable initially deferred;

-- SearXNG-Basis-URL fuer die optionale Websuche der KI-Kostenschaetzung.
-- Gleiches Muster wie custom_base_url (Haushalt schlaegt Umgebung, siehe
-- ai_settings_service.ResolvedAISettings.candidate_urls) - hier ohne
-- Umgebungs-Fallback-Kandidatenliste, weil es fuer eine selbstgehostete
-- Suchinstanz keine plausible Standard-URL gibt.
alter table ai_household_settings
  add column if not exists searxng_base_url text;

-- ---------------------------------------------------------------------------
-- Ehemals: 0008_import_mapping_profile_link.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Migration 0008: Import-Mapping-Profile mit Importlaeufen verknuepfen
-- ============================================================================
-- import_mapping_profiles wurde bisher nur angelegt/gelistet, aber nie beim
-- eigentlichen Parsen eines Imports angewendet. Diese Spalte erlaubt es, einen
-- Importlauf explizit mit dem Profil zu verknuepfen, dessen Spalten-Mapping
-- verwendet werden soll.

alter table import_runs
  add column mapping_profile_id uuid references import_mapping_profiles(id) on delete set null;

-- ---------------------------------------------------------------------------
-- Ehemals: 0009_xls_mapping_format.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Migration 0009: '.xls' (xls_generic) als gueltiges Mapping-Profil-Format
-- ============================================================================
-- Ergaenzt den neuen Legacy-Excel-Parser (xls_generic, getrennt von
-- xlsx_generic da xlrd nur .xls liest) als erlaubten bank_format-Wert fuer
-- Import-Mapping-Profile.

alter table import_mapping_profiles drop constraint import_mapping_profiles_bank_format_check;

alter table import_mapping_profiles
  add constraint import_mapping_profiles_bank_format_check
  check (bank_format in ('csv_generic', 'xlsx_generic', 'xls_generic', 'mt940', 'camt', 'paypal_csv'));

-- ---------------------------------------------------------------------------
-- Ehemals: 0010_household_invites.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Mitglieder-Einladung per Link/Code (Stage 2e)
-- Nach 0001-0009 anwenden.
--
-- household_members.status='invited'/invited_by/invited_at (0001_init.sql)
-- sind fuer ein anderes Muster gedacht (Einladung eines bereits bekannten
-- user_id) und bleiben hier unangetastet - Code-basierte Einladungen kennen
-- den einzuladenden Nutzer vorab nicht und brauchen eine eigene Tabelle.
-- ============================================================================

create table household_invites (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade,
  code         text not null unique,
  role         household_role not null default 'editor',
  created_by   uuid not null references auth.users(id) on delete cascade,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  revoked_at   timestamptz,
  used_by      uuid references auth.users(id) on delete set null,
  used_at      timestamptz
);
create index household_invites_household_idx on household_invites (household_id);

alter table household_invites enable row level security;

-- Verwaltung (erstellen/auflisten/widerrufen) nur fuer den Owner des
-- aktuell per X-Household-Id gewaehlten Haushalts - gleiches Muster wie
-- household_members_owner_write (0002_rls.sql).
create policy household_invites_owner_manage on household_invites for all to authenticated
  using (
    household_id = current_household_id()
    and is_household_member(household_id)
    and current_household_role() = 'owner'
  )
  with check (
    household_id = current_household_id()
    and is_household_member(household_id)
    and current_household_role() = 'owner'
  );

-- Vorschau (read-only, fuer die Beitreten-Seite vor der Bestaetigung) und
-- Einloesung muessen RLS bewusst umgehen (security definer), da die
-- einloesende Person zu diesem Zeitpunkt noch kein Mitglied des Ziel-
-- Haushalts ist - exakt das bestehende Muster von create_household()
-- (0001_init.sql).
create or replace function preview_household_invite(p_code text)
returns table(household_id uuid, household_name text, role household_role, expires_at timestamptz)
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_invite household_invites%rowtype;
begin
  select * into v_invite from household_invites where code = btrim(p_code);
  if not found then
    raise exception using errcode = '22023', message = 'INVITE_NOT_FOUND';
  end if;
  if v_invite.revoked_at is not null then
    raise exception using errcode = '22023', message = 'INVITE_REVOKED';
  end if;
  if v_invite.used_at is not null then
    raise exception using errcode = '22023', message = 'INVITE_ALREADY_USED';
  end if;
  if v_invite.expires_at < now() then
    raise exception using errcode = '22023', message = 'INVITE_EXPIRED';
  end if;

  return query
    select h.id, h.name, v_invite.role, v_invite.expires_at
    from households h
    where h.id = v_invite.household_id;
end $$;

create or replace function redeem_household_invite(p_code text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_invite household_invites%rowtype;
  v_uid uuid;
begin
  v_uid := auth.uid();
  if v_uid is null then
    raise exception using errcode = '42501', message = 'AUTH_REQUIRED';
  end if;

  select * into v_invite from household_invites where code = btrim(p_code) for update;
  if not found then
    raise exception using errcode = '22023', message = 'INVITE_NOT_FOUND';
  end if;
  if v_invite.revoked_at is not null then
    raise exception using errcode = '22023', message = 'INVITE_REVOKED';
  end if;
  if v_invite.used_at is not null then
    raise exception using errcode = '22023', message = 'INVITE_ALREADY_USED';
  end if;
  if v_invite.expires_at < now() then
    raise exception using errcode = '22023', message = 'INVITE_EXPIRED';
  end if;

  update household_invites set used_by = v_uid, used_at = now() where id = v_invite.id;

  insert into household_members (household_id, user_id, role, status, invited_by, joined_at)
  values (v_invite.household_id, v_uid, v_invite.role, 'active', v_invite.created_by, now())
  on conflict (household_id, user_id) do update
    set status = 'active', role = excluded.role, joined_at = now();

  return v_invite.household_id;
end $$;

-- ---------------------------------------------------------------------------
-- Ehemals: 0011_classification_runs.sql
-- ---------------------------------------------------------------------------
-- Batch-KI-Klassifikation mit Fortschrittsanzeige (Analog zu import_runs,
-- siehe "Ehemals: 0004_imports.sql"). Ein Datensatz pro Lauf, den der Worker
-- (worker/ai/processor.py) inkrementell fortschreibt; der Client pollt
-- GET /ai/classify-runs/{id} (dasselbe Polling-Muster wie useImportRun).

create table classification_runs (
  id               uuid primary key default gen_random_uuid(),
  household_id     uuid not null references households(id) on delete cascade
                     default current_household_id(),
  status           text not null default 'pending' check (
    status in ('pending', 'running', 'completed', 'failed', 'cancelled')
  ),
  since_date       date not null,
  total_count      int not null default 0,
  processed_count  int not null default 0,
  success_count    int not null default 0,
  error_count      int not null default 0,
  error_message    text,
  started_by       uuid references auth.users(id) on delete set null,
  created_at       timestamptz not null default now(),
  completed_at     timestamptz
);
create index classification_runs_household_idx
  on classification_runs (household_id, created_at desc);

alter table classification_runs enable row level security;

create policy classification_runs_read on classification_runs for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy classification_runs_write on classification_runs for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on classification_runs to authenticated;

-- ---------------------------------------------------------------------------
-- Phase 8: Lebensmittelplanung
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Phase 8: Essensplan, Einkaufsliste, Wochenbudget. Siehe
-- FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 6.8 und Abschnitt 17, Phase 8.
--
-- Plan- und Istkosten werden getrennt ausgewiesen: shopping_items.actual_price_minor
-- ist eine rein manuelle Notiz und wird nie mit verknuepften Transaktionen
-- verrechnet. Die massgebliche Istkosten-Summe je Einkaufsliste/Woche wird zur
-- Laufzeit aus den echten, ganzen Transaktionsbetraegen in
-- shopping_purchase_links berechnet (analog get_budget_summary) - eine
-- Banktransaktion liefert nur den Gesamtbon, keine kuenstliche Artikelaufteilung.
-- ============================================================================

create table meal_weeks (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  week_start   date not null,
  budget_minor bigint not null default 0 check (budget_minor >= 0),
  note         text,
  created_at   timestamptz not null default now(),
  unique (household_id, week_start)
);

-- Gerichte-Katalog: wiederverwendbare Gerichte je Haushalt. Einmal mit
-- Standardportionen und Kostenschaetzung angelegt, danach im Wochenplan nur
-- noch ausgewaehlt, statt Name/Portionen/Schaetzung jede Woche neu zu tippen.
create table if not exists dishes (
  id                   uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade default current_household_id(),
  name                 text not null,
  default_meal_type    text check (default_meal_type in ('breakfast', 'lunch', 'dinner', 'snack')),
  default_servings     int not null default 2 check (default_servings > 0),
  estimated_cost_minor bigint check (estimated_cost_minor >= 0),
  category             text,
  note                 text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
-- Case-insensitiv eindeutig, damit der Katalog nicht mit "Chili con Carne"
-- neben "chili con carne" zuwaechst - genau das wuerde die Wiederverwendung
-- aushebeln, um die es hier geht.
create unique index if not exists dishes_household_name_idx on dishes (household_id, lower(name));

-- Zutatenliste eines Gerichts ("Spaghetti" = Nudeln + Tomatensauce + Kleinkram).
-- WICHTIG: Die Mengen beziehen sich auf dishes.default_servings, nicht auf eine
-- Portion. So steht im Katalog, was auch im Rezept steht ("fuer 4 Personen:
-- 500 g Nudeln"); beim Uebertragen in die Einkaufsliste wird mit
-- meals.servings / dishes.default_servings hochgerechnet.
-- quantity ist bewusst nullable: "Salz", "Pfeffer", "etwas Olivenoel" haben
-- keine sinnvolle Menge, sollen aber auf der Einkaufsliste auftauchen duerfen.
create table if not exists dish_ingredients (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  dish_id      uuid not null references dishes(id) on delete cascade,
  name         text not null,
  quantity     numeric(10,2) check (quantity is null or quantity > 0),
  unit         text,
  category     text,
  note         text,
  sort_order   int not null default 0,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists dish_ingredients_dish_idx on dish_ingredients (dish_id, sort_order);

-- Eigene Preisliste des Haushalts. Der Preis haengt an der ZUTAT, nicht am
-- Gericht: "Nudeln kosten 1,99 EUR je kg" gilt in jedem Rezept gleich. Die
-- Kostenschaetzung eines Gerichts rechnet sich daraus zusammen, statt sie je
-- Gericht (und danach nochmal je Mahlzeit) von Hand einzutippen.
--
-- price_minor gilt fuer pack_quantity pack_unit - also die Packungsgroesse,
-- wie sie im Laden steht ("1,99 EUR fuer 1 kg", "0,89 EUR fuer 400 g",
-- "2,49 EUR fuer 10 Stk"). Erst daraus entsteht der Grundpreis, mit dem eine
-- Rezeptmenge bewertet wird.
--
-- Die Zuordnung Zutat -> Preis laeuft ueber den NAMEN (case-insensitiv), nicht
-- ueber einen Fremdschluessel. Absicht: ein spaeter nachgetragener Preis
-- wirkt sofort in allen Gerichten, die diese Zutat verwenden, ohne dass
-- irgendwo nachverknuepft werden muesste.
create table if not exists ingredient_prices (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade default current_household_id(),
  name          text not null,
  price_minor   bigint not null check (price_minor >= 0),
  pack_quantity numeric(10,2) not null default 1 check (pack_quantity > 0),
  pack_unit     text,
  category      text,
  note          text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create unique index if not exists ingredient_prices_household_name_idx
  on ingredient_prices (household_id, lower(name));

-- Bestaetigte Schreibweisen. Die Zuordnung Zutat -> Preis laeuft ueber den
-- exakten Namen; sobald Preise aus einer Tabelle kommen, treffen die
-- Schreibweisen fast nie ("Stueckige Tomaten (Dose)" im Rezept gegen
-- "Tomaten, stueckig 400g" in der Preisliste). Ohne diese Tabelle sieht das
-- aus wie "kein Preis hinterlegt", und der Nutzer legt den Preis ein zweites
-- Mal an.
--
-- Nur BESTAETIGTE Zuordnungen stehen hier. Der Server schlaegt aehnliche
-- Namen vor, uebernimmt sie aber nie selbst - "Salz" ist der Anfang von
-- "Salzkartoffeln", und eine still geratene Zuordnung waere genau die Sorte
-- Falschannahme, die der Rechenkern an keiner Stelle trifft.
--
-- Beide Seiten sind NAMEN, keine Fremdschluessel: dieselbe Entscheidung wie
-- bei ingredient_prices und ingredient_price_observations. Eine Zuordnung
-- ueberlebt so das Loeschen und Neuanlegen der Preiszeile.
create table if not exists ingredient_price_aliases (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  -- Schreibweise aus dem Rezept, exakt lower(trim(name)) - die Form, die
  -- grocery_pricing_service.load_price_lookup als Schluessel benutzt.
  alias_key    text not null,
  alias_name   text not null,
  -- Ziel: lower(trim(ingredient_prices.name)).
  price_key    text not null,
  price_name   text not null,
  created_at   timestamptz not null default now(),
  -- Eine Schreibweise kann nur auf einen Preis zeigen. Andersherum duerfen
  -- beliebig viele Schreibweisen auf denselben Preis zeigen.
  constraint ingredient_price_aliases_not_self check (alias_key <> price_key)
);
create unique index if not exists ingredient_price_aliases_household_alias_idx
  on ingredient_price_aliases (household_id, alias_key);

create table meals (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade default current_household_id(),
  meal_week_id          uuid not null references meal_weeks(id) on delete cascade,
  meal_date             date not null,
  meal_type             text not null check (meal_type in ('breakfast', 'lunch', 'dinner', 'snack')),
  dish_name             text not null,
  servings              int not null default 1 check (servings > 0),
  estimated_cost_minor  bigint check (estimated_cost_minor >= 0),
  note                  text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index meals_week_idx on meals (meal_week_id, meal_date);

-- Herkunftsverweis auf den Katalog. Bewusst 'on delete set null' und
-- zusaetzlich zu dish_name: der Name der geplanten Mahlzeit ist eine Kopie
-- und bleibt stehen, wenn der Katalogeintrag spaeter umbenannt oder geloescht
-- wird. Vergangene Wochenplaene sollen sich nicht rueckwirkend aendern.
alter table meals add column if not exists dish_id uuid references dishes(id) on delete set null;
create index if not exists meals_dish_idx on meals (dish_id);

create table shopping_lists (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  meal_week_id uuid not null references meal_weeks(id) on delete cascade,
  store        text,
  status       text not null default 'planning' check (status in ('planning', 'shopping', 'completed')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index shopping_lists_week_idx on shopping_lists (meal_week_id);

create table shopping_items (
  id                   uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade default current_household_id(),
  shopping_list_id     uuid not null references shopping_lists(id) on delete cascade,
  meal_id              uuid references meals(id) on delete set null,
  name                 text not null,
  category             text,
  quantity             numeric(10,2) not null default 1 check (quantity > 0),
  unit                 text,
  planned_price_minor  bigint check (planned_price_minor >= 0),
  actual_price_minor   bigint check (actual_price_minor >= 0),
  is_purchased         boolean not null default false,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
create index shopping_items_list_idx on shopping_items (shopping_list_id);

-- Freitext am Einkaufsposten. "Aus Wochenplan fuellen" traegt hier den
-- tatsaechlichen Wochenbedarf ein, wenn er von der Kaufmenge abweicht
-- ("fuer 750 g benoetigt", waehrend 1 kg auf der Liste steht): eine Packung
-- ist die kleinste kaufbare Einheit, der Rest bleibt uebrig und soll nicht
-- wie Verschwendung aussehen.
alter table shopping_items add column if not exists note text;

-- Markiert Zeilen, die aus "Aus Wochenplan fuellen" stammen. Nur sie darf ein
-- erneuter Abgleich ueberschreiben; von Hand ergaenzte Posten gehoeren dem
-- Nutzer.
--
-- Bewusst eine eigene Spalte und NICHT `meal_id is not null`: meal_id ist
-- 'on delete set null'. Wird die Mahlzeit geloescht, aus der ein Posten
-- stammt, waere er sonst ploetzlich "von Hand angelegt" und der Abgleich
-- liesse ihn mit veralteter Menge und veraltetem Preis stehen.
alter table shopping_items
  add column if not exists from_meal_plan boolean not null default false;

create table shopping_purchase_links (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade default current_household_id(),
  shopping_list_id  uuid not null references shopping_lists(id) on delete cascade,
  transaction_id    uuid not null references transactions(id) on delete cascade,
  created_at        timestamptz not null default now(),
  unique (transaction_id)
);
create index shopping_purchase_links_list_idx on shopping_purchase_links (shopping_list_id);

-- ---------- Row Level Security -----------------------------------------------

alter table meal_weeks              enable row level security;
alter table dishes                  enable row level security;
alter table dish_ingredients        enable row level security;
alter table ingredient_prices       enable row level security;
alter table ingredient_price_aliases enable row level security;
alter table meals                   enable row level security;
alter table shopping_lists          enable row level security;
alter table shopping_items          enable row level security;
alter table shopping_purchase_links enable row level security;

create policy meal_weeks_read on meal_weeks for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy meal_weeks_write on meal_weeks for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists dishes_read on dishes;
create policy dishes_read on dishes for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists dishes_write on dishes;
create policy dishes_write on dishes for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists dish_ingredients_read on dish_ingredients;
create policy dish_ingredients_read on dish_ingredients for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists dish_ingredients_write on dish_ingredients;
create policy dish_ingredients_write on dish_ingredients for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists ingredient_prices_read on ingredient_prices;
create policy ingredient_prices_read on ingredient_prices for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ingredient_prices_write on ingredient_prices;
create policy ingredient_prices_write on ingredient_prices for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists ingredient_price_aliases_read on ingredient_price_aliases;
create policy ingredient_price_aliases_read on ingredient_price_aliases for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ingredient_price_aliases_write on ingredient_price_aliases;
create policy ingredient_price_aliases_write on ingredient_price_aliases for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy meals_read on meals for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy meals_write on meals for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy shopping_lists_read on shopping_lists for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy shopping_lists_write on shopping_lists for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy shopping_items_read on shopping_items for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy shopping_items_write on shopping_items for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

create policy shopping_purchase_links_read on shopping_purchase_links for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
create policy shopping_purchase_links_write on shopping_purchase_links for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on
  meal_weeks, dishes, dish_ingredients, ingredient_prices, ingredient_price_aliases,
  meals, shopping_lists, shopping_items, shopping_purchase_links
  to authenticated;


-- ---------------------------------------------------------------------------
-- Ehemals: 0002_audit_log.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Migration 0002: Audit-Log & Regelversionierung (Phase 4)
-- ============================================================================
-- Erfuellt das Phase-4-Abnahmekriterium "Regelaenderungen sind auditierbar".
-- Bewusst rein additiv und idempotent formuliert (IF NOT EXISTS / DROP ...
-- IF EXISTS), damit ein erneuter Lauf gegen eine bereits migrierte Datenbank
-- nicht scheitert.

-- ---------- Versionierung der Klassifikationsregeln --------------------------

alter table classification_rules add column if not exists version int not null default 1;

-- Der Zaehler wird im Trigger und nicht in der Anwendung hochgezaehlt: so ist
-- jede Aenderung versioniert, egal ueber welchen Pfad sie geschrieben wird.
create or replace function bump_classification_rule_version()
returns trigger
language plpgsql
as $$
begin
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists classification_rules_version_trg on classification_rules;
create trigger classification_rules_version_trg
  before update on classification_rules
  for each row execute function bump_classification_rule_version();

-- ---------- Dedup: hoechstens eine einfache Regel je Haendler/Kategorie ------
-- Die Review-Inbox legt aus einer Korrektur eine Regel "Haendler -> Kategorie"
-- an. Ohne Constraint entstand bei jeder erneuten identischen Korrektur eine
-- weitere identische Regel. Der Index deckt bewusst nur diese einfache
-- Regelform ab (ausschliesslich Haendler-Match, ausschliesslich
-- Kategorie-Aktion). Regeln mit zusaetzlichen Kriterien (Verwendungszweck,
-- Betragsgrenzen, Konto) duerfen denselben Haendler weiterhin mehrfach
-- verwenden - dort ist die Mehrfachanlage fachlich gewollt.

-- Bestandsdubletten zuerst abraeumen (aelteste Zeile gewinnt), sonst laesst
-- sich der Unique-Index nicht anlegen.
delete from classification_rules cr
where cr.match_payee_raw is not null
  and cr.action_category_id is not null
  and cr.match_purpose is null
  and cr.match_min_amount_minor is null
  and cr.match_max_amount_minor is null
  and cr.match_account_id is null
  and exists (
    select 1
    from classification_rules older
    where older.household_id = cr.household_id
      and older.match_payee_raw is not null
      and lower(btrim(older.match_payee_raw)) = lower(btrim(cr.match_payee_raw))
      and older.action_category_id = cr.action_category_id
      and older.match_purpose is null
      and older.match_min_amount_minor is null
      and older.match_max_amount_minor is null
      and older.match_account_id is null
      and (older.created_at, older.id) < (cr.created_at, cr.id)
  );

create unique index if not exists classification_rules_payee_category_uniq
  on classification_rules (household_id, lower(btrim(match_payee_raw)), action_category_id)
  where match_payee_raw is not null
    and action_category_id is not null
    and match_purpose is null
    and match_min_amount_minor is null
    and match_max_amount_minor is null
    and match_account_id is null;

-- ---------- Audit-Log --------------------------------------------------------

create table if not exists audit_log (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade default current_household_id(),
  actor_user_id uuid references auth.users(id) on delete set null,
  entity_type   text not null check (length(btrim(entity_type)) between 1 and 80),
  entity_id     uuid not null,
  action        text not null check (action in ('create', 'update', 'delete')),
  before        jsonb,
  after         jsonb,
  created_at    timestamptz not null default now()
);

create index if not exists audit_log_household_idx on audit_log (household_id, created_at desc);
create index if not exists audit_log_entity_idx
  on audit_log (household_id, entity_type, entity_id, created_at desc);

-- ---------- Row Level Security ----------------------------------------------

alter table audit_log enable row level security;

drop policy if exists audit_log_read on audit_log;
create policy audit_log_read on audit_log for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

-- Bewusst nur INSERT (kein "for all" wie bei den uebrigen Tabellen): ein
-- Audit-Log, das der Schreiber nachtraeglich aendern oder loeschen kann, waere
-- als Nachweis wertlos. Schreiben darf, wer auch die auditierten Entitaeten
-- aendern darf - daher dasselbe can_edit_household()-Gate wie im Bestand.
drop policy if exists audit_log_write on audit_log;
create policy audit_log_write on audit_log for insert to authenticated
  with check (
    household_id = current_household_id()
    and is_household_member(household_id)
    and can_edit_household()
  );

grant select, insert on audit_log to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0003_ai_capabilities.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Migration 0003: Persistierte Modell-Faehigkeiten (Phase 6)
-- ============================================================================
-- Additiv zum konsolidierten Basisschema. Bis hierher war nirgends
-- gespeichert, OB ein lokal geladenes Modell strukturierte Ausgabe
-- (response_format/json_schema) beherrscht - der Capability-Canary lief bei
-- jedem Aufruf neu und sein Ergebnis war nach dem Request verloren. Diese
-- Tabelle haelt das Ergebnis je Haushalt/Modell/Promptversion fest, sodass
--   1. der Klassifikationspfad den bewussten Fallback (Prompt+Parse) kennt,
--      statt ihn stillschweigend bei jedem Lauf neu zu entdecken, und
--   2. llm_calls (das ohnehin prompt_version fuehrt) nach Modell UND
--      Promptversion ausgewertet werden kann.
-- Die Migration ist idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS), damit
-- ein erneuter Lauf gegen eine bereits migrierte DB nicht scheitert.

create table if not exists ai_model_capabilities (
  id                   uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade
                         default current_household_id(),
  model_name           text not null,
  prompt_version       text not null default 'v1',
  supports_json_schema boolean not null default false,
  canary_passed        boolean not null default false,
  last_error           text,
  checked_at           timestamptz not null default now(),
  created_at           timestamptz not null default now(),
  constraint ai_model_capabilities_unique unique (household_id, model_name, prompt_version)
);

create index if not exists ai_model_capabilities_household_idx
  on ai_model_capabilities (household_id, checked_at desc);

-- ---------- Row Level Security (analog llm_calls / classification_decisions) --

alter table ai_model_capabilities enable row level security;

drop policy if exists ai_model_capabilities_read on ai_model_capabilities;
create policy ai_model_capabilities_read on ai_model_capabilities for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

drop policy if exists ai_model_capabilities_write on ai_model_capabilities;
create policy ai_model_capabilities_write on ai_model_capabilities for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on ai_model_capabilities to authenticated;

-- ============================================================================
-- Messung je Modell: Zeichen je Token und Schaetzabstand
-- ============================================================================
-- Das relative KI-Budget (app/llm/budget.py) schaetzt die Eingabe eines
-- Aufrufs in Token. Statt einer festen Faustregel ("Zeichen / 4") lernt es das
-- Verhaeltnis je Modell aus den vom Server gemeldeten usage.prompt_tokens:
--
--   Zeichen je Token = sum_chars / sum_prompt_tokens
--   Schaetzabstand   = sum_estimate_error / sum_checked_tokens
--
-- Laufende Summen statt eines letzten Werts: ein winziger Aufruf
-- (Verbindungstest, 18 Token, Rahmen der Chatvorlage ueberwiegt) verschiebt
-- sie kaum. Der Schaetzabstand ist die Summe der Abweichungen der jeweils
-- VORHERIGEN Schaetzung von der echten Zahl, geteilt durch die Summe der
-- geprueften Token. Fortgeschrieben wird ausschliesslich in SQL (ein Upsert,
-- der inkrementiert) - zwei gleichzeitige Aufrufe verlieren so keine Messung.
-- Bis zur ersten Messung gelten die Startwerte aus ai_household_settings.
--
-- Schluessel ist der Modellserver (scheme://host:port) plus Modell: dasselbe
-- Modell auf einem anderen Server kann eine andere Chatvorlage haben.
create table if not exists ai_model_measurements (
  id                   uuid primary key default gen_random_uuid(),
  household_id         uuid not null references households(id) on delete cascade
                         default current_household_id(),
  server_target        text not null check (length(btrim(server_target)) between 1 and 300),
  model_key            text not null check (length(btrim(model_key)) between 1 and 300),
  sum_chars            bigint not null default 0 check (sum_chars >= 0),
  sum_prompt_tokens    bigint not null default 0 check (sum_prompt_tokens >= 0),
  sum_estimate_error   double precision not null default 0 check (sum_estimate_error >= 0),
  sum_checked_tokens   bigint not null default 0 check (sum_checked_tokens >= 0),
  calls                int not null default 0 check (calls >= 0),
  largest_need_tokens  int check (largest_need_tokens is null or largest_need_tokens >= 0),
  measured_at          timestamptz not null default now(),
  created_at           timestamptz not null default now(),
  constraint ai_model_measurements_unique unique (household_id, server_target, model_key)
);

create index if not exists ai_model_measurements_household_idx
  on ai_model_measurements (household_id, measured_at desc);

comment on table ai_model_measurements is
  'Gemessenes Verhaeltnis Zeichen/Token und Schaetzabstand je Haushalt, '
  'Modellserver und Modell. Verbindungstests messen nicht.';
comment on column ai_model_measurements.largest_need_tokens is
  'Groesste gemeldete Antwort (completion_tokens inkl. Denken) - nur zur Anzeige.';

alter table ai_model_measurements enable row level security;

drop policy if exists ai_model_measurements_read on ai_model_measurements;
create policy ai_model_measurements_read on ai_model_measurements for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

drop policy if exists ai_model_measurements_write on ai_model_measurements;
create policy ai_model_measurements_write on ai_model_measurements for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on ai_model_measurements to authenticated;

-- ---------------------------------------------------------------------------
-- Ehemals: 0004_transaction_versioning.sql
-- ---------------------------------------------------------------------------
-- Phase 9: Optimistische Nebenlaeufigkeitskontrolle fuer Buchungen.
--
-- Warum eine eigene version-Spalte und nicht updated_at als ETag?
--   1. now() ist innerhalb einer Transaktion konstant. Zwei aufeinander
--      folgende Updates derselben Buchung im selben Request (z. B. PUT
--      .../splits: erst Betrag, dann Splits) bekaemen identische
--      updated_at-Werte - eine dazwischen liegende Fremdaenderung waere nicht
--      mehr erkennbar. Ein monotoner Zaehler ist davon unabhaengig.
--   2. timestamptz verliert auf dem Weg durch JSON und die Client-Laufzeiten
--      (JavaScript kennt nur Millisekunden, Postgres speichert Mikrosekunden)
--      Praezision. Ein zurueckgeschickter Zeitstempel passt dann nicht mehr
--      exakt und erzeugt Phantomkonflikte.
--   3. households.version (0001_schema.sql) etabliert dieses Muster bereits.
--
-- Additiv und idempotent: die Datei darf mehrfach angewendet werden.

alter table transactions
  add column if not exists version int not null default 1;

-- Bestandszeilen: der Spalten-Default deckt sie beim ALTER bereits ab; das
-- Update faengt nur den Fall ab, dass die Spalte bei einem frueheren,
-- abgebrochenen Lauf ohne not-null/Default entstanden ist.
update transactions set version = 1 where version is null;

comment on column transactions.version is
  'Optimistic-Concurrency-Token. Wird ausschliesslich vom Trigger '
  'transactions_bump_version gepflegt und bei jeder fachlichen Aenderung um 1 '
  'erhoeht. Clients schicken den zuletzt gesehenen Wert als If-Match bzw. '
  'expected_version; weicht er ab, antwortet die API mit 409.';

-- Zaehlt die Version zentral hoch, damit kein Schreibpfad (API-Route,
-- Import-Worker, Regelengine, kuenftige SQL-Wartung) es vergessen kann.
create or replace function bump_transaction_version()
returns trigger
language plpgsql
as $$
begin
  if new.version = old.version then
    -- Kein Schreibpfad hat die Version explizit angefasst: nur zaehlen, wenn
    -- sich fachliche Spalten wirklich geaendert haben. Ein No-Op-Update darf
    -- keinen Konflikt bei anderen Clients ausloesen. version und updated_at
    -- sind aus dem Vergleich ausgenommen, weil sie die Folge der Aenderung
    -- sind, nicht ihre Ursache.
    if (to_jsonb(new) - 'version' - 'updated_at')
       is distinct from (to_jsonb(old) - 'version' - 'updated_at') then
      new.version := old.version + 1;
      new.updated_at := now();
    else
      new.version := old.version;
    end if;
  else
    -- Ein Schreibpfad hat die Version bewusst gesetzt (z. B. PUT .../splits,
    -- wo sich nur die Splits aendern, die Buchungszeile selbst aber gleich
    -- bleibt). Das zaehlt immer als Aenderung - und der Trigger bestimmt den
    -- neuen Wert, damit ein Client die Version nie frei setzen oder
    -- zuruecksetzen kann.
    new.version := old.version + 1;
    new.updated_at := now();
  end if;
  return new;
end;
$$;

drop trigger if exists transactions_bump_version on transactions;
create trigger transactions_bump_version
  before update on transactions
  for each row
  execute function bump_transaction_version();

-- ---------------------------------------------------------------------------
-- Ehemals: 0005_asset_valuations.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Migration 0005: Bewertungshistorie fuer Vermoegenswerte und Schulden
-- ============================================================================
-- assets_and_debts.current_value_minor wurde bisher beim PATCH in-place
-- ueberschrieben - die vorherige Bewertung war danach unwiederbringlich
-- verloren. Fuer den Nettovermoegensverlauf (Architekturplan 6.7) braucht es
-- aber eine nachvollziehbare Historie je Position.
--
-- Additiv und idempotent: erneutes Anwenden darf nicht scheitern.

create table if not exists asset_valuations (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade default current_household_id(),
  asset_id     uuid not null references assets_and_debts(id) on delete cascade,
  valued_on    date not null default current_date,
  value_minor  bigint not null check (value_minor >= 0),
  created_at   timestamptz not null default now(),
  created_by   uuid references auth.users(id) on delete set null default auth.uid()
);

create index if not exists asset_valuations_asset_idx
  on asset_valuations (household_id, asset_id, valued_on desc, created_at desc);

-- ---------- Row Level Security ----------------------------------------------
-- Gleiches Muster wie assets_and_debts (0001_schema.sql): Lesen fuer jedes
-- Mitglied des aktiven Haushalts, Schreiben nur mit Editor-/Owner-Rolle.

alter table asset_valuations enable row level security;

drop policy if exists asset_valuations_read on asset_valuations;
create policy asset_valuations_read on asset_valuations for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

drop policy if exists asset_valuations_write on asset_valuations;
create policy asset_valuations_write on asset_valuations for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on asset_valuations to authenticated;

-- Bestandsdaten erhalten ihren aktuellen Wert als ersten Historienpunkt, damit
-- die Historie nicht erst ab der naechsten Wertaenderung existiert. Der
-- not-exists-Filter haelt den Backfill idempotent.
insert into asset_valuations (household_id, asset_id, valued_on, value_minor)
select a.household_id, a.id, current_date, a.current_value_minor
from assets_and_debts a
where not exists (
  select 1 from asset_valuations av where av.asset_id = a.id
);

-- ---------------------------------------------------------------------------
-- Ehemals: 0006_ai_run_recovery.sql
-- ---------------------------------------------------------------------------
-- KI-Finanzassistent, Ausbaustufe 0: einen laufenden KI-Lauf nach Navigation,
-- Reload oder App-Neustart wiederfinden (siehe docs/KI-FINANZASSISTENT-PLAN.md).
--
-- Bisher lebte die Run-ID ausschliesslich im lokalen React-State der
-- Review-Seite. Der Dramatiq-Worker lief zwar weiter, die neu geladene
-- Oberflaeche zeigte aber wieder den Startknopf - und ein zweiter Klick
-- erzeugte einen zweiten Lauf mit doppelten Modellaufrufen.
--
-- Diese Migration ist rein additiv und idempotent (mehrfach anwendbar).

alter table classification_runs
  add column if not exists run_type         text not null default 'classification',
  add column if not exists current_stage    text,
  add column if not exists heartbeat_at     timestamptz,
  add column if not exists cancel_requested boolean not null default false,
  add column if not exists request_id       text;

comment on column classification_runs.run_type is
  'Fachliche Laufart. Heute ausschliesslich ''classification''; der spaetere '
  'KI-Finanz-Check haengt weitere Analysearten an denselben Lauf und '
  'unterscheidet sie hierueber.';
comment on column classification_runs.current_stage is
  'Aktuelle Stufe des Laufs (preparing | classifying | completed). Erlaubt der '
  'Oberflaeche eine Stufenanzeige statt nur eines Gesamtzaehlers.';
comment on column classification_runs.heartbeat_at is
  'Letztes Lebenszeichen des Workers. Ein aktiver Lauf ohne Lebenszeichen gilt '
  'nach STALE_RUN_AFTER (services/api/app/api/v1/routes/ai.py) als haengend und '
  'blockiert keinen Neustart mehr.';
comment on column classification_runs.cancel_requested is
  'Vom Nutzer angefordertes Abbrechen. Der Worker prueft das Flag zwischen zwei '
  'Buchungen und beendet den Lauf dann als ''cancelled'' - bereits geschriebene '
  'Teilresultate bleiben erhalten.';
comment on column classification_runs.request_id is
  'Idempotenzschluessel des Startaufrufs. Ein Retry desselben Requests liefert '
  'denselben Lauf zurueck, statt einen zweiten Job einzureihen.';

-- Bestandsdaten fuer den partiellen Unique-Index vorbereiten: aus mehreren
-- gleichzeitig aktiven Laeufen eines Haushalts bleibt nur der juengste aktiv.
-- Die aelteren waren ohnehin nur ueber Doppelklicks entstanden und in der
-- Oberflaeche nicht mehr erreichbar.
update classification_runs r
   set status = 'cancelled',
       completed_at = coalesce(r.completed_at, now()),
       error_message = coalesce(
         r.error_message,
         'Automatisch beendet: pro Haushalt ist nur ein aktiver KI-Lauf zulaessig.'
       )
 where r.status in ('pending', 'running')
   and exists (
     select 1
       from classification_runs newer
      where newer.household_id = r.household_id
        and newer.status in ('pending', 'running')
        and (newer.created_at, newer.id) > (r.created_at, r.id)
   );

-- Genau ein aktiver Lauf je Haushalt. Der Startendpunkt faengt die
-- Unique-Verletzung ab und liefert den bestehenden Lauf zurueck; damit
-- erzeugen auch zwei exakt gleichzeitige Starts nur einen Job.
create unique index if not exists classification_runs_one_active_idx
  on classification_runs (household_id)
  where status in ('pending', 'running');

-- Idempotenz je Haushalt: derselbe request_id kann nicht zweimal starten.
create unique index if not exists classification_runs_request_id_idx
  on classification_runs (household_id, request_id)
  where request_id is not null;

-- Laufhistorie/Statusfilter (GET /v1/ai/classify-runs) - der bestehende
-- Index deckt nur (household_id, created_at desc) ohne Status ab.
create index if not exists classification_runs_household_status_idx
  on classification_runs (household_id, status, created_at desc);

-- ---------------------------------------------------------------------------
-- Ehemals: 0007_category_management.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- Finanzplaner — Migration 0007: Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md)
-- ============================================================================
-- Liefert Abschnitt 0 des Kategorisierungsplans ("Integritaet und historische
-- Korrektheit") plus die Struktur fuer Standardpaket, Operationsvorschauen und
-- Merge-Tombstones.
--
-- Bewusst rein additiv und idempotent formuliert (IF NOT EXISTS / DROP ...
-- IF EXISTS), damit die Datei mehrfach gegen eine bereits migrierte Datenbank
-- laufen darf. Auf diesem Projekt gibt es kein psql/supabase-CLI; die Datei
-- wird per psycopg als eine Transaktion angewendet.
--
-- Die Baseline 0001_schema.sql bleibt unveraendert.

-- ============================================================================
-- 1. Namensnormalisierung
-- ============================================================================
-- Duplikaterkennung und Suche behandeln Gross-/Kleinschreibung, Umlaute und
-- Mehrfachleerzeichen gleichwertig (Plan: "Kategorieformular / Validierung").
-- IMMUTABLE, weil die Funktion in partiellen Unique-Indizes verwendet wird.

create or replace function category_normalize_name(p_name text)
returns text
language sql
immutable
as $$
  select btrim(regexp_replace(
    replace(replace(replace(replace(
      lower(btrim(coalesce(p_name, ''))),
      'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ß', 'ss'),
    '\s+', ' ', 'g'))
$$;

comment on function category_normalize_name(text) is
  'Normalisiert Kategorie-/Gruppennamen fuer Duplikatpruefung und Suche: '
  'trimmen, Kleinschreibung, ae/oe/ue/ss-Umschrift, Mehrfachleerzeichen zu '
  'einem Leerzeichen. Wird in den aktiven Unique-Indizes verwendet.';

-- ============================================================================
-- 2. Erweiterungen an category_groups und categories
-- ============================================================================

alter table category_groups
  add column if not exists updated_at       timestamptz not null default now(),
  add column if not exists version          int not null default 1,
  -- Gruppen tragen wie Kategorien Symbol und Farbe: die Verwaltungsansicht und
  -- der Suchpicker stellen Gruppenkarten dar (Plan: "Zieloberflaeche"), und die
  -- Vorlagentabelle category_group_templates fuehrt beide Felder bereits.
  add column if not exists icon             text,
  add column if not exists color            text,
  add column if not exists is_archived      boolean not null default false,
  add column if not exists archived_at      timestamptz,
  add column if not exists archived_by      uuid references auth.users(id) on delete set null,
  add column if not exists semantic_key     text,
  add column if not exists origin           text not null default 'user',
  add column if not exists taxonomy_version int;

alter table categories
  add column if not exists updated_at            timestamptz not null default now(),
  add column if not exists version               int not null default 1,
  add column if not exists archived_at           timestamptz,
  add column if not exists archived_by           uuid references auth.users(id) on delete set null,
  add column if not exists merged_into_category_id uuid references categories(id) on delete set null,
  add column if not exists semantic_key          text,
  add column if not exists origin                text not null default 'user',
  add column if not exists taxonomy_version      int,
  add column if not exists description           text,
  add column if not exists synonyms              text[] not null default '{}'::text[];

-- Bestandsdaten stammen aus dem hartcodierten Seed von create_household() bzw.
-- aus Nutzeranlagen ohne Herkunftsinformation. Sie werden als 'legacy'
-- markiert, damit ein spaeteres Standardpaket-Upgrade sie nicht stillschweigend
-- als eigene Standardkategorie umdeutet (Plan: "Versioniertes Standardpaket").
update category_groups set origin = 'legacy' where origin = 'user' and taxonomy_version is null;
update categories       set origin = 'legacy' where origin = 'user' and taxonomy_version is null;

alter table category_groups drop constraint if exists category_groups_origin_check;
alter table category_groups add constraint category_groups_origin_check
  check (origin in ('standard', 'user', 'legacy'));

alter table categories drop constraint if exists categories_origin_check;
alter table categories add constraint categories_origin_check
  check (origin in ('standard', 'user', 'legacy'));

-- Ein Tombstone zeigt immer auf eine andere Kategorie und ist selbst archiviert.
alter table categories drop constraint if exists categories_merge_self_check;
alter table categories add constraint categories_merge_self_check
  check (merged_into_category_id is null or merged_into_category_id <> id);

comment on column categories.merged_into_category_id is
  'Merge-Tombstone: gesetzt, wenn diese Kategorie in eine andere '
  'zusammengefuehrt wurde. Die Zeile bleibt archiviert erhalten, damit '
  'verspaetete Offline-Requests mit der alten ID serverseitig auf das aktive '
  'Ziel kanonisiert werden koennen.';

comment on column categories.synonyms is
  'Zusaetzliche Suchbegriffe fuer den Kategoriepicker (Plan: "Eine gemeinsame, '
  'durchsuchbare Kategorieauswahl"). Keine fachliche Bedeutung darueber hinaus.';

-- ---------- Aktive Eindeutigkeit statt case-sensitiver Constraints ----------
-- Die bisherigen Constraints waren case-sensitiv und verhinderten, dass ein
-- archivierter Name spaeter erneut verwendet wird. Ersetzt durch partielle,
-- normalisierte Unique-Indizes auf den aktiven Zeilen.

alter table category_groups drop constraint if exists category_groups_household_id_name_key;
alter table categories      drop constraint if exists categories_household_id_group_id_name_key;

create unique index if not exists category_groups_active_name_uniq
  on category_groups (household_id, category_normalize_name(name))
  where not is_archived;

create unique index if not exists categories_active_name_uniq
  on categories (household_id, group_id, category_normalize_name(name))
  where not is_archived;

create unique index if not exists category_groups_semantic_key_uniq
  on category_groups (household_id, semantic_key)
  where semantic_key is not null;

create unique index if not exists categories_semantic_key_uniq
  on categories (household_id, semantic_key)
  where semantic_key is not null;

create index if not exists category_groups_household_sort_idx
  on category_groups (household_id, sort_order)
  where not is_archived;

create index if not exists categories_household_group_sort_idx
  on categories (household_id, group_id, sort_order)
  where not is_archived;

create index if not exists categories_merged_into_idx
  on categories (merged_into_category_id)
  where merged_into_category_id is not null;

-- ---------- Optimistische Nebenlaeufigkeit -----------------------------------
-- Gleiches Muster wie bump_transaction_version() (0004_transaction_versioning):
-- der Zaehler wird ausschliesslich im Trigger gepflegt, ein No-Op-Update
-- erzeugt keinen Scheinkonflikt bei anderen Clients.

create or replace function bump_category_version()
returns trigger
language plpgsql
as $$
begin
  if (to_jsonb(new) - 'version' - 'updated_at')
     is distinct from (to_jsonb(old) - 'version' - 'updated_at') then
    new.version := old.version + 1;
    new.updated_at := now();
  else
    new.version := old.version;
    new.updated_at := old.updated_at;
  end if;
  return new;
end;
$$;

drop trigger if exists category_groups_bump_version on category_groups;
create trigger category_groups_bump_version
  before update on category_groups
  for each row execute function bump_category_version();

drop trigger if exists categories_bump_version on categories;
create trigger categories_bump_version
  before update on categories
  for each row execute function bump_category_version();

-- ============================================================================
-- 3. Haushaltsgrenzen in der Datenbank
-- ============================================================================
-- RLS waehlt nur den sichtbaren Ausschnitt; sie verhindert nicht, dass ein
-- Schreibpfad eine bekannte Fremd-UUID als category_id einsetzt. Deshalb wird
-- jede Kategorie-Referenz zu einem zusammengesetzten Fremdschluessel
-- (category_id, household_id) -> categories(id, household_id).

-- Bewusst "anlegen, falls nicht vorhanden" statt drop-and-add: ab dem zweiten
-- Lauf haengen die zusammengesetzten Fremdschluessel weiter unten an genau
-- diesen Unique-Indizes, ein DROP scheiterte dann an der Abhaengigkeit.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'category_groups_id_household_uniq'
  ) then
    alter table category_groups
      add constraint category_groups_id_household_uniq unique (id, household_id);
  end if;

  if not exists (
    select 1 from pg_constraint where conname = 'categories_id_household_uniq'
  ) then
    alter table categories
      add constraint categories_id_household_uniq unique (id, household_id);
  end if;
end $$;

-- Eine Kategorie gehoert zwingend in eine Gruppe desselben Haushalts.
alter table categories drop constraint if exists categories_group_id_fkey;
alter table categories drop constraint if exists categories_group_household_fkey;
alter table categories add constraint categories_group_household_fkey
  foreign key (group_id, household_id)
  references category_groups (id, household_id)
  on update cascade
  deferrable initially deferred;

-- Vor dem Umbau pruefen, ob Bestandsdaten ueberhaupt haushaltsrein sind.
-- Verdaechtige Zeilen werden nicht automatisch umgehaengt, sondern die
-- Migration bricht ab (Plan: "bricht bei einem Treffer ab").
do $$
declare
  v_offenders bigint := 0;
  v_count bigint;
  v_report text := '';
  v_table text;
  v_column text;
begin
  for v_table, v_column in
    select * from (values
      ('transaction_splits', 'category_id'),
      ('payees', 'default_category_id'),
      ('classification_rules', 'action_category_id'),
      ('recurring_rules', 'category_id'),
      ('subscriptions', 'category_id'),
      ('budget_allocations', 'category_id'),
      ('budget_rollovers', 'category_id'),
      ('classification_decisions', 'category_id'),
      ('savings_goals', 'category_id')
    ) as t(tbl, col)
  loop
    execute format(
      'select count(*) from %I r join categories c on c.id = r.%I '
      'where r.%I is not null and c.household_id <> r.household_id',
      v_table, v_column, v_column
    ) into v_count;
    if v_count > 0 then
      v_offenders := v_offenders + v_count;
      v_report := v_report || format('%s.%s=%s ', v_table, v_column, v_count);
    end if;
  end loop;

  if v_offenders > 0 then
    raise exception using
      errcode = '23514',
      message = 'CROSS_HOUSEHOLD_CATEGORY_REFERENCES: ' || v_report,
      hint = 'Fremdhaushaltige Kategorie-Referenzen zuerst fachlich klaeren, '
             'die Migration haengt sie bewusst nicht automatisch um.';
  end if;
end $$;

-- budget_rollovers bekommt eine Eindeutigkeit je Budget/Kategorie, damit ein
-- Merge zwei Rollover-Zeilen deterministisch addieren kann. Bestehende
-- Mehrfachzeilen werden vorher zur aeltesten Zeile aufsummiert.
with ranked as (
  select id, budget_id, category_id, amount_minor,
         row_number() over (partition by budget_id, category_id
                            order by created_at, id) as rn,
         sum(amount_minor) over (partition by budget_id, category_id) as total
  from budget_rollovers
)
update budget_rollovers br
set amount_minor = ranked.total
from ranked
where br.id = ranked.id and ranked.rn = 1 and br.amount_minor <> ranked.total;

delete from budget_rollovers br
where exists (
  select 1 from budget_rollovers keep
  where keep.budget_id = br.budget_id
    and keep.category_id = br.category_id
    and (keep.created_at, keep.id) < (br.created_at, br.id)
);

create unique index if not exists budget_rollovers_budget_category_uniq
  on budget_rollovers (budget_id, category_id);

-- Zusammengesetzte Fremdschluessel. Bewusst ohne fachliches Cascade-Loeschen:
-- eine einzelne Kategorie darf niemals Finanzhistorie mitloeschen. Die
-- Haushaltsloeschung bleibt ueber die household_id-Cascades moeglich; damit
-- die dabei gleichzeitig kaskadierten Eltern-/Kindzeilen nicht in einer
-- Pruefreihenfolge scheitern, sind die Constraints DEFERRABLE.
do $$
declare
  v_table text;
  v_column text;
  v_old text;
  v_new text;
begin
  for v_table, v_column, v_old in
    select * from (values
      ('transaction_splits', 'category_id', 'transaction_splits_category_id_fkey'),
      ('payees', 'default_category_id', 'payees_default_category_id_fkey'),
      ('classification_rules', 'action_category_id', 'classification_rules_action_category_id_fkey'),
      ('recurring_rules', 'category_id', 'recurring_rules_category_id_fkey'),
      ('subscriptions', 'category_id', 'subscriptions_category_id_fkey'),
      ('budget_allocations', 'category_id', 'budget_allocations_category_id_fkey'),
      ('budget_rollovers', 'category_id', 'budget_rollovers_category_id_fkey'),
      ('classification_decisions', 'category_id', 'classification_decisions_category_id_fkey'),
      ('savings_goals', 'category_id', 'savings_goals_category_id_fkey')
    ) as t(tbl, col, old_name)
  loop
    v_new := v_table || '_category_household_fkey';
    execute format('alter table %I drop constraint if exists %I', v_table, v_old);
    execute format('alter table %I drop constraint if exists %I', v_table, v_new);
    execute format(
      'alter table %I add constraint %I foreign key (%I, household_id) '
      'references categories (id, household_id) on update cascade '
      'deferrable initially deferred',
      v_table, v_new, v_column
    );
  end loop;
end $$;

-- ---------- Kein Hard-Delete ueber die Anwendungsrolle ----------------------
-- Die RLS-Policies sind "for all" und wuerden ein DELETE zulassen. Das
-- Tabellenrecht wird deshalb explizit entzogen; API und UI bieten ohnehin
-- keinen Hard-Delete-Endpunkt an (Plan: "Es gibt kein hartes Loeschen").
revoke delete on categories from authenticated;
revoke delete on category_groups from authenticated;

-- ---------- Neue Referenzen auf archivierte Kategorien ablehnen -------------

create or replace function reject_archived_category_reference()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_column text := tg_argv[0];
  v_new_id uuid;
  v_old_id uuid;
  v_archived boolean;
begin
  execute format('select ($1).%I', v_column) into v_new_id using new;
  if v_new_id is null then
    return new;
  end if;

  if tg_op = 'UPDATE' then
    execute format('select ($1).%I', v_column) into v_old_id using old;
    -- Bestandszeilen mit archivierter Kategorie bleiben aenderbar, solange die
    -- Kategorie selbst gleich bleibt: Historie darf nicht einfrieren.
    if v_old_id is not distinct from v_new_id then
      return new;
    end if;
  end if;

  select c.is_archived into v_archived from categories c where c.id = v_new_id;
  if coalesce(v_archived, false) then
    raise exception using
      errcode = '23514',
      message = 'CATEGORY_ARCHIVED',
      hint = 'Archivierte Kategorien koennen nicht neu zugeordnet werden.';
  end if;

  return new;
end;
$$;

comment on function reject_archived_category_reference() is
  'Zweite Schutzschicht hinter category_service.py: kein Schreibpfad - auch '
  'kein vergessener Worker- oder SQL-Pfad - darf eine archivierte Kategorie '
  'neu zuordnen. Der erste Trigger-Parameter ist der Spaltenname.';

do $$
declare
  v_table text;
  v_column text;
  v_trigger text;
begin
  for v_table, v_column in
    select * from (values
      ('transaction_splits', 'category_id'),
      ('payees', 'default_category_id'),
      ('classification_rules', 'action_category_id'),
      ('recurring_rules', 'category_id'),
      ('subscriptions', 'category_id'),
      ('budget_allocations', 'category_id'),
      ('budget_rollovers', 'category_id'),
      ('savings_goals', 'category_id')
    ) as t(tbl, col)
  loop
    v_trigger := v_table || '_reject_archived_category';
    execute format('drop trigger if exists %I on %I', v_trigger, v_table);
    execute format(
      'create trigger %I before insert or update on %I '
      'for each row execute function reject_archived_category_reference(%L)',
      v_trigger, v_table, v_column
    );
  end loop;
end $$;

-- classification_decisions bleibt bewusst ohne diesen Trigger: die Tabelle ist
-- ein unveraenderlicher Entscheidungsnachweis und darf die damals gewaehlte
-- Kategorie auch nach deren Archivierung festhalten.

-- ============================================================================
-- 4. Versionierter Standardkatalog
-- ============================================================================
-- Globale, nur lesbare Vorlagen. Eine Quelle der Wahrheit fuer neue Haushalte
-- (create_household) und fuer "Standardkategorien ergaenzen" bei
-- Bestandshaushalten.

create table if not exists category_taxonomy_versions (
  version     int primary key,
  locale      text not null default 'de-DE',
  label       text not null,
  is_released boolean not null default false,
  released_at timestamptz,
  created_at  timestamptz not null default now()
);

create table if not exists category_group_templates (
  id               uuid primary key default gen_random_uuid(),
  taxonomy_version int not null references category_taxonomy_versions(version) on delete cascade,
  semantic_key     text not null,
  name             text not null,
  sort_order       int not null default 0,
  icon             text,
  color            text,
  unique (taxonomy_version, semantic_key)
);

create table if not exists category_templates (
  id                 uuid primary key default gen_random_uuid(),
  taxonomy_version   int not null references category_taxonomy_versions(version) on delete cascade,
  group_semantic_key text not null,
  semantic_key       text not null,
  name               text not null,
  default_type       text check (default_type in ('income', 'expense', 'transfer')),
  budget_bucket      text check (budget_bucket in ('needs', 'wants', 'savings')),
  is_budgeted        boolean not null default true,
  sort_order         int not null default 0,
  icon               text,
  color              text,
  description        text,
  synonyms           text[] not null default '{}'::text[],
  unique (taxonomy_version, semantic_key),
  foreign key (taxonomy_version, group_semantic_key)
    references category_group_templates (taxonomy_version, semantic_key) on delete cascade
);

create index if not exists category_templates_group_idx
  on category_templates (taxonomy_version, group_semantic_key, sort_order);

create table if not exists household_category_taxonomy (
  household_id     uuid primary key references households(id) on delete cascade
                     default current_household_id(),
  taxonomy_version int references category_taxonomy_versions(version),
  revision         bigint not null default 1,
  applied_at       timestamptz,
  applied_by       uuid references auth.users(id) on delete set null,
  updated_at       timestamptz not null default now()
);

comment on column household_category_taxonomy.revision is
  'Monotone Baum-Revision als ETag fuer baumweite Operationen (Reorder, '
  'Paket-Upgrade). Einzelne Datensaetze schuetzt dagegen version an Gruppe '
  'bzw. Kategorie.';

create table if not exists category_operations (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  operation         text not null check (operation in
                      ('move', 'archive', 'restore', 'merge',
                       'group_archive', 'group_merge', 'taxonomy_apply')),
  status            text not null default 'preview' check (status in
                      ('preview', 'applied', 'expired', 'failed')),
  created_by        uuid references auth.users(id) on delete set null,
  request_id        text,
  source_id         uuid,
  target_id         uuid,
  source_version    int,
  target_version    int,
  taxonomy_revision bigint,
  fingerprint       text not null,
  impact            jsonb not null default '{}'::jsonb,
  result            jsonb,
  expires_at        timestamptz not null default now() + interval '30 minutes',
  applied_at        timestamptz,
  created_at        timestamptz not null default now()
);

create index if not exists category_operations_household_idx
  on category_operations (household_id, created_at desc);

create unique index if not exists category_operations_request_uniq
  on category_operations (household_id, operation, request_id)
  where request_id is not null;

comment on table category_operations is
  'Kurzlebige Vorschauen und idempotente Ergebnisse fuer Move, Archive, '
  'Merge und Paket-Upgrade. Bewusst keine generische Job-Infrastruktur.';

-- ---------- Baum-Revision automatisch fortschreiben -------------------------

create or replace function bump_household_category_revision()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Nur echte fachliche Aenderungen zaehlen: bump_category_version() laesst die
  -- Version bei einem No-Op-Update unveraendert, sonst entstuenden 409er bei
  -- baumweiten Operationen ohne tatsaechliche Aenderung.
  if tg_op = 'UPDATE' and new.version = old.version then
    return null;
  end if;

  insert into household_category_taxonomy (household_id, revision)
  values (new.household_id, 1)
  on conflict (household_id) do update
    set revision = household_category_taxonomy.revision + 1,
        updated_at = now();

  return null;
end;
$$;

drop trigger if exists category_groups_bump_revision on category_groups;
create trigger category_groups_bump_revision
  after insert or update on category_groups
  for each row execute function bump_household_category_revision();

drop trigger if exists categories_bump_revision on categories;
create trigger categories_bump_revision
  after insert or update on categories
  for each row execute function bump_household_category_revision();

-- ---------- Row Level Security ----------------------------------------------

alter table category_taxonomy_versions  enable row level security;
alter table category_group_templates    enable row level security;
alter table category_templates          enable row level security;
alter table household_category_taxonomy enable row level security;
alter table category_operations         enable row level security;

-- Die drei Template-Tabellen sind global und haushaltsunabhaengig: jeder
-- angemeldete Nutzer darf lesen, schreiben darf ausschliesslich die Migration
-- (kein insert/update/delete-Recht fuer authenticated).
drop policy if exists category_taxonomy_versions_read on category_taxonomy_versions;
create policy category_taxonomy_versions_read on category_taxonomy_versions
  for select to authenticated using (true);

drop policy if exists category_group_templates_read on category_group_templates;
create policy category_group_templates_read on category_group_templates
  for select to authenticated using (true);

drop policy if exists category_templates_read on category_templates;
create policy category_templates_read on category_templates
  for select to authenticated using (true);

drop policy if exists household_category_taxonomy_read on household_category_taxonomy;
create policy household_category_taxonomy_read on household_category_taxonomy
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

drop policy if exists household_category_taxonomy_write on household_category_taxonomy;
create policy household_category_taxonomy_write on household_category_taxonomy
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists category_operations_read on category_operations;
create policy category_operations_read on category_operations
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

drop policy if exists category_operations_write on category_operations;
create policy category_operations_write on category_operations
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select on category_taxonomy_versions, category_group_templates, category_templates
  to authenticated;
grant select, insert, update, delete on household_category_taxonomy, category_operations
  to authenticated;

-- ============================================================================
-- 5. Deutsches Standardpaket v1: 9 Gruppen, 50 Kategorien
-- ============================================================================

insert into category_taxonomy_versions (version, locale, label, is_released, released_at)
values (1, 'de-DE', 'Deutsches Standardpaket v1', true, now())
on conflict (version) do update
  set label = excluded.label,
      is_released = excluded.is_released;

insert into category_group_templates (taxonomy_version, semantic_key, name, sort_order, icon, color)
values
  (1, 'income',     'Einnahmen',                          0, '💶', '#2E7D32'),
  (1, 'housing',    'Wohnen & Haushalt',                  1, '🏠', '#5D4037'),
  (1, 'food',       'Lebensmittel & Gastronomie',         2, '🍽️', '#EF6C00'),
  (1, 'mobility',   'Mobilität',                          3, '🚆', '#1565C0'),
  (1, 'health',     'Gesundheit, Versicherung & Vorsorge', 4, '🩺', '#00838F'),
  (1, 'family',     'Familie, Bildung & Haustiere',       5, '👨‍👩‍👧', '#6A1B9A'),
  (1, 'lifestyle',  'Freizeit, Konsum & Reisen',          6, '🎧', '#AD1457'),
  (1, 'finance',    'Finanzen & Verpflichtungen',         7, '🏦', '#37474F'),
  (1, 'transfers',  'Transfers',                          8, '🔁', '#455A64')
on conflict (taxonomy_version, semantic_key) do update
  set name = excluded.name,
      sort_order = excluded.sort_order,
      icon = excluded.icon,
      color = excluded.color;

insert into category_templates (
  taxonomy_version, group_semantic_key, semantic_key, name,
  default_type, budget_bucket, is_budgeted, sort_order, icon, synonyms
) values
  -- Einnahmen (E: Einnahme, nicht budgetiert)
  (1, 'income', 'salary', 'Gehalt & Lohn', 'income', null, false, 0, '💼',
   array['lohn', 'gehalt', 'entgelt', 'bezuege', 'arbeitgeber', 'nettolohn']),
  (1, 'income', 'self_employment', 'Selbstständigkeit & Nebenverdienst', 'income', null, false, 1, '🧾',
   array['honorar', 'freelance', 'freiberuflich', 'nebenjob', 'rechnung', 'umsatz']),
  (1, 'income', 'pension', 'Rente & Pension', 'income', null, false, 2, '🧓',
   array['rente', 'pension', 'altersbezuege', 'drv']),
  (1, 'income', 'public_benefits', 'Kindergeld, Unterhalt & staatliche Leistungen', 'income', null, false, 3, '🏛️',
   array['kindergeld', 'elterngeld', 'buergergeld', 'wohngeld', 'bafoeg', 'unterhalt', 'familienkasse']),
  (1, 'income', 'refunds', 'Erstattungen & Rückzahlungen', 'income', null, false, 4, '↩️',
   array['erstattung', 'rueckzahlung', 'gutschrift', 'retoure', 'kaution zurueck', 'steuererstattung']),
  (1, 'income', 'capital_income', 'Kapitalerträge', 'income', null, false, 5, '📈',
   array['zinsen', 'dividende', 'ausschuettung', 'kapitalertrag', 'depot']),

  -- Wohnen & Haushalt (B: Bedarf)
  (1, 'housing', 'rent', 'Miete & Hausgeld', 'expense', 'needs', true, 0, '🔑',
   array['miete', 'kaltmiete', 'hausgeld', 'wohngeld hausverwaltung', 'wohnung']),
  (1, 'housing', 'service_charges', 'Nebenkosten', 'expense', 'needs', true, 1, '🧮',
   array['nebenkosten', 'betriebskosten', 'wasser', 'abwasser', 'muell', 'grundsteuer']),
  (1, 'housing', 'energy', 'Strom, Gas & Heizung', 'expense', 'needs', true, 2, '⚡',
   array['strom', 'gas', 'heizung', 'fernwaerme', 'stadtwerke', 'energie', 'heizoel']),
  (1, 'housing', 'telecom', 'Internet, Festnetz & Mobilfunk', 'expense', 'needs', true, 3, '📶',
   array['internet', 'dsl', 'glasfaser', 'mobilfunk', 'handyvertrag', 'telekom', 'vodafone', 'o2']),
  (1, 'housing', 'broadcast_fee', 'Rundfunkbeitrag', 'expense', 'needs', true, 4, '📻',
   array['rundfunkbeitrag', 'gez', 'ard zdf deutschlandradio', 'beitragsservice']),
  (1, 'housing', 'furnishing', 'Einrichtung & Instandhaltung', 'expense', 'needs', true, 5, '🛋️',
   array['moebel', 'einrichtung', 'renovierung', 'handwerker', 'baumarkt', 'ikea', 'reparatur']),
  (1, 'housing', 'household_supplies', 'Drogerie, Reinigung & Haushalt', 'expense', 'needs', true, 6, '🧴',
   array['drogerie', 'dm', 'rossmann', 'putzmittel', 'waschmittel', 'hygiene', 'haushaltswaren']),

  -- Lebensmittel & Gastronomie
  (1, 'food', 'groceries', 'Lebensmittel', 'expense', 'needs', true, 0, '🛒',
   array['supermarkt', 'lebensmittel', 'einkauf', 'rewe', 'edeka', 'aldi', 'lidl', 'penny', 'kaufland', 'netto']),
  (1, 'food', 'bakery_cafe', 'Bäckerei & Café', 'expense', 'wants', true, 1, '🥐',
   array['baeckerei', 'cafe', 'kaffee', 'baecker', 'starbucks', 'konditorei']),
  (1, 'food', 'restaurant', 'Restaurant', 'expense', 'wants', true, 2, '🍴',
   array['restaurant', 'essen gehen', 'gaststaette', 'imbiss', 'bar', 'kneipe', 'pizzeria']),
  (1, 'food', 'delivery_canteen', 'Lieferdienst, Kantine & Mensa', 'expense', 'wants', true, 3, '🛵',
   array['lieferando', 'lieferdienst', 'kantine', 'mensa', 'wolt', 'uber eats', 'mittagessen']),

  -- Mobilität
  (1, 'mobility', 'public_transport', 'ÖPNV & Bahn', 'expense', 'needs', true, 0, '🚈',
   array['oepnv', 'opnv', 'bahn', 'db', 'deutschlandticket', 'monatskarte', 'bus', 'ubahn', 'sbahn',
         'strassenbahn', 'nahverkehr', 'zug', 'fahrkarte', 'ticket', 'vrr', 'mvg', 'bvg', 'hvv', '49 euro ticket']),
  (1, 'mobility', 'fuel_charging', 'Tanken & Laden', 'expense', 'needs', true, 1, '⛽',
   array['tanken', 'benzin', 'diesel', 'tankstelle', 'laden', 'ladesaeule', 'aral', 'shell', 'jet', 'ionity']),
  (1, 'mobility', 'parking_toll', 'Parken, Maut & Fähre', 'expense', 'needs', true, 2, '🅿️',
   array['parken', 'parkhaus', 'parkschein', 'maut', 'vignette', 'faehre', 'parkgebuehr']),
  (1, 'mobility', 'car_service', 'Werkstatt, Wartung & Reifen', 'expense', 'needs', true, 3, '🔧',
   array['werkstatt', 'inspektion', 'tuev', 'reifen', 'oelwechsel', 'autoteile', 'reparatur auto']),
  (1, 'mobility', 'car_tax_insurance', 'Kfz-Steuer & Kfz-Versicherung', 'expense', 'needs', true, 4, '🚗',
   array['kfz steuer', 'kfz versicherung', 'autoversicherung', 'haftpflicht auto', 'kaskoversicherung', 'zulassung']),
  (1, 'mobility', 'taxi_sharing', 'Taxi, Sharing & Mietfahrzeug', 'expense', 'wants', true, 5, '🚕',
   array['taxi', 'uber', 'bolt', 'carsharing', 'mietwagen', 'leihwagen', 'escooter', 'sixt', 'miles']),

  -- Gesundheit, Versicherung & Vorsorge
  (1, 'health', 'doctor', 'Arzt & Zahnarzt', 'expense', 'needs', true, 0, '🩺',
   array['arzt', 'zahnarzt', 'praxis', 'facharzt', 'zuzahlung', 'behandlung', 'klinik']),
  (1, 'health', 'pharmacy', 'Apotheke & Medikamente', 'expense', 'needs', true, 1, '💊',
   array['apotheke', 'medikament', 'rezept', 'arznei', 'tabletten']),
  (1, 'health', 'therapy_care', 'Therapie, Pflege & Hilfsmittel', 'expense', 'needs', true, 2, '🦽',
   array['therapie', 'physiotherapie', 'psychotherapie', 'pflege', 'brille', 'hoergeraet', 'orthopaedie']),
  (1, 'health', 'health_insurance', 'Kranken- & Pflegeversicherung', 'expense', 'needs', true, 3, '🏥',
   array['krankenversicherung', 'krankenkasse', 'pflegeversicherung', 'pkv', 'gkv', 'aok', 'tk', 'barmer']),
  (1, 'health', 'property_insurance', 'Haftpflicht, Hausrat & Rechtsschutz', 'expense', 'needs', true, 4, '🛡️',
   array['haftpflicht', 'hausrat', 'rechtsschutz', 'versicherung', 'wohngebaeude']),
  (1, 'health', 'risk_insurance', 'Berufsunfähigkeit, Unfall & Leben', 'expense', 'needs', true, 5, '📋',
   array['berufsunfaehigkeit', 'bu versicherung', 'unfallversicherung', 'lebensversicherung', 'risikoleben']),
  (1, 'health', 'private_pension', 'Private Altersvorsorge', 'expense', 'savings', true, 6, '🪙',
   array['altersvorsorge', 'riester', 'ruerup', 'betriebliche altersvorsorge', 'bav', 'rentenversicherung privat']),

  -- Familie, Bildung & Haustiere
  (1, 'family', 'childcare_school', 'Kita, Schule & Betreuung', 'expense', 'needs', true, 0, '🎒',
   array['kita', 'kindergarten', 'schule', 'betreuung', 'hort', 'tagesmutter', 'schulgeld']),
  (1, 'family', 'child_supplies', 'Kinderbedarf & Taschengeld', 'expense', 'needs', true, 1, '🧸',
   array['kinder', 'taschengeld', 'windeln', 'spielzeug', 'kinderkleidung', 'babybedarf']),
  (1, 'family', 'alimony_paid', 'Unterhalt', 'expense', 'needs', true, 2, '🤝',
   array['unterhalt', 'unterhaltszahlung', 'kindesunterhalt', 'ehegattenunterhalt']),
  (1, 'family', 'education', 'Kurse, Studium & Weiterbildung', 'expense', 'wants', true, 3, '🎓',
   array['studium', 'semesterbeitrag', 'weiterbildung', 'kurs', 'seminar', 'fortbildung', 'buecher lernen']),
  (1, 'family', 'pets', 'Haustiere', 'expense', 'wants', true, 4, '🐾',
   array['haustier', 'tierarzt', 'tierfutter', 'hund', 'katze', 'fressnapf', 'tierhalterhaftpflicht']),

  -- Freizeit, Konsum & Reisen
  (1, 'lifestyle', 'clothing', 'Kleidung & Schuhe', 'expense', 'wants', true, 0, '👕',
   array['kleidung', 'schuhe', 'mode', 'textil', 'zalando', 'hm', 'jacke']),
  (1, 'lifestyle', 'electronics', 'Elektronik & Technik', 'expense', 'wants', true, 1, '💻',
   array['elektronik', 'technik', 'computer', 'handy', 'mediamarkt', 'saturn', 'zubehoer']),
  (1, 'lifestyle', 'hobby_gaming', 'Hobby, Gaming & Basteln', 'expense', 'wants', true, 2, '🎮',
   array['hobby', 'gaming', 'spiele', 'basteln', 'steam', 'playstation', 'modellbau']),
  (1, 'lifestyle', 'streaming', 'Streaming & digitale Medien', 'expense', 'wants', true, 3, '📺',
   array['streaming', 'netflix', 'spotify', 'disney', 'abo digital', 'youtube premium', 'apple music', 'zeitung digital']),
  (1, 'lifestyle', 'sports_clubs', 'Sport, Fitness & Vereine', 'expense', 'wants', true, 4, '🏋️',
   array['sport', 'fitness', 'fitnessstudio', 'verein', 'mitgliedsbeitrag', 'schwimmbad', 'yoga']),
  (1, 'lifestyle', 'culture_events', 'Kultur, Kino & Veranstaltungen', 'expense', 'wants', true, 5, '🎭',
   array['kino', 'theater', 'konzert', 'museum', 'veranstaltung', 'festival', 'eventim']),
  (1, 'lifestyle', 'travel', 'Urlaub, Reise & Unterkunft', 'expense', 'wants', true, 6, '✈️',
   array['urlaub', 'reise', 'hotel', 'flug', 'booking', 'airbnb', 'ferienwohnung', 'pauschalreise']),
  (1, 'lifestyle', 'gifts', 'Geschenke', 'expense', 'wants', true, 7, '🎁',
   array['geschenk', 'praesent', 'geburtstag', 'weihnachtsgeschenk', 'blumen']),

  -- Finanzen & Verpflichtungen
  (1, 'finance', 'bank_fees', 'Bank- & Zahlungsgebühren', 'expense', 'needs', true, 0, '💳',
   array['kontofuehrung', 'kontogebuehr', 'bankgebuehr', 'entgelt bank', 'auslandseinsatzentgelt', 'depotgebuehr']),
  (1, 'finance', 'loan_interest', 'Kreditzinsen & Finanzierungskosten', 'expense', 'needs', true, 1, '📉',
   array['zinsen kredit', 'kreditzins', 'finanzierung', 'ratenkredit zinsen', 'sollzinsen', 'dispozinsen']),
  (1, 'finance', 'taxes_authorities', 'Steuern, Abgaben & Behörden', 'expense', 'needs', true, 2, '🏛️',
   array['finanzamt', 'steuer', 'steuernachzahlung', 'buergeramt', 'gebuehr behoerde', 'bussgeld']),
  (1, 'finance', 'donations', 'Spenden', 'expense', 'wants', true, 3, '❤️',
   array['spende', 'foerderbeitrag', 'kirchensteuer freiwillig', 'hilfsorganisation']),

  -- Transfers (T: nicht als Ausgabe budgetiert)
  (1, 'transfers', 'internal_transfer', 'Interne Umbuchung', 'transfer', null, false, 0, '🔁',
   array['umbuchung', 'uebertrag', 'eigenuebertrag', 'bargeldabhebung', 'geldautomat', 'einzahlung']),
  (1, 'transfers', 'card_settlement', 'Kreditkarte & Zahlungsdienst-Ausgleich', 'transfer', null, false, 1, '🧾',
   array['kreditkartenabrechnung', 'kreditkarte ausgleich', 'paypal ausgleich', 'lastschrift kreditkarte', 'amex abrechnung']),
  (1, 'transfers', 'saving_investing', 'Sparen, Investieren & Tilgung', 'transfer', null, false, 2, '🏦',
   array['sparen', 'sparplan', 'depot einzahlung', 'etf', 'tilgung', 'kredittilgung', 'ruecklage'])
on conflict (taxonomy_version, semantic_key) do update
  set group_semantic_key = excluded.group_semantic_key,
      name = excluded.name,
      default_type = excluded.default_type,
      budget_bucket = excluded.budget_bucket,
      is_budgeted = excluded.is_budgeted,
      sort_order = excluded.sort_order,
      icon = excluded.icon,
      synonyms = excluded.synonyms;

-- Sicherung der Planzusage "genau 9 Gruppen und 50 Kategorien".
do $$
declare
  v_groups int;
  v_categories int;
begin
  select count(*) into v_groups from category_group_templates where taxonomy_version = 1;
  select count(*) into v_categories from category_templates where taxonomy_version = 1;
  if v_groups <> 9 or v_categories <> 50 then
    raise exception using
      errcode = '23514',
      message = format('TAXONOMY_V1_INCOMPLETE: %s Gruppen, %s Kategorien', v_groups, v_categories);
  end if;
end $$;

-- ============================================================================
-- 6. Installation des Standardpakets in einen Haushalt
-- ============================================================================
-- Additiv und idempotent: vorhandene semantic_keys werden nicht angefasst,
-- Namenskollisionen mit vorhandenen aktiven Kategorien werden uebersprungen
-- (die API bietet dafuer die explizite Zuordnung/Merge-Vorschau an). Es wird
-- nichts geloescht, archiviert, verschoben oder umbenannt.

create or replace function install_category_taxonomy(
  p_household_id uuid,
  p_version int default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_version int;
  v_group record;
  v_category record;
  v_group_id uuid;
  v_created_groups int := 0;
  v_created_categories int := 0;
  v_skipped_conflicts int := 0;
  v_next_group_sort int;
  v_next_category_sort int;
begin
  v_version := coalesce(
    p_version,
    (select max(version) from category_taxonomy_versions where is_released)
  );
  if v_version is null then
    raise exception using errcode = '22023', message = 'NO_RELEASED_TAXONOMY_VERSION';
  end if;

  for v_group in
    select * from category_group_templates
    where taxonomy_version = v_version
    order by sort_order
  loop
    -- 1. bereits per semantic_key installiert?
    select id into v_group_id
    from category_groups
    where household_id = p_household_id and semantic_key = v_group.semantic_key;

    -- 2. sonst: aktive Gruppe mit demselben normalisierten Namen wiederverwenden,
    --    ohne ihr den Standardschluessel aufzudraengen.
    if v_group_id is null then
      select id into v_group_id
      from category_groups
      where household_id = p_household_id
        and not is_archived
        and category_normalize_name(name) = category_normalize_name(v_group.name);
    end if;

    if v_group_id is null then
      select coalesce(max(sort_order) + 1, 0) into v_next_group_sort
      from category_groups where household_id = p_household_id;

      insert into category_groups (
        household_id, name, sort_order, icon, color, semantic_key, origin, taxonomy_version
      )
      values (
        p_household_id, v_group.name,
        greatest(v_group.sort_order, v_next_group_sort),
        v_group.icon, v_group.color,
        v_group.semantic_key, 'standard', v_version
      )
      returning id into v_group_id;
      v_created_groups := v_created_groups + 1;
    end if;

    for v_category in
      select * from category_templates
      where taxonomy_version = v_version and group_semantic_key = v_group.semantic_key
      order by sort_order
    loop
      if exists (
        select 1 from categories
        where household_id = p_household_id and semantic_key = v_category.semantic_key
      ) then
        continue;
      end if;

      if exists (
        select 1 from categories
        where household_id = p_household_id
          and group_id = v_group_id
          and not is_archived
          and category_normalize_name(name) = category_normalize_name(v_category.name)
      ) then
        v_skipped_conflicts := v_skipped_conflicts + 1;
        continue;
      end if;

      select coalesce(max(sort_order) + 1, 0) into v_next_category_sort
      from categories where household_id = p_household_id and group_id = v_group_id;

      insert into categories (
        household_id, group_id, name, icon, color, is_budgeted, default_type,
        budget_bucket, sort_order, semantic_key, origin, taxonomy_version,
        description, synonyms
      )
      values (
        p_household_id, v_group_id, v_category.name,
        coalesce(v_category.icon, v_group.icon), coalesce(v_category.color, v_group.color),
        v_category.is_budgeted, v_category.default_type, v_category.budget_bucket,
        greatest(v_category.sort_order, v_next_category_sort),
        v_category.semantic_key, 'standard', v_version,
        v_category.description, v_category.synonyms
      );
      v_created_categories := v_created_categories + 1;
    end loop;
  end loop;

  insert into household_category_taxonomy (household_id, taxonomy_version, applied_at, applied_by)
  values (p_household_id, v_version, now(), auth.uid())
  on conflict (household_id) do update
    set taxonomy_version = excluded.taxonomy_version,
        applied_at = excluded.applied_at,
        applied_by = excluded.applied_by,
        revision = household_category_taxonomy.revision + 1,
        updated_at = now();

  return jsonb_build_object(
    'taxonomy_version', v_version,
    'created_groups', v_created_groups,
    'created_categories', v_created_categories,
    'skipped_name_conflicts', v_skipped_conflicts
  );
end;
$$;

comment on function install_category_taxonomy(uuid, int) is
  'Installiert eine freigegebene Standardpaket-Version additiv und idempotent '
  'in einen Haushalt. Einzige Quelle der Wahrheit fuer neue Haushalte '
  '(create_household) und fuer "Standardkategorien ergaenzen".';

-- create_household() verwendet ab jetzt denselben Installationspfad statt des
-- hartcodierten Fuenf-Gruppen-/Sechs-Kategorien-Seeds.
create or replace function create_household(p_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_household_id uuid;
begin
  if auth.uid() is null then
    raise exception using errcode = '42501', message = 'AUTH_REQUIRED';
  end if;
  if btrim(coalesce(p_name, '')) = '' then
    raise exception using errcode = '22023', message = 'INVALID_HOUSEHOLD_NAME';
  end if;

  insert into households (name, created_by)
  values (btrim(p_name), auth.uid())
  returning id into v_household_id;

  insert into household_members (household_id, user_id, role, status, joined_at)
  values (v_household_id, auth.uid(), 'owner', 'active', now());

  perform install_category_taxonomy(v_household_id, null);

  return v_household_id;
end $$;

-- ============================================================================
-- 7. Audit-Log: zusaetzliche Aktionen und Korrelation
-- ============================================================================

alter table audit_log add column if not exists correlation_id uuid;
alter table audit_log add column if not exists metadata jsonb;

alter table audit_log drop constraint if exists audit_log_action_check;
alter table audit_log add constraint audit_log_action_check
  check (action in (
    'create', 'update', 'delete',
    'archive', 'restore', 'move', 'reorder', 'merge', 'taxonomy_apply'
  ));

create index if not exists audit_log_correlation_idx
  on audit_log (household_id, correlation_id)
  where correlation_id is not null;

comment on column audit_log.correlation_id is
  'Klammert die Einzelereignisse eines zusammengesetzten Vorgangs (Merge, '
  'Archivierung mit Referenzaufloesung, Paket-Upgrade) zu einem lesbaren '
  'Vorgang statt tausender Einzelereignisse.';

-- ============================================================================
-- 8. Bestandshaushalte: Taxonomiezeile nachziehen
-- ============================================================================
-- Bestandshaushalte behalten ihre Legacy-Kategorien (Plan: "Die heutigen sechs
-- groben Legacy-Kategorien bleiben erhalten"). Sie bekommen hier nur die
-- Buchhaltungszeile, damit revision/Paketstand ab sofort verfolgt werden.

insert into household_category_taxonomy (household_id, taxonomy_version, revision)
select h.id, null, 1
from households h
on conflict (household_id) do nothing;

-- ---------------------------------------------------------------------------
-- Ehemals: 0008_analytics_provenance.sql
-- ---------------------------------------------------------------------------
-- Analyse-Ausbau, Lieferabschnitt 0: Herkunft und Bankrohtext an der Buchung.
--
-- Ausgangslage (siehe docs/ANALYSE-EXPLORER-PLAN.md, Baustein A):
--   1. transactions hatte kein Feld fuer den Verwendungszweck. Der Zweck der
--      Bank landete in note und wurde dort von action_note einer greifenden
--      Regel ueberschrieben (import_service.commit_single_row). Danach existierte
--      der Rohtext nur noch in import_rows.
--   2. transaction_splits hatte sieben Spalten, keine davon beschrieb, wie die
--      Kategorie zustande kam. Der Regeltreffer beim Import wurde berechnet und
--      sofort verworfen. Die Frage "warum ist diese Buchung in dieser Kategorie"
--      war damit nicht beantwortbar - und sie wird es rueckwirkend auch nicht,
--      weshalb diese Migration am Anfang des Ausbaus steht und nicht am Ende.
--
-- Additiv und idempotent: die Datei darf mehrfach angewendet werden.

-- ---------------------------------------------------------------- Rohtexte --

alter table transactions
  add column if not exists payee_raw             text,
  add column if not exists purpose_raw           text,
  add column if not exists bank_transaction_code text;

comment on column transactions.payee_raw is
  'Unveraenderter Gegenparteitext der Bank. payee_label ist die gekuerzte '
  'Anzeigeform, payee_raw der Beleg dafuer.';
comment on column transactions.purpose_raw is
  'Unveraenderter Verwendungszweck der Bank. Wird von keiner Regelaktion und '
  'keiner Nutzernotiz ueberschrieben - note ist wieder ausschliesslich die '
  'Notiz des Nutzers.';
comment on column transactions.bank_transaction_code is
  'Buchungsart/Bankcode aus MT940 (:86:?00) bzw. CAMT. Traegt die '
  'strukturierte Information, die eine Textsuche im Zweck nicht ersetzt.';

-- ---------------------------------------------------------------- Herkunft --

alter table transaction_splits
  add column if not exists category_source       text not null default 'unset',
  add column if not exists category_rule_id      uuid references classification_rules(id) on delete set null,
  add column if not exists category_decision_id  uuid references classification_decisions(id) on delete set null,
  add column if not exists category_confidence   numeric(3,2),
  add column if not exists category_matched_on   jsonb,
  add column if not exists categorized_at        timestamptz,
  add column if not exists categorized_by        uuid references auth.users(id) on delete set null;

-- 'unknown' ist bewusst von 'unset' getrennt: 'unset' heisst "keine Kategorie",
-- 'unknown' heisst "Kategorie vorhanden, Herkunft wurde damals nicht erfasst".
-- Bestandsdaten als 'manual' auszugeben waere eine Behauptung, die die Daten
-- nicht hergeben.
alter table transaction_splits drop constraint if exists transaction_splits_category_source_check;
alter table transaction_splits add constraint transaction_splits_category_source_check
  check (category_source in ('unset', 'unknown', 'manual', 'import', 'rule', 'ai', 'history'));

alter table transaction_splits drop constraint if exists transaction_splits_category_confidence_check;
alter table transaction_splits add constraint transaction_splits_category_confidence_check
  check (category_confidence is null or (category_confidence >= 0.00 and category_confidence <= 1.00));

comment on column transaction_splits.category_source is
  'Wie die Kategorie zustande kam: unset (keine Kategorie), unknown (vor '
  'Einfuehrung dieser Spalte zugeordnet), manual, import (Nutzerwahl im '
  'Importdialog), rule, ai, history.';
comment on column transaction_splits.category_matched_on is
  'Wodurch eine Regel gegriffen hat, z. B. {"field":"purpose","value":"STADTWERKE"}. '
  'Erlaubt die Klartextbegruendung in der Oberflaeche, ohne die Regel erneut '
  'auswerten zu muessen - sie kann sich inzwischen geaendert haben.';
comment on column transaction_splits.category_decision_id is
  'Verweis auf den KI-Entscheid, der diese Kategorie geliefert hat. Bleibt als '
  'historischer Nachweis bestehen, auch wenn spaeter ein Proposal-Lifecycle '
  '(KI-FINANZASSISTENT-PLAN.md, Ausbaustufe 1) danebentritt.';

create index if not exists transaction_splits_category_source_idx
  on transaction_splits (household_id, category_source);

-- Beantwortet "zeig mir alles, was Regel X zugeordnet hat" ohne Full Scan.
create index if not exists transaction_splits_category_rule_idx
  on transaction_splits (household_id, category_rule_id)
  where category_rule_id is not null;

-- ------------------------------------------------------------ Rueckbefuellung --

-- Die Rohtexte liegen fuer importierte Buchungen bereits in import_rows und
-- wurden beim Commit schlicht nicht durchgereicht. Der Trigger wird waehrend
-- der Rueckbefuellung abgeschaltet: fachlich aendert sich nichts, und ein
-- Versionssprung auf allen importierten Buchungen wuerde jedem offenen Client
-- sein If-Match entwerten.
alter table transactions disable trigger transactions_bump_version;

update transactions t
set payee_raw             = coalesce(t.payee_raw, r.payee_raw),
    purpose_raw           = coalesce(t.purpose_raw, r.purpose),
    bank_transaction_code = coalesce(t.bank_transaction_code, r.bank_transaction_code)
from import_rows r
where r.committed_transaction_id = t.id
  and (t.payee_raw is null or t.purpose_raw is null or t.bank_transaction_code is null)
  and (r.payee_raw is not null or r.purpose is not null or r.bank_transaction_code is not null);

alter table transactions enable trigger transactions_bump_version;

-- Bereits zugeordnete Splits bekommen 'unknown' statt des Spalten-Defaults
-- 'unset'; ohne Kategorie bleibt es bei 'unset'.
update transaction_splits
set category_source = 'unknown'
where category_id is not null
  and category_source = 'unset';

-- ---------------------------------------------------------------------------
-- Ehemals: 0009_analytics_indexes.sql
-- ---------------------------------------------------------------------------
-- Analyse-Ausbau, Lieferabschnitt 1: Indizes fuer den Abfragevertrag.
--
-- Jede Auswertung geht ueber transaction_splits JOIN transactions JOIN
-- accounts (siehe app/services/analytics_service.py). Die Baseline hat dafuer
-- nur transactions_household_account_idx, das den Kontofilter bedient - nicht
-- aber die zwei Zugriffsmuster, die der Explorer erzeugt:
--   1. Zeitraum ueber den ganzen Haushalt (Monatsmatrix, Cashflow),
--   2. Volltextsuche ueber Empfaenger/Zweck/Notiz.
--
-- Additiv und idempotent.

-- ------------------------------------------------------------- Zeitraum --

-- Deckt "alle Buchungen eines Haushalts in einem Zeitraum" ab, ohne dass ein
-- Konto mitgegeben werden muss. Der partielle Filter haelt den Index klein:
-- archivierte Buchungen sind aus jeder Auswertung ausgeschlossen.
create index if not exists transactions_household_date_idx
  on transactions (household_id, booked_date desc)
  where not is_archived;

-- Der Splitpfad: von der Kategorie zur Buchung. Ohne ihn wird jede
-- Kategorieauswertung zum Seq Scan ueber alle Splits des Haushalts.
create index if not exists transaction_splits_household_category_idx
  on transaction_splits (household_id, category_id);

-- ------------------------------------------------------------ Volltext --

-- Trigram statt tsvector: gesucht wird nach Teilzeichenketten wie "REWE" in
-- "EINKAUF REWE FILIALE 42" und nach Fragmenten von Empfaengernamen. Eine
-- Wortstamm-Volltextsuche traefe genau diese Faelle nicht.
create extension if not exists pg_trgm;

create index if not exists transactions_payee_label_trgm_idx
  on transactions using gin (payee_label gin_trgm_ops);

create index if not exists transactions_payee_raw_trgm_idx
  on transactions using gin (payee_raw gin_trgm_ops);

create index if not exists transactions_purpose_raw_trgm_idx
  on transactions using gin (purpose_raw gin_trgm_ops);

create index if not exists transactions_note_trgm_idx
  on transactions using gin (note gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Ehemals: 0010_category_suggestions.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- 0010: Steuerrelevanz, Standardpaket v2 und KI-Kategorievorschlaege
-- ============================================================================
-- Drei zusammenhaengende Ergaenzungen zu docs/KATEGORISIERUNGSPLAN.md:
--
-- 1. `is_tax_deductible` macht Steuerrelevanz zu einer getrennten Dimension
--    (Planentscheidung "Steuerrelevanz als getrennte Dimension statt als
--    normale Ausgabenkategorie"). Ein Software-Abo bleibt damit fachlich ein
--    Software-Abo, unabhaengig davon, ob es beruflich absetzbar ist.
-- 2. Standardpaket v2 schliesst die Luecke, die im Alltag am haeufigsten
--    auffaellt: beruflich genutzte Software und KI-Dienste, Arbeitsmittel,
--    berufliche Reisekosten, Ratenkaeufe und Nahrungsergaenzung hatten in v1
--    keine eigene Kategorie und landeten zwangslaeufig in "Streaming &
--    digitale Medien" oder "Elektronik & Technik".
-- 3. `category_operations.operation` bekommt `suggestion_apply`. Die
--    KI-Kategorievorschlaege nutzen damit exakt dieselbe Vorschau-/Anwenden-
--    Mechanik wie Merge und Paket-Apply (kurzlebige Vorschau, Fingerprint,
--    Idempotenz ueber request_id) - bewusst keine zweite Job-Infrastruktur.

-- ---------------------------------------------------------------------------
-- 1. Steuerrelevanz als eigene Dimension
-- ---------------------------------------------------------------------------

alter table categories
  add column if not exists is_tax_deductible boolean not null default false;

alter table category_templates
  add column if not exists is_tax_deductible boolean not null default false;

comment on column categories.is_tax_deductible is
  'Steuerlich absetzbar. Bewusst ein eigenes Merkmal statt einer eigenen '
  'Kategorie oder eines Budgetbereichs: dieselbe fachliche Kategorie kann in '
  'einem Haushalt absetzbar sein und in einem anderen nicht.';

-- bump_category_version() vergleicht generisch ueber to_jsonb(); die neue
-- Spalte wird deshalb ohne Anpassung des Triggers versioniert.

-- ---------------------------------------------------------------------------
-- 2. Installation traegt die Steuerrelevanz mit
-- ---------------------------------------------------------------------------
-- Unveraendert gegenueber Migration 0007 bis auf die zusaetzliche Spalte -
-- die Funktion wird als Ganzes ersetzt, weil PostgreSQL kein partielles
-- Aendern eines Funktionskoerpers kennt.

create or replace function install_category_taxonomy(
  p_household_id uuid,
  p_version int default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_version int;
  v_group record;
  v_category record;
  v_group_id uuid;
  v_created_groups int := 0;
  v_created_categories int := 0;
  v_skipped_conflicts int := 0;
  v_next_group_sort int;
  v_next_category_sort int;
begin
  v_version := coalesce(
    p_version,
    (select max(version) from category_taxonomy_versions where is_released)
  );
  if v_version is null then
    raise exception using errcode = '22023', message = 'NO_RELEASED_TAXONOMY_VERSION';
  end if;

  for v_group in
    select * from category_group_templates
    where taxonomy_version = v_version
    order by sort_order
  loop
    select id into v_group_id
    from category_groups
    where household_id = p_household_id and semantic_key = v_group.semantic_key;

    if v_group_id is null then
      select id into v_group_id
      from category_groups
      where household_id = p_household_id
        and not is_archived
        and category_normalize_name(name) = category_normalize_name(v_group.name);
    end if;

    if v_group_id is null then
      select coalesce(max(sort_order) + 1, 0) into v_next_group_sort
      from category_groups where household_id = p_household_id;

      insert into category_groups (
        household_id, name, sort_order, icon, color, semantic_key, origin, taxonomy_version
      )
      values (
        p_household_id, v_group.name,
        greatest(v_group.sort_order, v_next_group_sort),
        v_group.icon, v_group.color,
        v_group.semantic_key, 'standard', v_version
      )
      returning id into v_group_id;
      v_created_groups := v_created_groups + 1;
    end if;

    for v_category in
      select * from category_templates
      where taxonomy_version = v_version and group_semantic_key = v_group.semantic_key
      order by sort_order
    loop
      if exists (
        select 1 from categories
        where household_id = p_household_id and semantic_key = v_category.semantic_key
      ) then
        continue;
      end if;

      if exists (
        select 1 from categories
        where household_id = p_household_id
          and group_id = v_group_id
          and not is_archived
          and category_normalize_name(name) = category_normalize_name(v_category.name)
      ) then
        v_skipped_conflicts := v_skipped_conflicts + 1;
        continue;
      end if;

      select coalesce(max(sort_order) + 1, 0) into v_next_category_sort
      from categories where household_id = p_household_id and group_id = v_group_id;

      insert into categories (
        household_id, group_id, name, icon, color, is_budgeted, default_type,
        budget_bucket, sort_order, semantic_key, origin, taxonomy_version,
        description, synonyms, is_tax_deductible
      )
      values (
        p_household_id, v_group_id, v_category.name,
        coalesce(v_category.icon, v_group.icon), coalesce(v_category.color, v_group.color),
        v_category.is_budgeted, v_category.default_type, v_category.budget_bucket,
        greatest(v_category.sort_order, v_next_category_sort),
        v_category.semantic_key, 'standard', v_version,
        v_category.description, v_category.synonyms, v_category.is_tax_deductible
      );
      v_created_categories := v_created_categories + 1;
    end loop;
  end loop;

  insert into household_category_taxonomy (household_id, taxonomy_version, applied_at, applied_by)
  values (p_household_id, v_version, now(), auth.uid())
  on conflict (household_id) do update
    set taxonomy_version = excluded.taxonomy_version,
        applied_at = excluded.applied_at,
        applied_by = excluded.applied_by,
        revision = household_category_taxonomy.revision + 1,
        updated_at = now();

  return jsonb_build_object(
    'taxonomy_version', v_version,
    'created_groups', v_created_groups,
    'created_categories', v_created_categories,
    'skipped_name_conflicts', v_skipped_conflicts
  );
end $$;

-- ---------------------------------------------------------------------------
-- 3. Standardpaket v2
-- ---------------------------------------------------------------------------
-- v2 muss VOLLSTAENDIG sein: install_category_taxonomy() installiert genau
-- eine Version, ein Haushalt bekaeme sonst nur die sieben Neuzugaenge. Die
-- 50 Eintraege aus v1 werden deshalb kopiert statt erneut ausgeschrieben -
-- so kann v1 nicht versehentlich von v2 abweichen.

insert into category_taxonomy_versions (version, locale, label, is_released, released_at)
values (2, 'de-DE', 'Deutsches Standardpaket v2', true, now())
on conflict (version) do update
  set label = excluded.label,
      is_released = excluded.is_released;

insert into category_group_templates (taxonomy_version, semantic_key, name, sort_order, icon, color)
select 2, semantic_key, name, sort_order, icon, color
from category_group_templates
where taxonomy_version = 1
on conflict (taxonomy_version, semantic_key) do update
  set name = excluded.name,
      sort_order = excluded.sort_order,
      icon = excluded.icon,
      color = excluded.color;

-- Neue Gruppe zwischen Freizeit und Finanzen; die beiden nachfolgenden
-- Gruppen ruecken in v2 eine Position weiter.
insert into category_group_templates (taxonomy_version, semantic_key, name, sort_order, icon, color)
values (2, 'work', 'Beruf & Arbeitsmittel', 7, '💼', '#3E4A59')
on conflict (taxonomy_version, semantic_key) do update
  set name = excluded.name,
      sort_order = excluded.sort_order,
      icon = excluded.icon,
      color = excluded.color;

update category_group_templates set sort_order = 8
  where taxonomy_version = 2 and semantic_key = 'finance';
update category_group_templates set sort_order = 9
  where taxonomy_version = 2 and semantic_key = 'transfers';

insert into category_templates (
  taxonomy_version, group_semantic_key, semantic_key, name,
  default_type, budget_bucket, is_budgeted, sort_order, icon, color,
  description, synonyms, is_tax_deductible
)
select 2, group_semantic_key, semantic_key, name,
       default_type, budget_bucket, is_budgeted, sort_order, icon, color,
       description, synonyms, is_tax_deductible
from category_templates
where taxonomy_version = 1
on conflict (taxonomy_version, semantic_key) do update
  set group_semantic_key = excluded.group_semantic_key,
      name = excluded.name,
      default_type = excluded.default_type,
      budget_bucket = excluded.budget_bucket,
      is_budgeted = excluded.is_budgeted,
      sort_order = excluded.sort_order,
      icon = excluded.icon,
      synonyms = excluded.synonyms,
      is_tax_deductible = excluded.is_tax_deductible;

-- Der Kategoriename beschreibt den Zweck, die Beschreibung die typischen
-- Einkaufsorte. So steht unter "Lebensmittel & Gastronomie" nicht noch einmal
-- eine konkurrierende Obergruppe, sondern der klare Einzelposten
-- "Lebensmittel" fuer Supermaerkte und Discounter.
update category_templates
set name = 'Lebensmittel',
    description = 'Lebensmitteleinkäufe in Supermärkten, Discountern und Lebensmittelgeschäften, zum Beispiel Aldi, Lidl, Rewe, Edeka, Penny, Netto oder Kaufland.'
where semantic_key = 'groceries';

insert into category_templates (
  taxonomy_version, group_semantic_key, semantic_key, name,
  default_type, budget_bucket, is_budgeted, sort_order, icon, synonyms, is_tax_deductible
) values
  -- Beruf & Arbeitsmittel: in v1 gab es dafuer nichts, weshalb berufliche
  -- Software zwangslaeufig unter "Streaming & digitale Medien" landete.
  (2, 'work', 'work_software', 'Software, KI-Dienste & Lizenzen', 'expense', 'needs', true, 0, '💻',
   array['software', 'lizenz', 'saas', 'abo software', 'cloud', 'speicherplatz',
         'chatgpt', 'openai', 'claude', 'anthropic', 'mistral', 'github', 'jetbrains',
         'adobe', 'microsoft 365', 'office 365', 'google one', 'bitwarden', 'api guthaben'], true),
  (2, 'work', 'work_equipment', 'Arbeitsmittel & Fachliteratur', 'expense', 'needs', true, 1, '🧰',
   array['arbeitsmittel', 'fachliteratur', 'fachbuch', 'buerobedarf', 'buerostuhl',
         'werkzeug beruflich', 'notebook beruflich', 'drucker'], true),
  (2, 'work', 'work_travel', 'Berufliche Reisekosten', 'expense', 'needs', true, 2, '🧳',
   array['reisekosten', 'dienstreise', 'spesen', 'verpflegungsmehraufwand',
         'uebernachtung dienstlich', 'bahncard beruflich'], true),
  (2, 'work', 'work_fees', 'Berufliche Beiträge & Versicherungen', 'expense', 'needs', true, 3, '🧾',
   array['kammerbeitrag', 'berufsverband', 'gewerkschaft', 'berufshaftpflicht',
         'ihk', 'gewerbeanmeldung', 'fortbildungsbeitrag'], true),

  -- Freizeit: Buecher und Presse waren nur ueber "Streaming & digitale
  -- Medien" bzw. "Hobby" erreichbar, obwohl es ein eigener Posten ist.
  (2, 'lifestyle', 'books_press', 'Bücher, Zeitungen & Hörbücher', 'expense', 'wants', true, 8, '📚',
   array['buch', 'buecher', 'thalia', 'hugendubel', 'zeitung', 'zeitschrift',
         'abo zeitung', 'hoerbuch', 'audible', 'bookbeat'], false),

  -- Gesundheit: Nahrungsergaenzung ist keine Apotheken-Zuzahlung und kein
  -- Arztbesuch, verzerrt dort aber jede Auswertung.
  (2, 'health', 'supplements', 'Nahrungsergänzung & Prävention', 'expense', 'wants', true, 7, '🌿',
   array['nahrungsergaenzung', 'supplement', 'vitamine', 'mineralstoffe',
         'praevention', 'sportnahrung', 'protein'], false),

  -- Finanzen: eine laufende Ratenzahlung ist keine Ausgabe der urspruenglichen
  -- Konsumkategorie und auch kein Kreditzins.
  (2, 'finance', 'installments', 'Ratenkäufe & Finanzierung', 'expense', 'needs', true, 4, '💳',
   array['ratenkauf', 'rate', 'teilzahlung', 'klarna', 'finanzierung',
         'ratenzahlung', 'null prozent finanzierung'], false)
on conflict (taxonomy_version, semantic_key) do update
  set group_semantic_key = excluded.group_semantic_key,
      name = excluded.name,
      default_type = excluded.default_type,
      budget_bucket = excluded.budget_bucket,
      is_budgeted = excluded.is_budgeted,
      sort_order = excluded.sort_order,
      icon = excluded.icon,
      synonyms = excluded.synonyms,
      is_tax_deductible = excluded.is_tax_deductible;

-- Dieselbe Zusicherung wie fuer v1: eine unvollstaendig eingespielte Version
-- soll die Migration abbrechen und nicht still ein halbes Paket ausliefern.
do $$
declare
  v_groups int;
  v_categories int;
begin
  select count(*) into v_groups from category_group_templates where taxonomy_version = 2;
  select count(*) into v_categories from category_templates where taxonomy_version = 2;
  if v_groups <> 10 or v_categories <> 57 then
    raise exception using
      errcode = '23514',
      message = format('TAXONOMY_V2_INCOMPLETE: %s Gruppen, %s Kategorien', v_groups, v_categories);
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 4. Vorgangsart fuer KI-Kategorievorschlaege
-- ---------------------------------------------------------------------------

alter table category_operations
  drop constraint if exists category_operations_operation_check;

alter table category_operations
  add constraint category_operations_operation_check check (operation in (
    'move', 'archive', 'restore', 'merge',
    'group_archive', 'group_merge', 'taxonomy_apply', 'suggestion_apply'
  ));

-- Eigenes Verb im Protokoll: ein angenommener KI-Vorschlag legt Kategorien an
-- UND ordnet Buchungen zu. Als 'create' waere im Log nicht erkennbar, dass die
-- Anregung vom Modell kam und welche Buchungen daran haengen.
alter table audit_log drop constraint if exists audit_log_action_check;
alter table audit_log add constraint audit_log_action_check
  check (action in (
    'create', 'update', 'delete',
    'archive', 'restore', 'move', 'reorder', 'merge',
    'taxonomy_apply', 'suggestion_apply'
  ));

-- ---------------------------------------------------------------------------
-- 5. Index fuer die Suche nach noch nicht zugeordneten Buchungen
-- ---------------------------------------------------------------------------
-- Der Vorschlagsdienst liest genau diese Menge: Splits ohne Kategorie im
-- Haushalt. Ohne den partiellen Index waere das ein Full Scan ueber alle
-- Splits, obwohl der interessante Teil im Regelfall klein ist.

create index if not exists transaction_splits_uncategorized_idx
  on transaction_splits (household_id, transaction_id)
  where category_id is null;

-- ---------------------------------------------------------------------------
-- Ehemals: 0011_ai_model_settings.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- 0011: Modellwahl und Token-Budget als Haushaltseinstellung
-- ============================================================================
-- Bis hierher hat die Anwendung das Modell NICHT gewaehlt, sondern schlicht
-- den ersten Eintrag aus GET /v1/models genommen. Das hatte drei Folgen:
--
--   1. Ein Einbettungsmodell (type = "embeddings") kann an erster Stelle
--      stehen. Es beherrscht keine Chat-Completions - die Klassifikation
--      waere reproduzierbar fehlgeschlagen, ohne dass irgendwo stuende warum.
--   2. Ob das gewaehlte Modell ueberhaupt geladen ist, blieb unbeachtet. LM
--      Studio laedt es dann beim ersten Aufruf nach, was das Zeitbudget
--      sprengen kann.
--   3. Wer zwei Modelle vorhaelt, konnte nicht bestimmen, welches der
--      Finanzplaner benutzt.
--
-- `custom_base_url` existiert seit Migration 0001, wurde aber von keinem
-- Codepfad gelesen. Ab hier ist sie wirksam.
--
-- Zum Kontextfenster: Es wird NICHT hier eingestellt, sondern beim Laden des
-- Modells in LM Studio (`loaded_context_length`). Die Anwendung kann es nur
-- auslesen. Steuerbar ist von hier aus das Ausgabebudget
-- (`max_output_tokens`), und genau das ist der Punkt: Prompt und Ausgabe
-- teilen sich dasselbe Fenster. Ein zu grosses Ausgabebudget laesst zu wenig
-- Platz fuer die Kategorienliste im Prompt - deshalb rechnet die API beides
-- gegeneinander und warnt, statt es den Nutzer im Fehlerfall raten zu lassen.

alter table ai_household_settings
  add column if not exists model_name text
    check (model_name is null or length(btrim(model_name)) between 1 and 200);

-- Das feste Ausgabebudget (frueher `max_output_tokens`, Rueckfall
-- AI_MAX_TOKENS=4000) gibt es nicht mehr: das Antwortbudget jedes Aufrufs
-- ergibt sich aus dem geladenen Kontextfenster und dem Reserveanteil (siehe
-- Abschnitt "Relatives KI-Budget" weiter unten). Die alte Spalte wird nur
-- umbenannt, damit die Einstellungen einmal darauf hinweisen koennen, dass ein
-- frueher eingetragener Wert nicht mehr gilt; danach entfaellt sie.
-- Umbenennen ist nur einmal moeglich - der DO-Block prueft deshalb beide Namen
-- und laeuft beim zweiten Anwenden der Datei ins Leere.
-- schema_sync: vorstufe Umbenennen erhaelt den alten Wert (migra saehe drop + add)
do $$
begin
  if exists (
       select 1 from information_schema.columns
       where table_schema = 'public' and table_name = 'ai_household_settings'
         and column_name = 'max_output_tokens')
     and not exists (
       select 1 from information_schema.columns
       where table_schema = 'public' and table_name = 'ai_household_settings'
         and column_name = 'legacy_max_output_tokens') then
    alter table ai_household_settings
      rename column max_output_tokens to legacy_max_output_tokens;
  end if;
end $$;
-- schema_sync: ende

-- Frisch aufgesetzte Datenbanken bekommen die Spalte leer, damit der Code
-- nicht zwischen zwei Schemastaenden unterscheiden muss.
alter table ai_household_settings
  add column if not exists legacy_max_output_tokens int;

alter table ai_household_settings
  add column if not exists temperature numeric(3,2)
    check (temperature is null or temperature between 0.00 and 2.00);

alter table ai_household_settings
  add column if not exists request_timeout_seconds int
    check (request_timeout_seconds is null or request_timeout_seconds between 5 and 900);

-- Zeitbudget je Chat-Completion: die Vorgabe steht seit dem relativen Budget
-- hier im Schema statt als Rueckfall auf AI_REQUEST_TIMEOUT_SECONDS. Bestehende
-- leere Zellen bekommen den bisherigen Umgebungswert (120 s).
-- schema_sync: vorstufe leere Zellen fuellen, bevor "set not null" kommt
do $$
begin
  if exists (
       select 1 from information_schema.columns
       where table_schema = 'public' and table_name = 'ai_household_settings'
         and column_name = 'request_timeout_seconds') then
    update ai_household_settings set request_timeout_seconds = 120
      where request_timeout_seconds is null;
  end if;
end $$;
-- schema_sync: ende
alter table ai_household_settings alter column request_timeout_seconds set default 120;
alter table ai_household_settings alter column request_timeout_seconds set not null;

comment on column ai_household_settings.model_name is
  'Ausdruecklich gewaehltes Chat-Modell. NULL bedeutet nicht "keins", sondern '
  '"automatisch": das erste geladene chatfaehige Modell. Ein hier '
  'eingetragenes, aber nicht mehr vorhandenes Modell wird nicht still '
  'ersetzt - die Statusanzeige benennt es als fehlend.';

comment on column ai_household_settings.legacy_max_output_tokens is
  'Frueheres festes Ausgabebudget (max_output_tokens). Wird nicht mehr '
  'verwendet - das Antwortbudget ergibt sich aus dem geladenen Fenster und '
  'answer_reserve_share. Steht nur noch fuer den einmaligen Hinweis in den '
  'Einstellungen hier und entfaellt danach.';

comment on column ai_household_settings.temperature is
  'Manuelle Sampling-Temperatur. Nur bei sampling_mode="manual" wirksam; '
  'NULL wird in der Oberflaeche mit einem neutralen Startwert dargestellt.';

comment on column ai_household_settings.request_timeout_seconds is
  'Zeitbudget je Chat-Completion in Sekunden. Die Vorgabe steht als '
  'Spaltenvorgabe im Schema; ein Umgebungsrueckfall existiert nicht mehr.';

comment on column ai_household_settings.custom_base_url is
  'Abweichende LM-Studio-Basis-URL dieses Haushalts. Seit Migration 0011 '
  'wirksam: sie wird vor den Kandidaten aus der Umgebung probiert.';

-- Jeder Haushalt hat genau eine Einstellungszeile: der Trigger im Abschnitt
-- "Relatives KI-Budget" legt sie beim Anlegen des Haushalts an, ein Backfill
-- dort ergaenzt den Bestand. Die Vorgaben stehen damit ausschliesslich als
-- Spaltenvorgaben hier im Schema - der Code kennt keine eigenen.

-- ---------------------------------------------------------------------------
-- Ehemals: 0012_ai_runtime_control.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- 0012: Kontextfenster und Reasoning als Haushaltseinstellung
-- ============================================================================
-- Migration 0011 hielt fest: "Das Kontextfenster wird NICHT hier eingestellt,
-- sondern beim Laden des Modells in LM Studio. Die Anwendung kann es nur
-- auslesen." Das stimmt fuer die OpenAI-kompatible API und fuer LM Studios
-- `/api/v0`. Es stimmt nicht mehr: `/api/v1` bietet
--
--   POST /api/v1/models/load    {"model": "<key>", "context_length": <int>}
--   POST /api/v1/models/unload  {"instance_id": "<id>"}
--   GET  /api/v1/models         -> loaded_instances[].config.context_length,
--                                  capabilities.reasoning.allowed_options
--
-- Damit kann der Finanzplaner beides selbst stellen, statt den Nutzer in die
-- LM-Studio-Oberflaeche zu schicken.
--
-- Warum das zwei getrennte Spalten braucht und nicht eine reicht (gemessen am
-- 03.08.2026 gegen google/gemma-4-26b-a4b-qat, Prompt mit 50 Kategorien):
--
--   Fenster 8192, Reasoning an   -> HTTP 400 "Context size has been exceeded"
--   Fenster 32768, Reasoning an  -> finish_reason "length", 4000 Tokens
--                                   verbraucht, content LEER
--   Fenster 32768, Reasoning aus -> finish_reason "stop", 39 Tokens, gueltiges
--                                   JSON
--
-- Das Fenster allein loest es also nicht: ein Reasoning-Modell denkt so lange
-- laut, bis das AUSGABEbudget aufgebraucht ist, und gibt dann nichts mehr aus.
-- Umgekehrt reicht Reasoning-aus allein nicht, wenn das Fenster so klein ist,
-- dass schon Prompt plus Ausgabebudget nicht hineinpassen. Beide Regler
-- gehoeren dem Nutzer.

alter table ai_household_settings
  add column if not exists context_length int
    check (context_length is null or context_length between 2048 and 1048576);

-- Bewusst Text mit CHECK statt boolean: LM Studio meldet je Modell
-- capabilities.reasoning.allowed_options (aktuell "off"/"on"), und ein
-- boolean-Feld haette keinen Platz fuer ein spaeteres "low"/"high".
alter table ai_household_settings
  add column if not exists reasoning text
    check (reasoning is null or reasoning in ('on', 'off'));

alter table ai_household_settings
  drop constraint if exists ai_household_settings_reasoning_check;
alter table ai_household_settings
  add constraint ai_household_settings_reasoning_check
  check (reasoning is null or reasoning in (
    'on', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'
  ));

comment on column ai_household_settings.reasoning is
  'NULL = Modellstandard. "off" sendet reasoning_effort="none"; "on" wird '
  'fuer den OpenAI-kompatiblen Endpunkt auf reasoning_effort="high" abgebildet '
  'und aktiviert bei booleschen Modellen deren On-Modus. Effort-Stufen werden '
  'unveraendert gesendet und nur angeboten, wenn das Modell sie meldet. Bei '
  'Anthropic: "off" = thinking disabled, "on" = adaptive, Stufen = effort '
  '(low bis max) mit adaptivem Denken.';

comment on column ai_household_settings.context_length is
  'Kontextfenster, mit dem das Modell in LM Studio geladen werden soll '
  '(POST /api/v1/models/load). NULL bedeutet "so lassen, wie es geladen ist" - '
  'die Anwendung laedt dann nichts um. Der Wert ist ein Wunsch, kein '
  'Zustand: was tatsaechlich geladen ist, steht in loaded_instances und wird '
  'von der Statusanzeige daneben gestellt.';

comment on column ai_household_settings.reasoning is
  '"off" schaltet das laute Denken ab (reasoning_effort "none" im '
  'Chat-Aufruf), "on" aktiviert es ausdruecklich. NULL = Modellstandard.';

-- Sampling wird entweder aus einem sichtbaren, herstellernahen Modellprofil
-- abgeleitet oder vollstaendig manuell gesendet. NULL beim Modus bedeutet den
-- automatischen Modellmodus; so bleiben bestehende Haushalte kompatibel.
alter table ai_household_settings
  add column if not exists sampling_mode text
    check (sampling_mode is null or sampling_mode in ('model', 'manual')),
  add column if not exists top_p numeric(4,3)
    check (top_p is null or (top_p > 0.000 and top_p <= 1.000)),
  add column if not exists top_k int
    check (top_k is null or top_k between 0 and 1000),
  add column if not exists repeat_penalty numeric(4,2)
    check (repeat_penalty is null or repeat_penalty between 0.00 and 2.00),
  add column if not exists min_p numeric(4,3)
    check (min_p is null or min_p between 0.000 and 1.000),
  add column if not exists presence_penalty numeric(4,2)
    check (presence_penalty is null or presence_penalty between -2.00 and 2.00);

comment on column ai_household_settings.sampling_mode is
  'NULL/model = sichtbares Herstellerprofil oder unveraenderter Serverstandard; '
  'manual = alle sechs Sampling-Werte exakt senden.';
comment on column ai_household_settings.top_p is
  'Manueller nucleus-sampling Anteil; nur bei sampling_mode=manual wirksam.';
comment on column ai_household_settings.top_k is
  'Manuelle Zahl der je Schritt betrachteten Token; 0 deaktiviert den Filter.';
comment on column ai_household_settings.repeat_penalty is
  'Manuelle Wiederholungsstrafe; 1.0 ist neutral, Werte ueber 1 hemmen Schleifen.';
comment on column ai_household_settings.min_p is
  'Manueller relativer Wahrscheinlichkeitsfilter; 0 deaktiviert ihn.';
comment on column ai_household_settings.presence_penalty is
  'Manuelle Presence-Strafe; positive Werte bevorzugen bisher ungenutzte Token.';

-- ============================================================================
-- Relatives KI-Budget: Fenster, Antwortreserve, Startwerte der Messung
-- ============================================================================
-- docs/KI-BUDGET-UND-KI-FELD-PLAN.md, Etappe 1: "Kein festes Budget mehr,
-- sondern Budget anhand eines relativen Satzes." Jeder Modellaufruf bekommt
-- sein Antwortbudget aus dem tatsaechlich GELADENEN Kontextfenster:
--
--   W = min(geladenes Fenster, context_cap_tokens)
--   R = W * answer_reserve_share          (Platz fuer Antwort und Denken)
--   max_tokens = W - E - A                (E Eingabe, A Schaetzabstand)
--   gesendet wird nur, wenn E + A <= W - R
--
-- Die Vorgaben stehen AUSSCHLIESSLICH hier als Spaltenvorgaben. Die API
-- liest sie aus information_schema.columns und zeigt sie in der Oberflaeche
-- an ("Vorgabe: 25 %"); Zuruecksetzen heisst "auf die Spaltenvorgabe". Im Code
-- steht keine eigene Zahl, auch nicht als Rueckfall.
--
--   context_cap_tokens      "Kontext hoechstens": NULL = das geladene Fenster
--                           gilt allein. Getrennt von context_length (Fenster
--                           beim Laden in LM Studio) - verschiedene Dinge (E7).
--   answer_reserve_share    Anteil des Fensters, der fuer Antwort und Denken
--                           frei bleiben muss.
--   result_page_share       Anteil des Fensters fuer EINE Seite eines
--                           Werkzeugergebnisses (KI-Feld, Etappe 2).
--   run_time_limit_minutes  Laufzeit eines mehrstufigen KI-Laufs.
--   start_chars_per_token   Zeichen je Token, bis die erste Messung eines
--                           Modells vorliegt (ai_model_measurements).
--   start_estimate_error    Schaetzabstand bis zur ersten Messung (E3).
--   context_line_enabled    Kontextzeile "[Kontext: E von W Token belegt ...]"
--                           im KI-Feld.
alter table ai_household_settings
  add column if not exists context_cap_tokens int,
  add column if not exists answer_reserve_share numeric(4,3) not null default 0.250,
  add column if not exists result_page_share numeric(4,3) not null default 0.100,
  add column if not exists run_time_limit_minutes int not null default 20,
  add column if not exists start_chars_per_token numeric(4,2) not null default 3.00,
  add column if not exists start_estimate_error numeric(4,3) not null default 0.150,
  add column if not exists context_line_enabled boolean not null default true;

-- Bereiche als benannte CHECKs (drop/add), damit ein erneutes Anwenden sie
-- angleicht statt zu scheitern. Die Grenzen sind Plausibilitaet, kein Budget:
-- ein Reserveanteil von 0 oder 1 liesse keine Rechnung mehr zu.
alter table ai_household_settings
  drop constraint if exists ai_household_settings_context_cap_tokens_check;
alter table ai_household_settings
  add constraint ai_household_settings_context_cap_tokens_check
  check (context_cap_tokens is null or context_cap_tokens between 2048 and 1048576);
alter table ai_household_settings
  drop constraint if exists ai_household_settings_answer_reserve_share_check;
alter table ai_household_settings
  add constraint ai_household_settings_answer_reserve_share_check
  check (answer_reserve_share between 0.050 and 0.900);
alter table ai_household_settings
  drop constraint if exists ai_household_settings_result_page_share_check;
alter table ai_household_settings
  add constraint ai_household_settings_result_page_share_check
  check (result_page_share between 0.010 and 0.500);
alter table ai_household_settings
  drop constraint if exists ai_household_settings_run_time_limit_minutes_check;
alter table ai_household_settings
  add constraint ai_household_settings_run_time_limit_minutes_check
  check (run_time_limit_minutes between 1 and 240);
alter table ai_household_settings
  drop constraint if exists ai_household_settings_start_chars_per_token_check;
alter table ai_household_settings
  add constraint ai_household_settings_start_chars_per_token_check
  check (start_chars_per_token between 1.00 and 8.00);
alter table ai_household_settings
  drop constraint if exists ai_household_settings_start_estimate_error_check;
alter table ai_household_settings
  add constraint ai_household_settings_start_estimate_error_check
  check (start_estimate_error between 0.000 and 1.000);

comment on column ai_household_settings.context_cap_tokens is
  '"Kontext hoechstens": W = min(geladenes Fenster, dieser Wert). NULL = das '
  'geladene Fenster gilt allein. Meldet der Server kein Fenster, ist dieser '
  'Wert das einzige - ohne beide findet kein Modellaufruf statt.';
comment on column ai_household_settings.answer_reserve_share is
  'Antwortreserve als Anteil des wirksamen Fensters (R = W * Anteil).';
comment on column ai_household_settings.result_page_share is
  'Groesse einer Ergebnisseite als Anteil des Fensters (KI-Feld).';
comment on column ai_household_settings.run_time_limit_minutes is
  'Laufzeit eines mehrstufigen KI-Laufs in Minuten (KI-Feld).';
comment on column ai_household_settings.start_chars_per_token is
  'Zeichen je Token, bis fuer das Modell eine Messung vorliegt.';
comment on column ai_household_settings.start_estimate_error is
  'Schaetzabstand (Anteil), bis fuer das Modell eine Messung vorliegt.';
comment on column ai_household_settings.context_line_enabled is
  'Kontextzeile mit Fensterbelegung im KI-Feld anzeigen und mitsenden.';

-- Genau eine Einstellungszeile je Haushalt. Der Trigger laeuft als
-- security definer: Haushalte entstehen in create_household() bzw. direkt
-- durch den Worker, und die Zeile muss unabhaengig von RLS-Kontext und
-- Mitgliedschaft im selben Moment existieren. Der Backfill ergaenzt den
-- Bestand; on conflict macht beides wiederholbar.
create or replace function ensure_ai_household_settings()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into ai_household_settings (household_id)
  values (new.id)
  on conflict (household_id) do nothing;
  return new;
end;
$$;

drop trigger if exists households_ai_settings_row on households;
create trigger households_ai_settings_row
  after insert on households
  for each row execute function ensure_ai_household_settings();

insert into ai_household_settings (household_id)
select h.id from households h
on conflict (household_id) do nothing;

-- ---------------------------------------------------------------------------
-- Ehemals: 0013_rule_backfill.sql
-- ---------------------------------------------------------------------------
-- ---------------------------------------------------------------------------
-- Rueckwirkende Regelanwendung
-- ---------------------------------------------------------------------------
-- Klassifikationsregeln griffen bis hierher ausschliesslich beim Import
-- (import_service.commit_single_row). Eine neu angelegte Regel liess den
-- vorhandenen Bestand unberuehrt - wer eine Fehlkategorisierung ueber Monate
-- hinweg korrigieren wollte, musste jede Buchung von Hand anfassen.
--
-- Der neue Endpunkt POST /v1/rules/{id}/apply schreibt Kategorie und
-- Transferkennzeichen auf bereits gebuchte Transaktionen. Er braucht ein
-- eigenes Verb im Protokoll: als 'update' auf der Regel waere im Log nicht
-- erkennbar, dass ein Lauf Buchungen veraendert hat und wie viele.
alter table audit_log drop constraint if exists audit_log_action_check;
alter table audit_log add constraint audit_log_action_check
  check (action in (
    'create', 'update', 'delete',
    'archive', 'restore', 'move', 'reorder', 'merge',
    'taxonomy_apply', 'suggestion_apply', 'rule_apply'
  ));

-- Kein zusaetzlicher Index: der Mustervergleich laeuft in `evaluate_rule` in
-- Python, damit Import und rueckwirkender Lauf nachweislich dieselbe Bedingung
-- auswerten. Die Kandidatenabfrage filtert nur ueber Haushalt, Archivstatus
-- und Buchungsdatum - dafuer traegt transactions bereits Indizes.

-- ---------------------------------------------------------------------------
-- Ehemals: 0014_contracts.sql
-- ---------------------------------------------------------------------------
-- ============================================================================
-- 0014: Vertragsobjekt (Phase 1 aus docs/VERTRAEGE-FIXKOSTEN-PLAN.md)
-- ============================================================================
-- Bisher gab es zwei unverbundene Toepfe fuer wiederkehrende Zahlungen:
-- recurring_rules (0001) und subscriptions (0001). Beide kannten nur GET und
-- POST, hatten kein Namensfeld (der Anbieter stand im Freitext "note"), kein
-- normiertes Vorzeichen und keinerlei Verbindung zum Buchungsstrom. Dadurch
-- konnte derselbe Vertrag doppelt gezaehlt werden, eine Preisaenderung erzeugte
-- einen zweiten Eintrag, und ein beendeter Vertrag war von einem nie
-- fortgeschriebenen nicht unterscheidbar.
--
-- Diese Migration legt das Vertragsobjekt an:
--   contracts               - der Vertrag selbst
--   contract_amount_history - Betragsverlauf (z. B. 150 EUR -> 120 EUR)
--   contract_occurrences    - je erwartete Faelligkeit ihre Erfuellung
--   contract_events         - abgeleitete Fakten (Preisaenderung, Zahlung fehlt)
--   contract_settings       - Haushaltseinstellung fuer das automatische Beenden
--
-- Zwei Modellentscheidungen sind hier tragend:
--   1. amount_minor ist IMMER positiv, die Richtung steht getrennt in
--      "direction". Die heutige Vorzeichenmischung (erkannte Regeln negativ,
--      manuelle positiv) hat Summen und Einfaerbung widerspruechlich gemacht.
--   2. contract_occurrences hat einen partiellen Unique-Index auf
--      transaction_id. Eine Buchung kann dadurch strukturell hoechstens einen
--      Vertrag erfuellen - Doppelzaehlung ist nicht mehr nur logisch, sondern
--      per Constraint ausgeschlossen.
--
-- Die Bestaende aus recurring_rules und subscriptions werden am Ende der Datei
-- uebernommen. Die Quelltabellen bleiben vorerst bestehen (Kompatibilitaet fuer
-- ausgelieferte Mobile-Versionen) und werden nicht mehr geschrieben.
--
-- Additiv und idempotent.

-- ---------- 1. contracts ----------------------------------------------------

create table if not exists contracts (
  id                       uuid primary key default gen_random_uuid(),
  household_id             uuid not null references households(id) on delete cascade
                             default current_household_id(),

  -- Anzeige
  name                     text not null check (length(btrim(name)) between 1 and 160),
  provider_label           text check (provider_label is null
                             or length(btrim(provider_label)) between 1 and 160),
  contract_type            text not null default 'other' check (contract_type in (
                             'subscription', 'insurance', 'rent', 'loan', 'installment',
                             'utility', 'telecom', 'membership', 'donation', 'savings',
                             'income', 'other')),

  -- Identitaet
  payee_id                 uuid references payees(id) on delete set null,
  account_id               uuid references accounts(id) on delete set null,
  category_id              uuid references categories(id) on delete set null,
  contract_key             text,
  contract_key_source      text check (contract_key_source is null
                             or contract_key_source in ('mandate', 'purpose_pattern', 'manual')),

  -- Geld
  direction                text not null default 'expense'
                             check (direction in ('expense', 'income')),
  amount_minor             bigint not null check (amount_minor > 0),
  frequency                text not null default 'monthly' check (frequency in (
                             'weekly', 'monthly', 'quarterly', 'semiannual', 'yearly')),
  reserve_enabled          boolean not null default false,
  reserve_started_on       date,
  reserve_starting_amount_minor bigint not null default 0
                             check (reserve_starting_amount_minor >= 0),

  -- Zeit
  due_rule                 text not null default 'day_of_month'
                             check (due_rule in ('day_of_month', 'month_end', 'nth_business_day')),
  due_day                  int check (due_day is null or due_day between 1 and 31),
  next_due_date            date not null,
  first_seen_date          date,
  last_seen_date           date,
  tolerance_days           int not null default 4 check (tolerance_days between 0 and 31),
  tolerance_amount_minor   bigint not null default 0 check (tolerance_amount_minor >= 0),

  -- Lebenszyklus
  status                   text not null default 'active' check (status in (
                             'active', 'paused', 'cancelled', 'ended', 'not_a_contract')),
  cancelled_on             date,
  ends_on                  date,
  min_term_months          int check (min_term_months is null
                             or min_term_months between 1 and 600),
  cancellation_period_days int check (cancellation_period_days is null
                             or cancellation_period_days between 0 and 1095),

  -- Ratenkauf
  installments_total       int check (installments_total is null
                             or installments_total between 2 and 240),
  installments_paid        int not null default 0 check (installments_paid >= 0),

  -- Vertrauen und Abgleichszustand
  source                   text not null default 'detected'
                             check (source in ('detected', 'manual', 'confirmed')),
  detection_confidence     numeric(3, 2) check (detection_confidence is null
                             or detection_confidence between 0 and 1),
  missed_periods           int not null default 0 check (missed_periods >= 0),
  last_matched_at          timestamptz,
  notified_at              timestamptz,

  -- Herkunft aus den Alttabellen; macht die Uebernahme wiederholbar und
  -- erlaubt der Kompatibilitaetsschicht, alte IDs weiter zu bedienen.
  legacy_recurring_rule_id uuid,
  legacy_subscription_id   uuid,

  note                     text,
  version                  int not null default 1,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create index if not exists contracts_household_status_idx
  on contracts (household_id, status, next_due_date);
create index if not exists contracts_household_due_idx
  on contracts (household_id, next_due_date)
  where status in ('active', 'cancelled');
create index if not exists contracts_payee_idx
  on contracts (household_id, payee_id) where payee_id is not null;
create index if not exists contracts_key_idx
  on contracts (household_id, contract_key) where contract_key is not null;
create unique index if not exists contracts_legacy_recurring_uniq
  on contracts (legacy_recurring_rule_id) where legacy_recurring_rule_id is not null;
create unique index if not exists contracts_legacy_subscription_uniq
  on contracts (legacy_subscription_id) where legacy_subscription_id is not null;

-- Optimistische Nebenlaeufigkeit wie bei Kategorien und Klassifikationsregeln.
-- notified_at und last_matched_at sind ausgenommen: ein Maschinenlauf darf das
-- If-Match des Nutzers nicht entwerten.
create or replace function bump_contract_version()
returns trigger language plpgsql as $$
begin
  if (to_jsonb(new) - 'version' - 'updated_at' - 'notified_at' - 'last_matched_at')
     is distinct from (to_jsonb(old) - 'version' - 'updated_at' - 'notified_at' - 'last_matched_at') then
    new.version := old.version + 1;
    new.updated_at := now();
  else
    new.version := old.version;
    new.updated_at := old.updated_at;
  end if;
  return new;
end;
$$;

drop trigger if exists contracts_bump_version on contracts;
create trigger contracts_bump_version before update on contracts
  for each row execute function bump_contract_version();

-- Kategorie-Invariante aus 0007: jede Kategorie-Referenz ist ein
-- zusammengesetzter Fremdschluessel, weil RLS eine bekannte Fremd-UUID nicht
-- verhindert. Die do-Bloecke in 0007 sind statische Listen und greifen fuer
-- neue Tabellen nicht, deshalb hier ausdruecklich.
alter table contracts drop constraint if exists contracts_category_id_fkey;
alter table contracts drop constraint if exists contracts_category_household_fkey;
alter table contracts add constraint contracts_category_household_fkey
  foreign key (category_id, household_id)
  references categories (id, household_id)
  on update cascade
  deferrable initially deferred;

-- ---------- 2. contract_amount_history --------------------------------------

create table if not exists contract_amount_history (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  contract_id           uuid not null references contracts(id) on delete cascade,
  amount_minor          bigint not null check (amount_minor > 0),
  valid_from            date not null,
  valid_to              date,
  delta_minor           bigint,
  change_reason         text not null default 'detected_change' check (change_reason in (
                          'initial', 'detected_change', 'manual', 'backfill')),
  source_transaction_id uuid references transactions(id) on delete set null,
  created_at            timestamptz not null default now(),
  check (valid_to is null or valid_to >= valid_from)
);

create index if not exists contract_amount_history_contract_idx
  on contract_amount_history (household_id, contract_id, valid_from desc);
-- Genau eine offene Preisstufe je Vertrag.
create unique index if not exists contract_amount_history_open_uniq
  on contract_amount_history (contract_id) where valid_to is null;

-- ---------- 3. contract_occurrences -----------------------------------------

create table if not exists contract_occurrences (
  id                 uuid primary key default gen_random_uuid(),
  household_id       uuid not null references households(id) on delete cascade
                       default current_household_id(),
  contract_id        uuid not null references contracts(id) on delete cascade,
  due_date           date not null,
  status             text not null check (status in ('matched', 'missing', 'skipped')),
  transaction_id     uuid references transactions(id) on delete set null,
  booked_date        date,
  amount_minor       bigint,
  amount_delta_minor bigint,
  days_delta         int,
  match_score        numeric(3, 2) check (match_score is null
                       or match_score between 0 and 1),
  matched_at         timestamptz,
  created_at         timestamptz not null default now()
);

create unique index if not exists contract_occurrences_due_uniq
  on contract_occurrences (contract_id, due_date);
-- Kern der Doppelzaehlungssicherung: eine Buchung erfuellt hoechstens einen
-- Vertrag.
create unique index if not exists contract_occurrences_transaction_uniq
  on contract_occurrences (household_id, transaction_id)
  where transaction_id is not null;
create index if not exists contract_occurrences_contract_idx
  on contract_occurrences (household_id, contract_id, due_date desc);

-- ---------- 4. contract_events ----------------------------------------------

create table if not exists contract_events (
  id              uuid primary key default gen_random_uuid(),
  household_id    uuid not null references households(id) on delete cascade
                    default current_household_id(),
  contract_id     uuid not null references contracts(id) on delete cascade,
  kind            text not null check (kind in (
                    'created', 'price_change', 'payment_missing', 'payment_resumed',
                    'cancelled', 'ended', 'reactivated', 'merged', 'dismissed',
                    'installment_completed', 'term_ending')),
  occurred_on     date not null default current_date,
  payload         jsonb not null default '{}'::jsonb,
  acknowledged_at timestamptz,
  notified_at     timestamptz,
  created_at      timestamptz not null default now()
);

create index if not exists contract_events_contract_idx
  on contract_events (household_id, contract_id, occurred_on desc);
create index if not exists contract_events_open_idx
  on contract_events (household_id, kind, occurred_on desc)
  where acknowledged_at is null;

-- ---------- 5. contract_settings --------------------------------------------

-- Bewusst text mit CHECK statt int: 'off' laesst sich so ohne Sonderwert wie 0
-- oder NULL darstellen (gleiche Begruendung wie 0012 fuer reasoning_effort).
create table if not exists contract_settings (
  household_id          uuid primary key references households(id) on delete cascade
                          default current_household_id(),
  auto_end_after_missed text not null default '3'
                          check (auto_end_after_missed in ('off', '2', '3', '4')),
  updated_at            timestamptz not null default now()
);

-- ---------- 6. Row Level Security und Grants --------------------------------

alter table contracts enable row level security;
drop policy if exists contracts_read on contracts;
create policy contracts_read on contracts for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contracts_write on contracts;
create policy contracts_write on contracts for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table contract_amount_history enable row level security;
drop policy if exists contract_amount_history_read on contract_amount_history;
create policy contract_amount_history_read on contract_amount_history for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_amount_history_write on contract_amount_history;
create policy contract_amount_history_write on contract_amount_history for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table contract_occurrences enable row level security;
drop policy if exists contract_occurrences_read on contract_occurrences;
create policy contract_occurrences_read on contract_occurrences for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_occurrences_write on contract_occurrences;
create policy contract_occurrences_write on contract_occurrences for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table contract_events enable row level security;
drop policy if exists contract_events_read on contract_events;
create policy contract_events_read on contract_events for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_events_write on contract_events;
create policy contract_events_write on contract_events for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table contract_settings enable row level security;
drop policy if exists contract_settings_read on contract_settings;
create policy contract_settings_read on contract_settings for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_settings_write on contract_settings;
create policy contract_settings_write on contract_settings for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update, delete on contracts to authenticated;
grant select, insert, update, delete on contract_amount_history to authenticated;
grant select, insert, update, delete on contract_occurrences to authenticated;
grant select, insert, update, delete on contract_events to authenticated;
grant select, insert, update on contract_settings to authenticated;

-- ---------- 7. Kommentare ---------------------------------------------------

comment on table contracts is
  'Vertraege, Abonnements und wiederkehrende Zahlungen. Loest recurring_rules '
  'und subscriptions ab. Siehe docs/VERTRAEGE-FIXKOSTEN-PLAN.md.';
comment on column contracts.amount_minor is
  'Sollbetrag, IMMER positiv. Die Richtung steht in direction. Damit sind '
  'Summenbildung und Einfaerbung eindeutig; die frueheren recurring_rules '
  'mischten negative (erkannte) und positive (manuelle) Betraege.';
comment on column contracts.contract_key is
  'Stabiler Kern des Verwendungszwecks (Mandatsreferenz, Vertrags- oder '
  'Kundennummer). Unterscheidet zwei parallele Vertraege desselben Anbieters '
  'von einer Preisaenderung eines einzelnen Vertrags.';
comment on column contracts.status is
  'cancelled bedeutet gekuendigt, laeuft aber bis ends_on weiter und kostet bis '
  'dahin Geld. ended bedeutet beendet. not_a_contract ist eine vom Nutzer '
  'verneinte Fehlerkennung, die nie wieder vorgeschlagen wird.';
comment on column contracts.missed_periods is
  'Zahl der aufeinanderfolgenden Faelligkeiten ohne passende Buchung. Ab 2 '
  'entsteht ein Review-Item, ab contract_settings.auto_end_after_missed wird '
  'der Vertrag automatisch beendet.';
comment on column contracts.detection_confidence is
  'Vertrags-Score der Erkennung (0..1) aus Datumsstabilitaet, Betragsstabilitaet, '
  'Vorkommen, Kategorieaffinitaet und Zahlungsart. Nicht der Rhythmus-Score.';

comment on table contract_occurrences is
  'Je erwarteter Faelligkeit ihre Erfuellung. Der partielle Unique-Index auf '
  'transaction_id stellt sicher, dass eine Buchung hoechstens einen Vertrag '
  'erfuellt.';
comment on table contract_amount_history is
  'Betragsverlauf eines Vertrags. Grundlage fuer die Preisaenderungserkennung '
  'und den Bericht zur Fixkostenentwicklung.';
comment on table contract_events is
  'Vom Abgleichslauf abgeleitete Fakten. Abgegrenzt vom audit_log, der '
  'Nutzerhandlungen protokolliert.';
comment on column contract_settings.auto_end_after_missed is
  'off = nur Vorschlag in der Review-Inbox. 2/3/4 = Vertrag wird nach so vielen '
  'ausgebliebenen Faelligkeiten automatisch beendet. Vertraege mit von Hand '
  'gesetztem ends_on oder cancelled_on sind davon ausgenommen.';

-- ---------- 8. Uebernahme aus recurring_rules und subscriptions -------------

-- Idempotent ueber legacy_recurring_rule_id / legacy_subscription_id.
-- Vertraege mit Betrag 0 werden uebersprungen (amount_minor > 0).

insert into contracts (
  household_id, name, provider_label, contract_type, payee_id, account_id, category_id,
  direction, amount_minor, frequency, due_rule, due_day, next_due_date,
  tolerance_days, tolerance_amount_minor, status, source, notified_at, note,
  legacy_recurring_rule_id
)
select
  rr.household_id,
  left(coalesce(
    nullif(btrim(regexp_replace(rr.note, '^Erkannt aus Buchungsverlauf:\s*', '')), ''),
    p.name,
    'Unbenannter Vertrag'
  ), 160),
  left(p.name, 160),
  case
    when rr.expected_amount_minor >= 0 then 'income'
    when lower(c.name) like '%miete%' or lower(c.name) like '%hausgeld%' then 'rent'
    when lower(c.name) like '%versicher%' or lower(c.name) like '%vorsorge%'
      or lower(c.name) like '%berufsunf%' then 'insurance'
    when lower(c.name) like '%sparen%' or lower(c.name) like '%investier%' then 'savings'
    when lower(c.name) like '%strom%' or lower(c.name) like '%gas%'
      or lower(c.name) like '%heiz%' or lower(c.name) like '%wasser%' then 'utility'
    when lower(c.name) like '%internet%' or lower(c.name) like '%mobilfunk%'
      or lower(c.name) like '%festnetz%' or lower(c.name) like '%telefon%' then 'telecom'
    when lower(c.name) like '%streaming%' or lower(c.name) like '%digitale medien%'
      then 'subscription'
    when lower(c.name) like '%ratenkauf%' or lower(c.name) like '%finanzierung%'
      or lower(c.name) like '%kredit%' or lower(c.name) like '%tilgung%' then 'loan'
    when lower(c.name) like '%spende%' then 'donation'
    when lower(c.name) like '%rundfunk%' then 'membership'
    else 'other'
  end,
  rr.payee_id,
  rr.account_id,
  rr.category_id,
  case when rr.expected_amount_minor < 0 then 'expense' else 'income' end,
  abs(rr.expected_amount_minor),
  rr.frequency,
  case when extract(day from rr.next_due_date) > 28 then 'month_end' else 'day_of_month' end,
  extract(day from rr.next_due_date)::int,
  rr.next_due_date,
  rr.tolerance_days,
  rr.tolerance_amount_minor,
  case when rr.is_active then 'active' else 'ended' end,
  case when rr.note like 'Erkannt aus Buchungsverlauf:%' then 'confirmed' else 'manual' end,
  rr.notified_at,
  case when rr.note like 'Erkannt aus Buchungsverlauf:%' then null else rr.note end,
  rr.id
from recurring_rules rr
left join payees p on p.id = rr.payee_id
left join categories c on c.id = rr.category_id
where rr.expected_amount_minor <> 0
  and not exists (
    select 1 from contracts x where x.legacy_recurring_rule_id = rr.id
  );

insert into contracts (
  household_id, name, provider_label, contract_type, account_id, category_id,
  direction, amount_minor, frequency, due_rule, due_day, next_due_date,
  status, source, cancellation_period_days, note,
  legacy_subscription_id
)
select
  s.household_id,
  left(s.provider || coalesce(' - ' || nullif(btrim(s.plan_name), ''), ''), 160),
  left(s.provider, 160),
  'subscription',
  s.account_id,
  s.category_id,
  'expense',
  case when s.monthly_cost_minor <> 0 then abs(s.monthly_cost_minor)
       else abs(s.annual_cost_minor) end,
  case when s.monthly_cost_minor <> 0 then 'monthly' else 'yearly' end,
  case when extract(day from coalesce(s.next_renewal_date,
         (date_trunc('month', current_date) + interval '1 month')::date)) > 28
       then 'month_end' else 'day_of_month' end,
  extract(day from coalesce(s.next_renewal_date,
    (date_trunc('month', current_date) + interval '1 month')::date))::int,
  coalesce(s.next_renewal_date, (date_trunc('month', current_date) + interval '1 month')::date),
  case when s.is_active then 'active' else 'ended' end,
  'manual',
  s.cancellation_period_days,
  s.note,
  s.id
from subscriptions s
where (s.monthly_cost_minor <> 0 or s.annual_cost_minor <> 0)
  and not exists (
    select 1 from contracts x where x.legacy_subscription_id = s.id
  );

-- Startstufe des Betragsverlaufs fuer jeden uebernommenen Vertrag.
insert into contract_amount_history (
  household_id, contract_id, amount_minor, valid_from, change_reason
)
select ct.household_id, ct.id, ct.amount_minor, ct.created_at::date, 'initial'
from contracts ct
where not exists (
  select 1 from contract_amount_history h where h.contract_id = ct.id
);

-- ---------- 9. Schutz gegen archivierte Kategorien --------------------------

-- Bewusst NACH der Uebernahme: eine Altregel darf auf eine inzwischen
-- archivierte Kategorie zeigen, und diese Historie soll nicht verloren gehen.
-- Ab jetzt verhindert der Trigger neue Referenzen darauf.
drop trigger if exists contracts_reject_archived_category on contracts;
create trigger contracts_reject_archived_category
  before insert or update on contracts
  for each row execute function reject_archived_category_reference('category_id');

-- ============================================================================
-- Ehemals: 0015_contract_intelligence.sql
-- Vertragsintelligenz (Phase 4 aus docs/VERTRAEGE-FIXKOSTEN-PLAN.md)
-- ============================================================================
-- Phase 1 bis 3 haben Vertraege erkannt, abgeglichen und fortgeschrieben.
-- Phase 4 beantwortet die Fragen, die danach kommen:
--
--   * "Bis wann muss ich kuendigen?"  -> starts_on + renewal_term_months
--     machen den letzten Kuendigungstermin ueberhaupt erst berechenbar,
--     contract_events_term_ending_uniq haelt die Erinnerung dedupliziert.
--   * "Woher stammt die Vertragsart?" -> contract_provider_templates ist der
--     globale Anbieterkatalog (Telekom, Allianz, Netflix ...), aus dem das
--     Anlegen-Formular Vertragsart, Zielkategorie und typische Kuendigungsfrist
--     vorschlaegt; provider_template_key haelt fest, welcher Eintrag es war.
--   * "Was war in Monat X vereinbart?" -> der Fixkostentrend liest
--     contract_amount_history haushaltweit ueber einen Zeitraum; der bestehende
--     Index hat contract_id in der Mitte und taugt dafuer nicht.
--   * "Welche Push-Nachricht will ich?" -> contract_settings.notify_*; ohne die
--     Schalter gaebe es nur alles oder nichts ueber die OS-Berechtigung.
--
-- Zwei Festlegungen sind hier tragend:
--   1. starts_on ist der VERTRAGSBEGINN und bewusst nicht first_seen_date.
--      Letzteres setzt der Abgleichsmotor aus dem Importzeitraum; wer die
--      Mindestlaufzeit daran ankert, verschiebt jede Frist auf den Tag, an dem
--      der Nutzer zufaellig seine erste CSV hochgeladen hat.
--   2. Der Anbieterabgleich laeuft in Python ueber contract_matching_service.
--      payee_key(), nicht in SQL. Eine zweite Normalisierung im Schema wuerde
--      genau die Anbieter verfehlen, die der Erkennungsmotor selbst gefunden
--      hat. match_patterns liegt deshalb bereits in payee_key()-Normalform vor:
--      kleingeschrieben, alle Nicht-Wortzeichen zu Leerzeichen ("1&1" -> "1 1").
--

-- ---------- 10. contract_provider_templates ----------------------------------

-- Global und haushaltsunabhaengig wie die drei Kategorie-Template-Tabellen aus
-- 0007: jeder angemeldete Nutzer darf lesen, schreiben darf ausschliesslich die
-- Migration.
create table if not exists contract_provider_templates (
  id                        uuid primary key default gen_random_uuid(),
  -- Stabiler Seed-Schluessel wie category_templates.semantic_key. Er traegt das
  -- idempotente "on conflict do update" weiter unten und ist zugleich das Ziel
  -- von contracts.provider_template_key.
  semantic_key              text not null unique,
  provider_name             text not null check (length(btrim(provider_name)) between 1 and 160),
  -- Bereits in payee_key()-Normalform. Ein Muster in Anzeigeschreibweise
  -- ("1&1 Telecom") traefe strukturell nie.
  match_patterns            text[] not null default '{}'::text[],
  contract_type             text not null check (contract_type in (
                              'subscription', 'insurance', 'rent', 'loan', 'installment',
                              'utility', 'telecom', 'membership', 'donation', 'savings',
                              'income', 'other')),
  -- Verweist auf category_templates.semantic_key bzw. categories.semantic_key.
  -- Bewusst OHNE Fremdschluessel: dort ist der Schluessel zusammengesetzt
  -- (taxonomy_version, semantic_key), der Anbieterkatalog ist aber
  -- paketversionsunabhaengig.
  default_category_key      text,
  typical_frequency         text check (typical_frequency is null or typical_frequency in
                              ('weekly', 'monthly', 'quarterly', 'semiannual', 'yearly')),
  typical_min_term_months   int check (typical_min_term_months is null
                              or typical_min_term_months between 1 and 600),
  typical_cancellation_days int check (typical_cancellation_days is null
                              or typical_cancellation_days between 0 and 1095),
  -- Kleinerer Wert gewinnt: ein mehrteiliges Muster ("fitness first") soll vor
  -- dem allgemeineren stehen.
  priority                  int not null default 100,
  is_active                 boolean not null default true,
  created_at                timestamptz not null default now()
);

create index if not exists contract_provider_templates_active_idx
  on contract_provider_templates (priority, semantic_key) where is_active;

alter table contract_provider_templates enable row level security;
drop policy if exists contract_provider_templates_read on contract_provider_templates;
create policy contract_provider_templates_read on contract_provider_templates
  for select to authenticated using (true);

-- Der Katalog ist Migrationsgut. Das "grant select" allein genuegt dafuer
-- nicht: "alter default privileges in schema public" (0001) verteilt an jede
-- neue Tabelle select/insert/update/delete an authenticated, das Recht ist also
-- schon da, bevor diese Zeile laeuft. Gegen einen Schreibzugriff schuetzt sonst
-- nur die fehlende RLS-Policy - eine Schicht, keine zwei.
grant select on contract_provider_templates to authenticated;
revoke insert, update, delete on contract_provider_templates from authenticated;

-- ---------- 11. contracts: Vertragsbeginn, Verlaengerung, Herkunft -----------

alter table contracts
  add column if not exists starts_on             date,
  add column if not exists renewal_term_months   int,
  add column if not exists provider_template_key text;

-- 1 bis 120 statt "> 0": ein renewal_term_months von 0 waere in der
-- Fristberechnung eine Endlosschleife (contract_service.compute_term_deadline
-- hat zusaetzlich einen Iterationsdeckel - Guertel und Hosentraeger).
alter table contracts drop constraint if exists contracts_renewal_term_months_check;
alter table contracts add constraint contracts_renewal_term_months_check
  check (renewal_term_months is null or renewal_term_months between 1 and 120);

alter table contracts drop constraint if exists contracts_provider_template_fkey;
alter table contracts add constraint contracts_provider_template_fkey
  foreign key (provider_template_key)
  references contract_provider_templates (semantic_key) on delete set null;

-- Arbeitsvorrat des naechtlichen Fristlaufs.
create index if not exists contracts_term_watch_idx
  on contracts (household_id, starts_on)
  where status in ('active', 'paused') and min_term_months is not null;

-- ---------- 12. contract_events: struktureller Dedup der Fristerinnerung -----

-- Genau ein Fristereignis je Vertrag, Termin und Vorlaufstufe. Gleiche Haltung
-- wie contract_occurrences_transaction_uniq: der Dedup steht im Schema, nicht
-- nur in der Schleife des Worker-Laufs.
create unique index if not exists contract_events_term_ending_uniq
  on contract_events (contract_id, (payload ->> 'deadline'), (payload ->> 'stage'))
  where kind = 'term_ending';

-- Arbeitsvorrat des Benachrichtigungslaufs. notified_at existiert seit 0014 und
-- wurde bisher von niemandem geschrieben - genau der freie Dedup-Anker fuer die
-- neuen Push-Typen (contracts.notified_at ist von 'recurring_due' belegt).
create index if not exists contract_events_unnotified_idx
  on contract_events (household_id, kind, occurred_on)
  where notified_at is null;

-- ---------- 13. contract_settings: Push je Typ -------------------------------

alter table contract_settings
  add column if not exists notify_price_change    boolean not null default true,
  add column if not exists notify_payment_missing boolean not null default true,
  add column if not exists notify_term_ending     boolean not null default true;

-- ---------- 5. transaction_splits: Herkunft "contract" ----------------------

alter table transaction_splits
  add column if not exists category_contract_id uuid references contracts(id) on delete set null;

alter table transaction_splits drop constraint if exists transaction_splits_category_source_check;
alter table transaction_splits add constraint transaction_splits_category_source_check
  check (category_source in ('unset', 'unknown', 'manual', 'import', 'rule', 'ai',
                             'history', 'contract'));

-- ---------- 6. Zeitreihenindex fuer den Fixkostenbericht --------------------

-- contract_amount_history_contract_idx hat contract_id in der Mitte und taugt
-- damit nicht fuer den haushaltweiten Zeitraumscan des Trendberichts.
create index if not exists contract_amount_history_window_idx
  on contract_amount_history (household_id, valid_from, valid_to);

-- ---------- 7. Kommentare ---------------------------------------------------

comment on table contract_provider_templates is
  'Globaler, nur lesbarer Anbieterkatalog (Telekom, Allianz, Netflix ...) fuer '
  'Vertragsart, Zielkategorie und typische Kuendigungsfrist beim Anlegen. '
  'match_patterns liegt in payee_key()-Normalform vor, der Abgleich laeuft in '
  'Python ueber contract_matching_service.payee_key().';
comment on column contract_provider_templates.default_category_key is
  'category_templates.semantic_key bzw. categories.semantic_key. Bewusst ohne '
  'Fremdschluessel: dort ist der Schluessel (taxonomy_version, semantic_key) '
  'zusammengesetzt, der Anbieterkatalog ist paketversionsunabhaengig.';
comment on column contracts.starts_on is
  'Vertragsbeginn und Anker der Mindestlaufzeit. NICHT first_seen_date: das '
  'setzt der Abgleichsmotor aus dem Importzeitraum und verschoebe jede '
  'Kuendigungsfrist auf den ersten CSV-Upload.';
comment on column contracts.renewal_term_months is
  'Automatische Verlaengerung nach Ablauf der Mindestlaufzeit. NULL bedeutet '
  'keine Verlaengerung; ohne dieses Feld waere nach dem ersten Termin kein '
  'weiterer Kuendigungstermin berechenbar.';
comment on column contracts.provider_template_key is
  'Der Katalogeintrag, aus dem Vertragsart und Frist uebernommen wurden. Macht '
  'die Vorbelegung nachvollziehbar und erlaubt spaeteres Nachziehen.';
comment on column transaction_splits.category_contract_id is
  'Der Vertrag, aus dem die Kategorie stammt (category_source = ''contract''). '
  'Ohne diese Spalte waere die Herkunft "aus dem Vertrag uebernommen" nicht '
  'auf einen konkreten Vertrag zurueckfuehrbar.';

-- =============================================================================
-- Fixkostenziel und Senkungsszenarien
-- =============================================================================
--
-- Die Fixkostenquote beantwortet "wie stehe ich da", aber nicht "wo will ich
-- hin" und schon gar nicht "was bringt es, wenn ich X kuendige". Dafuer stehen
-- hier zwei Dinge nebeneinander:
--
--   contract_cost_targets   - EIN Ziel je Haushalt (Quote plus Zieldatum)
--   contract_scenarios      - benannte Was-waere-wenn-Plaene mit Positionen
--
-- Szenarien aendern bewusst KEINEN Vertrag. Sie sind eine Absichtserklaerung,
-- kein Vollzug: wer "Fitnessstudio kuendigen" plant, hat es noch nicht
-- gekuendigt, und die Fixkostensumme muss bis zur echten Kuendigung
-- unveraendert bleiben. Genau deshalb liegen die Positionen in einer eigenen
-- Tabelle statt als Statusflag am Vertrag.

-- ---------- 1. contract_cost_targets -----------------------------------------

-- Quote als Leitgroesse, Betrag optional daneben: ein absolutes Ziel von
-- "2.400 EUR" ist nach der naechsten Gehaltserhoehung stillschweigend falsch,
-- eine Quote nicht. Wer trotzdem einen festen Betrag will, setzt
-- target_monthly_minor - dann fuehrt der Betrag und die Quote wird nur noch
-- nachrichtlich mitgefuehrt.
create table if not exists contract_cost_targets (
  household_id          uuid primary key references households(id) on delete cascade
                          default current_household_id(),
  target_ratio_percent  numeric(5, 2) not null default 50
                          check (target_ratio_percent > 0 and target_ratio_percent <= 100),
  target_monthly_minor  bigint check (target_monthly_minor is null
                          or target_monthly_minor >= 0),
  target_date           date,
  note                  text check (note is null or length(btrim(note)) between 1 and 500),
  updated_at            timestamptz not null default now()
);

-- ---------- 2. contract_scenarios --------------------------------------------

create table if not exists contract_scenarios (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),
  name         text not null check (length(btrim(name)) between 1 and 120),
  -- Genau ein Szenario darf "das ist mein Plan" sein. Es speist die
  -- Zielkarte; alle uebrigen bleiben Gedankenspiele.
  is_active    boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists contract_scenarios_household_idx
  on contract_scenarios (household_id, created_at desc);

create unique index if not exists contract_scenarios_active_uniq
  on contract_scenarios (household_id) where is_active;

-- ---------- 3. contract_scenario_items ---------------------------------------

-- 'cancel' spart den vollen Monatsbetrag, 'reduce' die Differenz zum neuen
-- Betrag. Beide Faelle in einer Zeile mit einem Nullwert zu mischen waere
-- mehrdeutig: ein target_amount_minor von 0 bei 'reduce' saehe aus wie eine
-- Kuendigung, ohne eine zu sein - deshalb erzwingt der CHECK die Trennung.
create table if not exists contract_scenario_items (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references households(id) on delete cascade
                        default current_household_id(),
  scenario_id         uuid not null references contract_scenarios(id) on delete cascade,
  contract_id         uuid not null references contracts(id) on delete cascade,
  action              text not null check (action in ('cancel', 'reduce')),
  target_amount_minor bigint check (target_amount_minor is null or target_amount_minor >= 0),
  note                text check (note is null or length(btrim(note)) between 1 and 300),
  created_at          timestamptz not null default now(),

  constraint contract_scenario_items_action_amount_chk check (
    (action = 'cancel' and target_amount_minor is null)
    or (action = 'reduce' and target_amount_minor is not null)
  )
);

-- Ein Vertrag kommt in einem Szenario hoechstens einmal vor: "kuendigen UND auf
-- 20 EUR senken" ergaebe eine doppelt gezaehlte Ersparnis.
create unique index if not exists contract_scenario_items_contract_uniq
  on contract_scenario_items (scenario_id, contract_id);

create index if not exists contract_scenario_items_scenario_idx
  on contract_scenario_items (household_id, scenario_id);

-- ---------- 4. Row Level Security und Grants ---------------------------------

alter table contract_cost_targets enable row level security;
drop policy if exists contract_cost_targets_read on contract_cost_targets;
create policy contract_cost_targets_read on contract_cost_targets for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_cost_targets_write on contract_cost_targets;
create policy contract_cost_targets_write on contract_cost_targets for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table contract_scenarios enable row level security;
drop policy if exists contract_scenarios_read on contract_scenarios;
create policy contract_scenarios_read on contract_scenarios for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_scenarios_write on contract_scenarios;
create policy contract_scenarios_write on contract_scenarios for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table contract_scenario_items enable row level security;
drop policy if exists contract_scenario_items_read on contract_scenario_items;
create policy contract_scenario_items_read on contract_scenario_items for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_scenario_items_write on contract_scenario_items;
create policy contract_scenario_items_write on contract_scenario_items for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update, delete on contract_cost_targets to authenticated;
grant select, insert, update, delete on contract_scenarios to authenticated;
grant select, insert, update, delete on contract_scenario_items to authenticated;

-- ---------- 5. Kommentare ----------------------------------------------------

comment on table contract_cost_targets is
  'Angestrebte Fixkosten je Haushalt. Genau eine Zeile - ein zweites Ziel waere '
  'kein Ziel mehr. Siehe docs/VERTRAEGE-FIXKOSTEN-PLAN.md.';
comment on column contract_cost_targets.target_ratio_percent is
  'Leitgroesse: angestrebter Anteil der Fixkosten an den Einnahmen. Bezugsgroesse '
  'ist dieselbe wie bei der Fixkostenquote im Budget (gebuchte Einnahmen, '
  'ersatzweise Einnahmevertraege) - zwei Bezugsgroessen ergaeben zwei Quoten '
  'unter einem Namen.';
comment on column contract_cost_targets.target_monthly_minor is
  'Optionaler absoluter Zielbetrag. Ist er gesetzt, fuehrt er; sonst wird er aus '
  'der Quote abgeleitet.';
comment on table contract_scenarios is
  'Was-waere-wenn-Plan zur Fixkostensenkung. Aendert keinen Vertrag: ein '
  'geplanter Kuendigungswunsch ist keine Kuendigung, und die Fixkostensumme '
  'darf sich davon nicht bewegen.';
comment on column contract_scenario_items.action is
  '''cancel'' spart den vollen normierten Monatsbetrag, ''reduce'' die Differenz '
  'zu target_amount_minor. target_amount_minor ist der Betrag JE PERIODE, nicht '
  'je Monat - gleiche Einheit wie contracts.amount_minor.';

-- ============================================================================
-- 8. Seed: bekannte deutsche Anbieter
-- ============================================================================
-- Idempotent per "on conflict (semantic_key) do update" wie das deutsche
-- Standardpaket in 0007. Muster IMMER in payee_key()-Normalform: klein, alle
-- Nicht-Wortzeichen zu Leerzeichen ("E.ON" -> "e on", "Disney+" -> "disney").
-- Die typischen Fristen bilden den Regelfall ab, nicht den Einzelvertrag - sie
-- sind eine Vorbelegung des Formulars, keine Rechtsauskunft.

insert into contract_provider_templates (
  semantic_key, provider_name, match_patterns, contract_type,
  default_category_key, typical_frequency, typical_min_term_months,
  typical_cancellation_days, priority
) values
  -- Telekommunikation: seit dem TKG 2021 nach der Mindestlaufzeit monatlich
  -- kuendbar, deshalb 30 Tage statt der frueheren 90.
  ('telekom',        'Telekom',              array['telekom', 't mobile', 'deutsche telekom'],
     'telecom',   'telecom',          'monthly', 24, 30,  90),
  ('vodafone',       'Vodafone',             array['vodafone', 'kabel deutschland', 'unitymedia'],
     'telecom',   'telecom',          'monthly', 24, 30,  90),
  ('one_and_one',    '1&1',                  array['1 1', '1und1', '1 1 telecom', '1 1 versatel'],
     'telecom',   'telecom',          'monthly', 24, 30,  90),
  ('o2',             'o2 (Telefonica)',      array['o2', 'telefonica', 'o2 germany'],
     'telecom',   'telecom',          'monthly', 24, 30,  90),
  ('congstar',       'congstar',             array['congstar'],
     'telecom',   'telecom',          'monthly', 24, 30, 100),
  ('pyur',           'PYUR',                 array['pyur', 'tele columbus'],
     'telecom',   'telecom',          'monthly', 24, 30, 100),

  -- Streaming und digitale Abos: jederzeit kuendbar, keine Mindestlaufzeit.
  ('netflix',        'Netflix',              array['netflix'],
     'subscription', 'streaming',     'monthly', null, 0, 100),
  ('spotify',        'Spotify',              array['spotify'],
     'subscription', 'streaming',     'monthly', null, 0, 100),
  ('disney_plus',    'Disney+',              array['disney', 'disney plus'],
     'subscription', 'streaming',     'monthly', null, 0, 100),
  ('amazon_prime',   'Amazon Prime',         array['amazon prime', 'prime video', 'amzn prime'],
     'subscription', 'streaming',     'yearly',  null, 0,  90),
  ('dazn',           'DAZN',                 array['dazn'],
     'subscription', 'streaming',     'monthly', 12,  30, 100),
  ('sky',            'Sky Deutschland',      array['sky', 'sky deutschland', 'wow'],
     'subscription', 'streaming',     'monthly', 12,  30, 100),
  ('apple_services', 'Apple Services',       array['apple com bill', 'apple services', 'itunes'],
     'subscription', 'streaming',     'monthly', null, 0,  90),

  -- Versicherungen: Sachversicherungen laufen ueblich ein Jahr und sind mit
  -- drei Monaten zum Ablauf kuendbar.
  ('allianz',        'Allianz',              array['allianz'],
     'insurance', 'property_insurance', 'yearly', 12, 90, 100),
  ('huk_coburg',     'HUK-COBURG',           array['huk', 'huk coburg', 'huk24'],
     'insurance', 'car_tax_insurance',  'yearly', 12, 30, 100),
  ('axa',            'AXA',                  array['axa'],
     'insurance', 'property_insurance', 'yearly', 12, 90, 100),
  ('ergo',           'ERGO',                 array['ergo', 'ergo direkt'],
     'insurance', 'property_insurance', 'yearly', 12, 90, 100),
  ('arag',           'ARAG',                 array['arag'],
     'insurance', 'property_insurance', 'yearly', 12, 90, 100),
  ('barmenia',       'Barmenia',             array['barmenia', 'barmenia gothaer'],
     'insurance', 'health_insurance',   'monthly', null, 90, 100),
  ('debeka',         'Debeka',               array['debeka'],
     'insurance', 'health_insurance',   'monthly', null, 90, 100),
  ('cosmosdirekt',   'CosmosDirekt',         array['cosmosdirekt', 'cosmos direkt'],
     'insurance', 'risk_insurance',     'yearly', 12, 30, 100),
  ('signal_iduna',   'SIGNAL IDUNA',         array['signal iduna'],
     'insurance', 'property_insurance', 'yearly', 12, 90, 100),

  -- Gesetzliche Krankenkassen: 12 Monate Bindung, zwei Monate Kuendigungsfrist.
  ('tk',             'Techniker Krankenkasse', array['tk', 'techniker krankenkasse'],
     'insurance', 'health_insurance',   'monthly', 12, 60, 90),
  ('aok',            'AOK',                  array['aok'],
     'insurance', 'health_insurance',   'monthly', 12, 60, 100),
  ('dak',            'DAK-Gesundheit',       array['dak', 'dak gesundheit'],
     'insurance', 'health_insurance',   'monthly', 12, 60, 100),
  ('barmer',         'BARMER',               array['barmer'],
     'insurance', 'health_insurance',   'monthly', 12, 60, 100),

  -- Energie und Grundversorgung.
  ('vattenfall',     'Vattenfall',           array['vattenfall'],
     'utility',   'energy',             'monthly', 12, 30, 100),
  ('eon',            'E.ON',                 array['e on', 'eon', 'eon energie'],
     'utility',   'energy',             'monthly', 12, 30, 100),
  ('enbw',           'EnBW',                 array['enbw'],
     'utility',   'energy',             'monthly', 12, 30, 100),
  ('stadtwerke',     'Stadtwerke',           array['stadtwerke', 'stadtwerk'],
     'utility',   'energy',             'monthly', 12, 30, 120),
  ('lichtblick',     'LichtBlick',           array['lichtblick'],
     'utility',   'energy',             'monthly', null, 30, 100),
  ('rundfunkbeitrag','Rundfunkbeitrag (ARD ZDF Deutschlandradio)',
     array['rundfunk ard zdf', 'rundfunkbeitrag', 'ard zdf deutschlandradio', 'gez'],
     'utility',   'broadcast_fee',      'quarterly', null, 0, 90),

  -- Mitgliedschaften: Fitnessstudios binden ueblich 12 Monate bei drei Monaten
  -- Kuendigungsfrist.
  ('mcfit',          'McFIT',                array['mcfit', 'mc fit', 'rsg group'],
     'membership', 'sports_clubs',      'monthly', 12, 90, 100),
  ('fitness_first',  'Fitness First',        array['fitness first'],
     'membership', 'sports_clubs',      'monthly', 12, 90,  90),
  ('clever_fit',     'clever fit',           array['clever fit', 'cleverfit'],
     'membership', 'sports_clubs',      'monthly', 12, 90,  90),
  ('urban_sports',   'Urban Sports Club',    array['urban sports club', 'urban sports'],
     'membership', 'sports_clubs',      'monthly', null, 30, 90),
  ('adac',           'ADAC',                 array['adac'],
     'membership', 'car_service',       'yearly',  12, 42, 100),
  ('bahncard',       'BahnCard (Deutsche Bahn)',
     array['bahncard', 'deutsche bahn', 'db vertrieb', 'db fernverkehr'],
     'membership', 'public_transport',  'yearly',  12, 42, 100),

  -- Zahlungsdienste: Ratenkauf ohne feste Zielkategorie - der gekaufte
  -- Gegenstand bestimmt sie, nicht der Zahlungsdienstleister.
  ('klarna',         'Klarna',               array['klarna'],
     'installment', null,               'monthly', null, 0, 100),
  ('paypal',         'PayPal',               array['paypal', 'paypal europe'],
     'installment', null,               'monthly', null, 0, 110),

  -- Spenden und Foerdermitgliedschaften.
  ('greenpeace',     'Greenpeace',           array['greenpeace'],
     'donation',  'donations',          'monthly', null, 0, 100),
  ('unicef',         'UNICEF',               array['unicef'],
     'donation',  'donations',          'monthly', null, 0, 100),
  ('brot_fuer_die_welt', 'Brot fuer die Welt', array['brot fuer die welt', 'brot f d welt'],
     'donation',  'donations',          'monthly', null, 0, 100)
on conflict (semantic_key) do update
  set provider_name             = excluded.provider_name,
      match_patterns            = excluded.match_patterns,
      contract_type             = excluded.contract_type,
      default_category_key      = excluded.default_category_key,
      typical_frequency         = excluded.typical_frequency,
      typical_min_term_months   = excluded.typical_min_term_months,
      typical_cancellation_days = excluded.typical_cancellation_days,
      priority                  = excluded.priority,
      is_active                 = true;

-- ============================================================================
-- Vertraege Phase 5: reversible Zusammenfuehrung, manuelle Zuordnung,
-- schluesselbewusste Duplikaterkennung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md,
-- Abschnitt 19).
-- ============================================================================
-- Ausloeser: drei Legacy-Zeilen desselben Anbieters, wo eigentlich zwei
-- Vertraege stehen (Mobilfunk getrennt, Festnetz mit einer Preissenkung
-- unterjaehrig). Der bisherige Merge loescht
-- Quellvertraege hart - eine falsch bestaetigte Zusammenfuehrung war bislang
-- nicht vollstaendig rueckgaengig zu machen (Abschnitt 19.5).

-- ---------- 1. contracts: weiche Zusammenfuehrung statt Loeschen -----------
alter table contracts add column if not exists merged_into_id uuid
  references contracts(id) on delete set null;
alter table contracts add column if not exists merged_at timestamptz;

create index if not exists contracts_merged_into_idx
  on contracts (household_id, merged_into_id) where merged_into_id is not null;

-- Kalkulatorische Fixkosten-Ruecklagen.  Additiv fuer bereits vorhandene
-- Datenbanken; reserve_started_on unterscheidet echte Altzeilen von einem
-- spaeteren ausdruecklichen Opt-out und verhindert erneutes Aktivieren beim
-- idempotenten Schema-Lauf.
alter table contracts add column if not exists reserve_enabled boolean not null default false;
alter table contracts add column if not exists reserve_started_on date;
alter table contracts add column if not exists reserve_starting_amount_minor bigint not null default 0;
alter table contracts drop constraint if exists contracts_reserve_starting_amount_check;
alter table contracts add constraint contracts_reserve_starting_amount_check
  check (reserve_starting_amount_minor >= 0);

update contracts
set reserve_enabled = true,
    reserve_started_on = current_date,
    reserve_starting_amount_minor = 0
where reserve_started_on is null
  and direction = 'expense'
  and frequency in ('quarterly', 'semiannual', 'yearly');

comment on column contracts.reserve_enabled is
  'Plant nichtmonatliche Ausgaben kalkulatorisch als monatliche Fixkosten-Ruecklage.';
comment on column contracts.reserve_started_on is
  'Beginn der Planungsannahme; kein Nachweis eines Kontostands.';
comment on column contracts.reserve_starting_amount_minor is
  'Zum Planungsbeginn bereits angenommener Ruecklagenbetrag in Minor Units; kein Kontostand.';

comment on column contracts.merged_into_id is
  'Gesetzt, sobald dieser Vertrag als Quelle in einen anderen zusammengefuehrt '
  'wurde. Die Anwendungsschicht filtert ihn damit aus allen normalen Listen '
  'und Abgleichslaeufen, die Zeile bleibt aber fuer Undo und Ereignisverlauf '
  'erhalten statt geloescht zu werden.';

-- ---------- 2. contract_merge_operations: eine Zeile je Zusammenfuehrung ---
create table if not exists contract_merge_operations (
  id                     uuid primary key default gen_random_uuid(),
  household_id           uuid not null references households(id) on delete cascade
                           default current_household_id(),
  target_id              uuid not null references contracts(id) on delete cascade,
  source_ids             uuid[] not null,
  request_id             text not null,
  actor_user_id          uuid references auth.users(id) on delete set null,
  status                 text not null default 'applied'
                           check (status in ('applied', 'undone')),
  -- contracts.version des Ziels UNMITTELBAR VOR bzw. NACH der Zusammen-
  -- fuehrung. Zwei Werte statt "vorher + 1 erwartet": das eigene Update auf
  -- den Zielvertrag in apply_merge bumpt die Version nicht zuverlaessig
  -- genau einmal (least()/greatest() auf bislang NULLen aendern nichts, der
  -- Trigger bumpt nur bei echter Differenz). Die Undo-Vorschau vergleicht
  -- den AKTUELLEN Stand gegen "after" - das ist robust unabhaengig davon,
  -- wie viele Bumps der Merge selbst verursacht hat (siehe
  -- build_undo_preview).
  target_version_before  int not null,
  target_version_after   int,
  undone_at              timestamptz,
  undone_by              uuid references auth.users(id) on delete set null,
  created_at             timestamptz not null default now()
);
-- Dieselbe Anfragekennung wie beim Zusammenfuehren selbst (Idempotenz ueber
-- find_merge_by_request_id): ein doppelt abgeschickter Merge-Klick legt keine
-- zweite Operationszeile an.
create unique index if not exists contract_merge_operations_request_uniq
  on contract_merge_operations (household_id, target_id, request_id);
create index if not exists contract_merge_operations_target_idx
  on contract_merge_operations (household_id, target_id, created_at desc);
create index if not exists contract_merge_operations_source_idx
  on contract_merge_operations using gin (source_ids);

-- ---------- 3. contract_merge_changes: Vorher-Snapshot je beruehrter Zeile -
-- Ein Snapshot pro betroffener Fälligkeit/Preisstufe/Ereignis/Vertragszeile
-- statt eines einzelnen Feld-Deltas: die Preistreppe wird beim Zusammen-
-- fuehren komplett neu aufgebaut (jede Stufe kann valid_to/delta_minor
-- aendern, nicht nur die verschobenen), ein feldweises Delta muesste also
-- ohnehin jede Zeile vollstaendig mitfuehren. JSON-Snapshots sind laut Plan
-- (Abschnitt 19.7) die zulaessige Alternative zum reinen Delta und
-- unterliegen denselben RLS-/Audit-/Aufbewahrungsregeln wie die Originaldaten.
create table if not exists contract_merge_changes (
  id                uuid primary key default gen_random_uuid(),
  operation_id      uuid not null references contract_merge_operations(id) on delete cascade,
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  entity_type       text not null check (entity_type in
                      ('contract', 'occurrence', 'history', 'event')),
  entity_id         uuid not null,
  -- Bei entity_type='history' ist das der komplette Bestand von Ziel UND
  -- Quellen vor dem Lauf (siehe apply_merge) - beim Wiederherstellen werden
  -- alle aktuellen Zeilen dieser beiden Vertraege gelöscht und durch die
  -- Snapshots ersetzt, statt Einzelaenderungen zurueckzurechnen.
  prior_data        jsonb not null,
  created_at        timestamptz not null default now()
);
create index if not exists contract_merge_changes_operation_idx
  on contract_merge_changes (household_id, operation_id, entity_type);

-- ---------- 4. contract_assignment_overrides: manuelle Buchungszuordnung ---
-- Eigene Tabelle statt eines Feldes an contract_occurrences (Abschnitt 19.7,
-- "contract_occurrences oder in einer separaten Tabelle"): Occurrences werden
-- bei jedem Abgleich und beim Zusammenfuehren geloescht/neu angelegt, ein
-- Override muss das ueberleben, um wirklich verbindlich zu sein.
create table if not exists contract_assignment_overrides (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade
                   default current_household_id(),
  transaction_id uuid not null references transactions(id) on delete cascade,
  -- null = die Buchung wurde ausdruecklich von jedem Vertrag geloest ("kein
  -- Vertrag"). Das ist ein eigener, verbindlicher Zustand - nicht dasselbe wie
  -- "kein Override vorhanden".
  contract_id    uuid references contracts(id) on delete cascade,
  set_by         uuid references auth.users(id) on delete set null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create unique index if not exists contract_assignment_overrides_tx_uniq
  on contract_assignment_overrides (household_id, transaction_id);
create index if not exists contract_assignment_overrides_contract_idx
  on contract_assignment_overrides (household_id, contract_id)
  where contract_id is not null;

comment on table contract_assignment_overrides is
  'Bindende manuelle Buchungszuordnung. Erkennung, Reparaturlauf und Rescan '
  'duerfen eine hier vorhandene Zeile nicht ueberschreiben oder entfernen - '
  'nur eine erneute ausdrueckliche Nutzeraktion darf das (Plan Abschnitt 19.6, '
  'Leitsatz 5).';

-- ---------- 5. Row Level Security und Grants --------------------------------
alter table contract_merge_operations enable row level security;
drop policy if exists contract_merge_operations_read on contract_merge_operations;
create policy contract_merge_operations_read on contract_merge_operations for select
  to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_merge_operations_write on contract_merge_operations;
create policy contract_merge_operations_write on contract_merge_operations for all
  to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update on contract_merge_operations to authenticated;

alter table contract_merge_changes enable row level security;
drop policy if exists contract_merge_changes_read on contract_merge_changes;
create policy contract_merge_changes_read on contract_merge_changes for select
  to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
-- Eigene INSERT-Policy statt "for all": das Journal wird einmal beim Merge
-- geschrieben und danach nie mehr veraendert, auch nicht durch ein
-- spaeteres Undo - ohne eine passende Policy verweigert RLS bei aktiviertem
-- RLS und fehlender Regel jeden Schreibzugriff, auch INSERT.
drop policy if exists contract_merge_changes_insert on contract_merge_changes;
create policy contract_merge_changes_insert on contract_merge_changes for insert
  to authenticated
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert on contract_merge_changes to authenticated;

alter table contract_assignment_overrides enable row level security;
drop policy if exists contract_assignment_overrides_read on contract_assignment_overrides;
create policy contract_assignment_overrides_read on contract_assignment_overrides for select
  to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists contract_assignment_overrides_write on contract_assignment_overrides;
create policy contract_assignment_overrides_write on contract_assignment_overrides for all
  to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on contract_assignment_overrides to authenticated;

-- ---------- 6. contract_events: neue Ereignisart fuer rueckgaengig gemachte
--              Zusammenfuehrungen ---------------------------------------------
alter table contract_events drop constraint if exists contract_events_kind_check;
alter table contract_events add constraint contract_events_kind_check
  check (kind in (
    'created', 'price_change', 'payment_missing', 'payment_resumed',
    'cancelled', 'ended', 'reactivated', 'merged', 'merge_undone', 'dismissed',
    'installment_completed', 'term_ending'
  ));

-- ---------- 7. audit_log: Aktionen fuer Undo und manuelle Zuordnung --------
-- 'cancel'/'reparse' kommen mit dem Importzentrum dazu. Beides als 'update' zu
-- fuehren waere im Log nicht mehr auseinanderzuhalten: der Abbruch beendet
-- einen Lauf vorzeitig, das erneute Einlesen verwirft saemtliche Staging-Zeilen
-- und baut sie neu auf - beides Vorgaenge, die spaeter erklaeren muessen,
-- warum ein Lauf anders aussieht als die Datei, die hochgeladen wurde.
alter table audit_log drop constraint if exists audit_log_action_check;
alter table audit_log add constraint audit_log_action_check
  check (action in (
    'create', 'update', 'delete',
    'archive', 'restore', 'move', 'reorder', 'merge', 'merge_undo', 'reassign',
    'taxonomy_apply', 'suggestion_apply', 'rule_apply',
    'cancel', 'reparse'
  ));

-- ---------- 8. contract_detection_service: Nachtrag zur Vorlagen-Erkennung -
-- transaction_splits.provenance kennt bereits 'contract' (Migration 0015).
-- Kein Schemaeingriff noetig fuer die schluesselbewussten Duplikatvorschlaege
-- aus Abschnitt 19.8 - sie lesen ausschliesslich contracts.contract_key
-- (bereits vorhanden) und contract_events.kind='dismissed' (bereits vorhanden).

-- ============================================================================
-- Fehlende Indizes auf Fremdschluessel- und Mandantenspalten
-- ============================================================================
-- PostgreSQL legt fuer einen Fremdschluessel KEINEN Index auf der verweisenden
-- Seite an - nur die referenzierte Seite braucht zwingend einen (den liefert
-- der Primaerschluessel). Fehlt er auf der verweisenden Spalte, muss jedes
-- Loeschen eines Elternsatzes die Kindtabelle vollstaendig durchsuchen, und
-- zwar EINMAL JE ELTERNSATZ.
--
-- Aufgefallen beim Aufraeumen der Testrueckstaende: 'categories.group_id'
-- zeigte ohne Index auf 'category_groups'. Das Loeschen von 199.644 Gruppen
-- haette rund 212 Milliarden Zeilenzugriffe gekostet und lief nach einer
-- Viertelstunde immer noch. Mit Index waren es Sekunden.
--
-- Der Nutzen reicht weit ueber Loeschvorgaenge hinaus:
-- 'transaction_splits.category_id' ohne Index bedeutet, dass "alle Buchungen
-- dieser Kategorie" - eine Kernabfrage jeder Auswertung - immer die gesamte
-- Tabelle liest.
--
-- 'household_id' ist der Mandantenschluessel dieses Schemas: JEDE Abfrage
-- filtert darauf (Row Level Security), und die Kaskade beim Loeschen eines
-- Haushalts laeuft ebenfalls darueber.
create index if not exists account_balance_snapshots_household_idx on account_balance_snapshots (household_id);
create index if not exists budget_allocations_category_id_idx on budget_allocations (category_id);
create index if not exists budget_allocations_household_idx on budget_allocations (household_id);
create index if not exists budget_rollovers_category_id_idx on budget_rollovers (category_id);
create index if not exists budget_rollovers_household_idx on budget_rollovers (household_id);
create index if not exists categories_archived_by_idx on categories (archived_by);
create index if not exists categories_group_id_idx on categories (group_id);
create index if not exists category_groups_archived_by_idx on category_groups (archived_by);
create index if not exists classification_decisions_category_id_idx on classification_decisions (category_id);
create index if not exists classification_rules_action_category_id_idx on classification_rules (action_category_id);
create index if not exists contracts_category_id_idx on contracts (category_id);
create index if not exists debt_draws_household_idx on debt_draws (household_id);
create index if not exists debt_fees_household_idx on debt_fees (household_id);
create index if not exists debt_rate_periods_household_idx on debt_rate_periods (household_id);
create index if not exists debt_special_repayments_household_idx on debt_special_repayments (household_id);
create index if not exists dish_ingredients_household_idx on dish_ingredients (household_id);
create index if not exists import_rows_household_idx on import_rows (household_id);
create index if not exists meals_household_idx on meals (household_id);
create index if not exists payees_default_category_id_idx on payees (default_category_id);
create index if not exists project_calculation_snapshots_household_idx on project_calculation_snapshots (household_id);
create index if not exists project_cashflow_assumptions_household_idx on project_cashflow_assumptions (household_id);
create index if not exists project_cost_actuals_household_idx on project_cost_actuals (household_id);
create index if not exists project_cost_items_household_idx on project_cost_items (household_id);
create index if not exists project_cost_schedule_entries_household_idx on project_cost_schedule_entries (household_id);
create index if not exists project_cost_sections_household_idx on project_cost_sections (household_id);
create index if not exists project_eligibility_profiles_household_idx on project_eligibility_profiles (household_id);
create index if not exists project_financing_sources_household_idx on project_financing_sources (household_id);
create index if not exists project_funding_check_runs_household_idx on project_funding_check_runs (household_id);
create index if not exists project_funding_matches_household_idx on project_funding_matches (household_id);
create index if not exists project_goal_assumptions_household_idx on project_goal_assumptions (household_id);
create index if not exists project_recurring_impacts_household_idx on project_recurring_impacts (household_id);
create index if not exists project_scenarios_household_idx on project_scenarios (household_id);
create index if not exists reconciliations_household_idx on reconciliations (household_id);
create index if not exists recurring_rules_category_id_idx on recurring_rules (category_id);
create index if not exists savings_goals_category_id_idx on savings_goals (category_id);
create index if not exists shopping_items_household_idx on shopping_items (household_id);
create index if not exists shopping_lists_household_idx on shopping_lists (household_id);
create index if not exists shopping_purchase_links_household_idx on shopping_purchase_links (household_id);
create index if not exists subscriptions_category_id_idx on subscriptions (category_id);
create index if not exists transaction_splits_category_id_idx on transaction_splits (category_id);
create index if not exists transfer_links_household_idx on transfer_links (household_id);

-- ============================================================================
-- Persistente, haushaltsweit serielle KI-Aufgaben
-- ============================================================================
-- Die bisherigen KI-Einstiegspunkte hatten unterschiedliche Lebenszyklen:
-- Klassifikation lief im Worker, Kategorien- und Vertragspruefungen dagegen im
-- HTTP-Request. ai_tasks ist die gemeinsame, geraeteuebergreifende
-- Warteschlange. Genau eine Zeile je Haushalt darf laufen; wartende Aufgaben
-- bleiben in der Reihenfolge ihrer monotonen queue_order erhalten.

create sequence if not exists ai_tasks_queue_order_seq;

create table if not exists ai_tasks (
  id                       uuid primary key default gen_random_uuid(),
  household_id             uuid not null references households(id) on delete cascade
                             default current_household_id(),
  task_type                text not null check (task_type in (
                             'category_suggestions',
                             'transaction_classification',
                             'contract_duplicate_crosscheck',
                             'contract_transaction_check',
                             'project_cost_estimate'
                           )),
  status                   text not null default 'queued' check (status in (
                             'queued', 'running', 'awaiting_confirmation',
                             'completed', 'failed', 'cancelled'
                           )),
  queue_order              bigint not null default nextval('ai_tasks_queue_order_seq'),
  request_id               text,
  input                    jsonb not null default '{}'::jsonb,
  result                   jsonb,
  result_summary           jsonb not null default '{}'::jsonb,
  route_path               text not null,
  current_stage            text,
  progress_current         int not null default 0 check (progress_current >= 0),
  progress_total           int check (progress_total is null or progress_total >= 0),
  status_message           text,
  model_name               text,
  error_message            text,
  cancel_requested         boolean not null default false,
  cancel_requested_at      timestamptz,
  attempt_count            int not null default 0 check (attempt_count >= 0),
  category_operation_id    uuid references category_operations(id) on delete set null,
  classification_run_id    uuid references classification_runs(id) on delete set null,
  created_by               uuid references auth.users(id) on delete set null,
  created_at               timestamptz not null default now(),
  started_at               timestamptz,
  completed_at             timestamptz,
  heartbeat_at             timestamptz,
  updated_at               timestamptz not null default now()
);

-- Der Kostenvorschlag lief frueher synchron am globalen KI-Status vorbei.
-- Als normaler Queue-Typ bleibt er nun bei Navigation/Reload sichtbar und
-- traegt denselben Ereignis- und Fehlerverlauf wie alle anderen KI-Aufgaben.
alter table ai_tasks drop constraint if exists ai_tasks_task_type_check;
alter table ai_tasks add constraint ai_tasks_task_type_check check (task_type in (
  'category_suggestions',
  'transaction_classification',
  'contract_duplicate_crosscheck',
  'contract_transaction_check',
  'project_cost_estimate'
));
-- Additiv fuer die bereits laufende Entwicklungsdatenbank: Zeitpunkt der
-- Abbruchbitte. `updated_at` taugt dafuer nicht - jeder Fortschrittsschritt
-- schreibt es neu. Ohne eigenen Zeitstempel kann die Oberflaeche nicht sagen,
-- wie lange ein "Abbruch angefordert" schon aussteht, und der Nutzer nicht
-- entscheiden, ob er die Aufgabe hart beenden will.
alter table ai_tasks add column if not exists cancel_requested_at timestamptz;
comment on column ai_tasks.cancel_requested_at is
  'Zeitpunkt der Abbruchbitte. Basis fuer die Anzeige "Abbruch angefordert vor X" '
  'und fuer den harten Abbruch, wenn der laufende Modellschritt nicht endet.';

-- Additiv fuer die bereits laufende Entwicklungsdatenbank. Ein technischer
-- Modellfehler ist kein leerer, anwendbarer Preview und wird deshalb als
-- eigener Zustand samt maschinen- und menschenlesbarer Ursache gehalten.
alter table project_cost_ai_operations
  add column if not exists generation_status text not null default 'success',
  add column if not exists error_code text,
  add column if not exists error_message text;
alter table project_cost_ai_operations
  drop constraint if exists project_cost_ai_operations_status_check;
alter table project_cost_ai_operations
  add constraint project_cost_ai_operations_status_check
    check (status in ('preview', 'applied', 'expired', 'failed'));
alter table project_cost_ai_operations
  drop constraint if exists project_cost_ai_operations_generation_status_check;
alter table project_cost_ai_operations
  add constraint project_cost_ai_operations_generation_status_check
    check (generation_status in (
      'success', 'no_suggestions', 'timeout', 'invalid_response', 'model_error'
    ));

create unique index if not exists ai_tasks_request_id_uniq
  on ai_tasks (household_id, request_id) where request_id is not null;
create unique index if not exists ai_tasks_one_running_uniq
  on ai_tasks (household_id) where status = 'running';
-- Ein Doppelklick soll keine zwei identischen Modelllaeufe erzeugen. Andere
-- Aufgabentypen duerfen gleichzeitig warten und werden danach seriell bedient.
create unique index if not exists ai_tasks_one_open_type_uniq
  on ai_tasks (household_id, task_type) where status in ('queued', 'running');
create index if not exists ai_tasks_household_queue_idx
  on ai_tasks (household_id, status, queue_order);
create index if not exists ai_tasks_household_created_idx
  on ai_tasks (household_id, created_at desc);

create table if not exists ai_task_events (
  id             bigint generated always as identity primary key,
  task_id        uuid not null references ai_tasks(id) on delete cascade,
  household_id   uuid not null references households(id) on delete cascade,
  level          text not null default 'info' check (level in ('info', 'warning', 'error')),
  stage          text,
  message        text not null,
  details        jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);
create index if not exists ai_task_events_task_idx
  on ai_task_events (household_id, task_id, created_at, id);

-- Entscheidungen bleiben unveraenderliche Nachweise. Die optionale Referenz
-- macht nun zusaetzlich nachvollziehbar, aus welcher Queue-Aufgabe sie stammen.
alter table classification_decisions add column if not exists ai_task_id uuid
  references ai_tasks(id) on delete set null;
create index if not exists classification_decisions_ai_task_idx
  on classification_decisions (household_id, ai_task_id, created_at desc)
  where ai_task_id is not null;

-- Modellversuche und Kostenvorschau lassen sich damit aus dem sichtbaren
-- Aufgabenverlauf direkt bis zur technischen Telemetrie verfolgen. Alte
-- Zeilen bleiben bewusst NULL statt nach Zeitstempel geraten zu werden.
alter table llm_calls add column if not exists ai_task_id uuid
  references ai_tasks(id) on delete set null;
create index if not exists llm_calls_ai_task_idx
  on llm_calls (household_id, ai_task_id, created_at)
  where ai_task_id is not null;
alter table project_cost_ai_operations add column if not exists ai_task_id uuid
  references ai_tasks(id) on delete set null;
create index if not exists project_cost_ai_operations_ai_task_idx
  on project_cost_ai_operations (household_id, ai_task_id)
  where ai_task_id is not null;

alter table ai_tasks enable row level security;
drop policy if exists ai_tasks_read on ai_tasks;
create policy ai_tasks_read on ai_tasks for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ai_tasks_write on ai_tasks;
create policy ai_tasks_write on ai_tasks for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table ai_task_events enable row level security;
drop policy if exists ai_task_events_read on ai_task_events;
create policy ai_task_events_read on ai_task_events for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ai_task_events_insert on ai_task_events;
create policy ai_task_events_insert on ai_task_events for insert to authenticated
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update on ai_tasks to authenticated;
grant select, insert on ai_task_events to authenticated;
grant usage, select on sequence ai_tasks_queue_order_seq to authenticated;
grant usage, select on sequence ai_task_events_id_seq to authenticated;


-- ============================================================================
-- Preismodul Lebensmittel: Laeden, Preissichtungen, Bewertungspreis
-- ============================================================================
-- Bisher galt: eine Zeile in ingredient_prices je Zutat, ein Preis, fertig.
-- Das beantwortet die Frage "was ist diese Mahlzeit wert?" und die Frage "was
-- kostet das bei Aldi gerade im Angebot?" mit DERSELBEN Zahl. Wer den
-- Angebotspreis eintraegt, bewertet jedes Gericht mit dieser Zutat dauerhaft
-- zu billig; wer den Normalpreis eintraegt, plant die Einkaufswoche zu teuer.
--
-- Aufloesung: ingredient_prices BLEIBT wie es ist, samt Unique-Index auf
-- (household_id, lower(name)). Die eine Zahl dort heisst ab hier
-- BEWERTUNGSPREIS und ist weiterhin die einzige, aus der Gerichts- und
-- Wochenkosten gerechnet werden. Darunter kommt ein anhaengendes Log von
-- Sichtungen. Dieselbe Aufteilung wie contracts.amount_minor neben
-- contract_amount_history: der Rechenkern liest eine flache Spalte, die
-- Vielfalt steht eine Ebene tiefer.
--
-- Damit bleiben load_price_lookup, cost_for, purchase_for, load_dish_costs und
-- used_in_dishes unveraendert - und alle Bestandszeilen behalten mit
-- price_source = 'manual' exakt ihren heutigen Wert. Am Tag der Migration
-- springt kein Gerichtspreis und keine Wochensumme.
--
-- Ein Lebensmittelpreis ist keine Eigenschaft der Zutat, sondern eine
-- SICHTUNG: "am 05.08. bei Aldi, Eigenmarke, 0,89 fuer 500 g". Zwei
-- Sichtungen widersprechen sich nicht, deshalb ein Log statt weiterer
-- Preisspalten. Genau diese Wiederholung ueber die Zeit ist spaeter die
-- Grundlage fuer Preisverlauf und Angebotszyklen.
-- ============================================================================

create table if not exists grocery_stores (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),
  -- Kette: "Aldi", "Rewe". location ist die Filiale und bleibt meist leer -
  -- noetig nur, weil Rewe- und Edeka-Maerkte ueberwiegend inhabergefuehrt sind
  -- und eigene Preise machen. Getrennte Spalten, damit "Rewe insgesamt teurer"
  -- und "die Filiale Bahnhofstrasse ist guenstiger" beide auswertbar bleiben.
  name         text not null,
  location     text,
  store_kind   text not null default 'supermarkt'
                 check (store_kind in ('discounter', 'supermarkt', 'biomarkt',
                                       'markt', 'online', 'sonstige')),
  is_archived  boolean not null default false,
  note         text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
-- Kleine Stammtabelle statt Freitext je Sichtung: "Rewe", "REWE" und
-- "Rewe " waeren sonst drei getrennte Preisreihen, und keine davon zeigt
-- einen Ladenvergleich. shopping_lists.store bleibt bewusst Freitext und wird
-- NICHT zwangsmigriert - vergangene Einkaufslisten sollen sich nicht
-- rueckwirkend aendern.
create unique index if not exists grocery_stores_household_name_idx
  on grocery_stores (household_id, lower(name), lower(coalesce(location, '')));

create table if not exists ingredient_price_observations (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),

  -- Zuordnung ueber den NAMEN, kein Fremdschluessel auf ingredient_prices.
  -- Dieselbe Entscheidung wie dort und aus demselben Grund - zusaetzlich
  -- ueberlebt der Verlauf so das Loeschen einer Preiszeile, und der
  -- bestehende DELETE-Endpunkt bleibt so harmlos, wie sein Kommentar sagt.
  -- ingredient_key ist exakt lower(trim(name)), also die Form, die auch
  -- grocery_pricing_service.load_price_lookup als Schluessel benutzt.
  ingredient_key  text not null,
  ingredient_name text not null,

  -- Achse 1: Ort. NULL heisst ehrlich "irgendwo gesehen" - so eine Sichtung
  -- zaehlt fuer den Preisverlauf, nur nicht fuer den Ladenvergleich.
  store_id     uuid references grocery_stores(id) on delete set null,

  -- Achse 2: Produkt. Zwei Merkmale statt einem, weil "Rewe Bio" Eigenmarke
  -- UND Bio ist und "Alnatura" Marke UND Bio - in einer Spalte ginge eine der
  -- beiden Aussagen verloren. brand_tier = 'unknown' heisst "nicht
  -- angegeben", ausdruecklich nicht "Eigenmarke": einer leeren Zelle eine
  -- Aussage zu unterstellen waere genau das Raten, das der Rechenkern an
  -- keiner Stelle tut.
  brand        text,
  quality_tier text not null default 'standard'
                 check (quality_tier in ('basic', 'standard', 'organic')),
  brand_tier   text not null default 'unknown'
                 check (brand_tier in ('private_label', 'branded', 'unknown')),

  -- Achse 3: Zeit. offer_valid_from ist die wichtigere der beiden Grenzen:
  -- nur mit dem BEGINN laesst sich spaeter erkennen, ob etwas alle vier oder
  -- alle sieben Wochen im Angebot ist. Ein Angebot ohne Ende ist kein
  -- Angebot, sondern der neue Normalpreis - und wuerde nach Ablauf still
  -- weiterrechnen; deshalb die Bedingung statt bloss einer Konvention.
  observed_on      date not null default current_date,
  is_promotion     boolean not null default false,
  offer_valid_from date,
  offer_valid_to   date,
  constraint ingredient_price_observations_offer_needs_end
    check (not is_promotion or offer_valid_to is not null),
  constraint ingredient_price_observations_offer_range
    check (offer_valid_from is null or offer_valid_to is null
           or offer_valid_from <= offer_valid_to),

  -- Preis gilt fuer pack_quantity pack_unit, exakt wie in ingredient_prices.
  price_minor   bigint not null check (price_minor >= 0),
  pack_quantity numeric(10,2) not null default 1 check (pack_quantity > 0),
  pack_unit     text,

  -- base_quantity/base_family setzt der Server aus
  -- grocery_pricing_service.to_base_amount. base_family ist FREITEXT und kein
  -- Enum: to_base_amount gibt fuer nicht umrechenbare Einheiten die
  -- normalisierte Einheit selbst zurueck ('pkg', 'dose', 'bund'). Die zu
  -- einem 'sonstige' zusammenzufassen wuerde Dose und Bund vergleichbar
  -- machen - dieselbe erfundene Gleichsetzung, die cost_for verweigert.
  base_quantity numeric(14,4) not null check (base_quantity > 0),
  base_family   text not null,
  -- Grundpreis in Millionstel Cent je Basiseinheit. In ganzen Cent gerechnet
  -- waeren 0,199 ct/g schlicht 0 und jeder Vergleich sinnlos. Generiert, damit
  -- er nicht gegen price_minor/base_quantity auseinanderlaufen kann.
  unit_price_micro bigint generated always as
    (round(price_minor * 1000000::numeric / base_quantity)::bigint) stored,

  source text not null default 'manual'
    check (source in ('manual', 'excel', 'shopping_list', 'migration')),
  -- Woher genau: Dateiname des Imports, ID der Einkaufsliste. Reiner Beleg.
  source_ref text,
  -- Erkennungswert derselben Sichtung ueber mehrere Importe hinweg. Damit ist
  -- ein versehentlich doppelt eingelesener Wochenstand folgenlos, waehrend
  -- eine neue Woche mit denselben Preisen sehr wohl neue Zeilen ergibt (der
  -- Wert enthaelt observed_on und offer_valid_from).
  fingerprint text,
  note       text,
  created_at timestamptz not null default now()
);
create index if not exists ingredient_price_observations_lookup_idx
  on ingredient_price_observations (household_id, ingredient_key, observed_on desc);
create index if not exists ingredient_price_observations_cheapest_idx
  on ingredient_price_observations
     (household_id, ingredient_key, base_family, unit_price_micro);
create index if not exists ingredient_price_observations_store_idx
  on ingredient_price_observations (household_id, store_id, observed_on desc)
  where store_id is not null;
create unique index if not exists ingredient_price_observations_fingerprint_idx
  on ingredient_price_observations (household_id, fingerprint)
  where fingerprint is not null;

comment on table ingredient_price_observations is
  'Anhaengendes Log von Preissichtungen. Kein UPDATE vorgesehen: eine Korrektur ist eine neue Zeile, ein Tippfehler wird geloescht. Der Bewertungspreis wird daraus abgeleitet und in ingredient_prices geschrieben, nicht hier gehalten.';

-- ---------------------------------------------------------------------------
-- ingredient_prices wird ausdruecklich Traegerin des Bewertungspreises
-- ---------------------------------------------------------------------------
-- Die Leiter, in dieser Reihenfolge: 'manual' = der Nutzer hat die Zahl
-- getippt, sie wird von keinem Import und keiner Strategie angefasst (die
-- Sperre gehoert in die WHERE-Klausel des UPDATE, nicht in ein Python-if).
-- 'pinned' = der Nutzer hat EINE Sichtung festgenagelt. 'derived' = die
-- Strategie waehlt. Default 'manual', damit jede Bestandszeile unangetastet
-- bleibt und die Migration nachweislich nichts verschiebt.
alter table ingredient_prices
  add column if not exists price_source text not null default 'manual'
    check (price_source in ('manual', 'pinned', 'derived')),
  add column if not exists price_strategy text
    check (price_strategy is null or price_strategy in ('latest', 'cheapest')),
  add column if not exists preferred_store_id uuid
    references grocery_stores(id) on delete set null,
  add column if not exists quality_preference text
    check (quality_preference is null
           or quality_preference in ('any', 'prefer_organic', 'organic_only')),
  -- Einheitenfamilie, in der diese Zutat geplant wird. Liegen Sichtungen in
  -- mehreren Familien vor (500 g Mehl neben 1 Pkg Mehl), ist ein
  -- Grundpreisvergleich ueber die Familiengrenze hinweg geraten - dann
  -- entscheidet diese Spalte, und ohne sie gewinnt die Familie der juengsten
  -- Sichtung.
  add column if not exists planning_base_family text,
  -- Welche Sichtung den aktuellen Wert stellt. Macht die Zahl rueckverfolgbar:
  -- "1,89 - Aldi, gesehen am 05.08." statt einer Zahl ohne Herkunft.
  add column if not exists price_observation_id uuid
    references ingredient_price_observations(id) on delete set null,
  add column if not exists price_observed_on date,
  -- Keine Sichtung mehr innerhalb der Altersgrenze: der letzte Wert BLEIBT
  -- stehen und wird nur als alt gekennzeichnet. Eine stille 0 waere falsch,
  -- und Loeschen wuerde jedes Gericht unbewertet machen, das gestern noch
  -- eine Schaetzung hatte.
  add column if not exists price_is_stale boolean not null default false;

create index if not exists ingredient_prices_observation_idx
  on ingredient_prices (price_observation_id)
  where price_observation_id is not null;

-- Haushaltsweite Vorgaben fuer die Ableitung. Bewusst nur ZWEI Strategien:
-- "zuletzt gesehen" und "guenstigster". Ein Median oder Mittelwert lieferte
-- eine Zahl, zu der es keine Sichtung gibt - nicht belegbar und damit
-- unvereinbar mit dem Rest des Moduls.
create table if not exists grocery_price_settings (
  household_id       uuid primary key references households(id) on delete cascade
                       default current_household_id(),
  default_strategy   text not null default 'latest'
                       check (default_strategy in ('latest', 'cheapest')),
  preferred_store_id uuid references grocery_stores(id) on delete set null,
  -- Ab wann eine Sichtung nicht mehr als aktuell gilt. Sie wird dadurch nicht
  -- geloescht und auch nicht ungueltig, sie verliert nur den Anspruch,
  -- "der aktuelle Preis" zu sein.
  max_age_days       int not null default 180 check (max_age_days between 14 and 730),
  updated_at         timestamptz not null default now()
);

alter table grocery_stores                enable row level security;
alter table ingredient_price_observations enable row level security;
alter table grocery_price_settings        enable row level security;

drop policy if exists grocery_stores_read on grocery_stores;
create policy grocery_stores_read on grocery_stores for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists grocery_stores_write on grocery_stores;
create policy grocery_stores_write on grocery_stores for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists ingredient_price_observations_read on ingredient_price_observations;
create policy ingredient_price_observations_read on ingredient_price_observations
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ingredient_price_observations_write on ingredient_price_observations;
create policy ingredient_price_observations_write on ingredient_price_observations
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

drop policy if exists grocery_price_settings_read on grocery_price_settings;
create policy grocery_price_settings_read on grocery_price_settings for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists grocery_price_settings_write on grocery_price_settings;
create policy grocery_price_settings_write on grocery_price_settings for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

-- Nur select/insert/delete auf dem Log: es ist anhaengend, ein UPDATE waere
-- eine stille Geschichtsfaelschung. Ein Tippfehler bleibt loeschbar.
grant select, insert, delete on ingredient_price_observations to authenticated;
grant select, insert, update, delete on grocery_stores, grocery_price_settings
  to authenticated;


-- ============================================================================
-- Echte Tokenwerte in llm_calls statt einer Schaetzung
-- ============================================================================
-- Bisher stand in llm_calls nur tokens_estimated = len(Prompt) // 4 - eine
-- Schaetzung der EINGABE. Erzeugte Tokens und der Denkanteil von
-- Reasoning-Modellen fehlten vollstaendig, obwohl LM Studio sie in jeder
-- Antwort mitliefert (usage.prompt_tokens, usage.completion_tokens,
-- usage.completion_tokens_details.reasoning_tokens). Ohne diese Zahlen laesst
-- sich weder eine Tokenrate berechnen noch beurteilen, ob ein Aufruf am Prompt
-- oder am lauten Denken haengt.
--
-- Alle drei Spalten sind bewusst NULL-faehig und OHNE Default: fehlt die
-- Angabe in der Antwort (aelterer Server, Fehlerfall vor der Antwort,
-- Zeitueberschreitung), bleibt die Zelle leer. Eine 0 waere eine Behauptung,
-- die die Antwort nicht deckt, und wuerde jeden Mittelwert verfaelschen.
-- tokens_estimated bleibt unveraendert bestehen, damit Bestandszeilen und
-- bestehende Auswertungen weiter lesbar sind.
alter table llm_calls
  add column if not exists prompt_tokens int,
  add column if not exists completion_tokens int,
  add column if not exists reasoning_tokens int;

comment on column llm_calls.prompt_tokens is
  'Vom Server gemeldete Eingabetokens (usage.prompt_tokens). NULL = die '
  'Antwort hat den Wert nicht geliefert; nicht geschaetzt und nicht geraten.';
comment on column llm_calls.completion_tokens is
  'Vom Server gemeldete Ausgabetokens (usage.completion_tokens), inklusive '
  'des Denkanteils. NULL = nicht gemeldet.';
comment on column llm_calls.reasoning_tokens is
  'Denkanteil der Ausgabe (usage.completion_tokens_details.reasoning_tokens). '
  'NULL = nicht gemeldet bzw. Modell ohne lautes Denken - ausdruecklich etwas '
  'anderes als eine gemeldete 0.';
comment on column llm_calls.tokens_estimated is
  'Historische Schaetzung der Eingabe. Bestandszeilen tragen len(Prompt)//4, '
  'neue Zeilen die Budgetschaetzung E (siehe input_tokens_estimated); '
  'massgeblich sind prompt_tokens/completion_tokens.';

-- ============================================================================
-- Budgetzeile je Modellaufruf (relatives KI-Budget)
-- ============================================================================
-- Jeder Aufruf ueber den KI-Kern (services/api/app/llm/gateway.py) schreibt
-- hier, womit gerechnet wurde: wirksames Fenster W samt Herkunft, Eingabe E
-- (exakt ueber den Praefix des Vorgaengers oder geschaetzt), Schaetzabstand A,
-- Antwortreserve R und das tatsaechlich gesendete max_tokens. Ohne diese
-- Zeile liesse sich ein abgeschnittener oder abgelehnter Aufruf nicht
-- nachvollziehen - "4000 Tokens" sagt nichts darueber, wie voll das Fenster
-- war. reason_code traegt die benannten Gruende des Kerns
-- (model_context_unknown, model_input_too_large, model_context_overflow, ...).
alter table llm_calls
  add column if not exists prompt_chars int,
  add column if not exists input_tokens_estimated int,
  add column if not exists estimate_exact boolean,
  add column if not exists estimated_part_tokens int,
  add column if not exists window_tokens int,
  add column if not exists window_loaded_tokens int,
  add column if not exists window_cap_tokens int,
  add column if not exists window_source text,
  add column if not exists reserve_tokens int,
  add column if not exists error_allowance_tokens int,
  add column if not exists max_tokens_sent int,
  add column if not exists chars_per_token numeric(6,3),
  add column if not exists estimate_error numeric(6,4),
  add column if not exists budget_parts jsonb,
  add column if not exists finish_reason text,
  add column if not exists reason_code text,
  add column if not exists http_status int,
  add column if not exists server_target text,
  add column if not exists reasoning_sent text;

-- 'refused' = der Kern hat bewusst NICHT gesendet (Fenster unbekannt, Eingabe
-- passt nicht, KI aus); 'cancelled' = vom Nutzer abgebrochen. Beides ist
-- etwas anderes als ein Fehler des Servers.
alter table llm_calls drop constraint if exists llm_calls_status_check;
alter table llm_calls add constraint llm_calls_status_check
  check (status in ('success', 'error', 'timeout', 'no_model', 'refused', 'cancelled'));

comment on column llm_calls.input_tokens_estimated is
  'E: Eingabe in Token vor dem Aufruf - exakt fuer den Teil, den das Modell im '
  'selben Lauf schon gesehen hat (estimate_exact), sonst geschaetzt.';
comment on column llm_calls.estimated_part_tokens is
  'Geschaetzter Anteil von E; auf ihn wird der Schaetzabstand angewandt.';
comment on column llm_calls.window_tokens is
  'W: wirksames Fenster = min(geladenes Fenster, Kontext hoechstens).';
comment on column llm_calls.window_source is
  'Woher das Fenster stammt, z. B. "/api/v1/models", "Ollama /api/ps".';
comment on column llm_calls.reserve_tokens is
  'R: Antwortreserve = W * answer_reserve_share.';
comment on column llm_calls.error_allowance_tokens is
  'A: Schaetzabstand = geschaetzter Teil * gemessener Schaetzfehler.';
comment on column llm_calls.max_tokens_sent is
  'Gesendetes max_tokens = W - E - A (der ganze freie Platz).';
comment on column llm_calls.budget_parts is
  'Aufschluesselung von E in Token: system, history, tool_results, tool_defs, current.';
comment on column llm_calls.reason_code is
  'Benannter Grund des KI-Kerns (siehe app/llm/reasons.py); NULL = ohne Befund.';
comment on column llm_calls.server_target is
  'scheme://host:port des Modellservers - Schluessel der Messung.';
comment on column llm_calls.reasoning_sent is
  'Gesendeter reasoning_effort; NULL = nichts gesendet (Modellstandard).';

-- ============================================================================
-- Mobile-Redesign: stabiles Paging fuer GET /v1/transactions/page
-- ============================================================================
-- docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5 "Daten/API": die neue mobile
-- Buchungsliste blaettert per Keyset-Cursor statt Offset (app/api/v1/routes/
-- transactions.py::list_transactions_page). Fuer die Standard- und
-- haeufigste Sortierung (sort=date_desc, "Neueste zuerst") muss der Index
-- dieselbe Spaltenreihenfolge wie ORDER BY tragen, inklusive der stabilen
-- Tie-Breaker (created_at, id) - sonst muesste Postgres nach dem Indexzugriff
-- auf booked_date jede Gleichstandsgruppe (gleiches Datum) zusaetzlich per
-- Sortierknoten ordnen, was den Keyset-Vorteil bei tiefen Seiten zunichte
-- macht.
--
-- transactions_household_date_idx (oben, "Ehemals: 0009_analytics_indexes.sql")
-- deckt household_id + booked_date bereits ab, aber ohne die Tie-Breaker.
-- Dieser Index ergaenzt additiv und dient zugleich date_asc (Postgres kann
-- einen absteigend gespeicherten Index rueckwaerts lesen, ohne eigenen
-- aufsteigenden Index). Die uebrigen Sortierungen (amount_desc/asc,
-- payee_asc) bleiben bewusst ohne eigenen Index - ihr Bestand pro Haushalt
-- ist fuer einen Sortierknoten ueber die bereits gefilterten Zeilen klein
-- genug, und ein Ausdrucksindex auf lower(coalesce(payee_label, '')) waere
-- fuer eine A-Z-Sortierung ohne Bereichspraedikat kaum wirksamer als ein Sort.
create index if not exists transactions_household_page_date_idx
  on transactions (household_id, booked_date desc, created_at desc, id desc)
  where not is_archived;

-- ============================================================================
-- Ruecklagen-Ledger: bestaetigte Zweckzuordnung statt kalkulatorischem Stand
-- ============================================================================
-- Der Ledger ist bewusst von Transaktionen und dem Monatsbudget getrennt. Ein
-- Stand entsteht ausschliesslich aus einem bestaetigten Startwert am Vertrag /
-- Sparziel und diesen append-only Bewegungen. Weder ein GET noch eine
-- Bankbuchung erzeugt automatisch eine Bewegung.

create unique index if not exists contracts_household_id_uniq
  on contracts (household_id, id);
create unique index if not exists savings_goals_household_id_uniq
  on savings_goals (household_id, id);
create unique index if not exists transaction_splits_household_id_uniq
  on transaction_splits (household_id, id);
create unique index if not exists contract_occurrences_household_id_uniq
  on contract_occurrences (household_id, id);

create table if not exists reserve_pots (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),
  kind         text not null check (kind in ('free', 'contract', 'goal')),
  contract_id  uuid,
  goal_id      uuid,
  is_active    boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (household_id, id),
  foreign key (household_id, contract_id)
    references contracts (household_id, id) on delete cascade,
  foreign key (household_id, goal_id)
    references savings_goals (household_id, id) on delete cascade,
  check (
    (kind = 'free' and contract_id is null and goal_id is null) or
    (kind = 'contract' and contract_id is not null and goal_id is null) or
    (kind = 'goal' and goal_id is not null and contract_id is null)
  )
);
create unique index if not exists reserve_pots_free_uniq
  on reserve_pots (household_id) where kind = 'free';
create unique index if not exists reserve_pots_contract_uniq
  on reserve_pots (household_id, contract_id) where contract_id is not null;
create unique index if not exists reserve_pots_goal_uniq
  on reserve_pots (household_id, goal_id) where goal_id is not null;

create table if not exists reserve_batches (
  id                     uuid primary key default gen_random_uuid(),
  household_id           uuid not null references households(id) on delete cascade
                           default current_household_id(),
  occurred_on            date not null,
  kind                   text not null check (kind in ('deposit', 'withdrawal', 'reallocation')),
  total_minor            bigint not null check (total_minor > 0),
  note                   text check (note is null or length(note) <= 500),
  created_by             uuid references auth.users(id) on delete set null,
  transaction_split_id   uuid,
  contract_occurrence_id uuid,
  created_at             timestamptz not null default now(),
  unique (household_id, id),
  foreign key (household_id, transaction_split_id)
    references transaction_splits (household_id, id) on delete restrict,
  foreign key (household_id, contract_occurrence_id)
    references contract_occurrences (household_id, id) on delete restrict
);
create unique index if not exists reserve_batches_split_uniq
  on reserve_batches (household_id, transaction_split_id)
  where transaction_split_id is not null;
create unique index if not exists reserve_batches_occurrence_uniq
  on reserve_batches (household_id, contract_occurrence_id)
  where contract_occurrence_id is not null;
create index if not exists reserve_batches_month_idx
  on reserve_batches (household_id, occurred_on desc, created_at desc);

create table if not exists reserve_movements (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),
  batch_id     uuid not null,
  pot_id       uuid not null,
  amount_minor bigint not null check (amount_minor <> 0),
  created_at   timestamptz not null default now(),
  foreign key (household_id, batch_id)
    references reserve_batches (household_id, id) on delete cascade,
  foreign key (household_id, pot_id)
    references reserve_pots (household_id, id) on delete restrict,
  unique (batch_id, pot_id)
);
create index if not exists reserve_movements_pot_idx
  on reserve_movements (household_id, pot_id, created_at desc);

create table if not exists reserve_month_decisions (
  id           uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
                 default current_household_id(),
  pot_id       uuid not null,
  year         int not null check (year between 2000 and 2200),
  month        int not null check (month between 1 and 12),
  decision     text not null check (decision = 'skip'),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  foreign key (household_id, pot_id)
    references reserve_pots (household_id, id) on delete cascade,
  unique (household_id, pot_id, year, month)
);

-- Ein freier Topf je Haushalt; historische, bestaetigte Startwerte bleiben in
-- ihren Quellobjekten und werden nicht als Bewegung dupliziert.
insert into reserve_pots (household_id, kind)
select h.id, 'free' from households h
on conflict do nothing;
insert into reserve_pots (household_id, kind, contract_id)
select c.household_id, 'contract', c.id from contracts c
where c.direction = 'expense' and c.frequency in ('quarterly', 'semiannual', 'yearly')
on conflict do nothing;
insert into reserve_pots (household_id, kind, goal_id)
select g.household_id, 'goal', g.id from savings_goals g
on conflict do nothing;

alter table reserve_pots enable row level security;
alter table reserve_batches enable row level security;
alter table reserve_movements enable row level security;
alter table reserve_month_decisions enable row level security;

drop policy if exists reserve_pots_read on reserve_pots;
create policy reserve_pots_read on reserve_pots for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists reserve_pots_write on reserve_pots;
create policy reserve_pots_write on reserve_pots for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

drop policy if exists reserve_batches_read on reserve_batches;
create policy reserve_batches_read on reserve_batches for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists reserve_batches_write on reserve_batches;
create policy reserve_batches_write on reserve_batches for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

drop policy if exists reserve_movements_read on reserve_movements;
create policy reserve_movements_read on reserve_movements for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists reserve_movements_write on reserve_movements;
create policy reserve_movements_write on reserve_movements for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

drop policy if exists reserve_month_decisions_read on reserve_month_decisions;
create policy reserve_month_decisions_read on reserve_month_decisions for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists reserve_month_decisions_write on reserve_month_decisions;
create policy reserve_month_decisions_write on reserve_month_decisions for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update, delete on reserve_pots, reserve_batches,
  reserve_movements, reserve_month_decisions to authenticated;

-- Cross-row invariants cannot be expressed as a CHECK on either table. The
-- deferred constraint triggers inspect the final transaction state, after all
-- movement rows of a batch exist, and reject mismatched sums/signs atomically.
create or replace function validate_reserve_batch_movements()
returns trigger
language plpgsql
as $$
declare
  v_batch_id uuid;
  v_kind text;
  v_total bigint;
  v_sum bigint;
  v_positive bigint;
  v_negative bigint;
begin
  if tg_table_name = 'reserve_batches' then
    v_batch_id := coalesce(new.id, old.id);
  else
    v_batch_id := coalesce(new.batch_id, old.batch_id);
  end if;

  select kind, total_minor into v_kind, v_total
  from reserve_batches where id = v_batch_id;
  if not found then
    return null;
  end if;

  select coalesce(sum(amount_minor), 0),
         coalesce(sum(amount_minor) filter (where amount_minor > 0), 0),
         coalesce(sum(abs(amount_minor)) filter (where amount_minor < 0), 0)
  into v_sum, v_positive, v_negative
  from reserve_movements where batch_id = v_batch_id;

  if (v_kind = 'deposit' and not (v_sum = v_total and v_negative = 0))
     or (v_kind = 'withdrawal' and not (v_sum = -v_total and v_positive = 0))
     or (v_kind = 'reallocation'
         and not (v_sum = 0 and v_positive = v_total and v_negative = v_total)) then
    raise exception using
      errcode = '23514',
      message = 'RESERVE_BATCH_MOVEMENTS_INVALID';
  end if;
  return null;
end;
$$;

drop trigger if exists reserve_batches_validate_movements on reserve_batches;
create constraint trigger reserve_batches_validate_movements
  after insert or update on reserve_batches
  deferrable initially deferred
  for each row execute function validate_reserve_batch_movements();
drop trigger if exists reserve_movements_validate_batch on reserve_movements;
create constraint trigger reserve_movements_validate_batch
  after insert or update or delete on reserve_movements
  deferrable initially deferred
  for each row execute function validate_reserve_batch_movements();

-- ============================================================================
-- Ehemals: 0002_reimbursements.sql
-- ============================================================================
-- Gesundheit & Erstattung. Private Gesundheitsbelege brauchen eine eigene,
-- nachvollziehbare Fachdomaene: Rechnungszahlung, Einreichung,
-- Versicherungsentscheidung und Bankgutschrift sind getrennte Tatsachen. Ein
-- einzelnes `abgerechnet`-Boolean kann diese Zustaende nicht abbilden.
--
-- Steht bewusst am Dateiende, nicht thematisch weiter oben. Zwei
-- Reihenfolgebedingungen erzwingen das:
--   * Die zusammengesetzten Fremdschluessel auf transactions(id, household_id)
--     brauchen transactions_id_household_uniq - das entsteht erst im DO-Block
--     weiter oben in dieser Datei.
--   * Der ai_tasks-Aufgabentyp `health_document_extraction` muss NACH dem
--     Block stehen, der ai_tasks_task_type_check mit fuenf Werten neu setzt.
--     Stuende er davor, verschwaende der Wert still: ai_tasks enthaelt keine
--     Zeile dieses Typs, das add constraint scheitert also nicht, und der
--     Fehler faellt erst beim naechsten Dokumentupload auf.
-- ============================================================================

create table if not exists health_insured_persons (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  display_name  text not null check (length(btrim(display_name)) between 1 and 120),
  birth_date    date,
  linked_user_id uuid references auth.users(id) on delete set null,
  is_active     boolean not null default true,
  created_by    uuid references auth.users(id) on delete set null default auth.uid(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (id, household_id)
);
create unique index if not exists health_insured_persons_name_uniq
  on health_insured_persons(household_id, lower(display_name));

-- Gesundheitszugriff ist enger als die allgemeine Haushaltsmitgliedschaft.
-- Owner duerfen die Freigaben administrieren; alle anderen sehen nur Personen,
-- fuer die eine explizite Freigabe besteht. Beim Anlegen einer Person erzeugt
-- die API eine Admin-Freigabe fuer den Ersteller.
create table if not exists health_access_grants (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  insured_person_id uuid not null,
  user_id           uuid not null references auth.users(id) on delete cascade,
  permission        text not null default 'manage'
                      check (permission in ('read', 'manage')),
  granted_by        uuid references auth.users(id) on delete set null default auth.uid(),
  created_at        timestamptz not null default now(),
  foreign key (insured_person_id, household_id)
    references health_insured_persons(id, household_id) on delete cascade,
  unique (household_id, insured_person_id, user_id)
);

create or replace function health_can_access_person(p_person_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select current_household_role() = 'owner'
    or exists (
      select 1 from health_access_grants g
      where g.household_id = current_household_id()
        and g.insured_person_id = p_person_id
        and g.user_id = auth.uid()
    )
$$;

create or replace function health_can_manage_person(p_person_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select current_household_role() = 'owner'
    or exists (
      select 1 from health_access_grants g
      where g.household_id = current_household_id()
        and g.insured_person_id = p_person_id
        and g.user_id = auth.uid()
        and g.permission = 'manage'
    )
$$;

create table if not exists reimbursement_payers (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  payer_type    text not null check (payer_type in ('pkv', 'beihilfe', 'supplementary', 'other')),
  name          text not null check (length(btrim(name)) between 1 and 160),
  is_active     boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (id, household_id)
);
create unique index if not exists reimbursement_payers_name_uniq
  on reimbursement_payers(household_id, lower(name));

create table if not exists health_expense_cases (
  id                     uuid primary key default gen_random_uuid(),
  household_id           uuid not null references households(id) on delete cascade
                           default current_household_id(),
  insured_person_id      uuid not null,
  provider_name          text not null check (length(btrim(provider_name)) between 1 and 240),
  invoice_number         text,
  invoice_date           date not null,
  treatment_from         date,
  treatment_to           date,
  due_date               date,
  billed_minor           bigint not null check (billed_minor >= 0),
  currency               text not null default 'EUR' check (currency ~ '^[A-Z]{3}$'),
  expected_minor         bigint not null default 0 check (expected_minor >= 0),
  reimbursed_minor       bigint not null default 0 check (reimbursed_minor >= 0),
  deductible_minor       bigint not null default 0 check (deductible_minor >= 0),
  noncovered_minor       bigint not null default 0 check (noncovered_minor >= 0),
  payment_status         text not null default 'unknown'
                           check (payment_status in ('unknown', 'unpaid', 'partial', 'paid', 'not_required')),
  submission_status      text not null default 'not_submitted'
                           check (submission_status in ('not_submitted', 'held_for_bonus', 'ready', 'submitted', 'documents_requested', 'not_planned')),
  decision_status        text not null default 'none'
                           check (decision_status in ('none', 'processing', 'received', 'partial', 'rejected', 'appeal')),
  reimbursement_status   text not null default 'not_expected'
                           check (reimbursement_status in ('not_expected', 'expected', 'partial', 'received', 'mismatch')),
  overall_status         text not null default 'draft'
                           check (overall_status in ('draft', 'action_required', 'waiting', 'reconciled')),
  next_action            text,
  next_action_due        date,
  notes                  text,
  version                int not null default 1 check (version > 0),
  created_by             uuid references auth.users(id) on delete set null default auth.uid(),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  unique (id, household_id),
  unique (id, insured_person_id, household_id),
  foreign key (insured_person_id, household_id)
    references health_insured_persons(id, household_id) on delete restrict,
  check (treatment_to is null or treatment_from is null or treatment_to >= treatment_from),
  check (reimbursed_minor <= billed_minor or billed_minor = 0),
  check (deductible_minor + noncovered_minor <= billed_minor or billed_minor = 0)
);
create index if not exists health_expense_cases_person_date_idx
  on health_expense_cases(household_id, insured_person_id, invoice_date desc);
create index if not exists health_expense_cases_action_idx
  on health_expense_cases(household_id, overall_status, next_action_due)
  where overall_status <> 'reconciled';

create table if not exists reimbursement_documents (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  insured_person_id     uuid not null,
  case_id               uuid,
  document_type         text not null default 'unknown'
                          check (document_type in ('unknown', 'invoice', 'prescription', 'submission_receipt', 'benefit_statement', 'request_for_documents', 'other')),
  original_filename     text not null check (length(original_filename) between 1 and 255),
  object_key            text not null,
  sha256                text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  semantic_fingerprint  text,
  mime_type             text not null,
  size_bytes            bigint not null check (size_bytes > 0),
  page_count            int check (page_count is null or page_count between 1 and 250),
  processing_status     text not null default 'queued'
                          check (processing_status in ('queued', 'extracting', 'needs_ocr', 'needs_review', 'confirmed', 'failed', 'duplicate')),
  extraction_confidence numeric(4,3) check (extraction_confidence between 0 and 1),
  error_code            text,
  error_message         text,
  created_by            uuid references auth.users(id) on delete set null default auth.uid(),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (id, household_id),
  unique (id, insured_person_id, household_id),
  unique (household_id, insured_person_id, sha256),
  unique (household_id, object_key),
  foreign key (insured_person_id, household_id)
    references health_insured_persons(id, household_id) on delete restrict,
  foreign key (case_id, insured_person_id, household_id)
    references health_expense_cases(id, insured_person_id, household_id) on delete restrict
);
create index if not exists reimbursement_documents_case_idx on reimbursement_documents(household_id, case_id);
create index if not exists reimbursement_documents_queue_idx
  on reimbursement_documents(processing_status, created_at)
  where processing_status in ('queued', 'extracting');
-- Zwei Downloads desselben Belegs aus dem Versicherungsportal unterscheiden
-- sich in einzelnen Bytes und damit im sha256. Erst der fachliche Fingerabdruck
-- (Nummer, Datum, Betrag) erkennt sie als denselben Vorgang.
create index if not exists reimbursement_documents_fingerprint_idx
  on reimbursement_documents(household_id, insured_person_id, semantic_fingerprint)
  where semantic_fingerprint is not null;

create table if not exists document_extractions (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  insured_person_id     uuid not null,
  document_id           uuid not null,
  extractor_version     text not null,
  schema_version        text not null,
  model_name            text,
  status                text not null check (status in ('success', 'needs_review', 'failed', 'needs_ocr')),
  confidence            numeric(4,3) check (confidence between 0 and 1),
  structured_data       jsonb not null default '{}'::jsonb,
  field_evidence        jsonb not null default '{}'::jsonb,
  validation_warnings   jsonb not null default '[]'::jsonb,
  confirmed_data        jsonb,
  confirmed_by          uuid references auth.users(id) on delete set null,
  confirmed_at          timestamptz,
  created_at            timestamptz not null default now(),
  foreign key (document_id, insured_person_id, household_id)
    references reimbursement_documents(id, insured_person_id, household_id) on delete cascade
);
create index if not exists document_extractions_document_idx
  on document_extractions(document_id, created_at desc);

create table if not exists reimbursement_submissions (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  insured_person_id     uuid not null,
  case_id               uuid not null,
  payer_id              uuid not null,
  external_reference    text,
  submitted_at          timestamptz,
  decided_at            timestamptz,
  claimed_minor         bigint not null default 0 check (claimed_minor >= 0),
  approved_minor        bigint not null default 0 check (approved_minor >= 0),
  reimbursed_minor      bigint not null default 0 check (reimbursed_minor >= 0),
  deductible_minor      bigint not null default 0 check (deductible_minor >= 0),
  noncovered_minor      bigint not null default 0 check (noncovered_minor >= 0),
  status                text not null default 'draft'
                          check (status in ('draft', 'submitted', 'processing', 'documents_requested', 'decided', 'partial', 'rejected', 'appeal', 'settled')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (id, household_id),
  unique (id, insured_person_id, household_id),
  foreign key (case_id, insured_person_id, household_id)
    references health_expense_cases(id, insured_person_id, household_id) on delete cascade,
  foreign key (payer_id, household_id)
    references reimbursement_payers(id, household_id) on delete restrict
);
create index if not exists reimbursement_submissions_case_idx
  on reimbursement_submissions(household_id, case_id, created_at);

create table if not exists health_transaction_allocations (
  id                    uuid primary key default gen_random_uuid(),
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  insured_person_id     uuid not null,
  case_id               uuid not null,
  submission_id         uuid,
  transaction_id        uuid not null,
  allocation_type       text not null check (allocation_type in ('provider_payment', 'reimbursement')),
  amount_minor          bigint not null check (amount_minor > 0),
  source                text not null default 'manual' check (source in ('manual', 'rule', 'ai')),
  confidence            numeric(4,3) check (confidence between 0 and 1),
  evidence              jsonb not null default '{}'::jsonb,
  confirmed_by          uuid references auth.users(id) on delete set null,
  created_at            timestamptz not null default now(),
  unique (household_id, case_id, transaction_id, allocation_type),
  foreign key (case_id, insured_person_id, household_id)
    references health_expense_cases(id, insured_person_id, household_id) on delete cascade,
  foreign key (submission_id, insured_person_id, household_id)
    references reimbursement_submissions(id, insured_person_id, household_id) on delete cascade,
  foreign key (transaction_id, household_id)
    references transactions(id, household_id) on delete cascade
);
create index if not exists health_transaction_allocations_transaction_idx
  on health_transaction_allocations(household_id, transaction_id);

create or replace function validate_health_transaction_allocation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_transaction_amount bigint;
  v_allocated bigint;
begin
  select amount_minor into v_transaction_amount
  from transactions
  where id = new.transaction_id and household_id = new.household_id;

  if v_transaction_amount is null then
    raise exception using errcode = '23503', message = 'HEALTH_TRANSACTION_NOT_FOUND';
  end if;
  if new.allocation_type = 'provider_payment' and v_transaction_amount >= 0 then
    raise exception using errcode = '23514', message = 'HEALTH_PROVIDER_PAYMENT_MUST_BE_OUTGOING';
  end if;
  if new.allocation_type = 'reimbursement' and v_transaction_amount <= 0 then
    raise exception using errcode = '23514', message = 'HEALTH_REIMBURSEMENT_MUST_BE_INCOMING';
  end if;

  select coalesce(sum(amount_minor), 0) into v_allocated
  from health_transaction_allocations
  where household_id = new.household_id
    and transaction_id = new.transaction_id
    and id <> new.id;

  if v_allocated + new.amount_minor > abs(v_transaction_amount) then
    raise exception using errcode = '23514', message = 'HEALTH_TRANSACTION_OVERALLOCATED';
  end if;
  return new;
end
$$;
drop trigger if exists health_transaction_allocation_guard on health_transaction_allocations;
create trigger health_transaction_allocation_guard
  before insert or update on health_transaction_allocations
  for each row execute function validate_health_transaction_allocation();

create table if not exists health_case_events (
  id                    bigint generated always as identity primary key,
  household_id          uuid not null references households(id) on delete cascade
                          default current_household_id(),
  insured_person_id     uuid not null,
  case_id               uuid not null,
  event_type            text not null,
  summary               text not null check (length(summary) between 1 and 500),
  details               jsonb not null default '{}'::jsonb,
  created_by            uuid references auth.users(id) on delete set null default auth.uid(),
  created_at            timestamptz not null default now(),
  foreign key (case_id, insured_person_id, household_id)
    references health_expense_cases(id, insured_person_id, household_id) on delete cascade
);
create index if not exists health_case_events_case_idx on health_case_events(case_id, created_at);

-- Row Level Security ----------------------------------------------------------

alter table health_insured_persons enable row level security;
alter table health_access_grants enable row level security;
alter table reimbursement_payers enable row level security;
alter table health_expense_cases enable row level security;
alter table reimbursement_documents enable row level security;
alter table document_extractions enable row level security;
alter table reimbursement_submissions enable row level security;
alter table health_transaction_allocations enable row level security;
alter table health_case_events enable row level security;

drop policy if exists health_insured_persons_read on health_insured_persons;
create policy health_insured_persons_read on health_insured_persons for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(id));
drop policy if exists health_insured_persons_insert on health_insured_persons;
create policy health_insured_persons_insert on health_insured_persons for insert to authenticated
  with check (household_id = current_household_id() and can_edit_household());
drop policy if exists health_insured_persons_update on health_insured_persons;
create policy health_insured_persons_update on health_insured_persons for update to authenticated
  using (household_id = current_household_id() and health_can_manage_person(id))
  with check (household_id = current_household_id() and health_can_manage_person(id));
drop policy if exists health_insured_persons_delete on health_insured_persons;
create policy health_insured_persons_delete on health_insured_persons for delete to authenticated
  using (household_id = current_household_id() and health_can_manage_person(id));

drop policy if exists health_access_grants_read on health_access_grants;
create policy health_access_grants_read on health_access_grants for select to authenticated
  using (household_id = current_household_id()
         and (user_id = auth.uid() or current_household_role() = 'owner'));
drop policy if exists health_access_grants_insert on health_access_grants;
create policy health_access_grants_insert on health_access_grants for insert to authenticated
  with check (household_id = current_household_id() and can_edit_household()
              and (user_id = auth.uid() or current_household_role() = 'owner'));
drop policy if exists health_access_grants_update on health_access_grants;
create policy health_access_grants_update on health_access_grants for update to authenticated
  using (household_id = current_household_id() and current_household_role() = 'owner')
  with check (household_id = current_household_id() and current_household_role() = 'owner');
drop policy if exists health_access_grants_delete on health_access_grants;
create policy health_access_grants_delete on health_access_grants for delete to authenticated
  using (household_id = current_household_id() and current_household_role() = 'owner');

drop policy if exists reimbursement_payers_read on reimbursement_payers;
create policy reimbursement_payers_read on reimbursement_payers for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists reimbursement_payers_write on reimbursement_payers;
create policy reimbursement_payers_write on reimbursement_payers for all to authenticated
  using (household_id = current_household_id() and can_edit_household())
  with check (household_id = current_household_id() and can_edit_household());

drop policy if exists health_expense_cases_read on health_expense_cases;
create policy health_expense_cases_read on health_expense_cases for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(insured_person_id));
drop policy if exists health_expense_cases_write on health_expense_cases;
create policy health_expense_cases_write on health_expense_cases for all to authenticated
  using (household_id = current_household_id() and health_can_manage_person(insured_person_id))
  with check (household_id = current_household_id() and health_can_manage_person(insured_person_id));

drop policy if exists reimbursement_documents_read on reimbursement_documents;
create policy reimbursement_documents_read on reimbursement_documents for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(insured_person_id));
drop policy if exists reimbursement_documents_write on reimbursement_documents;
create policy reimbursement_documents_write on reimbursement_documents for all to authenticated
  using (household_id = current_household_id() and health_can_manage_person(insured_person_id))
  with check (household_id = current_household_id() and health_can_manage_person(insured_person_id));

drop policy if exists document_extractions_read on document_extractions;
create policy document_extractions_read on document_extractions for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(insured_person_id));
drop policy if exists document_extractions_write on document_extractions;
create policy document_extractions_write on document_extractions for all to authenticated
  using (household_id = current_household_id() and health_can_manage_person(insured_person_id))
  with check (household_id = current_household_id() and health_can_manage_person(insured_person_id));

drop policy if exists reimbursement_submissions_read on reimbursement_submissions;
create policy reimbursement_submissions_read on reimbursement_submissions for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(insured_person_id));
drop policy if exists reimbursement_submissions_write on reimbursement_submissions;
create policy reimbursement_submissions_write on reimbursement_submissions for all to authenticated
  using (household_id = current_household_id() and health_can_manage_person(insured_person_id))
  with check (household_id = current_household_id() and health_can_manage_person(insured_person_id));

drop policy if exists health_transaction_allocations_read on health_transaction_allocations;
create policy health_transaction_allocations_read on health_transaction_allocations for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(insured_person_id));
drop policy if exists health_transaction_allocations_write on health_transaction_allocations;
create policy health_transaction_allocations_write on health_transaction_allocations for all to authenticated
  using (household_id = current_household_id() and health_can_manage_person(insured_person_id))
  with check (household_id = current_household_id() and health_can_manage_person(insured_person_id));

drop policy if exists health_case_events_read on health_case_events;
create policy health_case_events_read on health_case_events for select to authenticated
  using (household_id = current_household_id() and health_can_access_person(insured_person_id));
drop policy if exists health_case_events_insert on health_case_events;
create policy health_case_events_insert on health_case_events for insert to authenticated
  with check (household_id = current_household_id() and health_can_manage_person(insured_person_id));

grant select, insert, update, delete on
  health_insured_persons, health_access_grants, reimbursement_payers,
  health_expense_cases, reimbursement_documents, document_extractions,
  reimbursement_submissions, health_transaction_allocations
to authenticated;
grant select, insert on health_case_events to authenticated;
grant usage, select on sequence health_case_events_id_seq to authenticated;

-- Die bestehende, haushaltsweit serielle KI-Warteschlange verarbeitet auch
-- Dokumente. Im Task-Input steht ausschliesslich die Dokument-ID; PDF-Bytes
-- und OCR-Text werden nie in Postgres/Redis-Taskdaten dupliziert.
alter table ai_tasks drop constraint if exists ai_tasks_task_type_check;
alter table ai_tasks add constraint ai_tasks_task_type_check check (task_type in (
  'category_suggestions',
  'transaction_classification',
  'contract_duplicate_crosscheck',
  'contract_transaction_check',
  'project_cost_estimate',
  'health_document_extraction'
));

-- In einer vollstaendigen Supabase-Installation wird der private Bucket per
-- Migration angelegt. Schlanke Profile ohne Storage-Dienst ueberspringen
-- diesen Block bewusst; dort greift das lokale, nur ueber die autorisierte API
-- erreichbare Dateisystem-Backend (STORAGE_BACKEND=local).
--
-- Warum die Pruefung auf die SPALTE geht und nicht nur auf die Tabelle: das
-- Image supabase/postgres bringt ein `storage`-Schema samt `buckets` schon
-- mit, aber in einem Rohzustand - die Spalten `public`, `file_size_limit` und
-- `allowed_mime_types` traegt erst der Storage-Dienst beim ersten Start nach.
-- Wer Postgres aus diesem Image ohne Storage betreibt (siehe
-- infra/nas/README.md), hat also eine Tabelle, die es zu geben scheint und
-- doch nicht passt. `to_regclass` allein greift dort daneben und das
-- Einspielen des Schemas bricht auf der letzten Meile ab mit
--   ERROR: column "public" of relation "buckets" does not exist
do $$
begin
  if to_regclass('storage.buckets') is not null
     and exists (
       select 1 from information_schema.columns
       where table_schema = 'storage'
         and table_name = 'buckets'
         and column_name = 'public'
     ) then
    execute $sql$
      insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
      values ('health-documents', 'health-documents', false, 26214400,
              array['application/pdf', 'image/jpeg', 'image/png'])
      on conflict (id) do update set
        public = false,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types
    $sql$;
  end if;
end
$$;

-- ============================================================================
-- Erstattungsfaehigkeit am Buchungsstrom
-- ============================================================================
-- Bis hierher ist die Erstattung dokument-zuerst: ohne hochgeladenen Beleg gibt
-- es keinen Vorgang, und ohne Vorgang weiss nichts im System, dass Geld
-- zurueckkommen muesste. Selbst eine hochgeladene Arztrechnung hilft nicht -
-- sie nennt keinen Erstattungsbetrag, der Vorgang entsteht mit
-- expected_minor = 0, und die Statusableitung verlangt fuer "du bist dran"
-- ausdruecklich expected_minor > 0. Die Erwartung entsteht damit erst aus der
-- Leistungsabrechnung, also erst nachdem die Versicherung entschieden hat.
--
-- Die Marke setzt frueher an: an der Bankbuchung, am Tag der Zahlung.
--
-- Sie ist bewusst KEIN Vorgang. health_expense_cases verlangt eine
-- versicherte Person (not null) - eine Bankbuchung sagt nicht, wer behandelt
-- wurde. Und der Gesundheitsbereich hat eine engere Sichtbarkeit als der
-- Haushalt (health_can_manage_person). Ein Haeckchen in "Alle Buchungen"
-- duerfte fuer Mitglieder ohne Freigabe sonst nicht funktionieren.
--
-- Deshalb traegt die Marke keine Gesundheitsdaten: keine Person, keine
-- Diagnose, keinen Belegtext, kein Freitextfeld. Sie sagt nur "hier ist Geld
-- abgeflossen, davon erwarte ich etwas zurueck, in dieser Hoehe" - alles
-- Tatsachen, die der Buchungsstrom ohnehin hergibt. Sie liegt darum unter der
-- normalen Haushalts-RLS.
--
-- Ebenso bewusst KEINE case_id: die Tatsache "Buchung X gehoert zu Vorgang Y"
-- steht in health_transaction_allocations unter health_can_access_person. Hier
-- ein zweites Mal gespeichert waere sie fuer jedes Mitglied gruppierbar - und
-- die Fremdschluesselpruefung laeuft ohne RLS, ein Editor ohne Freigabe
-- koennte sie setzen und umhaengen. converted_at haelt beides, was gebraucht
-- wird: den Partialindex auf die offenen Marken und die Sperre gegen
-- automatische Ruecknahme. Wer die Freigabe nicht hat, sieht "bereits
-- bearbeitet" statt "Vorgang C7".
create table if not exists transaction_reimbursement_marks (
  id                 uuid primary key default gen_random_uuid(),
  household_id       uuid not null references households(id) on delete cascade
                       default current_household_id(),
  transaction_id     uuid not null,
  -- 'excluded' ist die ausdrueckliche Gegenentscheidung, nicht die Abwesenheit
  -- einer Marke: "hier kommt nichts zurueck, hoer auf zu fragen".
  state              text not null default 'expected'
                       check (state in ('expected', 'excluded')),
  -- 'unknown' ist ein echter Zustand: markiert, Hoehe offen. Als 0 gefuehrt
  -- verschwaende die Buchung aus der offenen Summe.
  expectation_mode   text not null default 'full'
                       check (expectation_mode in ('full', 'quota', 'fixed', 'unknown')),
  expectation_rate   numeric(5,4) check (expectation_rate > 0 and expectation_rate <= 1),
  expectation_minor  bigint check (expectation_minor >= 0),
  -- Die Herkunft ist zugleich der Schutz: 'manual' wird von Regel- und
  -- Kategorielaeufen nie ueberschrieben. Ohne diese Zusage waere jede
  -- Handkorrektur wertlos, weil der naechste Import sie kassiert.
  source             text not null default 'manual'
                       check (source in ('manual', 'rule', 'category')),
  rule_id            uuid references classification_rules(id) on delete set null,
  -- Gesetzt, sobald aus der Marke ein Vorgang geworden ist. Welcher, steht
  -- bewusst nicht hier (siehe Kopfkommentar).
  converted_at       timestamptz,
  created_by         uuid references auth.users(id) on delete set null default auth.uid(),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (household_id, transaction_id),
  foreign key (transaction_id, household_id)
    references transactions(id, household_id) on delete cascade,
  check (expectation_mode <> 'quota' or expectation_rate is not null),
  check (expectation_mode <> 'fixed' or expectation_minor is not null),
  check (source = 'rule' or rule_id is null)
);
create index if not exists transaction_reimbursement_marks_open_idx
  on transaction_reimbursement_marks (household_id, state)
  where state = 'expected' and converted_at is null;
create index if not exists transaction_reimbursement_marks_rule_idx
  on transaction_reimbursement_marks (household_id, rule_id)
  where source = 'rule';

-- Betragsbezogene Invarianten stehen in einer anderen Tabelle und lassen sich
-- nicht als CHECK ausdruecken. Spiegelbild von
-- validate_health_transaction_allocation().
create or replace function validate_transaction_reimbursement_mark()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_amount bigint;
begin
  select amount_minor into v_amount
  from transactions
  where id = new.transaction_id and household_id = new.household_id;

  if v_amount is null then
    raise exception using errcode = '23503', message = 'REIMBURSEMENT_MARK_TRANSACTION_NOT_FOUND';
  end if;
  -- Eine Erstattungserwartung setzt einen Abgang voraus: erwartet wird Geld
  -- fuer etwas, das bezahlt wurde.
  if v_amount >= 0 then
    raise exception using errcode = '23514', message = 'REIMBURSEMENT_MARK_MUST_BE_OUTGOING';
  end if;
  -- Mehr zurueckerwarten als bezahlt wurde ist keine Erwartung, sondern ein
  -- Tippfehler. Trifft den Teilbetragsfall, fuer den 'fixed' gedacht ist.
  if new.expectation_mode = 'fixed' and new.expectation_minor > abs(v_amount) then
    raise exception using errcode = '23514', message = 'REIMBURSEMENT_MARK_EXCEEDS_TRANSACTION';
  end if;
  return new;
end
$$;
drop trigger if exists transaction_reimbursement_mark_guard
  on transaction_reimbursement_marks;
create trigger transaction_reimbursement_mark_guard
  before insert or update on transaction_reimbursement_marks
  for each row execute function validate_transaction_reimbursement_mark();

alter table transaction_reimbursement_marks enable row level security;

drop policy if exists transaction_reimbursement_marks_read on transaction_reimbursement_marks;
create policy transaction_reimbursement_marks_read
  on transaction_reimbursement_marks for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists transaction_reimbursement_marks_write on transaction_reimbursement_marks;
create policy transaction_reimbursement_marks_write
  on transaction_reimbursement_marks for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update, delete on transaction_reimbursement_marks to authenticated;

-- Regelebene der Erstattungsfaehigkeit (docs/ERSTATTUNGSFAEHIGKEIT-PLAN.md,
-- Abschnitt 6). Die Trefferbedingungen bleiben unveraendert - eine Regel
-- entscheidet jetzt zusaetzlich, ob eine getroffene Buchung erstattungsfaehig
-- ist und wie viel zurueckerwartet wird.
--
-- Ausgewertet wird das in einem ZWEITEN, unabhaengigen Durchlauf ueber
-- dieselben Regeln. Die Kategorisierung endet beim ersten Treffer; haenge man
-- die Erstattungsaktion an dieselbe Auswertung, verdeckte eine hoeher
-- priorisierte Kategorieregel jede tiefer stehende Erstattungsregel - und
-- zwar unsichtbar.
alter table classification_rules
  add column if not exists action_reimbursable text,
  add column if not exists action_expectation_mode text,
  add column if not exists action_expectation_rate numeric(5,4),
  add column if not exists action_expectation_minor bigint;
alter table classification_rules drop constraint if exists classification_rules_reimbursable_check;
alter table classification_rules add constraint classification_rules_reimbursable_check
  check (action_reimbursable is null or action_reimbursable in ('expected', 'excluded'));
alter table classification_rules drop constraint if exists classification_rules_expectation_check;
alter table classification_rules add constraint classification_rules_expectation_check
  check (
    action_expectation_mode is null
    or (
      action_expectation_mode in ('full', 'quota', 'fixed', 'unknown')
      and (action_expectation_mode <> 'quota' or action_expectation_rate is not null)
      and (action_expectation_mode <> 'fixed' or action_expectation_minor is not null)
    )
  );
alter table classification_rules drop constraint if exists classification_rules_expectation_rate_check;
alter table classification_rules add constraint classification_rules_expectation_rate_check
  check (action_expectation_rate is null
         or (action_expectation_rate > 0 and action_expectation_rate <= 1));
-- Nur Regeln mit Erstattungsaktion nehmen am zweiten Durchlauf teil.
create index if not exists classification_rules_reimbursable_idx
  on classification_rules (household_id, priority desc)
  where is_active and action_reimbursable is not null;

-- ============================================================================
-- Ehemals: 0016_forecast.sql
-- ============================================================================
-- Liquiditaetsvorschau (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 1).
-- Die Vorschau selbst speichert NICHTS: sie ist eine reine Ableitung aus
-- Konten, Vertraegen, Budget und Ruecklagen. Persistiert werden nur zwei
-- Dinge, die der Nutzer entscheidet und die nirgends sonst hergeleitet werden
-- koennen:
--   * je Konto, ob es als liquide gilt (accounts.forecast_liquidity)
--   * je Haushalt die Rechenannahmen (forecast_settings)
-- ============================================================================

-- Kontoarten sind als Auswahl zu grob: ein Tagesgeldkonto und ein
-- Bausparvertrag sind beide 'savings', aber nur eines davon ist verfuegbar.
-- NULL = Heuristik nach account_type ('checking' und 'cash' zaehlen),
-- true/false = ausdrueckliche Entscheidung des Nutzers, die die Heuristik
-- ueberschreibt. Bewusst eine Spalte an accounts statt einer Liste in den
-- Einstellungen - der Nutzer erwartet sie in der Kontenverwaltung.
alter table accounts add column if not exists forecast_liquidity boolean;

comment on column accounts.forecast_liquidity is
  'Zaehlt dieses Konto zur Liquiditaetsvorschau? NULL = Heuristik nach '
  'account_type (checking, cash), true/false = ausdrueckliche Entscheidung.';

create table if not exists forecast_settings (
  household_id            uuid primary key references households(id) on delete cascade
                            default current_household_id(),
  -- Sicherheitspuffer: der Betrag, unter den der Kontostand nicht fallen soll.
  safety_buffer_minor     bigint not null default 0 check (safety_buffer_minor >= 0),
  -- Woher der variable Monatsansatz kommt. 'max' ist konservativ und fuer eine
  -- Warnfunktion richtig; die endgueltige Wahl faellt gegen echte Zahlen
  -- (Plan Abschnitt 16.1).
  variable_basis          text not null default 'max'
                            check (variable_basis in ('budget', 'history', 'max')),
  variable_lookback_months int not null default 6
                            check (variable_lookback_months between 3 and 24),
  horizon_months          int not null default 24
                            check (horizon_months between 1 and 60),
  updated_at              timestamptz not null default now(),
  updated_by              uuid references auth.users(id)
);

alter table forecast_settings enable row level security;
drop policy if exists forecast_settings_read on forecast_settings;
create policy forecast_settings_read on forecast_settings for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists forecast_settings_write on forecast_settings;
create policy forecast_settings_write on forecast_settings for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update on forecast_settings to authenticated;

comment on table forecast_settings is
  'Rechenannahmen der Liquiditaetsvorschau je Haushalt. Muster wie '
  'contract_settings: ein Datensatz je Haushalt, beim ersten PUT angelegt.';
comment on column forecast_settings.variable_basis is
  'budget = nur Budgetplan, history = nur Durchschnitt der Historie, '
  'max = der groessere der beiden Werte (konservativ).';

-- ---------- Benachrichtigungen zur Vorschau (Plan Stufe 5d) -----------------
-- Push nur bei ZUSTANDSWECHSEL, nie als wiederkehrende Zusammenfassung
-- (Plan Abschnitt 9, letzter Punkt). Die Vorschau selbst speichert weiterhin
-- nichts (Leitsatz 1) - gemerkt wird ausschliesslich der zuletzt GEMELDETE
-- Zustand, denn ohne ihn liesse sich ein Wechsel gar nicht feststellen: der
-- Lauf rechnet die Vorschau jede Nacht neu und wuesste sonst nicht, ob "unter
-- Puffer" neu ist oder seit sechs Wochen so steht.
--
-- Die drei Schalter gehoeren zu forecast_settings und nicht in eine eigene
-- Tabelle - Vorbild contract_settings.notify_* (Vertragsplan Phase 4).

alter table forecast_settings
  add column if not exists notify_shortfall      boolean not null default true,
  add column if not exists notify_below_buffer   boolean not null default true,
  add column if not exists notify_income_missing boolean not null default true;

comment on column forecast_settings.notify_shortfall is
  'Push, wenn die Vorschau erstmals ins Minus rutscht.';
comment on column forecast_settings.notify_below_buffer is
  'Push, wenn die Vorschau unter den Sicherheitspuffer faellt oder ein bisher '
  'unauffaelliger Monat kritisch wird.';
comment on column forecast_settings.notify_income_missing is
  'Push, wenn eine faellige Einnahme ausgeblieben ist.';

create table if not exists forecast_alert_state (
  household_id            uuid primary key references households(id) on delete cascade
                            default current_household_id(),
  -- Genau die Kennzahlen, an denen ein Wechsel haengt. NULL heisst "lag beim
  -- letzten Lauf nicht vor" - und ist damit zugleich die Ruecksetzung: erholt
  -- sich die Lage, wird hier wieder NULL geschrieben, sonst meldete sich
  -- dieselbe Lage nach dem naechsten Absturz nie wieder.
  first_negative_on       date,
  first_below_buffer_on   date,
  quality_level           text
                            check (quality_level in ('good', 'limited', 'insufficient')),
  -- Monate 'JJJJ-MM', deren Endstand unter dem Sicherheitspuffer lag. Als
  -- Liste und nicht als Einzeldatum, weil "ein bisher unauffaelliger Monat
  -- wird kritisch" je Monat getrennt beantwortet werden muss.
  critical_months         text[] not null default '{}',
  -- Vertraege mit direction='income', deren Zahlung zuletzt ausblieb.
  -- Dieselbe Mechanik wie critical_months und aus demselben Grund eine Liste:
  -- der Wechsel gilt je Einnahmequelle. Bewusst NICHT ueber
  -- contract_events.notified_at deduppt - dieser Anker gehoert bereits
  -- notify_contract_alerts, ein zweiter Verbraucher wuerde Nachrichten
  -- gegenseitig verschlucken (siehe Kopf von contract_alerts.py).
  missing_income_contract_ids uuid[] not null default '{}',
  -- Wann zuletzt tatsaechlich etwas gesendet wurde. updated_at dagegen zieht
  -- jeder Lauf hoch, auch der stille - die beiden Zeitpunkte beantworten
  -- verschiedene Fragen ("wann war der letzte Push?" vs. "ist der Zustand
  -- aktuell?").
  notified_at             timestamptz,
  updated_at              timestamptz not null default now()
);

alter table forecast_alert_state enable row level security;
drop policy if exists forecast_alert_state_read on forecast_alert_state;
create policy forecast_alert_state_read on forecast_alert_state for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists forecast_alert_state_write on forecast_alert_state;
create policy forecast_alert_state_write on forecast_alert_state for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update on forecast_alert_state to authenticated;

comment on table forecast_alert_state is
  'Zuletzt gemeldeter Zustand der Liquiditaetsvorschau je Haushalt. '
  'Arbeitsvorrat von notify_forecast_alerts: der Lauf sendet nur, wenn sich '
  'ein Feld gegenueber diesem Stand verschlechtert hat, und schreibt den '
  'beobachteten Zustand danach unabhaengig vom Versand zurueck.';


-- =============================================================================
-- Versicherungen, Phase P1: Policenregister (docs/VERSICHERUNGEN-PLAN.md)
-- =============================================================================
-- Vier Tabellen, nicht sechzehn: insurance_lines und insurance_coverage_keys
-- (global, Migrationsgut), insurance_policies (das Fachobjekt) und
-- insurance_settings. Personen, Objekte, Deckungen, Beitragsanpassungen,
-- Schaeden und Bedarfsabgleich entstehen erst in spaeteren Phasen und stehen
-- bewusst NICHT hier.
--
-- Leitsatz des Moduls: dieser Teil modelliert kein Geld. Der Beitrag lebt in
-- contracts; an der Police steht hoechstens ein Informationsbetrag eines
-- fremden Zahlers (Arbeitgeber, Entgeltumwandlung, Partner ausserhalb des
-- Haushalts). Ein selbst bezahlter Beitrag kann hier gar nicht entstehen -
-- es gibt kein Feld dafuer.

-- ---------- 0. Zusammengesetzte Eindeutigkeit an contracts -------------------
-- insurance_policies referenziert contracts(id, household_id), damit eine
-- fremde Vertrags-UUID strukturell nicht durchgeht. Der zusammengesetzte
-- Unique fehlte an contracts als einziger der referenzierten Tabellen
-- (accounts/savings_goals/assets_and_debts/transactions haben ihn seit dem
-- Vorhaben-Modul, 0001_schema.sql:1111-1125).
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'contracts_id_household_uniq') then
    alter table contracts add constraint contracts_id_household_uniq unique (id, household_id);
  end if;
end $$;

-- ---------- 1. insurance_lines: Spartenkatalog, global ----------------------
-- Muster 1:1 von contract_provider_templates: global, select fuer alle,
-- Schreibrechte ausdruecklich entzogen. Das "grant select" allein genuegt
-- nicht, weil "alter default privileges" jeder neuen Tabelle bereits
-- select/insert/update/delete gibt - erst das revoke macht den Katalog
-- schreibgeschuetzt.
create table if not exists insurance_lines (
  line_key              text primary key
                          check (line_key ~ '^[a-z][a-z0-9_]{2,48}$'),
  risk_field            text not null check (risk_field in (
                          'health','income','liability','property',
                          'mobility','retirement','legal','other')),
  gdv_sparte            text check (gdv_sparte is null or gdv_sparte ~ '^[0-9]{3}$'),
  label                 text not null check (length(btrim(label)) between 1 and 80),
  -- Der Kern des Auftrags: ein Satz in der Liste, mehrere im Detail. Beide
  -- Felder beschreiben die SPARTE, nie die konkrete Police des Nutzers, und
  -- sie bewerten nicht.
  purpose_short         text not null check (length(btrim(purpose_short)) between 10 and 200),
  purpose_long          text not null check (length(btrim(purpose_long)) between 10 and 1200),
  -- Bewusst "typically": die Spalten sagen etwas ueber die Sparte, nicht ueber
  -- die Police. Sobald Deckungsbausteine erfasst sind (P4), ueberschreibt die
  -- Police sie - dann wechselt auch die Beschriftung in der Oberflaeche.
  typically_covers      text[] not null default '{}',
  typically_not_covers  text[] not null default '{}',
  -- Der Platzhalter 'unknown' traegt weder Bedarfsstufe noch Quelle. Ohne
  -- diese Spalte waere die wichtigste Zeile des Seeds - jede uebernommene
  -- Police startet auf ihr - nur mit erfundenen Pflichtwerten einfuegbar.
  is_placeholder        boolean not null default false,
  -- Bedarfsstufe S0..S4, NICHT mit dem Phasenbuchstaben P: die Schreibweise
  -- waere zeichengleich mit den Phasennamen des Plans.
  need_tier             text check (need_tier in ('S0','S1','S2','S3','S4')),
  need_reason           text,
  -- Ohne Pruefdatum veraltet eine Wertung still. Die Oberflaeche zeigt es an.
  source_url            text,
  source_checked_on     date,
  constraint insurance_lines_editorial_complete check (
    is_placeholder
    or (need_tier is not null and need_reason is not null
        and source_url is not null and source_checked_on is not null
        and length(btrim(purpose_long)) >= 40)
  ),
  -- DEFAULT 'none', NICHT 'term_end': term_end rechnet ab der
  -- Hauptfaelligkeit und ist ohne sie sinnlos. Eine Zeile, die den Anker im
  -- Seed vergisst, soll KEINE Frist erzeugen statt einer falschen.
  default_notice_anchor text not null default 'none' check (default_notice_anchor in
                          ('term_end','main_due','period_end','fixed_date',
                           'month_end','per_event','none')),
  -- Grundlage des Ankers: Gesetz, Versicherungsbedingungen oder
  -- Einzelvertrag. PFLICHTFELD OHNE DEFAULT - ohne diese Spalte stuende die
  -- AKB-Ruheversicherung in derselben Spalte wie die VVG-Fristen.
  default_anchor_basis_kind text not null check (default_anchor_basis_kind in
                          ('statute','policy_terms','contract')),
  -- Paragraph 11 Abs. 3 VVG gibt genau dieses Intervall vor: "nicht weniger
  -- als einen Monat und nicht mehr als drei Monate". Loest die Tage-Kruecke
  -- aus contracts.cancellation_period_days ab. GENAU EINE Fristspalte im
  -- Katalog; der weitere Bereich 0-36 gehoert an die Police, wo eine
  -- abweichende Vereinbarung zulaessig ist.
  default_notice_period_months smallint check (default_notice_period_months between 1 and 3),
  -- Sichtbare Vorbelegung der Hauptfaelligkeit als 'MM-DD'. Damit steht die
  -- Kalenderjahr-ANNAHME als Annahme im Katalog, statt als vermeintliche
  -- Tatsache in einem Datum zu verschwinden.
  default_main_due_md   text check (default_main_due_md ~ '^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'),
  -- fixed_date ist nur fuer das Versicherungskennzeichen belegt (Moped,
  -- 28.02.). Kfz laeuft ueber term_end + default_main_due_md; der 30.11.
  -- wird gerechnet, nie gespeichert.
  default_fixed_month   int check (default_fixed_month between 1 and 12),
  default_fixed_day     int check (default_fixed_day between 1 and 31),
  default_waiting_days  int check (default_waiting_days between 0 and 3650),
  default_min_term_months     int check (default_min_term_months between 1 and 600),
  default_renewal_term_months int check (default_renewal_term_months between 1 and 120),
  -- Fristtyp-Diskriminator. PFLICHTFELD OHNE DEFAULT, und zwar buchstaeblich
  -- ohne default-Klausel: daran haengt, ob Paragraph 193 BGB das Fristende
  -- nach hinten schiebt. Fuer 'kuendigungsfrist' NICHT (BGH III ZR 172/04),
  -- fuer die uebrigen Erklaerungsfristen schon. Ein Default
  -- 'kuendigungsfrist' waere ausgerechnet der nicht-schiebende Zweig: eine
  -- vergessene Widerrufsfrist rechnete still einen Tag zu frueh.
  default_deadline_kind text not null
                          check (default_deadline_kind in
                            ('kuendigungsfrist','widerrufsfrist','anfechtungsfrist',
                             'klagefrist','zahlungsfrist')),
  -- Rechtsgrundlage des Sonderkuendigungsrechts nach einem Schaden. Haengt an
  -- der Sparte: Sach/Kasko Paragraph 92 VVG, Haftpflicht Paragraph 111 VVG,
  -- Kranken keins - dort greift ohnehin das Erstattungsmodul.
  default_claim_termination_basis text not null default 'vvg_92'
                          check (default_claim_termination_basis in
                            ('vvg_92','vvg_111','contract','none')),
  constraint insurance_lines_fixed_date_pair check (
    default_notice_anchor <> 'fixed_date'
    or (default_fixed_month is not null and default_fixed_day is not null)
  ),
  -- term_end ohne Vorbelegung der Hauptfaelligkeit ist zulaessig (Hausrat),
  -- fixed_date mit Vorbelegung dagegen widerspruechlich.
  constraint insurance_lines_main_due_md_anchor check (
    default_main_due_md is null or default_notice_anchor in ('term_end','main_due')
  ),
  -- Nenner des Vollstaendigkeitsbalkens. Spartenabhaengig, deshalb
  -- Katalogdatum: ohne diese Spalte waere "7 von 12" eine erfundene Zahl.
  completeness_fields   text[] not null default '{}',
  -- Sonderkuendigungsrecht der Sparte; steuert die Fristlaenge nach einer
  -- Beitragsanpassung (PKV zwei Monate nach Paragraph 205 IV statt einem
  -- nach Paragraph 40).
  special_law           text not null default 'vvg40' check (special_law in
                          ('vvg40','vvg205','none')),
  -- Doppelversicherungserkennung ohne Sonderlogik im Code: "Glasbruch steckt
  -- schon in Hausrat und Gebaeude" ist eine Katalogaussage.
  covered_by_keys       text[] not null default '{}',
  requires_object_kind  text check (requires_object_kind in
                          ('vehicle','property','pet','device','bike')),
  default_category_key  text,   -- categories.semantic_key, bewusst ohne FK
  attr_schema           jsonb not null default '{}'::jsonb,
  attr_model_name       text,
  sort_order            int not null default 100,
  is_active             boolean not null default true,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists insurance_lines_field_idx
  on insurance_lines (risk_field, sort_order) where is_active;

alter table insurance_lines enable row level security;
drop policy if exists insurance_lines_read on insurance_lines;
create policy insurance_lines_read on insurance_lines
  for select to authenticated using (true);
grant select on insurance_lines to authenticated;
revoke insert, update, delete on insurance_lines from authenticated;

-- ---------- 2. insurance_coverage_keys: Bausteinkatalog, global -------------
create table if not exists insurance_coverage_keys (
  coverage_key  text primary key
                  check (coverage_key ~ '^[a-z][a-z0-9_]{2,48}$'),
  label         text not null,
  purpose_short text not null,
  applies_to    text[] not null default '{}',   -- line_keys
  sort_order    int not null default 100,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

alter table insurance_coverage_keys enable row level security;
drop policy if exists insurance_coverage_keys_read on insurance_coverage_keys;
create policy insurance_coverage_keys_read on insurance_coverage_keys
  for select to authenticated using (true);
grant select on insurance_coverage_keys to authenticated;
revoke insert, update, delete on insurance_coverage_keys from authenticated;

-- ---------- 3. insurance_policies: die Police -------------------------------
create table if not exists insurance_policies (
  id                        uuid primary key default gen_random_uuid(),
  household_id              uuid not null references households(id) on delete cascade
                              default current_household_id(),

  -- GELDQUELLE, drei Faelle:
  --   (a) Der Haushalt zahlt  -> contract_id PFLICHT. Auch bei manueller
  --       Zahlung oder nicht angebundenem Konto: dann wird ein Vertrag ohne
  --       Kontoanbindung gefuehrt, NICHT ein Betrag an der Police.
  --   (b) Arbeitgeber/Partner/Dritter zahlt -> external_premium_minor +
  --       external_frequency + premium_payer. Reiner Informationsbetrag,
  --       ausdruecklich nicht Teil der Fixkosten.
  --   (c) beitragsfrei oder unbekannt -> alles null.
  -- Es gibt hier bewusst KEINEN Zahlerwert 'self': er waere nur zusammen mit
  -- external_premium_minor setzbar, und den verbietet die NAND-Bedingung
  -- neben contract_id. Fall (a) wird nicht positiv erzwungen, sondern sein
  -- Gegenteil unmoeglich gemacht.
  contract_id               uuid,
  external_premium_minor    bigint check (external_premium_minor > 0),
  external_frequency        text check (external_frequency in
                              ('weekly','monthly','quarterly','semiannual','yearly','single')),
  -- 'self_payroll' = Entgeltumwandlung: umgewandeltes Bruttoentgelt, das ueber
  -- die Gehaltsabrechnung laeuft und nie als Lastschrift erscheint. Als
  -- 'employer' gefuehrt waere es sachlich falsch. 'partner' meint einen
  -- Partner AUSSERHALB des Haushalts - zahlt ein Haushaltsmitglied, ist das
  -- Fall (a) mit einem Vertrag ohne Kontoanbindung.
  premium_payer             text check (premium_payer in
                              ('employer','self_payroll','partner','other')),
  premium_payer_note        text check (length(premium_payer_note) <= 200),
  -- Anteil dieser Police am Vertragsbeitrag, in Basispunkten. Wirksam erst ab
  -- P4 (Buendel); bis dahin traegt jede Police 10000 und der Trigger ist
  -- trivial erfuellt.
  premium_share_bp          int not null default 10000
                              check (premium_share_bp between 1 and 10000),

  -- Identitaet
  line_key                  text not null default 'unknown'
                              references insurance_lines(line_key) on update cascade,
  label                     text check (length(btrim(label)) between 1 and 120),
  insurer_name              text not null check (length(btrim(insurer_name)) between 1 and 160),
  insurer_template_key      text references contract_provider_templates(semantic_key)
                              on update cascade,
  -- Echte Versicherungsscheinnummer. NICHT contracts.contract_key: der ist
  -- fuer die Buchungserkennung gebaut und wird vom Abgleichsmotor
  -- ueberschrieben.
  policy_number             text check (length(btrim(policy_number)) between 1 and 80),
  policy_number_norm        text generated always as
                              (nullif(upper(regexp_replace(coalesce(policy_number,''),
                                                           '[^A-Za-z0-9]', '', 'g')), ''))
                              stored,
  customer_number           text,
  tariff_name               text,
  purpose_note              text check (length(purpose_note) <= 2000),

  -- KEIN parent_policy_id in diesem Block. Die Buendelhierarchie entsteht
  -- vollstaendig in P4. Wer diesen Block kopiert, baut kein Buendel - genau
  -- das ist beabsichtigt.

  -- Gesellschaftswechsel mit Historienmitnahme. Ohne diese Felder bricht die
  -- Beitragszeitreihe beim Wechsel ab.
  predecessor_policy_id     uuid,
  previous_insurer_name     text,
  previous_policy_number    text,
  prior_cover_since         date,

  -- Lebenszyklus. 'entered_in_error' statt Loeschen.
  --   paid_up   beitragsfrei gestellt: laeuft mit reduzierter Leistung weiter
  --   dormant   ruhend gestellt (Kfz-Ruheversicherung, PKV-Anwartschaft)
  --   suspended voruebergehend ausgesetzt, ohne der beiden Fachformen
  status                    text not null default 'active' check (status in (
                              'draft','applied','active','suspended','paid_up','dormant',
                              'cancelled','ended','entered_in_error')),
  paid_up_benefit_minor     bigint check (paid_up_benefit_minor >= 0),
  dormant_until             date,
  cancelled_on              date,
  cancellation_reason       text check (cancellation_reason in (
                              'term_end','premium_increase','claim','risk_ceased',
                              'switch','double_insured','no_longer_needed','insurer',
                              'vehicle_sold','vehicle_deregistered','other')),
  -- AUFFANGFALL, nicht Regelfall. Paragraph 96 Abs. 2 S. 2 VVG knuepft die
  -- Erwerberfrist primaer an den Erwerb und nur bei fehlender Kenntnis an die
  -- Kenntnis. Greift deshalb nur mit dokumentiertem Grund.
  event_known_on            date,
  event_known_reason        text check (event_known_reason in
                              ('no_knowledge_at_acquisition','insurer_notice','other')),

  -- Vereinbarte Dynamik: eine planmaessige Erhoehung ist keine Erhoehung im
  -- Sinne des Paragraphen 40 VVG und darf keine Rueckfrage ausloesen.
  dynamic_agreed            boolean not null default false,

  -- Zustellweg. Steuert den Versandpuffer bis zum empfohlenen Handlungsdatum
  -- und die Zugangsfiktion nach Paragraph 130 BGB.
  service_channel           text not null default 'post' check (service_channel in
                              ('post','einschreiben','email','kuendigungsbutton','fax')),
  -- Bundesland des EMPFAENGERS, nicht des Nutzers: Paragraph 193 BGB stellt
  -- auf den am Erklaerungsort anerkannten Feiertag ab.
  recipient_state           text check (recipient_state ~ '^(BW|BY|BE|BB|HB|HH|HE|MV|NI|NW|RP|SL|SN|ST|SH|TH)$'),

  -- Zeit. main_due_on ist das Ankerdatum, nicht starts_on.
  starts_on                 date,
  ends_on                   date,
  main_due_on               date,
  -- HERKUNFT DES ANKERDATUMS. Ohne diese Spalte kann main_due_on nicht
  -- zwischen "steht so im Versicherungsschein" und "ist die uebliche Annahme
  -- dieser Sparte" unterscheiden: beide saehen identisch aus, bekaemen
  -- denselben Countdown und loesten denselben Push aus.
  -- Nur 'user_confirmed' erzeugt Countdown, Ampel und Push;
  -- 'catalog_suggested' erzeugt eine Rueckfrage.
  main_due_source           text not null default 'not_computable'
                              check (main_due_source in
                                ('user_confirmed','catalog_suggested','not_computable')),
  main_due_confirmed_at     timestamptz,
  main_due_confirmed_by     uuid references auth.users(id) on delete set null,
  -- DEFAULT 'none', NICHT 'term_end': der CHECK weiter unten verlangt bei
  -- 'term_end' ein main_due_on. Mit dem Default 'term_end' waere jede Police
  -- ohne Hauptfaelligkeit unspeicherbar - also genau die, die P1 verspricht.
  notice_anchor             text not null default 'none' check (notice_anchor in
                              ('term_end','main_due','period_end','fixed_date',
                               'month_end','per_event','none')),
  -- KEIN Spaltendefault, aus demselben Grund wie bei deadline_kind:
  -- 'policy_terms' als Default entwertete jede gesetzliche Frist zur
  -- Bedingungsfrist, 'statute' waere die gefaehrlichere Luege. Der
  -- BEFORE-Trigger setzt den Wert aus dem Katalog; per UPDATE ist er pro
  -- Police ueberschreibbar.
  anchor_basis_kind         text not null
                              check (anchor_basis_kind in
                                ('statute','policy_terms','contract')),
  notice_period_months      int check (notice_period_months between 0 and 36),
  notice_fixed_month        int check (notice_fixed_month between 1 and 12),
  notice_fixed_day          int check (notice_fixed_day between 1 and 31),
  -- HERKUNFT DER FRISTLAENGE, die zweite Unsicherheitsquelle derselben Frist.
  -- 'derived_from_days' (Umrechnung aus cancellation_period_days) blockiert
  -- eine bestaetigte Frist; 'catalog_suggested' nicht, denn dort ist die
  -- Fristlaenge rechtlich hergeleitet.
  notice_terms_source       text not null default 'catalog_suggested'
                              check (notice_terms_source in
                                ('user_confirmed','catalog_suggested','derived_from_days')),
  notice_terms_confirmed_at timestamptz,
  notice_terms_confirmed_by uuid references auth.users(id) on delete set null,
  -- Fristtyp, vom Trigger aus dem Katalog gesetzt. KEIN Spaltendefault.
  deadline_kind             text not null
                              check (deadline_kind in
                                ('kuendigungsfrist','widerrufsfrist','anfechtungsfrist',
                                 'klagefrist','zahlungsfrist')),
  min_term_months           int check (min_term_months between 1 and 600),
  renewal_term_months       int check (renewal_term_months between 1 and 120),
  waiting_period_days       int check (waiting_period_days between 0 and 3650),

  -- Leistung
  sum_insured_minor         bigint check (sum_insured_minor >= 0),
  sum_insured_kind          text check (sum_insured_kind in (
                              'sliding_new_value','fixed_new_value','current_value',
                              'first_risk','unlimited')),
  deductible_minor          bigint check (deductible_minor >= 0),
  underinsurance_waiver     boolean,

  -- Beteiligte und Kontakt
  broker_name               text,
  broker_phone              text,
  broker_emergency_phone    text,
  claims_phone              text,
  -- Bewusst KEINE portal_url. Ein Kundenportal ist der Ort, an dem die Police
  -- liegt - ein Feld dafuer waere der Fundort-Link unter anderem Namen, und
  -- genau der ist ausgeschlossen. Kontaktwege bleiben Telefonnummern; sie
  -- fuehren zu einem Menschen, nicht zu einer Datei.

  -- Spartenspezifisches, im Dienst gegen insurance_lines.attr_schema geprueft
  attrs                     jsonb not null default '{}'::jsonb,

  -- Gesundheitsschutz: serverseitig aus insurance_lines.risk_field gesetzt,
  -- nie vom Client. Traegt ab P4 die zusaetzliche RLS-Bedingung.
  is_health_scoped          boolean not null default false,

  data_source               text not null default 'manual' check (data_source in
                              ('manual','from_contract')),
  line_confirmed_at         timestamptz,
  -- "Sparte nicht in der Liste": nimmt die Police aus der Aufgabenliste,
  -- ohne sie falsch zuzuordnen.
  line_deferred_at          timestamptz,
  note                      text,
  -- MASCHINENGESCHRIEBEN: Dedup-Anker des Fristen-Pushes. Aus dem
  -- Versionstrigger ausgenommen, sonst entwertet der Nachtlauf das If-Match
  -- des Nutzers.
  notified_at               timestamptz,
  version                   int not null default 1,
  created_by                uuid references auth.users(id) on delete set null default auth.uid(),
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),

  unique (id, household_id),
  -- SPALTENWEISE. Ohne '(contract_id)' setzt PostgreSQL beide Spalten des
  -- Fremdschluessels auf null, also auch household_id (not null), und
  -- DELETE /v1/contracts/{id} scheitert an einer Not-Null-Verletzung. Genau
  -- dieser Fehler ist im Vorhaben-Modul schon einmal passiert und steht in
  -- 0001_schema.sql:2598-2605 im Klartext. Seit PostgreSQL 15 verfuegbar,
  -- supabase/config.toml steht auf major_version = 15.
  foreign key (contract_id, household_id)
    references contracts(id, household_id) on delete set null (contract_id),
  foreign key (predecessor_policy_id, household_id)
    references insurance_policies(id, household_id)
    on delete set null (predecessor_policy_id),

  -- --- Die drei Geldfaelle, als drei Bedingungen ----------------------
  -- (1) NAND: nie Vertrag UND Fremdbetrag. Ueberlebt das
  --     'on delete set null (contract_id)': faellt der Vertrag weg, rutscht
  --     die Police in Fall (c), statt das Loeschen zu blockieren.
  constraint insurance_policies_premium_source_excl
    check (not (contract_id is not null and external_premium_minor is not null)),
  -- (2) Paarbindung: Betrag, Rhythmus und Zahler gemeinsam oder gar nicht.
  --     Ein Betrag ohne benannten Zahler waere wieder ein Geldwert im
  --     Versicherungsmodul, den niemand zuordnen kann.
  constraint insurance_policies_external_triple
    check (
      (external_premium_minor is null and external_frequency is null
       and premium_payer is null)
      or
      (external_premium_minor is not null and external_frequency is not null
       and premium_payer is not null)
    ),
  -- (3) Die Notiz haengt am Zahler, nicht in der Luft.
  constraint insurance_policies_payer_note_pair
    check (premium_payer_note is null or premium_payer is not null),

  constraint insurance_policies_cancel_pair
    check (status <> 'cancelled' or cancelled_on is not null),
  -- Beitragsfrei gestellt heisst: es fliesst kein Beitrag. Der CHECK sichert
  -- nur die Policenseite; die Vertragsseite laesst sich nicht mitfassen, weil
  -- ein CHECK ueber zwei Tabellen nicht formulierbar ist.
  constraint insurance_policies_paid_up_pair
    check (status <> 'paid_up' or external_premium_minor is null),
  constraint insurance_policies_fixed_date_pair
    check (notice_anchor <> 'fixed_date'
           or (notice_fixed_month is not null and notice_fixed_day is not null)),
  -- Der Anker term_end rechnet ab der Hauptfaelligkeit. Ohne main_due_on gibt
  -- es keine Frist, sondern eine Rueckfrage.
  constraint insurance_policies_term_end_anchor
    check (notice_anchor <> 'term_end' or main_due_on is not null),

  -- --- Herkunft der Termine -------------------------------------------
  -- Kein Datum ohne Herkunft und keine Herkunft ohne Datum.
  constraint insurance_policies_main_due_source_pair
    check ((main_due_on is null) = (main_due_source = 'not_computable')),
  -- "Bestaetigt" ist eine Handlung mit Zeitstempel und Urheber, kein Etikett.
  -- Ohne diese Bedingung koennte ein Import 'user_confirmed' schreiben, ohne
  -- dass je ein Nutzer zugestimmt hat.
  constraint insurance_policies_main_due_confirmed
    check (main_due_source <> 'user_confirmed'
           or (main_due_confirmed_at is not null and main_due_confirmed_by is not null)),
  constraint insurance_policies_notice_terms_confirmed
    check (notice_terms_source <> 'user_confirmed'
           or (notice_terms_confirmed_at is not null
               and notice_terms_confirmed_by is not null)),

  -- Der Auffangfall des Paragraphen 96 Abs. 2 S. 2 VVG braucht seine
  -- Begruendung, sonst verdraengt ein beliebiges Kenntnisdatum den Erwerb.
  constraint insurance_policies_event_known_pair
    check ((event_known_on is null) = (event_known_reason is null)),

  -- Nur predecessor_policy_id - parent_policy_id existiert in P1 nicht.
  constraint insurance_policies_self_reference
    check (predecessor_policy_id is distinct from id)
);

create index if not exists insurance_policies_household_status_idx
  on insurance_policies (household_id, status, line_key);
create index if not exists insurance_policies_contract_idx
  on insurance_policies (household_id, contract_id) where contract_id is not null;
-- Treibt die Aufgabenliste "Hauptfaelligkeit erfassen/bestaetigen".
create index if not exists insurance_policies_main_due_source_idx
  on insurance_policies (household_id, main_due_source)
  where main_due_source in ('catalog_suggested','not_computable');
create index if not exists insurance_policies_predecessor_idx
  on insurance_policies (household_id, predecessor_policy_id)
  where predecessor_policy_id is not null;
create index if not exists insurance_policies_deadline_idx
  on insurance_policies (household_id, main_due_on)
  where status in ('active','suspended','paid_up','dormant','cancelled');
-- P1-FASSUNG: ohne parent_policy_id-Bedingung, weil es bis P4 keine
-- Kindpolicen gibt. Eine zweite Police mit derselben Nummer beim selben
-- Versicherer wird bis dahin mit 409 abgewiesen.
create unique index if not exists insurance_policies_number_uniq
  on insurance_policies (household_id, lower(insurer_name), policy_number_norm)
  where policy_number_norm is not null
    and status <> 'entered_in_error';

-- ---------- 4. Versionstrigger ----------------------------------------------
create or replace function bump_insurance_version() returns trigger
language plpgsql as $$
begin
  -- notified_at schreibt der Nachtlauf. Es darf das If-Match des Nutzers
  -- nicht entwerten.
  if (to_jsonb(new) - 'version' - 'updated_at' - 'notified_at')
     is distinct from
     (to_jsonb(old) - 'version' - 'updated_at' - 'notified_at') then
    new.version := old.version + 1; new.updated_at := now();
  else
    new.version := old.version; new.updated_at := old.updated_at;
  end if;
  return new;
end $$;

drop trigger if exists insurance_policies_bump_version on insurance_policies;
create trigger insurance_policies_bump_version before update on insurance_policies
  for each row execute function bump_insurance_version();

-- ---------- 5. Fachtrigger: Serverwissen setzen -----------------------------
create or replace function validate_insurance_policy() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_field text;
  v_kind  text;
  v_basis text;
  v_sum   int;
  v_confirm text := coalesce(current_setting('insurance.main_due_confirm', true), '');
  v_terms   text := coalesce(current_setting('insurance.notice_terms_confirm', true), '');
begin
  -- (a) is_health_scoped ist Serverwissen. deadline_kind und
  --     anchor_basis_kind haben bewusst KEINEN Spaltendefault; der
  --     BEFORE-Trigger fuellt sie aus dem Katalog, bevor die Not-Null-Pruefung
  --     greift. Beim UPDATE bleibt ein vom Nutzer gesetzter Wert stehen.
  select risk_field, default_deadline_kind, default_anchor_basis_kind
    into v_field, v_kind, v_basis
    from insurance_lines where line_key = new.line_key;
  new.is_health_scoped := (v_field = 'health');
  if new.deadline_kind is null then
    new.deadline_kind := coalesce(v_kind, 'kuendigungsfrist');
  end if;
  if new.anchor_basis_kind is null then
    new.anchor_basis_kind := coalesce(v_basis, 'policy_terms');
  end if;

  -- (b) HERKUNFT DES ANKERDATUMS ist Serverwissen - und der Trigger SETZT
  --     sie, er ergaenzt sie nicht. Ohne die folgenden Zuweisungen koennte
  --     ein Client beim INSERT 'user_confirmed' behaupten, und ein UPDATE
  --     ohne Datumsaenderung koennte die Herkunft nachtraeglich hochstufen.
  if tg_op = 'INSERT' then
    new.main_due_source       := 'catalog_suggested';
    new.main_due_confirmed_at := null;
    new.main_due_confirmed_by := null;
  else
    new.main_due_source       := old.main_due_source;
    new.main_due_confirmed_at := old.main_due_confirmed_at;
    new.main_due_confirmed_by := old.main_due_confirmed_by;
  end if;

  --     Bestaetigt wird NUR ueber den ausdruecklichen Bestaetigungspfad, der
  --     die Sitzungsvariable setzt - nicht ueber "irgendein UPDATE mit
  --     gesetzter auth.uid()". Sonst stempelt der zweite Uebernahmelauf ein
  --     Datum als bestaetigt, das niemand angesehen hat.
  if v_confirm = 'user' and new.main_due_on is not null then
    new.main_due_source       := 'user_confirmed';
    new.main_due_confirmed_at := now();
    new.main_due_confirmed_by := auth.uid();
  end if;

  --     Kein Datum, keine Herkunft - das raeumt auch eine fruehere
  --     Bestaetigung ab, und zwar absichtlich: wer das Datum loescht, hat
  --     nichts mehr bestaetigt.
  if new.main_due_on is null then
    new.main_due_source       := 'not_computable';
    new.main_due_confirmed_at := null;
    new.main_due_confirmed_by := null;
  end if;

  -- (c) HERKUNFT DER FRISTLAENGE, nach demselben Muster.
  if tg_op = 'UPDATE' then
    new.notice_terms_source       := old.notice_terms_source;
    new.notice_terms_confirmed_at := old.notice_terms_confirmed_at;
    new.notice_terms_confirmed_by := old.notice_terms_confirmed_by;
  end if;
  if v_terms = 'user' then
    new.notice_terms_source       := 'user_confirmed';
    new.notice_terms_confirmed_at := now();
    new.notice_terms_confirmed_by := auth.uid();
  end if;

  -- (d) Buendelanteile duerfen den Vertragsbeitrag nicht ueberschreiten.
  --     Wirksam erst ab P4; bis dahin traegt jede Police 10000 bp und die
  --     Summe ist trivial erfuellt. Bewusst "<= 10000" statt "= 10000":
  --     sonst waere das Anlegen einer einzelnen Police ein Sonderfall. Der
  --     nicht zugeordnete Rest ist sichtbar, nicht verboten.
  if new.contract_id is not null then
    select coalesce(sum(premium_share_bp), 0) into v_sum
      from insurance_policies
     where contract_id = new.contract_id
       and household_id = new.household_id
       and status not in ('ended','entered_in_error')
       and id is distinct from new.id;
    if v_sum + new.premium_share_bp > 10000 then
      raise exception using errcode = '23514',
        message = 'INSURANCE_PREMIUM_SHARE_EXCEEDED';
    end if;
  end if;

  return new;
end $$;

drop trigger if exists insurance_policies_validate on insurance_policies;
create trigger insurance_policies_validate
  before insert or update on insurance_policies
  for each row execute function validate_insurance_policy();

-- ---------- 6. RLS und Grants -----------------------------------------------
-- P1-FASSUNG ohne Gesundheitsbedingung: in P1 gibt es keine Personenbezuege,
-- also niemanden, gegen den eine solche Bedingung pruefen koennte. Sie kommt
-- geschlossen mit P4 nach.
alter table insurance_policies enable row level security;

drop policy if exists insurance_policies_read on insurance_policies;
create policy insurance_policies_read on insurance_policies
  for select to authenticated
  using (
    household_id = current_household_id()
    and is_household_member(household_id)
  );

drop policy if exists insurance_policies_write on insurance_policies;
create policy insurance_policies_write on insurance_policies
  for all to authenticated
  using (
    household_id = current_household_id()
    and is_household_member(household_id)
    and can_edit_household()
  )
  with check (
    household_id = current_household_id()
    and is_household_member(household_id)
    and can_edit_household()
  );

grant select, insert, update, delete on insurance_policies to authenticated;

-- ---------- 7. insurance_settings -------------------------------------------
create table if not exists insurance_settings (
  household_id           uuid primary key references households(id) on delete cascade
                           default current_household_id(),
  notify_deadline        boolean not null default true,
  notify_premium_notice  boolean not null default true,
  notify_claim_followup  boolean not null default true,
  -- Ordentliche Kuendigungsfristen: 60/14/3 Tage. 60 statt 45, weil zwischen
  -- Entschluss und sicherem Zugang bei Postversand mehrere Werktage liegen.
  lead_days              int[] not null default '{60,14,3}',
  -- Sonderkuendigungsfenster: sofort/7/2 Tage. Das Fenster ist meist nur
  -- einen Monat lang; 45 Tage Vorlauf waeren nie erreichbar.
  lead_days_special      int[] not null default '{0,7,2}',
  review_interval_months int not null default 24 check (review_interval_months between 6 and 60),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

alter table insurance_settings enable row level security;
drop policy if exists insurance_settings_read on insurance_settings;
create policy insurance_settings_read on insurance_settings
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_settings_write on insurance_settings;
create policy insurance_settings_write on insurance_settings
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_settings to authenticated;

-- ---------- 8. Kommentare ----------------------------------------------------
comment on table insurance_lines is
  'Spartenkatalog, global und schreibgeschuetzt. Traegt den Zwecktext, der die '
  'Ausgangsfrage des Moduls beantwortet ("was ist das ueberhaupt fuer eine '
  'Versicherung"), sowie die Fristvorbelegung. Die Texte beschreiben die '
  'SPARTE, nie die konkrete Police - die Oberflaeche stellt das mit dem Satz '
  '"Ihre konkrete Police kann abweichen" klar.';

comment on table insurance_policies is
  'Die Police als eigenes Fachobjekt mit eigenem Primaerschluessel. Der '
  'Beitrag lebt weiter in contracts; hier steht hoechstens ein '
  'Informationsbetrag eines fremden Zahlers. Der eigene Schluessel existiert '
  'fuer die Faelle, die kein Vertrag sein koennen: beitragsfreie Policen und '
  'Beitraege ueber Arbeitgeber oder Partner.';

comment on column insurance_policies.main_due_source is
  'Herkunft des Ankerdatums, serverseitig gesetzt. Nur user_confirmed erzeugt '
  'Countdown und Push; catalog_suggested erzeugt eine Rueckfrage. Ein Client '
  'kann den Wert weder beim Anlegen noch beim Aendern selbst setzen - die '
  'Bestaetigung laeuft ueber die Sitzungsvariable insurance.main_due_confirm.';

comment on column insurance_policies.external_premium_minor is
  'Informationsbetrag eines FREMDEN Zahlers (Arbeitgeber, Entgeltumwandlung, '
  'Partner ausserhalb des Haushalts). Ausdruecklich nicht Teil der Fixkosten. '
  'Zahlt der Haushalt selbst, gehoert der Betrag an einen Vertrag - dafuer '
  'gibt es hier bewusst kein Feld.';

-- ---------- 9. Seed: Bausteinkatalog -----------------------------------------
insert into insurance_coverage_keys (coverage_key, label, purpose_short, applies_to, sort_order)
values
  ('glass', 'Glasbruch',
   'Ersetzt Bruchschaeden an Glasscheiben und Glasflaechen.',
   '{household_contents,building}', 10),
  ('elemental', 'Elementarschaeden',
   'Ergaenzt Schaeden durch Ueberschwemmung, Rueckstau, Erdrutsch und weitere Naturgefahren.',
   '{household_contents,building}', 20),
  ('bicycle', 'Fahrraddiebstahl',
   'Ersetzt das Fahrrad auch bei Diebstahl ausserhalb der Wohnung.',
   '{household_contents}', 30),
  ('gross_negligence', 'Grobe Fahrlaessigkeit',
   'Verzichtet auf die Leistungskuerzung, wenn der Schaden grob fahrlaessig verursacht wurde.',
   '{household_contents,building,motor_liability}', 40),
  ('claim_default', 'Forderungsausfall',
   'Springt ein, wenn ein Schaediger zahlungsunfaehig ist und selbst keine Haftpflicht hat.',
   '{private_liability}', 50),
  ('key_loss', 'Schluesselverlust',
   'Traegt die Kosten fuer Schliessanlagen nach dem Verlust fremder Schluessel.',
   '{private_liability}', 60),
  ('tortless_children', 'Deliktunfaehige Kinder',
   'Zahlt Schaeden durch Kinder, die noch nicht deliktfaehig sind und deshalb nicht haften.',
   '{private_liability}', 70),
  ('partial_casco', 'Teilkasko',
   'Ersetzt bestimmte Schaeden am eigenen Fahrzeug, etwa Diebstahl, Glasbruch, Hagel und Wildunfall.',
   '{motor_liability}', 80),
  ('full_casco', 'Vollkasko',
   'Ersetzt zusaetzlich selbst verursachte Schaeden am eigenen Fahrzeug und Vandalismus.',
   '{motor_liability}', 90),
  ('legal_traffic', 'Verkehrsrechtsschutz',
   'Traegt Kosten der Rechtsverfolgung in Verkehrssachen.',
   '{legal_expenses}', 100),
  ('legal_work', 'Arbeitsrechtsschutz',
   'Traegt Kosten der Rechtsverfolgung in arbeitsrechtlichen Streitigkeiten.',
   '{legal_expenses}', 110),
  ('legal_housing', 'Wohnrechtsschutz',
   'Traegt Kosten der Rechtsverfolgung in Miet- und Wohnungsangelegenheiten.',
   '{legal_expenses}', 120),
  ('dynamic', 'Dynamik',
   'Erhoeht Beitrag und Leistung planmaessig in vereinbarten Schritten.',
   '{occupational_disability,term_life,pension_private}', 130),
  ('extra_insurance', 'Nachversicherungsgarantie',
   'Erlaubt die Erhoehung der Leistung ohne erneute Gesundheitspruefung bei bestimmten Anlaessen.',
   '{occupational_disability,term_life}', 140)
on conflict (coverage_key) do update set
  label = excluded.label,
  purpose_short = excluded.purpose_short,
  applies_to = excluded.applies_to,
  sort_order = excluded.sort_order,
  updated_at = now();

-- ---------- 10. Seed: Kernkatalog der Sparten (Welle K) ----------------------
-- Fuenfzehn Sparten, nicht zweiundfuenfzig. Die erste Phase soll nicht daran
-- haengen, dass der ganze Katalog redaktionell fertig ist; die uebrigen
-- Sparten kommen in spaeteren Wellen nach.
--
-- Schreibregel fuer purpose_short und purpose_long, verbindlich:
--   1. Der Text beschreibt die SPARTE, nie die Police des Nutzers.
--   2. Keine Wertung ("die guenstigste sinnvolle Police"), keine Absolutheit
--      ("der einzige Schaden, der ruinieren kann").
--   3. Eine Pflichtaussage nur mit Fundstelle im selben Feld.
--   4. Leistungsaussagen mit "je nach Tarif" oder "nach den vereinbarten
--      Bedingungen", weil der Katalog den Tarif nicht kennt.
--   5. purpose_short ist genau ein Satz.
insert into insurance_lines (
  line_key, risk_field, gdv_sparte, label, purpose_short, purpose_long,
  typically_covers, typically_not_covers, is_placeholder,
  need_tier, need_reason, source_url, source_checked_on,
  default_notice_anchor, default_anchor_basis_kind, default_notice_period_months,
  default_main_due_md, default_min_term_months, default_deadline_kind,
  default_claim_termination_basis, special_law, completeness_fields,
  covered_by_keys, requires_object_kind, sort_order
) values

  -- --- Gesundheit ---------------------------------------------------------
  ('health_statutory', 'health', '020', 'Gesetzliche Krankenversicherung',
   'Traegt Behandlungskosten nach dem gesetzlichen Leistungskatalog und erfuellt damit die Krankenversicherungspflicht.',
   'Die gesetzliche Krankenversicherung uebernimmt Behandlungs-, Arznei- und Hilfsmittelkosten im Umfang des gesetzlichen Leistungskatalogs. Seit 2009 besteht nach Paragraph 193 Abs. 3 VVG eine Pflicht zur Krankenversicherung; ob sie gesetzlich oder privat erfuellt wird, haengt von Erwerbsstatus und Einkommen ab.',
   '{Behandlung,Arzneimittel,Krankenhaus,Vorsorge}', '{Wahlleistungen,Zahnersatz_vollstaendig}',
   false, 'S0', 'Krankenversicherung ist nach Paragraph 193 Abs. 3 VVG Pflicht; die gesetzliche ist einer von zwei Wegen, sie zu erfuellen.',
   'https://www.gesetze-im-internet.de/vvg_2008/__193.html', date '2026-08-14',
   'month_end', 'statute', 2, null, 12, 'kuendigungsfrist', 'none', 'vvg205',
   '{policy_number,main_due_on,customer_number}', '{}', null, 10),

  ('health_private', 'health', '020', 'Private Krankenversicherung',
   'Traegt Behandlungskosten nach dem vereinbarten Tarif statt nach dem gesetzlichen Leistungskatalog.',
   'Die private Krankenversicherung erstattet Behandlungskosten im Umfang des vereinbarten Tarifs; Selbstbehalt, Erstattungssaetze und eingeschlossene Leistungsarten stehen im Vertrag. Sie erfuellt die Versicherungspflicht nach Paragraph 193 Abs. 3 VVG ebenso wie die gesetzliche. Nach Paragraph 205 Abs. 4 VVG besteht bei einer Praemienerhoehung ein Sonderkuendigungsrecht von zwei Monaten.',
   '{Behandlung,Arzneimittel,Krankenhaus}', '{Leistungen_ausserhalb_des_Tarifs}',
   false, 'S0', 'Krankenversicherung ist nach Paragraph 193 Abs. 3 VVG Pflicht; die private ist der zweite Weg, sie zu erfuellen.',
   'https://www.gesetze-im-internet.de/vvg_2008/__205.html', date '2026-08-14',
   'term_end', 'statute', 3, null, 12, 'kuendigungsfrist', 'none', 'vvg205',
   '{policy_number,main_due_on,tariff_name,deductible_minor,customer_number}', '{}', null, 20),

  ('long_term_care_mandatory', 'health', '020', 'Private Pflegepflichtversicherung',
   'Deckt Pflegeleistungen fuer privat Krankenversicherte und wird in der Regel als eigener Vertrag mit eigenem Beitrag gefuehrt.',
   'Wer privat krankenversichert ist, muss nach Paragraph 23 SGB XI eine private Pflegepflichtversicherung abschliessen. Sie entspricht im Leistungsumfang der sozialen Pflegeversicherung und laeuft meist beim selben Versicherer wie die Krankenversicherung, aber als eigener Vertrag.',
   '{Pflegegeld,Pflegesachleistung}', '{Leistungen_oberhalb_des_Pflichtumfangs}',
   false, 'S0', 'Nach Paragraph 23 SGB XI Pflicht fuer privat Krankenversicherte.',
   'https://www.gesetze-im-internet.de/sgb_11/__23.html', date '2026-08-14',
   'term_end', 'statute', 3, null, 12, 'kuendigungsfrist', 'none', 'vvg205',
   '{policy_number,main_due_on,customer_number}', '{}', null, 30),

  ('dental_supplementary', 'health', '020', 'Zahnzusatzversicherung',
   'Erstattet je nach Tarif einen Teil der Kosten fuer Zahnersatz, Zahnbehandlung und Prophylaxe, die die Krankenkasse nicht traegt.',
   'Die Zahnzusatzversicherung ergaenzt den Leistungskatalog der gesetzlichen Krankenversicherung. Erstattungsquote, Summenbegrenzung in den ersten Jahren (Zahnstaffel) und Wartezeiten stehen im Tarif und unterscheiden sich erheblich.',
   '{Zahnersatz,Zahnbehandlung,Prophylaxe}', '{bereits_angeratene_Behandlungen}',
   false, 'S3', 'Erganzungsschutz ohne gesetzliche Pflicht; der Nutzen haengt vom eigenen Zahnstatus ab.',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/zahnzusatzversicherung-9166', date '2026-08-14',
   'term_end', 'statute', 3, null, 12, 'kuendigungsfrist', 'none', 'vvg205',
   '{policy_number,main_due_on,tariff_name,waiting_period_days}', '{}', null, 40),

  ('travel_health', 'health', '020', 'Auslandsreise-Krankenversicherung',
   'Traegt Behandlungskosten im Ausland, die die Krankenkasse nicht erstattet, und je nach Tarif den Ruecktransport.',
   'Die gesetzliche Krankenversicherung zahlt ausserhalb der EU in der Regel nicht und innerhalb der EU nur zum dortigen Satz. Die Auslandsreise-Krankenversicherung schliesst diese Luecke fuer die Dauer einer Reise; ob ein medizinisch sinnvoller Ruecktransport eingeschlossen ist, steht im Tarif.',
   '{Behandlung_im_Ausland,Ruecktransport}', '{geplante_Behandlungen_im_Ausland}',
   false, 'S1', 'Bei Auslandsreisen einschlaegig, weil die gesetzliche Krankenversicherung dort nur eingeschraenkt oder gar nicht leistet.',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/auslandsreisekrankenversicherung-13835', date '2026-08-14',
   'term_end', 'statute', 3, null, 12, 'kuendigungsfrist', 'none', 'vvg205',
   '{policy_number,main_due_on}', '{}', null, 50),

  -- --- Einkommen ----------------------------------------------------------
  ('occupational_disability', 'income', '010', 'Berufsunfaehigkeitsversicherung',
   'Zahlt eine vereinbarte Monatsrente, wenn der zuletzt ausgeuebte Beruf nach den Vertragsbedingungen dauerhaft zu mindestens 50 Prozent nicht mehr ausgeuebt werden kann.',
   'Die Berufsunfaehigkeitsversicherung sichert das Erwerbseinkommen ab. Massgeblich sind Rentenhoehe, Endalter und die Frage, ob auf einen anderen Beruf verwiesen werden darf. Nach Paragraph 168 Abs. 1 VVG kann der Vertrag jederzeit zum Schluss der laufenden Versicherungsperiode gekuendigt werden.',
   '{Berufsunfaehigkeitsrente}', '{Arbeitslosigkeit,voruebergehende_Erkrankung}',
   false, 'S1', 'Einschlaegig, solange Erwerbseinkommen die wesentliche Einkommensquelle des Haushalts ist.',
   'https://www.gesetze-im-internet.de/vvg_2008/__168.html', date '2026-08-14',
   'period_end', 'statute', null, null, null, 'kuendigungsfrist', 'none', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor,ends_on}', '{dynamic,extra_insurance}', null, 60),

  ('term_life', 'income', '010', 'Risikolebensversicherung',
   'Zahlt die vereinbarte Todesfallsumme an die bezugsberechtigte Person, wenn die versicherte Person waehrend der Laufzeit stirbt.',
   'Die Risikolebensversicherung sichert Hinterbliebene oder ein Darlehen ab und bildet kein Kapital. Massgeblich sind Todesfallsumme, Laufzeit und Bezugsrecht. Nach Paragraph 168 Abs. 1 VVG ist die Kuendigung jederzeit zum Schluss der laufenden Versicherungsperiode moeglich.',
   '{Todesfallleistung}', '{Erlebensfallleistung,Kapitalaufbau}',
   false, 'S1', 'Einschlaegig bei Unterhaltspflicht gegenueber Angehoerigen oder laufendem Darlehen.',
   'https://www.gesetze-im-internet.de/vvg_2008/__168.html', date '2026-08-14',
   'period_end', 'statute', null, null, null, 'kuendigungsfrist', 'none', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor,ends_on}', '{dynamic,extra_insurance}', null, 70),

  ('accident_private', 'income', '030', 'Private Unfallversicherung',
   'Zahlt Kapital oder Rente bei dauerhafter Invaliditaet nach einem Unfall; die Hoehe richtet sich nach Invaliditaetsgrad und Gliedertaxe.',
   'Die private Unfallversicherung leistet nur bei Unfaellen, nicht bei Krankheit. Massgeblich sind Invaliditaetssumme, Gliedertaxe und eine etwaige Progression. Sie ersetzt keine Berufsunfaehigkeitsversicherung, weil die haeufigeren Ursachen einer Erwerbsminderung Krankheiten sind.',
   '{Invaliditaet_nach_Unfall,Unfallrente}', '{Invaliditaet_durch_Krankheit}',
   false, 'S2', 'Ergaenzender Schutz; deckt nur Unfallfolgen und damit einen Teil der Ursachen einer dauerhaften Einschraenkung ab.',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/private-unfallversicherung-11145', date '2026-08-14',
   'term_end', 'statute', 3, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', null, 80),

  -- --- Haftung ------------------------------------------------------------
  ('private_liability', 'liability', '041', 'Privathaftpflichtversicherung',
   'Zahlt Personen-, Sach- und Vermoegensschaeden, die versicherte Personen Dritten zufuegen, und wehrt unberechtigte Ansprueche ab.',
   'Wer einem anderen schuldhaft einen Schaden zufuegt, haftet nach Paragraph 823 BGB unbegrenzt mit seinem Vermoegen. Die Privathaftpflichtversicherung uebernimmt berechtigte Ansprueche bis zur Deckungssumme und wehrt unberechtigte ab. Ob Familienangehoerige, deliktunfaehige Kinder oder Gefaelligkeitsschaeden eingeschlossen sind, steht in den Bedingungen.',
   '{Personenschaeden,Sachschaeden,Vermoegensschaeden,Abwehr_unberechtigter_Ansprueche}',
   '{eigene_Schaeden,vorsaetzliche_Schaeden,berufliche_Taetigkeit}',
   false, 'S1', 'Einschlaegig fuer jeden Haushalt, weil die gesetzliche Haftung nach Paragraph 823 BGB der Hoehe nach nicht begrenzt ist.',
   'https://www.gesetze-im-internet.de/bgb/__823.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{claim_default,key_loss,tortless_children}', null, 90),

  -- --- Eigentum -----------------------------------------------------------
  ('household_contents', 'property', '130', 'Hausratversicherung',
   'Ersetzt bewegliche Einrichtung der Wohnung nach den versicherten Gefahren.',
   'Die Hausratversicherung ersetzt Moebel, Geraete und persoenliche Gegenstaende nach Feuer, Leitungswasser, Sturm, Hagel und Einbruchdiebstahl. Die Versicherungssumme richtet sich ueblicherweise nach der Wohnflaeche; Elementarschaeden und Fahrraddiebstahl sind je nach Tarif eingeschlossen oder gesondert zu vereinbaren.',
   '{Feuer,Leitungswasser,Sturm,Hagel,Einbruchdiebstahl}',
   '{Gebaeudeschaeden,Elementarschaeden_ohne_Einschluss}',
   false, 'S2', 'Einschlaegig, wenn der Wiederbeschaffungswert des Hausrats aus eigenen Mitteln nicht kurzfristig aufzubringen waere.',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/hausratversicherung-13840', date '2026-08-14',
   'term_end', 'statute', 3, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor,deductible_minor}',
   '{glass,elemental,bicycle,gross_negligence}', 'property', 100),

  ('building', 'property', '140', 'Wohngebaeudeversicherung',
   'Ersetzt Schaeden am Gebaeude selbst nach den versicherten Gefahren.',
   'Die Wohngebaeudeversicherung deckt das Gebaeude einschliesslich fest eingebauter Teile gegen Feuer, Leitungswasser, Sturm und Hagel. Die Versicherungssumme wird ueblicherweise ueber den gleitenden Neuwert nach dem Wert 1914 bestimmt. Elementarschaeden sind gesondert einzuschliessen.',
   '{Feuer,Leitungswasser,Sturm,Hagel}', '{Hausrat,Elementarschaeden_ohne_Einschluss}',
   false, 'S1', 'Einschlaegig bei Eigentum an einem Gebaeude; finanzierende Banken verlangen den Nachweis regelmaessig.',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/wohngebaeudeversicherung-13842', date '2026-08-14',
   'term_end', 'statute', 3, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor,sum_insured_kind,deductible_minor}',
   '{glass,elemental,gross_negligence}', 'property', 110),

  -- --- Mobilitaet ---------------------------------------------------------
  ('motor_liability', 'mobility', '051', 'Kfz-Haftpflichtversicherung',
   'Deckt Schaeden, die mit dem Fahrzeug Dritten zugefuegt werden; fuer zugelassene Fahrzeuge ist sie vorgeschrieben.',
   'Die Kfz-Haftpflichtversicherung ist nach Paragraph 1 PflVG Voraussetzung fuer den Betrieb eines Fahrzeugs auf oeffentlichen Wegen. Teil- und Vollkasko sind freiwillige Ergaenzungen fuer Schaeden am eigenen Fahrzeug. Die Kuendigungsfrist betraegt nach Paragraph 11 Abs. 3 VVG einen Monat zum Ablauf der Versicherungsperiode; der oft genannte 30. November ergibt sich nur, wenn das Versicherungsjahr dem Kalenderjahr entspricht.',
   '{Personenschaeden,Sachschaeden_Dritter}', '{Schaeden_am_eigenen_Fahrzeug_ohne_Kasko}',
   false, 'S0', 'Nach Paragraph 1 PflVG Voraussetzung fuer den Betrieb eines zugelassenen Fahrzeugs.',
   'https://www.gesetze-im-internet.de/pflvg/__1.html', date '2026-08-14',
   'term_end', 'statute', 1, '01-01', null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,deductible_minor}',
   '{partial_casco,full_casco,gross_negligence}', 'vehicle', 120),

  -- --- Recht --------------------------------------------------------------
  ('legal_expenses', 'legal', '070', 'Rechtsschutzversicherung',
   'Traegt Kosten der Rechtsverfolgung in den versicherten Lebensbereichen.',
   'Die Rechtsschutzversicherung uebernimmt Anwalts-, Gerichts- und Sachverstaendigenkosten in den vereinbarten Bausteinen, etwa Verkehr, Arbeit, Wohnen oder Privat. Ueblich sind Wartezeiten von drei Monaten und eine Selbstbeteiligung je Fall; welche Streitigkeiten ausgeschlossen sind, steht in den Bedingungen.',
   '{Anwaltskosten,Gerichtskosten}', '{Streitigkeiten_vor_Vertragsbeginn,Baustreitigkeiten}',
   false, 'S3', 'Sinnvoll, wenn absehbar Streitigkeiten in einem der Bausteine drohen; die Bausteine sind einzeln waehlbar.',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/rechtsschutzversicherung-13841', date '2026-08-14',
   'term_end', 'statute', 3, null, null, 'kuendigungsfrist', 'contract', 'vvg40',
   '{policy_number,main_due_on,deductible_minor,waiting_period_days}',
   '{legal_traffic,legal_work,legal_housing}', null, 130),

  -- --- Alter --------------------------------------------------------------
  ('pension_private', 'retirement', '010', 'Private Renten- oder Kapitallebensversicherung',
   'Baut Kapital fuer die Altersvorsorge auf und zahlt es als Rente oder Einmalbetrag aus.',
   'Private Renten- und Kapitallebensversicherungen bilden ueber die Laufzeit Kapital und leisten im Erlebensfall. Massgeblich sind Rentenfaktor, garantierte und prognostizierte Leistung sowie die Frage, ob der Vertrag beitragsfrei gestellt werden kann. Nach Paragraph 168 Abs. 1 VVG ist die Kuendigung zum Schluss der laufenden Versicherungsperiode moeglich; bei gefoerderten Vertraegen gelten Sonderregeln.',
   '{Altersleistung}', '{Berufsunfaehigkeit_ohne_Zusatzbaustein}',
   false, 'S2', 'Ergaenzt die gesetzliche Altersvorsorge; die Einordnung haengt von der bereits erreichten Versorgung ab.',
   'https://www.gesetze-im-internet.de/vvg_2008/__168.html', date '2026-08-14',
   'period_end', 'statute', null, null, null, 'kuendigungsfrist', 'none', 'vvg40',
   '{policy_number,main_due_on,ends_on}', '{dynamic}', null, 140),

  -- --- Platzhalter --------------------------------------------------------
  -- Die wichtigste Zeile des Seeds: jede aus einem Vertrag uebernommene Police
  -- startet auf ihr. Sie traegt weder Bedarfsstufe noch Quelle und ist
  -- deshalb ausdruecklich als Platzhalter markiert - sonst waeren die
  -- Pflichtfelder nur mit erfundenen Werten befuellbar. Anker 'none': eine
  -- Police ohne bekannte Sparte darf keine Frist erzeugen.
  ('unknown', 'other', null, 'Sparte noch offen',
   'Vorlaeufige Zuordnung, bis die Sparte feststeht.',
   'Diese Police ist erfasst, aber noch keiner Sparte zugeordnet. Sie erscheint in der Liste und geht nicht verloren; Zwecktext, Fristvorbelegung und Bedarfsabgleich stehen erst nach der Zuordnung zur Verfuegung.',
   '{}', '{}', true,
   null, null, null, null,
   'none', 'policy_terms', null, null, null, 'kuendigungsfrist', 'none', 'none',
   '{policy_number}', '{}', null, 9999)

on conflict (line_key) do update set
  risk_field = excluded.risk_field,
  gdv_sparte = excluded.gdv_sparte,
  label = excluded.label,
  purpose_short = excluded.purpose_short,
  purpose_long = excluded.purpose_long,
  typically_covers = excluded.typically_covers,
  typically_not_covers = excluded.typically_not_covers,
  is_placeholder = excluded.is_placeholder,
  need_tier = excluded.need_tier,
  need_reason = excluded.need_reason,
  source_url = excluded.source_url,
  source_checked_on = excluded.source_checked_on,
  default_notice_anchor = excluded.default_notice_anchor,
  default_anchor_basis_kind = excluded.default_anchor_basis_kind,
  default_notice_period_months = excluded.default_notice_period_months,
  default_main_due_md = excluded.default_main_due_md,
  default_min_term_months = excluded.default_min_term_months,
  default_deadline_kind = excluded.default_deadline_kind,
  default_claim_termination_basis = excluded.default_claim_termination_basis,
  special_law = excluded.special_law,
  completeness_fields = excluded.completeness_fields,
  covered_by_keys = excluded.covered_by_keys,
  requires_object_kind = excluded.requires_object_kind,
  sort_order = excluded.sort_order,
  updated_at = now();

-- ---------------------------------------------------------------------------
-- Ehemals: 0017_household_people.sql
--
-- Stufe 6 des Liquiditaetsplans: Einnahmen nach Person und Art.
--
-- Warum eine Tabelle und kein Freitextfeld an `contracts`: der Zweck der Stufe
-- ist das Gruppieren. "Anna", "anna" und "Anna M." waeren als Freitext drei
-- Personen, und die Gruppierung damit wertlos. Der Partialindex weiter unten
-- macht die Namensgleichheit zur Regel statt zur Hoffnung.
--
-- Bewusst KEIN Zwang zu `auth.users`: Partner und Kinder haben in aller Regel
-- keinen eigenen Zugang, sollen aber trotzdem als Einnahmequelle zaehlen.
-- `member_user_id` ist die optionale Bruecke, falls doch einer existiert.
-- ---------------------------------------------------------------------------

create table if not exists household_people (
  id              uuid primary key default gen_random_uuid(),
  household_id    uuid not null references households(id) on delete cascade
                    default current_household_id(),
  name            text not null check (char_length(name) between 1 and 80),
  member_user_id  uuid references auth.users(id) on delete set null,
  sort_order      int not null default 0,
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- Anker fuer zusammengesetzte Fremdschluessel (RLS allein verhindert nicht,
-- dass eine bekannte fremde UUID eingetragen wird).
create unique index if not exists household_people_id_household_uniq
  on household_people (id, household_id);

-- Die eigentliche Pointe der Stufe: Namen sind je Haushalt eindeutig, ohne
-- Ruecksicht auf Gross- und Kleinschreibung. Nur aktive Personen, damit ein
-- geloeschter Name spaeter wiederverwendet werden darf.
create unique index if not exists household_people_name_uniq
  on household_people (household_id, lower(name)) where is_active;

create index if not exists household_people_household_idx
  on household_people (household_id, sort_order, name);

alter table household_people enable row level security;
drop policy if exists household_people_read on household_people;
create policy household_people_read on household_people for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists household_people_write on household_people;
create policy household_people_write on household_people for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

-- Einnahmeart und Person haengen an `contracts`, nicht an einer 1:1-Tabelle:
-- eine Einnahmequelle IST ein Vertrag (Plan Abschnitt 3), und zwei Spalten
-- rechtfertigen keinen zweiten Datensatz je Zeile.
alter table contracts add column if not exists income_kind text;
alter table contracts add column if not exists person_id uuid;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'contracts_income_kind_check') then
    alter table contracts add constraint contracts_income_kind_check
      check (income_kind is null or income_kind in (
        'salary', 'self_employment', 'pension', 'benefit',
        'rental', 'capital', 'support', 'other'));
  end if;
  -- Eine Einnahmeart an einer Ausgabe waere widerspruechlich.
  if not exists (select 1 from pg_constraint where conname = 'contracts_income_kind_direction_chk') then
    alter table contracts add constraint contracts_income_kind_direction_chk
      check (income_kind is null or direction = 'income');
  end if;
  if not exists (select 1 from pg_constraint where conname = 'contracts_person_fkey') then
    alter table contracts add constraint contracts_person_fkey
      foreign key (person_id, household_id)
      references household_people (id, household_id)
      on delete set null deferrable initially deferred;
  end if;
end $$;

create index if not exists contracts_person_idx
  on contracts (household_id, person_id) where person_id is not null;

-- ---------------------------------------------------------------------------
-- Ehemals: 0018_household_scenarios.sql
--
-- Stufe 7 des Liquiditaetsplans: gespeicherte Haushaltsszenarien.
--
-- Diese Tabellen sind ab jetzt das KANONISCHE Szenariosystem des Haushalts.
-- `contract_scenarios`/`contract_scenario_items` (cancel/reduce auf Vertraege)
-- gehen darin auf und werden weiter unten uebernommen - sonst stuende
-- "Netflix kuendigen" in zwei Systemen, moeglicherweise mit zwei Betraegen.
-- Diese Uebernahme ist Voraussetzung der Stufe, kein spaeteres Aufraeumen
-- (Plan Abschnitt 11).
--
-- Kein `as_of_date` wie bei `project_scenarios`: ein Haushaltsszenario rechnet
-- immer ab heute. Ein eingefrorener Stichtag hiesse, dass ein halbes Jahr
-- altes Szenario still eine ueberholte Startliquiditaet behauptet.
-- ---------------------------------------------------------------------------

create table if not exists household_scenarios (
  id              uuid primary key default gen_random_uuid(),
  household_id    uuid not null references households(id) on delete cascade
                    default current_household_id(),
  name            text not null check (char_length(name) between 1 and 120),
  description     text,
  status          text not null default 'draft'
                    check (status in ('draft', 'active', 'archived')),
  horizon_months  int check (horizon_months between 1 and 60),
  color_key       text,
  -- Herkunft: 'manual' oder 'contract_scenario' fuer die Uebernahme unten.
  origin          text not null default 'manual'
                    check (origin in ('manual', 'contract_scenario')),
  legacy_contract_scenario_id uuid references contract_scenarios(id) on delete set null,
  created_at      timestamptz not null default now(),
  created_by      uuid references auth.users(id),
  updated_at      timestamptz not null default now(),
  version         int not null default 1
);

create unique index if not exists household_scenarios_id_household_uniq
  on household_scenarios (id, household_id);
create index if not exists household_scenarios_household_idx
  on household_scenarios (household_id, status, name);
-- Jedes Fixkostenszenario darf hoechstens einmal uebernommen werden.
create unique index if not exists household_scenarios_legacy_uniq
  on household_scenarios (legacy_contract_scenario_id)
  where legacy_contract_scenario_id is not null;

create table if not exists household_scenario_changes (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references households(id) on delete cascade
                        default current_household_id(),
  scenario_id         uuid not null,
  change_type         text not null check (change_type in (
                        'income_add', 'income_change', 'income_end',
                        'expense_add', 'expense_change', 'expense_end', 'one_off')),
  target_contract_id  uuid,
  label               text not null check (char_length(label) between 1 and 160),
  -- nullable: bei *_end gibt es keinen Betrag und damit auch keinen Modus.
  amount_mode         text check (amount_mode in ('absolute', 'delta', 'percent')),
  -- VORZEICHENBEHAFTET: eine Gehaltssenkung waere sonst nicht ausdrueckbar.
  amount_minor        bigint,
  amount_percent      numeric(6, 2),
  frequency           text not null default 'monthly' check (frequency in (
                        'once', 'weekly', 'monthly', 'quarterly', 'semiannual', 'yearly')),
  due_day             int check (due_day between 1 and 31),
  income_kind         text,
  starts_on           date not null,
  ends_on             date,
  confidence          text not null default 'estimated'
                        check (confidence in ('contracted', 'estimated', 'speculative')),
  include_in_forecast boolean not null default true,
  sort_order          int not null default 0,
  note                text,
  created_at          timestamptz not null default now(),

  constraint household_scenario_changes_scenario_fkey
    foreign key (scenario_id, household_id)
    references household_scenarios (id, household_id)
    on delete cascade deferrable initially deferred,
  constraint household_scenario_changes_contract_fkey
    foreign key (target_contract_id, household_id)
    references contracts (id, household_id)
    on delete cascade deferrable initially deferred,

  -- Dieselben XOR-Regeln, die `ForecastChange` in der API prueft. Sie stehen
  -- hier ein zweites Mal, weil eine Tabelle auch dann stimmen muss, wenn
  -- jemand an der API vorbei schreibt.
  constraint household_scenario_changes_target_chk check (
    (change_type in ('income_change', 'income_end', 'expense_change', 'expense_end')
       and target_contract_id is not null)
    or (change_type in ('income_add', 'expense_add', 'one_off')
       and target_contract_id is null)),
  constraint household_scenario_changes_amount_chk check (
    (change_type in ('income_end', 'expense_end')
       and amount_mode is null and amount_minor is null and amount_percent is null)
    or (change_type not in ('income_end', 'expense_end') and amount_mode is not null)),
  constraint household_scenario_changes_mode_chk check (
    amount_mode is null
    or (amount_mode = 'percent' and amount_percent is not null and amount_minor is null)
    or (amount_mode in ('absolute', 'delta') and amount_minor is not null
        and amount_percent is null)),
  constraint household_scenario_changes_once_chk check (
    (change_type = 'one_off' and frequency = 'once' and ends_on is null)
    or (change_type <> 'one_off' and frequency <> 'once')),
  constraint household_scenario_changes_range_chk check (
    ends_on is null or ends_on >= starts_on),
  constraint household_scenario_changes_income_kind_chk check (
    income_kind is null or change_type = 'income_add')
);

create index if not exists household_scenario_changes_scenario_idx
  on household_scenario_changes (household_id, scenario_id, sort_order);

alter table household_scenarios enable row level security;
drop policy if exists household_scenarios_read on household_scenarios;
create policy household_scenarios_read on household_scenarios for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists household_scenarios_write on household_scenarios;
create policy household_scenarios_write on household_scenarios for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

alter table household_scenario_changes enable row level security;
drop policy if exists household_scenario_changes_read on household_scenario_changes;
create policy household_scenario_changes_read on household_scenario_changes
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists household_scenario_changes_write on household_scenario_changes;
create policy household_scenario_changes_write on household_scenario_changes
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

create or replace function bump_household_scenario_version() returns trigger as $fn$
begin
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end;
$fn$ language plpgsql;

drop trigger if exists household_scenarios_bump_version on household_scenarios;
create trigger household_scenarios_bump_version before update on household_scenarios
  for each row execute function bump_household_scenario_version();

-- ---------------------------------------------------------------------------
-- Uebernahme der Fixkostenszenarien
--
-- `cancel` wird zu `expense_end`, `reduce` zu `expense_change` mit absolutem
-- Zielbetrag. Beginn ist der Monatserste nach der Uebernahme: ein
-- Senkungsplan sagt "ab jetzt", nicht rueckwirkend.
--
-- Beide Einfuegungen sind wiederholbar (`not exists`), damit das Schema
-- mehrfach eingespielt werden kann, ohne Szenarien zu verdoppeln.
-- ---------------------------------------------------------------------------

insert into household_scenarios (
  household_id, name, description, status, origin, legacy_contract_scenario_id, created_at
)
select
  cs.household_id,
  cs.name,
  'Aus dem Fixkostenplan uebernommen.',
  case when cs.is_active then 'active' else 'draft' end,
  'contract_scenario',
  cs.id,
  cs.created_at
from contract_scenarios cs
where not exists (
  select 1 from household_scenarios hs where hs.legacy_contract_scenario_id = cs.id
);

insert into household_scenario_changes (
  household_id, scenario_id, change_type, target_contract_id, label,
  amount_mode, amount_minor, frequency, starts_on, confidence, sort_order
)
select
  hs.household_id,
  hs.id,
  case when csi.action = 'cancel' then 'expense_end' else 'expense_change' end,
  csi.contract_id,
  c.name,
  case when csi.action = 'cancel' then null else 'absolute' end,
  -- Ausgaben sind im Forecast negativ; `contracts.amount_minor` ist positiv.
  case when csi.action = 'cancel' then null else -abs(csi.target_amount_minor) end,
  c.frequency,
  (date_trunc('month', current_date) + interval '1 month')::date,
  'estimated',
  row_number() over (partition by hs.id order by c.name)
from contract_scenario_items csi
join household_scenarios hs on hs.legacy_contract_scenario_id = csi.scenario_id
join contracts c on c.id = csi.contract_id and c.household_id = hs.household_id
where not exists (
  select 1 from household_scenario_changes hsc
  where hsc.scenario_id = hs.id and hsc.target_contract_id = csi.contract_id
);

-- Nachtrag zu Stufe 6/7: `ForecastChange` traegt seit der Personenzuordnung ein
-- optionales `person_id`. Ohne dieselbe Spalte hier ginge es beim Speichern
-- eines Szenarios verloren, und der Steuerklassenhinweis fiele stumm auf
-- "andere Person" zurueck - eine Verschlechterung, die niemand bemerkt haette.
alter table household_scenario_changes add column if not exists person_id uuid;

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'household_scenario_changes_person_fkey') then
    alter table household_scenario_changes add constraint household_scenario_changes_person_fkey
      foreign key (person_id, household_id)
      references household_people (id, household_id)
      on delete set null deferrable initially deferred;
  end if;
  if not exists (select 1 from pg_constraint
                 where conname = 'household_scenario_changes_person_chk') then
    alter table household_scenario_changes add constraint household_scenario_changes_person_chk
      check (person_id is null or change_type = 'income_add');
  end if;
end $$;


-- =============================================================================
-- Versicherungen, Phase P3: Beitragsanpassungen (docs/VERSICHERUNGEN-PLAN.md 4.7)
-- =============================================================================
-- EINE Tabelle. Personen, Objekte, Deckungen (P4), Schaeden (P5) und der
-- Bedarfsabgleich (P6) stehen weiterhin bewusst nicht hier - eine Tabelle
-- entsteht mit dem Endpunkt, der aus ihr liest.
--
-- Warum eine eigene Tabelle und nicht zwei Spalten an contract_amount_history:
-- der Abgleichsmotor oeffnet bei der ersten abweichenden Lastschrift eine neue
-- Preisstufe (_open_price_stage), und contract_amount_history_open_uniq laesst
-- nur eine offene zu. Eine im November erfasste, ab Januar wirksame Anpassung
-- wuerde im Januar ueberschrieben - samt received_on, also samt dem einzigen
-- Datum, aus dem das Sonderkuendigungsrecht ueberhaupt folgt.
--
-- Der Leitsatz des Moduls gilt hier unveraendert: auch diese Tabelle modelliert
-- KEIN Geld. old_/new_amount_minor sind der Inhalt eines Anschreibens, nicht der
-- Beitrag. Der Beitrag entsteht erst mit der Bestaetigung, und zwar ueber die
-- Vertragsseite (contract_service.open_amount_stage) - amount_history_id zeigt
-- danach auf die dort erzeugte Preisstufe.

create table if not exists insurance_premium_notices (
  id                        uuid primary key default gen_random_uuid(),
  household_id              uuid not null references households(id) on delete cascade
                              default current_household_id(),
  policy_id                 uuid not null,

  -- ZUGANG des Schreibens und damit der FRISTANKER. Paragraph 40 Abs. 1 S. 1
  -- VVG laesst die Monatsfrist ab Zugang laufen, nicht ab Wirksamwerden der
  -- Erhoehung. Wer beides verwechselt, verschenkt dem Nutzer die Wochen
  -- dazwischen - bei der ueblichen Ankuendigung im November zum Januar sind das
  -- acht.
  received_on               date not null,
  -- WIRKSAMWERDEN. Zweite, eigenstaendige Rolle: die Sonderkuendigung wirkt
  -- "mit sofortiger Wirkung, fruehestens jedoch zum Zeitpunkt des
  -- Wirksamwerdens der Erhoehung". Das System fuehrt deshalb ZWEI Daten -
  -- Fristende und fruehestes Beendigungsdatum - und zeigt beide.
  effective_on              date not null,

  old_amount_minor          bigint not null check (old_amount_minor > 0),
  new_amount_minor          bigint not null check (new_amount_minor > 0),
  -- KFZ-BESONDERHEIT: die Erhoehung kann sich hinter der jaehrlichen
  -- SF-Hochstufung verstecken. Massgeblich ist der auf der Beitragsrechnung
  -- ausgewiesene VERGLEICHSBEITRAG - der Beitrag mit neuer SF-Klasse ohne Typ-,
  -- Regional- und Tarifanpassung. Das Recht besteht, wenn new_amount_minor
  -- darueber liegt, AUCH wenn der Rechnungsbetrag gegenueber dem Vorjahr sinkt -
  -- und es besteht NICHT, wenn der Betrag zwar steigt, aber unter dem
  -- Vergleichsbeitrag bleibt.
  comparison_amount_minor   bigint check (comparison_amount_minor > 0),

  -- BEWUSSTE TEILMENGE von insurance_policies.external_frequency: 'weekly' und
  -- 'single' fehlen. Ein Anpassungsschreiben setzt eine WIEDERKEHRENDE Praemie
  -- neu fest; ein Einmalbeitrag kann nicht angepasst werden, und eine
  -- woechentliche Versicherungspraemie kommt im Spartenkatalog nicht vor.
  frequency                 text not null check (frequency in
                              ('monthly','quarterly','semiannual','yearly')),

  -- Entscheidet ueber das Sonderkuendigungsrecht: Paragraph 40 VVG greift nur
  -- bei einer Erhoehung OHNE entsprechende Leistungserweiterung.
  with_benefit_increase     boolean not null default false,
  reason                    text check (reason in (
                              'claims_cost','risk_change','demographics','index',
                              'tariff_class','regional_class','sf_class','age_bracket','other')),

  -- --- Gerechnete Fristdaten -------------------------------------------------
  -- Gerechnet wird im Rechenkern (insurance_deadline_service); gespeichert wird
  -- nur, weil der naechtliche Push einen Dedup-Anker braucht und der Worker
  -- nicht je Zeile nachrechnen soll. Die API zeigt IMMER den frisch gerechneten
  -- Stand - aendert der Nutzer den Zustellweg, waere ein gespeichertes
  -- Handlungsdatum sonst still veraltet.
  --
  -- Das RECHTLICHE Fristende: received_on + ein Monat, bei
  -- insurance_lines.special_law = 'vvg205' (private Krankenversicherung,
  -- Paragraph 205 Abs. 4 VVG) zwei Monate. Paragraph 193 BGB schiebt es NICHT
  -- nach hinten - der Fristtyp ist 'kuendigungsfrist' (BGH III ZR 172/04).
  special_termination_until date,
  -- Der letzte Tag <= special_termination_until, an dem ein Zugang beim
  -- Versicherer zu erwarten ist (Samstag scheidet aus), und das daraus
  -- abgeleitete Handlungsdatum je service_channel.
  safe_service_on           date,
  recommended_action_on     date,
  -- Fruehestes Beendigungsdatum, = effective_on (Paragraph 40 Abs. 1 S. 1 VVG).
  earliest_effect_on        date,

  -- --- Uebernahme ------------------------------------------------------------
  -- Verweis auf die Preisstufe, die die Bestaetigung ueber die VERTRAGSSEITE
  -- erzeugt hat. Bleibt leer, wenn die Police keinen Vertrag traegt und der
  -- Fremdbetrag fortgeschrieben wurde - deshalb ist confirmed_at und nicht
  -- amount_history_id das Kennzeichen "uebernommen".
  amount_history_id         uuid references contract_amount_history(id) on delete set null,
  -- Nicht im Planentwurf, aber ohne Ersatz nicht wegzulassen: im Fall (b) - ein
  -- Dritter zahlt - schreibt die Bestaetigung nur external_premium_minor an der
  -- Police fort und hinterliesse an der Mitteilung selbst keine Spur. Eine
  -- zweite Bestaetigung derselben Zeile waere dann weder erkennbar noch
  -- abweisbar, und der Beitragsverlauf wuesste nicht, welche Mitteilung
  -- tatsaechlich gilt. Zeitstempel UND Urheber, wie ueberall in diesem Modul:
  -- eine Bestaetigung ist eine Handlung, kein Etikett.
  confirmed_at              timestamptz,
  confirmed_by              uuid references auth.users(id) on delete set null,
  -- Frist zur Kenntnis genommen - beendet weitere Push-Stufen. Die Staffel ist
  -- eine Eskalation fuer den Fall des Nichthandelns, keine Terminserie.
  acknowledged_at           timestamptz,
  -- MASCHINENGESCHRIEBEN: Dedup-Anker des Pushes, aus dem Versionstrigger
  -- ausgenommen. Ohne diese Ausnahme entwertete jeder Push das If-Match des
  -- Nutzers und ein PATCH liefe ohne inhaltliche Aenderung in ein 409.
  notified_at               timestamptz,

  version                   int not null default 1,
  created_by                uuid references auth.users(id) on delete set null default auth.uid(),
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),

  -- Ausfuehrungsvertrag Punkt 4: jede haushaltsgebundene Tabelle traegt
  -- unique (id, household_id), auch dort, wo heute kein zusammengesetzter
  -- Fremdschluessel darauf zeigt.
  unique (id, household_id),
  -- Dieselbe Mitteilung zweimal erfassen heisst: dasselbe Fenster zweimal
  -- melden. Zugang und Wirksamwerden zusammen identifizieren das Schreiben.
  unique (household_id, policy_id, received_on, effective_on),
  foreign key (policy_id, household_id)
    references insurance_policies(id, household_id) on delete cascade,

  -- Ein Schreiben kann nicht vor seinem Zugang wirken.
  constraint insurance_premium_notices_date_order
    check (effective_on >= received_on),
  -- Eine Preisstufe ohne Bestaetigung waere ein Betrag, den niemand uebernommen
  -- hat.
  constraint insurance_premium_notices_history_needs_confirm
    check (amount_history_id is null or confirmed_at is not null),
  constraint insurance_premium_notices_confirmed_pair
    check ((confirmed_at is null) = (confirmed_by is null)),
  -- Ein Fristende ohne Handlungsdatum waere die halbe Auskunft: die Oberflaeche
  -- zeigt beide, und der Countdown haengt am Handlungsdatum.
  constraint insurance_premium_notices_window_triple
    check (special_termination_until is null
           or (safe_service_on is not null and recommended_action_on is not null))
);

-- Arbeitsvorrat des Pushes: offene Fenster, noch nicht zur Kenntnis genommen.
create index if not exists insurance_premium_notices_open_idx
  on insurance_premium_notices (household_id, special_termination_until)
  where special_termination_until is not null and acknowledged_at is null;
-- Verlauf und Treppe je Police, juengste Stufe zuerst.
create index if not exists insurance_premium_notices_policy_idx
  on insurance_premium_notices (household_id, policy_id, effective_on desc);

-- Derselbe Versionstrigger wie an der Police: er nimmt notified_at aus dem
-- Vergleich aus, damit der Nachtlauf kein If-Match entwertet.
drop trigger if exists insurance_premium_notices_bump_version on insurance_premium_notices;
create trigger insurance_premium_notices_bump_version
  before update on insurance_premium_notices
  for each row execute function bump_insurance_version();

-- Standardmuster (Plan 4.14): lesen darf jedes Mitglied, schreiben nur mit
-- Editorrecht. Ohne enable row level security waere die Tabelle wegen
-- "alter default privileges" haushaltsuebergreifend les- und schreibbar - und
-- das fiele in keinem Test auf, der nur einen Haushalt kennt.
alter table insurance_premium_notices enable row level security;

drop policy if exists insurance_premium_notices_read on insurance_premium_notices;
create policy insurance_premium_notices_read on insurance_premium_notices
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));

drop policy if exists insurance_premium_notices_write on insurance_premium_notices;
create policy insurance_premium_notices_write on insurance_premium_notices
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());

grant select, insert, update, delete on insurance_premium_notices to authenticated;

comment on table insurance_premium_notices is
  'Beitragsanpassung als eigener Vorgang mit Fristanker. Traegt den Inhalt des '
  'Anschreibens, nicht den Beitrag: der entsteht erst mit der Bestaetigung und '
  'ausschliesslich ueber die Vertragsseite (contract_amount_history). Eigene '
  'Tabelle statt zweier Spalten an contract_amount_history, weil der '
  'Abgleichsmotor die dortige offene Stufe ueberschreibt und dabei den '
  'Fristanker mitnaehme.';

comment on column insurance_premium_notices.received_on is
  'ZUGANG des Anschreibens - der Fristanker des Paragraphen 40 Abs. 1 S. 1 VVG. '
  'Nicht effective_on: die Monatsfrist laeuft ab Zugang, nicht ab Wirksamwerden.';

comment on column insurance_premium_notices.comparison_amount_minor is
  'Auf der Beitragsrechnung ausgewiesener Vergleichsbeitrag (Kfz: neue '
  'SF-Klasse ohne Typ-, Regional- und Tarifanpassung). Ist er gesetzt, wird '
  'gegen ihn geprueft und nicht gegen old_amount_minor.';

comment on column insurance_premium_notices.special_termination_until is
  'Rechtliches Fristende, gerechnet und gespeichert. Gespeichert nur als '
  'Dedup-Anker des Pushes - die API antwortet mit dem frisch gerechneten Stand.';


-- =============================================================================
-- Versicherungen, Phase P4: Beteiligte, Objekte, Deckungsbausteine
-- (docs/VERSICHERUNGEN-PLAN.md 4.3 P4-Nachtrag, 4.4 bis 4.6, 3.9, 3.10, 4.14)
-- =============================================================================
-- Die Reihenfolge in diesem Block ist nicht beliebig:
--   1. insurance_policy_persons steht ganz vorn, weil
--      health_can_manage_or_access() daraus liest.
--   2. Die beiden Funktionen stehen vor der RLS-Ersetzung an
--      insurance_policies, weil die Policy sie aufruft. Eine Policy, die eine
--      nicht existierende Funktion nennt, laesst sich gar nicht erst anlegen -
--      der Fehler faellt hier also sofort auf und nicht erst beim ersten Lesen.
--   3. Der P4-Nachtrag zu insurance_policies steht als GESCHLOSSENER Block
--      beieinander: Spalte, Fremdschluessel, CHECK-Ersetzung, Indexwechsel,
--      Triggerzweig und RLS-Verschaerfung haengen sachlich zusammen.
--
-- Leitsatz des Moduls gilt unveraendert: auch diese Tabellen modellieren kein
-- Geld. sum_insured_minor und deductible_minor an einer Deckung sind
-- Vertragsinhalt, kein Zahlungsstrom - sie stehen in keiner Fixkostensumme.

-- ---------- 11. insurance_policy_persons: Beteiligte an einer Police --------
-- KEINE zweite Personenliste. Die Personen kommen aus health_insured_persons
-- (Erstattungsmodul); diese Tabelle traegt nur die Rolle an der Police. Zwei
-- Wahrheiten ueber dieselben Menschen waeren der teuerste Fehler, den dieses
-- Modul machen koennte - die Kinder eines Haushalts stuenden dann zweimal da,
-- mit zwei Schreibweisen und zwei Geburtsdaten.
create table if not exists insurance_policy_persons (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  policy_id         uuid not null,
  insured_person_id uuid not null,
  -- Rollen aus GDV Anlage 7, reduziert auf das, was privat vorkommt. Bewusst
  -- englisch wie jedes andere Enum dieses Schemas; die deutschen Etiketten
  -- (Versicherungsnehmer, versicherte Person, mitversichert,
  -- bezugsberechtigt) sind Sache der Oberflaeche. 'insured' deckt die
  -- mitversicherte Person mit ab: der Unterschied ist die Frage, wer den
  -- Vertrag geschlossen hat, und die beantwortet 'policyholder'.
  role              text not null default 'insured' check (role in (
                      'policyholder','insured','premium_payer','beneficiary',
                      'driver','vehicle_keeper')),
  -- Doppelt belegt, je nach Rolle: Beihilfebemessungssatz bei 'insured'
  -- (7000 = 70 %), Bezugsanteil bei 'beneficiary' (Risikoleben: wer bekommt
  -- welchen Teil der Todesfallsumme). Ohne den zweiten Fall waere ein
  -- Bezugsrecht eine Rolle ohne Anteil.
  share_bp          int check (share_bp between 0 and 10000),
  valid_from        date,
  valid_to          date,
  note              text,
  created_at        timestamptz not null default now(),
  -- Ausfuehrungsvertrag Punkt 4: jede haushaltsgebundene Tabelle traegt
  -- unique (id, household_id), auch ohne heutigen Fremdschluessel darauf.
  unique (id, household_id),
  unique (household_id, policy_id, insured_person_id, role),
  foreign key (policy_id, household_id)
    references insurance_policies(id, household_id) on delete cascade,
  -- RESTRICT und nicht CASCADE: eine Person zu loeschen, die in einer Police
  -- steht, hiesse die Police still zu entwerten. Der Gesundheitszweig fuehrt
  -- Personen ohnehin ueber is_active weiter, statt sie zu entfernen.
  foreign key (insured_person_id, household_id)
    references health_insured_persons(id, household_id) on delete restrict,
  check (valid_to is null or valid_from is null or valid_to >= valid_from)
);
-- Genau ein Versicherungsnehmer je Police - er ist der Vertragspartner, nicht
-- eine von mehreren Beteiligtenrollen.
create unique index if not exists insurance_policy_persons_holder_uniq
  on insurance_policy_persons (policy_id) where role = 'policyholder';
-- Treibt den Filter "welche Policen betreffen dieses Kind" und die
-- Personenbedingung der RLS.
create index if not exists insurance_policy_persons_person_idx
  on insurance_policy_persons (household_id, insured_person_id);
create index if not exists insurance_policy_persons_policy_idx
  on insurance_policy_persons (household_id, policy_id);

alter table insurance_policy_persons enable row level security;
drop policy if exists insurance_policy_persons_read on insurance_policy_persons;
create policy insurance_policy_persons_read on insurance_policy_persons
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_policy_persons_write on insurance_policy_persons;
create policy insurance_policy_persons_write on insurance_policy_persons
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_policy_persons to authenticated;

-- ---------- 12. insured_person_label: ein Name, keine Zeile -----------------
-- ENTSCHEIDUNG (Plan 4.4): eine FUNKTION, ausdruecklich weder Spalte noch View.
--
--   * Eine Spalte an insurance_policy_persons waere eine Kopie des Namens und
--     damit eine zweite Wahrheit: eine Umbenennung im Erstattungsmodul ginge
--     an ihr vorbei, und die Kopie ueberlebte sogar den Entzug der
--     Gesundheitsfreigabe. Ein Name, der nach dem Entzug noch dasteht, ist
--     genau der Fehler, den der ganze Abschnitt vermeiden soll.
--   * Eine View haette ihre eigene, unsichtbare Rechtelage: sie laeuft mit den
--     Rechten ihres Eigentuemers, solange nicht security_invoker gesetzt ist -
--     die Read-Policy von health_insured_persons waere damit umgangen, ohne
--     dass man das der Tabelle ansieht. Ein Rechtekonstrukt, das man beim
--     Lesen des Schemas nicht bemerkt, ist die schlechtere Loesung, auch wenn
--     es dasselbe Ergebnis liefert.
--   * Die Funktion exponiert genau EIN Feld, an genau einer Stelle (der
--     SELECT-Liste des Versicherungsdienstes), und laesst
--     health_can_access_person buchstaeblich unveraendert - das ist
--     Abnahmekriterium der Phase.
--
-- Sie gibt null zurueck, wenn die Person nicht zum aktuellen Haushalt gehoert.
-- Damit ist sie zugleich die Existenzpruefung des Dienstes: "kein Name" und
-- "gibt es hier nicht" sind derselbe Zustand.
create or replace function insured_person_label(p_person_id uuid)
returns text
language sql
stable
security definer
set search_path = public
as $$
  select p.display_name
    from health_insured_persons p
   where p.id = p_person_id
     and p.household_id = current_household_id()
$$;

-- ---------- 13. health_can_manage_or_access: Gesundheitsschutz je Police ----
-- Der Gesundheitsschutz haengt am Erstattungsmodul, nicht an einem zweiten
-- Rechtesystem: diese Funktion ruft health_can_access_person() auf und
-- entscheidet nichts selbst. Sie uebersetzt lediglich "Zugriff auf eine
-- Person" in "Zugriff auf eine Police".
--
-- Zur Form, dreimal begruendet:
--   1. KEIN Personenbezug -> sichtbar. Eine PKV-Police ohne benannte Person
--      enthaelt kein Personendatum; sie zu verbergen naehme dem Haushalt die
--      Uebersicht, ohne irgendetwas zu schuetzen. Genau diese Regel macht die
--      P1-Fassung der Policy fuer den P1-Bestand gleichwertig.
--   2. MINDESTENS EINE zugaengliche Person genuegt. Bei "alle" waere eine
--      Familienpolice fuer jeden unsichtbar, sobald ein Beteiligter fremd ist;
--      der Nutzer sieht die Police, deren Personen er ohnehin kennt.
--   3. Der Name sagt "manage_or_access", die Pruefung nennt aber nur
--      health_can_access_person(). Das ist kein Versehen: jene Funktion ist
--      bereits das Oder aus Owner, 'read'- und 'manage'-Freigabe. Eine
--      zusaetzliche manage-Pruefung wuerde einem lesend Freigegebenen die
--      Police entziehen, obwohl er die Person sehen darf - und sie stuende in
--      der Read-Policy, wo Schreibrechte nichts zu suchen haben.
create or replace function health_can_manage_or_access(p_policy_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select not exists (
           select 1 from insurance_policy_persons pp
            where pp.policy_id = p_policy_id
              and pp.household_id = current_household_id()
         )
      or exists (
           select 1 from insurance_policy_persons pp
            where pp.policy_id = p_policy_id
              and pp.household_id = current_household_id()
              and health_can_access_person(pp.insured_person_id)
         )
$$;

-- ===================================================================
-- 14. P4-NACHTRAG zu insurance_policies (Plan 4.3, geschlossener Block)
-- ===================================================================

-- (1) Buendelpolice (3.9): Kasko unter Kfz-Haftpflicht, Elementar unter
--     Wohngebaeude. Nur EINE Ebene - eine Kindpolice hat selbst kein Kind.
alter table insurance_policies add column if not exists parent_policy_id uuid;

alter table insurance_policies drop constraint if exists insurance_policies_parent_fk;
alter table insurance_policies add constraint insurance_policies_parent_fk
  foreign key (parent_policy_id, household_id)
  references insurance_policies(id, household_id) on delete cascade;

-- (2) Der self_reference-CHECK wird ERSETZT, nicht ergaenzt.
alter table insurance_policies drop constraint if exists insurance_policies_self_reference;
alter table insurance_policies add constraint insurance_policies_self_reference
  check (parent_policy_id is distinct from id
         and predecessor_policy_id is distinct from id);

create index if not exists insurance_policies_parent_idx
  on insurance_policies (household_id, parent_policy_id) where parent_policy_id is not null;

-- (3) Der Nummern-Index wird ERSETZT, nicht ergaenzt: eindeutig ist die
--     Versicherungsscheinnummer jetzt nur noch unter WURZELpolicen. Der
--     Buendelfall (Kfz-Haftpflicht + Vollkasko, Wohngebaeude + Elementar,
--     Hausrat + Glas) traegt dieselbe Nummer beim selben Versicherer.
--     Drop und Create statt "if not exists": mit "if not exists" bliebe beim
--     zweiten Einspielen die alte, engere Fassung stehen und der Buendelfall
--     waere je nach Einspielreihenfolge verboten oder erlaubt.
drop index if exists insurance_policies_number_uniq;
create unique index insurance_policies_number_uniq
  on insurance_policies (household_id, lower(insurer_name), policy_number_norm)
  where policy_number_norm is not null
    and parent_policy_id is null
    and status <> 'entered_in_error';

-- (4) Fachtrigger, als Ganzes neu gesetzt. Die Zweige (a) bis (d) sind
--     wortgleich zur P1-Fassung; neu ist ausschliesslich (e).
create or replace function validate_insurance_policy() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_field text;
  v_kind  text;
  v_basis text;
  v_sum   int;
  v_parent uuid;
  v_confirm text := coalesce(current_setting('insurance.main_due_confirm', true), '');
  v_terms   text := coalesce(current_setting('insurance.notice_terms_confirm', true), '');
begin
  -- (a) is_health_scoped ist Serverwissen. deadline_kind und
  --     anchor_basis_kind haben bewusst KEINEN Spaltendefault; der
  --     BEFORE-Trigger fuellt sie aus dem Katalog, bevor die Not-Null-Pruefung
  --     greift. Beim UPDATE bleibt ein vom Nutzer gesetzter Wert stehen.
  select risk_field, default_deadline_kind, default_anchor_basis_kind
    into v_field, v_kind, v_basis
    from insurance_lines where line_key = new.line_key;
  new.is_health_scoped := (v_field = 'health');
  if new.deadline_kind is null then
    new.deadline_kind := coalesce(v_kind, 'kuendigungsfrist');
  end if;
  if new.anchor_basis_kind is null then
    new.anchor_basis_kind := coalesce(v_basis, 'policy_terms');
  end if;

  -- (b) HERKUNFT DES ANKERDATUMS ist Serverwissen - und der Trigger SETZT
  --     sie, er ergaenzt sie nicht. Ohne die folgenden Zuweisungen koennte
  --     ein Client beim INSERT 'user_confirmed' behaupten, und ein UPDATE
  --     ohne Datumsaenderung koennte die Herkunft nachtraeglich hochstufen.
  if tg_op = 'INSERT' then
    new.main_due_source       := 'catalog_suggested';
    new.main_due_confirmed_at := null;
    new.main_due_confirmed_by := null;
  else
    new.main_due_source       := old.main_due_source;
    new.main_due_confirmed_at := old.main_due_confirmed_at;
    new.main_due_confirmed_by := old.main_due_confirmed_by;
  end if;

  --     Bestaetigt wird NUR ueber den ausdruecklichen Bestaetigungspfad, der
  --     die Sitzungsvariable setzt - nicht ueber "irgendein UPDATE mit
  --     gesetzter auth.uid()". Sonst stempelt der zweite Uebernahmelauf ein
  --     Datum als bestaetigt, das niemand angesehen hat.
  if v_confirm = 'user' and new.main_due_on is not null then
    new.main_due_source       := 'user_confirmed';
    new.main_due_confirmed_at := now();
    new.main_due_confirmed_by := auth.uid();
  end if;

  --     Kein Datum, keine Herkunft - das raeumt auch eine fruehere
  --     Bestaetigung ab, und zwar absichtlich: wer das Datum loescht, hat
  --     nichts mehr bestaetigt.
  if new.main_due_on is null then
    new.main_due_source       := 'not_computable';
    new.main_due_confirmed_at := null;
    new.main_due_confirmed_by := null;
  end if;

  -- (c) HERKUNFT DER FRISTLAENGE, nach demselben Muster.
  if tg_op = 'UPDATE' then
    new.notice_terms_source       := old.notice_terms_source;
    new.notice_terms_confirmed_at := old.notice_terms_confirmed_at;
    new.notice_terms_confirmed_by := old.notice_terms_confirmed_by;
  end if;
  if v_terms = 'user' then
    new.notice_terms_source       := 'user_confirmed';
    new.notice_terms_confirmed_at := now();
    new.notice_terms_confirmed_by := auth.uid();
  end if;

  -- (d) Buendelanteile duerfen den Vertragsbeitrag nicht ueberschreiten.
  --     Ab P4 wirksam, weil erst jetzt zwei Policen an einem Vertrag haengen
  --     koennen. Bewusst "<= 10000" statt "= 10000": sonst waere das Anlegen
  --     einer einzelnen Police ein Sonderfall. Der nicht zugeordnete Rest ist
  --     sichtbar, nicht verboten.
  if new.contract_id is not null then
    select coalesce(sum(premium_share_bp), 0) into v_sum
      from insurance_policies
     where contract_id = new.contract_id
       and household_id = new.household_id
       and status not in ('ended','entered_in_error')
       and id is distinct from new.id;
    if v_sum + new.premium_share_bp > 10000 then
      raise exception using errcode = '23514',
        message = 'INSURANCE_PREMIUM_SHARE_EXCEEDED';
    end if;
  end if;

  -- (e) P4: genau EINE Buendelebene, kein Baum. Ohne diese Pruefung waere
  --     "Wurzelpolice" im Nummern-Index oben nicht mehr bestimmbar - der
  --     Index laesst genau die Zeilen mit parent_policy_id is null zu, und
  --     ein Enkel truege dieselbe Nummer wie sein Grossvater, ohne dass ihn
  --     der Index sieht.
  if new.parent_policy_id is not null then
    select parent_policy_id into v_parent
      from insurance_policies
     where id = new.parent_policy_id and household_id = new.household_id;
    if v_parent is not null then
      raise exception using errcode = '23514',
        message = 'INSURANCE_POLICY_NESTING_TOO_DEEP';
    end if;
    -- Dieselbe Regel abwaerts gelesen. Der Plan schreibt nur die
    -- Aufwaertspruefung vor; ohne die Gegenrichtung entstuende dieselbe
    -- zweite Ebene ueber den Umweg "eine Wurzel mit Kindern bekommt
    -- nachtraeglich selbst einen Elternteil".
    if exists (
      select 1 from insurance_policies
       where parent_policy_id = new.id and household_id = new.household_id
    ) then
      raise exception using errcode = '23514',
        message = 'INSURANCE_POLICY_NESTING_TOO_DEEP';
    end if;
  end if;

  return new;
end $$;

-- Der Trigger selbst wird NICHT neu erzeugt - er zeigt bereits auf die
-- Funktion, und "create or replace function" tauscht den Rumpf unter ihm aus.

-- (5) RLS-VERSCHAERFUNG. Beide Policies werden ERSETZT, nicht ergaenzt.
--     Die Bedingung steht in "using" UND in "with check": ohne den zweiten
--     Teil koennte ein Editor ohne Gesundheitsfreigabe eine Police zwar nicht
--     lesen, aber anlegen und aendern - und damit Daten schreiben, die er
--     danach nicht mehr sieht.
drop policy if exists insurance_policies_read on insurance_policies;
create policy insurance_policies_read on insurance_policies
  for select to authenticated
  using (
    household_id = current_household_id()
    and is_household_member(household_id)
    and (not is_health_scoped or health_can_manage_or_access(id))
  );

drop policy if exists insurance_policies_write on insurance_policies;
create policy insurance_policies_write on insurance_policies
  for all to authenticated
  using (
    household_id = current_household_id()
    and is_household_member(household_id)
    and can_edit_household()
    and (not is_health_scoped or health_can_manage_or_access(id))
  )
  with check (
    household_id = current_household_id()
    and is_household_member(household_id)
    and can_edit_household()
    and (not is_health_scoped or health_can_manage_or_access(id))
  );

-- ---------- 15. insured_objects: die versicherte Sache ----------------------
create table if not exists insured_objects (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  kind          text not null check (kind in
                  ('vehicle','property','pet','device','bike','other')),
  label         text not null check (length(btrim(label)) between 1 and 160),
  -- Bruecke zum Vermoegensmodul statt Zweitmodellierung des Werts. Der Wert
  -- eines Hauses steht in assets_and_debts; hier steht, dass es versichert ist.
  asset_id      uuid,
  address_line  text, postal_code text, city text,
  living_area_sqm int check (living_area_sqm between 1 and 2000),
  build_year    int check (build_year between 1500 and 2200),
  attrs         jsonb not null default '{}'::jsonb,
  -- PRIMAERER Anker der Erwerberfrist nach Paragraph 96 Abs. 2 S. 2 VVG
  -- ("innerhalb eines Monats nach dem Erwerb"). Ein Modell, das nur ein
  -- Kenntnisdatum kennt, macht den gesetzlichen Auffangfall zum Regelfall.
  -- Fristbeginn nach Paragraph 187 Abs. 1, 188 Abs. 2 BGB am Folgetag, Ende
  -- mit Ablauf des datumsgleichen Tages des Folgemonats.
  --
  -- KEINE HERKUNFTSSPALTE, und zwar begruendet: acquired_on und disposed_on
  -- sind IMMER Nutzereingabe. Sie werden nie aus einem anderen Feld
  -- abgeleitet - anders als main_due_on und notice_period_months, wo es eine
  -- zweite Herkunft (Katalog, Umrechnung) tatsaechlich gibt.
  acquired_on   date,
  -- Veraeusserung (Paragraphen 95 Abs. 1, 97 Abs. 1 VVG). Ein abgegebenes
  -- Objekt wird nicht geloescht - sonst verlieren alte Buchungen und Schaeden
  -- ihren Bezug. ACHTUNG: Dieses Datum erzeugt KEINE Kuendigungsfrist. Der
  -- Veraeusserer hat kein Kuendigungsrecht, er hat eine Anzeigepflicht. Es
  -- erzeugt eine Aufgabe, keinen Eintrag in GET /v1/insurance/deadlines.
  disposed_on   date,
  check (disposed_on is null or acquired_on is null or disposed_on >= acquired_on),
  is_active     boolean not null default true,
  version       int not null default 1,
  created_by    uuid references auth.users(id) on delete set null default auth.uid(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (id, household_id),
  -- SPALTENWEISE, siehe 4.3: ohne '(asset_id)' faellt auch household_id auf
  -- null und das Loeschen einer Vermoegenszeile scheitert an not null.
  foreign key (asset_id, household_id)
    references assets_and_debts(id, household_id) on delete set null (asset_id)
);

create index if not exists insured_objects_kind_idx
  on insured_objects (household_id, kind) where is_active;
create index if not exists insured_objects_asset_idx
  on insured_objects (household_id, asset_id) where asset_id is not null;

-- Derselbe Versionstrigger wie an der Police. insured_objects hat kein
-- notified_at; der Ausschluss laeuft dort ins Leere und stoert nicht.
drop trigger if exists insured_objects_bump_version on insured_objects;
create trigger insured_objects_bump_version before update on insured_objects
  for each row execute function bump_insurance_version();

alter table insured_objects enable row level security;
drop policy if exists insured_objects_read on insured_objects;
create policy insured_objects_read on insured_objects
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insured_objects_write on insured_objects;
create policy insured_objects_write on insured_objects
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insured_objects to authenticated;

-- ---------- 16. insured_object_registrations: Halter und Kennzeichen --------
-- Kennzeichen und Halter in 'attrs' zu fuehren hiesse, sie beim Bearbeiten zu
-- ueberschreiben - und damit die Zuordnung alter Buchungen und Schaeden zu
-- verlieren. Ein Kennzeichenwechsel ist eine neue Zeile, keine Aenderung.
create table if not exists insured_object_registrations (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  insured_object_id uuid not null,
  plate             text check (length(btrim(plate)) between 2 and 20),
  keeper_person_id  uuid,
  valid_from        date not null,
  valid_to          date,
  note              text,
  created_at        timestamptz not null default now(),
  unique (id, household_id),
  foreign key (insured_object_id, household_id)
    references insured_objects(id, household_id) on delete cascade,
  -- SPALTENWEISE set null, siehe Ausfuehrungsvertrag 4a: eine geloeschte
  -- Person darf die Historienzeile nicht mitnehmen, aber auch nicht deren
  -- household_id auf null ziehen.
  foreign key (keeper_person_id, household_id)
    references health_insured_persons(id, household_id)
    on delete set null (keeper_person_id),
  check (valid_to is null or valid_to >= valid_from)
);
create index if not exists insured_object_registrations_object_idx
  on insured_object_registrations (household_id, insured_object_id, valid_from desc);
-- Hoechstens eine laufende Zeile je Objekt. Zwei offene Kennzeichen zugleich
-- waeren nicht Historie, sondern Widerspruch.
create unique index if not exists insured_object_registrations_open_uniq
  on insured_object_registrations (insured_object_id) where valid_to is null;

alter table insured_object_registrations enable row level security;
drop policy if exists insured_object_registrations_read on insured_object_registrations;
create policy insured_object_registrations_read on insured_object_registrations
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insured_object_registrations_write on insured_object_registrations;
create policy insured_object_registrations_write on insured_object_registrations
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insured_object_registrations to authenticated;

-- ---------- 17. insurance_policy_objects: Police zu Objekt ------------------
create table if not exists insurance_policy_objects (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  policy_id         uuid not null,
  insured_object_id uuid not null,
  note              text,
  created_at        timestamptz not null default now(),
  unique (id, household_id),
  unique (household_id, policy_id, insured_object_id),
  foreign key (policy_id, household_id)
    references insurance_policies(id, household_id) on delete cascade,
  -- RESTRICT: ein Objekt, das noch in einer Police steht, wird nicht
  -- weggeloescht. Der Ausweg ist disposed_on, nicht das Loeschen.
  foreign key (insured_object_id, household_id)
    references insured_objects(id, household_id) on delete restrict
);
create index if not exists insurance_policy_objects_object_idx
  on insurance_policy_objects (household_id, insured_object_id);

alter table insurance_policy_objects enable row level security;
drop policy if exists insurance_policy_objects_read on insurance_policy_objects;
create policy insurance_policy_objects_read on insurance_policy_objects
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_policy_objects_write on insurance_policy_objects;
create policy insurance_policy_objects_write on insurance_policy_objects
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_policy_objects to authenticated;

-- ---------- 18. insurance_coverages: die erfassten Bausteine ----------------
-- Der Feldsatz folgt riskine/ontology coverage.json (Apache-2.0): dieselbe
-- Vierergruppe enthalten / Selbstbeteiligung / Versicherungssumme / Wartezeit
-- traegt Hausrat mit ueber 25 Bausteinen genauso wie Haftpflicht mit 15 - das
-- erspart eine Tabelle je Sparte.
create table if not exists insurance_coverages (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references households(id) on delete cascade
                        default current_household_id(),
  policy_id           uuid not null,
  coverage_key        text not null references insurance_coverage_keys(coverage_key)
                        on update cascade,
  -- Ausdruecklich AUSGESCHLOSSEN ist eine eigene Aussage, kein fehlender Wert:
  -- "Elementar ist nicht eingeschlossen" beantwortet die Frage des
  -- Bedarfsabgleichs, "keine Zeile" beantwortet sie nicht.
  is_included         boolean not null default true,
  sum_insured_minor   bigint check (sum_insured_minor >= 0),
  deductible_minor    bigint check (deductible_minor >= 0),
  benefit_rate_bp     int check (benefit_rate_bp between 0 and 10000),
  waiting_until       date,
  note                text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (id, household_id),
  unique (household_id, policy_id, coverage_key),
  foreign key (policy_id, household_id)
    references insurance_policies(id, household_id) on delete cascade
);
create index if not exists insurance_coverages_key_idx
  on insurance_coverages (household_id, coverage_key) where is_included;

alter table insurance_coverages enable row level security;
drop policy if exists insurance_coverages_read on insurance_coverages;
create policy insurance_coverages_read on insurance_coverages
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_coverages_write on insurance_coverages;
create policy insurance_coverages_write on insurance_coverages
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_coverages to authenticated;

-- ---------- 19. Kommentare ---------------------------------------------------
comment on table insurance_policy_persons is
  'Beteiligte an einer Police, mit Rolle und Anteil. Die Personen selbst '
  'kommen aus health_insured_persons - eine zweite Personenliste waere eine '
  'zweite Wahrheit ueber dieselben Menschen. Fuer die Anzeige liefert '
  'insured_person_label() den Namen, ohne die Zeile freizugeben.';

comment on function health_can_manage_or_access(uuid) is
  'Gesundheitsschutz auf Policenebene: eine Police ohne Personenbezug ist fuer '
  'jedes Mitglied sichtbar, eine gesundheitsnahe Police mit Personenbezug nur '
  'fuer den, der mindestens eine der Personen sehen darf. Entscheidet nichts '
  'selbst, sondern ruft health_can_access_person() des Erstattungsmoduls.';

comment on column insured_objects.acquired_on is
  'Erwerbsdatum - primaerer Fristanker des Paragraphen 96 Abs. 2 S. 2 VVG beim '
  'Fahrzeugkauf. Immer Nutzereingabe, nie abgeleitet; deshalb ohne '
  'Herkunftsspalte.';

comment on table insured_object_registrations is
  'Halter- und Kennzeichenhistorie. Ein Kennzeichenwechsel ist eine neue Zeile '
  'mit valid_from, nicht das Ueberschreiben der alten - sonst verlieren alte '
  'Buchungen und Schaeden ihren Bezug.';

comment on table insurance_coverages is
  'Tatsaechlich vereinbarte Deckungsbausteine einer Police gegen den globalen '
  'Katalog insurance_coverage_keys. Sobald eine Police Bausteine traegt, '
  'beschreibt sie ihren Deckungsumfang selbst - die Oberflaeche wechselt dann '
  'von "Typischerweise enthalten" auf "In dieser Police enthalten".';

-- ---------- 20. Seed: Spartenkatalog, Welle 2 (Plan 3.10) --------------------
-- Achtzehn Zeilen. Sie kommen mit P4 und nicht frueher, weil sie saemtlich
-- entweder ein Objekt verlangen (requires_object_kind) oder die
-- Einzelpolicenform eines Bausteins sind - beides gibt es erst mit diesem
-- Block.
--
-- Die Schreibregel fuer purpose_short und purpose_long gilt unveraendert
-- (siehe ueber dem Seed der Welle K):
--   1. Der Text beschreibt die SPARTE, nie die Police des Lesers.
--   2. Keine Wertung und keine Absolutheit - gewertet wird ausschliesslich in
--      need_reason, dort mit Quelle und Pruefdatum.
--   3. Eine Aussage ueber Pflicht, Frist oder Haftung nur mit Fundstelle im
--      selben Feld.
--   4. Leistungsaussagen mit "je nach Tarif" oder "nach den Bedingungen".
--   5. purpose_short ist genau ein Satz.
insert into insurance_lines (
  line_key, risk_field, gdv_sparte, label, purpose_short, purpose_long,
  typically_covers, typically_not_covers, is_placeholder,
  need_tier, need_reason, source_url, source_checked_on,
  default_notice_anchor, default_anchor_basis_kind, default_notice_period_months,
  default_main_due_md, default_fixed_month, default_fixed_day,
  default_min_term_months, default_deadline_kind,
  default_claim_termination_basis, special_law, completeness_fields,
  covered_by_keys, requires_object_kind, sort_order
) values

  -- --- Haftung, Welle 2 ----------------------------------------------------
  ('animal_liability', 'liability', '040', 'Tierhalterhaftpflichtversicherung',
   'Zahlt Schaeden, die ein gehaltenes Tier Dritten zufuegt.',
   'Der Tierhalter haftet nach Paragraph 833 Satz 1 BGB auch ohne eigenes Verschulden fuer Schaeden, die das Tier verursacht. Die Tierhalterhaftpflichtversicherung uebernimmt berechtigte Ansprueche bis zur Deckungssumme und wehrt unberechtigte ab. Fuer Hunde und Pferde kennen mehrere Bundeslaender eigene Landesgesetze mit einer Versicherungspflicht; welche Fassung gilt, richtet sich nach dem Wohnort.',
   '{Personenschaeden,Sachschaeden,Vermoegensschaeden}', '{Schaeden_am_eigenen_Tier,Schaeden_des_Halters}',
   false, 'S1', 'Die Haftung des Tierhalters nach Paragraph 833 Satz 1 BGB ist der Hoehe nach nicht begrenzt; die Verbraucherzentrale zaehlt die Sparte bei Hunden und Pferden zu den einschlaegigen (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/bgb/__833.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', 'pet', 150),

  ('landowner_liability', 'liability', '040', 'Haus- und Grundbesitzerhaftpflichtversicherung',
   'Zahlt Schaeden aus der Verkehrssicherungspflicht fuer ein vermietetes oder unbebautes Grundstueck.',
   'Wer ein Grundstueck oder Gebaeude besitzt, haftet fuer Schaeden aus verletzter Verkehrssicherungspflicht, etwa nach einem Sturz auf nicht geraeumtem Gehweg; bei sich abloesenden Gebaeudeteilen greift zusaetzlich Paragraph 836 BGB. Fuer selbst bewohntes Wohneigentum ist dieses Risiko ueblicherweise in der Privathaftpflichtversicherung enthalten, fuer vermietete und unbebaute Grundstuecke nach den Bedingungen dagegen in der Regel nicht.',
   '{Verkehrssicherungspflicht,Personenschaeden,Sachschaeden}', '{Schaeden_am_Gebaeude_selbst,Mietausfall}',
   false, 'S1', 'Bei Vermietung einschlaegig, weil die Privathaftpflichtversicherung das vermietete Objekt nach ihren Bedingungen ueblicherweise ausschliesst (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/bgb/__836.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', 'property', 160),

  ('water_damage_liability', 'liability', '040', 'Gewaesserschadenhaftpflichtversicherung',
   'Zahlt Schaeden durch austretende wassergefaehrdende Stoffe, typischerweise Heizoel aus einem Tank.',
   'Wer eine Anlage mit wassergefaehrdenden Stoffen betreibt, haftet nach Paragraph 89 Absatz 2 WHG ohne Verschulden fuer Gewaesserschaeden. Die Kosten der Bodensanierung uebersteigen den Wert des ausgelaufenen Oels regelmaessig um ein Vielfaches. Deckungssumme, Tankvolumen und Bauart der Anlage stehen im Vertrag.',
   '{Bodensanierung,Gewaesserschaeden,Abwehr_unberechtigter_Ansprueche}', '{Schaeden_am_Tank_selbst}',
   false, 'S1', 'Bei eigenem Oeltank einschlaegig: die Haftung nach Paragraph 89 Absatz 2 WHG trifft den Anlagenbetreiber verschuldensunabhaengig und ist der Hoehe nach nicht begrenzt (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/whg_2009/__89.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', 'property', 170),

  ('builder_liability', 'liability', '040', 'Bauherrenhaftpflichtversicherung',
   'Zahlt Schaeden, die Dritten auf oder durch eine Baustelle entstehen.',
   'Die Verkehrssicherungspflicht fuer eine Baustelle trifft den Bauherrn nach Paragraph 823 BGB auch dann, wenn ein beauftragtes Unternehmen baut; die Beauftragung allein beendet die Pflicht nicht. Der Vertrag laeuft ueber die Bauzeit und bemisst sich in Beitrag und Dauer nach der Bausumme.',
   '{Personenschaeden,Sachschaeden,Abwehr_unberechtigter_Ansprueche}', '{Schaeden_am_Bauwerk_selbst,Mangelbeseitigung}',
   false, 'S1', 'Bei eigenem Bauvorhaben einschlaegig, weil die Verkehrssicherungspflicht nach Paragraph 823 BGB beim Bauherrn bleibt (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/bgb/__823.html', date '2026-08-14',
   'per_event', 'policy_terms', null, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,sum_insured_minor,starts_on,ends_on}', '{}', 'property', 180),

  ('drone_liability', 'liability', '040', 'Drohnenhaftpflichtversicherung',
   'Zahlt Schaeden, die ein unbemanntes Fluggeraet Dritten zufuegt.',
   'Paragraph 43 Absatz 2 LuftVG verlangt vom Halter eines unbemannten Fluggeraets eine Haftpflichtversicherung; die Halterhaftung nach Paragraph 33 LuftVG besteht ohne Verschulden. Private Haftpflichtvertraege schliessen Luftfahrzeuge nach ihren Bedingungen ueblicherweise aus. Der Umfang der Pflicht haengt von Geraet und Nutzung ab.',
   '{Personenschaeden,Sachschaeden}', '{Schaeden_am_Fluggeraet_selbst,gewerbliche_Nutzung_ohne_Einschluss}',
   false, 'S0', 'Paragraph 43 Absatz 2 LuftVG macht die Haftpflichtversicherung fuer Halter unbemannter Fluggeraete zur Pflicht (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/luftvg/__43.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', 'device', 190),

  ('hunting_liability', 'liability', '040', 'Jagdhaftpflichtversicherung',
   'Zahlt Schaeden aus der Jagdausuebung an Personen und Sachen Dritter.',
   'Paragraph 17 Absatz 1 Nummer 4 BJagdG macht den Nachweis einer Jagdhaftpflichtversicherung mit festgelegten Mindestsummen zur Voraussetzung fuer den Jagdschein. Versichert sind Personen- und Sachschaeden aus der Jagd einschliesslich des Fuehrens der Waffe; Wildschaeden an fremden Feldern regelt der Jagdpachtvertrag.',
   '{Personenschaeden,Sachschaeden,Waffengebrauch}', '{Wildschaeden_am_Feld}',
   false, 'S0', 'Ohne den Nachweis nach Paragraph 17 Absatz 1 Nummer 4 BJagdG wird der Jagdschein nicht erteilt (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/bjagdg/__17.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', null, 200),

  ('boat_liability', 'liability', '040', 'Wassersport-Haftpflichtversicherung',
   'Zahlt Schaeden, die ein Wasserfahrzeug Dritten zufuegt.',
   'Der Halter eines Wasserfahrzeugs haftet nach Paragraph 823 BGB fuer Schaeden aus dessen Betrieb; private Haftpflichtvertraege schliessen motorisierte Boote nach ihren Bedingungen ueblicherweise aus. Einzelne Gewaesser und Haefen verlangen in der Benutzungsordnung einen Versicherungsnachweis. Massgeblich fuer Beitrag und Annahme sind Fahrtgebiet, Bootstyp und Motorleistung.',
   '{Personenschaeden,Sachschaeden}', '{Schaeden_am_eigenen_Boot,Regattateilnahme_ohne_Einschluss}',
   false, 'S0', 'Auf einzelnen Gewaessern und in einzelnen Haefen ist der Nachweis Zugangsvoraussetzung; ausserhalb davon besteht keine allgemeine Pflicht (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/bgb/__823.html', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', 'vehicle', 210),

  -- --- Eigentum, Welle 2 ---------------------------------------------------
  ('construction_works', 'property', '152', 'Bauleistungsversicherung',
   'Ersetzt unvorhergesehene Schaeden am entstehenden Bauwerk waehrend der Bauzeit.',
   'Die Bauleistungsversicherung traegt Schaeden an der Bauleistung, je nach Bedingungen durch Sturm, Frost, Vandalismus und den Diebstahl bereits fest verbauter Teile. Sie laeuft ueber die Bauzeit und endet mit der Abnahme; Selbstbeteiligung und ausgeschlossene Ursachen stehen in den Bedingungen.',
   '{Bauleistung,Sturmschaeden,Diebstahl_verbauter_Teile}', '{Mangelbeseitigung,Diebstahl_von_Baumaterial_auf_Lager}',
   false, 'S1', 'Bei eigenem Bauvorhaben einschlaegig, weil der Bauherr die Gefahr fuer die Bauleistung bis zur Abnahme traegt (Paragraph 644 BGB, Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/bgb/__644.html', date '2026-08-14',
   'per_event', 'policy_terms', null, null, null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,sum_insured_minor,deductible_minor,starts_on,ends_on}', '{}', 'property', 220),

  ('fire_shell', 'property', '140', 'Feuerrohbauversicherung',
   'Versichert Brandschaeden am Rohbau waehrend der Bauzeit.',
   'Die Feuerrohbauversicherung deckt Brand, Blitzschlag und Explosion am entstehenden Gebaeude. Viele Versicherer schliessen sie beitragsfrei in eine dort abgeschlossene Wohngebaeudeversicherung ein und lassen sie mit der Fertigstellung in diese uebergehen; ob das im konkreten Fall so vereinbart ist, steht im Vertrag.',
   '{Brand,Blitzschlag,Explosion}', '{Sturmschaeden_am_Rohbau,Diebstahl}',
   false, 'S1', 'Bei einem Neubau einschlaegig; sie entfaellt, wenn die Wohngebaeudeversicherung des Bauvorhabens sie bereits einschliesst (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/wohngebaeudeversicherung-13842', date '2026-08-14',
   'per_event', 'policy_terms', null, null, null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,sum_insured_minor,starts_on,ends_on}', '{}', 'property', 230),

  ('photovoltaic', 'property', '172', 'Photovoltaikversicherung',
   'Ersetzt Schaeden an einer Photovoltaikanlage und, wenn vereinbart, den Ertragsausfall.',
   'Die Photovoltaikversicherung deckt Beschaedigung, Diebstahl und Ueberspannungsschaeden an Modulen, Wechselrichtern und Verkabelung. Ob der Ertragsausfall nach einem Schaden und die Betreiberhaftpflicht eingeschlossen sind, haengt vom Tarif ab. In der Wohngebaeudeversicherung sind Anlagen je nach Bedingungen mitversichert oder ausgeschlossen.',
   '{Beschaedigung,Diebstahl,Ueberspannung}', '{Ertragsausfall_ohne_Einschluss,Verschleiss}',
   false, 'S2', 'Bei eigener Anlage situativ: der Sachschaden ist je nach Bedingungen bereits ueber die Wohngebaeudeversicherung gedeckt (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/energie/erneuerbare-energien/photovoltaikanlage-versichern-13898', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor,deductible_minor}', '{}', 'property', 240),

  -- Tierkrankenversicherung steht im Risikofeld 'property' und nicht in
  -- 'health': 'health' setzt ueber den Trigger is_health_scoped und damit den
  -- Gesundheitsschutz fuer PERSONENdaten. Ein Hund ist keine versicherte
  -- Person, und der Erstattungsvorgang eines Tierarztes gehoert nicht in den
  -- Zweig, der Arztrechnungen schuetzt.
  ('pet_health', 'property', '684', 'Tierkrankenversicherung',
   'Erstattet Tierarzt- und Operationskosten fuer ein benanntes Tier nach den vereinbarten Saetzen.',
   'Die Tierkrankenversicherung erstattet Behandlungs- und Operationskosten fuer ein einzelnes Tier. Erstattungssatz, Jahreshoechstgrenze, Wartezeit und der zugrunde gelegte Satz der Gebuehrenordnung fuer Tieraerzte stehen im Tarif und unterscheiden sich erheblich; Vorerkrankungen sind nach den Bedingungen ueblicherweise ausgeschlossen.',
   '{Operationskosten,Behandlungskosten}', '{Vorerkrankungen,Routineuntersuchungen_ohne_Einschluss}',
   false, 'S3', 'Optionaler Kostenschutz; die Verbraucherzentrale verweist auf die Alternative, den Betrag selbst zurueckzulegen (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/tierkrankenversicherung-fuer-hund-und-katze-50993', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'none', 'vvg40',
   '{policy_number,main_due_on,tariff_name,deductible_minor,waiting_period_days}', '{}', 'pet', 250),

  -- --- Einzelpolicenform der Bausteine (Plan 3.9, Ausnahmefall) ------------
  -- Regelfall ist der Baustein an der Hauptpolice. Diese sechs Zeilen sind
  -- fuer die echte eigenstaendige Police mit eigener Scheinnummer beim
  -- moeglicherweise anderen Versicherer - der Fall existiert, ist aber die
  -- Ausnahme. Der Zusatz "(Einzelpolice)" im Label steht deshalb dort, wo der
  -- Nutzer waehlt, und nicht erst in der Erklaerung.
  ('elemental', 'property', '124', 'Elementarschadenversicherung (Einzelpolice)',
   'Versichert Naturgefahren wie Ueberschwemmung, Starkregen, Erdrutsch und Schneedruck.',
   'Ueberschwemmung, Rueckstau, Starkregen, Erdrutsch, Schneedruck und Erdbeben sind in Hausrat- und Wohngebaeudevertraegen ueblicherweise nicht enthalten, sondern gesondert zu vereinbaren. Dieselbe Deckung gibt es als Baustein unter dem Schluessel elemental an der Hausrat- oder Wohngebaeudepolice; diese Zeile ist fuer die eigenstaendige Police gedacht. Beitrag und Annahme haengen von der Gefaehrdungszone der Adresse ab.',
   '{Ueberschwemmung,Starkregen,Erdrutsch,Schneedruck}', '{Sturmschaeden_ohne_Grundvertrag,Grundwasser_ohne_Einschluss}',
   false, 'S1', 'Bei Wohneigentum einschlaegig; die Verbraucherzentrale weist darauf hin, dass Naturgefahrenschaeden ohne diesen Einschluss vollstaendig selbst zu tragen sind (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/elementarschadenversicherung-13843', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor,deductible_minor}', '{elemental}', 'property', 260),

  ('glass', 'property', '110', 'Glasversicherung (Einzelpolice)',
   'Ersetzt Glasbruch im vereinbarten Umfang an Gebaeude- oder Mobiliarverglasung.',
   'Die Glasversicherung ersetzt Bruchschaeden an Scheiben, je nach Vertrag an der Gebaeudeverglasung, der Mobiliarverglasung oder an beiden. Dieselbe Deckung ist in Hausrat- und Wohngebaeudevertraegen als Baustein unter dem Schluessel glass zu haben; diese Zeile ist fuer die eigenstaendige Police gedacht.',
   '{Gebaeudeverglasung,Mobiliarverglasung}', '{Handydisplays,Brillenglaeser}',
   false, 'S4', 'Verbraucherzentrale und Stiftung Warentest halten die Sparte fuer die meisten Haushalte fuer verzichtbar, weil die Schadenhoehe je Fall gering bleibt (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/glasversicherung-13844', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{glass}', 'property', 270),

  ('bicycle', 'property', '130', 'Fahrradversicherung (Einzelpolice)',
   'Ersetzt Diebstahl und Beschaedigung eines namentlich versicherten Fahrrads.',
   'Die Fahrradversicherung deckt den Diebstahl auch ausserhalb verschlossener Raeume und je nach Tarif Reparaturkosten nach einem Sturz. In der Hausratversicherung ist der Fahrraddiebstahl als Baustein unter dem Schluessel bicycle enthalten, ueblicherweise begrenzt auf einen Anteil der Versicherungssumme; diese Zeile ist fuer die eigenstaendige Police gedacht.',
   '{Diebstahl,Beschaedigung}', '{Verschleiss,Diebstahl_ohne_Schloss_nach_Bedingungen}',
   false, 'S4', 'Als Einzelpolice von der Verbraucherzentrale fuer die meisten Haushalte als verzichtbar eingestuft, weil der Baustein in der Hausratversicherung denselben Fall abdeckt (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/fahrradversicherung-13845', date '2026-08-14',
   'term_end', 'statute', 3, null, null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{bicycle}', 'bike', 280),

  -- --- Mobilitaet, Welle 2 -------------------------------------------------
  ('motor_comprehensive', 'mobility', '052', 'Vollkaskoversicherung (Einzelpolice)',
   'Ersetzt Schaeden am versicherten Fahrzeug einschliesslich selbst verursachter Schaeden und Vandalismus.',
   'Die Vollkaskoversicherung schliesst die Teilkaskogefahren ein und ersetzt zusaetzlich selbst verschuldete Schaeden am versicherten Fahrzeug. Ueblicherweise wird sie als Baustein der Kfz-Haftpflichtversicherung gefuehrt; diese Zeile ist fuer die eigenstaendige Police mit eigener Versicherungsscheinnummer gedacht. Selbstbeteiligung, Schadenfreiheitsklasse und eine etwaige Werkstattbindung stehen im Vertrag; nach einem Schaden richtet sich das Sonderkuendigungsrecht nach Paragraph 92 VVG.',
   '{Unfallschaeden_am_eigenen_Fahrzeug,Vandalismus,Teilkaskogefahren}', '{Verschleiss,Schaeden_Dritter}',
   false, 'S2', 'Situativ: bei Finanzierung oder Leasing verlangen Bank und Leasinggeber sie regelmaessig vertraglich; sonst haengt die Einordnung vom Fahrzeugwert ab (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/kfzversicherung-13846', date '2026-08-14',
   'term_end', 'statute', 1, '01-01', null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,deductible_minor}', '{full_casco,partial_casco}', 'vehicle', 290),

  ('motor_partial', 'mobility', '053', 'Teilkaskoversicherung (Einzelpolice)',
   'Ersetzt am versicherten Fahrzeug Diebstahl-, Glas-, Wild-, Sturm- und Brandschaeden.',
   'Die Teilkaskoversicherung deckt die in den Bedingungen abschliessend genannten Gefahren und kennt keine Schadenfreiheitsklasse. Ueblicherweise wird sie als Baustein der Kfz-Haftpflichtversicherung gefuehrt; diese Zeile ist fuer die eigenstaendige Police mit eigener Versicherungsscheinnummer gedacht. Nach einem Schaden richtet sich das Sonderkuendigungsrecht nach Paragraph 92 VVG.',
   '{Diebstahl,Glasbruch,Wildunfall,Sturm,Brand}', '{selbst_verursachte_Unfallschaeden,Vandalismus}',
   false, 'S2', 'Situativ, abhaengig vom Fahrzeugwert; die Verbraucherzentrale nennt sie fuer aeltere Fahrzeuge als Alternative zur Vollkasko (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/kfzversicherung-13846', date '2026-08-14',
   'term_end', 'statute', 1, '01-01', null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,deductible_minor}', '{partial_casco}', 'vehicle', 300),

  ('passenger_accident', 'mobility', '054', 'Insassenunfallversicherung (Einzelpolice)',
   'Zahlt bei Unfallverletzungen von Mitfahrern nach den vereinbarten Summen.',
   'Die Insassenunfallversicherung leistet an verletzte Insassen unabhaengig von der Schuldfrage. Personenschaeden von Mitfahrern sind bereits ueber die Kfz-Haftpflichtversicherung gedeckt, soweit der Fahrer haftet; die Insassenunfallversicherung wirkt daneben und traegt die Faelle ohne Haftung.',
   '{Invaliditaet_nach_Verkehrsunfall,Unfalltod}', '{Sachschaeden,Schaeden_ausserhalb_des_Fahrzeugs}',
   false, 'S4', 'Verbraucherzentrale und Stiftung Warentest halten die Sparte fuer die meisten Haushalte fuer verzichtbar, weil die Kfz-Haftpflichtversicherung Personenschaeden von Mitfahrern bereits deckt (Stand 14.08.2026).',
   'https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen/kfzversicherung-13846', date '2026-08-14',
   'term_end', 'statute', 1, '01-01', null, null, null, 'kuendigungsfrist', 'vvg_92', 'vvg40',
   '{policy_number,main_due_on,sum_insured_minor}', '{}', 'vehicle', 310),

  ('moped', 'mobility', '600', 'Mopedversicherung (Versicherungskennzeichen)',
   'Versichert Roller und Mofa fuer das laufende Versicherungsjahr des Versicherungskennzeichens.',
   'Ein Kleinkraftrad darf nach Paragraph 26 FZV nur mit gueltigem Versicherungskennzeichen gefuehrt werden. Das Versicherungsjahr laeuft vom 1. Maerz bis zum Ende des Februars; der Vertrag endet damit, ohne dass es einer Kuendigung bedarf, und das Kennzeichen wird jaehrlich neu ausgegeben. Deckungsumfang und Kaskobausteine richten sich nach dem Tarif.',
   '{Personenschaeden,Sachschaeden_Dritter}', '{Schaeden_am_eigenen_Fahrzeug_ohne_Kasko}',
   false, 'S0', 'Ohne gueltiges Versicherungskennzeichen darf ein Kleinkraftrad nach Paragraph 26 FZV nicht gefuehrt werden (Stand 14.08.2026).',
   'https://www.gesetze-im-internet.de/fzv_2011/__26.html', date '2026-08-14',
   'fixed_date', 'statute', null, null, 2, 28, null, 'kuendigungsfrist', 'vvg_111', 'vvg40',
   '{policy_number,starts_on}', '{}', 'vehicle', 320)

on conflict (line_key) do update set
  risk_field = excluded.risk_field,
  gdv_sparte = excluded.gdv_sparte,
  label = excluded.label,
  purpose_short = excluded.purpose_short,
  purpose_long = excluded.purpose_long,
  typically_covers = excluded.typically_covers,
  typically_not_covers = excluded.typically_not_covers,
  is_placeholder = excluded.is_placeholder,
  need_tier = excluded.need_tier,
  need_reason = excluded.need_reason,
  source_url = excluded.source_url,
  source_checked_on = excluded.source_checked_on,
  default_notice_anchor = excluded.default_notice_anchor,
  default_anchor_basis_kind = excluded.default_anchor_basis_kind,
  default_notice_period_months = excluded.default_notice_period_months,
  default_main_due_md = excluded.default_main_due_md,
  default_fixed_month = excluded.default_fixed_month,
  default_fixed_day = excluded.default_fixed_day,
  default_min_term_months = excluded.default_min_term_months,
  default_deadline_kind = excluded.default_deadline_kind,
  default_claim_termination_basis = excluded.default_claim_termination_basis,
  special_law = excluded.special_law,
  completeness_fields = excluded.completeness_fields,
  covered_by_keys = excluded.covered_by_keys,
  requires_object_kind = excluded.requires_object_kind,
  sort_order = excluded.sort_order,
  updated_at = now();

-- =============================================================================
-- Versicherungen, Phase P5: Schaden- und Leistungsfaelle
-- (docs/VERSICHERUNGEN-PLAN.md 4.8, 4.10 P5-Block, 9.1, 3.12 Pruefung 3)
-- =============================================================================
-- Drei Saetze erklaeren fast jede Entscheidung in diesem Block:
--
-- 1. GESUNDHEIT BLEIBT BEIM ERSTATTUNGSMODUL. Krankheitskosten laufen ueber
--    health_expense_cases mit Traegersplit; ein Vorgang auf einer Police mit
--    is_health_scoped = true wird hier per Trigger abgewiesen. Zwei
--    Vorgangsmodelle fuer dieselbe Sache waeren die Zweitwahrheit, die dieser
--    Plan ueberall vermeidet.
-- 2. AUCH HIER ENTSTEHT KEIN GELD. claimed_minor und settled_minor sind der
--    Inhalt einer Abrechnung. Eine Zahlung ist erst dann eine Zahlung, wenn sie
--    an einer Buchung haengt - und der Weg dorthin ist derselbe wie im
--    Erstattungsmodul (health_transaction_allocations als Vorbild), nicht ein
--    zweiter Zuordnungsmechanismus.
-- 3. DER FRISTANKER IST NICHT DIE ENTSCHEIDUNG. Paragraph 92 Abs. 2 S. 1 VVG
--    knuepft an den ABSCHLUSS DER VERHANDLUNGEN ueber die Entschaedigung, nicht
--    an das Datum, an dem der Versicherer entschieden hat. Deshalb heisst die
--    Spalte negotiations_closed_on und nicht decided_on - und deshalb gibt es
--    ohne sie kein berechenbares Fensterende, sondern die Aussage, dass die
--    Angabe fehlt.

-- ---------- 21. insurance_claims: der Schadenvorgang -------------------------
create table if not exists insurance_claims (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  -- NULLABLE: ein Schaden kann sich gegen eine FREMDE Versicherung richten -
  -- der Auffahrunfall, bei dem die Gegenseite reguliert. Ein solcher Vorgang
  -- hat keine eigene Police und deshalb auch kein Sonderkuendigungsrecht.
  policy_id         uuid,
  insured_object_id uuid,
  title             text not null check (length(btrim(title)) between 1 and 160),
  claim_number      text,
  occurred_on       date not null,
  reported_on       date,
  location          text,
  description       text,
  police_reference  text,
  status            text not null default 'draft' check (status in (
                      'draft','reported','in_review','info_requested','decided',
                      'payment_expected','paid','closed','withdrawn')),
  decision          text check (decision in ('accepted_full','accepted_partial','rejected')),
  decided_on        date,
  reduction_reason  text,
  claimed_minor     bigint not null default 0 check (claimed_minor >= 0),
  approved_minor    bigint check (approved_minor >= 0),
  -- Die am VORGANG tatsaechlich abgezogene Selbstbeteiligung. Nicht zu
  -- verwechseln mit insurance_policies.deductible_minor, das den vereinbarten
  -- Satz traegt: der Versicherer zieht im Einzelfall auch weniger ab.
  deductible_minor  bigint not null default 0 check (deductible_minor >= 0),
  settled_minor     bigint not null default 0 check (settled_minor >= 0),
  paid_to_third_party boolean not null default false,
  next_action       text,
  next_action_due   date,

  -- ------------------------------------------------------------------
  -- SONDERKUENDIGUNGSRECHT NACH DEM VERSICHERUNGSFALL (3.12, Pruefung 3)
  --
  -- decided_on bleibt das fachliche Datum der Versichererentscheidung - fuer
  -- Historie und Auswertung - und VERLIERT die Fristfunktion. Die Regel
  -- "special_termination_until = decided_on + 1 Monat" entfaellt ersatzlos:
  -- bei accepted_partial ist die Entscheidung der ANFANG der Verhandlung, bei
  -- accepted_full enden die Verhandlungen erst mit vollstaendiger Zahlung. In
  -- zwei von drei Faellen haette das Werkzeug ein bestehendes Recht als
  -- verfallen gemeldet.
  --
  -- Welche Norm gilt, entscheidet die SPARTE der betroffenen Police:
  --   vvg_92   Sach/Kasko/Hausrat/Wohngebaeude - Anker "Abschluss der
  --            Verhandlungen ueber die Entschaedigung" (Paragraph 92 Abs. 2 S. 1)
  --   vvg_111  Kfz- und Privathaftpflicht - Anker "Anerkennung oder Ablehnung
  --            des Freistellungsanspruchs" bzw. Rechtskraft
  --   contract abweichende Bedingungen (beide Normen sind dispositiv:
  --            Paragraph 87 VVG nennt 92 nicht, Paragraph 112 VVG nennt 111 nicht)
  --   none     Kranken/PKV - dort greift /v1/reimbursements/cases; und jeder
  --            Vorgang ohne eigene Police, weil es dort nichts zu kuendigen gibt
  -- ------------------------------------------------------------------
  termination_basis text not null default 'vvg_92'
                      check (termination_basis in ('vvg_92','vvg_111','contract','none')),
  negotiations_closed_on     date,
  negotiations_closed_reason text check (negotiations_closed_reason in (
                      'final_rejection','full_payment','settlement_letter',
                      'withdrawal','judgment_final','liability_acknowledged',
                      'liability_refused','user_confirmed')),
  negotiations_closed_source text check (negotiations_closed_source in
                      ('transaction','manual')),
  negotiations_closed_transaction_id uuid,   -- Zahlungseingang
  -- Ein Datum ohne sichtbare Herkunft ist in diesem Feld eine Behauptung,
  -- keine Information. Dieselbe Haltung wie main_due_source in 4.3.
  negotiations_closed_certainty text not null default 'unbekannt'
                      check (negotiations_closed_certainty in
                        ('belegt','abgeleitet','geschaetzt','unbekannt')),
  -- Expliziter Zustand statt NULL-Raterei. Ab 'reported' gilt
  -- 'open_undetermined': das Recht besteht seit dem Schadentag (Paragraph 92
  -- Abs. 1 gibt es ab EINTRITT des Versicherungsfalles, Abs. 2 setzt nur die
  -- Obergrenze), das Ende steht noch nicht fest. Der Uebergang nach 'expired'
  -- erfolgt NUR ueber einen belegten oder bestaetigten Anker - das Werkzeug
  -- erklaert ein Recht niemals von sich aus fuer verfallen.
  special_termination_state text not null default 'none'
                      check (special_termination_state in
                        ('none','open_undetermined','open_until','expired')),
  -- ABGELEITET aus negotiations_closed_on + 1 Monat, sonst null. Wird nie per
  -- Statuswechsel geschrieben; gespeichert nur als Dedup-Anker fuer den Push.
  special_termination_until date,
  -- Wirkung der Kuendigung ist eine WAHL, kein Datum: Paragraph 92 Abs. 2 S. 3
  -- begrenzt den VN auf den Schluss der laufenden Versicherungsperiode,
  -- VHB/VGB erlauben zusaetzlich sofortige Wirkung. Ueber Paragraph 111 Abs. 2
  -- S. 2 gilt die Deckelung auch fuer die Haftpflicht.
  termination_effect_options text[] not null default '{end_of_period}',
  -- MASCHINENGESCHRIEBEN: Dedup-Anker des Pushes insurance_claim_followup, aus
  -- dem Versionstrigger ausgenommen.
  notified_at       timestamptz,
  version           int not null default 1,
  created_by        uuid references auth.users(id) on delete set null default auth.uid(),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (id, household_id),
  -- SPALTENWEISE, siehe 4.3: ohne '(policy_id)' faellt beim Loeschen einer
  -- Police auch household_id auf null und die Zeile ist unrettbar.
  foreign key (policy_id, household_id)
    references insurance_policies(id, household_id) on delete set null (policy_id),
  foreign key (insured_object_id, household_id)
    references insured_objects(id, household_id) on delete set null (insured_object_id),
  -- Der Buchungsverweis des belegten Ankers. Im Planentwurf ohne Fremdschluessel
  -- notiert; ohne ihn zeigte eine geloeschte Buchung als "belegt" ins Leere.
  -- Der Trigger stuft die Sicherheit beim Wegfall der Buchung zurueck - sonst
  -- schluege der CHECK darunter beim SET NULL zu und das Loeschen einer Buchung
  -- waere blockiert.
  foreign key (negotiations_closed_transaction_id, household_id)
    references transactions(id, household_id)
    on delete set null (negotiations_closed_transaction_id),
  check (decision <> 'accepted_partial' or reduction_reason is not null),
  check (reported_on is null or reported_on >= occurred_on),
  check (decided_on is null or decided_on >= occurred_on),
  -- Kein Ankerdatum ohne Grund, und "belegt" nur mit zugeordnetem Kontoumsatz.
  check (negotiations_closed_on is null or negotiations_closed_reason is not null),
  check (negotiations_closed_certainty <> 'belegt'
         or negotiations_closed_transaction_id is not null),
  -- 'open_until' und 'expired' setzen einen gesetzten Anker voraus - ohne ihn
  -- bleibt es bei 'open_undetermined'.
  check (special_termination_state not in ('open_until','expired')
         or negotiations_closed_on is not null)
);

-- Arbeitsvorrat des Nachtlaufs (8.2 Punkt 5): offene Vorgaenge nach Faelligkeit.
create index if not exists insurance_claims_open_idx
  on insurance_claims (household_id, next_action_due)
  where status not in ('paid','closed','withdrawn');
-- Arbeitsvorrat des Nachtlaufs (8.2 Punkt 2b) und des Insights
-- insurance_special_termination: alles, was noch laufen kann.
create index if not exists insurance_claims_termination_idx
  on insurance_claims (household_id, special_termination_state, special_termination_until)
  where special_termination_state in ('open_undetermined','open_until');
create index if not exists insurance_claims_policy_idx
  on insurance_claims (household_id, policy_id, occurred_on desc)
  where policy_id is not null;

-- Derselbe Versionstrigger wie an Police und Beitragsanpassung: er nimmt
-- notified_at aus dem Vergleich aus, damit der Nachtlauf kein If-Match entwertet.
drop trigger if exists insurance_claims_bump_version on insurance_claims;
create trigger insurance_claims_bump_version before update on insurance_claims
  for each row execute function bump_insurance_version();

-- Harte Abgrenzung zum Erstattungsmodul, per Trigger und nicht per Kommentar.
create or replace function validate_insurance_claim() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_health boolean;
begin
  if new.policy_id is not null then
    select is_health_scoped into v_health
      from insurance_policies
     where id = new.policy_id and household_id = new.household_id;
    if coalesce(v_health, false) then
      -- Formatstring und "using message" schliessen einander aus, siehe 4.3.
      -- Der Verweis auf /v1/reimbursements steht in der API-Uebersetzung des
      -- Codes; die Meldung selbst bleibt maschinenlesbar.
      raise exception using errcode = '23514',
        message = 'INSURANCE_CLAIM_HEALTH_SCOPED';
    end if;
  end if;
  -- Ohne Buchung gibt es keinen Beleg. Faellt der Kontoumsatz weg (Import
  -- zurueckgenommen, Buchung geloescht), bleibt das Datum stehen - aber es ist
  -- nur noch geschaetzt. Der stille Verlust waere hier die Alternative: der
  -- CHECK unten liesse das SET NULL des Fremdschluessels sonst scheitern, und
  -- eine Buchung waere nicht mehr loeschbar, weil irgendwo ein Schaden auf sie
  -- zeigt.
  if new.negotiations_closed_transaction_id is null
     and new.negotiations_closed_certainty = 'belegt' then
    new.negotiations_closed_certainty := 'geschaetzt';
  end if;
  return new;
end $$;

drop trigger if exists insurance_claims_validate on insurance_claims;
create trigger insurance_claims_validate
  before insert or update on insurance_claims
  for each row execute function validate_insurance_claim();

alter table insurance_claims enable row level security;
drop policy if exists insurance_claims_read on insurance_claims;
create policy insurance_claims_read on insurance_claims
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_claims_write on insurance_claims;
create policy insurance_claims_write on insurance_claims
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_claims to authenticated;

-- ---------- 22. insurance_claim_payments: Zahlungen zum Schaden -------------
-- n:m, weil eine Sammelueberweisung mehrere Vorgaenge deckt und ein Vorgang
-- Abschlag plus Restzahlung kennt. Direktzahlung an Werkstatt/Arzt hat gar
-- keine Buchung: transaction_id bleibt null, paid_to_third_party ist true.
create table if not exists insurance_claim_payments (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references households(id) on delete cascade
                   default current_household_id(),
  claim_id       uuid not null,
  transaction_id uuid,
  -- 'inbound'  Geld kommt beim Haushalt an ODER der Versicherer zahlt direkt an
  --            die Werkstatt - beides ist Entschaedigung, nur der Weg ist ein
  --            anderer. Die Direktzahlung traegt deshalb keine Buchung.
  -- 'outbound' Selbstbeteiligung, selbst bezahlte Rechnung, Rueckforderung.
  direction      text not null check (direction in ('inbound','outbound')),
  kind           text not null default 'settlement' check (kind in
                   ('settlement','advance','deductible','invoice','recovery')),
  amount_minor   bigint not null check (amount_minor > 0),
  booked_on      date not null,
  note           text,
  created_by     uuid references auth.users(id) on delete set null default auth.uid(),
  created_at     timestamptz not null default now(),
  unique (id, household_id),
  foreign key (claim_id, household_id)
    references insurance_claims(id, household_id) on delete cascade,
  -- SPALTENWEISE, siehe 4.3.
  foreign key (transaction_id, household_id)
    references transactions(id, household_id) on delete set null (transaction_id)
);
create index if not exists insurance_claim_payments_claim_idx
  on insurance_claim_payments (household_id, claim_id, booked_on);
create index if not exists insurance_claim_payments_transaction_idx
  on insurance_claim_payments (household_id, transaction_id)
  where transaction_id is not null;

-- Vorbild ist validate_health_transaction_allocation: dieselben drei Fragen,
-- weil eine Zuordnung dieselben drei Wege hat, falsch zu sein - fremdes Konto,
-- falsche Richtung, mehr zugeordnet als die Buchung hergibt.
create or replace function validate_insurance_claim_payment() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_amount    bigint;
  v_allocated bigint;
begin
  if new.transaction_id is null then
    -- Direktzahlung an einen Dritten. Sie hat keine Buchung und deshalb auch
    -- nichts zu pruefen; ihr Beleg ist die Abrechnung, nicht das Konto.
    return new;
  end if;
  select amount_minor into v_amount
    from transactions
   where id = new.transaction_id and household_id = new.household_id;
  if v_amount is null then
    raise exception using errcode = '23503',
      message = 'INSURANCE_CLAIM_PAYMENT_TRANSACTION_NOT_FOUND';
  end if;
  if new.direction = 'inbound' and v_amount <= 0 then
    raise exception using errcode = '23514',
      message = 'INSURANCE_CLAIM_PAYMENT_MUST_BE_INCOMING';
  end if;
  if new.direction = 'outbound' and v_amount >= 0 then
    raise exception using errcode = '23514',
      message = 'INSURANCE_CLAIM_PAYMENT_MUST_BE_OUTGOING';
  end if;
  select coalesce(sum(amount_minor), 0) into v_allocated
    from insurance_claim_payments
   where household_id = new.household_id
     and transaction_id = new.transaction_id
     and id <> new.id;
  if v_allocated + new.amount_minor > abs(v_amount) then
    raise exception using errcode = '23514',
      message = 'INSURANCE_CLAIM_PAYMENT_OVERALLOCATED';
  end if;
  return new;
end $$;

drop trigger if exists insurance_claim_payments_validate on insurance_claim_payments;
create trigger insurance_claim_payments_validate
  before insert or update on insurance_claim_payments
  for each row execute function validate_insurance_claim_payment();

alter table insurance_claim_payments enable row level security;
drop policy if exists insurance_claim_payments_read on insurance_claim_payments;
create policy insurance_claim_payments_read on insurance_claim_payments
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_claim_payments_write on insurance_claim_payments;
create policy insurance_claim_payments_write on insurance_claim_payments
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_claim_payments to authenticated;

-- ---------- 23. insurance_claim_events: der Zeitstrahl ----------------------
-- Journal, unveraenderlich. Muster contract_merge_changes: eigene
-- INSERT-Policy statt "for all", grant nur select/insert. Ein Zeitstrahl, den
-- man nachtraeglich glaetten kann, ist keiner.
create table if not exists insurance_claim_events (
  id            bigserial primary key,
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  claim_id      uuid not null,
  from_status   text,
  to_status     text not null,
  occurred_on   date not null default current_date,
  actor_user_id uuid references auth.users(id) on delete set null default auth.uid(),
  payload       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  foreign key (claim_id, household_id)
    references insurance_claims(id, household_id) on delete cascade
);
create index if not exists insurance_claim_events_claim_idx
  on insurance_claim_events (household_id, claim_id, occurred_on, id);

alter table insurance_claim_events enable row level security;
drop policy if exists insurance_claim_events_read on insurance_claim_events;
create policy insurance_claim_events_read on insurance_claim_events
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_claim_events_insert on insurance_claim_events;
create policy insurance_claim_events_insert on insurance_claim_events
  for insert to authenticated
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert on insurance_claim_events to authenticated;
grant usage, select on sequence insurance_claim_events_id_seq to authenticated;

-- ---------- 24. Erstattungsbruecke: reimbursement_payers.policy_id ----------
-- Der PKV-Beitragsvertrag und der PKV-Zahler im Erstattungsmodul sind heute
-- zwei Objekte ohne Beziehung. Eine Spalte behebt das; nullable, weil
-- Beihilfestellen keine Police haben. Bestehende Zahler bleiben unveraendert
-- bei null - das Erstattungsmodul funktioniert unveraendert weiter, auch wenn
-- nie eine Police angelegt wird (9.1).
alter table reimbursement_payers add column if not exists policy_id uuid;
alter table reimbursement_payers drop constraint if exists reimbursement_payers_policy_fk;
alter table reimbursement_payers add constraint reimbursement_payers_policy_fk
  foreign key (policy_id, household_id)
  references insurance_policies(id, household_id)
  -- SPALTENWEISE, siehe 4.3: sonst faellt beim Loeschen einer Police auch
  -- reimbursement_payers.household_id auf null - und die Zeile ist unrettbar.
  on delete set null (policy_id);
create index if not exists reimbursement_payers_policy_idx
  on reimbursement_payers (household_id, policy_id) where policy_id is not null;

-- ---------- 25. Katalogkorrektur: Kfz-Haftpflicht ist Paragraph 111 ---------
-- Der Seed der Welle K traegt fuer motor_liability 'vvg_92'. Das ist falsch:
-- Paragraph 92 VVG steht in der Sachversicherung, fuer die Haftpflicht gilt
-- Paragraph 111 VVG mit anderem Anker (3.12 Pruefung 3, dort ausdruecklich fuer
-- motor_liability). Ohne die Korrektur lieferten Kfz-Haftpflicht und Kasko aus
-- demselben Unfall denselben Anker - der Referenzfall "zwei Fristen aus einem
-- Unfall" (13.1) waere nicht erreichbar. Als UPDATE und nicht im Seed, weil
-- Schemaaenderungen ans Ende der Datei gehoeren; der Lauf konvergiert dadurch
-- auch bei wiederholtem Einspielen.
update insurance_lines set default_claim_termination_basis = 'vvg_111', updated_at = now()
 where line_key = 'motor_liability' and default_claim_termination_basis <> 'vvg_111';

-- ---------- 26. Kommentare ---------------------------------------------------
comment on table insurance_claims is
  'Schaden- und Leistungsfaelle fuer Sach, Kfz, Haftpflicht und Rechtsschutz. '
  'Krankheitskosten gehoeren NICHT hierher: sie laufen ueber '
  'health_expense_cases mit Traegersplit, und ein Vorgang auf einer Police mit '
  'is_health_scoped = true wird per Trigger abgewiesen.';

comment on column insurance_claims.negotiations_closed_on is
  'Abschluss der Verhandlungen ueber die Entschaedigung - der Fristanker des '
  'Paragraphen 92 Abs. 2 S. 1 VVG. Ausdruecklich NICHT decided_on: bei einer '
  'Teilregulierung ist die Entscheidung der Anfang der Verhandlung, nicht ihr '
  'Ende. Ohne dieses Datum gibt es kein Fensterende, sondern die Aussage, dass '
  'die Angabe fehlt.';

comment on column insurance_claims.decided_on is
  'Fachliches Datum der Versichererentscheidung. Hat KEINE Fristfunktion mehr '
  '(3.12 Pruefung 3); bei termination_basis = vvg_111 ist es der Vorschlag fuer '
  'negotiations_closed_on, geschrieben wird es dort erst nach Bestaetigung.';

comment on column insurance_claims.special_termination_state is
  'Expliziter Zustand statt NULL-Raterei. Ab reported gilt open_undetermined: '
  'das Recht besteht seit dem Schadentag, sein Ende steht noch nicht fest. Der '
  'Uebergang nach expired erfolgt ausschliesslich ueber einen belegten oder '
  'bestaetigten Anker - das Werkzeug erklaert ein Recht nie von sich aus fuer '
  'verfallen.';

comment on column insurance_claims.termination_basis is
  'Norm des Sonderkuendigungsrechts, vorbelegt aus '
  'insurance_lines.default_claim_termination_basis. Keine Konstante im Code: '
  'beide Normen sind dispositiv, die Bedingungen der Police gehen vor.';

comment on table insurance_claim_payments is
  'Zahlungen zu einem Schadenvorgang, n:m gegen transactions - eine '
  'Sammelueberweisung deckt mehrere Vorgaenge, ein Vorgang kennt Abschlag und '
  'Restzahlung. Zahlt der Versicherer direkt an die Werkstatt, bleibt '
  'transaction_id null: es gibt keine Buchung, aber sehr wohl eine Zahlung.';

comment on table insurance_claim_events is
  'Zeitstrahl eines Schadenvorgangs, unveraenderlich (grant select, insert). '
  'Abgeleitete Fakten, keine Anzeigetexte: die Oberflaeche formuliert daraus, '
  'sie liest keine fertigen Saetze.';

comment on column reimbursement_payers.policy_id is
  'Erstattungsbruecke (9.1): derselbe Versicherer existiert genau einmal - als '
  'Police mit Beitrag und Fristen, und hier als Zahler, der auf sie zeigt. '
  'Nullable, weil Beihilfestellen keine Police haben; bestehende Zahler bleiben '
  'unveraendert bei null.';



-- ============================================================================
-- BEDARFSABGLEICH UND DECKUNGSLUECKEN (Plan 4.9 und 10, Phase P6)
-- ============================================================================
-- Drei Tabellen und eine Spalte. Sie tragen ausschliesslich ANGABEN und
-- ENTSCHEIDUNGEN - kein Befund wird gespeichert. Die Befunde rechnet
-- insurance_needs_service bei jedem Aufruf frisch (Muster
-- rules_service.get_review_inbox: es gibt keine review_items-Tabelle).
-- Persistiert ist nur, was der Nutzer dazu gesagt hat; das ueberlebt jeden
-- Neulauf und jede Regelaenderung, ein gerechneter Befund muss das nicht.
--
-- Die wichtigste Eigenschaft dieses Blocks steht in den Spaltentypen und nicht
-- in den Tabellen: JEDES Angabefeld ist NULLBAR und hat KEINEN Default.
-- "Nicht beantwortet" muss von "nein" unterscheidbar bleiben - sonst erzeugt
-- eine nie gestellte Frage eine Luecke, die es womoeglich gar nicht gibt. Der
-- Entwurf in 4.9 fuehrte has_oil_tank, side_business und volunteering als
-- "not null default false"; das ist genau die Verwechslung, gegen die der Rest
-- des Moduls gebaut ist ("ich weiss es nicht" ist ein Ergebniszustand, kein
-- Fehler), und wird hier bewusst abweichend umgesetzt. Der Auswerter liefert
-- fuer eine unbeantwortete Angabe 'unclear' und nennt das fehlende Feld.

-- ---------- 27. insurance_needs_profile: Angaben des Haushalts --------------
create table if not exists insurance_needs_profile (
  household_id          uuid primary key references households(id) on delete cascade
                          default current_household_id(),
  -- Wohnen
  housing_form          text check (housing_form in
                          ('rent','owner_occupied','owner_rented_out','none')),
  living_area_sqm       int check (living_area_sqm between 1 and 2000),
  elemental_zone        text check (elemental_zone in ('zurs1','zurs2','zurs3','zurs4','unknown')),
  has_oil_tank          boolean,
  construction_active   boolean,
  has_photovoltaic      boolean,
  -- Vermoegen und Verpflichtungen. liquid_reserve_minor und
  -- contents_value_minor stehen nebeneinander, weil R15 genau ihren Vergleich
  -- prueft: Hausrat lohnt, wo der Wiederbeschaffungswert die Ruecklage
  -- uebersteigt. Die Darlehenssumme selbst kommt NICHT hierher - sie steht in
  -- assets_and_debts und wird dort gelesen (Formel open_debt). Das Kennzeichen
  -- has_open_loans ist die ANTWORT des Nutzers, nicht die Summe: ein Haushalt
  -- ohne gepflegtes Vermoegensmodul soll die Frage trotzdem beantworten
  -- koennen.
  liquid_reserve_minor  bigint check (liquid_reserve_minor >= 0),
  contents_value_minor  bigint check (contents_value_minor >= 0),
  has_open_loans        boolean,
  -- Haushalt und Lebensumstaende
  children_count        int check (children_count between 0 and 20),
  -- NULLBAR und ohne Default, anders als der Entwurf: '[]' heisst "keine
  -- Tiere, gefragt und beantwortet", null heisst "nie gefragt". Nur mit dieser
  -- Unterscheidung darf R14 aus einem leeren Feld ein 'not_applicable' machen.
  pets                  jsonb check (pets is null or jsonb_typeof(pets) = 'array'),
  travel_frequency      text check (travel_frequency in ('never','occasional','frequent')),
  vehicle_count         int check (vehicle_count >= 0),
  side_business         boolean,
  volunteering          boolean,
  has_drone             boolean,
  has_hunting_license   boolean,
  has_boat              boolean,
  -- Wiedervorlage "Schutz erneut pruefen" (Nachtlauf 8.2, Schritt 4).
  last_checked_on       date,
  version               int not null default 1,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

drop trigger if exists insurance_needs_profile_bump_version on insurance_needs_profile;
create trigger insurance_needs_profile_bump_version before update on insurance_needs_profile
  for each row execute function bump_insurance_version();

alter table insurance_needs_profile enable row level security;
drop policy if exists insurance_needs_profile_read on insurance_needs_profile;
create policy insurance_needs_profile_read on insurance_needs_profile
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_needs_profile_write on insurance_needs_profile;
create policy insurance_needs_profile_write on insurance_needs_profile
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_needs_profile to authenticated;

-- ---------- 28. insurance_person_profile: Angaben je Person -----------------
-- Der sensibelste Datensatz des Moduls: Versichertenstatus, Beihilfesatz und
-- Nettoeinkommen je Person. Er bekommt deshalb NICHT das Standardmuster des
-- Versicherungsmoduls ("jedes Haushaltsmitglied darf lesen"), sondern die
-- personenbezogene Bedingung des Gesundheitszweigs. Zwei Abschnitte vorher
-- wird mit erheblichem Aufwand der blosse Anzeigename einer versicherten
-- Person geschuetzt - fuer diese Tabelle waere das Standardmuster ein
-- Rueckschritt hinter den bereits erreichten Schutz.
create table if not exists insurance_person_profile (
  insured_person_id  uuid primary key,
  household_id       uuid not null references households(id) on delete cascade
                       default current_household_id(),
  employment_status  text check (employment_status in
                       ('employed','civil_servant','self_employed','apprentice',
                        'student','retired','not_employed','child')),
  net_income_minor   bigint check (net_income_minor >= 0),
  health_coverage    text check (health_coverage in ('gkv','pkv','pkv_beihilfe','other')),
  -- Nur die drei tatsaechlich vorkommenden Bemessungssaetze. Die Gegenprobe
  -- rechnet der Auswerter (Formel beihilfe_complement), sie steht bewusst
  -- nicht als zweites Feld daneben: zwei Zahlen fuer denselben Sachverhalt
  -- laufen frueher oder spaeter auseinander.
  beihilfe_rate_bp   int check (beihilfe_rate_bp in (5000, 7000, 8000)),
  has_dependents     boolean,
  -- Lebensereignisse mitversicherter Personen (R3b): die Mitversicherung in
  -- Privathaftpflicht und Hausrat endet mit dem Ende der Erstausbildung bzw.
  -- mit dem eigenen Hausstand. Ohne diese beiden Felder gibt es keine
  -- Wiedervorlage darauf.
  education_until    date,
  moved_out_on       date,
  version            int not null default 1,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  foreign key (insured_person_id, household_id)
    references health_insured_persons(id, household_id) on delete cascade
);

drop trigger if exists insurance_person_profile_bump_version on insurance_person_profile;
create trigger insurance_person_profile_bump_version before update on insurance_person_profile
  for each row execute function bump_insurance_version();

alter table insurance_person_profile enable row level security;
drop policy if exists insurance_person_profile_read on insurance_person_profile;
create policy insurance_person_profile_read on insurance_person_profile
  for select to authenticated
  using (household_id = current_household_id()
         and is_household_member(household_id)
         and health_can_access_person(insured_person_id));
drop policy if exists insurance_person_profile_write on insurance_person_profile;
create policy insurance_person_profile_write on insurance_person_profile
  for all to authenticated
  using (household_id = current_household_id()
         and is_household_member(household_id)
         and can_edit_household()
         and health_can_manage_person(insured_person_id))
  with check (household_id = current_household_id()
              and is_household_member(household_id)
              and can_edit_household()
              and health_can_manage_person(insured_person_id));
grant select, insert, update, delete on insurance_person_profile to authenticated;

-- Todesfall: eine Spalte im Gesundheitszweig, KEINE zweite Personenliste.
-- Ohne sie verlangt der Bedarfsabgleich fuer eine verstorbene Person weiter
-- Berufsunfaehigkeit und Risikoleben (Ausschlussregel R7b).
alter table health_insured_persons add column if not exists died_on date;

-- ---------- 29. insurance_rule_decisions: was der Nutzer entschieden hat ----
-- Befunde werden bei jedem Aufruf frisch gerechnet; persistiert wird
-- ausschliesslich die Nutzerentscheidung. Sie schlaegt das Regelergebnis -
-- immer. Ohne diesen Vorrang waere ein weggedrueckter Befund beim naechsten
-- Aufruf wieder da, und der Abgleich haette genau die Eigenschaft, wegen der
-- Stiftung Warentest die geprueften Makler-Apps abgewertet hat.
create table if not exists insurance_rule_decisions (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  rule_key      text not null check (rule_key ~ '^[A-Z][0-9]{1,3}[a-z]?$'),
  -- NULLABLE: der Normalfall ist die regelweite Entscheidung ohne
  -- Policenbezug - die meisten Regeln zeigen auf eine FEHLENDE Police.
  policy_id     uuid,
  decision      text not null check (decision in ('dismissed','accepted','snoozed')),
  reason        text check (reason in
                  ('self_insured','covered_elsewhere','not_applicable','planned','other')),
  reason_note   text check (length(reason_note) <= 2000),
  remind_on     date,
  decided_by    uuid references auth.users(id) on delete set null default auth.uid(),
  decided_at    timestamptz not null default now(),
  unique (id, household_id),
  -- 'snoozed' ohne Datum waere ein Wegdruecken auf Dauer unter falschem Namen.
  constraint insurance_rule_decisions_snooze_pair
    check (decision <> 'snoozed' or remind_on is not null),
  foreign key (policy_id, household_id)
    references insurance_policies(id, household_id) on delete cascade
);
-- Eindeutigkeit ueber zwei partielle Indizes statt ueber einen
-- Primaerschluessel mit Sentinel: ein Fremdschluessel in der Tabellendefinition
-- ist nicht partiell, und jede regelweite Entscheidung waere mit der
-- Null-UUID als Sentinel mit 23503 abgewiesen worden - also ausgerechnet der
-- Normalfall. Der einspaltige Surrogatschluessel id kommt dazu, weil die
-- Katalogabfrage des Datenexplorers Primaerschluessel nur einspaltig aufnimmt.
create unique index if not exists insurance_rule_decisions_policy_uniq
  on insurance_rule_decisions (household_id, rule_key, policy_id)
  where policy_id is not null;
create unique index if not exists insurance_rule_decisions_global_uniq
  on insurance_rule_decisions (household_id, rule_key)
  where policy_id is null;
-- Wiedervorlage: der Nachtlauf sucht die faelligen Erinnerungen.
create index if not exists insurance_rule_decisions_remind_idx
  on insurance_rule_decisions (household_id, remind_on)
  where remind_on is not null;

alter table insurance_rule_decisions enable row level security;
drop policy if exists insurance_rule_decisions_read on insurance_rule_decisions;
create policy insurance_rule_decisions_read on insurance_rule_decisions
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists insurance_rule_decisions_write on insurance_rule_decisions;
create policy insurance_rule_decisions_write on insurance_rule_decisions
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and can_edit_household());
grant select, insert, update, delete on insurance_rule_decisions to authenticated;

-- ---------- 30. Kommentare zum Bedarfsabgleich ------------------------------
comment on table insurance_needs_profile is
  'Haushaltsangaben des Bedarfsabgleichs. Jedes Feld ist nullbar und ohne '
  'Default: "nicht beantwortet" muss von "nein" unterscheidbar bleiben, sonst '
  'erzeugt eine nie gestellte Frage eine Luecke, die es vielleicht nicht gibt. '
  'Der Auswerter meldet dafuer unclear und nennt das fehlende Feld.';

comment on column insurance_needs_profile.has_open_loans is
  'Antwort des Nutzers auf die Frage nach offenen Darlehen - NICHT die Summe. '
  'Die Summe liest die Formel open_debt aus assets_and_debts; ein Haushalt '
  'ohne gepflegtes Vermoegensmodul soll die Frage trotzdem beantworten koennen.';

comment on column insurance_needs_profile.pets is
  'Liste von Tierarten. null heisst "nie gefragt", [] heisst "gefragt, keine '
  'Tiere" - nur mit dieser Unterscheidung darf die Tierhalterhaftpflicht-Regel '
  'aus einem leeren Feld ein not_applicable machen.';

comment on table insurance_person_profile is
  'Bedarfsangaben je Person: Erwerbsstatus, Nettoeinkommen, Versichertenstatus '
  'und Beihilfesatz. Traegt den Gesundheitsschutz des Erstattungsmoduls '
  '(health_can_access_person beim Lesen, health_can_manage_person beim '
  'Schreiben) und NICHT das Standardmuster des Versicherungsmoduls - ein '
  'zweiter Editor ohne Gesundheitsfreigabe liest hier die leere Menge.';

comment on table insurance_rule_decisions is
  'Was der Nutzer zu einem Befund entschieden hat. Der einzige persistierte '
  'Teil des Bedarfsabgleichs - die Befunde selbst werden bei jedem Aufruf '
  'frisch gerechnet. Eine Entscheidung schlaegt das Regelergebnis immer; '
  'snoozed bringt den Befund am remind_on zurueck.';

comment on column insurance_rule_decisions.policy_id is
  'Nullable, und das ist der Normalfall: die meisten Regeln zeigen auf eine '
  'FEHLENDE Police, eine Entscheidung dazu kann also auf keine zeigen. Die '
  'Eindeutigkeit stellen zwei partielle Indizes her, nicht ein Sentinel.';

comment on column health_insured_persons.died_on is
  'Todesfall. Eine Spalte statt einer zweiten Personenliste; ohne sie verlangt '
  'der Bedarfsabgleich fuer eine verstorbene Person weiter Berufsunfaehigkeit '
  'und Risikoleben (Ausschlussregel R7b).';


-- ============================================================================
-- Umrechnung von Haushaltseinheiten je Zutat
-- ============================================================================
-- Ein Rezept sagt "4 Zehen Knoblauch", die Preisliste sagt "100 g". Der
-- Rechenkern kennt von sich aus nur g<->kg und ml<->l; alles andere bildet
-- eine eigene Familie und passt nur auf sich selbst. Rund siebzig Zutaten
-- waren damit nicht bewertbar, obwohl Preis UND Menge dastanden.
--
-- Diese Tabelle ist die fehlende Zahl dazwischen, immer im selben Satz:
--
--     1 <unit> dieser Zutat = <factor> <base_unit>
--
-- Eine Dichte faellt unter dieselbe Regel ("1 ml Sesamoel = 0,93 g") - deshalb
-- reicht EIN Begriff statt dreien. Damit keine Umrechnungskette entsteht
-- (1 EL -> 15 ml -> 0,93 g), hat jede Zutat genau EINE Basiseinheit: alle
-- Zeilen derselben Zutat zeigen auf g oder alle auf ml. Der Dienst weist eine
-- abweichende zweite Basis mit 409 ab; ein reiner Spaltencheck kann das nicht.
--
-- Der Schluessel ist der NAME, nicht ein Fremdschluessel - dieselbe
-- Entscheidung wie bei ingredient_price_aliases und aus demselben Grund: eine
-- Umrechnung ueberlebt das Loeschen und Neuanlegen einer Preiszeile und wirkt
-- sofort in jedem Gericht, das die Zutat verwendet.
--
-- Was hier NICHT steht, wird auch nicht geraten: der Rechenkern benutzt
-- ausschliesslich gespeicherte Zeilen. Die mitgelieferte Werteliste
-- (services/api/app/services/grocery_unit_defaults.py) fuellt nur den
-- Vorschlagsknopf. Sonst verschoebe eine spaetere Korrektur dort rueckwirkend
-- die Kosten jedes Haushalts, ohne dass jemand etwas getan haette.
create table if not exists ingredient_unit_conversions (
  id              uuid primary key default gen_random_uuid(),
  household_id    uuid not null references households(id) on delete cascade default current_household_id(),
  -- lower(btrim(name)) der Zutat - die Form, die load_price_lookup als
  -- Schluessel benutzt.
  ingredient_key  text not null,
  ingredient_name text not null,
  -- Einheit, normalisiert ueber grocery_pricing_service.normalize_unit.
  -- Ohne die Normalisierung stuenden "EL", "el" und "El" als drei Zeilen
  -- nebeneinander, die kein Unique-Index zusammenfasst.
  unit            text not null,
  factor          numeric(12,4) not null check (factor > 0),
  base_unit       text not null check (base_unit in ('g', 'ml')),
  -- 'default' = unveraendert aus der mitgelieferten Liste uebernommen,
  -- 'manual' = selbst eingetippt. Traegt das "ungefaehr"-Zeichen in der
  -- Oberflaeche und macht spaeter unterscheidbar, was nur durchgewinkt wurde.
  origin          text not null default 'manual' check (origin in ('default', 'manual')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
-- Eine Einheit kann je Zutat nur einen Faktor haben.
create unique index if not exists ingredient_unit_conversions_household_key_idx
  on ingredient_unit_conversions (household_id, ingredient_key, unit);
create index if not exists ingredient_unit_conversions_household_idx
  on ingredient_unit_conversions (household_id, ingredient_key);

alter table ingredient_unit_conversions enable row level security;
drop policy if exists ingredient_unit_conversions_read on ingredient_unit_conversions;
create policy ingredient_unit_conversions_read on ingredient_unit_conversions
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ingredient_unit_conversions_write on ingredient_unit_conversions;
create policy ingredient_unit_conversions_write on ingredient_unit_conversions
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());

grant select, insert, update, delete on ingredient_unit_conversions to authenticated;

comment on table ingredient_unit_conversions is
  'Umrechnung einer Haushaltseinheit in g oder ml, je Zutat: "1 Zehe = 5 g". '
  'Bruecke zwischen Rezepteinheit und Packungseinheit der Preisliste. Je '
  'Zutat genau EINE Basiseinheit, damit keine Umrechnungskette entsteht; eine '
  'Dichte ist schlicht die ml-Zeile einer Zutat mit Basis g.';

-- Nachtraeglicher Dublettencheck: nur ausdrueckliche Fehlalarm-Entscheidungen
-- speichern. Die Treffer selbst werden stets aus dem aktiven Bestand ermittelt.
create table if not exists transaction_duplicate_dismissals (
  household_id uuid not null references households(id) on delete cascade,
  first_transaction_id uuid not null references transactions(id) on delete cascade,
  second_transaction_id uuid not null references transactions(id) on delete cascade,
  first_version int not null check (first_version > 0),
  second_version int not null check (second_version > 0),
  dismissed_at timestamptz not null default now(),
  primary key (household_id, first_transaction_id, second_transaction_id),
  check (first_transaction_id < second_transaction_id)
);
alter table transaction_duplicate_dismissals enable row level security;
drop policy if exists transaction_duplicate_dismissals_read on transaction_duplicate_dismissals;
create policy transaction_duplicate_dismissals_read on transaction_duplicate_dismissals
  for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists transaction_duplicate_dismissals_write on transaction_duplicate_dismissals;
create policy transaction_duplicate_dismissals_write on transaction_duplicate_dismissals
  for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id) and can_edit_household())
  with check (household_id = current_household_id() and is_household_member(household_id) and can_edit_household());
grant select, insert, update, delete on transaction_duplicate_dismissals to authenticated;


-- ============================================================================
-- KI-Feld: Chat zu den eigenen Finanzen (Etappe 2)
-- ============================================================================
-- Vertrag: docs/KI-FELD-SCHNITTSTELLE.md, Entscheidungen E4-E6 in
-- docs/KI-BUDGET-UND-KI-FELD-PLAN.md.
--
--   assistant_chats         ein Chat je Verfasser (privat, E6)
--   assistant_messages      Fragen und Antworten; eine Antwort entsteht in
--                           einem Lauf. kind = 'compaction' traegt den
--                           Abschluss eines Verdichtungslaufs und erscheint
--                           nicht im Verlauf.
--   assistant_runs          ein Lauf = eine Aufgabe der KI-Warteschlange
--                           (ai_tasks.task_type = 'assistant_chat'), Modus
--                           answer oder compact; traegt auch die
--                           Zusammenfassung einer Verdichtung
--   assistant_run_calls     jeder Modellaufruf eines Laufs mit Budgetzeile
--   assistant_tool_results  jedes Werkzeugergebnis im Wortlaut (W1, W2, ...
--                           eindeutig je Chat) samt allen Zeilen fuer das
--                           Weiterblaettern
--   assistant_run_events    gespeicherte Zeilen des NDJSON-Stroms mit streng
--                           steigender seq je Lauf (Nachspielen ab ?after=N)
--
-- Denktext wird nie gespeichert, nur seine Laenge (reasoning_chars). Die KI
-- liest nur (E5); die Werkzeuge laufen in READ-ONLY-Transaktionen mit den
-- RLS-Rechten des Verfassers.
--
-- Sichtbarkeit: jede Zeile gehoert ihrem Verfasser (created_by). Betrachter
-- duerfen fragen (E6) - die Policies verlangen deshalb Mitgliedschaft, aber
-- kein can_edit_household(). Der Worker schreibt ohne RLS und filtert selbst.
--
-- Wie jede Haushaltstabelle: nullbare Nutzerverweise (die Wiederherstellung
-- einer Sicherung in eine frische Instanz leert Verweise auf Nutzer, die es
-- dort nicht gibt - ein Chat ohne Verfasser ist fuer niemanden sichtbar und
-- faellt mit der Aufbewahrung weg; geloeschte Nutzer nehmen ihre Chats mit),
-- uuid-Schluessel (Sicherungen spielen Zeilen mit ihrer ID zurueck) und kein
-- Fremdschluessel-Zyklus (siehe unten).
--
-- Haushaltstabellen duerfen keinen Fremdschluessel-Zyklus bilden (Explorer,
-- Sicherung, Leeren brauchen eine Loeschreihenfolge) - die Rueckverweise auf
-- Laeufe sind deshalb einfache Spalten, siehe unten.

-- Aufbewahrung der Chats in Tagen seit der letzten Aenderung (E6). Die
-- Vorgabe steht nur hier; aufgeraeumt wird im Wiederherstellungsjob der
-- KI-Warteschlange (worker/ai/task_processor.py, recover_stale_tasks).
alter table ai_household_settings
  add column if not exists assistant_retention_days int not null default 90;
alter table ai_household_settings
  drop constraint if exists ai_household_settings_assistant_retention_days_check;
alter table ai_household_settings
  add constraint ai_household_settings_assistant_retention_days_check
  check (assistant_retention_days between 1 and 3650);
comment on column ai_household_settings.assistant_retention_days is
  'Aufbewahrung der Chats des KI-Felds in Tagen seit der letzten Aenderung; '
  'aeltere Chats loescht der Wiederherstellungsjob der KI-Warteschlange.';

create table if not exists assistant_chats (
  id                       uuid primary key default gen_random_uuid(),
  household_id             uuid not null references households(id) on delete cascade
                             default current_household_id(),
  created_by               uuid references auth.users(id) on delete cascade
                             default auth.uid(),
  title                    text not null default 'Neuer Chat'
                             check (char_length(title) between 1 and 200),
  -- null = Haushaltseinstellung (Vertrag: ReasoningOverride)
  reasoning_override       text check (reasoning_override in ('on', 'off')),
  -- Zaehler fuer assistant_messages.seq und die Werkzeugkennung W<n>; per
  -- "update ... returning" vergeben, damit zwei Schreiber nie dieselbe Zahl
  -- bekommen.
  last_message_seq         int not null default 0,
  last_tool_key            int not null default 0,
  -- gueltige Zusammenfassung (Lauf, der sie erzeugt hat) und laufende
  -- Verdichtung - ohne Fremdschluessel, siehe unten.
  summary_run_id           uuid,
  active_compaction_run_id uuid,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  last_message_at          timestamptz
);
create index if not exists assistant_chats_owner_idx
  on assistant_chats (household_id, created_by, updated_at desc);
create index if not exists assistant_chats_retention_idx
  on assistant_chats (household_id, updated_at);

create table if not exists assistant_runs (
  id                              uuid primary key default gen_random_uuid(),
  household_id                    uuid not null references households(id) on delete cascade
                                    default current_household_id(),
  created_by                      uuid references auth.users(id) on delete cascade
                                    default auth.uid(),
  chat_id                         uuid not null references assistant_chats(id) on delete cascade,
  -- Antwortnachricht dieses Laufs (Constraint unten) und die Frage dazu
  message_id                      uuid not null,
  question_message_id             uuid,
  ai_task_id                      uuid references ai_tasks(id) on delete set null,
  mode                            text not null default 'answer'
                                    check (mode in ('answer', 'compact')),
  status                          text not null default 'queued'
                                    check (status in ('queued', 'running', 'done', 'failed', 'cancelled')),
  end_reason                      text
                                    check (end_reason in ('answer', 'stall', 'runtime', 'cancelled', 'failed')),
  -- wirksame Uebersteuerung dieses Laufs (Senden ohne Angabe = die des Chats)
  reasoning_override              text check (reasoning_override in ('on', 'off')),
  focus                           text,
  retry_of_run_id                 uuid references assistant_runs(id) on delete set null,
  model_configured                text,
  model_answering                 text,
  window_loaded                   int,
  window_cap                      int,
  window_effective                int,
  window_source                   text,
  reasoning_sent                  text,
  -- was das Modell beim Lauf ueber sein Denken meldete (ChatDetail zeigt den
  -- letzten bekannten Stand, ohne bei jedem Abruf LM Studio zu fragen)
  reasoning_capability            text
                                    check (reasoning_capability in ('switchable', 'always', 'none', 'unknown')),
  error_code                      text,
  error_message                   text,
  -- hoechste vergebene seq in assistant_run_events
  last_seq                        int not null default 0,
  -- Zusammenfassung (Verdichtung): Text in 7 Abschnitten, Nummer V<n>,
  -- letzte abgedeckte Nachricht, Token vorher/nachher, Zahlen ohne Beleg
  summary_nr                      int,
  summary_text                    text,
  summary_covers_until_message_id uuid,
  summary_tokens_before           int,
  summary_tokens_after            int,
  summary_unverified              int not null default 0,
  created_at                      timestamptz not null default now(),
  started_at                      timestamptz,
  finished_at                     timestamptz,
  updated_at                      timestamptz not null default now()
);
create index if not exists assistant_runs_chat_idx
  on assistant_runs (household_id, chat_id, created_at);
create index if not exists assistant_runs_task_idx
  on assistant_runs (ai_task_id) where ai_task_id is not null;
create index if not exists assistant_runs_active_idx
  on assistant_runs (household_id, status) where status in ('queued', 'running');

create table if not exists assistant_messages (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references households(id) on delete cascade
                        default current_household_id(),
  created_by          uuid references auth.users(id) on delete cascade
                        default auth.uid(),
  chat_id             uuid not null references assistant_chats(id) on delete cascade,
  seq                 int not null,
  role                text not null check (role in ('user', 'assistant')),
  kind                text not null default 'chat' check (kind in ('chat', 'compaction')),
  -- Antworttext ohne <think>-Bloecke
  content             text not null default '',
  status              text not null default 'done'
                        check (status in ('queued', 'running', 'done', 'failed', 'cancelled')),
  -- Lauf, der diese Antwort schreibt (ohne Fremdschluessel, siehe unten)
  run_id              uuid,
  -- Antwort -> beantwortete Frage (Wiederholen legt eine neue Antwort zur
  -- selben Frage an)
  reply_to_message_id uuid references assistant_messages(id) on delete cascade,
  request_id          text,
  error_code          text,
  error_message       text,
  numbers             jsonb,
  context_meter       jsonb,
  run_summary         jsonb,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint assistant_messages_chat_seq_unique unique (chat_id, seq)
);
-- Idempotenz des Sendens: gleiche request_id im selben Chat = dieselbe Frage.
create unique index if not exists assistant_messages_request_uniq
  on assistant_messages (chat_id, request_id) where request_id is not null;
create index if not exists assistant_messages_run_idx
  on assistant_messages (run_id) where run_id is not null;

create table if not exists assistant_run_calls (
  id                uuid primary key default gen_random_uuid(),
  household_id      uuid not null references households(id) on delete cascade
                      default current_household_id(),
  created_by        uuid references auth.users(id) on delete cascade
                      default auth.uid(),
  run_id            uuid not null references assistant_runs(id) on delete cascade,
  nr                int not null,
  step              text not null,
  retry_of          int,
  retry_reason      text,
  started_at        timestamptz not null default now(),
  duration_ms       int,
  ttft_ms           int,
  finish_reason     text,
  model             text,
  budget            jsonb,
  tools_offered     jsonb not null default '[]'::jsonb,
  prompt_tokens     int,
  completion_tokens int,
  reasoning_tokens  int,
  -- Laenge des Denktexts; der Text selbst wird nie gespeichert (E6)
  reasoning_chars   int not null default 0,
  tokens_per_s      numeric(10,2),
  answer_text       text,
  tool_call_names   jsonb not null default '[]'::jsonb,
  -- Assistentennachricht dieser Runde, wie sie in den Verlauf ging (Inhalt
  -- ohne Denken + tool_calls) - daraus entsteht der Verlauf der Folgefrage.
  assistant_message jsonb,
  error_code        text,
  error             text,
  llm_call_id       uuid references llm_calls(id) on delete set null,
  created_at        timestamptz not null default now(),
  constraint assistant_run_calls_run_nr_unique unique (run_id, nr)
);

create table if not exists assistant_tool_results (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  created_by    uuid references auth.users(id) on delete cascade
                  default auth.uid(),
  chat_id       uuid not null references assistant_chats(id) on delete cascade,
  run_id        uuid not null references assistant_runs(id) on delete cascade,
  key_nr        int not null,
  key           text not null,
  call_nr       int,
  tool_call_id  text,
  tool_name     text not null,
  args          jsonb not null default '{}'::jsonb,
  status        text not null default 'ok'
                  check (status in ('ok', 'rejected', 'error', 'cancelled')),
  -- genau so, wie das Modell es bekam (erste Seite samt Kopfzeile)
  result_text   text not null default '',
  -- alle Zeilen (Spalten einmal, Zeilen als Listen) fuer result_page
  result_columns jsonb,
  result_rows   jsonb,
  row_count     int,
  duration_ms   int,
  created_at    timestamptz not null default now(),
  constraint assistant_tool_results_chat_key_unique unique (chat_id, key_nr)
);
create index if not exists assistant_tool_results_run_idx
  on assistant_tool_results (run_id, key_nr);

create table if not exists assistant_run_events (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  created_by    uuid references auth.users(id) on delete cascade
                  default auth.uid(),
  run_id        uuid not null references assistant_runs(id) on delete cascade,
  seq           int not null check (seq > 0),
  type          text not null,
  -- die Stromzeile, wie sie der Client bekommt (type, seq, ...)
  payload       jsonb not null,
  created_at    timestamptz not null default now(),
  constraint assistant_run_events_run_seq_unique unique (run_id, seq)
);

-- Verweise vom Lauf auf seine Nachrichten erst jetzt (die Nachrichten-
-- tabelle entsteht nach den Laeufen), wiederholbar.
--
-- Bewusst OHNE Fremdschluessel bleiben die Rueckverweise
-- assistant_messages.run_id, assistant_chats.summary_run_id und
-- assistant_chats.active_compaction_run_id: mit ihnen entstuenden Zyklen
-- (Chat <-> Lauf, Nachricht <-> Lauf), und fuer Haushaltstabellen mit Zyklus
-- gibt es keine Loeschreihenfolge - Datenbank-Explorer, Sicherung und das
-- Leeren eines Haushalts (data_explorer_service.deletion_order) brechen dann
-- ab. Alle drei Spalten zeigen in denselben Chat und verschwinden mit ihm
-- (Kaskade ueber chat_id); die Anwendung setzt sie nur auf Laeufe dieses
-- Chats.
alter table assistant_runs drop constraint if exists assistant_runs_message_fk;
alter table assistant_runs add constraint assistant_runs_message_fk
  foreign key (message_id) references assistant_messages(id) on delete cascade;
alter table assistant_runs drop constraint if exists assistant_runs_question_fk;
alter table assistant_runs add constraint assistant_runs_question_fk
  foreign key (question_message_id) references assistant_messages(id) on delete set null;

-- Row Level Security: Haushalt UND Verfasser. Kein can_edit_household() -
-- auch Betrachter fragen (E6), aber nie in fremden Chats.
alter table assistant_chats enable row level security;
alter table assistant_messages enable row level security;
alter table assistant_runs enable row level security;
alter table assistant_run_calls enable row level security;
alter table assistant_tool_results enable row level security;
alter table assistant_run_events enable row level security;

drop policy if exists assistant_chats_owner on assistant_chats;
create policy assistant_chats_owner on assistant_chats for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and created_by = auth.uid());
drop policy if exists assistant_messages_owner on assistant_messages;
create policy assistant_messages_owner on assistant_messages for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and created_by = auth.uid());
drop policy if exists assistant_runs_owner on assistant_runs;
create policy assistant_runs_owner on assistant_runs for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and created_by = auth.uid());
drop policy if exists assistant_run_calls_owner on assistant_run_calls;
create policy assistant_run_calls_owner on assistant_run_calls for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and created_by = auth.uid());
drop policy if exists assistant_tool_results_owner on assistant_tool_results;
create policy assistant_tool_results_owner on assistant_tool_results for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and created_by = auth.uid());
drop policy if exists assistant_run_events_owner on assistant_run_events;
create policy assistant_run_events_owner on assistant_run_events for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and created_by = auth.uid());

grant select, insert, update, delete on
  assistant_chats, assistant_messages, assistant_runs, assistant_run_calls,
  assistant_tool_results, assistant_run_events
to authenticated;

-- KI-Warteschlange: eine Frage im KI-Feld ist eine Aufgabe wie jede andere
-- (seriell je Haushalt, E4). Steht bewusst NACH dem Block, der
-- ai_tasks_task_type_check mit health_document_extraction setzt.
alter table ai_tasks drop constraint if exists ai_tasks_task_type_check;
alter table ai_tasks add constraint ai_tasks_task_type_check check (task_type in (
  'category_suggestions',
  'transaction_classification',
  'contract_duplicate_crosscheck',
  'contract_transaction_check',
  'project_cost_estimate',
  'health_document_extraction',
  'assistant_chat'
));
-- "Eine offene Aufgabe je Typ" faengt Doppelklicks ab - Fragen im KI-Feld
-- sollen sich dagegen einreihen lassen (Knopf "Einreihen"). Ihre Idempotenz
-- kommt aus request_id (ai_tasks_request_id_uniq, assistant_messages).
drop index if exists ai_tasks_one_open_type_uniq;
create unique index ai_tasks_one_open_type_uniq
  on ai_tasks (household_id, task_type)
  where status in ('queued', 'running') and task_type <> 'assistant_chat';
-- Betrachter duerfen fragen (E6): eigene Aufgaben dieses Typs anlegen,
-- abbrechen und mit Ereignissen versehen. Alle anderen Typen bleiben bei
-- ai_tasks_write (can_edit_household()).
drop policy if exists ai_tasks_assistant_own on ai_tasks;
create policy ai_tasks_assistant_own on ai_tasks for all to authenticated
  using (household_id = current_household_id() and is_household_member(household_id)
         and task_type = 'assistant_chat' and created_by = auth.uid())
  with check (household_id = current_household_id() and is_household_member(household_id)
              and task_type = 'assistant_chat' and created_by = auth.uid());
-- Sicherungen spielen Ereigniszeilen mit ihrer ID zurueck (backup_service:
-- insert ... select * from jsonb_populate_recordset). Mit GENERATED ALWAYS
-- scheiterte das Einspielen jedes Haushalts, der je eine KI-Aufgabe hatte -
-- mit dem KI-Feld (eine Aufgabe je Frage) also praktisch jedes.
alter table ai_task_events alter column id set generated by default;
drop policy if exists ai_task_events_assistant_insert on ai_task_events;
create policy ai_task_events_assistant_insert on ai_task_events for insert to authenticated
  with check (household_id = current_household_id() and is_household_member(household_id)
              and exists (select 1 from ai_tasks t
                          where t.id = ai_task_events.task_id
                            and t.household_id = ai_task_events.household_id
                            and t.task_type = 'assistant_chat'
                            and t.created_by = auth.uid()));

-- ---------------------------------------------------------------------------
-- KI-Protokoll fuer alle Mitglieder (llm_calls, ai_model_measurements)
-- ---------------------------------------------------------------------------
-- Jeder Modellaufruf schreibt eine llm_calls-Zeile und schreibt die Messung
-- des Modells fort (app/llm/calls_log.py, app/llm/measurements.py) - auch
-- Aufrufe, die Betrachter ausloesen duerfen (Hinweise mit KI-Formulierung,
-- KI-Feld). llm_calls_write und ai_model_measurements_write erlauben das nur
-- owner/editor (can_edit_household()); ein Betrachter scheiterte deshalb am
-- ersten Protokolleintrag (42501 -> 403 fuer den ganzen Bericht), und der
-- Ausweg war eine eigene Sitzung an RLS vorbei.
--
-- Stattdessen hier: Einfuegen und das Nachtragen des fachlichen Ergebnisses
-- fuer jedes aktive Mitglied, ausschliesslich im eigenen Haushalt - RLS prueft
-- wie ueberall. Was ein Mitglied aendern darf, begrenzen Spaltenrechte: in
-- llm_calls nur Status, Grund und Meldung (update_call_outcome), in
-- ai_model_measurements nur die Messspalten (der Upsert der Messung).
-- Loeschen bleibt bei owner/editor (llm_calls_write,
-- ai_model_measurements_write).
--
-- Bewusst Richtlinien statt einer SECURITY-DEFINER-Funktion: der Kern
-- schreibt in API und Worker mit denselben Anweisungen (der Worker ohne
-- Nutzerkontext, eine Funktion mit Mitgliedschaftspruefung braeuchte dort
-- einen zweiten Weg), und RLS bleibt die einzige Schutzschicht - es gibt
-- keinen Weg an ihr vorbei.
drop policy if exists llm_calls_member_insert on llm_calls;
create policy llm_calls_member_insert on llm_calls for insert to authenticated
  with check (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists llm_calls_member_outcome on llm_calls;
create policy llm_calls_member_outcome on llm_calls for update to authenticated
  using (household_id = current_household_id() and is_household_member(household_id))
  with check (household_id = current_household_id() and is_household_member(household_id));
-- Tabellenrecht UPDATE weg (es schloesse jede Spalte ein), dann genau die
-- Spalten, die update_call_outcome setzt. Ein REVOKE auf der Tabelle nimmt
-- auch fruehere Spaltenrechte mit - die Reihenfolge ist wiederholbar.
revoke update on llm_calls from authenticated;
grant update (status, reason_code, error_message) on llm_calls to authenticated;

drop policy if exists ai_model_measurements_member_insert on ai_model_measurements;
create policy ai_model_measurements_member_insert on ai_model_measurements
  for insert to authenticated
  with check (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ai_model_measurements_member_update on ai_model_measurements;
create policy ai_model_measurements_member_update on ai_model_measurements
  for update to authenticated
  using (household_id = current_household_id() and is_household_member(household_id))
  with check (household_id = current_household_id() and is_household_member(household_id));
revoke update on ai_model_measurements from authenticated;
grant update (sum_chars, sum_prompt_tokens, sum_estimate_error, sum_checked_tokens, calls,
              largest_need_tokens, measured_at)
  on ai_model_measurements to authenticated;

-- ---------------------------------------------------------------------------
-- KI-Anbieter und API-Schluessel (Etappe 3, docs/KI-BUDGET-UND-KI-FELD-PLAN.md)
-- ---------------------------------------------------------------------------
-- Der Haushalt waehlt, wohin seine KI-Aufrufe gehen: 'local' (LM Studio,
-- Ollama oder ein eigener OpenAI-kompatibler Server unter custom_base_url -
-- das bisherige Verhalten) oder 'anthropic' (Claude ueber die Messages-API).
-- Fest gewaehlt, kein stiller Wechsel: faellt der gewaehlte Anbieter aus,
-- scheitert der Aufruf mit benanntem Grund (app/llm/reasons.py).
alter table ai_household_settings
  add column if not exists provider text not null default 'local';
alter table ai_household_settings
  drop constraint if exists ai_household_settings_provider_check;
alter table ai_household_settings
  add constraint ai_household_settings_provider_check
  check (provider in ('local', 'anthropic'));
comment on column ai_household_settings.provider is
  'KI-Anbieter des Haushalts: local = Server unter custom_base_url (LM Studio, '
  'Ollama, OpenAI-kompatibel); anthropic = Claude. Bei anthropic verlassen die '
  'Daten eines KI-Aufrufs den Haushalt.';

-- API-Schluessel der Anbieter, je Haushalt und Ziel (scheme://host:port). Ein
-- Schluessel geht nur an genau dieses Ziel (app/llm/keys.py).
--
-- Gespeichert ist nur der mit AES-GCM verschluesselte Wert; der Schluessel zum
-- Entschluesseln wird auf dem Server aus SUPABASE_JWT_SECRET abgeleitet (HKDF)
-- und liegt nie in der Datenbank. Mitglieder duerfen die Zeile deshalb lesen
-- (Anzeige "hinterlegt ...abcd" auch fuer Betrachter) - der Geheimtext nuetzt
-- ohne das Servergeheimnis nichts, und RLS bleibt die einzige Schutzschicht,
-- wie bei llm_calls. Schreiben darf nur der Owner: ein Schluessel kostet Geld,
-- und mit ihm verlassen Haushaltsdaten das Haus.
--
-- Der Datenbank-Explorer blendet die Tabelle aus (und damit auch Sicherung und
-- Leeren, die seinen Katalog benutzen): nach einer Wiederherstellung wird der
-- Schluessel neu hinterlegt.
create table if not exists ai_provider_keys (
  household_id  uuid not null references households(id) on delete cascade
                  default current_household_id(),
  target        text not null check (char_length(target) between 1 and 300),
  ciphertext    text not null check (char_length(ciphertext) between 1 and 4000),
  key_ending    text check (key_ending is null or char_length(key_ending) <= 8),
  created_at    timestamptz not null default now(),
  created_by    uuid references auth.users(id) on delete set null default auth.uid(),
  primary key (household_id, target)
);
comment on table ai_provider_keys is
  'API-Schluessel der KI-Anbieter je Haushalt und Ziel (scheme://host:port), '
  'AES-GCM-verschluesselt; der Entschluesselungsschluessel liegt nur auf dem Server.';

alter table ai_provider_keys enable row level security;
drop policy if exists ai_provider_keys_read on ai_provider_keys;
create policy ai_provider_keys_read on ai_provider_keys for select to authenticated
  using (household_id = current_household_id() and is_household_member(household_id));
drop policy if exists ai_provider_keys_owner_write on ai_provider_keys;
create policy ai_provider_keys_owner_write on ai_provider_keys for all to authenticated
  using (household_id = current_household_id() and current_household_role() = 'owner')
  with check (household_id = current_household_id() and current_household_role() = 'owner');
grant select, insert, update, delete on ai_provider_keys to authenticated;
revoke all on ai_provider_keys from anon;

-- ---------- 26. PKV-PDF: einzelne Rechnungen und Bescheide ----------
-- schema_sync: vorstufe PKV-Altwerte ohne Banknachweis kennzeichnen, nicht loeschen
alter table health_expense_cases add column if not exists cash_verification_required boolean
  not null default false;
alter table health_expense_cases add column if not exists document_baseline jsonb;
update health_expense_cases c set cash_verification_required = true,
  updated_at = now(), version = version + 1
where not c.cash_verification_required and c.reimbursed_minor > 0 and exists (
  select 1 from reimbursement_documents d where d.case_id = c.id
    and d.document_type = 'benefit_statement'
) and c.reimbursed_minor <> coalesce((
  select sum(a.amount_minor) from health_transaction_allocations a
  where a.case_id = c.id and a.allocation_type = 'reimbursement'
), 0);
-- schema_sync: ende
alter table document_extractions add column if not exists previous_confirmed_data jsonb;
alter table reimbursement_submissions add column if not exists source_document_id uuid
  references reimbursement_documents(id) on delete set null;
alter table reimbursement_submissions add column if not exists source_item_index int
  check (source_item_index is null or source_item_index between 0 and 199);
alter table reimbursement_submissions add column if not exists decision_history jsonb
  not null default '[]'::jsonb;
alter table reimbursement_submissions add column if not exists decision_source text
  not null default 'manual' check (decision_source in ('manual', 'document'));
create unique index if not exists reimbursement_submissions_document_decision_uniq
  on reimbursement_submissions(household_id, case_id, payer_id, external_reference)
  where decision_source = 'document';

create table if not exists reimbursement_document_links (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references households(id) on delete cascade
    default current_household_id(),
  document_id uuid not null,
  case_id uuid not null,
  insured_person_id uuid not null,
  item_index int not null default -1 check (item_index between -1 and 199),
  created_at timestamptz not null default now(),
  unique (household_id, document_id, item_index),
  foreign key (document_id, household_id)
    references reimbursement_documents(id, household_id) on delete cascade,
  foreign key (case_id, insured_person_id, household_id)
    references health_expense_cases(id, insured_person_id, household_id) on delete cascade
);
create index if not exists reimbursement_document_links_case_idx
  on reimbursement_document_links(household_id, case_id);
alter table reimbursement_document_links enable row level security;
drop policy if exists reimbursement_document_links_read on reimbursement_document_links;
create policy reimbursement_document_links_read on reimbursement_document_links
  for select to authenticated using (
    household_id = current_household_id() and health_can_access_person(insured_person_id)
    and exists (select 1 from reimbursement_documents d where d.id = document_id)
  );
drop policy if exists reimbursement_document_links_write on reimbursement_document_links;
create policy reimbursement_document_links_write on reimbursement_document_links
  for all to authenticated using (
    household_id = current_household_id() and health_can_manage_person(insured_person_id)
    and exists (select 1 from reimbursement_documents d where d.id = document_id
      and health_can_manage_person(d.insured_person_id))
  ) with check (
    household_id = current_household_id() and health_can_manage_person(insured_person_id)
    and exists (select 1 from reimbursement_documents d where d.id = document_id
      and health_can_manage_person(d.insured_person_id))
  );
grant select, insert, update, delete on reimbursement_document_links to authenticated;
revoke all on reimbursement_document_links from anon;
