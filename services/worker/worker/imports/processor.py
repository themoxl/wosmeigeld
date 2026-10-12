"""Import-Verarbeitungslogik (Architekturplan Abschnitt 7.3).

Kanonische Implementierung von `process_import_run` - laeuft ausschliesslich
im Worker-Prozess mit eigener Session/Transaktionskontrolle (siehe
`worker/db.py`), aufgerufen ueber den dramatiq-Actor `worker.actors.process_import`.
Der API-Prozess ruft diese Funktion NICHT mehr synchron auf (siehe
`services/api/app/api/v1/routes/imports.py`, das stattdessen den Actor per
`.send()` einreiht) - deshalb committet diese Funktion selbststaendig, statt
sich auf eine umgebende Request-Transaktion zu verlassen.

Der Commit-Schritt (`commit_import_run`, inkl. Regelengine) bleibt bewusst in
`services/api/app/services/import_service.py`: er ist bereits schnell (kleine,
nutzergewaehlte Zeilenmenge) und wird per expliziter Nutzeraktion ausgeloest,
nicht durch den Upload selbst."""

import json
import logging
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from worker.db import get_session_factory
from worker.imports.canonical import CanonicalTransaction, ImportParseError
from worker.imports.dedup import classify_row, compute_fingerprint
from worker.imports.parsers.base import ImportParser
from worker.imports.parsers.csv_generic import (
    AMOUNT_ALIASES,
    DATE_ALIASES,
    _find_column,
    map_rows,
)
from worker.imports.parsers.mt940 import StatementSummary
from worker.imports.registry import PARSERS, detect_format, parse_file_with_summary
from worker.imports.security import check_file_size, check_row_count

logger = logging.getLogger(__name__)

# Zeilen, die als Beispiel in import_runs.sample_raw wandern. Mehr Zeilen sagen
# nichts zusaetzliches ueber die Spaltenstruktur aus, waeren aber mehr echte
# Kontodaten ausserhalb von import_rows (gleiche Obergrenze wie
# ImportMappingSuggestionRequest.sample_rows).
_SAMPLE_ROW_LIMIT = 20

# Abstand zwischen zwei Lebenszeichen. 200 Zeilen sind auch bei langsamer
# Dublettenerkennung Sekundenbruchteile - kurz genug, dass ein Abbruchwunsch
# nicht minutenlang liegen bleibt, und selten genug, dass die zusaetzliche
# Verbindung nicht je Zeile bemueht wird.
_HEARTBEAT_ROW_INTERVAL = 200

_CANCELLED_MESSAGE = "Vom Nutzer abgebrochen."

_NEEDS_MAPPING_MESSAGE = (
    "Datum und Betrag liessen sich keiner Spalte der Datei zuordnen. "
    "Die Zuordnung lässt sich von Hand oder per KI-Vorschlag korrigieren; "
    "die Datei muss dafür nicht erneut hochgeladen werden."
)

_STALE_MESSAGE = (
    "Die Verarbeitung wurde nie oder nicht zu Ende ausgeführt "
    "(seit über 15 Minuten kein Lebenszeichen des Verarbeitungsdienstes). "
    "Bitte die Datei erneut hochladen."
)


def _check_statement_balances(summary: StatementSummary | None) -> None:
    """Saldenprobe: Anfangssaldo + Buchungen muss den Schlusssaldo ergeben.

    MT940 nennt zu jedem Auszugsblock beide Salden. Geht die Rechnung nicht
    auf, hat der Parser Buchungen verloren oder falsch vorzeichenbehaftet -
    beides waere sonst unsichtbar, weil eine unvollstaendig eingelesene Datei
    genauso aussieht wie eine vollstaendige. Lieber ein fehlgeschlagener Lauf
    mit klarer Meldung als ein Konto, das stillschweigend nicht stimmt."""
    if summary is None or summary.reconciles:
        return
    blocks = summary.unbalanced_blocks
    detail = "; ".join(blocks[:3])
    if len(blocks) > 3:
        detail += f"; ... ({len(blocks) - 3} weitere)"
    raise ImportParseError(
        f"Die Saldenprobe des Kontoauszugs geht nicht auf ({len(blocks)} von "
        f"Bank gemeldete Salden stimmen nicht mit den enthaltenen Buchungen "
        f"ueberein): {detail}. Die Datei wurde nicht uebernommen."
    )


async def _apply_opening_balance(
    session: AsyncSession, account_id: object, summary: StatementSummary | None
) -> None:
    """Uebernimmt den Anfangssaldo des Auszugs als Eroeffnungssaldo des Kontos.

    Nur, solange das Konto noch keinen gepflegten Eroeffnungssaldo hat (Default
    0 ohne Stichtag) und noch keine Buchung traegt. Sonst waere das ein
    stiller Eingriff in Zahlen, die der Nutzer selbst gesetzt hat.

    Ohne diesen Schritt zeigt die App nach dem ersten Import die Summe der
    Bewegungen als Kontostand an - bei einem Halbjahresauszug also z. B.
    -100,00 statt der tatsaechlichen 2.500,00."""
    if account_id is None or summary is None or summary.opening is None:
        return
    await session.execute(
        text(
            "update accounts set opening_balance_minor = :balance, "
            "opening_balance_date = :balance_date, updated_at = now() "
            "where id = :account_id "
            "and opening_balance_minor = 0 and opening_balance_date is null "
            "and not exists (select 1 from transactions where account_id = :account_id)"
        ),
        {
            "balance": summary.opening.balance_minor,
            "balance_date": summary.opening.balance_date,
            "account_id": str(account_id),
        },
    )


def _select_parser(
    file_bytes: bytes, filename: str, forced_format: str | None
) -> ImportParser | None:
    """Derselbe Parser, den `parse_file_with_summary` gleich verwenden wird."""
    if forced_format:
        return next((p for p in PARSERS if p.format_name == forced_format), None)
    detected = detect_format(file_bytes, filename)
    return next((p for p in PARSERS if p.format_name == detected.format), None)


def _read_raw_rows(
    file_bytes: bytes, filename: str, forced_format: str | None, limit: int | None = None
) -> tuple[str | None, list[str], list[dict[str, str]]]:
    """Spaltennamen und Rohwerte der Datei, ohne sie kanonisch zu deuten.

    Der Aufruf laeuft ueber getattr, so wie registry.py es bei
    parse_with_mapping und parse_statement macht: `read_raw_rows` bietet nur
    an, wer ueberhaupt Spalten hat (CSV/XLSX/XLS). Fuer mt940, camt und paypal
    ist ein Spalten-Mapping fachlich sinnlos - sie liefern hier nichts und
    bleiben damit beim bisherigen Verhalten."""
    parser = _select_parser(file_bytes, filename, forced_format)
    if parser is None:
        return None, [], []
    read_raw_rows = getattr(parser, "read_raw_rows", None)
    if read_raw_rows is None:
        return parser.format_name, [], []
    try:
        result: tuple[list[str], list[dict[str, str]]] = read_raw_rows(file_bytes, limit)
    except Exception:
        # Diese Zeilen sind reines Diagnosematerial. Scheitert schon das
        # Rohlesen, soll der Lauf nicht zusaetzlich daran scheitern - das
        # kanonische Parsen liefert gleich die aussagekraeftigere Meldung.
        logger.exception("Rohzeilen konnten nicht gelesen werden (filename=%s)", filename)
        return parser.format_name, [], []
    return parser.format_name, result[0], result[1]


def _needs_column_mapping(
    headers: list[str], column_mapping: Mapping[str, str] | None
) -> bool:
    """Fehlt der Datei die Datums- oder Betragsspalte - der einzige Parse-Fehler,
    aus dem eine korrigierte Zuordnung herausfuehrt?

    Bewusst an denselben Aliastabellen entschieden, die die Parser selbst
    benutzen, und nicht am Wortlaut ihrer Fehlermeldung: eine umformulierte
    Meldung wuerde sonst stillschweigend wieder im Totalausfall 'failed'
    landen. Alles andere - unlesbares Datum in einer Zeile, nicht aufgehende
    Saldenprobe, unbekanntes Format - bleibt ein echter Fehler, gegen den ein
    Mapping nicht hilft."""
    if column_mapping:
        # Ein Mapping-Profil war im Spiel: heilbar ist der Fall, in dem genau
        # dieses Profil ins Leere zeigt, weil der Export seine Spalten
        # umbenannt hat.
        return any(
            not column_mapping.get(field) or column_mapping[field] not in headers
            for field in ("booked_date", "amount")
        )
    return (
        _find_column(headers, DATE_ALIASES) is None
        or _find_column(headers, AMOUNT_ALIASES) is None
    )


async def _heartbeat(import_run_id: UUID) -> bool:
    """Lebenszeichen schreiben und melden, ob der Lauf abgebrochen werden soll.

    Bewusst auf einer EIGENEN, sofort committenden Sitzung. Innerhalb der
    langen Staging-Transaktion waere das Lebenszeichen bis zum Ende des Laufs
    unbestaetigt und damit unsichtbar fuer genau die Haengerkennung, fuer die
    es geschrieben wird. Schlimmer: es haelt die Zeile in import_runs bis zum
    Commit gesperrt, sodass das UPDATE des Abbruch-Endpunkts warten muesste -
    der Pruefpunkt hier bekaeme den Abbruchwunsch nie zu sehen."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "update import_runs set heartbeat_at = now() where id = :id "
                    "returning cancel_requested, status"
                ),
                {"id": str(import_run_id)},
            )
        ).first()
        await session.commit()
    # Ein verschwundener Lauf (waehrend der Verarbeitung geloescht) wird wie ein
    # Abbruch behandelt: weiterzuarbeiten hiesse, Zeilen an einen Lauf zu
    # haengen, den es nicht mehr gibt.
    return row is None or bool(row.cancel_requested) or row.status == "cancelled"


async def _load_staged_raw_rows(
    session: AsyncSession, import_run_id: UUID
) -> list[dict[str, object]]:
    """Die Rohzeilen des Laufs in Dateireihenfolge.

    Die einzige Quelle fuer ein erneutes Einlesen: die hochgeladene Datei wird
    nirgends aufbewahrt, ihre Spalten leben nur noch hier weiter."""
    rows = (
        await session.execute(
            text(
                "select raw_data from import_rows where import_run_id = :id order by row_number"
            ),
            {"id": str(import_run_id)},
        )
    ).all()
    return [dict(row.raw_data) for row in rows if isinstance(row.raw_data, dict)]


def _reparse_rows_from_staging(
    raw_rows: list[dict[str, object]], mapping: dict[str, str]
) -> list[CanonicalTransaction]:
    """Rohzeilen + korrigierte Zuordnung -> kanonische Buchungen.

    Bewusst dieselbe Funktion wie beim ersten Einlesen (map_rows): eine zweite
    Zuordnungslogik neben der der Parser wuerde frueher oder spaeter anders
    runden, anders trennen oder ein anderes Datumsformat annehmen als der
    Erstimport - der Nutzer saehe nach einer Korrektur andere Betraege als
    vorher, ohne dass sich an der Datei etwas geaendert haette."""
    if not raw_rows:
        raise ImportParseError(
            "Zu diesem Lauf sind keine Rohzeilen gespeichert - die Zuordnung kann nicht "
            "erneut angewendet werden. Bitte die Datei erneut hochladen."
        )
    return map_rows(raw_rows, mapping)


async def _stage_raw_rows_for_mapping(
    session: AsyncSession,
    household_id: object,
    import_run_id: UUID,
    detected_format: str | None,
    rows: list[dict[str, str]],
) -> None:
    """Legt die Rohzeilen ohne kanonische Deutung ab und setzt 'needs_mapping'.

    Ohne diese Zeilen waere 'needs_mapping' eine Sackgasse: die korrigierte
    Zuordnung (POST /v1/imports/{id}/reparse) arbeitet ausschliesslich auf
    import_rows.raw_data, weil die hochgeladene Datei bewusst nirgends
    aufbewahrt wird. Sie tragen deshalb status='error' und weder Datum noch
    Betrag - beides entsteht erst, wenn die Zuordnung stimmt."""
    check_row_count(len(rows))
    for idx, row in enumerate(rows, start=1):
        await session.execute(
            text(
                "insert into import_rows (household_id, import_run_id, row_number, "
                "raw_data, status) values (:household_id, :import_run_id, :row_number, "
                ":raw_data, 'error')"
            ),
            {
                "household_id": str(household_id),
                "import_run_id": str(import_run_id),
                "row_number": idx,
                "raw_data": json.dumps(row),
            },
        )
    await session.execute(
        text(
            "update import_runs set status = 'needs_mapping', "
            "detected_format = coalesce(:detected_format, detected_format), "
            "total_rows = :total_rows, new_count = 0, duplicate_count = 0, "
            "transfer_candidate_count = 0, error_count = :total_rows, "
            "error_message = :error_message, heartbeat_at = now() where id = :id"
        ),
        {
            "detected_format": detected_format,
            "total_rows": len(rows),
            "error_message": _NEEDS_MAPPING_MESSAGE,
            "id": str(import_run_id),
        },
    )
    await session.commit()


async def process_import_run(
    session: AsyncSession, import_run_id: UUID, file_bytes: bytes
) -> None:
    """Verarbeitet eine hochgeladene Datei in Staging-Zeilen (status='ready')."""
    # 'analysing' gehoert bewusst in die Liste: bricht der Worker mitten in der
    # Verarbeitung weg, bleibt der Lauf fuer immer in diesem Status stehen -
    # nicht loeschbar (die API sperrt in-flight-Status) und, solange dieser
    # Waechter ihn uebersprang, auch nicht wiederholbar. Eine erneut
    # zugestellte Nachricht lief dann ins Leere und der Nutzer sah einen
    # Fortschritt, der nie endet. Ein abgebrochener oder zum Abbruch
    # vorgemerkter Lauf wird dagegen nie angefasst: sonst entstuenden
    # Staging-Zeilen zu einem Lauf, den der Nutzer gerade weggeklickt hat.
    run_row = (
        await session.execute(
            text(
                "select household_id, account_id, mapping_profile_id, filename, "
                "manual_column_mapping, detected_format "
                "from import_runs where id = :id "
                "and status in ('pending', 'analysing', 'failed') "
                "and not cancel_requested"
            ),
            {"id": str(import_run_id)},
        )
    ).first()
    if run_row is None:
        return

    household_id = run_row.household_id
    account_id = run_row.account_id
    filename = run_row.filename
    manual_mapping = run_row.manual_column_mapping

    forced_format: str | None = None
    column_mapping: dict[str, str] | None = None
    if run_row.mapping_profile_id is not None:
        profile_row = (
            await session.execute(
                text(
                    "select bank_format, column_mapping from import_mapping_profiles "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(run_row.mapping_profile_id), "household_id": str(household_id)},
            )
        ).first()
        if profile_row is not None:
            forced_format = profile_row.bank_format
            column_mapping = profile_row.column_mapping

    # started_at/heartbeat_at ab der ersten Sekunde: ohne Lebenszeichen laesst
    # sich ein Lauf, den niemand mehr abholt, nicht von einem unterscheiden,
    # der gerade arbeitet (siehe fail_stale_import_runs).
    await session.execute(
        text(
            "update import_runs set status = 'analysing', started_at = now(), "
            "heartbeat_at = now() where id = :id"
        ),
        {"id": str(import_run_id)},
    )
    await session.commit()

    raw_format: str | None = None
    raw_headers: list[str] = []
    try:
        if manual_mapping:
            # Erneutes Einlesen mit korrigierter Zuordnung. Die Datei ist hier
            # NICHT verfuegbar (der Upload schickt sie einmalig durch den
            # Broker, aufbewahrt wird sie bewusst nirgends) - die Rohzeilen
            # stehen stattdessen in import_rows.raw_data, wo sie der erste Lauf
            # abgelegt hat. Deshalb steht dieser Zweig VOR check_file_size:
            # der Reparse-Aufruf stellt einen leeren Dateiinhalt zu, an dem die
            # Groessenpruefung sonst mit "Datei ist leer." abbraeche.
            detected_format = run_row.detected_format
            parsed_txs = _reparse_rows_from_staging(
                await _load_staged_raw_rows(session, import_run_id), manual_mapping
            )
            summary = None
            check_row_count(len(parsed_txs))
            # Die alten Zeilen erst jetzt loeschen - waere das vor dem Parsen
            # passiert, haette ein unbrauchbares Mapping die einzige Quelle der
            # Rohzeilen vernichtet und den Lauf endgueltig verloren.
            await session.execute(
                text("delete from import_rows where import_run_id = :id"),
                {"id": str(import_run_id)},
            )
        else:
            check_file_size(file_bytes)

            # Kopfzeile und Beispielzeilen VOR dem kanonischen Parsen. Scheitert
            # das Parsen, ist das hier das Einzige, was Mappingdialog und
            # KI-Spaltenvorschlag zu sehen bekommen - import_rows gibt es zu
            # diesem Zeitpunkt noch nicht.
            raw_format, raw_headers, raw_sample = _read_raw_rows(
                file_bytes, filename, forced_format, limit=_SAMPLE_ROW_LIMIT
            )
            if raw_headers:
                await session.execute(
                    text(
                        "update import_runs set detected_columns = cast(:columns as jsonb), "
                        "sample_raw = cast(:sample as jsonb) where id = :id"
                    ),
                    {
                        "columns": json.dumps(raw_headers),
                        "sample": json.dumps(raw_sample),
                        "id": str(import_run_id),
                    },
                )
                await session.commit()

            detected_format, parsed_txs, summary = parse_file_with_summary(
                file_bytes, filename, forced_format=forced_format, column_mapping=column_mapping
            )
        check_row_count(len(parsed_txs))
        _check_statement_balances(summary)
        # Vor der Dublettenerkennung, die gleich transactions liest: der
        # Eroeffnungssaldo wird nur gesetzt, solange das Konto leer ist.
        await _apply_opening_balance(session, account_id, summary)

        target_acc_id = str(account_id) if account_id else str(household_id)

        existing_txs = []
        if account_id is not None:
            # payee_label mitlesen: Stufe 4 der Dublettenerkennung vergleicht
            # den Empfaenger (siehe dedup.classify_row). Ersatzweise der Name
            # aus payees - bei Buchungen, die ueber eine Regel einem Empfaenger
            # zugeordnet wurden, steht das Label nicht zwingend gefuellt da.
            #
            # purpose_raw ebenso: ohne ihn verglich die Erkennung den
            # Verwendungszweck ueberhaupt nicht und uebersah jede Dublette, bei
            # der die Schreibweise des Empfaengers abweicht (Stufe 3).
            res = await session.execute(
                text(
                    "select t.id, t.booked_date, t.amount_minor, t.external_reference, "
                    "t.fingerprint, coalesce(t.payee_label, p.name) as payee, "
                    "t.purpose_raw as purpose "
                    "from transactions t "
                    "left join payees p on p.id = t.payee_id "
                    "where t.account_id = :account_id and not t.is_archived"
                ),
                {"account_id": str(account_id)},
            )
            existing_txs = [
                {
                    "id": str(row.id),
                    "booked_date": row.booked_date,
                    "amount_minor": row.amount_minor,
                    "external_reference": row.external_reference,
                    "fingerprint": row.fingerprint,
                    "payee": row.payee,
                    "purpose": row.purpose,
                }
                for row in res
            ]

        other_txs = []
        other_params: dict[str, object] = {"household_id": str(household_id)}
        if account_id:
            other_params["account_id"] = str(account_id)
        res_oth = await session.execute(
            text(
                "select id, booked_date, amount_minor, is_transfer from transactions "
                "where household_id = :household_id "
                + ("and account_id <> :account_id " if account_id else "")
                + "and not is_archived"
            ),
            other_params,
        )
        other_txs = [
            {
                "id": str(row.id),
                "booked_date": row.booked_date,
                "amount_minor": row.amount_minor,
                "is_transfer": row.is_transfer,
            }
            for row in res_oth
        ]

        new_count = 0
        duplicate_count = 0
        transfer_candidate_count = 0
        error_count = 0
        cancelled = False

        for idx, tx in enumerate(parsed_txs, start=1):
            # Der Pruefpunkt, auf den sich der Abbruch-Endpunkt verlaesst: er
            # setzt nur cancel_requested und darf den laufenden Import nicht
            # von aussen abschiessen.
            if idx % _HEARTBEAT_ROW_INTERVAL == 0 and await _heartbeat(import_run_id):
                cancelled = True
                break

            fp = compute_fingerprint(
                target_acc_id, tx.booked_date, tx.amount_minor, tx.payee_raw, tx.purpose
            )
            c_res = classify_row(tx, fp, target_acc_id, existing_txs, other_txs)
            st = c_res.status
            dup_id = c_res.duplicate_of_transaction_id

            if st == "new":
                new_count += 1
            elif st in ("duplicate_exact", "duplicate_possible"):
                duplicate_count += 1
            elif st == "transfer_candidate":
                transfer_candidate_count += 1

            await session.execute(
                text(
                    "insert into import_rows (household_id, import_run_id, row_number, "
                    "raw_data, external_reference, booked_date, value_date, amount_minor, "
                    "currency, payee_raw, purpose, bank_transaction_code, fingerprint, "
                    "status, duplicate_of_transaction_id) "
                    "values (:household_id, :import_run_id, :row_number, :raw_data, "
                    ":external_reference, :booked_date, :value_date, :amount_minor, "
                    ":currency, :payee_raw, :purpose, :bank_transaction_code, :fingerprint, "
                    ":status, :duplicate_of_transaction_id)"
                ),
                {
                    "household_id": str(household_id),
                    "import_run_id": str(import_run_id),
                    "row_number": idx,
                    "raw_data": json.dumps(tx.raw),
                    "external_reference": tx.external_reference,
                    "booked_date": tx.booked_date,
                    "value_date": tx.value_date,
                    "amount_minor": tx.amount_minor,
                    "currency": tx.currency,
                    "payee_raw": tx.payee_raw,
                    "purpose": tx.purpose,
                    "bank_transaction_code": tx.bank_transaction_code,
                    "fingerprint": fp,
                    "status": st,
                    "duplicate_of_transaction_id": str(dup_id) if dup_id else None,
                },
            )

        if cancelled:
            # Rohzeilen verwerfen: die bisher gestagten Zeilen sind nie
            # committet worden, ein Rollback nimmt sie restlos zurueck -
            # zusammen mit einem eventuell gesetzten Eroeffnungssaldo.
            await session.rollback()
            await session.execute(
                text(
                    "update import_runs set status = 'cancelled', error_message = :err, "
                    "heartbeat_at = now() where id = :id"
                ),
                {"err": _CANCELLED_MESSAGE, "id": str(import_run_id)},
            )
            await session.commit()
            return

        # manual_column_mapping wird geleert: es ist ein einmaliger Auftrag,
        # kein Dauerzustand. Bliebe es stehen, liefe jede spaetere erneute
        # Zustellung derselben Nachricht wieder in den Reparse-Zweig - und
        # zwar auf Rohzeilen, die inzwischen aus genau diesem Mapping
        # entstanden sind. error_message faellt mit weg, sonst behauptet ein
        # geheilter Lauf weiter den Fehler, an dem er einmal gescheitert ist.
        await session.execute(
            text(
                "update import_runs set detected_format = :detected_format, "
                "status = 'ready', total_rows = :total_rows, new_count = :new_count, "
                "duplicate_count = :duplicate_count, "
                "transfer_candidate_count = :transfer_candidate_count, "
                "error_count = :error_count, heartbeat_at = now(), "
                "manual_column_mapping = null, error_message = null "
                "where id = :id"
            ),
            {
                "detected_format": detected_format,
                "total_rows": len(parsed_txs),
                "new_count": new_count,
                "duplicate_count": duplicate_count,
                "transfer_candidate_count": transfer_candidate_count,
                "error_count": error_count,
                "id": str(import_run_id),
            },
        )
        await session.commit()

    except Exception as exc:
        await session.rollback()

        # Nicht zuzuordnende Datums-/Betragsspalte ist kein Totalausfall: die
        # Spaltennamen der Datei sind bekannt und genau hier gebraucht.
        raw_rows: list[dict[str, str]] = []
        if (
            raw_headers
            and isinstance(exc, ImportParseError)
            and _needs_column_mapping(raw_headers, column_mapping)
        ):
            raw_rows = _read_raw_rows(file_bytes, filename, forced_format)[2]
        if raw_rows:
            try:
                await _stage_raw_rows_for_mapping(
                    session, household_id, import_run_id, raw_format, raw_rows
                )
                logger.info(
                    "Import braucht eine Spaltenzuordnung (import_run_id=%s): %s",
                    import_run_id,
                    exc,
                )
                return
            except Exception:
                # Bleiben die Rohzeilen aus, waere 'needs_mapping' eine
                # Sackgasse - dann lieber der ehrliche Fehlerstatus.
                logger.exception(
                    "Rohzeilen konnten nicht abgelegt werden (import_run_id=%s)", import_run_id
                )
                await session.rollback()

        # Vorher wurde ein fehlgeschlagener Import nur als DB-Status
        # geschrieben, nirgends geloggt - ohne diese Zeile taucht ein
        # kaputter Import in den Worker-Logs ueberhaupt nicht auf.
        logger.exception("Import fehlgeschlagen (import_run_id=%s)", import_run_id)
        await session.execute(
            text(
                "update import_runs set status = 'failed', error_message = :err "
                "where id = :id"
            ),
            {"err": str(exc), "id": str(import_run_id)},
        )
        await session.commit()


async def fail_stale_import_runs() -> list[UUID]:
    """Beendet Importlaeufe, die kein Lebenszeichen mehr senden.

    Gegenstueck zu `_heartbeat`: ein Lauf, dessen Worker weggebrochen ist,
    bliebe sonst dauerhaft auf 'pending' oder 'analysing' stehen. 15 Minuten
    liegen weit ueber jedem realen Lauf - der Actor process_import gibt
    ohnehin nach 5 Minuten auf -, also raeumt diese Grenze nichts ab, was noch
    arbeitet.

    Eigene Sitzung, weil der Aufrufer ein periodischer Actor ohne
    Request-Kontext ist (gleiches Muster wie worker/ai/task_orchestrator.py)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "update import_runs set status = 'failed', error_message = :err "
                    "where status in ('pending', 'analysing') "
                    "and coalesce(heartbeat_at, started_at, created_at) "
                    "< now() - interval '15 minutes' "
                    "returning id"
                ),
                {"err": _STALE_MESSAGE},
            )
        ).all()
        await session.commit()
    return [UUID(str(row.id)) for row in rows]
