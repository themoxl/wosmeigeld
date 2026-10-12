#!/usr/bin/env bash
# Manueller Disaster-Recovery-Restore: entschluesselt ein Backup und spielt
# es in eine echte Zieldatenbank ein. Das Backup enthaelt nur Daten (siehe
# backup.sh) - --target-url muss daher bereits das aktuelle Schema haben
# (z.B. per `supabase db reset` bzw. das Anwenden von
# supabase/migrations/0001_schema.sql gegen eine leere Datenbank), BEVOR
# dieses Skript laeuft. Destruktiv (loescht/ueberschreibt vorhandene Daten in
# den betroffenen Tabellen) - erfordert deshalb --yes. Fuer den
# automatisierten Beweis, dass ein Backup ueberhaupt wiederherstellbar ist,
# siehe restore_test.sh (isolierte Wegwerfdatenbank inkl. Schema-Aufbau,
# kein --target-url noetig).
#
# Usage:
#   ./restore.sh <backup.tar.age> --identity <age-identity-datei> \
#     --target-url postgresql+psycopg://... --yes
set -euo pipefail

SCRIPT_TAG="restore"
# shellcheck source=./lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

BACKUP_FILE=""
IDENTITY_FILE=""
TARGET_URL=""
CONFIRMED=0

while [ $# -gt 0 ]; do
  case "$1" in
    --identity) IDENTITY_FILE="$2"; shift 2 ;;
    --target-url) TARGET_URL="$2"; shift 2 ;;
    --yes) CONFIRMED=1; shift ;;
    -*) die "unbekanntes Argument: $1" ;;
    *) BACKUP_FILE="$1"; shift ;;
  esac
done

require_cmd docker
require_cmd tar
[ -n "$BACKUP_FILE" ] || die "Usage: restore.sh <backup.tar.age> --identity <datei> --target-url <url> --yes"
[ -f "$BACKUP_FILE" ] || die "Backup-Datei nicht gefunden: $BACKUP_FILE"
[ -n "$IDENTITY_FILE" ] && [ -f "$IDENTITY_FILE" ] || die "--identity <age-identity-datei> ist erforderlich"
[ -n "$TARGET_URL" ] || die "--target-url <postgresql(+psycopg)://...> ist erforderlich"
[ "$CONFIRMED" -eq 1 ] || die "destruktiv (--clean --if-exists auf --target-url) - erfordert --yes zur Bestaetigung"

WORKDIR="$(mktemp -d)"
register_cleanup "rm -rf '$WORKDIR'"
trap run_cleanups EXIT INT TERM

LIBPQ_URL="$(to_libpq_url "$TARGET_URL")"

log "Entschluessele $BACKUP_FILE..."
age_decrypt "$IDENTITY_FILE" "$BACKUP_FILE" "$WORKDIR/backup.tar"

log "Entpacke..."
tar -C "$WORKDIR" -xf "$WORKDIR/backup.tar" dump.bin manifest.json

log "Spiele in $(redact_url "$TARGET_URL") ein (nur Daten, Schema muss bereits vorhanden sein)..."
pg_tool pg_restore --no-owner --no-privileges --data-only --disable-triggers \
  --dbname="$LIBPQ_URL" < "$WORKDIR/dump.bin"

log "Verifiziere gegen mitgeliefertes Manifest..."
_target_psql() { pg_tool psql "$LIBPQ_URL" "$@"; }
generate_manifest _target_psql "$WORKDIR/actual_manifest.json" "$(redact_url "$TARGET_URL")"

if diff_manifests "$WORKDIR/manifest.json" "$WORKDIR/actual_manifest.json"; then
  log "RESTORE OK: Zieldatenbank stimmt mit dem Manifest im Backup ueberein."
else
  die "RESTORE VERIFIKATION FEHLGESCHLAGEN: siehe Tabellenbericht oben."
fi
