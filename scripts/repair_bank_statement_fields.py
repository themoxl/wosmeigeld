"""Zieht bereits uebernommene Bankbuchungen auf den korrigierten Parser nach.

Bis zur Ueberarbeitung von services/worker/worker/imports/parsers/mt940.py
speicherte der Import mehrere Felder falsch:

* `payee_raw` endete nach 27 Zeichen (das Subfeld ?33 wurde nicht angehaengt),
  bei Kartenauszuegen fehlte der Empfaenger ganz,
* `purpose_raw` enthielt das rohe Feld :86: samt '?NN'-Markern, mit mitten
  durchgebrochenen Woertern und IBANs,
* `external_reference` trug Platzhalter wie 'KREF+' oder abgeschnittene Werte,
* `value_date` blieb leer, und `booked_date` bekam das Valuta- statt des
  Buchungsdatums,
* `bank_transaction_code` trug den SWIFT-Schluessel statt des
  Geschaeftsvorfallcodes.

Dieses Skript liest die urspruenglichen Dateien erneut ein und aktualisiert
NUR diese Felder. Kategorien, Splits, Vertraege, Budgets und jede sonstige
Zuordnung bleiben unberuehrt - im Unterschied zu Loeschen-und-neu-Importieren,
das die gesamte Kategorisierungsarbeit mitnimmt.

Zugeordnet wird ueber `import_rows`: jede Staging-Zeile kennt ihre Buchung
(`committed_transaction_id`). Bei MT940 stimmt die Reihenfolge (`row_number`)
mit dem neuen Parse ueberein, bei XLSX wird ueber die Referenznummer aus
`raw_data` zugeordnet, weil dort eine vorgemerkte Zeile entfaellt.

    python scripts/repair_bank_statement_fields.py --household <uuid> \\
        --file <import_run_id>=<pfad> [--file ...]
    python scripts/repair_bank_statement_fields.py ... --apply

Ohne --apply passiert nichts; das Skript zeigt nur, was es aendern wuerde.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import namedtuple_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "worker"))

from worker.imports.dedup import compute_fingerprint  # noqa: E402
from worker.imports.registry import parse_file_with_summary  # noqa: E402

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"

# Felder, die dieses Skript anfasst - bewusst eine feste Liste.
FELDER = (
    "payee_raw",
    "purpose_raw",
    "external_reference",
    "value_date",
    "booked_date",
    "bank_transaction_code",
    "fingerprint",
)


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN).replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _referenz(raw: Any) -> str | None:
    """Referenznummer aus dem gespeicherten Rohdatensatz einer XLSX-Zeile."""
    if not isinstance(raw, dict):
        return None
    for key in ("Referenznummer", "referenznummer", "Referenz"):
        if raw.get(key):
            return str(raw[key]).strip()
    return None


def _zuordnen(
    staging: list[Any], geparst: list[Any], format_name: str
) -> tuple[list[tuple[Any, Any]], list[Any]]:
    """Liefert (Paare, uebrig gebliebene Staging-Zeilen).

    MT940: Reihenfolge, abgesichert ueber den Betrag. XLSX: Referenznummer.
    """
    if format_name == "mt940" and len(staging) == len(geparst):
        paare = list(zip(staging, geparst, strict=True))
        if all(s.amount_minor == g.amount_minor for s, g in paare):
            return paare, []
        raise SystemExit(
            "Betraege stimmen in der Reihenfolge nicht ueberein - Zuordnung "
            "waere geraten. Abbruch, es wurde nichts geaendert."
        )

    nach_referenz = {
        g.external_reference: g for g in geparst if g.external_reference
    }
    paare = []
    uebrig = []
    for zeile in staging:
        referenz = _referenz(zeile.raw_data)
        treffer = nach_referenz.get(referenz) if referenz else None
        if treffer is not None and treffer.amount_minor == zeile.amount_minor:
            paare.append((zeile, treffer))
        else:
            uebrig.append(zeile)
    return paare, uebrig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--household", required=True, help="UUID des Haushalts")
    parser.add_argument(
        "--file",
        action="append",
        required=True,
        metavar="RUN_ID=PFAD",
        help="Importlauf und zugehoerige Quelldatei (mehrfach angebbar)",
    )
    parser.add_argument("--apply", action="store_true", help="Aenderungen schreiben")
    args = parser.parse_args()

    zuordnung = {}
    for eintrag in args.file:
        run_id, _, pfad = eintrag.partition("=")
        if not pfad:
            raise SystemExit(f"--file erwartet RUN_ID=PFAD, bekam: {eintrag!r}")
        zuordnung[run_id] = Path(pfad)

    with psycopg.connect(_dsn(), autocommit=False) as con, con.cursor() as cur:
        gesamt_geaendert = 0
        gesamt_archiviert = 0

        for run_id, pfad in zuordnung.items():
            cur.execute(
                "select filename, detected_format, account_id from import_runs "
                "where id = %s and household_id = %s",
                (run_id, args.household),
            )
            lauf = cur.fetchone()
            if lauf is None:
                raise SystemExit(f"Importlauf {run_id} nicht im Haushalt gefunden.")
            filename, format_name, account_id = lauf

            with con.cursor(row_factory=namedtuple_row) as zeilen_cur:
                zeilen_cur.execute(
                    "select id, row_number, amount_minor, raw_data, "
                    "committed_transaction_id, payee_raw, purpose, external_reference, "
                    "booked_date "
                    "from import_rows where import_run_id = %s order by row_number",
                    (run_id,),
                )
                staging = zeilen_cur.fetchall()

            _, geparst, summary = parse_file_with_summary(pfad.read_bytes(), filename)
            if summary is not None and not summary.reconciles:
                raise SystemExit(
                    f"{filename}: Saldenprobe geht nicht auf - Datei passt nicht "
                    "zum Bestand. Abbruch."
                )

            paare, uebrig = _zuordnen(staging, geparst, format_name)
            print(f"\n=== {filename} ({format_name}) ===")
            print(f"  Staging-Zeilen {len(staging)}, neuer Parse {len(geparst)}, "
                  f"zugeordnet {len(paare)}, ohne Entsprechung {len(uebrig)}")

            geaendert = 0
            for zeile, neu in paare:
                if zeile.committed_transaction_id is None:
                    continue
                fingerprint = compute_fingerprint(
                    str(account_id), neu.booked_date, neu.amount_minor,
                    neu.payee_raw, neu.purpose,
                )
                aenderung = (
                    zeile.payee_raw != neu.payee_raw
                    or zeile.purpose != neu.purpose
                    or zeile.external_reference != neu.external_reference
                    or zeile.booked_date != neu.booked_date
                )
                if not aenderung:
                    continue
                geaendert += 1
                if not args.apply:
                    continue

                # note und payee_label nur nachziehen, solange sie noch das
                # sind, was der Import damals hineingeschrieben hat - eine vom
                # Nutzer oder einer Regel gesetzte Notiz bleibt unangetastet.
                cur.execute(
                    """
                    update transactions set
                        payee_raw = %(payee_raw)s,
                        purpose_raw = %(purpose)s,
                        external_reference = %(external_reference)s,
                        value_date = %(value_date)s,
                        booked_date = %(booked_date)s,
                        bank_transaction_code = %(code)s,
                        fingerprint = %(fingerprint)s,
                        note = case when note is not distinct from %(alt_purpose)s
                                    then %(purpose)s else note end,
                        payee_label = case
                            when payee_label is not distinct from %(alt_label)s
                            then left(coalesce(%(payee_raw)s, ''), 160)
                            else payee_label end,
                        updated_at = now()
                    where id = %(tx)s and household_id = %(household)s
                    """,
                    {
                        "payee_raw": neu.payee_raw,
                        "purpose": neu.purpose,
                        "external_reference": neu.external_reference,
                        "value_date": neu.value_date,
                        "booked_date": neu.booked_date,
                        "code": neu.bank_transaction_code,
                        "fingerprint": fingerprint,
                        "alt_purpose": zeile.purpose,
                        "alt_label": (zeile.payee_raw or "")[:160] or None,
                        "tx": zeile.committed_transaction_id,
                        "household": args.household,
                    },
                )
                cur.execute(
                    """
                    update import_rows set
                        payee_raw = %(payee_raw)s, purpose = %(purpose)s,
                        external_reference = %(external_reference)s,
                        value_date = %(value_date)s, booked_date = %(booked_date)s,
                        bank_transaction_code = %(code)s, fingerprint = %(fingerprint)s,
                        raw_data = %(raw)s
                    where id = %(row)s
                    """,
                    {
                        "payee_raw": neu.payee_raw,
                        "purpose": neu.purpose,
                        "external_reference": neu.external_reference,
                        "value_date": neu.value_date,
                        "booked_date": neu.booked_date,
                        "code": neu.bank_transaction_code,
                        "fingerprint": fingerprint,
                        "raw": json.dumps(neu.raw, default=str),
                        "row": zeile.id,
                    },
                )

            print(f"  Buchungen mit geaenderten Feldern: {geaendert}")
            gesamt_geaendert += geaendert

            # Zeilen ohne Entsprechung sind die vorgemerkten Kartenumsaetze,
            # die der Parser heute ueberspringt. Sie werden archiviert, nicht
            # geloescht: eine Buchung verschwinden zu lassen, an der Splits und
            # womoeglich eine Kategorie haengen, waere nicht rueckholbar.
            for zeile in uebrig:
                print(f"  ohne Entsprechung (wird archiviert): "
                      f"{zeile.booked_date} {zeile.amount_minor / 100:.2f} "
                      f"{str(zeile.purpose)[:40]}")
                gesamt_archiviert += 1
                if args.apply and zeile.committed_transaction_id:
                    cur.execute(
                        "update transactions set is_archived = true, updated_at = now() "
                        "where id = %s and household_id = %s",
                        (zeile.committed_transaction_id, args.household),
                    )

        print(f"\nGesamt: {gesamt_geaendert} Buchungen zu aktualisieren, "
              f"{gesamt_archiviert} zu archivieren")
        if args.apply:
            con.commit()
            print("Geschrieben.")
        else:
            con.rollback()
            print("Trockenlauf - es wurde nichts geaendert. Zum Ausfuehren: --apply")

    return 0


if __name__ == "__main__":
    sys.exit(main())
