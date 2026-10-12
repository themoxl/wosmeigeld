"""Vergleicht zwei Backup-Manifeste (siehe lib.sh: generate_manifest) Tabelle
fuer Tabelle. Wird in einem python:3.12-alpine-Wegwerfcontainer ausgefuehrt,
damit weder der Host noch die Backup-Skripte selbst eine Python-Abhaengigkeit
brauchen. Exitcode 0 nur, wenn jede Tabelle in beiden Manifesten mit
identischer Zeilenzahl und Pruefsumme vorkommt.
"""

import json
import sys


def main() -> int:
    expected_path, actual_path = sys.argv[1], sys.argv[2]

    with open(expected_path, encoding="utf-8") as f:
        expected = json.load(f)
    with open(actual_path, encoding="utf-8") as f:
        actual = json.load(f)

    expected_tables = expected.get("tables", {})
    actual_tables = actual.get("tables", {})

    ok = True
    for name in sorted(set(expected_tables) | set(actual_tables)):
        exp = expected_tables.get(name)
        act = actual_tables.get(name)
        if exp is None:
            print(f"FAIL  {name}: unerwartete Tabelle nach Restore")
            ok = False
        elif act is None:
            print(f"FAIL  {name}: fehlt nach Restore")
            ok = False
        elif exp != act:
            print(
                f"FAIL  {name}: Abweichung (erwartet {exp['row_count']} Zeilen/"
                f"{exp['checksum']}, erhalten {act['row_count']} Zeilen/{act['checksum']})"
            )
            ok = False
        else:
            print(f"OK    {name}: {act['row_count']} Zeilen")

    if expected.get("schema_sha256") != actual.get("schema_sha256"):
        print(
            "WARN  schema_sha256 weicht ab - Schema wurde nach dem Backup "
            "geaendert (Restoretest nutzt immer die aktuelle Schemadatei)."
        )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
