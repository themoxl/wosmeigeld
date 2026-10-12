"""Beitragsanpassungen und Beitragsverlauf (docs/VERSICHERUNGEN-PLAN.md, P3).

Vier Regeln erklaeren fast jede Entscheidung in dieser Datei:

1. **Der Fristanker ist der ZUGANG, nicht das Wirksamwerden.** Paragraph 40
   Abs. 1 S. 1 VVG laesst die Monatsfrist ab Zugang der Mitteilung laufen. Bei
   der ueblichen Ankuendigung im November zum 1. Januar liegen dazwischen acht
   Wochen - genau die Zeit, die eine Rechnung ab `effective_on` dem Nutzer
   wegnaehme. `effective_on` hat eine andere Rolle: es ist das FRUEHESTE
   Beendigungsdatum, weil die Kuendigung "mit sofortiger Wirkung, fruehestens
   jedoch zum Zeitpunkt des Wirksamwerdens der Erhoehung" wirkt.
2. **Hier entsteht kein Geld.** Eine erfasste Mitteilung ist der Inhalt eines
   Anschreibens. Erst `confirm` schreibt den Betrag fort, und zwar ueber die
   VERTRAGSSEITE (`contract_service.open_amount_stage`) - nie in eine zweite
   Betragsquelle. Ohne Vertrag bleibt der Informationsbetrag des fremden
   Zahlers die einzige Fortschreibung, und die beruehrt die Fixkosten nicht.
3. **Nicht jede Erhoehung ist eine Erhoehung im Sinne des Gesetzes.** Eine
   vereinbarte Dynamik ist keine (Leitsatz 3), eine Erhoehung mit
   Leistungserweiterung ist keine, und im Kfz-Fall entscheidet der ausgewiesene
   VERGLEICHSBEITRAG - dort kann der Rechnungsbetrag sinken und das Recht
   trotzdem bestehen.
4. **Es gibt genau einen Fristenrechner.** Werktags-, Zugangs- und
   Monatsfragen beantwortet `insurance_deadline_service`; hier steht nur, wo
   die Frist beginnt und wie lang sie ist.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final, Literal, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import VERSION_CONFLICT, entity_etag
from app.schemas.insurance import (
    PolicyReplacement,
    PolicyReplaceRequest,
    PremiumHistory,
    PremiumHistoryStep,
    PremiumNotice,
    PremiumNoticeConfirm,
    PremiumNoticeConfirmResult,
    PremiumNoticeCreate,
    PremiumNoticeUpdate,
    SpecialTerminationWindow,
)
from app.services import contract_service, insurance_service
from app.services.insurance_deadline_service import (
    _CHANNEL_BUFFER_BUSINESS_DAYS,
    add_months,
    minus_delivery_days,
    previous_delivery_day,
)

# ---------------------------------------------------------------------------
# Fehlercodes
# ---------------------------------------------------------------------------

INSURANCE_NOTICE_NOT_FOUND: Final = "insurance_premium_notice_not_found"
INSURANCE_NOTICE_MISSING_RECEIVED_ON: Final = "insurance_notice_missing_received_on"
INSURANCE_NOTICE_DUPLICATE: Final = "insurance_premium_notice_duplicate"
INSURANCE_NOTICE_DATE_ORDER: Final = "insurance_premium_notice_date_order"
# Eine bestaetigte Mitteilung haengt an einer Preisstufe im Vertragsverlauf.
# Sie stillschweigend zu aendern oder zu loeschen hinterliesse den Betrag ohne
# seinen Beleg - die zweite Wahrheit, die dieses Modul sonst ueberall vermeidet.
INSURANCE_NOTICE_ALREADY_CONFIRMED: Final = "insurance_premium_notice_already_confirmed"
INSURANCE_NOTICE_REQUIRES_EXTERNAL_PAYER: Final = (
    "insurance_premium_notice_requires_external_payer"
)
INSURANCE_NOTICE_SINGLE_PREMIUM: Final = "insurance_premium_notice_single_premium"
INSURANCE_NOTICE_FREQUENCY_MISMATCH: Final = "insurance_premium_notice_frequency_mismatch"
# Ein ausdrueckliches `null` auf einem Feld, das die Mitteilung braucht. Es
# kommt beim PATCH an, weil alle Felder in `PremiumNoticeUpdate` als optional
# deklariert sind - "nicht gesendet" und "auf null gesetzt" sind dort dieselbe
# Gestalt. Ohne diesen Code liefe der Loeschwunsch in die Fensterrechnung und
# scheiterte dort an einem Vergleich gegen None: derselbe Bedienfehler
# antwortete je nach Feld mit 422 (NOT NULL der Tabelle) oder 500.
INSURANCE_NOTICE_FIELD_NOT_CLEARABLE: Final = "insurance_premium_notice_field_not_clearable"
INSURANCE_REPLACEMENT_TARGET_INVALID: Final = "insurance_replacement_target_invalid"

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Statuswerte, in denen eine Beitragsanpassung ueberhaupt ein Fenster erzeugen
# kann. 'paid_up' fehlt bewusst: eine beitragsfrei gestellte Police zahlt keinen
# Beitrag, der angepasst werden koennte (Plan 4.15, Punkt 3).
_WINDOW_STATUSES: Final = ("active", "suspended", "dormant")

# Fristlaenge je Sonderkuendigungsrecht der Sparte. 'none' steht fuer Sparten
# ohne Sonderkuendigungsrecht und taucht deshalb hier nicht auf.
_SPECIAL_LAW_MONTHS: Final[dict[str, int]] = {"vvg40": 1, "vvg205": 2}

_SPECIAL_LAW_BASIS: Final[dict[str, str]] = {
    "vvg40": "Paragraph 40 Abs. 1 S. 1 VVG",
    "vvg205": "Paragraph 205 Abs. 4 VVG",
}

# Zahlweisen, fuer die ein Jahreswert ueberhaupt definiert ist. 'single' fehlt:
# ein Einmalbeitrag hat keinen Jahresbeitrag, und ihn als solchen in eine
# Treppe zu stellen erfaende eine wiederkehrende Belastung.
_YEARLY_FREQUENCIES: Final = ("weekly", "monthly", "quarterly", "semiannual", "yearly")

_NOTICE_UPDATABLE_COLUMNS: Final = (
    "received_on",
    "effective_on",
    "old_amount_minor",
    "new_amount_minor",
    "comparison_amount_minor",
    "frequency",
    "with_benefit_increase",
    "reason",
)

# Die Teilmenge davon, die eine Mitteilung nicht verlieren kann. Sie ist genau
# das Gegenstueck zu `PremiumNoticeUpdate.clear_fields`: dort stehen die beiden
# Felder, die geleert werden DUERFEN (`comparison_amount_minor`, `reason`), hier
# die uebrigen. `received_on` steht vorn, weil es der Fristanker ist - eine
# Mitteilung ohne Zugangsdatum ist keine Mitteilung mehr, sondern eine
# Rueckfrage, und der Weg dorthin ist DELETE.
_NOTICE_REQUIRED_COLUMNS: Final = (
    "received_on",
    "effective_on",
    "old_amount_minor",
    "new_amount_minor",
    "frequency",
    "with_benefit_increase",
)

_NOTICE_INSERT_COLUMNS: Final = (
    "household_id",
    "policy_id",
    *_NOTICE_UPDATABLE_COLUMNS,
    "special_termination_until",
    "safe_service_on",
    "recommended_action_on",
    "earliest_effect_on",
)

_NOTICE_COLUMNS: Final = """
  n.id, n.policy_id, n.received_on, n.effective_on, n.old_amount_minor,
  n.new_amount_minor, n.comparison_amount_minor, n.frequency,
  n.with_benefit_increase, n.reason, n.amount_history_id, n.confirmed_at,
  n.acknowledged_at, n.version, n.created_at, n.updated_at
"""

# Der Policenkontext reist bei jeder Mitteilung mit: ohne `special_law`,
# `dynamic_agreed`, `service_channel` und `recipient_state` ist das Fenster
# nicht zu rechnen, und ein zweiter Rundlauf je Zeile waere N+1.
_POLICY_CONTEXT_COLUMNS: Final = """
  p.insurer_name, p.policy_number, p.status as policy_status,
  p.starts_on as policy_starts_on, p.ends_on as policy_ends_on,
  p.prior_cover_since, p.predecessor_policy_id, p.dynamic_agreed,
  p.service_channel, p.recipient_state, p.contract_id,
  p.external_premium_minor, p.external_frequency, p.premium_payer,
  p.version as policy_version, l.special_law,
  c.name as contract_name, c.frequency as contract_frequency,
  c.status as contract_status, c.amount_minor as contract_amount_minor,
  coalesce(c.version, 0) as contract_version
"""

_POLICY_CONTEXT_FROM: Final = """
  from insurance_policies p
  join insurance_lines l on l.line_key = p.line_key
  left join contracts c
    on c.id = p.contract_id and c.household_id = p.household_id
   and c.merged_into_id is null
"""

_NOTICE_FROM: Final = """
  from insurance_premium_notices n
  join insurance_policies p
    on p.id = n.policy_id and p.household_id = n.household_id
  join insurance_lines l on l.line_key = p.line_key
  left join contracts c
    on c.id = p.contract_id and c.household_id = p.household_id
   and c.merged_into_id is null
"""


# ---------------------------------------------------------------------------
# Das Sonderkuendigungsfenster
# ---------------------------------------------------------------------------


def _delivery_buffer_days(service_channel: str) -> int:
    """Versandpuffer bis zum sicheren Zugang, in Zustelltagen.

    Bezieht die Staffel bewusst aus dem Rechenkern statt sie zu wiederholen.
    Eine zweite Tabelle "Post drei Tage, E-Mail null" ergaebe frueher oder
    spaeter zwei verschiedene Handlungsdaten fuer dieselbe Police - je nachdem,
    ob die ordentliche Frist oder das Sonderkuendigungsfenster gefragt wird.
    Der Zugriff auf den modulinternen Namen ist der Preis dafuer und die
    kleinere Abweichung.
    """
    return _CHANNEL_BUFFER_BUSINESS_DAYS.get(service_channel, 3)


def _yearly_minor(amount_minor: int, frequency: str) -> int | None:
    """Jahreswert einer Zahlweise, oder ``None`` wo es keinen gibt."""
    if frequency not in _YEARLY_FREQUENCIES:
        return None
    return contract_service.yearly_minor(amount_minor, frequency)


def _no_window(reason: str, *, effective_on: date | None = None) -> SpecialTerminationWindow:
    """Kein Sonderkuendigungsrecht - mit Begruendung, nicht als Leerstelle.

    Der Grund gehoert in die Antwort: "kein Fenster" und "Fenster nicht
    gerechnet" saehen sonst identisch aus, und der Nutzer koennte nicht
    unterscheiden, ob sein Recht fehlt oder nur eine Angabe.
    """
    return SpecialTerminationWindow(state="none", earliest_effect_on=effective_on, basis=reason)


def compute_window(
    *,
    today: date,
    received_on: date,
    effective_on: date,
    old_amount_minor: int,
    new_amount_minor: int,
    comparison_amount_minor: int | None,
    with_benefit_increase: bool,
    special_law: str,
    dynamic_agreed: bool,
    policy_status: str,
    service_channel: str,
    recipient_state: str | None,
) -> SpecialTerminationWindow:
    """Rechnet das Sonderkuendigungsfenster einer Beitragsanpassung.

    Gibt IMMER ein Ergebnis zurueck, nie ``None``: "kein Recht" ist eine
    Aussage, kein fehlender Wert. Die Reihenfolge der Ausschluesse ist
    absichtlich - sie nennt dem Nutzer den ERSTEN Grund, aus dem sein Recht
    nicht besteht, und der spezifischste steht vorn.
    """
    if policy_status not in _WINDOW_STATUSES:
        # Drei Saetze statt zweier: 'draft' und 'applied' sind "noch nicht",
        # nicht "nicht mehr". Der Antrag laeuft, der Schutz beginnt erst - wer
        # dort "nicht mehr aktiv" liest, haelt seine Police fuer beendet.
        if policy_status == "paid_up":
            reason = (
                "Beitragsfrei gestellte Police: es fliesst kein Beitrag, der angepasst "
                "werden koennte."
            )
        elif policy_status in ("draft", "applied"):
            reason = (
                "Die Police ist noch nicht in Kraft - vor dem Versicherungsbeginn gibt es "
                "keinen Beitrag, der angepasst werden koennte."
            )
        elif policy_status == "entered_in_error":
            reason = "Diese Police ist als Fehleintrag gekennzeichnet."
        else:
            reason = "Die Police ist nicht mehr aktiv."
        return _no_window(reason, effective_on=effective_on)

    if special_law == "none":
        return _no_window(
            "Diese Sparte kennt kein Sonderkuendigungsrecht nach einer Beitragsanpassung.",
            effective_on=effective_on,
        )

    if dynamic_agreed:
        # Leitsatz 3: eine planmaessige Dynamik ist keine Erhoehung im Sinne des
        # Paragraphen 40 VVG. Sie erzeugt weder Fenster noch Rueckfrage.
        return _no_window(
            "Vereinbarte Dynamik - eine planmaessige Erhoehung ist keine Erhoehung "
            "im Sinne des Paragraphen 40 VVG.",
            effective_on=effective_on,
        )

    if with_benefit_increase:
        return _no_window(
            "Paragraph 40 Abs. 1 S. 1 VVG setzt eine Erhoehung ohne Aenderung des "
            "Versicherungsschutzes voraus.",
            effective_on=effective_on,
        )

    # KFZ-FALL: ist ein Vergleichsbeitrag ausgewiesen, wird gegen IHN geprueft
    # und nicht gegen den zuletzt gezahlten Beitrag. Sonst verschluckte die
    # jaehrliche SF-Hochstufung entweder ein bestehendes Recht (Rechnungsbetrag
    # sinkt, liegt aber ueber dem Vergleichsbeitrag) oder erfaende eines
    # (Rechnungsbetrag steigt, bleibt aber unter dem Vergleichsbeitrag).
    baseline = comparison_amount_minor if comparison_amount_minor is not None else old_amount_minor
    if new_amount_minor <= baseline:
        return _no_window(
            (
                "Die neue Praemie liegt nicht ueber dem ausgewiesenen Vergleichsbeitrag."
                if comparison_amount_minor is not None
                else "Der Beitrag steigt nicht - ohne Erhoehung kein Sonderkuendigungsrecht."
            ),
            effective_on=effective_on,
        )

    months = _SPECIAL_LAW_MONTHS.get(special_law, 1)
    # Paragraphen 187 Abs. 1, 188 Abs. 2 BGB: der Zugangstag zaehlt nicht mit,
    # die Frist endet am Tag mit derselben Zahl im Folgemonat. Paragraph 193 BGB
    # schiebt NICHT - der Fristtyp ist 'kuendigungsfrist' (BGH III ZR 172/04),
    # und ein Schub kehrte die Schutzrichtung um.
    deadline_legal_on = add_months(received_on, months)
    safe_service_on = previous_delivery_day(deadline_legal_on, recipient_state)
    buffer_days = _delivery_buffer_days(service_channel)
    recommended_action_on = (
        minus_delivery_days(safe_service_on, buffer_days, recipient_state)
        if buffer_days
        else safe_service_on
    )

    # "Verstrichen" haengt am rechtlichen Fristende, nicht am Handlungsdatum:
    # am Samstag nach dem empfohlenen Versandtag kann elektronisch immer noch
    # fristwahrend gekuendigt werden.
    expired = today > deadline_legal_on
    return SpecialTerminationWindow(
        state="expired" if expired else "open",
        deadline_legal_on=deadline_legal_on,
        safe_service_on=safe_service_on,
        recommended_action_on=recommended_action_on,
        # Paragraph 40 Abs. 1 S. 1 VVG: fruehestens zum Wirksamwerden der
        # Erhoehung. Das zweite Datum, das die Oberflaeche neben dem Fristende
        # zeigt.
        earliest_effect_on=effective_on,
        days_left=None if expired else (recommended_action_on - today).days,
        basis=(
            f"{_SPECIAL_LAW_BASIS.get(special_law, 'Paragraph 40 Abs. 1 S. 1 VVG')}, "
            f"Zugang {received_on.strftime('%d.%m.%Y')}"
        ),
        basis_kind="statute",
    )


def _window_from(data: dict[str, Any], *, today: date) -> SpecialTerminationWindow:
    return compute_window(
        today=today,
        received_on=cast(date, data["received_on"]),
        effective_on=cast(date, data["effective_on"]),
        old_amount_minor=int(data["old_amount_minor"]),
        new_amount_minor=int(data["new_amount_minor"]),
        comparison_amount_minor=(
            int(data["comparison_amount_minor"])
            if data.get("comparison_amount_minor") is not None
            else None
        ),
        with_benefit_increase=bool(data["with_benefit_increase"]),
        special_law=str(data.get("special_law") or "vvg40"),
        dynamic_agreed=bool(data.get("dynamic_agreed")),
        policy_status=str(data.get("policy_status") or "active"),
        service_channel=str(data.get("service_channel") or "post"),
        recipient_state=cast("str | None", data.get("recipient_state")),
    )


def _as_notice(data: dict[str, Any], *, today: date) -> PremiumNotice:
    old = int(data["old_amount_minor"])
    new = int(data["new_amount_minor"])
    frequency = str(data["frequency"])
    return PremiumNotice(
        **data,
        delta_minor=new - old,
        # Quoten als Basispunkte, wie ueberall im Projekt.
        delta_bp=round((new - old) * 10000 / old),
        yearly_old_minor=contract_service.yearly_minor(old, frequency),
        yearly_new_minor=contract_service.yearly_minor(new, frequency),
        special_termination=_window_from(data, today=today),
    )


# ---------------------------------------------------------------------------
# Nebenlaeufigkeit
# ---------------------------------------------------------------------------


def notice_etag(version: int) -> str:
    """ETag aus der Version der Mitteilung.

    Bewusst NICHT das zusammengesetzte ETag der Police (Plan 4.11): eine
    Mitteilung haengt an keinem Vertrag, und eine parallele Betragsaenderung am
    Vertrag aendert nichts an dem, was in dem Anschreiben steht. Erst `confirm`
    beruehrt beide Seiten - und der ist keine Feldaenderung, sondern eine
    Handlung mit eigener Vorbedingung (`confirmed_at`).
    """
    return entity_etag(version)


def _assert_notice_version(data: dict[str, Any], expected: int | None) -> None:
    current = int(data["version"])
    if expected is not None and expected != current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=VERSION_CONFLICT,
            headers={"ETag": notice_etag(current)},
        )


def _assert_policy_version(data: dict[str, Any], expected: tuple[int, int] | None) -> None:
    current = (int(data["contract_version"]), int(data["policy_version"]))
    if expected is not None and expected != current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=VERSION_CONFLICT,
            headers={"ETag": insurance_service.policy_etag(*current)},
        )


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


async def _require_policy_context(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> dict[str, Any]:
    """Die Policenfelder, die eine Beitragsanpassung braucht.

    Eine eigene, schlanke Abfrage statt des Lesemodells aus
    `insurance_service`: dort fehlen `special_law` und der Vertragsstatus, und
    der Katalogblock samt Zwecktext waere hier totes Gewicht.
    """
    row = (
        await session.execute(
            text(
                f"select p.id, {_POLICY_CONTEXT_COLUMNS} {_POLICY_CONTEXT_FROM} "
                "where p.id = :id and p.household_id = :household_id"
            ),
            {"id": str(policy_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        # 404 statt 403 bei fremdem Haushalt - die Existenz einer fremden
        # Ressource soll nicht verraten werden.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=insurance_service.INSURANCE_POLICY_NOT_FOUND,
        )
    return dict(row._mapping)


async def _require_notice(
    session: AsyncSession, household_id: UUID, notice_id: UUID
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                f"select {_NOTICE_COLUMNS}, {_POLICY_CONTEXT_COLUMNS} {_NOTICE_FROM} "
                "where n.id = :id and n.household_id = :household_id"
            ),
            {"id": str(notice_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_NOTICE_NOT_FOUND
        )
    return dict(row._mapping)


async def list_notices(
    session: AsyncSession, household_id: UUID, policy_id: UUID, *, today: date
) -> list[PremiumNotice]:
    """Der Verlauf der Anpassungsschreiben einer Police, juengster Zugang zuerst."""
    await _require_policy_context(session, household_id, policy_id)
    rows = (
        await session.execute(
            text(
                f"select {_NOTICE_COLUMNS}, {_POLICY_CONTEXT_COLUMNS} {_NOTICE_FROM} "
                "where n.policy_id = :policy_id and n.household_id = :household_id "
                "order by n.received_on desc, n.effective_on desc"
            ),
            {"policy_id": str(policy_id), "household_id": str(household_id)},
        )
    ).all()
    return [_as_notice(dict(row._mapping), today=today) for row in rows]


async def get_notice(
    session: AsyncSession, household_id: UUID, notice_id: UUID, *, today: date
) -> PremiumNotice:
    return _as_notice(await _require_notice(session, household_id, notice_id), today=today)


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


def _translate_integrity_error(exc: IntegrityError) -> HTTPException:
    message = str(getattr(exc, "orig", exc))
    if "insurance_premium_notices_household_id_policy_id_received" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_NOTICE_DUPLICATE
        )
    if "insurance_premium_notices_date_order" in message:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_NOTICE_DATE_ORDER
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="insurance_premium_notice_rejected",
    )


def _window_columns(window: SpecialTerminationWindow) -> dict[str, Any]:
    """Die gerechneten Fristdaten, wie sie in die Tabelle gehen.

    Gespeichert wird nur fuer den Push-Dedup; die API antwortet immer mit dem
    frisch gerechneten Stand. Ohne Fenster stehen alle vier auf null - ein
    gespeichertes Datum ohne Recht waere ein Countdown ohne Anlass.
    """
    if window.state == "none":
        return {
            "special_termination_until": None,
            "safe_service_on": None,
            "recommended_action_on": None,
            "earliest_effect_on": None,
        }
    return {
        "special_termination_until": window.deadline_legal_on,
        "safe_service_on": window.safe_service_on,
        "recommended_action_on": window.recommended_action_on,
        "earliest_effect_on": window.earliest_effect_on,
    }


async def create_notice(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: PremiumNoticeCreate,
    *,
    today: date,
) -> PremiumNotice:
    """Erfasst ein Anpassungsschreiben und rechnet sein Fenster.

    Ohne Zugangsdatum entsteht keine Frist, sondern eine Rueckfrage
    (Leitsatz 4) - deshalb ein eigener Fehlercode und keine Schemaverletzung.
    """
    policy = await _require_policy_context(session, household_id, policy_id)
    if payload.received_on is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_NOTICE_MISSING_RECEIVED_ON,
        )

    window = compute_window(
        today=today,
        received_on=payload.received_on,
        effective_on=payload.effective_on,
        old_amount_minor=payload.old_amount_minor,
        new_amount_minor=payload.new_amount_minor,
        comparison_amount_minor=payload.comparison_amount_minor,
        with_benefit_increase=payload.with_benefit_increase,
        special_law=str(policy.get("special_law") or "vvg40"),
        dynamic_agreed=bool(policy.get("dynamic_agreed")),
        policy_status=str(policy.get("policy_status") or "active"),
        service_channel=str(policy.get("service_channel") or "post"),
        recipient_state=cast("str | None", policy.get("recipient_state")),
    )

    values: dict[str, Any] = {
        "household_id": str(household_id),
        "policy_id": str(policy_id),
        "received_on": payload.received_on,
        "effective_on": payload.effective_on,
        "old_amount_minor": payload.old_amount_minor,
        "new_amount_minor": payload.new_amount_minor,
        "comparison_amount_minor": payload.comparison_amount_minor,
        "frequency": payload.frequency,
        "with_benefit_increase": payload.with_benefit_increase,
        "reason": payload.reason,
        **_window_columns(window),
    }

    try:
        result = await session.execute(
            text(
                "insert into insurance_premium_notices ("
                + ", ".join(_NOTICE_INSERT_COLUMNS)
                + ") values ("
                + ", ".join(f":{column}" for column in _NOTICE_INSERT_COLUMNS)
                + ") returning id"
            ),
            values,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc

    row = result.first()
    if row is None:  # pragma: no cover - "returning id" liefert immer eine Zeile
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insurance_premium_notice_rejected",
        )
    return await get_notice(session, household_id, row.id, today=today)


async def update_notice(
    session: AsyncSession,
    household_id: UUID,
    notice_id: UUID,
    payload: PremiumNoticeUpdate,
    *,
    today: date,
    expected: int | None,
) -> PremiumNotice:
    """Korrigiert eine erfasste Mitteilung und rechnet ihr Fenster neu.

    Eine bereits uebernommene Mitteilung ist gesperrt: ihr Betrag steht als
    Preisstufe im Vertragsverlauf, und eine Korrektur hier machte die beiden
    Zahlen still verschieden. Der Weg zurueck fuehrt ueber die Vertragsseite.
    """
    before = await _require_notice(session, household_id, notice_id)
    _assert_notice_version(before, expected)
    if before.get("confirmed_at") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_NOTICE_ALREADY_CONFIRMED
        )

    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    changes.pop("clear_fields", None)
    # Ein ausdrueckliches `null` auf einem Pflichtfeld ist eine Rueckfrage, keine
    # Ausnahme. Die Pruefung steht VOR der Datums- und der Fensterrechnung, weil
    # beide sonst gegen None vergleichen wuerden - und der Nutzer bekaeme fuer
    # denselben Bedienfehler je nach Feld eine Erklaerung oder einen 500er.
    cleared_required = [
        column for column in _NOTICE_REQUIRED_COLUMNS if changes.get(column, ...) is None
    ]
    if cleared_required:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": INSURANCE_NOTICE_FIELD_NOT_CLEARABLE,
                "fields": cleared_required,
                "hint": (
                    "Diese Angaben kann eine Mitteilung nicht verlieren. Wer sie "
                    "zuruecknehmen will, loescht die Mitteilung."
                ),
            },
        )
    # Ein ausdruecklicher Loeschwunsch ist die spezifischere Angabe und gewinnt
    # gegen einen gleichzeitig gesetzten Wert.
    for cleared in payload.clear_fields:
        changes[cleared] = None

    resulting = {**before, **{key: value for key, value in changes.items() if key in before}}
    if resulting["effective_on"] < resulting["received_on"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=INSURANCE_NOTICE_DATE_ORDER
        )

    params: dict[str, Any] = {"id": str(notice_id), "household_id": str(household_id)}
    assignments: list[str] = []
    for column in _NOTICE_UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        assignments.append(f"{column} = :{column}")
        params[column] = changes[column]

    if not assignments:
        # Ein leerer Patch bumpt nicht - das ETag des Clients bleibt gueltig.
        return _as_notice(before, today=today)

    # Die gespeicherten Fristdaten folgen JEDER Aenderung: ein korrigiertes
    # Zugangsdatum mit altem Fenster waere ein Push auf ein Datum, das niemand
    # mehr behauptet.
    for column, value in _window_columns(_window_from(resulting, today=today)).items():
        assignments.append(f"{column} = :{column}")
        params[column] = value

    try:
        updated = await session.execute(
            text(
                "update insurance_premium_notices set "
                + ", ".join(assignments)
                + " where id = :id and household_id = :household_id"
            ),
            params,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    if cast(CursorResult[Any], updated).rowcount == 0:  # pragma: no cover - RLS-Restrisiko
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_NOTICE_NOT_FOUND
        )
    return await get_notice(session, household_id, notice_id, today=today)


async def delete_notice(session: AsyncSession, household_id: UUID, notice_id: UUID) -> None:
    """Nimmt eine erfasste Mitteilung zurueck.

    Nach der Uebernahme nicht mehr: die Preisstufe im Vertragsverlauf bliebe
    stehen und verloere ihren Beleg. Wer den Betrag zurueckdrehen will, tut das
    dort, wo er steht.
    """
    before = await _require_notice(session, household_id, notice_id)
    if before.get("confirmed_at") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_NOTICE_ALREADY_CONFIRMED
        )
    await session.execute(
        text(
            "delete from insurance_premium_notices "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(notice_id), "household_id": str(household_id)},
    )


async def acknowledge_notice(
    session: AsyncSession, household_id: UUID, notice_id: UUID, *, today: date
) -> PremiumNotice:
    """Frist zur Kenntnis genommen - beendet weitere Erinnerungen.

    `coalesce` statt Ueberschreiben: die Staffel ist eine Eskalation fuer den
    Fall des Nichthandelns, und ein zweiter Aufruf soll den Zeitpunkt der
    ersten Kenntnisnahme nicht nach hinten schieben.
    """
    await _require_notice(session, household_id, notice_id)
    await session.execute(
        text(
            "update insurance_premium_notices "
            "set acknowledged_at = coalesce(acknowledged_at, now()) "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(notice_id), "household_id": str(household_id)},
    )
    return await get_notice(session, household_id, notice_id, today=today)


# ---------------------------------------------------------------------------
# Uebernahme
# ---------------------------------------------------------------------------


async def _confirm_into_contract(
    session: AsyncSession,
    household_id: UUID,
    data: dict[str, Any],
    *,
    amount_minor: int,
    today: date,
) -> tuple[UUID | None, str]:
    """Fall (a): der Haushalt zahlt - der Betrag geht in die Preistreppe.

    Genau EINE Preisstufe, `change_reason = 'manual'`, `valid_from =
    effective_on`. Der Sollbetrag des Vertrags folgt nur, wenn die Erhoehung
    heute schon gilt.
    """
    contract_id = cast(UUID, data["contract_id"])
    contract_frequency = str(data.get("contract_frequency") or "")
    if not contract_frequency:
        # Der Vertrag ist zusammengefuehrt oder weg; die Police zeigt ins Leere.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=insurance_service.INSURANCE_CONTRACT_NOT_FOUND,
        )
    frequency = str(data["frequency"])
    if contract_frequency != frequency:
        # Ein Jahresbetrag in eine Monatsstufe zu schreiben waere ein stiller
        # Faktor zwoelf. Umrechnen waere hier keine Hilfe, sondern eine
        # erfundene Zahl - der Nutzer muss die Zahlweise angleichen.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": INSURANCE_NOTICE_FREQUENCY_MISMATCH,
                "notice_frequency": frequency,
                "contract_frequency": contract_frequency,
            },
        )

    effective_on = cast(date, data["effective_on"])
    await contract_service.open_amount_stage(
        session,
        household_id,
        contract_id,
        amount_minor=amount_minor,
        valid_from=effective_on,
        change_reason="manual",
    )
    stage = (
        await session.execute(
            text(
                "select id from contract_amount_history "
                "where household_id = :household_id and contract_id = :contract_id "
                "and valid_from = :valid_from order by created_at desc limit 1"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(contract_id),
                "valid_from": effective_on,
            },
        )
    ).first()

    hint = ""
    old_amount = int(data.get("contract_amount_minor") or 0)
    if effective_on <= today:
        await session.execute(
            text(
                "update contracts set amount_minor = :amount_minor "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(contract_id),
                "household_id": str(household_id),
                "amount_minor": amount_minor,
            },
        )
    else:
        # Der Sollbetrag folgt NICHT vorab. Bei einer im November erfassten
        # Anpassung zum Januar zaehlte er sonst zwei Monate zu frueh in die
        # Fixkosten, und der Abgleichsmotor meldete jede der beiden
        # dazwischenliegenden Lastschriften als Abweichung.
        hint = (
            "Die Erhoehung gilt erst ab dem Wirksamkeitsdatum. Der Sollbetrag des "
            "Vertrags bleibt bis dahin unveraendert, die Preisstufe steht bereits."
        )

    await contract_service.record_event(
        session,
        household_id,
        contract_id,
        "price_change",
        occurred_on=effective_on,
        payload={
            "old_minor": old_amount,
            "new_minor": amount_minor,
            "delta_minor": amount_minor - old_amount,
            "source": "insurance_premium_notice",
        },
    )
    return (cast(UUID, stage.id) if stage is not None else None), hint


async def _confirm_into_policy(
    session: AsyncSession, household_id: UUID, data: dict[str, Any], *, amount_minor: int
) -> str:
    """Fall (b): ein Dritter zahlt - der Informationsbetrag wird fortgeschrieben."""
    if data.get("external_premium_minor") is None:
        # Fall (c): weder Vertrag noch Zahler. Der Plan verlangt hier
        # ausdruecklich eine Antwort mit Vorschlag statt eines rohen 23514.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": INSURANCE_NOTICE_REQUIRES_EXTERNAL_PAYER,
                "hint": (
                    "Zuerst einen Vertrag verknuepfen oder einen fremden Zahler benennen - "
                    "ohne beides gibt es keinen Beitrag, der fortgeschrieben werden koennte."
                ),
            },
        )
    if str(data.get("external_frequency") or "") == "single":
        # Ein Einmalbeitrag wird nicht angepasst; ein Anpassungsschreiben kann
        # sich nur auf eine wiederkehrende Praemie beziehen.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_NOTICE_SINGLE_PREMIUM,
        )
    # Betrag UND Rhythmus gemeinsam: die Paarbindung
    # insurance_policies_external_triple verbietet das eine ohne das andere.
    await session.execute(
        text(
            "update insurance_policies set external_premium_minor = :amount_minor, "
            "external_frequency = :frequency "
            "where id = :id and household_id = :household_id"
        ),
        {
            "id": str(data["policy_id"]),
            "household_id": str(household_id),
            "amount_minor": amount_minor,
            "frequency": str(data["frequency"]),
        },
    )
    return (
        "Fremdbetrag fortgeschrieben. Er bleibt ein Informationswert und geht weder in "
        "die Preistreppe noch in die Fixkosten ein."
    )


async def confirm_notice(
    session: AsyncSession,
    household_id: UUID,
    notice_id: UUID,
    payload: PremiumNoticeConfirm | None,
    *,
    today: date,
    user_id: UUID,
) -> PremiumNoticeConfirmResult:
    """Schreibt den angekuendigten Betrag fort - ueber die Vertragsseite.

    Leitsatz 1 in einer Funktion: der Beitrag lebt in `contracts`, also oeffnet
    die Bestaetigung dort eine Preisstufe und merkt sich deren Schluessel. Es
    entsteht KEINE zweite Betragsquelle im Versicherungsmodul.
    """
    data = await _require_notice(session, household_id, notice_id)
    if data.get("confirmed_at") is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_NOTICE_ALREADY_CONFIRMED
        )

    amount_minor = (
        payload.amount_minor
        if payload is not None and payload.amount_minor is not None
        else int(data["new_amount_minor"])
    )
    contract_id = cast("UUID | None", data.get("contract_id"))
    amount_history_id: UUID | None = None
    target: Literal["contract", "external"]

    if contract_id is not None:
        amount_history_id, hint = await _confirm_into_contract(
            session, household_id, data, amount_minor=amount_minor, today=today
        )
        target = "contract"
    else:
        hint = await _confirm_into_policy(
            session, household_id, data, amount_minor=amount_minor
        )
        target = "external"

    # Die Uebernahme nimmt die Frist zugleich zur Kenntnis: wer den Betrag
    # fortschreibt, hat die Mitteilung gesehen, und eine weitere Erinnerung an
    # dasselbe Ereignis informierte nicht mehr, sondern stumpfte ab.
    await session.execute(
        text(
            "update insurance_premium_notices set confirmed_at = now(), "
            "confirmed_by = cast(:user_id as uuid), "
            "amount_history_id = cast(:amount_history_id as uuid), "
            "acknowledged_at = coalesce(acknowledged_at, now()) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "id": str(notice_id),
            "household_id": str(household_id),
            "user_id": str(user_id),
            "amount_history_id": str(amount_history_id) if amount_history_id else None,
        },
    )

    return PremiumNoticeConfirmResult(
        notice=await get_notice(session, household_id, notice_id, today=today),
        target=target,
        contract_id=contract_id,
        amount_history_id=amount_history_id,
        valid_from=cast(date, data["effective_on"]),
        amount_minor=amount_minor,
        hint=hint,
    )


# ---------------------------------------------------------------------------
# Beitragsverlauf
# ---------------------------------------------------------------------------

# Die Kette aus Vorgaenger- und Nachfolgepolicen, in beide Richtungen verfolgt:
# wer die alte Police oeffnet, soll denselben Verlauf sehen wie auf der neuen.
# `union` entfernt Wiederholungen, der Tiefendeckel bricht eine im Bestand
# angelegte Schleife ab, statt die Abfrage laufen zu lassen.
_CHAIN_SQL: Final = """
with recursive chain as (
  select p.id, p.predecessor_policy_id, 0 as depth
    from insurance_policies p
   where p.id = :policy_id and p.household_id = :household_id
  union
  select p.id, p.predecessor_policy_id, c.depth + 1
    from insurance_policies p
    join chain c
      on p.id = c.predecessor_policy_id or p.predecessor_policy_id = c.id
   where p.household_id = :household_id and c.depth < 12
)
select p.id, p.insurer_name, p.starts_on, p.ends_on, p.created_at,
       p.contract_id, p.external_premium_minor, p.external_frequency,
       p.predecessor_policy_id
  from insurance_policies p
  -- `distinct`: `union` in einem rekursiven Ausdruck entfernt nur ganze Zeilen,
  -- und dieselbe Police wird ueber verschiedene Tiefen erreicht. Ohne diese
  -- Klammer erschiene jede Police der Kette mehrfach - und mit ihr jede Stufe
  -- ihrer Beitragstreppe.
  join (select distinct id from chain) c on c.id = p.id
 where p.household_id = :household_id
 order by coalesce(p.starts_on, cast(p.created_at as date)), p.created_at
"""

# Nur rueckwaerts: die Vorgaengerkette einer Police. Sie beantwortet die einzige
# Frage, die der Wechsel stellen muss - ist die kuenftige Nachfolgerin in
# Wahrheit eine Vorgaengerin, also wuerde ein Ring entstehen.
_ANCESTORS_SQL: Final = """
with recursive ancestors as (
  select p.id, p.predecessor_policy_id, 0 as depth
    from insurance_policies p
   where p.id = :policy_id and p.household_id = :household_id
  union all
  select p.id, p.predecessor_policy_id, a.depth + 1
    from insurance_policies p
    join ancestors a on p.id = a.predecessor_policy_id
   where p.household_id = :household_id and a.depth < 12
)
select 1 from ancestors where id = :candidate_id limit 1
"""


async def _chain(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(_CHAIN_SQL),
            {"policy_id": str(policy_id), "household_id": str(household_id)},
        )
    ).all()
    return [dict(row._mapping) for row in rows]


async def _contract_steps(
    session: AsyncSession,
    household_id: UUID,
    policy: dict[str, Any],
    notices: list[dict[str, Any]],
) -> list[PremiumHistoryStep]:
    """Die Preistreppe eines Vertrags, angereichert um ihre Anlaesse.

    Ohne die Verknuepfung ueber `amount_history_id` stuende in der Treppe eine
    Erhoehung ohne Grund, und die Steigerungstabelle koennte nicht sagen, welcher
    Sprung auf ein Anschreiben zurueckgeht und welcher auf eine erkannte
    Lastschrift.
    """
    by_history = {
        notice["amount_history_id"]: notice
        for notice in notices
        if notice.get("amount_history_id") is not None
    }
    rows = (
        await session.execute(
            text(
                "select h.id, h.amount_minor, h.valid_from, h.valid_to, h.change_reason, "
                "c.frequency from contract_amount_history h "
                "join contracts c on c.id = h.contract_id and c.household_id = h.household_id "
                "where h.household_id = :household_id and h.contract_id = :contract_id "
                "order by h.valid_from, h.created_at"
            ),
            {"household_id": str(household_id), "contract_id": str(policy["contract_id"])},
        )
    ).all()

    steps: list[PremiumHistoryStep] = []
    for row in rows:
        data = dict(row._mapping)
        frequency = str(data["frequency"])
        yearly = _yearly_minor(int(data["amount_minor"]), frequency)
        if yearly is None:  # pragma: no cover - contracts kennen kein 'single'
            continue
        notice = by_history.get(data["id"])
        steps.append(
            PremiumHistoryStep(
                policy_id=cast(UUID, policy["id"]),
                insurer_name=str(policy["insurer_name"]),
                valid_from=cast(date, data["valid_from"]),
                valid_to=cast("date | None", data["valid_to"]),
                amount_minor=int(data["amount_minor"]),
                frequency=frequency,
                yearly_minor=yearly,
                yearly_delta_minor=None,
                source="contract",
                change_reason=str(data["change_reason"]),
                notice_id=None if notice is None else notice["id"],
                received_on=None if notice is None else notice["received_on"],
                with_benefit_increase=(
                    None if notice is None else bool(notice["with_benefit_increase"])
                ),
                notice_reason=None if notice is None else notice.get("reason"),
            )
        )
    return steps


def _notice_steps(
    policy: dict[str, Any], notices: list[dict[str, Any]]
) -> list[PremiumHistoryStep]:
    """Treppe einer Police OHNE Vertrag, gebaut aus den uebernommenen Schreiben.

    Die erste Stufe kommt aus `old_amount_minor` des aeltesten Schreibens: der
    Betrag davor ist bekannt, sein Beginn nicht. `valid_from = None` sagt genau
    das - "seit Beginn" - statt ein Datum zu erfinden.
    """
    steps: list[PremiumHistoryStep] = []

    def _step(
        *,
        valid_from: date | None,
        valid_to: date | None,
        amount_minor: int,
        frequency: str,
        source: Literal["premium_notice", "external"],
        notice: dict[str, Any] | None = None,
    ) -> None:
        yearly = _yearly_minor(amount_minor, frequency)
        if yearly is None:
            # Einmalbeitrag: er hat keinen Jahreswert und gehoert nicht in eine
            # Treppe, die wiederkehrende Belastung zeigt.
            return
        steps.append(
            PremiumHistoryStep(
                policy_id=cast(UUID, policy["id"]),
                insurer_name=str(policy["insurer_name"]),
                valid_from=valid_from,
                valid_to=valid_to,
                amount_minor=amount_minor,
                frequency=frequency,
                yearly_minor=yearly,
                yearly_delta_minor=None,
                source=source,
                change_reason=None,
                notice_id=None if notice is None else notice["id"],
                received_on=None if notice is None else notice["received_on"],
                with_benefit_increase=(
                    None if notice is None else bool(notice["with_benefit_increase"])
                ),
                notice_reason=None if notice is None else notice.get("reason"),
            )
        )

    if notices:
        first = notices[0]
        _step(
            valid_from=cast("date | None", policy.get("starts_on")),
            valid_to=cast(date, first["effective_on"]) - timedelta(days=1),
            amount_minor=int(first["old_amount_minor"]),
            frequency=str(first["frequency"]),
            source="external",
        )
    elif policy.get("external_premium_minor") is not None:
        _step(
            valid_from=cast("date | None", policy.get("starts_on")),
            valid_to=cast("date | None", policy.get("ends_on")),
            amount_minor=int(policy["external_premium_minor"]),
            frequency=str(policy["external_frequency"]),
            source="external",
        )

    for notice in notices:
        _step(
            valid_from=cast(date, notice["effective_on"]),
            valid_to=None,
            amount_minor=int(notice["new_amount_minor"]),
            frequency=str(notice["frequency"]),
            source="premium_notice",
            notice=notice,
        )
    return steps


async def _chain_notices(
    session: AsyncSession, household_id: UUID, chain_ids: list[UUID]
) -> list[dict[str, Any]]:
    if not chain_ids:  # pragma: no cover - die Kette enthaelt immer die Police selbst
        return []
    params: dict[str, Any] = {"household_id": str(household_id)}
    names: list[str] = []
    for index, policy_id in enumerate(chain_ids):
        name = f"p{index}"
        names.append(f":{name}")
        params[name] = str(policy_id)
    rows = (
        await session.execute(
            text(
                "select id, policy_id, received_on, effective_on, old_amount_minor, "
                "new_amount_minor, frequency, with_benefit_increase, reason, "
                "amount_history_id, confirmed_at from insurance_premium_notices "
                "where household_id = :household_id and policy_id in (" + ", ".join(names) + ") "
                "order by effective_on, received_on"
            ),
            params,
        )
    ).all()
    return [dict(row._mapping) for row in rows]


def _clamp_to_policy(
    steps: list[PremiumHistoryStep], ends_on: date | None
) -> list[PremiumHistoryStep]:
    """Begrenzt die Treppe einer Police auf ihre Laufzeit.

    Der Vertrag ueberlebt die Police regelmaessig - beim Gesellschaftswechsel
    laeuft er weiter, bis der Nutzer ihn bewusst beendet. Seine Preisstufen
    nach dem Ende der Police gehoeren aber nicht mehr in DEREN Verlauf; ohne
    diese Klemmung ueberlappten Vorgaengerin und Nachfolgerin, und eine Stufe
    bekaeme beim Schliessen der offenen Enden ein `valid_to` vor ihrem eigenen
    `valid_from`.
    """
    if ends_on is None:
        return steps
    clamped: list[PremiumHistoryStep] = []
    for step in steps:
        if step.valid_from is not None and step.valid_from > ends_on:
            continue
        if step.valid_to is None or step.valid_to > ends_on:
            step = step.model_copy(update={"valid_to": ends_on})
        clamped.append(step)
    return clamped


async def premium_history(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    *,
    today: date,
    years: int = 5,
) -> PremiumHistory:
    """Der Beitragsverlauf als Treppe, ueber die ganze Policenkette.

    Zusammengefuehrt aus `contract_amount_history` (dort lebt der Beitrag) und
    den uebernommenen Mitteilungen (dort steht, warum er sich geaendert hat).
    Beim Gesellschaftswechsel bricht die Reihe nicht ab, sondern traegt einen
    sichtbaren Wechselpunkt - ein Abbruch saehe aus wie ein beendeter Schutz.
    """
    await _require_policy_context(session, household_id, policy_id)
    chain = await _chain(session, household_id, policy_id)
    chain_ids = [cast(UUID, policy["id"]) for policy in chain]
    position = {policy_id_: index for index, policy_id_ in enumerate(chain_ids)}

    notices = await _chain_notices(session, household_id, chain_ids)
    pending = [
        cast(UUID, notice["id"]) for notice in notices if notice.get("confirmed_at") is None
    ]

    steps: list[PremiumHistoryStep] = []
    for index, policy in enumerate(chain):
        confirmed = [
            notice
            for notice in notices
            if notice["policy_id"] == policy["id"] and notice.get("confirmed_at") is not None
        ]
        if policy.get("contract_id") is not None:
            own = await _contract_steps(session, household_id, policy, confirmed)
        else:
            own = _notice_steps(policy, confirmed)
        own = _clamp_to_policy(own, cast("date | None", policy.get("ends_on")))
        if own and index > 0:
            # Erste Stufe nach dem Wechsel. Ohne diese Marke saehe der Sprung
            # beim Gesellschaftswechsel wie eine Beitragsanpassung aus.
            own[0] = own[0].model_copy(update={"is_switch_point": True})
        steps.extend(own)

    steps.sort(key=lambda step: (step.valid_from or date.min, position.get(step.policy_id, 0)))

    # Offene Enden schliessen: eine Stufe laeuft bis zum Tag vor der naechsten.
    # Ueber die Kettengrenze hinweg ist das zugleich der Wechselpunkt.
    for index, step in enumerate(steps[:-1]):
        following = steps[index + 1].valid_from
        if step.valid_to is not None or following is None:
            continue
        # Nur, wenn dabei ein Zeitraum herauskommt. Zwei Stufen am selben Tag
        # ergaeben sonst ein Ende vor dem Anfang - dann laesst die Stufe ihr
        # Ende lieber offen und die naechste ueberschreibt sie sichtbar.
        if step.valid_from is None or following > step.valid_from:
            steps[index] = step.model_copy(update={"valid_to": following - timedelta(days=1)})

    cutoff = add_months(today, -12 * years)
    kept = [step for step in steps if step.valid_to is None or step.valid_to >= cutoff]

    previous: int | None = None
    for index, step in enumerate(kept):
        if previous is not None:
            kept[index] = step.model_copy(
                update={"yearly_delta_minor": step.yearly_minor - previous}
            )
        previous = step.yearly_minor

    current = next(
        (
            step.yearly_minor
            for step in reversed(kept)
            if (step.valid_from is None or step.valid_from <= today)
            and (step.valid_to is None or step.valid_to >= today)
        ),
        kept[-1].yearly_minor if kept else None,
    )
    first = kept[0].yearly_minor if kept else None
    return PremiumHistory(
        policy_ids=chain_ids,
        steps=kept,
        yearly_first_minor=first,
        yearly_current_minor=current,
        total_increase_bp=(
            round((current - first) * 10000 / first)
            if first is not None and current is not None and first > 0
            else None
        ),
        pending_notice_ids=pending,
    )


# ---------------------------------------------------------------------------
# Gesellschaftswechsel
# ---------------------------------------------------------------------------


async def _assert_no_cycle(
    session: AsyncSession, household_id: UUID, policy_id: UUID, successor_id: UUID
) -> None:
    """Die Nachfolgerin darf keine Vorgaengerin sein.

    Ohne diese Pruefung entstuende ein Ring, und der Beitragsverlauf liefe seine
    eigene Kette im Kreis. Der Tiefendeckel in `_CHAIN_SQL` faenge das ab - aber
    erst, nachdem der Bestand schon falsch waere.
    """
    if successor_id == policy_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_REPLACEMENT_TARGET_INVALID,
        )
    found = (
        await session.execute(
            text(_ANCESTORS_SQL),
            {
                "policy_id": str(policy_id),
                "household_id": str(household_id),
                "candidate_id": str(successor_id),
            },
        )
    ).first()
    if found is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=INSURANCE_REPLACEMENT_TARGET_INVALID,
        )


async def replace_with(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: PolicyReplaceRequest,
    *,
    today: date,
    expected: tuple[int, int] | None,
) -> tuple[PolicyReplacement, str]:
    """Gesellschaftswechsel mit Historienmitnahme (Plan 4.15).

    Eine Transaktion, drei Wirkungen: die neue Police kennt ihre Vorgaengerin,
    die alte bekommt ihr Ende samt Begruendung, und der Beitragsverlauf laeuft
    ueber beide. Was hier ausdruecklich NICHT passiert: der Vertrag der alten
    Police wird nicht angefasst. Ihn still zu beenden aenderte die Fixkosten
    ohne Rueckfrage - die Statuskopplung bleibt eine bestaetigte Handlung
    (Plan 4.16), und dieser Endpunkt weist mit `hint` darauf hin.
    """
    predecessor = await _require_policy_context(session, household_id, policy_id)
    _assert_policy_version(predecessor, expected)

    if payload.new_policy is not None:
        created = await insurance_service.create_policy(
            session, household_id, payload.new_policy, today=today
        )
        successor_id = created.policy.id
    else:
        successor_id = cast(UUID, payload.new_policy_id)
        await _require_policy_context(session, household_id, successor_id)
        await _assert_no_cycle(session, household_id, policy_id, successor_id)

    # Die Vorversicherungszeit ist der eigentliche Grund fuer diesen Endpunkt:
    # eine mitgenommene Zeit verkuerzt oder erledigt die Wartezeit der neuen
    # Police (Zahnzusatz, Rechtsschutz). Ohne Angabe erbt sie die der alten
    # Police, hilfsweise deren Beginn.
    prior_cover_since = (
        payload.prior_cover_since
        or cast("date | None", predecessor.get("prior_cover_since"))
        or cast("date | None", predecessor.get("policy_starts_on"))
    )

    await session.execute(
        text(
            "update insurance_policies set predecessor_policy_id = cast(:predecessor as uuid), "
            "previous_insurer_name = :insurer_name, previous_policy_number = :policy_number, "
            "prior_cover_since = :prior_cover_since "
            "where id = :id and household_id = :household_id"
        ),
        {
            "id": str(successor_id),
            "household_id": str(household_id),
            "predecessor": str(policy_id),
            "insurer_name": predecessor["insurer_name"],
            "policy_number": predecessor.get("policy_number"),
            "prior_cover_since": prior_cover_since,
        },
    )

    # 'ended' und nicht 'cancelled': beendet wurde nicht die Deckung, sondern
    # der Anbieter gewechselt. Deshalb auch kein `cancelled_on` - der CHECK
    # insurance_policies_cancel_pair verlangt es nur im Kuendigungsfall.
    await session.execute(
        text(
            "update insurance_policies set status = 'ended', ends_on = :ends_on, "
            "cancellation_reason = 'switch' "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(policy_id), "household_id": str(household_id), "ends_on": payload.ends_on},
    )

    hint = ""
    if predecessor.get("contract_id") is not None and predecessor.get("contract_status") in (
        "active",
        "paused",
    ):
        contract_name = predecessor.get("contract_name") or "der bisherige Vertrag"
        hint = (
            f"Der Vertrag '{contract_name}' der bisherigen Police laeuft weiter und zaehlt "
            "in die Fixkosten. Der Statuswechsel bleibt eine eigene, bestaetigte Handlung "
            "ueber die Statusaktion der Police."
        )

    old_policy = await insurance_service.get_policy(session, household_id, policy_id, today=today)
    new_policy = await insurance_service.get_policy(
        session, household_id, successor_id, today=today
    )
    return (
        PolicyReplacement(predecessor=old_policy.policy, successor=new_policy.policy, hint=hint),
        new_policy.etag,
    )


__all__ = [
    "INSURANCE_NOTICE_ALREADY_CONFIRMED",
    "INSURANCE_NOTICE_DATE_ORDER",
    "INSURANCE_NOTICE_DUPLICATE",
    "INSURANCE_NOTICE_FIELD_NOT_CLEARABLE",
    "INSURANCE_NOTICE_FREQUENCY_MISMATCH",
    "INSURANCE_NOTICE_MISSING_RECEIVED_ON",
    "INSURANCE_NOTICE_NOT_FOUND",
    "INSURANCE_NOTICE_REQUIRES_EXTERNAL_PAYER",
    "INSURANCE_NOTICE_SINGLE_PREMIUM",
    "INSURANCE_REPLACEMENT_TARGET_INVALID",
    "acknowledge_notice",
    "compute_window",
    "confirm_notice",
    "create_notice",
    "delete_notice",
    "get_notice",
    "list_notices",
    "notice_etag",
    "premium_history",
    "replace_with",
    "update_notice",
]
