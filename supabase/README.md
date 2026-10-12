# supabase

Lokale Supabase-Konfiguration (Auth, Storage, Postgres, RLS).

```bash
supabase start   # startet Postgres/Auth/Storage/Studio lokal, wendet migrations/ an
supabase db reset  # Datenbank neu aufsetzen und migrations/ (+ seed/) neu anwenden
supabase stop
```

Nach `supabase start`: API unter `http://127.0.0.1:55321`, Postgres unter
`127.0.0.1:55322`, Studio unter `http://127.0.0.1:55323` (Ports bewusst
abweichend von den Supabase-Standardports gewaehlt, damit mehrere lokale
Projekte parallel laufen koennen, siehe `config.toml`).

## Schema-Verwaltung: eine Datei, direkt bearbeitet

**Regel (verbindlich): das gesamte Schema steht in
`migrations/0001_schema.sql` und wird dort direkt bearbeitet.** Neue
nummerierte Dateien (`0002_...`, `0003_...`) werden waehrend der
Entwicklungsphase nicht mehr angelegt. Jede Schemaaenderung - neue Tabelle,
Spalte, RLS-Policy, SQL-Funktion, Enum-Wert, Constraint - wird an einer
sinnvollen Stelle nahe den verwandten Anweisungen eingearbeitet.

Warum: Es gibt keinen Produktionsstand, dessen Historie zu bewahren waere,
und genau eine Datenbank - den Entwicklungsrechner. Eine wachsende Kette von
Einzelmigrationen kostet dort nur Aufmerksamkeit und verdeckt den
tatsaechlichen Stand des Schemas.

Dazu kam ein konkreter Fehler: `infra/backup/lib.sh` setzt
`SCHEMA_FILE="$REPO_ROOT/supabase/migrations/0001_schema.sql"`, und
`restore_test.sh` spielt ausschliesslich diese eine Datei gegen eine leere
Datenbank ein. Solange `0002..0014` danebenlagen, pruefte der
Disaster-Recovery-Test also einen Restore gegen ein Schema, das dreizehn
Migrationen hinterherhinkte - ohne dass irgendetwas rot wurde.

Der Weg zurueck ist ausdruecklich vorgesehen: **sobald es echte
Produktionsdaten gibt**, wird wieder inkrementell und nummeriert migriert,
mit dem dann erreichten `0001_schema.sql` als Baseline. Ab diesem Zeitpunkt
gilt die umgekehrte Regel, denn dann ist eine nachtraegliche Aenderung an
einer bereits angewendeten Datei fuer jede laufende Datenbank unsichtbar.

Zwei frueher hier stehende Regeln gelten **nicht mehr**:

- "`0001_schema.sql` wird nicht mehr bearbeitet" (Stand bis 2026-08-03)
- "jede weitere Schemaaenderung kommt als neue, fortlaufend nummerierte
  Migrationsdatei dazu" (derselbe Stand)

### Konventionen fuer Schemaaenderungen

- **Direkt in `0001_schema.sql`**, thematisch einsortiert - nicht ans Ende
  angehaengt, sonst entsteht dieselbe Chronologie nur ohne Dateinamen.
- **Additiv und idempotent** schreiben (`create table if not exists`,
  `alter table ... add column if not exists`, `drop policy if exists` vor
  `create policy`, `drop constraint if exists` vor `add constraint`). Das
  bleibt noetig: die bestehende Entwicklungsdatenbank wird nicht bei jeder
  Aenderung neu aufgebaut, die Datei muss also gegen eine bereits migrierte
  Datenbank erneut laufen koennen.
- Kopfkommentar am Abschnitt: welches Problem, und warum so geloest.
- Neue haushaltsgebundene Tabellen brauchen `household_id`, aktivierte
  Row-Level-Security und Policies analog zum Bestand (Lesen ueber
  Haushaltsmitgliedschaft, Schreiben ueber `can_edit_household()`).
- Die Abschnittsmarkierungen `-- Ehemals: ...` stammen aus den beiden
  Zusammenfuehrungen und sind reine Herkunftsangaben. Neue Aenderungen
  brauchen keine solche Markierung.
- Kommentare im Code nennen teils noch Nummern ("Migration 0007", "seit
  0014"). Das sind **historische Ausbaustufen, keine Dateien mehr** - der
  zugehoerige Abschnitt steht unter der passenden `-- Ehemals:`-Markierung in
  `0001_schema.sql`. Neue Kommentare sollten keine Nummern mehr nennen.

### Aktueller Stand

| Datei             | Inhalt                                      |
| ----------------- | ------------------------------------------- |
| `0001_schema.sql` | das vollstaendige Datenmodell, ~4050 Zeilen |

Entstanden in drei Zusammenfuehrungen: zuerst aus elf Einzelmigrationen
(`0001_init.sql` .. `0011_classification_runs.sql`), dann am 2026-08-03 aus
den inzwischen erneut angewachsenen `0002_audit_log.sql` ..
`0014_contracts.sql`, zuletzt am 2026-08-04 aus
`0015_contract_intelligence.sql` (Phase 4 der Vertragsverwaltung). Jedes Mal in
unveraenderter Anwendungsreihenfolge.

Die zweite Zusammenfuehrung wurde gegen zwei frisch aufgesetzte Datenbanken
geprueft - einmal die vierzehn Originaldateien nacheinander, einmal die
zusammengefuehrte Datei. Tabellen, Spalten, Constraints, Indizes,
Funktionsruempfe, Trigger, RLS-Policies und Enums stimmten vollstaendig
ueberein.

Die dritte Zusammenfuehrung wurde gegen die laufende Entwicklungsdatenbank
geprueft: auth-Stub und `0001_schema.sql` auf eine leere Datenbank angewendet -
also exakt der Weg aus `infra/backup/restore_test.sh` - ergaben dieselben
50 Tabellen, 574 Spalten und 144 Indizes wie der laufende Stand.

### Anwenden

`supabase start` / `supabase db reset` wenden alles in
`supabase/migrations/` in Dateinamensreihenfolge an (feste CLI-Vorgabe, kein
konfigurierbarer Pfad). Deshalb bleibt der Ordner bestehen, auch wenn nur
eine Datei darin liegt. `supabase db reset` ist damit jederzeit ein sauberer
Weg, die lokale DB auf den aktuellen Schema-Stand zu bringen.

Ohne installierte Supabase CLI laesst sich das Schema auch direkt gegen die
lokale Postgres-Instanz anwenden (dank Idempotenz auch gegen eine bereits
befuellte Datenbank):

```bash
python -c "import psycopg; c=psycopg.connect('postgresql://postgres:postgres@127.0.0.1:55322/postgres'); c.execute(open('supabase/migrations/0001_schema.sql',encoding='utf-8').read()); c.commit()"
```

`seed/` enthaelt spaeter ausschliesslich synthetische Demodaten fuer einen
Demohaushalt (Phase 10), niemals produktive Bankdaten (Abschnitt 5, Punkt 10).
