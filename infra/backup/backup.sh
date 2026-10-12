#!/usr/bin/env bash
# Erstellt ein verschluesseltes Postgres-Backup (pg_dump -Fc + Manifest,
# siehe lib.sh) und wendet die Aufbewahrungsrichtlinie an.
#
# Usage:
#   DATABASE_URL=postgresql+psycopg://... AGE_RECIPIENT=age1... ./backup.sh
#   ./backup.sh --out-dir DIR --retention N --recipient age1...
#
# Siehe README.md fuer Cron-/Systemd-Beispiele und Schluesselverwaltung.
set -euo pipefail

SCRIPT_TAG="backup"
# shellcheck source=./lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

OUT_DIR="${BACKUP_OUT_DIR:-$BACKUP_LIB_DIR/out}"
RETENTION="${BACKUP_RETENTION_COUNT:-14}"
RECIPIENT="${AGE_RECIPIENT:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --retention) RETENTION="$2"; shift 2 ;;
    --recipient) RECIPIENT="$2"; shift 2 ;;
    *) die "unbekanntes Argument: $1" ;;
  esac
done

require_cmd docker
require_cmd tar
require_cmd sha256sum
[ -n "${DATABASE_URL:-}" ] || die "DATABASE_URL ist nicht gesetzt"
[ -n "$RECIPIENT" ] || die "AGE_RECIPIENT (oder --recipient) ist erforderlich (siehe README.md)"

mkdir -p "$OUT_DIR"

WORKDIR="$(mktemp -d)"
register_cleanup "rm -rf '$WORKDIR'"
trap run_cleanups EXIT INT TERM

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASENAME="finanzplaner_${TIMESTAMP}"
LIBPQ_URL="$(to_libpq_url "$DATABASE_URL")"

log "Dumpe Datenbank ($(redact_url "$DATABASE_URL"))..."
# --data-only: das Schema ist bereits versioniert in
# supabase/migrations/0001_schema.sql - ein Restore wendet immer zuerst das
# aktuelle Schema an (z.B. `supabase db reset`) und spielt danach nur die
# Daten ein. Das vermeidet Drift zwischen einem eingefrorenen Schema-Snapshot
# im Backup und dem tatsaechlich aktuellen, versionierten Schema.
# --schema=public: nur die fachlichen Anwendungsdaten (vollstaendig in
# 0001_schema.sql definiert). Supabases eigene interne Schemata (auth,
# storage, realtime, vault, supabase_functions/_migrations) sind Supabases
# eigener Betriebsbereich - fuer self-hosted Supabase gilt dafuer Supabases
# eigene, unabhaengige Backup-Strategie, nicht die dieser Anwendung.
# --exclude-table-data fuer GLOBALLY_SEEDED_TABLES (siehe lib.sh): deren
# Inhalt kommt bereits aus der Schemaanwendung selbst, ein zusaetzlicher
# Data-Only-Restore derselben Zeilen wuerde nur an deren Unique-Constraints
# scheitern.
exclude_args=()
for t in "${GLOBALLY_SEEDED_TABLES[@]}"; do
  exclude_args+=(--exclude-table-data="public.${t}")
done
pg_tool pg_dump --format=custom --data-only --no-owner --no-privileges \
  --schema=public "${exclude_args[@]}" "$LIBPQ_URL" > "$WORKDIR/dump.bin"
[ -s "$WORKDIR/dump.bin" ] || die "pg_dump hat keine (oder eine leere) Datei erzeugt"

log "Erzeuge Manifest (Zeilenzahl + Pruefsumme je Tabelle)..."
_source_psql() { pg_tool psql "$LIBPQ_URL" "$@"; }
generate_manifest _source_psql "$WORKDIR/manifest.json" "$(redact_url "$DATABASE_URL")"

log "Packe Dump + Manifest..."
tar -C "$WORKDIR" -cf "$WORKDIR/${BASENAME}.tar" dump.bin manifest.json

log "Verschluessele fuer $RECIPIENT..."
age_encrypt "$RECIPIENT" "$WORKDIR/${BASENAME}.tar" "$OUT_DIR/${BASENAME}.tar.age"
[ -s "$OUT_DIR/${BASENAME}.tar.age" ] || die "Verschluesselung hat keine (oder eine leere) Datei erzeugt"

CHECKSUM="$(sha256sum "$OUT_DIR/${BASENAME}.tar.age" | awk '{print $1}')"
SIZE="$(wc -c < "$OUT_DIR/${BASENAME}.tar.age" | tr -d ' ')"
log "Backup erstellt: $OUT_DIR/${BASENAME}.tar.age (${SIZE} bytes, sha256=${CHECKSUM})"

log "Wende Aufbewahrungsrichtlinie an (behalte die neuesten ${RETENTION})..."
mapfile -t old_backups < <(ls -1t "$OUT_DIR"/*.tar.age 2>/dev/null | tail -n +"$((RETENTION + 1))")
for old in "${old_backups[@]:-}"; do
  [ -n "$old" ] || continue
  log "  loesche altes Backup: $old"
  rm -f "$old"
done

log "Fertig."
