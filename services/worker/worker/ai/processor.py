"""Batch-Klassifikationslauf (Analog zu worker/imports/processor.py).

Commit-Strategie: PRO ZEILE, nicht gesammelt am Ende. Jede Zeile loest 1-3
echte HTTP-Aufrufe an LM Studio aus (1-8s je Aufruf) - der DB-Commit-Overhead
(wenige ms) ist dagegen vernachlaessigbar. Im Gegenzug bleibt bei einem
Absturz/Timeout mitten im Lauf der bereits verarbeitete Anteil
(classification_decisions, llm_calls, processed_count/success_count/
error_count) dauerhaft erhalten. Anders als beim Import (import_rows sind nur
Vorschlaege, die der Nutzer erst per /commit bestaetigt) schreibt
Klassifikation direkt den finalen Seiteneffekt - Teil-Durabilitaet ist hier
also korrekt, nicht riskant.

Der Lauf schreibt zusaetzlich zwei Felder, von denen die Oberflaeche abhaengt
(siehe docs/KI-FINANZASSISTENT-PLAN.md, Ausbaustufe 0):
  * `heartbeat_at` nach jeder Buchung - nur daran erkennt die API einen
    haengenden Lauf und gibt den Haushalt fuer einen Neustart wieder frei;
  * `current_stage` als grobe Stufe des Laufs.
Ausserdem prueft er zwischen zwei Buchungen `cancel_requested`.

Vor dem Modell steht ein Vorfilter (worker/ai/known_payees.py): Buchungen,
deren Empfaenger im Bestand eindeutig von Hand oder per Regel kategorisiert
ist, bekommen ihren Vorschlag ohne LM-Studio-Aufruf. Sie zaehlen wie jede
andere Buchung als verarbeitet, beruehren aber die Fehlerkette unten nicht -
sie sagen ueber das Modell nichts aus.

Drei Notbremsen sorgen dafuer, dass der Lauf nicht ins Leere laeuft:
  * ist das geladene Kontextfenster unbekannt (oder die KI ausgeschaltet),
    scheitert der ganze Lauf sofort und benannt - statt 171 einzelner
    Fehlentscheidungen mit demselben Grund;
  * ein durchgefallener Vorabtest (Canary) beendet ihn, BEVOR die erste
    Buchung an das Modell geht;
  * zwei gleichartige Fehler unmittelbar hintereinander beenden ihn mitten
    im Lauf.
Alle Wege enden mit `status = 'failed'` und einer Meldung im Klartext samt
zustaendiger Einstellung (Reason-Codes des KI-Kerns, app/llm/reasons.py) -
eine Statistik aus 171 Fehlern kann niemand gebrauchen. Bereits verarbeitete
Buchungen bleiben in allen Faellen erhalten.
"""

import asyncio
from datetime import date
from typing import Any
from uuid import UUID

from app.llm import reasons
from app.llm.settings import load_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from worker.ai.capabilities import record_model_capability
from worker.ai.classifier import (
    FAILURE_REASON_CODES,
    NO_MATCH_REASON_CODE,
    PROMPT_VERSION,
    classify_transaction,
    load_categories,
    probe_model_capability,
    resolve_lm_studio_endpoint,
)
from worker.ai.known_payees import classify_known_payee, load_known_payees

_UNCLASSIFIED_SQL = (
    "select t.id, t.payee_label, "
    "coalesce(nullif(t.payee_raw, ''), t.payee_label) as payee_text, "
    "t.note, t.purpose_raw, t.amount_minor "
    "from transactions t "
    "where t.household_id = :household_id and not t.is_archived and not t.is_transfer "
    "and (not exists (select 1 from transaction_splits s where s.transaction_id = t.id) "
    "or exists (select 1 from transaction_splits s where s.transaction_id = t.id "
    "and s.category_id is null)) "
    "order by t.booked_date desc"
)

# Die API reiht den Job innerhalb ihrer noch offenen Request-Transaktion ein
# (dasselbe Muster wie beim Import). Der Worker kann die Laufzeile deshalb
# Millisekunden vor ihrem Commit lesen wollen und wuerde sie dann nicht finden
# - der Lauf bliebe fuer immer auf 'pending' stehen und blockierte wegen
# classification_runs_one_active_idx jeden weiteren Start. Darum kurz warten,
# statt sofort aufzugeben.
_RUN_VISIBILITY_ATTEMPTS = 10
_RUN_VISIBILITY_DELAY_SECONDS = 1.0

# Notaus fuer aussichtslose Laeufe: zwei gleichartige Fehler unmittelbar
# hintereinander sind kein Zufall mehr, sondern ein Zustand (Modell weg,
# Zeitbudget zu knapp, Ausgabebudget erschoepft). Zwei statt drei, weil jeder
# weitere Versuch bei einem Timeout das volle Zeitbudget kostet: bei 171
# offenen Buchungen und 60s Timeout waeren das knapp drei Stunden Warten auf
# ein Ergebnis, das schon feststeht.
_CONSECUTIVE_ERROR_LIMIT = 2

# Nur diese Reason Codes stehen fuer einen fehlgeschlagenen Modellaufruf und
# duerfen den Notaus ausloesen. Die Liste kommt aus dem KI-Kern
# (app/llm/classification.py::FAILURE_REASON_CODES - genau die Codes, mit
# denen classify_one() einen kaputten Aufruf meldet) und steht bewusst nicht
# als Praefixregel ("llm_"), weil der Breaker einen laufenden Lauf beendet und
# dabei nicht raten darf. llm_token_budget_exceeded bleibt fuer Bestandsdaten
# darin, neue Laeufe melden dafuer model_budget_exhausted.
#
# Bewusst NICHT enthalten:
#   * NO_MATCH_REASON_CODE - "keine passende Kategorie" ist eine richtige
#     Antwort (siehe der Kommentar in der Schleife weiter unten). Ein Modell,
#     das den Vertrag sauber einhaelt, wuerde den Lauf sonst selbst abwuergen.
#   * STALE_CATEGORY_REASON_CODE - das Modell hat geantwortet, nur war die
#     gewaehlte Kategorie seit dem Laden der Kandidaten archiviert oder
#     gemergt. Das ist ein Datenrennen im Haushalt, kein Transportproblem;
#     ein Abbruch wuerde hier nichts verbessern.
_BREAKER_REASON_CODES = FAILURE_REASON_CODES | frozenset({"llm_token_budget_exceeded"})

# Klartext fuer die Abbruchmeldung: der Reason Code ist ein interner Schluessel
# und taugt nicht als Erklaerung im Aufgabenprotokoll.
_BREAKER_REASON_LABELS = {
    "llm_timeout": "Zeitueberschreitung",
    "llm_schema_invalid": "keine gueltige Antwortform",
    "llm_token_budget_exceeded": "Antwort abgeschnitten, Ausgabebudget erschoepft",
    "llm_execution_error": "Fehler beim Modellaufruf",
    reasons.MODEL_BUDGET_EXHAUSTED: "Antwortbudget aufgebraucht, meist durch das Denken",
    reasons.MODEL_CONTEXT_OVERFLOW: "Kontextfenster uebergelaufen",
    reasons.MODEL_INPUT_TOO_LARGE: "Eingabe passt nicht ins Kontextfenster",
    reasons.MODEL_CONTEXT_UNKNOWN: "Kontextfenster unbekannt",
    reasons.MODEL_REASONING_UNSUPPORTED: "Reasoning-Einstellung vom Server abgelehnt",
    reasons.MODEL_SETTINGS_INVALID: "KI-Einstellung fuer das Budget fehlt",
    reasons.AI_DISABLED: "KI ausgeschaltet",
}


async def _claim_run(session: AsyncSession, run_id: UUID) -> tuple[UUID, date] | None:
    """Wartet auf die Sichtbarkeit der Laufzeile und uebernimmt sie
    ('pending' -> 'running'). Gibt Haushalt und historisches Startmetadatum
    zurueck, oder None, wenn es den Lauf nicht gibt bzw. er nicht mehr zu uebernehmen ist
    (bereits uebernommen, abgebrochen, abgeschlossen)."""
    for attempt in range(_RUN_VISIBILITY_ATTEMPTS):
        exists = (
            await session.execute(
                text("select 1 from classification_runs where id = :id"),
                {"id": str(run_id)},
            )
        ).first()
        if exists is not None:
            break
        await session.rollback()
        if attempt == _RUN_VISIBILITY_ATTEMPTS - 1:
            return None
        await asyncio.sleep(_RUN_VISIBILITY_DELAY_SECONDS)

    # Der Statusfilter im UPDATE ist zugleich die Sperre gegen doppelte
    # Verarbeitung: nur genau einer kann 'pending' -> 'running' drehen.
    claimed = (
        await session.execute(
            text(
                "update classification_runs set status = 'running', "
                "current_stage = 'preparing', heartbeat_at = now() "
                "where id = :id and status = 'pending' and not cancel_requested "
                "returning household_id, since_date"
            ),
            {"id": str(run_id)},
        )
    ).first()
    await session.commit()
    if claimed is None:
        return None
    return claimed.household_id, claimed.since_date


async def _finish_run(
    session: AsyncSession, run_id: UUID, status: str, error_message: str | None = None
) -> None:
    # `and status = 'running'` ist die Sperre gegen ein Wiederauferstehen: hat
    # die API den Lauf zwischenzeitlich hart beendet (Sofortabbruch), darf der
    # Worker ihn nicht nachtraeglich auf 'completed' drehen.
    await session.execute(
        text(
            "update classification_runs set status = :status, heartbeat_at = now(), "
            "completed_at = now(), error_message = coalesce(:err, error_message), "
            # Nur ein regulaer beendeter Lauf erreicht die Endstufe. Bei
            # 'failed'/'cancelled' bleibt sichtbar, wo er stehen geblieben ist.
            "current_stage = case when :status = 'completed' then 'completed' "
            "else current_stage end where id = :id and status = 'running'"
        ),
        {"status": status, "err": error_message, "id": str(run_id)},
    )
    await session.commit()


async def _is_cancel_requested(session: AsyncSession, run_id: UUID) -> bool:
    """Wahr, sobald der Lauf nicht mehr weiterlaufen soll.

    Neben der Bitte (`cancel_requested`) zaehlt auch ein von aussen bereits
    beendeter Lauf: der Sofortabbruch setzt den Status direkt auf 'cancelled',
    und der Worker darf dann keine weitere Buchung mehr klassifizieren."""
    row = (
        await session.execute(
            text("select cancel_requested, status from classification_runs where id = :id"),
            {"id": str(run_id)},
        )
    ).first()
    if row is None:
        return True
    return bool(row.cancel_requested) or row.status != "running"


async def process_classification_run(
    session: AsyncSession, run_id: UUID, ai_task_id: UUID | None = None
) -> None:
    claimed = await _claim_run(session, run_id)
    if claimed is None:
        return
    household_id, _since_date = claimed

    try:
        rows = (
            await session.execute(
                text(_UNCLASSIFIED_SQL), {"household_id": str(household_id)}
            )
        ).all()
        # Der lokale Vorfilter steht VOR jeder Modell-Discovery und vor dem
        # Canary. Damit funktionieren bestaetigte Empfaenger auch dann, wenn
        # LM Studio aus, das Modell zu gross oder sein Kontextfenster falsch
        # geladen ist.
        known_payees = await load_known_payees(session, household_id)
        remaining_rows: list[Any] = []
        await session.execute(
            text(
                "update classification_runs set total_count = :total, "
                "current_stage = 'preparing', heartbeat_at = now() where id = :id"
            ),
            {"total": len(rows), "id": str(run_id)},
        )
        if ai_task_id is not None:
            await session.execute(
                text(
                    "update ai_tasks set progress_total = :total, "
                    "status_message = 'Bekannte Empfaenger werden geprueft', "
                    "heartbeat_at = now(), updated_at = now() "
                    "where id = :task_id and household_id = :household_id "
                    "and status = 'running'"
                ),
                {
                    "total": len(rows),
                    "task_id": str(ai_task_id),
                    "household_id": str(household_id),
                },
            )
        await session.commit()

        for row in rows:
            if await _is_cancel_requested(session, run_id):
                await _finish_run(session, run_id, "cancelled", "Vom Nutzer abgebrochen.")
                return
            known_result = await classify_known_payee(
                session, household_id, row.id, row.payee_label, known_payees, ai_task_id
            )
            if known_result is None:
                remaining_rows.append(row)
                continue
            await session.execute(
                text(
                    "update classification_runs set processed_count = processed_count + 1, "
                    "success_count = success_count + :s, heartbeat_at = now() where id = :id"
                ),
                {"s": 1, "id": str(run_id)},
            )
            if ai_task_id is not None:
                await session.execute(
                    text(
                        "update ai_tasks set progress_current = progress_current + 1, "
                        "heartbeat_at = now(), updated_at = now() "
                        "where id = :task_id and household_id = :household_id "
                        "and status = 'running'"
                    ),
                    {
                        "task_id": str(ai_task_id),
                        "household_id": str(household_id),
                    },
                )
            await session.commit()

        if not remaining_rows:
            await _finish_run(session, run_id, "completed")
            return

        # Einstellungen, Modellwahl und geladenes Fenster dieses Haushalts -
        # EINMAL je Lauf, aus dem KI-Kern (dieselbe Quelle wie die API).
        settings = await load_settings(session, household_id)

        async def fail_before_start(message: str) -> None:
            await session.execute(
                text("update classification_runs set total_count = :total where id = :id"),
                {"total": len(rows), "id": str(run_id)},
            )
            await _finish_run(session, run_id, "failed", message)

        found = await resolve_lm_studio_endpoint(settings)
        if found.target is None or found.reason_code is not None:
            # KI aus (ohne Serverkontakt), Server weg, Modell fehlt - oder das
            # geladene Fenster ist unbekannt. Ohne Fenster laesst sich kein
            # Antwortbudget rechnen; der ganze Lauf scheitert deshalb hier,
            # benannt und einmal.
            await fail_before_start(
                found.message or reasons.describe(reasons.MODEL_UNAVAILABLE)
            )
            return
        target = found.target

        # Kandidatenliste, erlaubte IDs und Taxonomieversion wandern als EIN
        # Objekt durch den Lauf - sonst koennte eine Auswertung Trefferquoten
        # der falschen Taxonomie zuordnen.
        candidates = await load_categories(session, household_id)

        # Vorabtest EINMAL je Lauf mit dem echten Prompt (alle Kategorien):
        # er prueft die Antwortform UND ob die Liste samt Antwortreserve ins
        # geladene Fenster passt. Das Ergebnis wird persistiert, damit ein
        # Fallback auf Prompt+Parse nachvollziehbar bleibt.
        canary = await probe_model_capability(
            target, settings, candidates, session=session, household_id=household_id
        )
        await record_model_capability(
            session,
            household_id,
            target.model,
            PROMPT_VERSION,
            supports_json_schema=canary.supports_json_schema,
            canary_passed=canary.passed,
            last_error=None if canary.passed else canary.message,
        )

        # Wer am Vorabtest scheitert, scheitert erst recht an echten
        # Verwendungszwecken - der Lauf haette nur den Nutzer eine Stunde
        # warten lassen, um am Ende jede Buchung als Fehler zu melden.
        # record_model_capability() lief bereits und wird von _finish_run()
        # mitcommittet - die Statusanzeige findet den Grund also auch dann.
        if not canary.passed:
            await fail_before_start(
                f"Das Modell „{target.model}“ hat den Vorabtest nicht bestanden: "
                f"{canary.message or reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE)}"
            )
            return

        await session.execute(
            text(
                "update classification_runs set total_count = :total, "
                "current_stage = 'classifying', heartbeat_at = now() where id = :id"
            ),
            {"total": len(rows), "id": str(run_id)},
        )
        if ai_task_id is not None:
            await session.execute(
                text(
                    "update ai_tasks set current_stage = 'classifying', "
                    "progress_total = :total, status_message = 'Buchungen werden geprueft', "
                    "heartbeat_at = now(), updated_at = now() "
                    "where id = :task_id and household_id = :household_id "
                    "and status = 'running'"
                ),
                {
                    "total": len(rows),
                    "task_id": str(ai_task_id),
                    "household_id": str(household_id),
                },
            )
        await session.commit()

        # Gedaechtnis des Notaus: welcher Fehler laeuft gerade wie oft
        # ununterbrochen? Leerer Schluessel heisst "keine offene Kette" - ein
        # Reason Code ist nie leer, die Unterscheidung bleibt also eindeutig.
        streak_reason = ""
        streak_length = 0
        processed_count = len(rows) - len(remaining_rows)

        for row in remaining_rows:
            # Abbruch zwischen zwei Buchungen: nie mitten in einer laufenden
            # Klassifikation, damit kein halber Schreibvorgang zurueckbleibt.
            if await _is_cancel_requested(session, run_id):
                await _finish_run(session, run_id, "cancelled", "Vom Nutzer abgebrochen.")
                return

            # Der echte Bank-Verwendungszweck ist purpose_raw. `note` bleibt
            # nur Rueckfall fuer manuell angelegte Buchungen ohne Rohfeld.
            purpose = getattr(row, "purpose_raw", None) or row.note
            result = await classify_transaction(
                session,
                household_id,
                row.id,
                getattr(row, "payee_text", None) or row.payee_label,
                purpose,
                row.amount_minor,
                target,
                candidates,
                canary.supports_json_schema,
                settings,
                ai_task_id,
            )
            # "Kein passender Treffer" ist seit dem no_match-Vertrag eine
            # richtige Antwort und kein Fehler (Kategorisierungsplan,
            # Leitplanke 8: "KI darf vorschlagen, nicht erfinden"). Wuerde sie
            # als error_count gezaehlt, sabotierte ein sauber arbeitendes
            # Modell die eigene Laufstatistik - und der Nutzer bekaeme fuer
            # korrektes Verhalten eine Fehlermeldung angezeigt. Ein echter
            # Fehler (Timeout, ungueltiges Schema) liefert einen anderen
            # reason_code und bleibt damit zaehlbar.
            no_match = (
                result.category_id is None and result.reason_code == NO_MATCH_REASON_CODE
            )
            success = 1 if (result.category_id is not None or no_match) else 0

            # Dieselbe Unterscheidung noch einmal fuer den Notaus, nur
            # strenger: gezaehlt wird ausschliesslich, was ein kaputter
            # Modellaufruf ist. Ein Erfolg - und dazu zaehlt no_match -
            # loescht die Kette, denn dann antwortet das Modell ja. Ein
            # Fehler, der nicht am Transport liegt (archivierte Kategorie),
            # neutralisiert die Kette ebenfalls, statt sie zu verlaengern:
            # sonst koennten zwei voellig verschiedene Ursachen gemeinsam
            # einen Abbruch ausloesen, den keine von beiden rechtfertigt.
            #
            # Eine vom Vorfilter beantwortete Buchung bleibt hier bewusst
            # aussen vor: sie hat das Modell nie erreicht und sagt deshalb
            # weder aus, dass es antwortet, noch dass es schweigt. Wuerde sie
            # die Kette loeschen, koennte ein einzelner bekannter Empfaenger
            # zwischen zwei Timeouts den Notaus aushebeln - der Lauf liefe
            # wieder stundenlang ins Leere.
            if success or result.reason_code not in _BREAKER_REASON_CODES:
                streak_reason = ""
                streak_length = 0
            else:
                streak_length = streak_length + 1 if result.reason_code == streak_reason else 1
                streak_reason = result.reason_code

            await session.execute(
                text(
                    "update classification_runs set processed_count = processed_count + 1, "
                    "success_count = success_count + :s, error_count = error_count + :e, "
                    "heartbeat_at = now() where id = :id"
                ),
                {"s": success, "e": 1 - success, "id": str(run_id)},
            )
            if ai_task_id is not None:
                await session.execute(
                    text(
                        "update ai_tasks set progress_current = progress_current + 1, "
                        "heartbeat_at = now(), updated_at = now() "
                        "where id = :task_id and household_id = :household_id "
                        "and status = 'running'"
                    ),
                    {
                        "task_id": str(ai_task_id),
                        "household_id": str(household_id),
                    },
                )
            await session.commit()
            processed_count += 1

            # Erst NACH dem Commit pruefen: die gerade verarbeitete Zeile ist
            # damit dauerhaft gezaehlt, und der Abbruch verwirft nichts von
            # dem, was der Lauf bereits geschafft hat.
            if streak_length >= _CONSECUTIVE_ERROR_LIMIT:
                label = _BREAKER_REASON_LABELS.get(streak_reason, streak_reason)
                await _finish_run(
                    session,
                    run_id,
                    "failed",
                    (
                        f"Abgebrochen nach {streak_length} gleichartigen Fehlern "
                        f"hintereinander ({label}). {processed_count} von {len(rows)} "
                        "Buchungen wurden verarbeitet, ihre Ergebnisse bleiben "
                        "erhalten. Der Rest wurde nicht mehr an das Modell geschickt."
                    ),
                )
                return

        await _finish_run(session, run_id, "completed")

    except Exception as exc:
        # Bereits committete Zeilen (processed_count etc.) bleiben erhalten -
        # nur der aktuelle, noch nicht committete Schritt wird zurueckgerollt.
        await session.rollback()
        await _finish_run(session, run_id, "failed", str(exc))
