#!/usr/bin/env bash
# Gemeinsame Hilfsfunktionen fuer backup.sh, restore.sh und restore_test.sh.
# Wird per `source` eingebunden, nicht direkt ausgefuehrt.
#
# Einzige harte Host-Abhaengigkeit ist Docker (siehe README.md) - pg_dump/
# pg_restore/psql laufen immer ueber `docker run postgres:15-alpine ...`
# (exakte Versionsgleichheit mit dem im Repo gepinnten Server-Image, kein
# Host-Install noetig), Verschluesselung ueber ein selbst gebautes Alpine-
# Image mit `age` (siehe Dockerfile.age). Alle Datenuebergaben laufen ueber
# Stdin/Stdout statt Bind-Mounts, wo immer moeglich (pg_dump/pg_restore/psql
# und age sind alle stream-faehig) - das haelt die Skripte auf Linux, macOS
# UND unter Git Bash auf Windows identisch funktionsfaehig, ohne dass jeder
# Docker-Aufruf Host-Pfade als Container-interne Argumente durchreichen muss
# (Git Bash/MSYS wandelt fuehrende "/..."-Argumente sonst automatisch in
# Windows-Pfade um, was Container-interne Pfadargumente zerstoert - verifiziert
# waehrend der Implementierung).

set -uo pipefail

BACKUP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BACKUP_LIB_DIR/../.." && pwd)"
SCHEMA_FILE="$REPO_ROOT/supabase/migrations/0001_schema.sql"

# Global kuratierte Katalogtabellen (kein household_id, keine Haushaltsdaten):
# ihr Inhalt kommt bereits deterministisch aus den `insert ... on conflict do
# update`-Bloecken in 0001_schema.sql selbst - ein Restore wendet IMMER zuerst
# das aktuelle Schema an (siehe backup.sh-Kommentar zu --data-only), das seedet
# diese Tabellen also schon richtig. Ein zusaetzlicher --data-only-Restore
# derselben Zeilen kollidiert mit den Unique-Constraints der Schema-Seeds
# (doppelte Primaerschluessel) und wuerde den Restoretest ohne echten Grund
# scheitern lassen; ihr Checksum unterscheidet sich zudem allein durch
# `created_at` zwischen zwei getrennten Schemaanwendungen, waere also nie
# stabil vergleichbar. Deshalb sowohl aus dem Data-Only-Dump als auch aus dem
# Manifestvergleich ausgenommen - eine Aenderung an diesen Tabellen wird
# stattdessen ueber Code-Review der Migration selbst abgesichert, nicht ueber
# den Backup-/Restore-Zyklus.
GLOBALLY_SEEDED_TABLES=(
  category_taxonomy_versions
  category_group_templates
  category_templates
  contract_provider_templates
  funding_programs
  funding_program_versions
  project_template_versions
)

SCRIPT_TAG="${SCRIPT_TAG:-backup}"

log()  { printf '[%s] %s\n' "$SCRIPT_TAG" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$SCRIPT_TAG" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$SCRIPT_TAG" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "benoetigt '$1', ist aber nicht installiert"
}

# Fuer die wenigen Faelle, in denen ein Bind-Mount unvermeidbar ist (aktuell
# nur age_decrypt und diff_manifests): unter Git Bash muss der Host-Pfad
# explizit in einen Windows-Pfad uebersetzt werden (cygpath), UND die
# automatische MSYS-Pfadkonvertierung fuer die Container-internen
# Pfadargumente muss fuer genau diesen einen Aufruf abgeschaltet werden
# (MSYS_NO_PATHCONV=1) - sonst mangelt Git Bash entweder den Mount-Quellpfad
# oder das Container-interne Zielargument. Auf Linux/macOS ist dies ein No-Op.
host_path_for_mount() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

# --- Cleanup-Registry: LIFO, laeuft immer (auch bei Fehlern/Interrupt) ------

declare -a _CLEANUP_CMDS=()

register_cleanup() { _CLEANUP_CMDS+=("$1"); }

run_cleanups() {
  local idx
  for (( idx=${#_CLEANUP_CMDS[@]}-1; idx>=0; idx-- )); do
    eval "${_CLEANUP_CMDS[idx]}" || true
  done
  _CLEANUP_CMDS=()
}

# --- URL-Hilfsfunktionen -----------------------------------------------------

# DATABASE_URL im Repo nutzt das SQLAlchemy-Praefix "postgresql+psycopg://" -
# pg_dump/pg_restore/psql verstehen nur "postgresql://".
to_libpq_url() {
  printf '%s' "${1/postgresql+psycopg:/postgresql:}"
}

# Passwort in einer Connection-URL fuer Logausgaben maskieren.
redact_url() {
  printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'
}

# --- Postgres-Client-Tools ueber Docker --------------------------------------

# pg_tool <subcommand> [args...] - fuehrt pg_dump/pg_restore/psql in einem
# Wegwerf-Container aus, Ein-/Ausgabedaten ausschliesslich ueber
# Stdin/Stdout/Argumente (kein Bind-Mount noetig). BACKUP_DOCKER_NETWORK
# (optional) haengt den Container in das Compose-Netzwerk ein, falls
# DATABASE_URL auf das "local-db"-Profil zeigt (per Containername statt
# host.docker.internal erreichbar).
pg_tool() {
  local subcmd="$1"; shift
  local -a docker_args=(run --rm -i --add-host=host.docker.internal:host-gateway)
  if [ -n "${BACKUP_DOCKER_NETWORK:-}" ]; then
    docker_args+=(--network "$BACKUP_DOCKER_NETWORK")
  fi
  docker_args+=(postgres:15-alpine "$subcmd" "$@")
  docker "${docker_args[@]}"
}

# --- Verschluesselung (age) ueber ein selbst gebautes Wegwerf-Image ----------

ensure_age_image() {
  if ! docker image inspect finanzplaner-age >/dev/null 2>&1; then
    log "baue finanzplaner-age Image (age/age-keygen)..."
    docker build -t finanzplaner-age -f "$BACKUP_LIB_DIR/Dockerfile.age" "$BACKUP_LIB_DIR" >/dev/null
  fi
}

# age_encrypt <recipient> <in_file> <out_file> - reine Stdin/Stdout-Pipe,
# kein Mount noetig (der Recipient ist ein oeffentlicher Schluessel als
# String, kein Dateipfad).
age_encrypt() {
  local recipient="$1" in_file="$2" out_file="$3"
  ensure_age_image
  docker run --rm -i finanzplaner-age age -r "$recipient" < "$in_file" > "$out_file"
}

# age_decrypt <identity_file> <in_file> <out_file> - der einzige Fall, der
# einen Bind-Mount braucht (die private Identity muss als Datei uebergeben
# werden, age unterstuetzt dafuer kein Stdin) - siehe host_path_for_mount().
age_decrypt() {
  local identity_file="$1" in_file="$2" out_file="$3"
  ensure_age_image
  local identity_dir identity_name
  identity_dir="$(cd "$(dirname "$identity_file")" && pwd)"
  identity_name="$(basename "$identity_file")"
  MSYS_NO_PATHCONV=1 docker run --rm -i \
    -v "$(host_path_for_mount "$identity_dir"):/identity:ro" \
    finanzplaner-age \
    age -d -i "/identity/${identity_name}" < "$in_file" > "$out_file"
}

# --- Manifest: Zeilenzahl + ordnungsunabhaengige Pruefsumme je Tabelle ------
#
# generate_manifest <psql_fn> <out_file> <source_label>
#
# <psql_fn> ist der Name einer Funktion, die psql-Argumente entgegennimmt und
# durchreicht (z.B. "_source_psql" fuer eine echte Quelle ueber pg_tool, oder
# eine Funktion, die "docker exec -i <container> psql -U postgres -d postgres"
# aufruft, fuer die Restoretest-Wegwerfdatenbank) - so teilen sich
# backup.sh/restore.sh und restore_test.sh dieselbe Definition von
# "Manifest".
generate_manifest() {
  local psql_fn="$1" out_file="$2" source_label="$3"

  local exclude_list="" t
  for t in "${GLOBALLY_SEEDED_TABLES[@]}"; do
    [ -n "$exclude_list" ] && exclude_list+=", "
    exclude_list+="'${t}'"
  done

  local -a tables=()
  while IFS= read -r line; do
    [ -n "$line" ] && tables+=("$line")
  done < <("$psql_fn" -Atqc "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' and table_name not in (${exclude_list}) order by table_name")

  local query="" first=1 t
  for t in "${tables[@]}"; do
    if [ "$first" -eq 1 ]; then first=0; else query+=" union all "; fi
    query+="select '${t}'::text as table_name, count(*)::bigint as row_count, coalesce(sum(('x'||substr(md5(tt.*::text),1,15))::bit(60)::bigint),0)::text as checksum from \"public\".\"${t}\" as tt"
  done

  local tables_json="{}"
  if [ -n "$query" ]; then
    tables_json=$("$psql_fn" -Atqc "select coalesce(json_object_agg(table_name, json_build_object('row_count', row_count, 'checksum', checksum)), '{}'::json)::text from ($query) s;")
  fi

  local schema_hash="unknown"
  if [ -f "$SCHEMA_FILE" ]; then
    schema_hash=$(sha256sum "$SCHEMA_FILE" | awk '{print $1}')
  fi

  printf '{\n  "generated_at": "%s",\n  "source": "%s",\n  "schema_sha256": "%s",\n  "tables": %s\n}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$source_label" "$schema_hash" "$tables_json" > "$out_file"
}

# diff_manifests <expected_file> <actual_file> - vergleicht die "tables"-
# Objekte zweier Manifeste Tabelle fuer Tabelle, gibt einen lesbaren Bericht
# aus und liefert einen Exitcode != 0 bei jeder Abweichung. Laeuft ueber ein
# Python-Wegwerf-Image (kein jq/python3 auf dem Host noetig - dieselbe
# "nur Docker" Philosophie wie bei pg_tool/age).
diff_manifests() {
  local expected="$1" actual="$2"
  local expected_dir actual_dir
  expected_dir="$(cd "$(dirname "$expected")" && pwd)"
  actual_dir="$(cd "$(dirname "$actual")" && pwd)"
  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(host_path_for_mount "$BACKUP_LIB_DIR"):/scripts:ro" \
    -v "$(host_path_for_mount "$expected_dir"):/expected:ro" \
    -v "$(host_path_for_mount "$actual_dir"):/actual:ro" \
    python:3.12-alpine \
    python3 /scripts/diff_manifests.py "/expected/$(basename "$expected")" "/actual/$(basename "$actual")"
}
