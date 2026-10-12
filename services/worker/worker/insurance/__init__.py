"""Versicherungsseitige Worker-Laeufe (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 8).

Dieses Paket enthaelt den naechtlichen Versicherungslauf (`scan.py`). Der
zugehoerige Push liegt bei den uebrigen Benachrichtigungen in
`worker/notifications/insurance_alerts.py` - dort, wo `contract_alerts.py`
und `due_recurring.py` schon stehen.

**Warum hier der Fristenrechner der API benutzt wird und kein zweiter.**
Der Fristenkern ist `services/api/app/services/insurance_deadline_service.py`
(Plan 8.1: "ein Fristenrechner"). Ein zweiter im Worker lieferte fuer dieselbe
Police auf `/versicherungen` und im Push verschiedene Kuendigungstermine - der
einzige Fehler, den der Nutzer sofort als Widerspruch sieht. Der Weg zum
Original ist derselbe wie in `worker/contracts/reconcile.py`: `app.*` kommt
ueber PYTHONPATH (services/worker/Dockerfile setzt `PYTHONPATH=/app/services/api`
und kopiert `services/api/app` mit).

Der eigentliche Import steht bewusst IN der Funktion und nicht auf Modulebene:
`worker.actors` wird auch vom API-Prozess importiert (nur zum Einreihen von
Nachrichten), und ein Import auf Modulebene zoege `services/api` in jedem
dieser Prozesse mit - dieselbe Begruendung wie in `reconcile.py:39-45`.

`PolicyDeadline` ist ausdruecklich KEIN zweiter Rechner, sondern nur die
Uebersetzung des Ergebnisses in eine Form, die mypy im Worker kennt: der
Import aus `app.services` ist hier unaufloesbar (`ignore_missing_imports`), das
Ergebnis waere sonst ueberall `Any` und jede Feldverwechslung bliebe stumm.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PolicyDeadline:
    """Fristergebnis einer Police, verkuerzt auf das, was Lauf und Push brauchen.

    `certainty` gehoert bewusst dazu und nicht in die Anzeige: ein angenommenes
    Datum darf nirgends wie ein bestaetigtes aussehen (Plan, Leitsatz 1). Der
    Push liest genau dieses Feld, statt die Herkunftsspalten selbst zu
    interpretieren.
    """

    certainty: str
    term_ends_on: date | None
    deadline_legal_on: date | None
    safe_service_on: date | None
    recommended_action_on: date | None
    days_left: int | None
    reason: str


def compute_policy_deadline(policy: Mapping[str, Any], *, today: date) -> PolicyDeadline:
    """Rechnet die Frist einer Policenzeile mit dem Kern aus `services/api`.

    Die Argumentliste ist deckungsgleich mit `insurance_service._deadline`
    (services/api). Weicht sie ab, rechnet der Push etwas anderes als die
    Detailseite - deshalb steht hier keine einzige eigene Herleitung, auch
    keine scheinbar harmlose Vorbelegung eines fehlenden Feldes.
    """
    from app.services.insurance_deadline_service import (
        compute_insurance_deadline,
        min_term_end_on,
    )

    computed = compute_insurance_deadline(
        anchor=policy["notice_anchor"],
        deadline_kind=policy["deadline_kind"],
        today=today,
        main_due_on=policy.get("main_due_on"),
        notice_period_months=policy.get("notice_period_months"),
        notice_fixed_month=policy.get("notice_fixed_month"),
        notice_fixed_day=policy.get("notice_fixed_day"),
        service_channel=policy.get("service_channel") or "post",
        main_due_source=policy["main_due_source"],
        notice_terms_source=policy["notice_terms_source"],
        status=policy["status"],
        ends_on=policy.get("ends_on"),
        # Empfaengerland und Mindestbindung gehoeren MIT in die Rechnung: die
        # Staffel zaehlt auf `recommended_action_on`, und ein Feiertag am Sitz
        # des Versicherers verschiebt genau dieses Datum. Ohne sie erinnerte der
        # Push an einen anderen Tag als die Detailseite ihn nennt.
        recipient_state=policy.get("recipient_state"),
        min_term_ends_on=min_term_end_on(
            policy.get("starts_on") or policy.get("contract_starts_on"),
            policy.get("min_term_months"),
        ),
    )
    return PolicyDeadline(
        certainty=str(computed.certainty),
        term_ends_on=computed.term_ends_on,
        deadline_legal_on=computed.deadline_legal_on,
        safe_service_on=computed.safe_service_on,
        recommended_action_on=computed.recommended_action_on,
        days_left=computed.days_left,
        reason=str(computed.reason),
    )
