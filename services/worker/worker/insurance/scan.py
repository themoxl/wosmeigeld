"""Naechtlicher Versicherungslauf (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 8.2).

**Neun Schritte, nicht elf.** Der Plan zaehlt elf; zwei davon lesen Tabellen,
die es auch in P5 nicht gibt (`insurance_needs_profile` und
`insurance_person_profile` ab P6). Ein Schritt ohne Quelltabelle waere kein
vorbereiteter Schritt, sondern toter Code mit einer falschen Zusage im Namen.
Die zwei fehlen deshalb vollstaendig und kommen mit ihrer Quelle. Schritt 2a ist
mit `insurance_premium_notices` in P3 dazugekommen, die Schritte 2b und 5 mit
`insurance_claims` in P5.

| # | Schritt                  | liest / schreibt                              |
| - | ------------------------ | --------------------------------------------- |
| 1 | Grabstein-Reparatur      | schreibt `contract_id` um, meldet Waisen       |
| 1a| Rueckfrage statt Frist   | meldet angenommene Anker (KEIN Push von hier)  |
| 2a| Sonderfenster Beitrag    | meldet offene und verstrichene Fenster         |
| 2b| Sonderfenster Schaden    | meldet Fenster UND fehlende Fristanker         |
| 3 | Wartezeitende            | meldet den Tag, ab dem der Schutz greift       |
| 5 | Vorgang ohne Rueckmeldung| meldet liegengebliebene Vorgaenge              |
| 6 | Kfz-Wiedervorlage        | meldet im Januar die Hauptfaelligkeitsfrage    |
| 7 | Ruhendstellungsablauf    | meldet auslaufende Ruheversicherungen          |
| 9 | Statusdivergenz          | meldet, korrigiert NICHT                       |

**Genau ein Schritt schreibt.** Schritt 1 haengt eine Police vom Grabstein auf
den Zielvertrag um ([Plan 4.12](docs/VERSICHERUNGEN-PLAN.md)); ohne ihn verloere
die Police lautlos ihren Beitrag. Alle uebrigen acht sind Befunde. Das ist
keine Zurueckhaltung aus Bequemlichkeit, sondern Plan 4.16 Punkt 3: eine still
korrigierte Divergenz nimmt dem Nutzer die Entscheidung, die nur er treffen
kann. Bei Schritt 2b ist es sogar ausdrueckliches Recht: das Werkzeug erklaert
ein Sonderkuendigungsrecht niemals von sich aus fuer verfallen (Plan 3.12,
Pruefung 3).

**Wohin die Befunde gehen.** In P2 gibt es keine Befundtabelle - `build_tasks`
(services/api/app/services/insurance_service.py) rechnet die Aufgabenliste bei
jedem Aufruf frisch aus dem Policenzustand. Der Lauf gibt seine Befunde deshalb
strukturiert zurueck und protokolliert sie; wer sie speichern will, bekommt mit
`InsuranceFinding` bereits die Form dafuer. Ein eigener Cache waere genau die
Befundtabelle, die Plan 8.4 fuer den Bedarfsabgleich ausschliesst.

**Warum die Befunde keine Bezeichnung tragen, nur IDs.** Ein Befund landet im
Protokoll. "Barmenia PKV" oder "Berufsunfaehigkeit" darin waere eine
Gesundheitsangabe in einer Datei, die niemand als solche behandelt (Plan 7.1
begruendet dieselbe Strenge fuer den Sperrbildschirm). Die Oberflaeche loest
`policy_id` selbst auf - sie liest ohnehin schon die Police.

**Warum ein eigener Actor und kein dritter Schritt in `reconcile.py`.** Plan 8.2
sieht den Aufruf dort vor. `worker/contracts/reconcile.py` laeuft aber ueber
`list_reconcilable_households` - Haushalte mit mindestens einem laufenden
Vertrag. Eine Police in Geldfall (b) oder (c) hat per Definition keinen Vertrag
(Plan 2.1); ein Haushalt, der ausschliesslich solche Policen fuehrt, kaeme in
dieser Liste nie vor und wuerde nie geprueft. Genau diese Haushalte sind der
Grund, warum die Police ein eigenes Fachobjekt ist. Der Lauf holt sich seine
Haushalte deshalb selbst aus `insurance_policies`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final
from uuid import UUID

from sqlalchemy import RowMapping, TextClause, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from worker.db import get_session_factory

logger = logging.getLogger(__name__)

# Zustaende, in denen eine Police noch etwas schuetzt und deshalb noch etwas zu
# tun sein kann. Deckungsgleich mit `insurance_service._LIVE_STATUSES` - eine
# abweichende Liste hiesse, der Nachtlauf haette eine andere Meinung darueber,
# was eine laufende Police ist, als die Seite, die sie anzeigt.
_LIVE_STATUSES: Final[list[str]] = ["active", "suspended", "paid_up", "dormant"]

# Alle drei Kfz-Zeilen, obwohl der Kernkatalog nur `motor_liability` seedet
# (Plan 8.2 Schritt 6). Die beiden Kaskozeilen kommen mit Welle 2 in P4 - dann
# greift der Schritt ohne Aenderung, statt beim Seed-Nachtrag vergessen zu
# werden.
_MOTOR_LINES: Final[list[str]] = ["motor_liability", "motor_comprehensive", "motor_partial"]

# Vorlauf der Ruhendstellungsmeldung. Aus Plan 8.2 Schritt 7 ("dormant_until <=
# today + 30"): wer sein Fahrzeug wieder anmelden will, braucht die
# Versicherungsbestaetigung vor dem Termin, nicht danach. Der Wert ist bewusst
# ein Fenster und kein Stichtag - dadurch erfasst er auch die bereits
# abgelaufenen Ruhendstellungen, nach denen der Auftrag ausdruecklich fragt.
_DORMANT_HORIZON_DAYS: Final = 30

# Die Kfz-Wiedervorlage gehoert zum 01.01. Sie laeuft trotzdem die erste
# Januarwoche: sie ist die einzige Gelegenheit im Jahr, und ein Worker, der in
# der Neujahrsnacht steht, verschoebe die Frage um zwoelf Monate. Der Befund
# wiederholt sich dadurch bis zu siebenmal im Protokoll - er loest keinen Push
# aus, es entsteht also keine wiederholte Stoerung beim Nutzer.
_MOTOR_REVIEW_LAST_DAY: Final = 7

# Voreinstellung der Sonderfenster-Staffel aus 0001_schema.sql
# (`insurance_settings.lead_days_special` default '{0,7,2}'), falls der Haushalt
# noch keine Zeile in den Einstellungen hat. Bewusst dieselbe Liste wie im Push
# und nicht eine eigene Zahl: aus ihrer weitesten Stufe entsteht der Horizont
# dieses Schrittes (siehe `_SPECIAL_TERMINATION_QUERY`).
_DEFAULT_LEAD_DAYS_SPECIAL: Final[list[int]] = [0, 7, 2]

# Wie lange ein bereits verstrichenes Sonderkuendigungsfenster noch gemeldet
# wird. Ohne Deckel wiederholte sich der Befund jede Nacht bis in alle Ewigkeit -
# eine unbestaetigte Mitteilung altert nicht von selbst. Zwei Wochen sind
# derselbe Altersdeckel, den Plan 8.3 dem Push gegen den Schwall beim ersten
# Lauf gibt.
_SPECIAL_EXPIRED_LOOKBACK_DAYS: Final = 14

# Schweigefrist eines gemeldeten Vorgangs (Plan 8.2 Schritt 5: "reported_on <
# today - 30"). Dreissig Tage sind die Spanne, nach der ein Sachbearbeiter sich
# gemeldet haben sollte - kuerzer waere eine Mahnung an den Versicherer fuer
# etwas, das noch in Bearbeitung ist, laenger liesse den Vorgang liegen.
_CLAIM_SILENCE_DAYS: Final = 30

# SQLSTATE "undefined_table". Worker-Image und Datenbankschema werden getrennt
# ausgerollt; ein Worker, der vor der P3-Migration startet, findet
# `insurance_premium_notices` noch nicht vor - einer vor der P5-Migration
# `insurance_claims` und `insurance_claim_payments`.
_UNDEFINED_TABLE: Final = "42P01"

# Merges koennen ketten (A in B, B in C). Der Deckel begrenzt die Rekursion
# gegen einen Zyklus in den Daten; erreicht sie ihn, bleibt die Police auf dem
# letzten aufgeloesten Ziel stehen, statt den Lauf haengen zu lassen.
_MERGE_CHAIN_MAX_DEPTH: Final = 10


@dataclass(frozen=True)
class InsuranceFinding:
    """Ein Befund des Laufs - eine Handlung mit ihrer Begruendung.

    `what` und `why` sind dieselbe Aufteilung wie in `InsuranceTask`
    (services/api): was zu tun ist und warum es sonst schiefgeht. Bewusst ohne
    Bezeichnung der Police, siehe Modulkopf.

    **`policy_id` ist seit P5 optional, und das ist keine Bequemlichkeit.** Ein
    Schadenvorgang kann sich gegen eine FREMDE Versicherung richten
    (Plan 4.8: `insurance_claims.policy_id` ist nullable) - der Vorgang liegt
    dann genauso liegen, aber es gibt keine eigene Police, auf die der Befund
    zeigen koennte. Ein Platzhalter dort waere eine erfundene Zuordnung.

    **`claim_id` steht daneben und ersetzt sie nicht.** Eine Police traegt
    mehrere Vorgaenge; ohne die Vorgangs-ID sagte ein Befund, dass irgendetwas
    an dieser Police liegengeblieben ist, aber nicht was. Es bleibt bei IDs, aus
    demselben Grund wie im Modulkopf: kein Titel, keine Sparte, kein Betrag.
    """

    kind: str
    household_id: str
    policy_id: str | None
    what: str
    why: str
    due_on: date | None = None
    claim_id: str | None = None


@dataclass(frozen=True)
class InsuranceScanResult:
    """Ergebnis eines Haushaltslaufs."""

    household_id: str
    repaired_count: int = 0
    findings: list[InsuranceFinding] = field(default_factory=list)

    def counts_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Texte je Befundart. An einer Stelle, damit sie sich zwischen Lauf und
# spaeterer Anzeige nicht auseinanderentwickeln.
# ---------------------------------------------------------------------------
_TEXTS: Final[dict[str, tuple[str, str]]] = {
    "merged_contract_repaired": (
        "Nichts - erledigt",
        "Der Vertrag dieser Police wurde zusammengefuehrt; die Police zeigt "
        "jetzt auf den Zielvertrag.",
    ),
    "merged_contract_conflict": (
        "Beitragsanteil pruefen",
        "Der Vertrag dieser Police wurde zusammengefuehrt, der Zielvertrag ist "
        "aber bereits vollstaendig auf andere Policen verteilt.",
    ),
    "orphaned_contract": (
        "Beitrag neu zuordnen",
        "Der Vertrag zu dieser Police wurde geloescht. Der Beitrag zaehlt "
        "seither nicht mehr in die Fixkosten.",
    ),
    "unconfirmed_main_due": (
        "Hauptfaelligkeit bestaetigen",
        "Das Datum ist eine Annahme aus dem Katalog - bis zur Bestaetigung gibt "
        "es keinen Countdown und keine Erinnerung.",
    ),
    "unconfirmed_notice_terms": (
        "Bezugspunkt bestaetigen",
        "Die Fristlaenge ist aus Tagen umgerechnet; der Bezugspunkt der "
        "Kuendigungsfrist steht damit nicht fest.",
    ),
    "special_termination_open": (
        "Sonderkuendigung entscheiden",
        "Nach der Beitragsanpassung besteht ein Sonderkuendigungsrecht - das "
        "Fenster schliesst sich in Kuerze.",
    ),
    "special_termination_expired": (
        "Nichts - Frist verstrichen",
        "Das Sonderkuendigungsrecht zur Beitragsanpassung ist abgelaufen; die "
        "Anpassung bleibt bestehen.",
    ),
    "waiting_period_ended": (
        "Nichts - ab heute geschuetzt",
        "Die vereinbarte Wartezeit ist abgelaufen; ab heute besteht Schutz.",
    ),
    "motor_annual_review": (
        "Hauptfaelligkeit und Beitrag pruefen",
        "Ein Fahrzeugwechsel verschiebt die Hauptfaelligkeit - zum Jahreswechsel "
        "lohnt der Blick auf Termin, SF-Klasse und Beitrag.",
    ),
    "dormant_ending": (
        "Fahrzeug anmelden oder Vertrag beenden",
        "Die Ruheversicherung endet. Ohne Anmeldung besteht danach kein Schutz.",
    ),
    "status_divergence_policy_ended": (
        "Vertrag pruefen",
        "Die Police ist beendet, der Vertrag laeuft weiter - der Beitrag zaehlt "
        "weiter in die Fixkosten.",
    ),
    "status_divergence_contract_ended": (
        "Police pruefen",
        "Der Vertrag ist beendet, die Police gilt weiter als laufender Schutz.",
    ),
    # --- Schritt 2b: Sonderkuendigungsrecht nach einem Schadenfall (P5) ------
    # Die beiden "pending"-Texte sind der Kern von Leitsatz 3: sie sagen, dass
    # eine Frist NICHT laeuft, statt eine zu behaupten. Keiner von beiden nennt
    # die Sparte, obwohl der Anker aus ihr stammt - das Protokoll bekommt die
    # Rechtslage, nicht die Versicherungsart (Modulkopf).
    "claim_negotiations_pending": (
        "Abschluss der Verhandlungen festhalten",
        "Das Sonderkuendigungsrecht endet einen Monat nach Abschluss der "
        "Verhandlungen ueber die Entschaedigung - dieses Datum steht noch nicht "
        "fest.",
    ),
    "claim_negotiations_anchor_suggested": (
        "Abschluss der Verhandlungen bestaetigen",
        "Der Vorgang ist vollstaendig anerkannt und bezahlt; die letzte "
        "zugeordnete Zahlung koennte den Abschluss belegen. Festhalten kann ihn "
        "nur der Nutzer.",
    ),
    "claim_decision_pending": (
        "Anerkennung oder Ablehnung festhalten",
        "Das Sonderkuendigungsrecht dieser Sparte laeuft ab Anerkennung, "
        "Ablehnung oder Rechtskraft - dieser Zeitpunkt ist nicht festgehalten.",
    ),
    "claim_special_termination_open": (
        "Sonderkuendigung entscheiden",
        "Nach dem Schadenfall besteht ein Sonderkuendigungsrecht; das Fenster "
        "schliesst sich einen Monat nach Abschluss der Verhandlungen.",
    ),
    "claim_special_termination_expired": (
        "Nichts - Frist verstrichen",
        "Das Sonderkuendigungsrecht nach dem Schadenfall ist abgelaufen; der "
        "Schutz laeuft unveraendert weiter.",
    ),
    # --- Schritt 5: Vorgaenge ohne Rueckmeldung (P5) -------------------------
    "claim_awaiting_response": (
        "Beim Versicherer nachfassen",
        "Seit der Meldung ist am Vorgang nichts geschehen - ohne Nachfrage "
        "bleibt er liegen.",
    ),
    "claim_next_action_overdue": (
        "Vorgemerkten Schritt erledigen",
        "Der vorgemerkte naechste Schritt ist ueberfaellig; der Vorgang wartet "
        "darauf.",
    ),
}


def _finding(
    kind: str,
    *,
    household_id: str,
    policy_id: str | None,
    due_on: date | None = None,
    claim_id: str | None = None,
) -> InsuranceFinding:
    what, why = _TEXTS[kind]
    return InsuranceFinding(
        kind=kind,
        household_id=household_id,
        policy_id=policy_id,
        what=what,
        why=why,
        due_on=due_on,
        claim_id=claim_id,
    )


# ---------------------------------------------------------------------------
# Abfragen
# ---------------------------------------------------------------------------

# Haushalte mit mindestens einer Police, die noch etwas bedeuten kann.
# 'entered_in_error' faellt heraus: das ist der Ersatz fuer das Loeschen.
_HOUSEHOLDS_QUERY = text(
    "select distinct household_id as household_id "
    "from insurance_policies "
    "where status <> 'entered_in_error'"
)

# Schritt 1, Teil 1: Policen auf einem Grabstein. Rekursiv, weil Merges ketten
# koennen - A in B, B in C laesst eine Police sonst auf B stehen, das selbst
# schon aus allen Lesepfaden gefiltert ist (contract_service.py:329).
#
# `distinct on (policy_id) ... order by depth desc` nimmt das Ende der Kette.
# Der abschliessende Join auf `contracts` haelt das Ziel im selben Haushalt:
# der Fremdschluessel der Police ist (contract_id, household_id), ein Ziel aus
# einem fremden Haushalt liefe in eine Fremdschluesselverletzung.
_MERGED_CANDIDATES_QUERY = text(
    "with recursive chain (policy_id, target_id, depth) as ( "
    "    select p.id, c.merged_into_id, 0 "
    "      from insurance_policies p "
    "      join contracts c "
    "        on c.id = p.contract_id and c.household_id = p.household_id "
    "     where p.household_id = cast(:household_id as uuid) "
    "       and p.contract_id is not null "
    "       and p.status <> 'entered_in_error' "
    "       and c.merged_into_id is not null "
    "  union all "
    "    select ch.policy_id, c.merged_into_id, ch.depth + 1 "
    "      from chain ch "
    "      join contracts c "
    "        on c.id = ch.target_id and c.household_id = cast(:household_id as uuid) "
    "     where c.merged_into_id is not null "
    "       and ch.depth < cast(:max_depth as int) "
    ") "
    "select distinct on (ch.policy_id) "
    "       ch.policy_id as policy_id, ch.target_id as target_contract_id "
    "  from chain ch "
    "  join contracts t "
    "    on t.id = ch.target_id and t.household_id = cast(:household_id as uuid) "
    " order by ch.policy_id, ch.depth desc"
)

# Die einzige schreibende Anweisung des Laufs. `contract_id is not null` in der
# Bedingung: haengt ein Nutzer die Police zwischen Auswahl und Update ab, soll
# der Lauf sie nicht wieder anhaengen.
_MERGED_REPAIR_UPDATE = text(
    "update insurance_policies "
    "   set contract_id = cast(:target_contract_id as uuid) "
    " where id = cast(:policy_id as uuid) "
    "   and household_id = cast(:household_id as uuid) "
    "   and contract_id is not null"
)

# Schritt 1, Teil 2: die Waisen. `contract_id is null` bei
# data_source='from_contract' heisst, der Vertrag wurde geloescht - der
# Fremdschluessel setzt nur die Spalte auf null (0001_schema.sql: "on delete
# set null (contract_id)"), damit das Loeschen des Vertrags nicht scheitert.
# `external_premium_minor is null` schliesst die Policen aus, denen inzwischen
# ein fremder Zahler zugeordnet wurde: die haben ihren Beitrag nicht verloren,
# sondern einen neuen bekommen.
_ORPHANED_QUERY = text(
    "select p.id as policy_id "
    "from insurance_policies p "
    "where p.household_id = cast(:household_id as uuid) "
    "and p.contract_id is null "
    "and p.data_source = 'from_contract' "
    "and p.external_premium_minor is null "
    "and p.status = any(cast(:live_statuses as text[])) "
    "order by p.created_at"
)

# Schritt 1a: die beiden Unsicherheitsquellen derselben Frist. Der Anker
# ('catalog_suggested') und die Fristlaenge ('derived_from_days') sind zwei
# getrennte Rueckfragen mit zwei getrennten Bestaetigungspfaden
# (POST .../confirm-main-due bzw. .../confirm-notice-terms), deshalb zwei
# Befunde und nicht einer.
_ASSUMED_TERMS_QUERY = text(
    "select p.id as policy_id, p.main_due_on as main_due_on, "
    "       p.main_due_source as main_due_source, "
    "       p.notice_terms_source as notice_terms_source "
    "from insurance_policies p "
    "where p.household_id = cast(:household_id as uuid) "
    "and p.status = any(cast(:live_statuses as text[])) "
    "and (p.main_due_source = 'catalog_suggested' "
    "     or p.notice_terms_source = 'derived_from_days') "
    "order by p.created_at"
)

# Schritt 2a: Sonderkuendigungsfenster einer Beitragsanpassung (Plan 8.2
# Punkt 2, Quelle `insurance_premium_notices` aus Plan 4.7).
#
# Die beiden ersten Bedingungen sind woertlich der Teilindex der Tabelle
# (`insurance_premium_notices_open_idx`): "offenes Fenster" heisst dort
# `special_termination_until is not null and acknowledged_at is null`. Ein
# eigener Begriff davon waere eine zweite Definition desselben Zustands - und
# ginge am Index vorbei.
#
# `special_termination_until is null` bleibt deshalb bewusst stumm: das ist die
# Zeile, fuer die der Fachdienst KEIN Fenster gerechnet hat (Leistung
# mitgestiegen, Sparte ohne Sonderrecht, Kfz ohne ueberschrittenen
# Vergleichsbeitrag). Ein Befund daraus waere ein zweites Urteil ueber ein
# Recht, das hier niemand nachrechnet.
#
# `not n.with_benefit_increase` ist die zweite Sicherung daneben, dieselbe
# Bauart wie `contract_id is null` im Fristen-Push: Paragraph 40 Abs. 1 VVG
# greift nur bei einer Erhoehung OHNE entsprechende Leistungserweiterung.
#
# `not p.dynamic_agreed` ist Leitsatz 3: eine vereinbarte Dynamik in BU,
# Ruerup oder Risikoleben ist planmaessig und keine Erhoehung im Sinne des
# Paragraphen 40 - sie darf keine Rueckfrage ausloesen.
#
# Der Horizont ist die WEITESTE Vorlaufstufe des Haushalts
# (`lead_days_special`, Voreinstellung sofort/7/2) und keine zweite Zahl
# daneben: der Befund soll genau die Fenster erklaeren, zu denen der Push um
# 08:45 etwas verschickt. Eine eigene Horizontkonstante hier hiesse, Protokoll
# und Sperrbildschirm meldeten an verschiedenen Tagen dasselbe Fenster.
_SPECIAL_TERMINATION_QUERY = text(
    "select n.id as notice_id, n.policy_id as policy_id, "
    "       n.special_termination_until as window_ends_on, "
    "       n.recommended_action_on as recommended_action_on "
    "from insurance_premium_notices n "
    "join insurance_policies p "
    "  on p.id = n.policy_id and p.household_id = n.household_id "
    "left join insurance_settings s on s.household_id = n.household_id "
    "where n.household_id = cast(:household_id as uuid) "
    "and n.acknowledged_at is null "
    "and n.special_termination_until is not null "
    "and not n.with_benefit_increase "
    "and not p.dynamic_agreed "
    "and p.status = any(cast(:live_statuses as text[])) "
    "and n.special_termination_until >= cast(:today as date) "
    "    - cast(:expired_lookback_days as int) "
    "and coalesce(n.recommended_action_on, n.special_termination_until) <= "
    "    cast(:today as date) + (select coalesce(max(d), 0) from unnest("
    "      coalesce(s.lead_days_special, cast(:default_lead_special as int[]))"
    "    ) as d) "
    "order by n.special_termination_until, n.id"
)

# Schritt 2b: Sonderkuendigungsfenster nach einem Schadenfall (Plan 8.2 Punkt
# 2b, Quelle `insurance_claims` aus Plan 4.8).
#
# Die erste Bedingung ist woertlich der Teilindex der Tabelle
# (`insurance_claims_termination_idx`): "ein Recht, das noch etwas bedeutet"
# heisst dort `special_termination_state in ('open_undetermined','open_until')`.
# 'none' faellt damit heraus (kein Sonderrecht) und 'expired' ebenfalls - der
# Verfall ist dort bereits festgehalten und braucht keine naechtliche
# Wiederholung.
#
# `c.termination_basis <> 'none'` ist die zweite Sicherung daneben, dieselbe
# Bauart wie `not n.with_benefit_increase` in Schritt 2a: die Sparte entscheidet
# ueber den Anker (Plan 4.8, `insurance_lines.default_claim_termination_basis`),
# und 'none' - Kranken/PKV - heisst kein Sonderrecht.
#
# **Der Join auf die Police ist ein INNER JOIN, und das ist eine fachliche
# Aussage.** Ein Vorgang gegen eine FREMDE Versicherung (policy_id null, Plan
# 4.8) begruendet kein Recht, den EIGENEN Vertrag zu kuendigen - Paragraph 92
# VVG gibt es in dem Vertrag, in dem der Versicherungsfall eingetreten ist.
# Schritt 5 sieht denselben Vorgang trotzdem, denn liegenbleiben kann er auch
# ohne eigene Police.
#
# `not p.is_health_scoped` ist kein Misstrauen gegen den Trigger aus Plan 4.8:
# der prueft beim Schreiben des VORGANGS. Wandert eine Police spaeter auf eine
# gesundheitsnahe Sparte, bleibt ein alter Vorgang stehen - und der gehoert dann
# zum Erstattungsmodul (Leitsatz 2), nicht in diesen Lauf.
#
# Der Zahlungsvorschlag steht als Unterabfrage daneben und nicht als weiterer
# Join: er darf die Zeile nicht vervielfachen. Er nimmt nur Zahlungen MIT
# Kontoumsatz, denn nur die tragen `certainty = 'belegt'` (Plan 4.8, CHECK auf
# `negotiations_closed_transaction_id`), und nur eingehende - eine
# Selbstbeteiligung nach draussen schliesst keine Verhandlung ab.
_CLAIM_SPECIAL_TERMINATION_QUERY = text(
    "select c.id as claim_id, c.policy_id as policy_id, "
    "       c.termination_basis as termination_basis, "
    "       c.special_termination_state as termination_state, "
    "       c.special_termination_until as window_ends_on, "
    "       c.decision as decision, c.status as status, "
    "       c.decided_on as decided_on, "
    "       (select max(pay.booked_on) from insurance_claim_payments pay "
    "         where pay.claim_id = c.id and pay.household_id = c.household_id "
    "           and pay.direction = 'inbound' "
    "           and pay.transaction_id is not null) as last_payment_on "
    "from insurance_claims c "
    "join insurance_policies p "
    "  on p.id = c.policy_id and p.household_id = c.household_id "
    "where c.household_id = cast(:household_id as uuid) "
    "and c.special_termination_state in ('open_undetermined', 'open_until') "
    "and c.termination_basis <> 'none' "
    "and not p.is_health_scoped "
    "and p.status = any(cast(:live_statuses as text[])) "
    "and (c.special_termination_until is null "
    "     or c.special_termination_until >= cast(:today as date) "
    "         - cast(:expired_lookback_days as int)) "
    "order by c.special_termination_until nulls last, c.id"
)

# Schritt 5: Vorgaenge ohne Rueckmeldung (Plan 8.2 Punkt 5). Die drei Status
# und die beiden Bedingungen stehen dort woertlich; die Statusliste ist eine
# Teilmenge des Teilindex `insurance_claims_open_idx` (alles ausser
# 'paid','closed','withdrawn'), der Schritt liest also ueber ihn.
#
# `left join` statt `join`, anders als in Schritt 2b: hier zaehlt nicht das
# Kuendigungsrecht an der eigenen Police, sondern dass ein Vorgang liegt. Der
# Schaden gegen den Haftpflichtversicherer eines Dritten hat keine eigene Police
# und wird trotzdem vergessen - er ist sogar der Fall, an den niemand denkt.
#
# **Kein Filter auf den Policenstatus**, wieder anders als 2b: ein Vorgang wird
# noch abgewickelt, wenn die Police laengst gekuendigt ist. Der Anspruch aus
# einem Schaden ueberlebt den Vertrag.
_CLAIM_FOLLOWUP_QUERY = text(
    "select c.id as claim_id, c.policy_id as policy_id, "
    "       c.reported_on as reported_on, c.next_action_due as next_action_due "
    "from insurance_claims c "
    "left join insurance_policies p "
    "  on p.id = c.policy_id and p.household_id = c.household_id "
    "where c.household_id = cast(:household_id as uuid) "
    "and c.status in ('reported', 'in_review', 'info_requested') "
    "and not coalesce(p.is_health_scoped, false) "
    "and ( "
    "  (c.reported_on is not null "
    "   and c.reported_on < cast(:today as date) - cast(:silence_days as int)) "
    "  or (c.next_action_due is not null "
    "      and c.next_action_due < cast(:today as date)) "
    ") "
    "order by coalesce(c.next_action_due, c.reported_on), c.id"
)

# Schritt 3: Wartezeitende, tagesgenau und nicht als Fenster. Der Befund sagt
# "ab heute geschuetzt"; an jedem anderen Tag waere derselbe Satz falsch, und
# einen Dedup-Anker fuer eine mehrtaegige Wiederholung gibt es in P2 nicht.
_WAITING_END_QUERY = text(
    "select p.id as policy_id, (p.starts_on + p.waiting_period_days) as protected_from "
    "from insurance_policies p "
    "where p.household_id = cast(:household_id as uuid) "
    "and p.status = any(cast(:live_statuses as text[])) "
    "and p.starts_on is not null "
    "and p.waiting_period_days is not null "
    "and p.waiting_period_days > 0 "
    "and p.starts_on + p.waiting_period_days = cast(:today as date) "
    "order by p.created_at"
)

# Schritt 6: Kfz zum Jahreswechsel.
_MOTOR_QUERY = text(
    "select p.id as policy_id, p.main_due_on as main_due_on "
    "from insurance_policies p "
    "where p.household_id = cast(:household_id as uuid) "
    "and p.status = any(cast(:live_statuses as text[])) "
    "and p.line_key = any(cast(:motor_lines as text[])) "
    "order by p.created_at"
)

# Schritt 7: Ruhendstellung laeuft ab.
_DORMANT_QUERY = text(
    "select p.id as policy_id, p.dormant_until as dormant_until "
    "from insurance_policies p "
    "where p.household_id = cast(:household_id as uuid) "
    "and p.status = 'dormant' "
    "and p.dormant_until is not null "
    "and p.dormant_until <= cast(:today as date) + cast(:horizon_days as int) "
    "order by p.dormant_until"
)

# Schritt 9: Statusdivergenz, beide Richtungen.
#
# `c.merged_into_id is null`: ein Grabstein ist keine Divergenz, sondern
# Schritt 1 - und der hat im selben Lauf bereits davor gelaufen.
# 'paused' fehlt in der zweiten Richtung mit Absicht: eine Pause ist ein
# voruebergehender Zustand, den der Nutzer selbst gesetzt hat, keine
# Abweichung. Beendet ist beendet.
_DIVERGENCE_QUERY = text(
    "select p.id as policy_id, p.status as policy_status, c.status as contract_status "
    "from insurance_policies p "
    "join contracts c on c.id = p.contract_id and c.household_id = p.household_id "
    "where p.household_id = cast(:household_id as uuid) "
    "and c.merged_into_id is null "
    "and ( "
    "  (p.status in ('cancelled', 'ended', 'paid_up') and c.status = 'active') "
    "  or (p.status in ('active', 'suspended') "
    "      and c.status in ('cancelled', 'ended', 'not_a_contract')) "
    ") "
    "order by p.id"
)


# ---------------------------------------------------------------------------
# Die neun Schritte
# ---------------------------------------------------------------------------


async def _repair_gravestones(
    session: AsyncSession, household_id: str
) -> tuple[int, list[InsuranceFinding]]:
    """Schritt 1: Policen vom Grabstein auf den Zielvertrag umhaengen.

    Je Police eine eigene Sicherungsmarke (`begin_nested`) statt eines einzigen
    Sammel-Updates. Grund: der Fachtrigger `validate_insurance_policy` prueft
    beim Umhaengen die Summe der Beitragsanteile am Zielvertrag und wirft bei
    Ueberschreitung 23514. Trugen beide zusammengefuehrten Vertraege je eine
    Police mit vollem Anteil - der Normalfall in P1, wo jede Police 10000 bp
    haelt -, dann kippt genau dieser Fall. Ohne Sicherungsmarke risse er den
    gesamten Haushaltslauf mit und die uebrigen acht Schritte lieferten nichts.
    """
    rows = (
        await session.execute(
            _MERGED_CANDIDATES_QUERY,
            {"household_id": household_id, "max_depth": _MERGE_CHAIN_MAX_DEPTH},
        )
    ).mappings().all()

    repaired = 0
    findings: list[InsuranceFinding] = []
    for row in rows:
        policy_id = str(row["policy_id"])
        target_id = str(row["target_contract_id"])
        try:
            async with session.begin_nested():
                await session.execute(
                    _MERGED_REPAIR_UPDATE,
                    {
                        "policy_id": policy_id,
                        "household_id": household_id,
                        "target_contract_id": target_id,
                    },
                )
        except DBAPIError:
            # Kein erneutes Werfen: der Konflikt ist eine Tatsache ueber die
            # Daten, kein Fehler des Laufs. Er wird gemeldet, damit der Nutzer
            # die Anteile selbst aufteilt - stillschweigend abzubrechen hiesse,
            # die Police behielte ihren toten Vertrag.
            logger.warning(
                "Grabstein-Reparatur abgelehnt (household_id=%s, policy_id=%s): "
                "Zielvertrag ist bereits voll verteilt.",
                household_id,
                policy_id,
            )
            findings.append(
                _finding(
                    "merged_contract_conflict",
                    household_id=household_id,
                    policy_id=policy_id,
                )
            )
            continue
        repaired += 1
        findings.append(
            _finding(
                "merged_contract_repaired", household_id=household_id, policy_id=policy_id
            )
        )

    orphans = (
        await session.execute(
            _ORPHANED_QUERY,
            {"household_id": household_id, "live_statuses": _LIVE_STATUSES},
        )
    ).mappings().all()
    for row in orphans:
        policy_id = str(row["policy_id"])
        logger.warning(
            "Police ohne Vertrag (household_id=%s, policy_id=%s): der Vertrag "
            "wurde geloescht, der Beitrag zaehlt nicht mehr in die Fixkosten.",
            household_id,
            policy_id,
        )
        findings.append(
            _finding("orphaned_contract", household_id=household_id, policy_id=policy_id)
        )
    return repaired, findings


async def _ask_instead_of_deadline(
    session: AsyncSession, household_id: str
) -> list[InsuranceFinding]:
    """Schritt 1a: Rueckfrage statt Frist bei angenommenem Anker.

    Der Kern von P2 und der einzige Schritt, dessen Wirkung im WEGLASSEN liegt:
    hier entsteht ausdruecklich KEINE Frist und KEIN Fristen-Push, sondern eine
    Rueckfrage. Ein Countdown auf ein Datum, das der Katalog geraten hat, saehe
    von einem bestaetigten nicht zu unterscheiden aus - und ein Push darauf
    schickte den Nutzer an einem erfundenen Termin zur Kuendigung.

    Den Push dazu (`insurance_main_due_question`) sendet
    `worker/notifications/insurance_alerts.py`; er haengt an derselben Spalte
    `main_due_source` und braucht deshalb kein Ergebnis dieses Schrittes.
    """
    rows = (
        await session.execute(
            _ASSUMED_TERMS_QUERY,
            {"household_id": household_id, "live_statuses": _LIVE_STATUSES},
        )
    ).mappings().all()

    findings: list[InsuranceFinding] = []
    for row in rows:
        policy_id = str(row["policy_id"])
        if row["main_due_source"] == "catalog_suggested":
            findings.append(
                _finding(
                    "unconfirmed_main_due",
                    household_id=household_id,
                    policy_id=policy_id,
                    due_on=row["main_due_on"],
                )
            )
        if row["notice_terms_source"] == "derived_from_days":
            findings.append(
                _finding(
                    "unconfirmed_notice_terms",
                    household_id=household_id,
                    policy_id=policy_id,
                )
            )
    return findings


async def _special_termination_windows(
    session: AsyncSession, household_id: str, *, today: date
) -> list[InsuranceFinding]:
    """Schritt 2a: Sonderkuendigungsfenster der Beitragsanpassung (P3).

    **Der Schritt schreibt nichts, auch kein 'expired'.** Plan 8.2 Punkt 2
    formuliert das Fortschreiben als Markierung; die Tabelle aus Plan 4.7 hat
    dafuer keine Statusspalte, und sie braucht auch keine: verstrichen ist
    `special_termination_until < today`, also ein Vergleich und kein Zustand.
    Eine Statusspalte daneben waere eine zweite Wahrheit ueber dasselbe Datum,
    und ihre naechtliche Fortschreibung genau die stille Korrektur, die
    Leitsatz 4 des Plans ausschliesst. Was der Plan mit der Markierung erreichen
    will - "der Nutzer soll sehen, dass ein Recht verfallen ist" - leistet der
    Befund, ohne den Datenbestand anzufassen.

    **Offen und verstrichen sind zwei Befundarten, nicht eine mit einem
    Merkmal.** Das eine verlangt eine Entscheidung und bekommt deshalb einen
    Push, das andere nur die Kenntnis, dass keine mehr moeglich ist - und
    bekommt ausdruecklich keinen. Eine Erinnerung an ein bereits verfallenes
    Recht waere reine Stoerung.

    Gerechnet wird hier nichts: `special_termination_until`,
    `recommended_action_on` und `safe_service_on` stehen fertig in der Zeile,
    weil der Fachdienst sie beim Erfassen der Mitteilung gerechnet und
    gespeichert hat (Plan 4.7: "gespeichert nur fuer den Push-Dedup"). Die
    Zuordnung `special_law` -> Fristlaenge (Paragraph 40 VVG ein Monat,
    Paragraph 205 Abs. 4 VVG zwei Monate bei PKV) bleibt damit an ihrer
    einzigen Stelle in `insurance_deadline_service`; der Lauf liest ein
    Ergebnis, er bildet sich keins.
    """
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    _SPECIAL_TERMINATION_QUERY,
                    {
                        "household_id": household_id,
                        "live_statuses": _LIVE_STATUSES,
                        "today": today,
                        "expired_lookback_days": _SPECIAL_EXPIRED_LOOKBACK_DAYS,
                        "default_lead_special": _DEFAULT_LEAD_DAYS_SPECIAL,
                    },
                )
            ).mappings().all()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _UNDEFINED_TABLE:
            raise
        # Sicherungsmarke aus demselben Grund wie in Schritt 1, nur gegen einen
        # anderen Fehler: ohne sie risse ein Worker-Rollout VOR der
        # P3-Migration den ganzen Haushaltslauf mit - samt der
        # Grabstein-Reparatur, dem einzigen Schritt, der etwas schreibt.
        logger.info(
            "Schritt 2a uebersprungen (household_id=%s): "
            "insurance_premium_notices existiert im Schema noch nicht.",
            household_id,
        )
        return []

    findings: list[InsuranceFinding] = []
    for row in rows:
        window_ends_on: date = row["window_ends_on"]
        if window_ends_on < today:
            findings.append(
                _finding(
                    "special_termination_expired",
                    household_id=household_id,
                    policy_id=str(row["policy_id"]),
                    due_on=window_ends_on,
                )
            )
            continue
        findings.append(
            _finding(
                "special_termination_open",
                household_id=household_id,
                policy_id=str(row["policy_id"]),
                # Das Handlungsdatum, nicht das Fristende: bis dahin muss die
                # Erklaerung raus, damit sie rechtzeitig zugeht. Fehlt es -
                # etwa bei einer Mitteilung ohne Versandweg -, bleibt das
                # rechtliche Fristende der ehrlichere Wert als gar keiner.
                due_on=row["recommended_action_on"] or window_ends_on,
            )
        )
    return findings


async def _claim_rows(
    session: AsyncSession,
    household_id: str,
    query: TextClause,
    params: dict[str, Any],
    *,
    step: str,
) -> list[RowMapping]:
    """Liest eine Vorgangsabfrage und schweigt, solange die Tabelle fehlt.

    Ein eigener Helfer statt der zweiten und dritten Abschrift der Sicherungs-
    marke aus Schritt 2a: die Schritte 2b und 5 lesen dieselbe Tabelle und haben
    denselben Rollout-Fall. Schritt 2a behaelt seine eigene, weil er an einer
    anderen Tabelle haengt - eine gemeinsame Fassung muesste raten, welche der
    beiden gerade fehlt.

    Die Sicherungsmarke ist nicht Hoeflichkeit: ohne sie risse ein Worker, der
    vor der P5-Migration startet, den gesamten Haushaltslauf mit - samt der
    Grabstein-Reparatur, dem einzigen Schritt, der etwas schreibt.
    """
    try:
        async with session.begin_nested():
            result = await session.execute(query, params)
            return list(result.mappings().all())
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _UNDEFINED_TABLE:
            raise
        logger.info(
            "Schritt %s uebersprungen (household_id=%s): insurance_claims "
            "existiert im Schema noch nicht.",
            step,
            household_id,
        )
        return []


async def _claim_special_termination_windows(
    session: AsyncSession, household_id: str, *, today: date
) -> list[InsuranceFinding]:
    """Schritt 2b: Sonderkuendigungsrecht nach einem Schadenfall (P5).

    **Der Schritt schreibt nichts - hier weniger als irgendwo sonst.** Plan 8.2
    Punkt 2b und Plan 3.12 Pruefung 3 sagen es woertlich: der Uebergang nach
    `expired` erfolgt ausschliesslich ueber einen belegten oder vom Nutzer
    bestaetigten Anker. Ein Nachtlauf, der `special_termination_state` selbst
    fortschriebe, erklaerte ein bestehendes Recht fuer verfallen - bei
    `accepted_partial` ist die Entscheidung des Versicherers der ANFANG der
    Verhandlungen, bei `accepted_full` enden sie erst mit der vollstaendigen
    Zahlung.

    **Ohne Anker keine Frist, sondern ein Befund.** Das ist der ganze
    Unterschied zu Schritt 2a: dort steht das Fensterende fertig in der Zeile,
    weil eine Beitragsanpassung ein datiertes Schreiben ist. Hier haengt es an
    "Abschluss der Verhandlungen ueber die Entschaedigung" - einem
    Rechtsbegriff, den weder das Werkzeug noch der Nutzer nebenbei bestimmt.
    Fehlt `negotiations_closed_on`, meldet der Lauf genau das und rechnet
    nichts.

    **Welcher Anker gilt, entscheidet die Sparte, nicht der Vorgang.**
    `termination_basis` kommt aus `insurance_lines.default_claim_termination_basis`
    (Plan 4.8): 'vvg_92' knuepft an den Abschluss der Verhandlungen, 'vvg_111'
    an Anerkennung, Ablehnung oder Rechtskraft. Deshalb zwei Befundarten und
    nicht eine - die Rueckfrage lautet verschieden, weil der Nutzer zwei
    verschiedene Dinge nachschlagen muss. 'contract' laeuft mit 'vvg_92': beide
    Normen sind dispositiv, aber die abweichende Bedingung kennt nur der
    Vertrag, und geraten wird hier nichts.

    **Kein Horizont wie in Schritt 2a.** Dort war er die Zusage, dass Protokoll
    und Push dasselbe Fenster meinen. Zu diesem Fenster gibt es keinen Push
    (Plan 8.3 kennt drei Typen; der dritte ist der Vorgang ohne Rueckmeldung) -
    ein Horizont haette hier nichts, worauf er sich einstellen koennte, und
    verschwiege ein offenes Recht wochenlang. Der Altersdeckel fuer das
    verstrichene Fenster bleibt derselbe wie in 2a.
    """
    rows = await _claim_rows(
        session,
        household_id,
        _CLAIM_SPECIAL_TERMINATION_QUERY,
        {
            "household_id": household_id,
            "live_statuses": _LIVE_STATUSES,
            "today": today,
            "expired_lookback_days": _SPECIAL_EXPIRED_LOOKBACK_DAYS,
        },
        step="2b",
    )

    findings: list[InsuranceFinding] = []
    for row in rows:
        claim_id = str(row["claim_id"])
        policy_id = str(row["policy_id"])
        basis = str(row["termination_basis"])
        window_ends_on: date | None = row["window_ends_on"]

        if row["termination_state"] == "open_until":
            if window_ends_on is None:
                # Zustand und Datum widersprechen sich: 'open_until' setzt einen
                # Anker voraus (CHECK in Plan 4.8), aus dem der Fachdienst das
                # Fensterende ableitet. Der Lauf rechnet den Monat NICHT selbst
                # nach - das waere ein zweiter Fristenrechner fuer denselben
                # Paragraphen, und er stuende dann gegen den der Detailseite.
                logger.warning(
                    "Vorgang mit Fenster ohne Enddatum (household_id=%s, claim_id=%s): "
                    "special_termination_state ist 'open_until', "
                    "special_termination_until fehlt.",
                    household_id,
                    claim_id,
                )
                continue
            kind = (
                "claim_special_termination_expired"
                if window_ends_on < today
                else "claim_special_termination_open"
            )
            findings.append(
                _finding(
                    kind,
                    household_id=household_id,
                    policy_id=policy_id,
                    due_on=window_ends_on,
                    claim_id=claim_id,
                )
            )
            continue

        # Ab hier: 'open_undetermined' - das Recht besteht seit dem Schadentag,
        # sein Ende steht nicht fest.
        if (
            basis in ("vvg_92", "contract")
            and row["decision"] == "accepted_full"
            and row["status"] == "paid"
            and row["last_payment_on"] is not None
        ):
            # Der einzige Vorschlag, den der Plan diesem Lauf zugesteht (8.2
            # Punkt 2b, letzter Satz). Er ersetzt die allgemeine Rueckfrage,
            # statt neben ihr zu stehen - zwei Befunde zu einem Vorgang waeren
            # zwei Aufgaben fuer dieselbe Handlung. Nur bei 'vvg_92': bei
            # Haftung zahlt der Versicherer an den Dritten, der Zahltag sagt
            # dort nichts ueber Anerkennung oder Ablehnung.
            findings.append(
                _finding(
                    "claim_negotiations_anchor_suggested",
                    household_id=household_id,
                    policy_id=policy_id,
                    due_on=row["last_payment_on"],
                    claim_id=claim_id,
                )
            )
            continue

        if basis == "vvg_111":
            findings.append(
                _finding(
                    "claim_decision_pending",
                    household_id=household_id,
                    policy_id=policy_id,
                    # `decided_on` ist hier der richtige Anker (Plan 4.8,
                    # Vorschlagstabelle) und darf deshalb mit - aber als Datum
                    # des Befundes, nicht als Frist: uebertragen wird es nur
                    # durch den Nutzer.
                    due_on=row["decided_on"],
                    claim_id=claim_id,
                )
            )
            continue

        findings.append(
            _finding(
                "claim_negotiations_pending",
                household_id=household_id,
                policy_id=policy_id,
                # Bewusst ohne `due_on`: es gibt kein Datum. `decided_on` hier
                # einzusetzen waere genau die Verwechslung, wegen der das Feld
                # negotiations_closed_on heisst und nicht decided_on.
                claim_id=claim_id,
            )
        )
    return findings


async def _claims_awaiting_response(
    session: AsyncSession, household_id: str, *, today: date
) -> list[InsuranceFinding]:
    """Schritt 5: Vorgaenge ohne Rueckmeldung (P5).

    Zwei Anlaesse, ein Schritt: seit der Meldung ist nichts passiert, oder ein
    vorgemerkter naechster Schritt ist ueberfaellig. Sie bekommen zwei
    Befundarten, weil die Handlung eine andere ist - beim einen fasst man beim
    Versicherer nach, beim anderen liegt der Ball beim Nutzer selbst.

    **Je Vorgang hoechstens ein Befund**, und der ueberfaellige Schritt gewinnt.
    Ein Vorgang, der beides erfuellt, ist derselbe liegengebliebene Vorgang;
    zweimal gemeldet waere er zwei Aufgaben fuer eine Handlung, und die
    konkretere von beiden ist die mit dem vorgemerkten Schritt.
    """
    rows = await _claim_rows(
        session,
        household_id,
        _CLAIM_FOLLOWUP_QUERY,
        {
            "household_id": household_id,
            "today": today,
            "silence_days": _CLAIM_SILENCE_DAYS,
        },
        step="5",
    )

    findings: list[InsuranceFinding] = []
    for row in rows:
        next_action_due: date | None = row["next_action_due"]
        overdue = next_action_due is not None and next_action_due < today
        findings.append(
            _finding(
                "claim_next_action_overdue" if overdue else "claim_awaiting_response",
                household_id=household_id,
                policy_id=str(row["policy_id"]) if row["policy_id"] is not None else None,
                due_on=next_action_due if overdue else row["reported_on"],
                claim_id=str(row["claim_id"]),
            )
        )
    return findings


async def _waiting_periods_ended(
    session: AsyncSession, household_id: str, *, today: date
) -> list[InsuranceFinding]:
    """Schritt 3: Wartezeitende.

    Bei Rechtsschutz ist das eine echte Information: bis zum Ablauf der
    Wartezeit traegt der Versicherer die Kosten eines Falles nicht, danach
    schon. Wer den Tag nicht kennt, meldet zu frueh oder wartet zu lange.
    """
    rows = (
        await session.execute(
            _WAITING_END_QUERY,
            {
                "household_id": household_id,
                "live_statuses": _LIVE_STATUSES,
                "today": today,
            },
        )
    ).mappings().all()
    return [
        _finding(
            "waiting_period_ended",
            household_id=household_id,
            policy_id=str(row["policy_id"]),
            due_on=row["protected_from"],
        )
        for row in rows
    ]


async def _motor_annual_review(
    session: AsyncSession, household_id: str, *, today: date
) -> list[InsuranceFinding]:
    """Schritt 6: Kfz-Wiedervorlage zum Jahreswechsel.

    Kein fester 30.11. und kein gespeicherter Termin - der Kfz-Stichtag entsteht
    aus `term_end` und der Hauptfaelligkeit und wird gerechnet (Plan 3.12,
    Pruefung 4). Dieser Schritt fragt deshalb genau das, was der Rechner nicht
    wissen kann: ob die Hauptfaelligkeit nach einem Fahrzeugwechsel noch stimmt.
    """
    if today.month != 1 or today.day > _MOTOR_REVIEW_LAST_DAY:
        return []
    rows = (
        await session.execute(
            _MOTOR_QUERY,
            {
                "household_id": household_id,
                "live_statuses": _LIVE_STATUSES,
                "motor_lines": _MOTOR_LINES,
            },
        )
    ).mappings().all()
    return [
        _finding(
            "motor_annual_review",
            household_id=household_id,
            policy_id=str(row["policy_id"]),
            due_on=row["main_due_on"],
        )
        for row in rows
    ]


async def _dormant_expiring(
    session: AsyncSession, household_id: str, *, today: date
) -> list[InsuranceFinding]:
    """Schritt 7: Ruhendstellung laeuft ab."""
    rows = (
        await session.execute(
            _DORMANT_QUERY,
            {
                "household_id": household_id,
                "today": today,
                "horizon_days": _DORMANT_HORIZON_DAYS,
            },
        )
    ).mappings().all()
    return [
        _finding(
            "dormant_ending",
            household_id=household_id,
            policy_id=str(row["policy_id"]),
            due_on=row["dormant_until"],
        )
        for row in rows
    ]


async def _status_divergences(
    session: AsyncSession, household_id: str
) -> list[InsuranceFinding]:
    """Schritt 9: Divergenz zwischen Police und Vertrag - als Befund.

    Ausdruecklich ohne Korrektur (Plan 4.16 Punkt 3). Beide Richtungen sind
    verschieden schlimm und bekommen deshalb verschiedene Texte: eine beendete
    Police auf einem laufenden Vertrag kostet weiter Geld, ein beendeter Vertrag
    unter einer laufenden Police taeuscht Schutz vor. Welches von beiden richtig
    ist, weiss nur der Nutzer.
    """
    rows = (
        await session.execute(_DIVERGENCE_QUERY, {"household_id": household_id})
    ).mappings().all()

    findings: list[InsuranceFinding] = []
    for row in rows:
        policy_id = str(row["policy_id"])
        kind = (
            "status_divergence_policy_ended"
            if row["policy_status"] in ("cancelled", "ended", "paid_up")
            else "status_divergence_contract_ended"
        )
        logger.warning(
            "Statusdivergenz (household_id=%s, policy_id=%s): Police '%s', Vertrag '%s'.",
            household_id,
            policy_id,
            row["policy_status"],
            row["contract_status"],
        )
        findings.append(_finding(kind, household_id=household_id, policy_id=policy_id))
    return findings


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------


async def scan_insurance(
    session: AsyncSession, household_id: UUID | str, *, today: date
) -> InsuranceScanResult:
    """Fuehrt die neun Schritte fuer einen Haushalt aus.

    Committet nicht - die Transaktionsklammer liegt beim Aufrufer, damit der
    naechtliche Lauf je Haushalt einzeln committen kann (dasselbe Muster wie
    `worker/contracts/reconcile.py`).

    Die Reihenfolge ist Absicht: die Grabstein-Reparatur laeuft zuerst, damit
    Schritt 9 die frisch umgehaengten Policen bereits gegen ihren richtigen
    Vertrag prueft und nicht gegen den Grabstein.
    """
    key = str(household_id)
    repaired, findings = await _repair_gravestones(session, key)
    findings.extend(await _ask_instead_of_deadline(session, key))
    findings.extend(await _special_termination_windows(session, key, today=today))
    findings.extend(await _claim_special_termination_windows(session, key, today=today))
    findings.extend(await _waiting_periods_ended(session, key, today=today))
    findings.extend(await _claims_awaiting_response(session, key, today=today))
    findings.extend(await _motor_annual_review(session, key, today=today))
    findings.extend(await _dormant_expiring(session, key, today=today))
    findings.extend(await _status_divergences(session, key))

    result = InsuranceScanResult(household_id=key, repaired_count=repaired, findings=findings)
    if findings or repaired:
        logger.info(
            "Versicherungslauf (household_id=%s): %s Police(n) umgehaengt, "
            "%s Befund(e) %s.",
            key,
            repaired,
            len(findings),
            result.counts_by_kind(),
        )
    return result


async def list_scannable_household_ids(session: AsyncSession) -> list[UUID]:
    """Haushalte mit mindestens einer Police, die nicht als Fehleingabe gilt."""
    rows = (await session.execute(_HOUSEHOLDS_QUERY)).mappings().all()
    return [UUID(str(row["household_id"])) for row in rows]


async def run_insurance_scan(household_id: str | UUID, *, today: date | None = None) -> None:
    """Lauf fuer einen einzelnen Haushalt, mit eigener Session und Commit."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await scan_insurance(session, UUID(str(household_id)), today=today or date.today())
        await session.commit()


async def run_insurance_scan_all() -> None:
    """Naechtlicher Lauf ueber alle Haushalte mit Policen.

    Je Haushalt eine eigene Session und ein eigener Commit - dieselben drei
    Gruende wie in `worker/contracts/orchestrator.py`: ein Haushalt mit
    unerwarteten Daten darf weder die Ergebnisse der anderen zurueckrollen noch
    den Rest des Laufs abbrechen, und eine einzige Transaktion hielte ihre
    Sperren ueber den gesamten Lauf.

    Der Stichtag wird einmal bestimmt, damit ein ueber Mitternacht laufender
    Durchgang nicht in der Mitte den Tag wechselt - sonst meldete Schritt 3 eine
    Wartezeit an zwei aufeinanderfolgenden Tagen unterschiedlich.
    """
    today = date.today()
    session_factory = get_session_factory()

    async with session_factory() as session:
        household_ids = await list_scannable_household_ids(session)

    logger.info("Naechtlicher Versicherungslauf startet fuer %s Haushalt(e).", len(household_ids))

    repaired_total = 0
    findings_total = 0
    failed = 0
    for household_id in household_ids:
        async with session_factory() as session:
            try:
                result = await scan_insurance(session, household_id, today=today)
                await session.commit()
                repaired_total += result.repaired_count
                findings_total += len(result.findings)
            except Exception:
                # Wie beim Vertragsabgleich bewusst kein erneutes Werfen: ein
                # Retry des Actors durchliefe alle bereits fertigen Haushalte
                # erneut, und der naechste Durchgang holt den einen Haushalt in
                # 24 Stunden ohnehin nach.
                await session.rollback()
                failed += 1
                logger.exception(
                    "Versicherungslauf fuer Haushalt %s fehlgeschlagen.", household_id
                )

    logger.info(
        "Naechtlicher Versicherungslauf beendet: %s Haushalt(e), %s Police(n) umgehaengt, "
        "%s Befund(e).",
        len(household_ids),
        repaired_total,
        findings_total,
    )
    if failed:
        logger.warning(
            "Naechtlicher Versicherungslauf: %s von %s Haushalt(en) fehlgeschlagen.",
            failed,
            len(household_ids),
        )
