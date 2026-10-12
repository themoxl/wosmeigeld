"""Push-Benachrichtigungen zu Versicherungen (docs/VERSICHERUNGEN-PLAN.md 8.3).

Vier Typen, ein Actor - dieselbe Aufteilung wie in `contract_alerts.py` und aus
demselben Grund: sie teilen den Empfaengerweg, den Versandbatch und die Regel,
dass erst nach dem Versand markiert wird.

| data.type                      | Anlass                                          |
| ------------------------------ | ----------------------------------------------- |
| insurance_deadline_ordinary    | bestaetigte ordentliche Frist, Police OHNE Vertrag |
| insurance_main_due_question    | angenommenes Ankerdatum - Rueckfrage, kein Countdown |
| insurance_special_termination  | offenes Sonderkuendigungsfenster nach Beitragsanpassung (P3) |
| insurance_claim_followup       | Vorgang ohne Rueckmeldung (P5, Nachtlauf Schritt 5) |

**Zwei Vorlaufstaffeln und drei Dedup-Anker** (Plan 8.3). Die ordentliche Frist
zaehlt 60/14/3 Tage und markiert `insurance_policies.notified_at`; das
Sonderkuendigungsfenster zaehlt sofort/7/2 Tage und markiert
`insurance_premium_notices.notified_at`; der Vorgang zaehlt dieselbe kurze
Staffel und markiert `insurance_claims.notified_at`. Die zweite Staffel ist
kuerzer, weil das Fenster meist nur einen Monat lang ist - 60 Tage Vorlauf
waeren nie erreichbar. Die Anker bleiben getrennt, weil sie verschiedene Dinge
verbrauchen: schriebe der Sonderfenster-Push die Police fort, verbrauchte er
eine Stufe der ordentlichen Frist, und der Nutzer verloere die Erinnerung an
einen ganz anderen Termin. Fuer den Vorgang gilt dasselbe doppelt - eine Police
kann mehrere Vorgaenge tragen.

**Ein hier ergaenzter Typ ist auf dem Geraet tot, solange er dort nicht
mitgefuehrt wird.** `apps/mobile/src/notifications/useNotificationResponseRouter.ts`
fuehrt `INSURANCE_NOTIFICATION_TYPES` als Reissleine; ein unbekannter Typ wird
zugestellt, angetippt - und nichts geschieht. Der vierte Typ gehoert dort
ergaenzt, sobald die App dem Modul nachzieht.

**Warum der Fristen-Push streng auf `contract_id is null` beschraenkt ist.**
Policen MIT Vertrag laufen weiter ueber `scan_term_deadlines` ->
`contract_events(kind='term_ending')` -> `contract_term_ending`
(contract_alerts.py). Fiele diese Bedingung weg, bekaeme der Nutzer zu einer
einzigen Frist zwei Nachrichten aus zwei Laeufen. Der Typ hier schliesst
ausschliesslich die Luecke, die der Vertragsweg offen laesst: die Police in
Geldfall (b) oder (c) hat keinen Vertrag, kommt in `scan_term_deadlines` nicht
vor und bekaeme sonst nie eine Erinnerung (Plan 8.1, letzter Absatz).

**Warum der zweite Typ eine Frage ist und keine Frist.** Ein Ankerdatum mit
`main_due_source = 'catalog_suggested'` ist geraten - der uebliche Termin der
Sparte, nicht der Termin dieser Police. Ein Countdown darauf saehe von einem
belegten nicht zu unterscheiden aus, und ein Push darauf schickte den Nutzer an
einem erfundenen Datum zur Kuendigung. Genau das ist der Fehler, den Leitsatz 1
des Plans verbietet. Die Nachricht traegt deshalb keine Tageszahl, sondern eine
Bitte um eine Angabe.

**Dedup ueber `insurance_policies.notified_at`,** wie `contract_alerts` es ueber
`contract_events.notified_at` tut. Die Spalte ist maschinengeschrieben und aus
dem Versionstrigger ausgenommen (0001_schema.sql, `bump_insurance_version`) -
ohne diese Ausnahme entwertete jeder Push das ETag des Nutzers und sein
naechstes `PATCH /v1/insurance/policies/{id}` liefe ohne inhaltliche Aenderung
in ein 409.

**Verbotener Inhalt, strenger als bei Vertraegen** (Plan 7.1): auf dem
gesperrten Geraet erscheinen WEDER Betrag NOCH Person NOCH Versicherungsart.
`contract_alerts.py:29-31` verbietet nur Betraege; hier kommt die Sparte dazu,
weil "Berufsunfaehigkeit" oder "Pflegezusatz" auf einem Sperrbildschirm eine
Gesundheitsangabe ist, die jeder im Raum mitliest. Deshalb steht in Titel und
Rumpf kein einziger Wert aus der Police - die Texte unten sind Konstanten, in
die nur eine Tageszahl bzw. eine Anzahl eingesetzt wird. Der Datenteil traegt
`policy_id` und fuehrt nach dem Entsperren in die Detailseite; dort steht der
Rest.

Beim Sonderkuendigungsfenster ist das die scharfste Stelle des ganzen Moduls:
der Anlass IST eine Beitragserhoehung, und der naheliegende Satz "Die PKV steigt
um 48 EUR" traegt Betrag UND Sparte UND, ueber die Sparte, eine Gesundheitsangabe
zur versicherten Person. Der Rumpf nennt deshalb nur das Recht und eine
Tageszahl: "Ein Sonderkuendigungsrecht laeuft in 7 Tagen ab." Auch die
Vorgangs-ID bleibt aussen vor - der Datenteil traegt weiterhin nur `type` und
`policy_id`, weil schon der Vorrat im Datenteil das ist, woraus eine kuenftige
lokale Benachrichtigung ihren Text anreichern koennte
(apps/mobile/src/notifications/useNotificationResponseRouter.ts fuehrt dieselbe
Liste als Reissleine).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from worker.db import get_session_factory
from worker.insurance import compute_policy_deadline
from worker.notifications.push import PushMessage, send_expo_push_batch

logger = logging.getLogger(__name__)

# Deckungsgleich mit `insurance_service._LIVE_STATUSES` und mit dem, was der
# Rechenkern ueberhaupt annimmt (`compute_insurance_deadline` gibt fuer alles
# andere "nicht mehr aktiv" zurueck).
_LIVE_STATUSES: Final[list[str]] = ["active", "suspended", "paid_up", "dormant"]

# Voreinstellung aus 0001_schema.sql, falls der Haushalt noch keine Zeile in
# insurance_settings hat. 60 statt 45 Tage in der obersten Stufe, weil zwischen
# Entschluss und sicherem Zugang bei Postversand mehrere Werktage liegen und wer
# wechseln will, Zeit fuer ein Angebot braucht (Plan 8.3).
_DEFAULT_LEAD_DAYS: Final[list[int]] = [60, 14, 3]

# Voreinstellung fuer das Sonderfenster aus 0001_schema.sql
# (`insurance_settings.lead_days_special` default '{0,7,2}'). Sofort/7/2 statt
# 60/14/3: das Fenster des Paragraphen 40 VVG ist einen Monat lang (PKV zwei
# nach Paragraph 205 Abs. 4), eine 60er-Stufe darauf waere nie erreichbar und
# die oberste Stufe bliebe stumm.
_DEFAULT_LEAD_DAYS_SPECIAL: Final[list[int]] = [0, 7, 2]

# Schweigefrist eines gemeldeten Vorgangs. Deckungsgleich mit
# `worker/insurance/scan.py:_CLAIM_SILENCE_DAYS` und aus Plan 8.2 Schritt 5
# ("reported_on < today - 30"): der Push soll genau die Vorgaenge melden, die
# der Nachtlauf als Befund fuehrt. Eine eigene Zahl hier hiesse, Protokoll und
# Sperrbildschirm meldeten an verschiedenen Tagen denselben Vorgang.
_CLAIM_SILENCE_DAYS: Final = 30

# SQLSTATE "undefined_table" - siehe `select_special_termination_alerts`.
_UNDEFINED_TABLE: Final = "42P01"

_TYPE_DEADLINE: Final = "insurance_deadline_ordinary"
_TYPE_QUESTION: Final = "insurance_main_due_question"
_TYPE_SPECIAL: Final = "insurance_special_termination"
_TYPE_CLAIM: Final = "insurance_claim_followup"

_DEADLINE_TITLE: Final = "Frist laeuft ab"
_QUESTION_TITLE: Final = "Angabe fehlt"
# Erlaubt trotz Plan 7.1: "Sonderkuendigungsrecht" ist weder Betrag noch Person
# noch Versicherungsart, sondern die Benennung eines Rechts. Es sagt, worum es
# geht, ohne zu sagen, wen oder wieviel es betrifft.
_SPECIAL_TITLE: Final = "Sonderkuendigungsrecht"
# "Vorgang" statt "Schaden": auf dem Sperrbildschirm soll nicht stehen, dass
# etwas kaputt ist, wem etwas passiert ist oder worum es geht. Der Titel nennt
# den Zustand - es wartet etwas -, und das ist alles, was ohne Entsperren
# gebraucht wird.
_CLAIM_TITLE: Final = "Vorgang ohne Rueckmeldung"


@dataclass(frozen=True)
class DeadlineAlert:
    """Eine versandreife ordentliche Frist - je Police hoechstens eine je Stufe."""

    household_id: str
    policy_id: str
    days_left: int
    # Die Vorlaufstufe, in der die Police heute steht (60, 14 oder 3). Sie ist
    # der Dedup-Schluessel: `notified_at` traegt nur einen Zeitpunkt, die Stufe
    # macht daraus "schon in dieser Stufe benachrichtigt?".
    stage_days: int


@dataclass(frozen=True)
class QuestionAlert:
    """Eine Police, deren Ankerdatum nur angenommen ist."""

    household_id: str
    policy_id: str


@dataclass(frozen=True)
class SpecialTerminationAlert:
    """Ein offenes Sonderkuendigungsfenster nach einer Beitragsanpassung.

    Traegt `notice_id` UND `policy_id`, und beide werden gebraucht: die
    Vorgangs-ID ist der Dedup-Anker (`insurance_premium_notices.notified_at`),
    die Policen-ID der einzige Wert, der bis aufs Geraet darf. Eine Police kann
    mehrere offene Mitteilungen haben - nach einem Wechsel der Sparte oder bei
    einer zweiten Anpassung im selben Jahr -, deshalb ist die Police als
    Schluessel hier untauglich.
    """

    household_id: str
    notice_id: str
    policy_id: str
    days_left: int
    stage_days: int


@dataclass(frozen=True)
class ClaimFollowupAlert:
    """Ein Vorgang, zu dem seit Tagen nichts passiert ist (Plan 8.2 Schritt 5).

    `policy_id` ist optional, weil ein Vorgang sich gegen eine FREMDE
    Versicherung richten kann (Plan 4.8). Genau der wird am ehesten vergessen -
    ihn hier auszulassen, weil der Datenteil sonst kein Ziel haette, hiesse den
    haeufigsten Fall zu verschweigen. Die Nachricht fuehrt dann auf die
    Uebersicht statt auf eine Police; der Router faengt das bereits ab.

    `action_overdue` unterscheidet die beiden Anlaesse, weil sie verschiedene
    Saetze verlangen: beim einen wartet man auf den Versicherer, beim anderen
    liegt der Ball beim Nutzer. Ein gemeinsamer Text muesste einen der beiden
    Faelle falsch beschreiben.
    """

    household_id: str
    claim_id: str
    policy_id: str | None
    days_waiting: int
    action_overdue: bool
    stage_days: int


# Eine Abfrage fuer beide Typen. Die Bedingungen der beiden Zweige schliessen
# sich ueber `main_due_source` gegenseitig aus - eine Police kann nie zugleich
# ein bestaetigtes und ein angenommenes Ankerdatum haben -, deshalb kann aus
# einer Zeile nur eine Nachricht entstehen.
#
# Zweig (1) ist bewusst nur eine VORAUSWAHL: ob die Frist tatsaechlich
# `certainty = 'confirmed'` erreicht, entscheidet allein der Rechenkern in
# services/api. Die Bedingungen hier halten nur die Zeilen fern, die er
# garantiert verwirft - eine zweite Fassung der Regel in SQL waere der zweite
# Rechner, den Plan 8.1 ausschliesst.
#
# `exists (...)` auf die Geraete filtert Haushalte ohne Push-Token schon hier
# heraus: sonst markierte der Lauf ihre Policen als benachrichtigt, ohne dass je
# etwas zugestellt wurde (uebernommen aus contract_alerts.py).
_PENDING_QUERY = text(
    "select p.id as policy_id, p.household_id as household_id, "
    "       p.contract_id as contract_id, p.status as status, "
    "       p.notice_anchor as notice_anchor, p.deadline_kind as deadline_kind, "
    "       p.main_due_on as main_due_on, "
    "       p.notice_period_months as notice_period_months, "
    "       p.notice_fixed_month as notice_fixed_month, "
    "       p.notice_fixed_day as notice_fixed_day, "
    "       p.service_channel as service_channel, "
    "       p.main_due_source as main_due_source, "
    "       p.notice_terms_source as notice_terms_source, "
    "       p.ends_on as ends_on, p.starts_on as starts_on, "
    "       p.recipient_state as recipient_state, "
    "       p.min_term_months as min_term_months, "
    "       p.notified_at as notified_at, "
    "       coalesce(s.lead_days, cast(:default_lead_days as int[])) as lead_days "
    "from insurance_policies p "
    "left join insurance_settings s on s.household_id = p.household_id "
    "where p.status = any(cast(:live_statuses as text[])) "
    "and coalesce(s.notify_deadline, true) "
    "and ( "
    "  (p.contract_id is null "
    "   and p.main_due_source = 'user_confirmed' "
    "   and p.notice_terms_source <> 'derived_from_days' "
    "   and p.notice_anchor not in ('none', 'per_event')) "
    "  or (p.main_due_source = 'catalog_suggested' "
    "      and p.notice_anchor in ('term_end', 'main_due', 'period_end') "
    "      and p.notified_at is null) "
    ") "
    "and exists ("
    "  select 1 from household_members hm "
    "  join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "  where hm.household_id = p.household_id and hm.status = 'active') "
    "order by p.household_id, p.id"
)

# Der dritte Typ, aus `insurance_premium_notices` (Plan 4.7, 8.3 Zeile 2).
#
# Eine eigene Abfrage und kein dritter Zweig in `_PENDING_QUERY`: sie liest eine
# andere Tabelle, mit einem anderen Dedup-Anker und einer anderen Staffel. Ein
# Union daraus haette an jeder Spalte einen Platzhalter fuer die jeweils andere
# Seite.
#
# Was hier NICHT steht, ist so wichtig wie was steht:
# - `special_termination_until is not null and acknowledged_at is null` ist
#   woertlich der Teilindex der Tabelle - "offenes Fenster" ist dort definiert,
#   nicht hier. Eine Zeile ohne dieses Datum hat kein Fenster, weil der
#   Fachdienst ihr keins zugestanden hat; der Push rechnet das nicht nach.
# - `special_termination_until >= today` haelt verstrichene Fenster fern. Der
#   Nachtlauf meldet sie noch (Schritt 2a), damit der Nutzer den Verfall sieht;
#   eine PUSH-Erinnerung an ein verfallenes Recht waere nur noch Stoerung.
# - `acknowledged_at is null` ist zugleich die Zusage aus Plan 8.3: wer die
#   Frist zur Kenntnis genommen hat, hoert nichts mehr - auch wenn die naechste
#   Stufe noch kaeme. Die Stufen sind eine Eskalation fuer den Fall des
#   Nichthandelns, keine Terminserie. `confirmed_at` schweigt hier ausdruecklich
#   NICHT mit: das ist die Uebernahme des neuen Betrags in die Preistreppe. Wer
#   den hoeheren Beitrag fortschreibt, hat damit noch nicht entschieden, auf sein
#   Sonderkuendigungsrecht zu verzichten - beides zu verwechseln naehme ihm das
#   Recht durch eine Buchhaltungsgeste.
# - `not p.dynamic_agreed` ist Leitsatz 3 des Plans: eine vereinbarte Dynamik
#   ist keine Erhoehung im Sinne des Paragraphen 40 VVG.
# - `notify_premium_notice` ist der eigene Schalter dieses Typs; `notify_deadline`
#   im Zweig oben gilt fuer die ordentliche Frist und darf ihn nicht mitschalten.
_SPECIAL_PENDING_QUERY = text(
    "select n.id as notice_id, n.policy_id as policy_id, "
    "       n.household_id as household_id, "
    "       n.special_termination_until as window_ends_on, "
    "       n.recommended_action_on as recommended_action_on, "
    "       n.notified_at as notified_at, "
    "       coalesce(s.lead_days_special, cast(:default_lead_special as int[])) "
    "         as lead_days "
    "from insurance_premium_notices n "
    "join insurance_policies p "
    "  on p.id = n.policy_id and p.household_id = n.household_id "
    "left join insurance_settings s on s.household_id = n.household_id "
    "where n.acknowledged_at is null "
    "and n.special_termination_until is not null "
    "and n.special_termination_until >= cast(:today as date) "
    "and not n.with_benefit_increase "
    "and not p.dynamic_agreed "
    "and p.status = any(cast(:live_statuses as text[])) "
    "and coalesce(s.notify_premium_notice, true) "
    "and exists ("
    "  select 1 from household_members hm "
    "  join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "  where hm.household_id = n.household_id and hm.status = 'active') "
    "order by n.household_id, n.special_termination_until, n.id"
)

# Der vierte Typ, aus `insurance_claims` (Plan 4.8, 8.3 Zeile 3). Die Auswahl
# ist zeilengleich mit Schritt 5 des Nachtlaufs
# (`worker/insurance/scan.py:_CLAIM_FOLLOWUP_QUERY`) - dieselben drei Status,
# dieselben zwei Bedingungen. Das ist die Zusage aus Plan 8.3 ("Vorgaenge aus
# 8.2 Punkt 5"): was der Lauf um 03:30 als Befund protokolliert, ist genau das,
# wovon der Push um 08:45 spricht. Eine engere oder weitere Auswahl hier waere
# eine zweite Definition von "liegengeblieben".
#
# `left join` auf die Police: ein Vorgang gegen eine fremde Versicherung hat
# keine (Plan 4.8), und gerade der wird vergessen.
#
# `not coalesce(p.is_health_scoped, false)` ist Leitsatz 2 des Moduls. Der
# Trigger aus Plan 4.8 verhindert den Vorgang auf einer Gesundheitspolice beim
# SCHREIBEN; wandert eine Police spaeter auf eine gesundheitsnahe Sparte, bleibt
# ein alter Vorgang stehen. Auf dem Sperrbildschirm waere er dann eine
# Gesundheitsangabe, auch ohne ein Wort darueber - er gehoert ab da zu
# /v1/reimbursements.
#
# **Kein Filter auf den Policenstatus**, anders als bei den drei uebrigen
# Typen: ein Vorgang wird noch abgewickelt, wenn die Police laengst gekuendigt
# ist. Der Anspruch aus einem Schaden ueberlebt den Vertrag.
#
# `notify_claim_followup` ist der eigene Schalter dieses Typs
# (0001_schema.sql, `insurance_settings`); die beiden anderen Schalter gelten
# fuer die Frist und die Beitragsmitteilung und duerfen ihn nicht mitschalten.
_CLAIM_PENDING_QUERY = text(
    "select c.id as claim_id, c.policy_id as policy_id, "
    "       c.household_id as household_id, "
    "       c.reported_on as reported_on, c.next_action_due as next_action_due, "
    "       c.notified_at as notified_at, "
    "       coalesce(s.lead_days_special, cast(:default_lead_special as int[])) "
    "         as lead_days "
    "from insurance_claims c "
    "left join insurance_policies p "
    "  on p.id = c.policy_id and p.household_id = c.household_id "
    "left join insurance_settings s on s.household_id = c.household_id "
    "where c.status in ('reported', 'in_review', 'info_requested') "
    "and not coalesce(p.is_health_scoped, false) "
    "and ( "
    "  (c.reported_on is not null "
    "   and c.reported_on < cast(:today as date) - cast(:silence_days as int)) "
    "  or (c.next_action_due is not null "
    "      and c.next_action_due < cast(:today as date)) "
    ") "
    "and coalesce(s.notify_claim_followup, true) "
    "and exists ("
    "  select 1 from household_members hm "
    "  join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "  where hm.household_id = c.household_id and hm.status = 'active') "
    "order by c.household_id, c.next_action_due nulls last, c.id"
)

_TOKEN_QUERY = text(
    "select hm.household_id as household_id, dpt.expo_token as expo_token "
    "from household_members hm "
    "join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "where hm.status = 'active' "
    "and hm.household_id = any(cast(:household_ids as uuid[]))"
)

# Ohne "and notified_at is null": die Spalte ist hier kein Schalter, sondern der
# Zeitpunkt der letzten Nachricht. Die Stufenlogik unten vergleicht ihn mit dem
# Beginn der aktuellen Vorlaufstufe - wuerde er nie fortgeschrieben, bliebe es
# bei genau einer Nachricht je Police statt bei einer je Stufe.
_MARK_NOTIFIED_QUERY = text(
    "update insurance_policies p set notified_at = now() "
    "from unnest(cast(:household_ids as uuid[]), cast(:policy_ids as uuid[])) "
    "     as sel(household_id, policy_id) "
    "where p.household_id = sel.household_id and p.id = sel.policy_id"
)


# Eigener Anker, eigene Anweisung: `insurance_policies.notified_at` bleibt
# unberuehrt, sonst verbrauchte der Sonderfenster-Push eine Vorlaufstufe der
# ordentlichen Frist (Modulkopf, "Zwei Vorlaufstaffeln und zwei Dedup-Anker").
_MARK_NOTICES_NOTIFIED_QUERY = text(
    "update insurance_premium_notices n set notified_at = now() "
    "from unnest(cast(:household_ids as uuid[]), cast(:notice_ids as uuid[])) "
    "     as sel(household_id, notice_id) "
    "where n.household_id = sel.household_id and n.id = sel.notice_id"
)


# Dritter Anker, dritte Anweisung, aus demselben Grund wie der zweite: eine
# Police kann mehrere Vorgaenge tragen, und ein Vorgang ueberlebt seine Police.
# `insurance_claims.notified_at` ist wie die beiden anderen ein
# maschinengeschriebenes Feld und deshalb aus dem Versionstrigger auszunehmen
# (Plan 8.3, letzter Absatz) - sonst entwertet jeder Push das ETag des Nutzers
# und sein naechstes `PATCH /v1/insurance/claims/{id}` liefe ohne inhaltliche
# Aenderung in ein 409.
_MARK_CLAIMS_NOTIFIED_QUERY = text(
    "update insurance_claims c set notified_at = now() "
    "from unnest(cast(:household_ids as uuid[]), cast(:claim_ids as uuid[])) "
    "     as sel(household_id, claim_id) "
    "where c.household_id = sel.household_id and c.id = sel.claim_id"
)


def current_stage(days_left: int, lead_days: list[int]) -> int | None:
    """Die engste Vorlaufstufe, in der die Police heute steht - oder None.

    Aus [60, 14, 3] und `days_left = 10` wird 14: die 60er-Stufe ist laengst
    ueberschritten, die 3er noch nicht erreicht. Die engste zu nehmen ist der
    Kern des Dedups - haetten wir die weiteste genommen, faellt jede spaetere
    Eskalation aus, weil ihr Stufenbeginn vor der ersten Nachricht laege.
    """
    applicable = sorted({int(lead) for lead in lead_days if int(lead) >= 0})
    reached = [lead for lead in applicable if days_left <= lead]
    return min(reached) if reached else None


def is_new_stage(notified_at: datetime | None, stage_starts_on: date) -> bool:
    """Wurde in dieser Stufe schon benachrichtigt?

    Der Vergleich laeuft ueber den Tag, nicht ueber den Zeitpunkt: der Lauf
    kennt nur Tagesgrenzen, und `stage_starts_on` ist ein Datum. Eine Nachricht
    vom Beginntag der Stufe selbst zaehlt als bereits gesendet.

    **Beide Seiten muessen denselben Tag meinen.** `notified_at` kommt als
    `now()` aus der Datenbank und damit in UTC; `stage_starts_on` ist aus
    `today` gerechnet, und das ist die ORTSZEIT des Laufs. Zwischen Mitternacht
    und dem UTC-Versatz laegen die beiden sonst einen Tag auseinander - die
    Nachricht von "gestern 22:11 UTC" waere fuer den Lauf um "heute 00:11"
    keine, und dieselbe Erinnerung ginge ein zweites Mal raus. Genau das, was
    `notified_at` verhindern soll.
    """
    if notified_at is None:
        return True
    return notified_at.astimezone().date() < stage_starts_on


async def select_pending_alerts(
    session: AsyncSession, *, today: date
) -> tuple[list[DeadlineAlert], list[QuestionAlert]]:
    """Liest beide Anlaesse in einem Durchgang.

    `today` kommt von aussen statt aus `current_date`: die Prozesszeitzone des
    Workers und die Serverzeitzone der Datenbank muessen sich an der
    Tagesgrenze nicht einig sein - dieselbe Begruendung wie in
    `contract_alerts.select_pending_alerts`.
    """
    result = await session.execute(
        _PENDING_QUERY,
        {"live_statuses": _LIVE_STATUSES, "default_lead_days": _DEFAULT_LEAD_DAYS},
    )

    deadlines: list[DeadlineAlert] = []
    questions: list[QuestionAlert] = []
    for row in result.mappings().all():
        data: dict[str, Any] = dict(row)
        household_id = str(data["household_id"])
        policy_id = str(data["policy_id"])

        if data["main_due_source"] == "catalog_suggested":
            questions.append(QuestionAlert(household_id=household_id, policy_id=policy_id))
            continue

        if data["contract_id"] is not None:
            # Zweite Sicherung neben der Abfrage: eine Police MIT Vertrag
            # bekommt ihre Fristerinnerung ueber contract_term_ending. Diese
            # Zeile ist die Zusage "genau ein Fristen-Push je Frist".
            continue

        deadline = compute_policy_deadline(data, today=today)
        # Der Rechenkern ist die Instanz, nicht die Abfrage: nur eine belegte
        # Frist (bestaetigtes Ankerdatum UND nicht umgerechnete Fristlaenge)
        # darf ueberhaupt einen Countdown ausloesen.
        if deadline.certainty != "confirmed":
            continue
        if deadline.recommended_action_on is None or deadline.days_left is None:
            continue
        if deadline.days_left < 0:
            # Das empfohlene Handlungsdatum ist vorbei. Rechtlich kann die
            # Kuendigung noch rechtzeitig sein (Plan 4.7a Regel 4), aber eine
            # Erinnerung, die zum Postversand raet, waere ab hier falsch - und
            # "verstrichen" darf sie erst ab deadline_legal_on sagen. Der Lauf
            # schweigt also und ueberlaesst der Detailseite den Unterschied.
            continue

        lead_days = [int(value) for value in (data["lead_days"] or _DEFAULT_LEAD_DAYS)]
        stage = current_stage(deadline.days_left, lead_days)
        if stage is None:
            continue
        stage_starts_on = deadline.recommended_action_on - timedelta(days=stage)
        if not is_new_stage(data["notified_at"], stage_starts_on):
            continue

        deadlines.append(
            DeadlineAlert(
                household_id=household_id,
                policy_id=policy_id,
                days_left=deadline.days_left,
                stage_days=stage,
            )
        )
    return deadlines, questions


async def select_special_termination_alerts(
    session: AsyncSession, *, today: date
) -> list[SpecialTerminationAlert]:
    """Offene Sonderkuendigungsfenster in der faelligen Vorlaufstufe.

    **Gezaehlt wird auf das Handlungsdatum, nicht auf das Fristende** - genau
    wie beim ordentlichen Typ. Das rechtliche Fenster endet an
    `special_termination_until`; wer erst an diesem Tag zur Post geht, ist zu
    spaet, weil die Erklaerung ZUGEHEN muss. `recommended_action_on` ist der
    Tag, den der Fristenkern dafuer gerechnet hat, und deshalb der Tag, auf den
    der Nutzer hin erinnert werden will. Fehlt er, bleibt das Fristende der
    ehrlichere Bezug als gar keiner.

    **Eine negative Tageszahl ist hier kein Abbruchgrund, anders als oben.** Bei
    der ordentlichen Frist schweigt der Lauf, sobald das Handlungsdatum vorbei
    ist - dort bleiben Wochen bis zum rechtlichen Ende, und ein Rat zum
    Postversand waere falsch. Beim Sonderfenster liegen zwischen Handlungsdatum
    und Fristende nur wenige Tage, und die Staffel beginnt ausdruecklich bei
    "sofort" (Stufe 0). Wer heute noch faxen oder mailen kann, soll die
    Nachricht bekommen; der Rumpf sagt dann "endet in Kuerze" statt eine
    Tageszahl zu nennen, die nicht mehr stimmt.

    Gerechnet wird auch hier nichts: die drei Daten stehen fertig in der Zeile,
    weil der Fachdienst sie beim Erfassen der Mitteilung gespeichert hat (Plan
    4.7: "gespeichert nur fuer den Push-Dedup"). Die Zuordnung `special_law` ->
    Fristlaenge bleibt damit an ihrer einzigen Stelle im Fristendienst.
    """
    try:
        async with session.begin_nested():
            result = await session.execute(
                _SPECIAL_PENDING_QUERY,
                {
                    "live_statuses": _LIVE_STATUSES,
                    "today": today,
                    "default_lead_special": _DEFAULT_LEAD_DAYS_SPECIAL,
                },
            )
            rows = result.mappings().all()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _UNDEFINED_TABLE:
            raise
        # Worker-Image und Schema werden getrennt ausgerollt. Ohne die
        # Sicherungsmarke risse ein Rollout vor der P3-Migration die ganze
        # Transaktion mit - und mit ihr die Markierung der bereits versendeten
        # ordentlichen Fristen, die der Nutzer morgen ein zweites Mal bekaeme.
        logger.info(
            "Sonderkuendigungs-Push uebersprungen: insurance_premium_notices "
            "existiert im Schema noch nicht."
        )
        return []

    alerts: list[SpecialTerminationAlert] = []
    for row in rows:
        action_on: date = row["recommended_action_on"] or row["window_ends_on"]
        days_left = (action_on - today).days
        lead_days = [int(value) for value in (row["lead_days"] or _DEFAULT_LEAD_DAYS_SPECIAL)]
        stage = current_stage(days_left, lead_days)
        if stage is None:
            continue
        stage_starts_on = action_on - timedelta(days=stage)
        if not is_new_stage(row["notified_at"], stage_starts_on):
            continue
        alerts.append(
            SpecialTerminationAlert(
                household_id=str(row["household_id"]),
                notice_id=str(row["notice_id"]),
                policy_id=str(row["policy_id"]),
                days_left=days_left,
                stage_days=stage,
            )
        )
    return alerts


async def select_claim_followup_alerts(
    session: AsyncSession, *, today: date
) -> list[ClaimFollowupAlert]:
    """Vorgaenge, zu denen seit Tagen nichts passiert ist.

    **Der Faelligkeitstag ist der Tag, an dem der Vorgang liegengeblieben IST,
    nicht ein kuenftiger Termin.** Bei den drei uebrigen Typen zaehlt die
    Staffel auf ein Datum in der Zukunft; hier gibt es keines - ein Schweigen
    hat keinen Termin. Der Bezugstag ist deshalb `next_action_due`, wenn ein
    vorgemerkter Schritt ueberfaellig ist, sonst `reported_on + 30`: der Tag,
    an dem aus Bearbeitungszeit ein Liegenbleiben wurde.

    **Damit greift von der Staffel in der Regel nur die Stufe "sofort" - und
    genau das ist beabsichtigt.** Der Bezugstag liegt immer in der Vergangenheit
    oder auf heute, `current_stage` waehlt also die engste Stufe, und
    `is_new_stage` laesst danach Ruhe. Ein Vorgang, der seit 200 Tagen liegt,
    bekommt EINE Nachricht, keine 200. Setzt der Nutzer einen neuen naechsten
    Schritt und laesst auch den verstreichen, wandert der Bezugstag nach vorn
    und es kommt wieder eine - das ist ein neuer Anlass, keine Wiederholung.

    **`acknowledged_at` gibt es hier nicht, und es fehlt nicht.** Beim
    Sonderfenster beendet die Kenntnisnahme die Erinnerungen; bei einem Vorgang
    ist die Kenntnisnahme das Weiterbearbeiten selbst - ein Statuswechsel, ein
    neues `next_action_due` oder das Schliessen des Vorgangs nimmt ihn aus der
    Auswahl.
    """
    try:
        async with session.begin_nested():
            result = await session.execute(
                _CLAIM_PENDING_QUERY,
                {
                    "today": today,
                    "silence_days": _CLAIM_SILENCE_DAYS,
                    "default_lead_special": _DEFAULT_LEAD_DAYS_SPECIAL,
                },
            )
            rows = result.mappings().all()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _UNDEFINED_TABLE:
            raise
        # Sicherungsmarke aus demselben Grund wie beim Sonderfenster, nur eine
        # Phase spaeter: ein Worker-Rollout vor der P5-Migration darf nicht die
        # Markierung der bereits versendeten Fristen mitreissen.
        logger.info(
            "Vorgangs-Push uebersprungen: insurance_claims existiert im Schema "
            "noch nicht."
        )
        return []

    alerts: list[ClaimFollowupAlert] = []
    for row in rows:
        reported_on: date | None = row["reported_on"]
        next_action_due: date | None = row["next_action_due"]
        action_overdue = next_action_due is not None and next_action_due < today

        if action_overdue and next_action_due is not None:
            action_on = next_action_due
        elif reported_on is not None:
            action_on = reported_on + timedelta(days=_CLAIM_SILENCE_DAYS)
        else:
            # Weder ein ueberfaelliger Schritt noch ein Meldedatum: die Abfrage
            # laesst das nicht zu, der Zweig ist die Absicherung dagegen, dass
            # eine spaetere Bedingung dort etwas anderes durchlaesst.
            continue

        days_left = (action_on - today).days
        lead_days = [int(value) for value in (row["lead_days"] or _DEFAULT_LEAD_DAYS_SPECIAL)]
        stage = current_stage(days_left, lead_days)
        if stage is None:
            continue
        stage_starts_on = action_on - timedelta(days=stage)
        if not is_new_stage(row["notified_at"], stage_starts_on):
            continue

        # Gewartet wird seit der Meldung, nicht seit dem Bezugstag: "seit 41
        # Tagen" ist die Zahl, die der Nutzer nachvollziehen kann. Ohne
        # Meldedatum bleibt der ueberfaellige Schritt der einzige Bezug.
        since = reported_on or next_action_due
        alerts.append(
            ClaimFollowupAlert(
                household_id=str(row["household_id"]),
                claim_id=str(row["claim_id"]),
                policy_id=str(row["policy_id"]) if row["policy_id"] is not None else None,
                days_waiting=(today - since).days if since is not None else 0,
                action_overdue=action_overdue,
                stage_days=stage,
            )
        )
    return alerts


async def load_push_tokens(
    session: AsyncSession, household_ids: list[str]
) -> dict[str, list[str]]:
    """Gueltige Expo-Token je Haushalt ueber dessen aktive Mitglieder."""
    if not household_ids:
        return {}
    result = await session.execute(_TOKEN_QUERY, {"household_ids": household_ids})
    tokens: dict[str, list[str]] = {}
    for row in result.mappings().all():
        tokens.setdefault(str(row["household_id"]), []).append(str(row["expo_token"]))
    return tokens


def deadline_body(days_left: int) -> str:
    """Der Rumpf des Fristen-Pushes - eine Tageszahl, sonst nichts.

    Keine Sparte, kein Versicherer, kein Betrag, keine Person. Was diese Frist
    betrifft, steht hinter dem Entsperren.
    """
    if days_left <= 0:
        return "Eine Frist laeuft heute ab."
    if days_left == 1:
        return "Eine Frist laeuft morgen ab."
    return f"Eine Frist laeuft in {days_left} Tagen ab."


def question_body(policy_count: int) -> str:
    """Der Rumpf der Rueckfrage - eine Anzahl, kein Countdown.

    Bewusst ohne Tageszahl: das Datum, um das es geht, ist geraten. Eine Zahl
    daneben liesse es belegt aussehen.
    """
    if policy_count == 1:
        return "Eine Versicherung braucht eine bestaetigte Angabe."
    return f"{policy_count} Versicherungen brauchen eine bestaetigte Angabe."


def special_termination_body(days_left: int) -> str:
    """Der Rumpf des Sonderkuendigungs-Pushes - ein Recht und eine Tageszahl.

    **Diese Funktion ist die Zusage aus Plan 7.1 in ihrer schaerfsten Form und
    gehoert deshalb durch einen Test gesichert** (services/worker/tests):
    egal welche Tageszahl hineingeht, es darf WEDER ein Betrag NOCH eine Person
    NOCH eine Versicherungsart herauskommen. Der Anlass ist eine
    Beitragserhoehung, der naheliegende Satz waere "Die PKV steigt um 48 EUR" -
    und der stuende auf dem Sperrbildschirm im Wartezimmer. Der Parameter ist
    ein `int`, damit hier gar nichts anderes ankommen KANN.

    Negative Tageszahlen kommen vor und duerfen nicht wie ein Countdown klingen:
    das Handlungsdatum ist dann vorbei, das rechtliche Fenster aber noch offen
    (siehe `select_special_termination_alerts`). "In -2 Tagen" waere Unsinn,
    "heute" waere eine Behauptung ueber einen Termin, den der Nutzer schon
    verpasst hat.
    """
    if days_left < 0:
        return "Ein Sonderkuendigungsrecht endet in Kuerze. Heute handeln."
    if days_left == 0:
        return "Ein Sonderkuendigungsrecht laeuft heute ab."
    if days_left == 1:
        return "Ein Sonderkuendigungsrecht laeuft morgen ab."
    return f"Ein Sonderkuendigungsrecht laeuft in {days_left} Tagen ab."


def claim_followup_body(days_waiting: int, *, action_overdue: bool) -> str:
    """Der Rumpf des Vorgangs-Pushes - eine Wartezeit, sonst nichts.

    **Dieselbe Zusage wie bei `special_termination_body`, an einer neuen
    Bruchstelle** (Plan 7.1, gehoert durch einen Test gesichert): der Anlass ist
    ein Schadenfall, und der naheliegende Satz waere "Wasserschaden 1.240 EUR
    wartet auf Rueckmeldung". Der traegt Betrag UND Hergang - und ueber den
    Hergang mitunter mehr ueber die Wohnung, als der Nutzer im Wartezimmer
    vorlesen moechte. Der Rumpf nennt deshalb nur, dass etwas wartet, und wie
    lange. Beide Parameter sind `int` bzw. `bool`, damit hier gar nichts
    anderes ankommen KANN.

    Zwei Saetze, weil es zwei Anlaesse sind: beim Schweigen wartet man auf den
    Versicherer, beim ueberfaelligen Schritt liegt der Ball beim Nutzer. "Wartet
    auf Rueckmeldung" waere im zweiten Fall eine Ausrede, die das Werkzeug dem
    Nutzer liefert.
    """
    if action_overdue:
        return "Zu einem Vorgang ist ein vorgemerkter Schritt faellig."
    if days_waiting <= 1:
        return "Ein Vorgang wartet auf Rueckmeldung."
    return f"Ein Vorgang wartet seit {days_waiting} Tagen auf Rueckmeldung."


def build_push_messages(
    deadlines: list[DeadlineAlert],
    questions: list[QuestionAlert],
    specials: list[SpecialTerminationAlert],
    tokens_by_household: dict[str, list[str]],
    claims: Sequence[ClaimFollowupAlert] = (),
) -> list[PushMessage]:
    """Baut die Nachrichten: je Frist eine, je Haushalt eine Rueckfrage.

    **Die Rueckfrage wird je Haushalt zusammengefasst, die Frist nicht.** Nach
    der Bestandsuebernahme tragen leicht ein Dutzend Policen ein angenommenes
    Ankerdatum; ein Dutzend gleichlautender Nachrichten an einem Morgen waere
    keine Rueckfrage mehr, sondern der Grund, Benachrichtigungen abzuschalten.
    Plan 7.1 nennt die zusammengefasste Form ausdruecklich als erlaubtes
    Beispiel ("Zwei Versicherungen brauchen eine Angabe."). Fristen bleiben
    einzeln: jede ist ein eigener Termin mit eigener Tageszahl, und es sind
    wenige - der Typ gilt nur fuer Policen ohne Vertrag.

    Der Datenteil traegt `policy_id`, weil der mobile Router darauf routet
    (apps/mobile/src/notifications/useNotificationResponseRouter.ts). Bei der
    zusammengefassten Rueckfrage ist das die erste betroffene Police; die
    vollstaendige Liste steht auf der Uebersicht unter "Jetzt wichtig".

    **Das Sonderfenster bleibt einzeln und traegt keine Vorgangs-ID.** Einzeln,
    weil jedes Fenster einen eigenen Tag hat und eine zusammengefasste Zahl die
    Dringlichkeit des knappsten verschluckte - und weil sie selten sind. Ohne
    Vorgangs-ID, weil der Datenteil des Versicherungs-Pushes auf `type`,
    `policy_id` und `policy_count` begrenzt ist: was dort liegt, ist der Vorrat,
    aus dem eine kuenftige lokale Benachrichtigung ihren Text anreichern
    koennte. Die Police fuehrt ohnehin auf die Seite, auf der die Mitteilung
    steht.

    **Der Vorgang bleibt ebenfalls einzeln und traegt keine Vorgangs-ID** - aus
    denselben zwei Gruenden. Hat er keine Police, faehrt der Datenteil ganz
    ohne ID: der Vorgang gegen eine fremde Versicherung hat keine, und die
    Uebersicht ist der richtige Landeplatz (der Router faengt den Fall bereits
    ab). Ein Platzhalter waere eine Detailseite zu einer erfundenen ID.

    **`claims` steht hinter der Tokenliste und nicht bei seinen Geschwistern.**
    Die Reihenfolge ist keine Nachlaessigkeit, sondern Ruecksicht auf die
    Aufrufer, die die drei aelteren Typen positionsweise uebergeben
    (services/worker/tests/test_insurance_alerts.py): eine Einsortierung an
    dritter Stelle haette dort die Tokenliste als Vorgangsliste gelesen.
    """
    messages: list[PushMessage] = []

    for alert in deadlines:
        body = deadline_body(alert.days_left)
        for token in tokens_by_household.get(alert.household_id, []):
            messages.append(
                PushMessage(
                    to=token,
                    title=_DEADLINE_TITLE,
                    body=body,
                    data={"type": _TYPE_DEADLINE, "policy_id": alert.policy_id},
                )
            )

    for special in specials:
        special_body = special_termination_body(special.days_left)
        for token in tokens_by_household.get(special.household_id, []):
            messages.append(
                PushMessage(
                    to=token,
                    title=_SPECIAL_TITLE,
                    body=special_body,
                    data={"type": _TYPE_SPECIAL, "policy_id": special.policy_id},
                )
            )

    for claim in claims:
        claim_body = claim_followup_body(
            claim.days_waiting, action_overdue=claim.action_overdue
        )
        for token in tokens_by_household.get(claim.household_id, []):
            # Je Nachricht ein eigener Datenteil, wie bei den drei anderen
            # Typen: eine gemeinsame Abbildung waere ein geteilter
            # veraenderlicher Zustand zwischen Geraeten desselben Haushalts.
            claim_data: dict[str, Any] = {"type": _TYPE_CLAIM}
            if claim.policy_id is not None:
                claim_data["policy_id"] = claim.policy_id
            messages.append(
                PushMessage(
                    to=token,
                    title=_CLAIM_TITLE,
                    body=claim_body,
                    data=claim_data,
                )
            )

    by_household: dict[str, list[QuestionAlert]] = {}
    for question in questions:
        by_household.setdefault(question.household_id, []).append(question)
    for household_id, group in by_household.items():
        body = question_body(len(group))
        for token in tokens_by_household.get(household_id, []):
            messages.append(
                PushMessage(
                    to=token,
                    title=_QUESTION_TITLE,
                    body=body,
                    data={
                        "type": _TYPE_QUESTION,
                        "policy_id": group[0].policy_id,
                        "policy_count": len(group),
                    },
                )
            )
    return messages


async def mark_alerts_notified(
    session: AsyncSession, household_ids: list[str], policy_ids: list[str]
) -> None:
    """Schreibt `notified_at` auf allen versendeten Policen fort."""
    if not policy_ids:
        return
    await session.execute(
        _MARK_NOTIFIED_QUERY,
        {"household_ids": household_ids, "policy_ids": policy_ids},
    )


async def mark_notices_notified(
    session: AsyncSession, household_ids: list[str], notice_ids: list[str]
) -> None:
    """Schreibt `notified_at` auf allen versendeten Beitragsmitteilungen fort.

    Getrennt von `mark_alerts_notified`, weil der Anker ein anderer ist - nicht
    aus Symmetrie, sondern weil eine gemeinsame Anweisung die Police mitmarkieren
    muesste und damit eine Vorlaufstufe der ordentlichen Frist verbrauchte.
    """
    if not notice_ids:
        return
    await session.execute(
        _MARK_NOTICES_NOTIFIED_QUERY,
        {"household_ids": household_ids, "notice_ids": notice_ids},
    )


async def mark_claims_notified(
    session: AsyncSession, household_ids: list[str], claim_ids: list[str]
) -> None:
    """Schreibt `notified_at` auf allen versendeten Vorgaengen fort.

    Der leere Fall kehrt vor der Anweisung um, und das ist hier mehr als eine
    Ersparnis: solange `insurance_claims` im Schema fehlt, liefert die Auswahl
    keine Zeilen - ohne diese Ruecknahme liefe die Markierung in denselben
    Tabellenfehler, den die Auswahl gerade abgefangen hat, und risse die
    Markierung der uebrigen Typen mit.
    """
    if not claim_ids:
        return
    await session.execute(
        _MARK_CLAIMS_NOTIFIED_QUERY,
        {"household_ids": household_ids, "claim_ids": claim_ids},
    )


async def check_insurance_alerts(today: date | None = None) -> None:
    """Versendet die faelligen Versicherungshinweise und markiert sie.

    Reihenfolge wie bei `check_contract_alerts`: erst senden, dann markieren.
    Andersherum verschluckte ein Abbruch mitten im Batch die Nachricht
    dauerhaft; so wird sie im schlechtesten Fall einmal doppelt zugestellt - und
    genau deshalb laeuft der Actor mit max_retries=0 (worker/actors.py).

    Markiert werden nur Policen, deren Haushalt tatsaechlich ein Geraet hatte.
    `contract_alerts` markiert an dieser Stelle unbesehen alles; hier waere das
    teurer, weil `notified_at` bei einer Frist nicht nur "gesendet" bedeutet,
    sondern die Vorlaufstufe verbraucht - eine Markierung ohne Versand liesse
    eine ganze Eskalationsstufe ausfallen.
    """
    stichtag = today or date.today()
    session_factory = get_session_factory()
    async with session_factory() as session:
        deadlines, questions = await select_pending_alerts(session, today=stichtag)
        specials = await select_special_termination_alerts(session, today=stichtag)
        claims = await select_claim_followup_alerts(session, today=stichtag)
        if not deadlines and not questions and not specials and not claims:
            logger.info("Keine offenen Versicherungshinweise fuer Push-Benachrichtigungen.")
            return

        household_ids = sorted(
            {alert.household_id for alert in deadlines}
            | {question.household_id for question in questions}
            | {special.household_id for special in specials}
            | {claim.household_id for claim in claims}
        )
        tokens_by_household = await load_push_tokens(session, household_ids)
        messages = build_push_messages(
            deadlines, questions, specials, tokens_by_household, claims
        )

        if messages:
            tickets = await send_expo_push_batch(messages)
            for ticket in tickets:
                if ticket.get("status") == "error":
                    logger.warning("Expo-Push-Ticket-Fehler: %s", ticket.get("message"))

        sent: list[tuple[str, str]] = [
            (alert.household_id, alert.policy_id)
            for alert in deadlines
            if tokens_by_household.get(alert.household_id)
        ]
        sent.extend(
            (question.household_id, question.policy_id)
            for question in questions
            if tokens_by_household.get(question.household_id)
        )
        await mark_alerts_notified(
            session, [item[0] for item in sent], [item[1] for item in sent]
        )
        sent_notices: list[tuple[str, str]] = [
            (special.household_id, special.notice_id)
            for special in specials
            if tokens_by_household.get(special.household_id)
        ]
        await mark_notices_notified(
            session,
            [item[0] for item in sent_notices],
            [item[1] for item in sent_notices],
        )
        sent_claims: list[tuple[str, str]] = [
            (claim.household_id, claim.claim_id)
            for claim in claims
            if tokens_by_household.get(claim.household_id)
        ]
        await mark_claims_notified(
            session,
            [item[0] for item in sent_claims],
            [item[1] for item in sent_claims],
        )
        await session.commit()

        logger.info(
            "Versicherungs-Push verarbeitet: %s Frist(en), %s Rueckfrage(n), "
            "%s Sonderfenster, %s Vorgang/Vorgaenge in %s Haushalt(en), "
            "%s Nachricht(en).",
            len(deadlines),
            len(questions),
            len(specials),
            len(claims),
            len(household_ids),
            len(messages),
        )
