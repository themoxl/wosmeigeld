"""Import-Commit-, Verwaltungs- und Review-Logik fuer die API.

Der Parse-/Staging-Schritt (`process_import_run`) laeuft seit der
Einfuehrung echter asynchroner Verarbeitung kanonisch im Worker-Prozess
(`services/worker/worker/imports/processor.py`, aufgerufen ueber den
dramatiq-Actor `worker.actors.process_import`) - hier nicht mehr dupliziert."""

import json
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import (
    category_service,
    payee_link_service,
    provenance_service,
    reimbursement_mark_service,
)
from app.services.audit_service import record_audit_event
from app.services.rules_service import (
    match_reimbursement_rule_for_transaction,
    match_rules_for_transaction,
)
from app.services.transaction_detach_service import (
    as_labelled,
    detach_transactions,
    existing_tables,
)


async def _resolve_rule_category(
    session: AsyncSession, household_id: UUID, category_id: UUID | None
) -> UUID | None:
    """Kategorie aus einem Regeltreffer aufloesen, ohne den Import abzubrechen.

    Anders als bei einer ausdruecklichen Nutzerwahl ist eine ungueltig
    gewordene Regelkategorie kein Eingabefehler des Aufrufers, sondern
    Altbestand. Die Zeile wird dann bewusst ohne Kategorie importiert und
    taucht in der Review-Inbox auf, statt den gesamten Importlauf zu stoppen."""
    if category_id is None:
        return None
    try:
        return await category_service.resolve_category_id(session, household_id, category_id)
    except HTTPException:
        return None


# Entitaetstyp im audit_log fuer Importlaeufe (siehe audit_service).
IMPORT_RUN_ENTITY_TYPE = "import_run"

# Statuswerte, in denen der Worker die Zeile gerade selbst anfasst (Parsen bzw.
# Uebernahme laeuft). Aenderungen und Loeschungen von aussen waeren hier ein
# Rennen gegen den Worker - sie werden bewusst abgelehnt statt "meistens" zu
# funktionieren.
_IN_FLIGHT_STATUSES = frozenset({"pending", "analysing", "committing"})

# Die Teilmenge davon, fuer die der WORKER zustaendig ist und von der deshalb
# ein Lebenszeichen (heartbeat_at) zu erwarten ist. Zwei Stellen bauen darauf
# auf: nur diese Laeufe lassen sich abbrechen, und nur bei ihnen bedeutet ein
# ausbleibendes Lebenszeichen "verwaist". 'committing' gehoert nicht dazu - das
# macht die API selbst in einer Transaktion, ganz ohne Lebenszeichen.
_WORKER_OWNED_STATUSES = frozenset({"pending", "analysing"})

# Statuswerte, aus denen ein erneutes Einlesen mit korrigierter Zuordnung
# herausfuehrt. 'needs_mapping' ist der Regelfall, 'failed' der Nachzuegler
# (dort gibt es Rohzeilen nur, wenn sie vor dem Fehler gestaged wurden), und
# 'ready' erlaubt die Korrektur eines Laufs, der zwar Zeilen hat, aber
# erkennbar die falschen Spalten getroffen hat.
REPARSABLE_STATUSES = frozenset({"ready", "needs_mapping", "failed"})

# Ab wann gilt ein Lauf ohne Lebenszeichen als haengend? Dieselbe Rolle wie
# _STALE_AFTER_MINUTES bei den KI-Aufgaben, aber deutlich kuerzer: ein Import
# schreibt seine Staging-Zeilen in einem Zug, er wartet auf kein Modell.
STALE_RUN_AFTER_MINUTES = 15

# Berechnete Spalte fuer die Laufzeile: in Arbeit, aber ohne Lebenszeichen.
# Muster wie ai_tasks (routes/ai_tasks.py) - der Aufrufer muss
# `stale_minutes` binden.
RUN_IS_STALE_EXPRESSION = (
    "(status in ('pending', 'analysing') "
    "and coalesce(heartbeat_at, started_at, created_at) "
    "< now() - make_interval(mins => :stale_minutes)) as is_stale"
)

# Wie viel aus dem Lauf bereits in den echten Bestand uebernommen wurde -
# dieselbe Regel, nach der delete_import_run das Entfernen verweigert (siehe
# count_committed_transactions, das diesen Ausdruck ebenfalls benutzt).
#
# Warum das in der Laufzeile mitgeliefert wird: der Status allein verraet es
# nicht. Ein Teil-Commit laesst den Lauf auf 'ready' stehen, weil noch offene
# Dubletten uebrig sind - die Oberflaeche zeigte dann "Bereit zur Uebernahme"
# samt Entfernen-Knopf, und der Server lehnte den Klick anschliessend ab. Mit
# dieser Zahl bietet die Oberflaeche von vornherein die Aktion an, die
# tatsaechlich moeglich ist.
RUN_COMMITTED_COUNT_EXPRESSION = (
    "greatest("
    "(select count(*) from transactions t "
    " where t.import_run_id = import_runs.id "
    " and t.household_id = import_runs.household_id), "
    "(select count(*) from import_rows r "
    " where r.import_run_id = import_runs.id and r.status = 'committed')"
    ") as committed_count"
)

# Wie viele der entstandenen Buchungen noch im Bestand stehen. Bewusst getrennt
# von committed_count: nach einem Zuruecknehmen bleibt committed_count stehen
# (das Entfernen bleibt gesperrt), diese Zahl faellt auf 0 - und genau daran
# haengt, ob ein Zuruecknehmen ueberhaupt noch etwas bewirken wuerde.
RUN_ACTIVE_TRANSACTION_COUNT_EXPRESSION = (
    "(select count(*) from transactions t "
    " where t.import_run_id = import_runs.id "
    " and t.household_id = import_runs.household_id "
    " and not t.is_archived) as active_transaction_count"
)

# Spalten, die als Schnappschuss ins audit_log gehen und gleichzeitig die
# vollstaendige Laufzeile fuer die HTTP-Antwort liefern.
_RUN_SNAPSHOT_COLUMNS = (
    "id, account_id, mapping_profile_id, filename, file_hash, file_size_bytes, "
    "detected_format, status, total_rows, new_count, duplicate_count, "
    "transfer_candidate_count, error_count, error_message, started_by, "
    "created_at, started_at, heartbeat_at, cancel_requested, detected_columns, "
    "sample_raw, manual_column_mapping, completed_at"
)

# Felder, die PATCH /v1/imports/{run_id} setzen darf. Bewusst eine
# Allowlist: der SET-Teil der UPDATE-Anweisung wird aus diesen Namen gebaut.
UPDATABLE_RUN_FIELDS: tuple[str, ...] = ("account_id", "mapping_profile_id")

# ---------------------------------------------------------------------------
# Fachliche Fehler rund um einen Importlauf
#
# Frueher warf commit_import_run EINEN nackten ValueError fuer drei voellig
# verschiedene Faelle ("nicht gefunden, nicht bereit oder kein Konto
# zugewiesen"). Er wurde nirgends abgefangen und schlug als HTTP 500 durch -
# im Web las der Nutzer daraufhin "Die API ist nicht erreichbar", obwohl die
# API lief und in Wahrheit nur das Zielkonto fehlte. Die Faelle sind jetzt
# getrennte Typen, die die Route auf 404 / 409 / 400 abbildet.
# ---------------------------------------------------------------------------


class ImportRunError(Exception):
    """Basisklasse: die gewuenschte Operation auf dem Importlauf ist fachlich
    nicht moeglich. Traegt eine bereits nutzertaugliche deutsche Meldung
    (str(exc)) - die Route reicht sie unveraendert als HTTP-detail weiter."""


class ImportRunNotFoundError(ImportRunError):
    """Kein Importlauf mit dieser ID im aktuellen Haushalt (auch der Fall
    'gehoert einem anderen Haushalt' - bewusst nicht unterscheidbar, sonst
    waere die Existenz fremder Laeufe abfragbar)."""


class ImportRunNotReadyError(ImportRunError):
    """Der Lauf existiert, ist aber nicht im Status 'ready' (z. B. noch in
    Verarbeitung, bereits uebernommen oder fehlgeschlagen). `actual_status`
    haelt den tatsaechlichen Status fuer die HTTP-Antwort fest."""

    def __init__(self, actual_status: str) -> None:
        self.actual_status = actual_status
        hint = _NOT_READY_HINTS.get(actual_status, "")
        super().__init__(
            f"Importlauf kann nicht uebernommen werden: Status ist '{actual_status}', "
            f"erwartet wird 'ready'.{hint}"
        )


class ImportRunAccountMissingError(ImportRunError):
    """Der Lauf ist bereit, hat aber kein Zielkonto - genau der Fall, den der
    Nutzer live getroffen hat.

    Die Meldung unterscheidet die beiden Ursachen, weil die naechste Handlung
    eine voellig andere ist: existiert im Haushalt bereits ein Konto, fehlt nur
    die Zuweisung (die seit PATCH /v1/imports/{run_id} nachtraeglich moeglich
    ist); existiert noch gar keines, muss zuerst ein Konto angelegt werden."""

    def __init__(self, *, household_has_accounts: bool) -> None:
        self.household_has_accounts = household_has_accounts
        if household_has_accounts:
            detail = (
                "Bitte weise dem Importlauf ein Zielkonto zu - das geht nachtraeglich, "
                "ein erneuter Upload ist dafuer nicht noetig."
            )
        else:
            detail = (
                "In diesem Haushalt existiert ausserdem noch kein Konto, in das die "
                "Buchungen geschrieben werden koennten. Bitte lege zuerst ein Konto an "
                "und weise dem Importlauf danach dieses Zielkonto zu - ein erneuter "
                "Upload ist dafuer nicht noetig."
            )
        super().__init__(f"Dem Importlauf ist kein Zielkonto zugewiesen. {detail}")


class ImportRunNotEditableError(ImportRunError):
    """Der Lauf laesst sich nicht mehr aendern, weil sein Status das nicht mehr
    hergibt (uebernommen, fehlgeschlagen oder gerade in Verarbeitung)."""

    def __init__(self, actual_status: str) -> None:
        self.actual_status = actual_status
        hint = _NOT_EDITABLE_HINTS.get(actual_status, "")
        super().__init__(
            f"Der Importlauf kann nicht mehr geaendert werden: Status ist "
            f"'{actual_status}', aenderbar ist nur ein Lauf im Status 'ready'.{hint}"
        )


class ImportRunAccountUnknownError(ImportRunError):
    """Das angegebene Zielkonto existiert nicht in diesem Haushalt. Bewusst
    nicht unterscheidbar von "gehoert einem fremden Haushalt"."""

    def __init__(self) -> None:
        super().__init__(
            "Das angegebene Zielkonto gehoert nicht zu diesem Haushalt oder existiert "
            "nicht."
        )


class ImportRunMappingProfileUnknownError(ImportRunError):
    """Das angegebene Mapping-Profil existiert nicht in diesem Haushalt."""

    def __init__(self) -> None:
        super().__init__(
            "Das angegebene Mapping-Profil gehoert nicht zu diesem Haushalt oder "
            "existiert nicht."
        )


class ImportRunInProgressError(ImportRunError):
    """Der Lauf wird gerade vom Worker verarbeitet. Ihn jetzt zu loeschen waere
    ein Rennen: der Worker schreibt anschliessend Staging-Zeilen bzw. einen
    Status auf eine Zeile, die es nicht mehr gibt."""

    def __init__(self, actual_status: str) -> None:
        self.actual_status = actual_status
        # Ohne den Zusatz stand der Nutzer vor einem Lauf, der sich weder
        # uebernehmen noch entfernen liess, und wartete auf einen Worker, der
        # moeglicherweise gar nicht laeuft.
        hint = (
            (
                f" Laeuft gar kein Worker, wird der Lauf nach {STALE_RUN_AFTER_MINUTES} "
                "Minuten ohne Lebenszeichen entfernbar - abbrechen laesst er sich sofort."
            )
            if actual_status in _WORKER_OWNED_STATUSES
            else ""
        )
        super().__init__(
            f"Der Importlauf wird gerade verarbeitet (Status '{actual_status}') und kann "
            "deshalb nicht entfernt werden. Bitte warte, bis die Verarbeitung "
            f"abgeschlossen ist, und versuche es danach erneut.{hint}"
        )


class ImportRunAlreadyCommittedError(ImportRunError):
    """Der Lauf wurde uebernommen - er bleibt als Herkunftsnachweis bestehen.

    transactions.import_run_id ist ON DELETE SET NULL: ein Loeschen wuerde die
    entstandenen Buchungen nicht entfernen, sondern ihnen still die Herkunft
    nehmen. Die Meldung nennt deshalb bewusst KEINEN Umweg, der genau das
    tut."""

    def __init__(self, actual_status: str, committed_transactions: int) -> None:
        self.actual_status = actual_status
        self.committed_transactions = committed_transactions
        if committed_transactions > 0:
            detail = (
                f"Aus ihm sind bereits {committed_transactions} Buchungen entstanden, "
                "die dadurch still ihre Herkunft verlieren wuerden. Um die Buchungen "
                "loszuwerden, nutze 'Buchungen entfernen' - danach laesst sich dieselbe "
                "Datei erneut einlesen."
            )
        else:
            detail = (
                "Er ist bereits abgeschlossen und haelt fest, welche Datei eingelesen "
                "wurde."
            )
        super().__init__(
            f"Ein bereits uebernommener Importlauf wird nicht entfernt. {detail} "
            "Der Importlauf bleibt als Nachweis bestehen."
        )


class ImportRunNotCancellableError(ImportRunError):
    """Der Lauf ist nicht (mehr) in Arbeit - es gibt nichts abzubrechen.

    Bewusst getrennt von ImportRunInProgressError, das genau die
    Gegenrichtung meldet: dort ist der Lauf zu frueh dran zum Loeschen, hier
    zu spaet zum Abbrechen."""

    def __init__(self, actual_status: str) -> None:
        self.actual_status = actual_status
        hint = _NOT_CANCELLABLE_HINTS.get(actual_status, "")
        super().__init__(
            f"Der Importlauf kann nicht abgebrochen werden: Status ist "
            f"'{actual_status}'. Abbrechen laesst sich nur ein Lauf, der noch "
            f"verarbeitet wird.{hint}"
        )


class ImportRunNotReparsableError(ImportRunError):
    """Der Status laesst kein erneutes Einlesen zu (z. B. waehrend der Worker
    laeuft oder nachdem der Lauf uebernommen wurde)."""

    def __init__(self, actual_status: str) -> None:
        self.actual_status = actual_status
        hint = _NOT_REPARSABLE_HINTS.get(actual_status, "")
        super().__init__(
            f"Der Importlauf kann nicht erneut eingelesen werden: Status ist "
            f"'{actual_status}', moeglich ist das nur bei 'ready', 'needs_mapping' "
            f"oder 'failed'.{hint}"
        )


class ImportRunReparseCommittedError(ImportRunError):
    """Aus dem Lauf wurden bereits Zeilen uebernommen.

    Ein erneutes Einlesen ersetzt saemtliche Staging-Zeilen. Die bereits
    entstandenen Buchungen bleiben dabei bestehen, verlieren aber die Zeile, aus
    der sie hervorgegangen sind - genau der stille Herkunftsverlust, den auch
    das Loeschen eines uebernommenen Laufs verhindert."""

    def __init__(self, committed_transactions: int) -> None:
        self.committed_transactions = committed_transactions
        super().__init__(
            f"Aus diesem Importlauf wurden bereits {committed_transactions} Zeilen "
            "uebernommen. Ein erneutes Einlesen wuerde die zugehoerigen "
            "Staging-Zeilen verwerfen, womit die daraus entstandenen Buchungen still "
            "ihre Herkunft verloeren. Bitte korrigiere oder archiviere die betroffenen "
            "Buchungen; fuer die restlichen Zeilen hilft nur ein neuer Upload."
        )


class ImportRunNoStagedRowsError(ImportRunError):
    """Der Lauf hat keine Rohzeilen, auf die eine korrigierte Zuordnung
    angewendet werden koennte.

    Das erneute Einlesen arbeitet ausschliesslich auf import_rows.raw_data - die
    hochgeladene Datei wird bewusst nirgends aufbewahrt (kein Speicher voller
    unverschluesselter Kontoauszuege). Ohne Rohzeilen fuehrt nur ein neuer
    Upload weiter."""

    def __init__(self) -> None:
        super().__init__(
            "Zu diesem Importlauf sind keine Rohzeilen gespeichert - es gibt nichts, "
            "was sich mit einer korrigierten Spaltenzuordnung erneut lesen liesse. "
            "Die hochgeladene Datei wird bewusst nicht aufbewahrt; bitte lade sie "
            "erneut hoch."
        )


class ImportRunMappingIncompleteError(ImportRunError):
    """Die mitgeschickte Zuordnung nennt keine Datums- oder keine Betragsspalte.

    Der Parser (worker.imports.parsers.csv_generic.map_rows) verlangt beides und
    wuerde den Lauf sonst erneut auf 'failed' setzen - ein Umweg ueber den
    Worker, an dessen Ende dieselbe Aussage stuende, nur eine Minute spaeter und
    als Fehlerstatus statt als Antwort auf die Eingabe."""

    def __init__(self, missing_fields: list[str]) -> None:
        self.missing_fields = missing_fields
        labels = {"booked_date": "Buchungsdatum", "amount": "Betrag"}
        missing = ", ".join(labels.get(field, field) for field in missing_fields)
        super().__init__(
            f"Die Spaltenzuordnung ist unvollstaendig - es fehlt: {missing}. Ohne "
            "Datums- und Betragsspalte laesst sich aus einer Zeile keine Buchung "
            "bilden."
        )


# Zusatzhinweis je Status - sagt dem Nutzer, ob er warten, nichts tun oder
# neu hochladen muss, statt ihn mit dem blossen Statusnamen zu lassen.
_NOT_READY_HINTS: dict[str, str] = {
    "pending": " Die Verarbeitung wurde noch nicht gestartet - bitte kurz warten.",
    "analysing": " Die Datei wird gerade analysiert - bitte kurz warten.",
    "needs_mapping": (
        " Datum und Betrag liessen sich keiner Spalte zuordnen. Korrigiere die "
        "Spaltenzuordnung - die Datei muss dafuer nicht erneut hochgeladen werden."
    ),
    "committing": " Der Lauf wird bereits uebernommen.",
    "completed": " Der Lauf wurde bereits vollstaendig uebernommen.",
    "failed": (
        " Die Verarbeitung ist fehlgeschlagen - der Lauf kann entfernt und die Datei "
        "erneut hochgeladen werden."
    ),
    "cancelled": " Der Lauf wurde abgebrochen.",
}

# Dasselbe fuer PATCH: hier ist die weiterfuehrende Handlung eine andere als
# beim Commit (ein fehlgeschlagener Lauf hat gar keine Zeilen, denen ein
# Zielkonto helfen wuerde - der Worker rollt vor 'failed' alles zurueck).
_NOT_EDITABLE_HINTS: dict[str, str] = {
    "pending": " Die Datei wird gleich verarbeitet - bitte kurz warten und danach zuweisen.",
    "analysing": " Die Datei wird gerade analysiert - bitte kurz warten und danach zuweisen.",
    "needs_mapping": (
        " Der Lauf hat noch keine auswertbaren Zeilen; zuerst muss die "
        "Spaltenzuordnung korrigiert werden, danach laesst sich das Zielkonto "
        "zuweisen."
    ),
    "committing": " Der Lauf wird gerade uebernommen.",
    "completed": (
        " Der Lauf wurde bereits uebernommen; die Buchungen liegen im damals "
        "gewaehlten Konto und werden ueber den Import nicht mehr verschoben."
    ),
    "failed": (
        " Die Datei konnte nicht gelesen werden, es gibt also keine Zeilen, denen ein "
        "Zielkonto helfen wuerde. Bitte den Lauf entfernen und die Datei erneut "
        "hochladen."
    ),
    "cancelled": " Der Lauf wurde abgebrochen und kann nur noch entfernt werden.",
}

# Und fuer den Abbruch: hier ist die Frage nicht "wie repariere ich den Lauf",
# sondern "wieso passiert nichts mehr" - jeder Hinweis nennt deshalb den
# Zustand, in dem der Lauf stattdessen steht.
_NOT_CANCELLABLE_HINTS: dict[str, str] = {
    "ready": (
        " Die Analyse ist bereits fertig; wenn du ihn nicht uebernehmen willst, "
        "entferne ihn."
    ),
    "needs_mapping": (
        " Er wartet auf eine Spaltenzuordnung und laeuft nicht - entweder die "
        "Zuordnung korrigieren oder den Lauf entfernen."
    ),
    "committing": (
        " Die Uebernahme laeuft bereits; sie jetzt zu stoppen liesse halb "
        "geschriebene Buchungen zurueck."
    ),
    "completed": " Der Lauf wurde bereits vollstaendig uebernommen.",
    "failed": " Der Lauf ist bereits beendet - er kann nur noch entfernt werden.",
    "cancelled": " Der Lauf wurde bereits abgebrochen.",
}

# Und fuers erneute Einlesen: 'pending'/'analysing' sind reine Wartefaelle,
# alles andere ist endgueltig.
_NOT_REPARSABLE_HINTS: dict[str, str] = {
    "pending": " Die Verarbeitung ist eingereiht - bitte kurz warten.",
    "analysing": (
        " Die Datei wird gerade gelesen - bitte das Ergebnis abwarten und die "
        "Zuordnung danach korrigieren."
    ),
    "committing": " Der Lauf wird gerade uebernommen.",
    "completed": (
        " Der Lauf wurde bereits uebernommen; die entstandenen Buchungen werden "
        "ueber den Import nicht mehr veraendert."
    ),
    "cancelled": " Der Lauf wurde abgebrochen und kann nur noch entfernt werden.",
}


async def commit_single_row(
    session: AsyncSession,
    household_id: UUID,
    account_id: UUID,
    row: Any,
    *,
    is_transfer: bool = False,
    category_id: UUID | None = None,
    committed_by: UUID | None = None,
    payee_index: payee_link_service.PayeeIndex | None = None,
) -> UUID:
    """Committet eine einzelne import_rows-Staging-Zeile in transactions +
    transaction_splits, wendet passende Klassifikationsregeln an und markiert die
    Staging-Zeile als 'committed'. `category_id` (falls vom Aufrufer explizit gewaehlt)
    hat Vorrang vor einer automatisch zutreffenden Regel.

    `committed_by` landet als `categorized_by` an einem Split, dessen Kategorie
    aus einer ausdruecklichen Nutzerwahl stammt - bei einem Regeltreffer waere
    der Mensch nicht die Ursache und das Feld bleibt leer.

    `payee_index` ist der einmal geladene Empfaengerindex des Haushalts. Der
    Stapelcommit reicht ihn durch, damit die Empfaengeraufloesung nicht je Zeile
    dieselben Stammdaten nachlaedt; Einzelaufrufer (Review-Aufloesung) lassen ihn
    weg und bekommen ihn dann fuer diese eine Zeile geladen."""
    rule_match = await match_rules_for_transaction(
        session, household_id, row.payee_raw, row.purpose, row.amount_minor, account_id
    )

    # Beide Quellen einer Kategorie - explizite Nutzerwahl und Regeltreffer -
    # laufen durch den zentralen Resolver, bevor sie in einen Split gelangen.
    # Eine Regel kann auf eine inzwischen archivierte Kategorie zeigen; dann
    # wird die Zeile lieber unkategorisiert importiert und landet in der
    # Review-Inbox, statt den ganzen Import scheitern zu lassen.
    final_category_id = await category_service.resolve_category_id(
        session, household_id, category_id
    )
    final_payee_id = None
    final_is_transfer = is_transfer
    # note behaelt sein bisheriges Verhalten (Zweck, ggf. von action_note einer
    # Regel ersetzt), damit sich die Buchungsliste nicht schlagartig aendert.
    # Der Datenverlust ist trotzdem behoben: purpose_raw haelt den Bankrohtext
    # unabhaengig davon fest. Die Trennung "note = Notiz des Nutzers" folgt mit
    # dem Explorer, der purpose_raw als eigene Spalte anzeigt.
    final_note = row.purpose

    # Herkunft der Kategorie: eine ausdrueckliche Nutzerwahl im Importdialog
    # schlaegt den Regeltreffer, deshalb wird sie zuerst gesetzt.
    provenance = provenance_service.manual_assignment(
        final_category_id, committed_by, source="import"
    )

    if rule_match:
        if final_category_id is None:
            final_category_id = await _resolve_rule_category(
                session, household_id, rule_match["category_id"]
            )
            # Nur wenn die Regel die Kategorie wirklich gesetzt hat, ist sie auch
            # deren Herkunft. Zeigt sie auf eine archivierte Kategorie, bleibt die
            # Buchung unkategorisiert - dann waere 'rule' eine Falschaussage.
            if final_category_id is not None:
                provenance = provenance_service.rule_assignment(
                    final_category_id,
                    rule_match["rule_id"],
                    rule_match["rule_name"],
                    rule_match["matched_on"],
                )
        final_payee_id = rule_match["payee_id"]
        final_is_transfer = final_is_transfer or bool(rule_match["is_transfer"])
        if rule_match["note"]:
            final_note = rule_match["note"]

    # Empfaengerverknuepfung (Plan 19.2): ohne sie bleibt payee_id leer, und der
    # Vertragsabgleich kommt ueber identity_score 0,60 nicht hinaus - die
    # Preisaenderungserkennung verlangt 0,80. Die `action_payee_id` einer Regel
    # ist eine ausdrueckliche Nutzerkonfiguration und wird deshalb NIE
    # ueberschrieben; aufgeloest wird nur, wenn sie nichts gesetzt hat. Neu
    # angelegt wird kein Empfaenger (Begruendung: payee_link_service).
    if final_payee_id is None:
        final_payee_id = await payee_link_service.resolve_payee_id(
            session, household_id, row.payee_raw, index=payee_index
        )

    tx_id: UUID = (
        await session.execute(
            text(
                # source = 'import': die Buchung stammt aus einer Bankdatei und
                # nicht aus einer Handeingabe. Der Spalten-Default 'manual' hat
                # das bisher fuer jede importierte Buchung falsch behauptet.
                "insert into transactions (household_id, account_id, booked_date, "
                "value_date, amount_minor, currency, payee_id, payee_label, note, "
                "payee_raw, purpose_raw, bank_transaction_code, source, "
                "is_transfer, import_run_id, external_reference, fingerprint) "
                "values (:household_id, :account_id, :booked_date, :value_date, "
                ":amount_minor, :currency, :payee_id, :payee_label, :note, "
                ":payee_raw, :purpose_raw, :bank_transaction_code, 'import', "
                ":is_transfer, :import_run_id, :external_reference, :fingerprint) "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "account_id": str(account_id),
                "booked_date": row.booked_date,
                "value_date": row.value_date,
                "amount_minor": row.amount_minor,
                "currency": row.currency or "EUR",
                "payee_id": str(final_payee_id) if final_payee_id else None,
                "payee_label": (row.payee_raw or "")[:160] or None,
                "note": final_note,
                "payee_raw": row.payee_raw,
                "purpose_raw": row.purpose,
                "bank_transaction_code": getattr(row, "bank_transaction_code", None),
                "is_transfer": final_is_transfer,
                "import_run_id": str(row.import_run_id),
                "external_reference": row.external_reference,
                "fingerprint": row.fingerprint,
            },
        )
    ).scalar_one()

    # Zweiter, unabhaengiger Regeldurchlauf: Erstattungsfaehigkeit. Getrennt vom
    # Kategorietreffer oben, weil der beim ersten Treffer endet - sonst
    # verdeckte eine hoeher priorisierte Kategorieregel jede Erstattungsregel.
    # Ein Fehlschlag bleibt folgenlos: die Buchung ist wichtiger als die Marke.
    reimbursement_rule = await match_reimbursement_rule_for_transaction(
        session, household_id, row.payee_raw, row.purpose, row.amount_minor, account_id
    )
    if reimbursement_rule is not None:
        await reimbursement_mark_service.apply_rule(
            session, household_id, tx_id, reimbursement_rule
        )

    await session.execute(
        text(
            "insert into transaction_splits "
            "(household_id, transaction_id, amount_minor, "
            f"{provenance_service.CATEGORY_ASSIGNMENT_INSERT_COLUMNS}) "
            "values (:household_id, :transaction_id, :amount_minor, "
            f"{provenance_service.CATEGORY_ASSIGNMENT_INSERT_VALUES})"
        ),
        {
            "household_id": str(household_id),
            "transaction_id": str(tx_id),
            "amount_minor": row.amount_minor,
            **provenance,
        },
    )

    await session.execute(
        text(
            "update import_rows set status = 'committed', "
            "committed_transaction_id = :tx_id where id = :row_id"
        ),
        {"tx_id": str(tx_id), "row_id": str(row.id)},
    )

    return tx_id


async def get_import_row_for_review(
    session: AsyncSession, household_id: UUID, import_row_id: UUID, expected_status: str
) -> Any | None:
    """Laedt eine einzelne Staging-Zeile inkl. Konto des zugehoerigen Imports fuer
    Review-Aktionen (Dubletten-/Transfer-Aufloesung). Gibt None zurueck, wenn die Zeile
    nicht existiert, einem anderen Haushalt gehoert oder nicht mehr im erwarteten Status ist."""
    return (
        await session.execute(
            text(
                "select r.id, r.import_run_id, r.booked_date, r.value_date, "
                "r.amount_minor, r.currency, r.payee_raw, r.purpose, "
                "r.bank_transaction_code, "
                "r.external_reference, r.fingerprint, r.duplicate_of_transaction_id, "
                "ir.account_id "
                "from import_rows r "
                "join import_runs ir on ir.id = r.import_run_id "
                "where r.id = :row_id and r.household_id = :household_id "
                "and r.status = :expected_status"
            ),
            {
                "row_id": str(import_row_id),
                "household_id": str(household_id),
                "expected_status": expected_status,
            },
        )
    ).first()


async def mark_import_row_skipped(
    session: AsyncSession, household_id: UUID, import_row_id: UUID
) -> bool:
    """Markiert eine Dubletten-Staging-Zeile als bestaetigt (wird nie committet)."""
    result = await session.execute(
        text(
            "update import_rows set status = 'skipped' "
            "where id = :row_id and household_id = :household_id "
            "and status = 'duplicate_possible'"
        ),
        {"row_id": str(import_row_id), "household_id": str(household_id)},
    )
    return cast(CursorResult[Any], result).rowcount > 0


async def maybe_complete_run(session: AsyncSession, import_run_id: UUID) -> None:
    """Schliesst einen Importlauf nur dann ab, wenn keine offenen (noch nicht
    committeten/uebersprungenen) Staging-Zeilen mehr vorhanden sind. Wird sowohl vom
    Batch-Commit als auch von den Review-Aktionen fuer Dubletten/Transfers aufgerufen,
    da beide den Zeilenstatus unabhaengig voneinander veraendern koennen."""
    await session.execute(
        text(
            "update import_runs set status = 'completed', completed_at = now() "
            "where id = :run_id and status = 'ready' "
            "and not exists ("
            "  select 1 from import_rows "
            "  where import_run_id = :run_id "
            "  and status in ('new', 'transfer_candidate', 'duplicate_possible')"
            ")"
        ),
        {"run_id": str(import_run_id)},
    )


async def commit_import_run(
    session: AsyncSession,
    import_run_id: UUID,
    household_id: UUID,
    row_ids: list[UUID] | None = None,
    category_by_row: Mapping[UUID, UUID | None] | None = None,
    committed_by: UUID | None = None,
) -> int:
    """Uebernimmt ausgewaehlte Staging-Zeilen (oder alle nicht-Duplikate) in die echte
    transactions-Tabelle.

    Wirft ImportRunNotFoundError / ImportRunNotReadyError /
    ImportRunAccountMissingError - die drei Fehlerfaelle werden bewusst
    getrennt gemeldet, damit die Route sie auf unterschiedliche HTTP-Codes
    abbilden kann. Der Status wird VOR dem Zielkonto geprueft: bei einem
    fehlgeschlagenen oder noch laufenden Lauf ist der Hinweis auf ein
    fehlendes Konto nicht die Handlung, die weiterhilft."""
    run_row = (
        await session.execute(
            text(
                "select account_id, status from import_runs "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(import_run_id), "household_id": str(household_id)},
        )
    ).first()
    if run_row is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")
    if run_row.status != "ready":
        raise ImportRunNotReadyError(str(run_row.status))
    if run_row.account_id is None:
        raise ImportRunAccountMissingError(
            household_has_accounts=await household_has_accounts(session, household_id)
        )

    account_id = run_row.account_id

    query = (
        "select id, import_run_id, booked_date, value_date, amount_minor, currency, "
        "payee_raw, purpose, bank_transaction_code, external_reference, fingerprint, "
        "status, duplicate_of_transaction_id "
        "from import_rows "
        "where import_run_id = :run_id and status <> 'committed' "
    )
    params: dict[str, object] = {"run_id": str(import_run_id)}
    if row_ids is not None:
        query += "and id = any(:row_ids) "
        params["row_ids"] = [str(r) for r in row_ids]
    else:
        query += "and status in ('new', 'transfer_candidate') "

    rows = (await session.execute(text(query), params)).all()
    if not rows:
        return 0

    # Eine im Staging manuell gewaehlte Kategorie hat Vorrang vor dem
    # Regeltreffer (Kategorisierungsplan, "Review und Import"). Sie wird an
    # commit_single_row durchgereicht, das genau diese Rangfolge umsetzt.
    manual_categories = category_by_row or {}
    # Einmal je Lauf statt einmal je Zeile: die Empfaengerstammdaten aendern
    # sich waehrend eines Commits nicht, und ein Kontoauszug hat schnell
    # mehrere hundert Zeilen.
    payee_index = await payee_link_service.load_payee_index(session, household_id)
    committed_count = 0
    for r in rows:
        # Eine als Transfer erkannte Zeile bleibt auch im Stapelcommit ein
        # Transfer. Vorher landete sie hier als gewoehnliche Buchung: die
        # Umbuchung aufs Kreditkartenkonto stand danach als Ausgabe im
        # Girokonto UND die Kartenumsaetze einzeln daneben, also doppelt in
        # jeder Auswertung. Der Weg ueber die Review-Inbox
        # (POST /v1/review/accept) hat das immer richtig gemacht - der
        # Stapelweg zog nur nicht nach.
        is_transfer = str(r.status) == "transfer_candidate"
        await commit_single_row(
            session,
            household_id,
            account_id,
            r,
            is_transfer=is_transfer,
            category_id=manual_categories.get(UUID(str(r.id))),
            committed_by=committed_by,
            payee_index=payee_index,
        )
        if is_transfer and r.duplicate_of_transaction_id:
            # Die Gegenbuchung liegt bereits im Bestand und muss dieselbe
            # Kennzeichnung bekommen, sonst zaehlt die eine Seite weiter mit.
            await session.execute(
                text(
                    "update transactions set is_transfer = true "
                    "where id = :id and household_id = :household_id"
                ),
                {
                    "id": str(r.duplicate_of_transaction_id),
                    "household_id": str(household_id),
                },
            )
        committed_count += 1

    await maybe_complete_run(session, import_run_id)
    return committed_count


# ---------------------------------------------------------------------------
# Verwaltung eines Importlaufs: Zielkonto nachtraeglich zuweisen, Lauf entfernen
#
# Vorher war ein Lauf, der ohne Zielkonto hochgeladen wurde, dauerhaft
# unbrauchbar UND unentfernbar: uebernehmen ging nicht (kein Konto), aendern
# war nicht vorgesehen, loeschen ebenfalls nicht - er blieb fuer immer als
# "Bereit zur Uebernahme" in der Historie stehen.
# ---------------------------------------------------------------------------


async def household_has_accounts(session: AsyncSession, household_id: UUID) -> bool:
    """Gibt es im Haushalt ueberhaupt ein (nicht geschlossenes) Konto? Nur dafuer
    gedacht, die richtige Handlungsanweisung zu waehlen ("Konto anlegen" vs.
    "Konto zuweisen") - `not is_closed` deckungsgleich mit GET /v1/accounts,
    damit die Meldung zu dem passt, was die Oberflaeche zur Auswahl anbietet."""
    row = (
        await session.execute(
            text(
                "select 1 from accounts "
                "where household_id = :household_id and not is_closed limit 1"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    return row is not None


async def _fetch_run_snapshot(
    session: AsyncSession, import_run_id: UUID, household_id: UUID
) -> dict[str, Any] | None:
    """Vollstaendige Laufzeile - dient gleichzeitig als Vorher-/Nachher-Bild
    fuers audit_log und als Datenquelle der HTTP-Antwort."""
    row = (
        await session.execute(
            text(
                f"select {_RUN_SNAPSHOT_COLUMNS} from import_runs "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(import_run_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    return dict(row._mapping)


async def _fetch_run_response(
    session: AsyncSession, import_run_id: UUID, household_id: UUID
) -> dict[str, Any]:
    """Laufzeile fuer die HTTP-Antwort: dieselben Spalten plus die berechnete
    Haengerkennung.

    Bewusst getrennt vom Audit-Schnappschuss - is_stale ist kein gespeicherter
    Zustand, sondern eine Aussage ueber die aktuelle Uhrzeit. Im Audit-Diff
    stuende sonst eine Aenderung, die niemand vorgenommen hat."""
    row = (
        await session.execute(
            text(
                f"select {_RUN_SNAPSHOT_COLUMNS}, {RUN_IS_STALE_EXPRESSION} "
                "from import_runs where id = :id and household_id = :household_id"
            ),
            {
                "id": str(import_run_id),
                "household_id": str(household_id),
                "stale_minutes": STALE_RUN_AFTER_MINUTES,
            },
        )
    ).first()
    if row is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")
    return dict(row._mapping)


async def _run_is_stale(session: AsyncSession, import_run_id: UUID) -> bool:
    """Hat der Lauf seit STALE_RUN_AFTER_MINUTES kein Lebenszeichen mehr?

    Die Rechnung bleibt in der Datenbank, statt die Zeitstempel in Python zu
    vergleichen: `now()` ist dieselbe Uhr, die auch die Zeitstempel geschrieben
    hat - eine abweichende Serveruhr oder ein zeitzonenloser Zeitstempel wuerde
    die Haengerkennung sonst still verschieben."""
    row = (
        await session.execute(
            text(
                "select coalesce(heartbeat_at, started_at, created_at) "
                "< now() - make_interval(mins => :stale_minutes) as is_stale "
                "from import_runs where id = :id"
            ),
            {"id": str(import_run_id), "stale_minutes": STALE_RUN_AFTER_MINUTES},
        )
    ).first()
    return bool(row is not None and row.is_stale)


async def update_import_run(
    session: AsyncSession,
    import_run_id: UUID,
    household_id: UUID,
    actor_user_id: UUID | None,
    updates: Mapping[str, UUID | None],
) -> dict[str, Any]:
    """Weist einem noch nicht uebernommenen Lauf nachtraeglich ein Zielkonto
    (und/oder ein Mapping-Profil) zu.

    `updates` enthaelt ausschliesslich die Felder, die der Aufrufer wirklich
    gesetzt hat (siehe ImportRunUpdate/exclude_unset) - ein leeres Mapping ist
    erlaubt und liefert den unveraenderten Lauf zurueck, wie es der
    Nachbarendpunkt PATCH /v1/accounts/{id} ebenfalls tut.

    Erlaubt ist das nur im Status 'ready':
      - 'completed' waere eine nachtraegliche Umbuchung bereits entstandener
        Buchungen, die dieser Endpunkt gerade nicht vornimmt,
      - 'pending'/'analysing'/'committing' ist ein Rennen mit dem Worker (er
        liest account_id zu Beginn der Dublettenerkennung),
      - 'failed' hat gar keine Staging-Zeilen (der Worker rollt vor dem Setzen
        von 'failed' zurueck), ein Zielkonto wuerde dort nichts aendern -
        dieser Lauf gehoert entfernt, nicht repariert.

    ACHTUNG mapping_profile_id: die Datei ist zu diesem Zeitpunkt bereits
    geparst. Ein nachtraeglich gesetztes Profil korrigiert nur die
    Verknuepfung, es wird NICHT neu geparst."""
    before = await _fetch_run_snapshot(session, import_run_id, household_id)
    if before is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")
    if str(before["status"]) != "ready":
        raise ImportRunNotEditableError(str(before["status"]))

    fields = {key: value for key, value in updates.items() if key in UPDATABLE_RUN_FIELDS}

    account_id = fields.get("account_id")
    if account_id is not None:
        account_row = (
            await session.execute(
                text("select 1 from accounts where id = :id and household_id = :household_id"),
                {"id": str(account_id), "household_id": str(household_id)},
            )
        ).first()
        if account_row is None:
            raise ImportRunAccountUnknownError()

    mapping_profile_id = fields.get("mapping_profile_id")
    if mapping_profile_id is not None:
        profile_row = (
            await session.execute(
                text(
                    "select 1 from import_mapping_profiles "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(mapping_profile_id), "household_id": str(household_id)},
            )
        ).first()
        if profile_row is None:
            raise ImportRunMappingProfileUnknownError()

    if not fields:
        return before

    # Der SET-Teil entsteht ausschliesslich aus UPDATABLE_RUN_FIELDS (Allowlist
    # oben), nie aus einem frei gewaehlten Schluessel des Aufrufers.
    set_clause = ", ".join(f"{column} = :{column}" for column in fields)
    params: dict[str, object] = {
        column: (str(value) if value is not None else None) for column, value in fields.items()
    }
    params["id"] = str(import_run_id)
    params["household_id"] = str(household_id)
    row = (
        await session.execute(
            text(
                f"update import_runs set {set_clause} "
                "where id = :id and household_id = :household_id "
                f"returning {_RUN_SNAPSHOT_COLUMNS}"
            ),
            params,
        )
    ).first()
    if row is None:
        # Kann nur eintreten, wenn RLS das UPDATE verwirft - dann lieber melden
        # als einen Erfolg vorzutaeuschen, den es nicht gab.
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    after = dict(row._mapping)
    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        IMPORT_RUN_ENTITY_TYPE,
        import_run_id,
        "update",
        before=before,
        after=after,
    )
    return after


async def cancel_import_run(
    session: AsyncSession,
    import_run_id: UUID,
    household_id: UUID,
    actor_user_id: UUID | None,
) -> dict[str, Any]:
    """Bricht einen Lauf ab, der noch verarbeitet wird.

    Zwei Faelle mit unterschiedlicher Wirkung:
      - 'pending': die Verarbeitung hat noch nicht begonnen, der Lauf endet
        sofort. Holt der Worker die Nachricht spaeter doch noch ab, ueberspringt
        er ihn - `process_import_run` laedt nur Laeufe in Arbeit, ein
        abgebrochener ist keiner mehr.
      - 'analysing': der Worker schreibt gerade. Hier wird ausschliesslich der
        Wunsch vermerkt; er steigt an seinem naechsten Pruefpunkt aus und setzt
        den Endstatus selbst. Ihn hier auf 'cancelled' zu setzen waere ein
        Rennen gegen genau diesen Schreibvorgang - und `error_message` bliebe
        auf einer Meldung stehen, die den echten Abbruchgrund ueberschreibt.

    Alles andere ist kein laufender Import und wird abgelehnt
    (ImportRunNotCancellableError)."""
    before = await _fetch_run_snapshot(session, import_run_id, household_id)
    if before is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    run_status = str(before["status"])
    if run_status not in _WORKER_OWNED_STATUSES:
        raise ImportRunNotCancellableError(run_status)

    if run_status == "pending":
        statement = (
            "update import_runs set status = 'cancelled', cancel_requested = true, "
            "completed_at = now(), error_message = :error_message "
            "where id = :id and household_id = :household_id "
            f"returning {_RUN_SNAPSHOT_COLUMNS}"
        )
    else:
        statement = (
            "update import_runs set cancel_requested = true "
            "where id = :id and household_id = :household_id "
            f"returning {_RUN_SNAPSHOT_COLUMNS}"
        )

    row = (
        await session.execute(
            text(statement),
            {
                "id": str(import_run_id),
                "household_id": str(household_id),
                "error_message": "Vom Nutzer abgebrochen.",
            },
        )
    ).first()
    if row is None:
        # Nur erreichbar, wenn RLS das UPDATE verwirft - dann lieber melden als
        # einen Abbruch zu bestaetigen, den es nicht gab.
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        IMPORT_RUN_ENTITY_TYPE,
        import_run_id,
        "cancel",
        before=before,
        after=dict(row._mapping),
    )
    return await _fetch_run_response(session, import_run_id, household_id)


async def count_committed_transactions(
    session: AsyncSession, import_run_id: UUID, household_id: UUID
) -> int:
    """Zaehlt, was aus dem Lauf bereits in den echten Bestand uebernommen wurde.

    Zwei Quellen, weil beide fuer sich luecken haben: `transactions` zaehlt die
    tatsaechlich vorhandenen Buchungen; `import_rows.status = 'committed'`
    faengt zusaetzlich den Fall ab, dass eine daraus entstandene Buchung
    zwischenzeitlich geloescht wurde (committed_transaction_id ist dann ON
    DELETE SET NULL, der Zeilenstatus bleibt aber 'committed'). Ein Teil-Commit
    laesst den Lauf auf 'ready' stehen - der Status allein ist also KEIN
    ausreichendes Kriterium dafuer, ob geloescht werden darf.

    Benutzt denselben Ausdruck, den die Laufzeile als committed_count
    ausliefert: die Oberflaeche darf nicht nach einer anderen Regel entscheiden
    als der Server, sonst zeigt sie einen Knopf, der beim Klick abgelehnt wird.
    Setzt voraus, dass der Lauf existiert - beide Aufrufer pruefen das vorher."""
    return int(
        (
            await session.execute(
                text(
                    f"select {RUN_COMMITTED_COUNT_EXPRESSION} from import_runs "
                    "where id = :run_id and household_id = :household_id"
                ),
                {"run_id": str(import_run_id), "household_id": str(household_id)},
            )
        ).scalar_one()
    )


# Warum eine Buchung beim Zuruecknehmen zunaechst stehen bleibt. Die Reihenfolge
# ist die der Pruefung: der erste zutreffende Grund gewinnt, damit jede Buchung
# genau einmal gezaehlt wird.
#
# "Zunaechst", weil der Nutzer sie ausdruecklich mitnehmen kann (include_linked).
# Der Grund ist dann kein Hindernis mehr, sondern die Liste der Verknuepfungen,
# die dabei zu loesen sind - siehe transaction_detach_service.
#
# ACHTUNG - diese Liste muss decken, was transaction_detach_service beim
# Entfernen loest. Fehlt hier ein Fall, gilt die Buchung als unverknuepft, geht
# ohne Rueckfrage mit und die daran haengende Arbeit wird vernichtet, obwohl der
# Nutzer den Haken bewusst NICHT gesetzt hat. Genau so fehlte hier zunaechst die
# Versicherungszahlung. tests/test_transaction_detach_completeness.py erzwingt
# die Deckungsgleichheit.
_REVERT_BLOCK_REASONS: dict[str, str] = {
    "bearbeitet": "nachtraeglich bearbeitet",
    "umbuchung": "als Umbuchung mit einem anderen Konto verknuepft",
    "vertrag": "einem Vertrag zugeordnet",
    "erstattung": "einem Erstattungsvorgang zugeordnet",
    "versicherung": "einem Versicherungsschaden zugeordnet",
    "einkauf": "mit einem Einkauf verknuepft",
    "sparziel": "einem Sparziel zugeordnet",
}

# Eine Zeile je Buchung des Laufs, mit dem Grund fuer ein Stehenbleiben oder
# NULL, wenn sie zurueckgenommen werden darf.
#
# Zusammengesetzt statt fest verdrahtet: eine Installation kann hinter dem
# Schema zurueckliegen (in einer aelteren Installation fehlten die Tabellen des
# Schadenmoduls vollstaendig). Ein fester Verweis darauf haette die Vorschau dort nicht bloss
# ungenau gemacht, sondern mit einem Fehler abgebrochen - Postgres stolpert
# schon beim Planen ueber einen unbekannten Namen. Was es nicht gibt, kann auch
# nichts blockieren.
_REVERT_REASON_CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "umbuchung",
        "transfer_links",
        "select 1 from transfer_links l where l.outgoing_transaction_id = k.id "
        "or l.incoming_transaction_id = k.id",
    ),
    (
        "vertrag",
        "contract_occurrences",
        "select 1 from contract_occurrences o where o.transaction_id = k.id",
    ),
    (
        "vertrag",
        "contract_amount_history",
        "select 1 from contract_amount_history h where h.source_transaction_id = k.id",
    ),
    (
        "vertrag",
        "contract_assignment_overrides",
        "select 1 from contract_assignment_overrides a where a.transaction_id = k.id",
    ),
    (
        "erstattung",
        "health_transaction_allocations",
        "select 1 from health_transaction_allocations ha where ha.transaction_id = k.id",
    ),
    (
        "versicherung",
        "insurance_claim_payments",
        "select 1 from insurance_claim_payments ip where ip.transaction_id = k.id",
    ),
    (
        "versicherung",
        "insurance_claims",
        "select 1 from insurance_claims ic "
        "where ic.negotiations_closed_transaction_id = k.id",
    ),
    (
        "einkauf",
        "shopping_purchase_links",
        "select 1 from shopping_purchase_links s where s.transaction_id = k.id",
    ),
    (
        "sparziel",
        "transaction_splits",
        "select 1 from transaction_splits sp "
        "where sp.transaction_id = k.id and sp.goal_id is not null",
    ),
)


def _revert_candidates_sql(vorhandene_tabellen: set[str]) -> str:
    """Baut die Befundabfrage aus den Pruefungen, deren Tabellen es hier gibt."""
    zweige = "\n".join(
        f"    when exists ({abfrage}) then '{grund}'"
        for grund, tabelle, abfrage in _REVERT_REASON_CHECKS
        if tabelle in vorhandene_tabellen
    )
    return f"""
with kandidaten as (
  select t.id, t.booked_date, t.amount_minor, t.version
    from transactions t
   where t.import_run_id = :run_id
     and t.household_id = :household_id
     and not t.is_archived
)
select
  k.id,
  k.booked_date,
  k.amount_minor,
  case
    -- version > 1 heisst: nach dem Uebernehmen hat jemand etwas geaendert.
    -- Das setzt der Trigger bump_transaction_version nur bei einer echten
    -- fachlichen Aenderung, ein wirkungsloses Update zaehlt nicht mit.
    when k.version > 1 then 'bearbeitet'
{zweige}
    else null
  end as grund
from kandidaten k
"""


async def _collect_revert_candidates(
    session: AsyncSession, import_run_id: UUID, household_id: UUID
) -> dict[str, Any]:
    """Sammelt, was ein Zuruecknehmen des Laufs betreffen wuerde.

    Gemeinsame Grundlage von Vorschau und Ausfuehrung - beide muessen exakt
    dieselbe Menge sehen, sonst zeigt die Vorschau etwas anderes an, als
    anschliessend passiert."""
    vorhanden = await existing_tables(
        session, {tabelle for _grund, tabelle, _abfrage in _REVERT_REASON_CHECKS}
    )
    rows = (
        await session.execute(
            text(_revert_candidates_sql(vorhanden)),
            {"run_id": str(import_run_id), "household_id": str(household_id)},
        )
    ).all()

    zuruecknehmbar = [r for r in rows if r.grund is None]
    verknuepft = [r for r in rows if r.grund is not None]
    blockiert: dict[str, int] = {}
    for r in verknuepft:
        blockiert[str(r.grund)] = blockiert.get(str(r.grund), 0) + 1

    daten = [r.booked_date for r in zuruecknehmbar if r.booked_date is not None]
    alle_daten = [r.booked_date for r in rows if r.booked_date is not None]
    return {
        # Die Menge fuer das ausdrueckliche "auch das Verknuepfte entfernen":
        # dieselben Zeilen, nur ohne die Grund-Filterung.
        "alle_ids": [r.id for r in rows],
        "summe_alle_minor": sum(int(r.amount_minor) for r in rows),
        "von_alle": min(alle_daten) if alle_daten else None,
        "bis_alle": max(alle_daten) if alle_daten else None,
        "ids": [r.id for r in zuruecknehmbar],
        "gesamt": len(rows),
        "zuruecknehmbar": len(zuruecknehmbar),
        "summe_minor": sum(int(r.amount_minor) for r in zuruecknehmbar),
        "von": min(daten) if daten else None,
        "bis": max(daten) if daten else None,
        "blockiert": blockiert,
    }


async def preview_import_revert(
    session: AsyncSession, import_run_id: UUID, household_id: UUID
) -> dict[str, Any]:
    """Was wuerde ein Zuruecknehmen dieses Laufs anfassen?

    Aendert nichts. Gedacht als Vorschau vor der Bestaetigung: die Zahl allein
    ("47 Buchungen") sagt nichts darueber, ob darunter etwas ist, an dem
    inzwischen gearbeitet wurde."""
    snapshot = await _fetch_run_snapshot(session, import_run_id, household_id)
    if snapshot is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    befund = await _collect_revert_candidates(session, import_run_id, household_id)
    return {
        "import_run_id": import_run_id,
        "filename": snapshot.get("filename"),
        "transactions_total": befund["gesamt"],
        "revertable": befund["zuruecknehmbar"],
        "sum_minor": befund["summe_minor"],
        "from_date": befund["von"],
        "to_date": befund["bis"],
        # Dieselbe Vorschau fuer den Fall, dass der Nutzer die verknuepften
        # Buchungen ausdruecklich mitnimmt. Beide Zahlenwerke kommen aus einem
        # Abruf, damit die Oberflaeche beim Umschalten des Hakens nicht
        # nachladen muss und dabei einen anderen Bestand sehen koennte.
        "sum_minor_with_linked": befund["summe_alle_minor"],
        "from_date_with_linked": befund["von_alle"],
        "to_date_with_linked": befund["bis_alle"],
        "blocked": [
            {"reason": schluessel, "label": _REVERT_BLOCK_REASONS[schluessel], "count": anzahl}
            for schluessel, anzahl in sorted(befund["blockiert"].items())
        ],
    }


async def revert_import_run(
    session: AsyncSession,
    import_run_id: UUID,
    household_id: UUID,
    actor_user_id: UUID | None,
    include_linked: bool = False,
) -> dict[str, Any]:
    """Nimmt die Buchungen eines uebernommenen Laufs wieder aus dem Bestand.

    ARCHIVIEREN, NICHT LOESCHEN. Buchungen werden im ganzen System nie
    entfernt, sondern auf is_archived gesetzt (siehe DELETE /v1/transactions/
    {id}) - Auswertungen, Budgets und Listen blenden sie aus, der Bestand
    bleibt aber vollstaendig. Ein echtes Loeschen wuerde ueber die
    Fremdschluessel Splits, Verknuepfungen und Entscheidungen mitreissen.

    NEBENWIRKUNG, DIE ERWUENSCHT IST: die Dublettenerkennung liest
    ausschliesslich nicht-archivierte Buchungen (siehe
    worker/imports/processor.py). Nach dem Zuruecknehmen laesst sich dieselbe
    Datei also erneut einlesen und wird wieder als neu erkannt - genau der
    Ablauf "Import war schlecht, nochmal richtig".

    Der Importlauf selbst bleibt stehen. Er ist der Herkunftsnachweis der
    archivierten Buchungen; entfernte man ihn, waere hinterher nicht mehr
    nachvollziehbar, woher sie kamen.

    Standardmaessig stehen bleiben Buchungen, an denen inzwischen etwas haengt -
    was genau, steht in _REVERT_BLOCK_REASONS. Sie stillschweigend mit zu
    archivieren hiesse, Arbeit zu vernichten, die nach dem Import entstanden
    ist.

    include_linked=True nimmt sie ausdruecklich mit. Das ist kein "Schutz
    abschalten": die Verknuepfungen werden dabei sauber geloest (siehe
    transaction_detach_service), statt auf eine Buchung zu zeigen, die aus dem
    Bestand verschwunden ist. Ohne diesen Weg blieb bei "der Import war unsauber,
    nochmal richtig" genau die eine vertragszugeordnete Buchung zurueck und
    machte den erneuten Import unvollstaendig - die Dublettenerkennung sieht
    sie ja weiterhin und weist die entsprechende Zeile als Dublette ab."""
    snapshot = await _fetch_run_snapshot(session, import_run_id, household_id)
    if snapshot is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    befund = await _collect_revert_candidates(session, import_run_id, household_id)
    zu_entfernen = befund["alle_ids"] if include_linked else befund["ids"]
    geloest: dict[str, int] = {}
    if zu_entfernen:
        # Erst loesen, dann archivieren - und ZWINGEND fuer beide Faelle, nicht
        # nur fuer include_linked: auch eine unverknuepfte Buchung kann von einer
        # noch offenen Staging-Zeile als Dublette referenziert werden, und die
        # bliebe sonst als Warnung zu einer Buchung stehen, die es nicht mehr
        # gibt. Wo nichts zeigt, tut der Aufruf nichts.
        geloest = await detach_transactions(session, household_id, zu_entfernen)
        await session.execute(
            text(
                "update transactions set is_archived = true "
                "where household_id = :household_id and id = any(:ids)"
            ),
            {"household_id": str(household_id), "ids": zu_entfernen},
        )

    # Ein Ereignis fuer den ganzen Vorgang statt eines je Buchung: die
    # auditierte Handlung ist "dieser Import wurde zurueckgenommen", nicht
    # hundert einzelne Archivierungen. "archive" als Verb, weil genau das mit
    # den Buchungen geschieht - ein eigenes Verb haette eine Schemaaenderung
    # am CHECK von audit_log.action verlangt, ohne etwas zu erklaeren, was
    # "archive" nicht schon sagt.
    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        IMPORT_RUN_ENTITY_TYPE,
        import_run_id,
        "archive",
        before=snapshot,
        after=None,
        metadata={
            "reverted_transactions": len(zu_entfernen),
            "kept_transactions": befund["gesamt"] - len(zu_entfernen),
            "kept_reasons": {} if include_linked else befund["blockiert"],
            # Was mitgenommen wurde und welche Verknuepfungen dabei zu loesen
            # waren - ohne das liesse sich hinterher nicht mehr erklaeren, warum
            # eine Vertragsfaelligkeit ploetzlich wieder offen ist.
            "included_linked": include_linked,
            "untangled_links": geloest,
        },
    )

    return {
        "import_run_id": import_run_id,
        "reverted": len(zu_entfernen),
        "kept": befund["gesamt"] - len(zu_entfernen),
        "blocked": []
        if include_linked
        else [
            {"reason": schluessel, "label": _REVERT_BLOCK_REASONS[schluessel], "count": anzahl}
            for schluessel, anzahl in sorted(befund["blockiert"].items())
        ],
        # Die Oberflaeche nennt es dem Nutzer: "3 Vertragsfaelligkeiten wieder
        # offen" ist die Nebenwirkung, die er sonst erst spaeter entdeckt.
        "untangled": as_labelled(geloest),
    }


async def delete_import_run(
    session: AsyncSession,
    import_run_id: UUID,
    household_id: UUID,
    actor_user_id: UUID | None,
) -> dict[str, Any]:
    """Entfernt einen nie uebernommenen Importlauf samt seiner Staging-Zeilen
    (import_rows.import_run_id ist ON DELETE CASCADE, siehe
    supabase/migrations/0001_schema.sql) und gibt den geloeschten Lauf als
    Schnappschuss zurueck.

    Abgelehnt wird:
      - ein Lauf in Verarbeitung ('pending'/'analysing'/'committing'), weil der
        Worker gleich auf die Zeile schreibt,
      - ein uebernommener Lauf ('completed' oder mit bereits uebernommenen
        Zeilen), weil transactions.import_run_id ON DELETE SET NULL ist und die
        Buchungen sonst still ihre Herkunft verlieren wuerden.

    Ausnahme vom ersten Punkt: ein haengender Lauf (siehe unten).

    Der Vorgang wird auditiert - ein entfernter Importlauf ist genau das
    Ereignis, das spaeter erklaeren muss, warum eine Datei nicht mehr in der
    Historie steht."""
    before = await _fetch_run_snapshot(session, import_run_id, household_id)
    if before is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    run_status = str(before["status"])
    # Der Schutz "der Worker schreibt gleich auf die Zeile" setzt voraus, dass
    # ueberhaupt ein Worker laeuft. Tut er es nicht - Redis aus, Worker nicht
    # gestartet, Prozess abgestuerzt -, blieb der Lauf fuer immer auf 'pending'
    # bzw. 'analysing' stehen und war damit dauerhaft unloeschbar: uebernehmen
    # ging nicht (nicht 'ready'), aendern nicht, entfernen nicht. Nach
    # STALE_RUN_AFTER_MINUTES ohne Lebenszeichen gibt es das Rennen nicht mehr,
    # gegen das hier geschuetzt wird - dann darf der Lauf weg.
    # 'committing' bleibt ausgenommen: dort schreibt die API selbst innerhalb
    # einer Transaktion, ein Lebenszeichen gibt es dabei gar nicht.
    stale_and_abandoned = run_status in _WORKER_OWNED_STATUSES and await _run_is_stale(
        session, import_run_id
    )
    if run_status in _IN_FLIGHT_STATUSES and not stale_and_abandoned:
        raise ImportRunInProgressError(run_status)

    committed = await count_committed_transactions(session, import_run_id, household_id)
    if run_status == "completed" or committed > 0:
        raise ImportRunAlreadyCommittedError(run_status, committed)

    # Audit-Zeile vor dem DELETE: audit_log.entity_id ist bewusst ohne
    # Fremdschluessel, das Ereignis ueberlebt also die geloeschte Zeile. Beides
    # laeuft in derselben Transaktion - es gibt kein Audit ohne Loeschung und
    # keine Loeschung ohne Audit.
    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        IMPORT_RUN_ENTITY_TYPE,
        import_run_id,
        "delete",
        before=before,
        after=None,
    )

    result = await session.execute(
        text("delete from import_runs where id = :id and household_id = :household_id"),
        {"id": str(import_run_id), "household_id": str(household_id)},
    )
    if cast(CursorResult[Any], result).rowcount == 0:
        # RLS hat das DELETE verworfen (oder ein paralleler Request war
        # schneller). Die Ausnahme rollt die Transaktion samt Audit-Zeile
        # zurueck.
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    return before


async def count_staged_rows(session: AsyncSession, import_run_id: UUID) -> int:
    """Wie viele Staging-Zeilen haengen am Lauf? Fuer das erneute Einlesen ist
    das die Bestandsfrage schlechthin: raw_data dieser Zeilen ist die einzige
    verbliebene Kopie der hochgeladenen Datei."""
    return int(
        (
            await session.execute(
                text("select count(*) from import_rows where import_run_id = :run_id"),
                {"run_id": str(import_run_id)},
            )
        ).scalar_one()
    )


async def reparse_import_run(
    session: AsyncSession,
    import_run_id: UUID,
    household_id: UUID,
    actor_user_id: UUID | None,
    column_mapping: Mapping[str, str],
) -> dict[str, Any]:
    """Liest denselben Lauf mit einer korrigierten Spaltenzuordnung erneut ein.

    WARUM DIE ZUORDNUNG HIER NICHT SELBST ANGEWENDET WIRD: der Parse- und
    Staging-Schritt laeuft kanonisch im Worker (siehe Modul-Docstring von
    worker/imports/processor.py). Ein zweiter, synchroner Parse-Pfad in der API
    haette die Dublettenerkennung (compute_fingerprint/classify_row), den
    Kontoabgleich und das Schreiben der Staging-Zeilen ein zweites Mal
    implementiert - zwei Stellen, die auseinanderlaufen, sobald sich eine Regel
    der Dublettenerkennung aendert, und ein Requesthandler, der bei einem
    Jahresauszug minutenlang haelt, ohne Fortschritt oder Abbruch anbieten zu
    koennen. Stattdessen wird die Korrektur am Lauf vermerkt
    (import_runs.manual_column_mapping), der Lauf auf 'pending' zurueckgesetzt
    und derselbe Actor erneut zugestellt (das uebernimmt die Route); der Worker
    liest die Rohzeilen aus import_rows.raw_data, ruft `map_rows` auf und
    ersetzt die Staging-Zeilen mit derselben Logik wie beim ersten Einlesen.

    Die alten Staging-Zeilen werden deshalb hier NICHT geloescht: ihr raw_data
    ist die einzige verbliebene Kopie der hochgeladenen Datei (die selbst
    bewusst nirgends aufbewahrt wird). Wer sie vor dem Worker entfernt, nimmt
    ihm seine einzige Eingabe - das Ergebnis waere ein leerer Lauf statt eines
    korrigierten.

    Abgelehnt wird ein Lauf, aus dem bereits Zeilen uebernommen wurden: das
    erneute Einlesen ersetzt saemtliche Staging-Zeilen, die entstandenen
    Buchungen verloeren still ihre Herkunft (dieselbe Begruendung wie beim
    Loeschen)."""
    missing = [field for field in ("booked_date", "amount") if not column_mapping.get(field)]
    if missing:
        raise ImportRunMappingIncompleteError(missing)

    before = await _fetch_run_snapshot(session, import_run_id, household_id)
    if before is None:
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    run_status = str(before["status"])
    if run_status not in REPARSABLE_STATUSES:
        raise ImportRunNotReparsableError(run_status)

    committed = await count_committed_transactions(session, import_run_id, household_id)
    if committed > 0:
        raise ImportRunReparseCommittedError(committed)

    if await count_staged_rows(session, import_run_id) == 0:
        raise ImportRunNoStagedRowsError()

    row = (
        await session.execute(
            text(
                "update import_runs set "
                "manual_column_mapping = cast(:column_mapping as jsonb), "
                "status = 'pending', cancel_requested = false, "
                # started_at traegt die Haengerkennung. Bliebe es leer, fiele
                # coalesce(heartbeat_at, started_at, created_at) auf das alte
                # created_at zurueck - der gerade erst eingereihte Lauf waere in
                # derselben Sekunde als "haengt" markiert und sofort loeschbar.
                "started_at = now(), heartbeat_at = null, "
                # Zaehler und Fehlermeldung beschreiben den verworfenen Versuch.
                # Sie stehen zu lassen hiesse, waehrend des zweiten Laufs Zahlen
                # zu zeigen, zu denen es keine Staging-Zeile mehr gibt.
                "error_message = null, completed_at = null, total_rows = 0, "
                "new_count = 0, duplicate_count = 0, transfer_candidate_count = 0, "
                "error_count = 0 "
                "where id = :id and household_id = :household_id "
                f"returning {_RUN_SNAPSHOT_COLUMNS}"
            ),
            {
                "id": str(import_run_id),
                "household_id": str(household_id),
                "column_mapping": json.dumps(dict(column_mapping)),
            },
        )
    ).first()
    if row is None:
        # Nur erreichbar, wenn RLS das UPDATE verwirft.
        raise ImportRunNotFoundError("Importlauf nicht gefunden.")

    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        IMPORT_RUN_ENTITY_TYPE,
        import_run_id,
        "reparse",
        before=before,
        after=dict(row._mapping),
    )
    return await _fetch_run_response(session, import_run_id, household_id)
