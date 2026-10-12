#!/usr/bin/env bash
# Automatisierter Restoretest: beweist, dass ein verschluesseltes Backup
# tatsaechlich wiederhergestellt werden kann - nicht nur, dass die Datei
# existiert (Abnahmekriterium aus dem Architekturplan, Phase 10). Restauriert
# in eine voellig isolierte Wegwerf-Postgres-Instanz (echtes Schema inkl.
# eines minimalen auth-Stubs, siehe restoretest/auth_stub.sql) und vergleicht
# das Ergebnis Tabelle fuer Tabelle gegen das im Backup mitgelieferte
# Manifest. Ruehrt niemals die Quelldatenbank an; raeumt die Wegwerf-
# Instanz immer auf, auch bei Fehlern/Abbruch.
#
# Usage:
#   AGE_IDENTITY_FILE=... ./restore_test.sh [<backup.tar.age>]
#   (ohne Dateiargument: neuestes *.tar.age in BACKUP_OUT_DIR)
set -euo pipefail

SCRIPT_TAG="restore-test"
# shellcheck source=./lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

OUT_DIR="${BACKUP_OUT_DIR:-$BACKUP_LIB_DIR/out}"
IDENTITY_FILE="${AGE_IDENTITY_FILE:-}"
BACKUP_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --identity) IDENTITY_FILE="$2"; shift 2 ;;
    -*) die "unbekanntes Argument: $1" ;;
    *) BACKUP_FILE="$1"; shift ;;
  esac
done

require_cmd docker
require_cmd tar

if [ -z "$BACKUP_FILE" ]; then
  BACKUP_FILE="$(ls -1t "$OUT_DIR"/*.tar.age 2>/dev/null | head -n1 || true)"
  [ -n "$BACKUP_FILE" ] || die "kein Backup angegeben und keines in $OUT_DIR gefunden"
  log "kein Dateiargument - verwende neuestes Backup: $BACKUP_FILE"
fi
[ -f "$BACKUP_FILE" ] || die "Backup-Datei nicht gefunden: $BACKUP_FILE"
[ -n "$IDENTITY_FILE" ] && [ -f "$IDENTITY_FILE" ] || die "AGE_IDENTITY_FILE (oder --identity) ist erforderlich"
[ -f "$SCHEMA_FILE" ] || die "Schemadatei nicht gefunden: $SCHEMA_FILE"

WORKDIR="$(mktemp -d)"
register_cleanup "rm -rf '$WORKDIR'"
trap run_cleanups EXIT INT TERM

log "Entschluessele $BACKUP_FILE..."
age_decrypt "$IDENTITY_FILE" "$BACKUP_FILE" "$WORKDIR/backup.tar"

log "Entpacke..."
tar -C "$WORKDIR" -xf "$WORKDIR/backup.tar" dump.bin manifest.json
[ -f "$WORKDIR/dump.bin" ] && [ -f "$WORKDIR/manifest.json" ] || die "Archiv unvollstaendig (dump.bin/manifest.json fehlen)"

CN="finanzplaner-restoretest-$$-${RANDOM}"
PORT=$(( (RANDOM % 1000) + 20000 ))

log "Starte isolierte Wegwerf-Datenbank $CN auf Port $PORT..."
docker run -d --name "$CN" -e POSTGRES_PASSWORD=restoretest -p "${PORT}:5432" postgres:15-alpine >/dev/null
register_cleanup "docker rm -f '$CN' >/dev/null 2>&1"

READY=0
for _ in $(seq 1 30); do
  if docker exec "$CN" pg_isready -U postgres >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
[ "$READY" -eq 1 ] || die "Postgres in $CN ist nach 30s nicht bereit geworden"

_throwaway_psql() { docker exec -i "$CN" psql -U postgres -d postgres -v ON_ERROR_STOP=1 "$@"; }

log "Wende auth-Stub an (siehe restoretest/auth_stub.sql)..."
_throwaway_psql < "$BACKUP_LIB_DIR/restoretest/auth_stub.sql"

log "Wende echtes Schema an (supabase/migrations/0001_schema.sql)..."
_throwaway_psql < "$SCHEMA_FILE"

log "Kopiere Dump in den Container und restauriere..."
docker cp "$WORKDIR/dump.bin" "$CN:/tmp/dump.bin"
# MSYS_NO_PATHCONV: /tmp/dump.bin ist ein Container-interner Pfad (siehe
# lib.sh-Kommentar) - unter Git Bash sonst faelschlich zu einem Windows-Pfad
# umgeschrieben, bevor pg_restore ihn zu sehen bekommt.
# --data-only/--disable-triggers: das Backup enthaelt nur Daten (siehe
# backup.sh), das Schema kommt hier bereits aus dem echten Migrationsfile.
MSYS_NO_PATHCONV=1 docker exec "$CN" pg_restore -U postgres -d postgres \
  --no-owner --no-privileges --data-only --disable-triggers /tmp/dump.bin

log "Erzeuge Manifest der wiederhergestellten Datenbank..."
generate_manifest _throwaway_psql "$WORKDIR/actual_manifest.json" "restoretest:$CN"

log "Vergleiche gegen mitgeliefertes Manifest..."
if diff_manifests "$WORKDIR/manifest.json" "$WORKDIR/actual_manifest.json"; then
  log "RESTORE TEST PASSED"
  exit 0
else
  die "RESTORE TEST FAILED - siehe Tabellenbericht oben"
fi
