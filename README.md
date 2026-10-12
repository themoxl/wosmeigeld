# wosmeigeld

Haushalts- und Finanzplanung für Familien: Buchungen, Budgets, Verträge und
Fixkosten, Sparziele, Rücklagen, Vermögen und Schulden, Lebensmittelplanung
und Erstattungen — als eigenständige Desktop-Web-App, als native Mobile-App
(iOS/Android) und mit gemeinsamer Backend-Plattform.

Persönliches Projekt. Der Name ist Bairisch für die Frage, die die App
beantwortet: _Wo ist mein Geld?_

> **Hinweis zum Namensraum:** die Pakete heißen intern weiterhin
> `@finanzplaner/*`, die Docker-Images `finanzplaner-*`. Das ist der frühere
> Arbeitstitel und bewusst nicht umbenannt — ein Rename quer durch
> Workspace-Namen, Image-Namen und Importpfade wäre viel Risiko für nichts.

## Struktur

```text
apps/
  web/         Desktop-Web-App (Vite, React, TypeScript)
  mobile/      Expo / React Native, iOS + Android
services/
  api/         FastAPI BFF (Auth-Validierung, Fachlogik, CRUD, Jobsteuerung)
  worker/      Dramatiq-Worker (Import, KI-Kategorisierung, Dokumente, Exporte)
packages/
  contracts/       geteilte TypeScript-Typen, inkl. aus OpenAPI generierter Typen
  design-tokens/   Farben, Typografie, Abstände, Diagrammfarben
  test-fixtures/   ausschließlich anonymisierte Importdateien
supabase/      Migrationen (Schema + RLS), Seeds, lokale Supabase-Konfiguration
infra/         Caddy, Docker, Backup- und Monitoring-Konfiguration
scripts/       Entwicklungswerkzeuge (Start, Seed, Datenbankstatus)
```

Ein Monorepo, kein getrenntes Repo je App: Web, Mobile und API teilen sich mit
`packages/contracts` einen aus dem OpenAPI-Schema **generierten** Typvertrag.
Getrennt müsste man den versionieren und zwischen Repos synchron halten.

## Voraussetzungen

- Docker Desktop
- Node.js 20+ und pnpm (`corepack enable`)
- Python 3.12+ (nur für lokale Entwicklung außerhalb von Docker)
- [Supabase CLI](https://supabase.com/docs/guides/cli) für Auth/RLS/Storage
- Optional: [LM Studio](https://lmstudio.ai/) mit geladenem Modell für die
  lokalen KI-Funktionen (Kategorisierung, Dokumentauswertung)

## Schnellstart

```bash
cp .env.example .env

cd supabase
supabase start
cd ..

docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

`.env` zeigt per Default auf das lokale Supabase (`host.docker.internal`) —
kein weiterer Konfigurationsschritt nötig. Das startet Web, API, Worker und
Redis mit einem Befehl. Anschließend:

- Web: http://localhost:5173
- API: http://localhost:8882 (Swagger unter `/docs`)
- API-Systemstatus: http://localhost:8882/v1/system/health
- Web-Statusseite: http://localhost:5173/status
- Supabase Studio: http://127.0.0.1:55323

Für die Mobile-App (separat, außerhalb von Docker):

```bash
cd apps/mobile
pnpm install
pnpm start
```

Alternative ohne Supabase (kein Login möglich, nur Health-/Statusseiten):
`docker compose --profile local-db up --build` nutzt eine schlanke lokale
Postgres-Instanz ohne `auth`-Schema.

## Entwicklung ohne Docker

Der kurze Weg — alle Dienste in einer Konsole, mit den nötigen Overrides und
Portprüfungen:

```powershell
python scripts/dev.py
```

Von Hand, jeweils in einem eigenen Terminal:

```powershell
# 0) Vorbedingungen: supabase start (Ordner supabase/) und - weil .env auf den
#    Docker-Servicenamen "redis" zeigt, der nativ nicht auflöst - ein
#    einzelner Redis-Container:
docker run -d --name finanzplaner-redis-dev -p 6379:6379 redis:7-alpine

# Overrides für den nativen Weg. Echte Umgebungsvariablen haben Vorrang vor
# der .env, die Datei selbst bleibt unverändert (sie ist auf Docker
# eingestellt). In JEDEM der folgenden Terminals setzen:
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:LM_STUDIO_BASE_URL = "http://127.0.0.1:1234/v1"

# 1) API
cd services/api; .venv\Scripts\python.exe run_dev.py

# 2) Worker
cd services/worker; .venv\Scripts\dramatiq.exe worker.actors --processes 1 --threads 4

# 3) Web
pnpm --filter @finanzplaner/web dev
```

Drei Fallstricke, die dieser Weg bewusst umgeht:

- **API nur über `run_dev.py` starten**, nicht über `uvicorn app.main:app`.
  Sonst greift auf Windows die `WindowsSelectorEventLoopPolicy` zu spät (jede
  DB-Abfrage scheitert, `/v1/system/health` meldet `database: down`) und die
  `.env` im Repo-Wurzelverzeichnis wird nicht gefunden. Begründung im Detail
  in [`services/api/README.md`](services/api/README.md).
- **Redis fehlt nativ.** `.env` enthält `REDIS_URL=redis://redis:6379/0` — den
  Docker-Servicenamen. Ohne den Container oben und den Override melden Redis
  und Worker im Systemstatus `down`.
- **periodiq läuft nativ nicht.** Der periodische Scheduler ist SIGALRM-basiert
  (Unix-only, siehe Kommentar am `periodiq`-Service in `docker-compose.yml`);
  zeitgesteuerte Jobs gibt es nur im Container-Weg.

## Qualitätsschranken

```bash
# JavaScript / TypeScript
pnpm --filter @finanzplaner/web run typecheck
pnpm --filter @finanzplaner/web run test
pnpm --filter @finanzplaner/web run lint
pnpm format:check

# Python (services/api und services/worker)
ruff check .
mypy .
pytest
```

`ruff format` wird in diesem Projekt **nicht** verwendet — die Formatierung
des Python-Codes ist handgesetzt, `ruff check` und `mypy` sind die Schranken.

## Architektur in einem Absatz

React/Vite (Web), Expo/React Native (Mobile), FastAPI/Pydantic v2 (API),
PostgreSQL mit Row Level Security und Auth über Supabase, Dramatiq/Redis für
Hintergrundjobs, lokale KI über LM Studio oder optional Anthropic (Claude), Docker
Compose mit Caddy als Reverse Proxy. Das gesamte Schema steht in
`supabase/migrations/0001_schema.sql` und wird dort direkt bearbeitet.

## Daten und externe Dienste

Finanz- und Gesundheitsdaten liegen in der selbst betriebenen Installation.
Ob Inhalte an andere Dienste gehen, hängt von den gewählten Funktionen und
Serveradressen ab:

- **Lokale KI:** Anfragen gehen an den konfigurierten Modellserver. Wer die
  Daten im eigenen Netz halten möchte, muss eine entsprechende Serveradresse
  verwenden. Eine eigene URL kann auch auf einen externen Server zeigen.
- **Anthropic (Claude):** Bei Auswahl dieses Anbieters werden die für den
  jeweiligen KI-Aufruf benötigten Inhalte an Anthropic übertragen. Dazu können
  Buchungen, Vertragsdaten, Dokumenttexte und Chatfragen gehören. Der Anbieter
  benötigt einen hinterlegten API-Schlüssel; es gibt keinen automatischen
  Wechsel zwischen lokalem Modell und Anthropic.
- **Mobile Push-Nachrichten:** Bei aktivierten Benachrichtigungen laufen
  Geräte-Token und Nachrichten über Expo. Einige Nachrichten enthalten
  Vertragsnamen. Beträge werden in den Push-Nachrichten nicht genannt.
- **Optionale Websuche:** Die KI-Kostenschätzung kann die Projektbeschreibung
  als Suchanfrage an die konfigurierte SearXNG-Instanz senden. Je nach deren
  Konfiguration wird die Anfrage an externe Suchmaschinen weitergegeben.
- **Optionales Monitoring:** OpenTelemetry sendet Laufzeitdaten nur bei
  konfiguriertem Collector an die angegebene Adresse.

Die vorhandene Maskierung einzelner Kontodaten ist keine vollständige
Anonymisierung: Namen, Adressen, Freitexte und manche Schreibweisen von
Kontodaten können erhalten bleiben. Entsprechend sollten nur Inhalte an
externe Dienste übermittelt werden, die dafür vorgesehen sind.

Zugangsdaten, Datenbankabzüge, Gesundheitsdokumente und Screenshots mit
Nutzerdaten gehören in private Ablagen. Die `.env.example` enthält ausschließlich
öffentliche Demo-Werte; für einen erreichbaren Betrieb sind eigene
Zugangsdaten erforderlich.

## Lizenz

[GNU Affero General Public License v3.0](LICENSE).

Kurz: nutzen, aendern und weitergeben ist erlaubt. Wer eine geaenderte
Fassung weitergibt **oder sie ueber ein Netzwerk anderen zugaenglich macht**,
muss den vollstaendigen Quelltext dieser Fassung unter derselben Lizenz
herausgeben. Der eigene Betrieb im Haushalt loest das nicht aus - dafuer
braucht es keine Erlaubnis und keine Veroeffentlichung.

Sicherheitsluecken bitte nicht als Issue melden, sondern nach
[SECURITY.md](SECURITY.md).

## Haftungsausschluss

Dieses Projekt ist ein privates Werkzeug und **keine Finanz-, Steuer- oder
Rechtsberatung**.

Die mitgelieferten Förderprogramme unter
`services/api/app/data/funding_packages/` geben öffentlich veröffentlichte
Konditionen von KfW, BAFA und BayernLabo zum jeweils vermerkten
Recherchestand wieder (Feld `researched_on`, Quelle je Programm als
`official_url`). Förderkonditionen ändern sich laufend. **Verbindlich ist
ausschließlich die offizielle Quelle** — vor jeder Entscheidung dort prüfen.
Für Richtigkeit, Vollständigkeit und Aktualität wird keine Gewähr übernommen.
