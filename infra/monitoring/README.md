# infra/monitoring

`GET /v1/system/health` (`services/api`) deckt "ist Dienst X gerade
erreichbar" bereits vollstaendig ab (API/Datenbank/Redis/Worker/Storage/LM
Studio), inklusive der Web-/Mobile-Statusseiten. Dieses Verzeichnis fuegt
bewusst **keine** zweite Liveness-/Uptime-Quelle hinzu - siehe
[`../../docs/BETRIEB.md`](../../docs/BETRIEB.md) fuer die Begruendung.

Was hier liegt, deckt stattdessen ab, was der Health-Endpoint strukturell
nicht kann:

- **`otel-collector-config.yaml`** - Konfiguration fuer einen optionalen
  OpenTelemetry-Collector, aktiviert ueber die Ueberlagerung
  `../../docker-compose.monitoring.yml`:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up
  ```
  Nimmt OTLP-Traces von `services/api`
  (`services/api/app/core/tracing.py`) und `services/worker`
  (`services/worker/worker/tracing.py`) entgegen, sobald
  `OTEL_EXPORTER_OTLP_ENDPOINT` gesetzt ist (Standard: leer/aus, siehe
  `.env.example` - kein Overhead und keine erzwungene Zusatzinfrastruktur
  fuer Privat/Home-Server-Betrieb). Exportiert aktuell nur ueber einen
  `debug`-Exporter (`docker compose logs otel-collector`) - **liefert
  bewusst kein Prometheus/Grafana mit**, das waere fuer die primaere
  Zielgruppe (ein einzelner kleiner Server) unverhaeltnismaessig; wer eigene
  Visualisierung betreibt, bindet den Collector an die eigene
  Infrastruktur an (ein Prometheus-Exporter im Collector ist eine
  naheliegende spaetere Ergaenzung, sobald tatsaechlich OTel-Metriken
  gesendet werden - aktuell werden nur Traces gesendet).

Job-/Importmetriken (Dauer, Format, Zeilenanzahl) werden **nicht**
verdoppelt: sie liegen bereits durabel in den Tabellen `import_runs` und
`llm_calls` (Postgres) - direkt per SQL auswertbar (Supabase Studio oder
`psql`), das ist die massgebliche Quelle fuer historische/Audit-Zwecke. Die
OTel-Spans dienen ausschliesslich der Live-Sicht waehrend ein Request/Job
laeuft.

Strukturierte JSON-Logs mit Request-/Job-Korrelations-ID und PII-Redaction
sind direkt in `services/api/app/core/logging.py` bzw.
`services/worker/worker/logging_config.py` implementiert (kein separates
Infra-Setup noetig) - siehe `docs/BETRIEB.md`.
