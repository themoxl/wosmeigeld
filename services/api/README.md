# services/api

FastAPI-BFF. Python 3.12+, Pydantic v2. Siehe Architekturplan Abschnitt 2
(Architekturentscheidungen) und Abschnitt 12 (API-Oberflaeche).

## Lokale Entwicklung ohne Docker

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp ../../.env.example ../../.env   # falls noch nicht geschehen
supabase start                     # im Ordner supabase/, fuer Auth/RLS

# Redis wird auch nativ gebraucht (Jobsteuerung, Worker-Heartbeat im
# Systemstatus). .env zeigt auf den Docker-Servicenamen "redis", der nativ
# nicht aufloest - deshalb ein einzelner Container plus Override:
docker run -d --name finanzplaner-redis-dev -p 6379:6379 redis:7-alpine

python run_dev.py                  # Port 8882, mit Auto-Reload
```

`run_dev.py` ist der unterstuetzte native Startweg (Windows wie Linux/macOS).
`python run_dev.py --port 8010 --no-reload` fuer einen zweiten,
reload-freien Prozess.

**Nicht `python -m uvicorn app.main:app` verwenden.** Zwei Gruende, beide auf
Windows verifiziert:

1. **Event-Loop-Policy.** psycopg (async) kann nicht mit Windows'
   Default-`ProactorEventLoop` arbeiten. Der Guard in `app/main.py` setzt
   `WindowsSelectorEventLoopPolicy`, greift unter dem uvicorn-CLI aber zu
   spaet: uvicorn erzeugt seinen Loop, bevor es `app.main` importiert. Ohne
   `--reload` scheitert dann jede DB-Abfrage mit der Meldung "Psycopg cannot
   use the 'ProactorEventLoop' to run in async mode"
   (`psycopg.InterfaceError`), `/v1/system/health` meldet `database: down`.
   (Mit `--reload` geht es nur zufaellig gut, weil uvicorn im
   Subprozessmodus dieselbe Policy selbst setzt - darauf sollte sich niemand
   verlassen.) `run_dev.py` setzt die Policy deterministisch, bevor ein Loop
   entsteht.
2. **Konfiguration.** pydantic-settings sucht die `.env` relativ zum
   Arbeitsverzeichnis (`app/core/config.py`, `env_file=".env"`). Die einzige
   `.env` liegt im Repo-Root; ein Start aus `services/api` heraus findet sie
   nicht und faellt still auf die Docker-Defaults (`postgres:5432`,
   `redis:6379`) zurueck. `run_dev.py` wechselt dafuer ins Repo-Root und legt
   `services/api` auf den `sys.path`.

### Umgebungs-Overrides fuer den nativen Weg

Die `.env` im Repo-Root ist auf den Docker-Weg eingestellt (Servicenamen aus
`docker-compose.yml`). Nativ zwei Werte ueberschreiben - echte
Umgebungsvariablen haben Vorrang vor der `.env`, die Datei selbst bleibt
unveraendert:

```powershell
$env:REDIS_URL = "redis://127.0.0.1:6379/0"          # statt redis://redis:6379/0
$env:LM_STUDIO_BASE_URL = "http://127.0.0.1:1234/v1"  # LM Studio lauscht nur lokal
python run_dev.py
```

`DATABASE_URL` und `SUPABASE_URL` funktionieren unveraendert, weil Docker
Desktop `host.docker.internal` auch auf dem Host aufloest. Ohne Docker Desktop
stattdessen `127.0.0.1:55322` bzw. `127.0.0.1:55321` setzen.

Der Dramatiq-Worker laeuft nativ mit
`dramatiq worker.actors --processes 1 --threads 4` (siehe
`services/worker/README.md`) und braucht dieselben Overrides. Der
periodiq-Scheduler dagegen laeuft nativ unter Windows nicht - er ist
SIGALRM-basiert (Unix-only, siehe Kommentar am `periodiq`-Service in
`docker-compose.yml`) und existiert nur im Container.

## Tests, Lint, Typpruefung

```bash
pytest
ruff check .
mypy app
```

`tests/test_household_isolation.py` ist der kritische Phase-1-Abnahmetest
("zwei Testhaushalte koennen gegenseitig keinerlei Daten lesen") - er
signup-t zwei echte Nutzer gegen ein lokales Supabase und prueft die
Isolation sowohl auf API-Ebene (403 bei fremdem Haushaltskontext) als auch
direkt auf SQL/RLS-Ebene (auch wenn die API-Pruefung uebersprungen wuerde).
Braucht ein laufendes `supabase start` (siehe `tests/conftest.py` fuer die
erwarteten Umgebungsvariablen).

## PKV-Bescheide und Bankeingang

PDF-Angaben zu Auszahlungen sind bewilligte Betraege, keine Bankeingaenge.
Bestaetigte Bescheide werden pro Rechnung und Kostentraeger gespeichert;
dieselbe Bescheidreferenz aktualisiert eine Entscheidung und erhaelt ihre
Historie. Sammelabrechnungen haben explizite Positionszuordnungen. Die
Vorschau `/v1/reimbursements/documents/{id}/case-preview` schreibt nichts.
Uebernahme und Loesen einer Zuordnung veraendern keine Bankzuordnungen.
Manuell bestaetigte Dokumentfelder bleiben bei erneuter Auswertung als
Vergleichsbasis erhalten.

Bestehende Datenbanken brauchen den regulaeren Abgleich mit
`scripts/schema_sync.py` und `supabase/migrations/0001_schema.sql`, Abschnitt 26.
Die Vorstufe markiert bisherige PDF-basierte Zahlungseingaenge zur Pruefung;
gespeicherte Altbetraege bleiben erhalten. API, Worker und Web muessen gemeinsam
aktualisiert werden; API und Worker brauchen die neuen Requirements.

Die Integrationstests `tests/test_pkv_pdf_workflow.py` koennen mit einer
isolierten PostgreSQL-Datenbank aus `scripts/pkv_test_database.py` laufen.
`PKV_TEST_ADMIN_DSN` waehlt den Server; `DATABASE_URL` und
`TEST_DIRECT_DATABASE_URL` muessen auf die neu angelegte Datenbank zeigen.
Die Authentifizierung liefert im Test Testidentitaeten; echte API-Transaktionen
und RLS pruefen Haushalts- und Personenrechte. Der Helfer entfernt mit `--drop`
nur Datenbanken mit seinem Namenspraefix und seiner eigenen Testmarkierung.

## Auth

Signup/Login laufen **client-seitig direkt gegen Supabase Auth** (Web/Mobile
nutzen `@supabase/supabase-js`), nicht ueber diese API. Die API validiert nur
das resultierende JWT (`app/auth/jwt.py`): aktuelle Supabase-CLI-Versionen
signieren asymmetrisch (ES256) und stellen die Schluessel unter
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json` bereit; ein HS256-Fallback
ueber `SUPABASE_JWT_SECRET` bleibt fuer aeltere/self-hosted Instanzen ohne
JWT-Signing-Keys erhalten.

Jeder authentifizierte Request laeuft in `app/auth/dependencies.py` in einer
eigenen DB-Transaktion, die `SET LOCAL ROLE authenticated` setzt und
`request.jwt.claims` nachbildet (das, was PostgREST fuer jeden Request tun
wuerde) - so greifen die RLS-Policies aus `supabase/migrations/0001_schema.sql`
korrekt, obwohl die API direkt per SQLAlchemy/psycopg verbindet statt ueber
PostgREST. Haushaltsgebundene Endpunkte verlangen zusaetzlich den Header
`X-Household-Id`; die Mitgliedschaft wird zuerst explizit in Python geprueft
(Abschnitt 5, Punkt 5) und danach als Session-GUC gesetzt, die RLS als zweite
Schutzschicht erneut prueft.

## Endpunkte (Stand Phase 2)

| Methode  | Pfad                                                  | Auth                    | Zweck                                                                  |
| -------- | ----------------------------------------------------- | ----------------------- | ---------------------------------------------------------------------- |
| GET      | `/health`                                             | -                       | Liveness-Probe (Container-Healthcheck)                                 |
| GET      | `/v1/system/health`                                   | -                       | Aggregierter Status: API, Datenbank, Redis, Worker, Storage, LM Studio |
| GET      | `/v1/me`                                              | Bearer                  | Profil + Haushaltsmitgliedschaften (legt Profil bei erstem Aufruf an)  |
| GET/POST | `/v1/households`                                      | Bearer                  | Haushalte auflisten / anlegen (seedet Standard-Kategorien)             |
| PATCH    | `/v1/households`                                      | Bearer + X-Household-Id | aktuellen Haushalt umbenennen (nur `owner`)                            |
| GET/POST | `/v1/accounts`                                        | Bearer + X-Household-Id | Konten auflisten / anlegen                                             |
| PATCH    | `/v1/accounts/{id}`                                   | Bearer + X-Household-Id | Konto bearbeiten/schliessen                                            |
| POST     | `/v1/accounts/{id}/reconcile`                         | Bearer + X-Household-Id | Kontoabstimmung gegen Kontoauszugssaldo                                |
| GET      | `/v1/accounts/{id}/reconciliations`                   | Bearer + X-Household-Id | Abstimmungshistorie                                                    |
| GET      | `/v1/categories`                                      | Bearer + X-Household-Id | aktiver Picker-Baum (Gruppen mit Kategorien)                           |
| GET/POST | `/v1/transactions`                                    | Bearer + X-Household-Id | Buchungen auflisten / manuell anlegen                                  |
| GET      | `/v1/transactions/{id}`                               | Bearer + X-Household-Id | Buchungsdetail inkl. Splits                                            |
| PATCH    | `/v1/transactions/{id}`                               | Bearer + X-Household-Id | Datum/Konto/Notiz/Status bearbeiten (nicht Betrag/Kategorie)           |
| DELETE   | `/v1/transactions/{id}`                               | Bearer + X-Household-Id | Soft-Delete, loest bestehende Transferverknuepfung                     |
| PUT      | `/v1/transactions/{id}/splits`                        | Bearer + X-Household-Id | Betrag+Splits atomar ersetzen (Split-Summen-Invariante)                |
| POST     | `/v1/transactions/bulk-categorize`                    | Bearer + X-Household-Id | Bulk-Kategorisierung (nur Einzel-Split-Buchungen, Altvertrag)          |
| PUT      | `/v1/transactions/{id}/category`                      | Bearer + X-Household-Id | Kategorie genau eines Splits, versionsgeprueft                         |
| POST     | `/v1/transactions/bulk-category/preview` und `/apply` | Bearer + X-Household-Id | Mehrfachzuordnung mit Einzelresultat je Buchung (max. 500)             |
| POST     | `/v1/transfers/link`                                  | Bearer + X-Household-Id | zwei Buchungen als Transfer verknuepfen                                |
| DELETE   | `/v1/transfers/link/{id}`                             | Bearer + X-Household-Id | Transferverknuepfung aufheben                                          |

### Kategorienverwaltung

Verbindliche Detailplanung: [`docs/KATEGORISIERUNGSPLAN.md`](../../docs/KATEGORISIERUNGSPLAN.md).
Lesen setzt Haushaltsmitgliedschaft voraus, Schreiben `owner`/`editor`; der
irreversible Merge und das Anwenden eines Paket-Upgrades sind `owner`-only.
Aendern und Archivieren verlangen `If-Match` (fehlt er: `428`, veraltet: `409`).
Fremde UUIDs liefern `404`, damit keine Existenz eines anderen Haushalts
verraten wird.

| Methode | Pfad                                                 | Zweck                                                    |
| ------- | ---------------------------------------------------- | -------------------------------------------------------- |
| GET     | `/v1/categories/catalog`                             | Verwaltungsansicht inkl. archivierter Zeilen und Nutzung |
| POST    | `/v1/categories/groups`                              | Gruppe anlegen                                           |
| PATCH   | `/v1/categories/groups/{id}`                         | Gruppe aendern (`If-Match`)                              |
| POST    | `/v1/categories/groups/reorder`                      | Gruppen atomar sortieren (Baum-Revision)                 |
| POST    | `/v1/categories/groups/{id}/archive-preview`         | Auswirkung einer Gruppenarchivierung                     |
| POST    | `/v1/categories/groups/{id}/archive` · `/restore`    | Gruppe archivieren / wiederherstellen                    |
| POST    | `/v1/categories`                                     | Kategorie anlegen                                        |
| PATCH   | `/v1/categories/{id}`                                | Kategorie aendern (`If-Match`)                           |
| POST    | `/v1/categories/reorder`                             | Kategorien einer Gruppe atomar sortieren                 |
| POST    | `/v1/categories/{id}/move-preview` · `/move`         | Gruppenwechsel mit historischer Auswirkung               |
| POST    | `/v1/categories/{id}/archive-preview` · `/archive`   | Archivieren mit Referenzaufloesung                       |
| POST    | `/v1/categories/{id}/restore`                        | Kategorie wiederherstellen                               |
| POST    | `/v1/categories/merge-preview` · `/merge`            | Zusammenfuehren (owner, idempotent ueber `request_id`)   |
| GET     | `/v1/categories/standard-package`                    | installierte und verfuegbare Taxonomieversion            |
| POST    | `/v1/categories/standard-package/preview` · `/apply` | additive Ergaenzung berechnen und anwenden (owner)       |

Es gibt bewusst **kein** `DELETE` auf Kategorien oder Gruppen - auch nicht auf
Datenbankebene (das Recht ist der Anwendungsrolle entzogen, siehe
`supabase/migrations/0001_schema.sql`).

Vollstaendige Sollstruktur der API in Abschnitt 12 des Architekturplans.
`tests/test_transactions_phase2.py` deckt die Phase-2-Invarianten ab
(Split-Summe, Transfer-Neutralitaet/Exklusivitaet, Abstimmung),
`tests/test_categories_*.py` die Kategorieninvarianten (Haushaltsisolierung,
Versionskonflikte, Archivierung ohne Summenaenderung, summenneutraler Merge,
End-to-End-Kette).

## Migrationen

**Der Migrationspfad dieses Projekts sind fortlaufend nummerierte SQL-Dateien
unter `supabase/migrations/`** - nicht Alembic. `0001_schema.sql` ist die
eingefrorene Baseline, jede weitere Schemaaenderung kommt als neue Datei
(`0002_...`, `0003_...`, ...) dazu, additiv und idempotent geschrieben.
Angewendet ueber die Supabase CLI (`supabase db reset`) oder einzeln per
psycopg. Konventionen und Begruendung: `supabase/README.md`.

### Warum `alembic/` hier trotzdem liegt

`alembic/` stammt aus dem Projektgeruest und ist **ungenutzt**:
`alembic/versions/` ist leer, es gibt keine ORM-Modelle als
Autogenerate-Basis, und weder Anwendungscode noch Docker-Images noch CI
rufen `alembic` jemals auf. Es ist ausdruecklich **kein** paralleler oder
kuenftiger Migrationsweg - wer das Schema aendern will, legt eine neue Datei
unter `supabase/migrations/` an.

Der sachliche Grund gegen Alembic bleibt bestehen: die Auth/RLS-Spezifika
(Supabase-`auth`-Schema, `auth.uid()`, GUC-basierter Haushaltskontext,
63 RLS-Policies ueber `can_edit_household()`) lassen sich nicht sinnvoll aus
generischen SQLAlchemy-Modellen generieren. Wenn Alembic dauerhaft ungenutzt
bleibt, gehoert das Verzeichnis samt `alembic.ini` und der
Alembic-Dependency entfernt - das ist bewusst noch offen und nicht
stillschweigend hier mitentschieden.
