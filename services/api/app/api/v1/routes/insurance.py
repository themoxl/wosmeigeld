"""Policenregister (docs/VERSICHERUNGEN-PLAN.md, Phase P1).

Nebenlaeufigkeit wie im Vertragsmodul, aber mit zusammengesetztem ETag
`W/"<Vertragsversion>.<Policenversion>"` (Plan 4.11): ein reiner Policen-PATCH
aendert `contracts` nicht, ein ETag allein aus der Vertragsversion waere fuer
alle Versicherungsfelder wirkungslos - und umgekehrt bliebe eine parallele
Betragsaenderung am Vertrag unbemerkt. `PATCH` verlangt `If-Match` (428 ohne
Header, 409 bei veraltetem Stand), die Einfeldaktionen akzeptieren ihn optional.

Mit P6 kommt der Bedarfsabgleich dazu - und mit ihm die einzige Stelle des
Moduls ohne jede Nebenlaeufigkeitsform. Das ist Absicht: seine Befunde werden
bei jedem Aufruf frisch gerechnet und nirgends gespeichert. Ein ETag ueber ein
Ergebnis, das keine Zeile hat, waere die Behauptung eines Zustands, den es
nicht gibt. Geschrieben werden nur Angaben und Entscheidungen, und beide sind
ein Satz Werte je Haushalt - dort kostet ein verlorenes Rennen eine Antwort,
keinen Datensatz.

Mit P3 kommt eine zweite Nebenlaeufigkeitsform dazu: die Beitragsanpassung
traegt ein EINFACHES ETag aus ihrer eigenen Version. Das zusammengesetzte ETag
der Police haette hier keinen Sinn - eine parallele Betragsaenderung am Vertrag
aendert nichts an dem, was in einem Anschreiben steht.

Mit P4 kommt eine dritte dazu, und zwar bewusst als NICHT-Fall: Beteiligte,
Bausteine und Objektzuordnungen tragen kein ETag. Sie sind Mengen an einer
Police, keine Felder von ihr; ein Rundlauf ueber die Policenversion wuerde zwei
Nutzer, die verschiedene Personen erfassen, ohne Not gegeneinander sperren. Ein
ETag traegt genau ein P4-Objekt: `insured_objects`, weil ein Fahrzeug seine
eigenen Felder hat und der PATCH sie einzeln aendert.

Mit P5 kommt die vierte, und sie ist wieder einfach: ein Schadenvorgang traegt
sein eigenes ETag. Eine parallele Betragsaenderung am Beitrag aendert nichts an
dem, was in der Schadenakte steht.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.reimbursements import router as reimbursements_router
from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match
from app.schemas.insurance import (
    BackfillPreview,
    BackfillResult,
    ClaimCloseNegotiations,
    ClaimPayment,
    ClaimPaymentCreate,
    ClaimPaymentDirection,
    ClaimStatusChange,
    ClaimTransactionCandidate,
    ConfirmLine,
    ConfirmMainDue,
    ConfirmNoticeTerms,
    ContractCandidate,
    DoubleInsuranceFinding,
    InsuranceClaim,
    InsuranceClaimCreate,
    InsuranceClaimDetail,
    InsuranceClaimUpdate,
    InsuranceCoverageKey,
    InsuranceLine,
    InsurancePolicy,
    InsurancePolicyCreate,
    InsurancePolicyUpdate,
    InsuranceSettings,
    InsuranceSummary,
    InsuranceTask,
    InsuredObject,
    InsuredObjectCreate,
    InsuredObjectKind,
    InsuredObjectUpdate,
    LinkContract,
    NeedsCheck,
    NeedsCompleteness,
    NeedsProfile,
    NeedsProfileInput,
    ObjectRegistration,
    ObjectRegistrationCreate,
    PayerPolicyLink,
    PayerPolicySuggestion,
    PersonNeedsProfile,
    PersonNeedsProfileInput,
    PolicyBundle,
    PolicyBundleResult,
    PolicyCoverage,
    PolicyCoverageReplacement,
    PolicyObject,
    PolicyObjectCreate,
    PolicyPerson,
    PolicyPersonCreate,
    PolicyPurpose,
    PolicyReplacement,
    PolicyReplaceRequest,
    PremiumHistory,
    PremiumNotice,
    PremiumNoticeConfirm,
    PremiumNoticeConfirmResult,
    PremiumNoticeCreate,
    PremiumNoticeUpdate,
    RuleDecisionRequest,
)
from app.services import (
    insurance_claim_service,
    insurance_coverage_service,
    insurance_needs_service,
    insurance_premium_service,
    insurance_service,
)

router = APIRouter(prefix="/insurance", tags=["insurance"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]

PremiumSource = Literal["contract", "external", "none"]
PolicySort = Literal["deadline", "yearly", "line", "name"]


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


@router.get("/lines", response_model=list[InsuranceLine])
async def list_lines(
    ctx: HouseholdSession,
    risk_field: str | None = None,
) -> list[InsuranceLine]:
    """Spartenkatalog. Ohne Editorrecht lesbar - er ist Migrationsgut, kein
    Haushaltsdatum."""
    session, _household_id = ctx
    return await insurance_service.list_lines(session, risk_field=risk_field)


@router.get("/lines/{line_key}", response_model=insurance_service.InsuranceLineDetail)
async def get_line(
    line_key: str, ctx: HouseholdSession
) -> insurance_service.InsuranceLineDetail:
    """Eine Sparte samt `attr_schema` und `completeness_fields` - beides braucht
    die Formular- und Balkenerzeugung."""
    session, _household_id = ctx
    return await insurance_service.get_line(session, line_key)


@router.get("/coverage-keys", response_model=list[InsuranceCoverageKey])
async def list_coverage_keys(
    ctx: HouseholdSession,
    line_key: str | None = None,
) -> list[InsuranceCoverageKey]:
    session, _household_id = ctx
    return await insurance_service.list_coverage_keys(session, line_key=line_key)


# ---------------------------------------------------------------------------
# Uebersicht
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=InsuranceSummary)
async def get_summary(ctx: HouseholdSession) -> InsuranceSummary:
    """Kennzahlenleiste. Fixkosten und Fremdbeitraege bleiben getrennt - ihre
    Summe waere die erste Zahl, die jemand als Fixkostenwert missversteht."""
    session, household_id = ctx
    return await insurance_service.build_summary(session, household_id, today=date.today())


@router.get("/tasks", response_model=list[InsuranceTask])
async def list_tasks(ctx: HouseholdSession) -> list[InsuranceTask]:
    """Die Liste hinter "Jetzt wichtig" - ein eigener Endpunkt, weil sie sonst
    aus mehreren Abfragen im Client zusammengesetzt und dort sortiert wuerde."""
    session, household_id = ctx
    return await insurance_service.build_tasks(session, household_id, today=date.today())


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=InsuranceSettings)
async def get_settings(ctx: HouseholdSession) -> InsuranceSettings:
    """Vorlaufstaffeln und Schalter des naechtlichen Laufs.

    Ohne Editorrecht lesbar: wer die Liste sehen darf, soll auch erklaert
    bekommen, warum eine Erinnerung kommt - aendern darf er sie deshalb nicht.
    """
    session, household_id = ctx
    return await insurance_service.get_settings(session, household_id)


@router.put("/settings", response_model=InsuranceSettings)
async def put_settings(
    ctx: HouseholdEditorSession, payload: InsuranceSettings
) -> InsuranceSettings:
    """Schreibt die Einstellungen vollstaendig.

    PUT und nicht PATCH: die Staffel ist eine Liste, und ein Teilstueck einer
    Liste ist nicht eindeutig zusammenzufuehren.
    """
    session, household_id = ctx
    return await insurance_service.save_settings(session, household_id, payload)


# ---------------------------------------------------------------------------
# Bestandsuebernahme - feste Pfade vor /policies/{policy_id}
# ---------------------------------------------------------------------------


@router.get("/backfill/preview", response_model=BackfillPreview)
async def preview_backfill(ctx: HouseholdSession) -> BackfillPreview:
    """Was die Uebernahme anlegen wuerde - vor dem Anlegen.

    Vorschau statt Automatik: der Lauf beruehrt den Bestand, und der Nutzer soll
    vorher sehen, was entsteht.
    """
    session, household_id = ctx
    return await insurance_service.backfill_preview(session, household_id)


@router.post("/backfill", response_model=BackfillResult)
async def run_backfill(ctx: HouseholdEditorSession) -> BackfillResult:
    """Fuehrt die Uebernahme aus - wiederholbar, ohne Bestehendes anzufassen."""
    session, household_id = ctx
    return await insurance_service.run_backfill(session, household_id)


# ---------------------------------------------------------------------------
# Policen
# ---------------------------------------------------------------------------


@router.get("/policies", response_model=list[InsurancePolicy])
async def list_policies(
    ctx: HouseholdSession,
    policy_status: Annotated[str | None, Query(alias="status")] = None,
    line_key: str | None = None,
    risk_field: str | None = None,
    q: Annotated[str | None, Query(max_length=160)] = None,
    premium_source: PremiumSource | None = None,
    include_ended: bool = False,
    sort: PolicySort = "line",
) -> list[InsurancePolicy]:
    session, household_id = ctx
    return await insurance_service.list_policies(
        session,
        household_id,
        today=date.today(),
        policy_status=policy_status,
        line_key=line_key,
        risk_field=risk_field,
        search=q,
        premium_source=premium_source,
        include_ended=include_ended,
        sort=sort,
    )


@router.post("/policies", response_model=InsurancePolicy, status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: InsurancePolicyCreate,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsurancePolicy:
    """Pflicht sind `line_key`, `insurer_name` und genau einer der drei
    Geldfaelle: ein Vertrag (der Haushalt zahlt), ein Fremdbetrag samt Zahler,
    oder keins. Zwei davon zugleich lehnt bereits das Eingabemodell ab.

    Eine Police ohne Hauptfaelligkeit ist speicherbar - der Anker steht dann auf
    'none' und die Frist ist nicht berechenbar. Das ist der haeufige Fall und
    kein Fehler.
    """
    session, household_id = ctx
    result = await insurance_service.create_policy(
        session, household_id, payload, today=date.today()
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.get("/policies/{policy_id}", response_model=insurance_service.InsurancePolicyDetail)
async def get_policy(
    policy_id: UUID, ctx: HouseholdSession, response: Response
) -> insurance_service.InsurancePolicyDetail:
    session, household_id = ctx
    detail, etag = await insurance_service.get_policy_detail(
        session, household_id, policy_id, today=date.today()
    )
    response.headers["ETag"] = etag
    return detail


@router.patch("/policies/{policy_id}", response_model=InsurancePolicy)
async def update_policy(
    policy_id: UUID,
    payload: InsurancePolicyUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> InsurancePolicy:
    """Teilaenderung mit `If-Match`.

    Die Herkunft eines Datums laesst sich hier nicht hochstufen: die sechs
    Herkunftsspalten stehen in keinem Spaltentupel, und die erste
    Hauptfaelligkeit entsteht ausschliesslich ueber `/confirm-main-due`.
    """
    session, household_id = ctx
    expected = insurance_service.parse_policy_if_match(if_match)
    result = await insurance_service.update_policy(
        session, household_id, policy_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: UUID, ctx: HouseholdEditorSession) -> Response:
    """Loescht nur die Police. Der Vertrag bleibt, Beitrag und Fixkosten auch."""
    session, household_id = ctx
    await insurance_service.delete_policy(session, household_id, policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/policies/{policy_id}/status", response_model=insurance_service.PolicyStatusOutcome
)
async def change_status(
    policy_id: UUID,
    payload: insurance_service.PolicyStatusChangeRequest,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> insurance_service.PolicyStatusOutcome:
    """Einfeldaktion aus dem Zeilenmenue; `If-Match` ist optional.

    Beim Wechsel nach `cancelled`, `ended`, `paid_up` oder `dormant` antwortet
    sie an einer Police MIT Vertrag zunaechst mit einer Vorschau und schreibt
    nichts: sonst bliebe der Vertrag aktiv und sein Beitrag zaehlte weiter in
    die Fixkostenquote. Mit `contract_status` schreibt sie beides in einer
    Transaktion.
    """
    session, household_id = ctx
    expected = insurance_service.parse_policy_if_match(if_match, required=False)
    outcome, etag = await insurance_service.change_status(
        session, household_id, policy_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = etag
    return outcome


@router.get("/policies/{policy_id}/purpose", response_model=PolicyPurpose)
async def get_purpose(policy_id: UUID, ctx: HouseholdSession) -> PolicyPurpose:
    """Der Endpunkt hinter "wofuer ist die da".

    Der Vorbehalt "Ihre konkrete Police kann abweichen" steht im Vertrag und
    nicht nur in der Oberflaeche: der Satz gehoert zum Text.
    """
    session, household_id = ctx
    return await insurance_service.get_purpose(session, household_id, policy_id)


@router.post("/policies/{policy_id}/confirm-line", response_model=InsurancePolicy)
async def confirm_line(
    policy_id: UUID,
    payload: ConfirmLine,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsurancePolicy:
    """Bestaetigt die Sparte, oder stellt sie mit `defer` ausdruecklich zurueck."""
    session, household_id = ctx
    result = await insurance_service.confirm_line(
        session, household_id, policy_id, payload, today=date.today()
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.post("/policies/{policy_id}/confirm-main-due", response_model=InsurancePolicy)
async def confirm_main_due(
    policy_id: UUID,
    ctx: HouseholdEditorSession,
    response: Response,
    payload: ConfirmMainDue | None = None,
) -> InsurancePolicy:
    """Einfeldaktion, kein Formular - und der einzige Schreibweg zu
    `main_due_source = 'user_confirmed'`.

    Ohne Body bestaetigt sie das vorhandene oder das im Katalog vorgeschlagene
    Datum unveraendert. Die Antwort enthaelt die neu gerechnete Frist, damit die
    Liste ohne zweiten Rundlauf von "angenommen" auf "bestaetigt" umschaltet.
    """
    session, household_id = ctx
    result = await insurance_service.confirm_main_due(
        session, household_id, policy_id, payload, today=date.today()
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.post("/policies/{policy_id}/confirm-notice-terms", response_model=InsurancePolicy)
async def confirm_notice_terms(
    policy_id: UUID,
    payload: ConfirmNoticeTerms,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsurancePolicy:
    """Dasselbe fuer die Fristlaenge.

    Ohne diesen Schritt bleibt eine uebernommene Police dauerhaft auf
    `certainty = 'assumed'`, auch wenn die Hauptfaelligkeit bestaetigt ist.
    """
    session, household_id = ctx
    result = await insurance_service.confirm_notice_terms(
        session, household_id, policy_id, payload, today=date.today()
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.post("/policies/{policy_id}/link-contract", response_model=InsurancePolicy)
async def link_contract(
    policy_id: UUID,
    payload: LinkContract,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsurancePolicy:
    """Fall (b)/(c) -> Fall (a). Ein etwaiger Fremdbetrag faellt dabei weg."""
    session, household_id = ctx
    result = await insurance_service.link_contract(
        session, household_id, policy_id, payload, today=date.today()
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.delete("/policies/{policy_id}/link-contract", response_model=InsurancePolicy)
async def unlink_contract(
    policy_id: UUID,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsurancePolicy:
    """Police faellt in Fall (c), "kein Beitrag hinterlegt". Der Vertrag bleibt
    unberuehrt - er verliert weder Betrag noch Faelligkeit."""
    session, household_id = ctx
    result = await insurance_service.unlink_contract(
        session, household_id, policy_id, today=date.today()
    )
    response.headers["ETag"] = result.etag
    return result.policy


@router.get("/policies/{policy_id}/contract-candidates", response_model=list[ContractCandidate])
async def contract_candidates(
    policy_id: UUID, ctx: HouseholdSession
) -> list[ContractCandidate]:
    """Vertraege ohne Police, gerankt. Vorschlag mit Beleg, keine Automatik."""
    session, household_id = ctx
    return await insurance_service.contract_candidates(session, household_id, policy_id)


@router.post("/policies/{policy_id}/replace-with", response_model=PolicyReplacement)
async def replace_with(
    policy_id: UUID,
    payload: PolicyReplaceRequest,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> PolicyReplacement:
    """Gesellschaftswechsel mit Historienmitnahme.

    Die alte Police endet mit Begruendung `switch`, die neue kennt ihre
    Vorgaengerin - und der Beitragsverlauf laeuft ueber beide, statt beim
    Wechsel abzubrechen. Der Vertrag der alten Police bleibt unberuehrt: ihn
    still zu beenden aenderte die Fixkosten ohne Rueckfrage. Die Antwort
    benennt ihn stattdessen in `hint`.
    """
    session, household_id = ctx
    expected = insurance_service.parse_policy_if_match(if_match, required=False)
    result, etag = await insurance_premium_service.replace_with(
        session, household_id, policy_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = etag
    return result


# ---------------------------------------------------------------------------
# Beitragsanpassungen (P3)
# ---------------------------------------------------------------------------


@router.get("/policies/{policy_id}/premium-notices", response_model=list[PremiumNotice])
async def list_premium_notices(
    policy_id: UUID, ctx: HouseholdSession
) -> list[PremiumNotice]:
    """Der Verlauf der Anpassungsschreiben, juengster Zugang zuerst.

    Jede Zeile traegt ihr frisch gerechnetes Sonderkuendigungsfenster - auch
    das leere, samt Begruendung. "Kein Recht" und "nicht gerechnet" saehen
    sonst gleich aus.
    """
    session, household_id = ctx
    return await insurance_premium_service.list_notices(
        session, household_id, policy_id, today=date.today()
    )


@router.post(
    "/policies/{policy_id}/premium-notices",
    response_model=PremiumNotice,
    status_code=status.HTTP_201_CREATED,
)
async def create_premium_notice(
    policy_id: UUID,
    payload: PremiumNoticeCreate,
    ctx: HouseholdEditorSession,
    response: Response,
) -> PremiumNotice:
    """Erfasst ein Anpassungsschreiben.

    Fristanker ist `received_on`, der ZUGANG - nicht `effective_on`. Fehlt er,
    antwortet der Dienst mit `insurance_notice_missing_received_on`: ohne
    Zugangsdatum entsteht keine Frist, sondern eine Rueckfrage.
    """
    session, household_id = ctx
    notice = await insurance_premium_service.create_notice(
        session, household_id, policy_id, payload, today=date.today()
    )
    response.headers["ETag"] = insurance_premium_service.notice_etag(notice.version)
    return notice


@router.get("/policies/{policy_id}/premium-history", response_model=PremiumHistory)
async def get_premium_history(
    policy_id: UUID,
    ctx: HouseholdSession,
    years: Annotated[int, Query(ge=1, le=30)] = 5,
) -> PremiumHistory:
    """Beitragsverlauf als Treppe, ueber die ganze Policenkette.

    Gespeist aus `contract_amount_history` - dort lebt der Beitrag - und den
    uebernommenen Mitteilungen, die sagen, warum er sich geaendert hat. Eine
    erfasste, aber nicht uebernommene Anpassung steht bewusst NICHT als Stufe
    darin, sondern nur in `pending_notice_ids`.
    """
    session, household_id = ctx
    return await insurance_premium_service.premium_history(
        session, household_id, policy_id, today=date.today(), years=years
    )


@router.patch("/premium-notices/{notice_id}", response_model=PremiumNotice)
async def update_premium_notice(
    notice_id: UUID,
    payload: PremiumNoticeUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> PremiumNotice:
    """Korrigiert eine erfasste Mitteilung; `If-Match` ist Pflicht.

    Die Fristdaten werden dabei neu gerechnet: ein korrigiertes Zugangsdatum
    mit altem Fenster waere ein Push auf ein Datum, das niemand mehr behauptet.
    Nach der Uebernahme ist die Zeile gesperrt (409) - ihr Betrag steht als
    Preisstufe im Vertragsverlauf.
    """
    session, household_id = ctx
    expected = parse_if_match(if_match)
    notice = await insurance_premium_service.update_notice(
        session, household_id, notice_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = insurance_premium_service.notice_etag(notice.version)
    return notice


@router.delete("/premium-notices/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_premium_notice(notice_id: UUID, ctx: HouseholdEditorSession) -> Response:
    """Nimmt eine erfasste Mitteilung zurueck - solange sie nicht uebernommen ist."""
    session, household_id = ctx
    await insurance_premium_service.delete_notice(session, household_id, notice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/premium-notices/{notice_id}/confirm", response_model=PremiumNoticeConfirmResult)
async def confirm_premium_notice(
    notice_id: UUID,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    payload: PremiumNoticeConfirm | None = None,
) -> PremiumNoticeConfirmResult:
    """Uebernimmt den angekuendigten Betrag.

    Mit Vertrag geht er in die Preistreppe (`change_reason = 'manual'`,
    `valid_from = effective_on`) - der einzige sanktionierte Weg, und
    ausdruecklich nicht in eine zweite Betragsquelle. Ohne Vertrag ist die
    Uebernahme nur zulaessig, wenn ein Dritter zahlt; dann wandert der
    Informationsbetrag samt Rhythmus an die Police.
    """
    session, household_id = ctx
    result = await insurance_premium_service.confirm_notice(
        session, household_id, notice_id, payload, today=date.today(), user_id=user.id
    )
    response.headers["ETag"] = insurance_premium_service.notice_etag(result.notice.version)
    return result


@router.post("/premium-notices/{notice_id}/ack", response_model=PremiumNotice)
async def acknowledge_premium_notice(
    notice_id: UUID, ctx: HouseholdEditorSession, response: Response
) -> PremiumNotice:
    """Frist zur Kenntnis genommen - der Push schweigt fuer dieses Ereignis.

    Die Vorlaufstaffel ist eine Eskalation fuer den Fall des Nichthandelns,
    keine Terminserie: eine Bestaetigung beendet auch die noch ausstehenden
    Stufen.
    """
    session, household_id = ctx
    notice = await insurance_premium_service.acknowledge_notice(
        session, household_id, notice_id, today=date.today()
    )
    response.headers["ETag"] = insurance_premium_service.notice_etag(notice.version)
    return notice


# ---------------------------------------------------------------------------
# Beteiligte an einer Police (P4)
# ---------------------------------------------------------------------------


@router.get("/policies/{policy_id}/persons", response_model=list[PolicyPerson])
async def list_policy_persons(policy_id: UUID, ctx: HouseholdSession) -> list[PolicyPerson]:
    """Wer ist an dieser Police beteiligt - und in welcher Rolle.

    Der Name kommt aus `insured_person_label()`, nicht aus einem Join auf die
    Personentabelle: exponiert wird ein Name, nicht die Zeile. Die
    Gesundheitsdaten derselben Person bleiben unberuehrt geschuetzt.
    """
    session, household_id = ctx
    return await insurance_coverage_service.list_persons(session, household_id, policy_id)


@router.post(
    "/policies/{policy_id}/persons",
    response_model=PolicyPerson,
    status_code=status.HTTP_201_CREATED,
)
async def add_policy_person(
    policy_id: UUID,
    payload: PolicyPersonCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> PolicyPerson:
    """Ordnet eine bestehende Person zu oder legt sie im selben Aufruf an.

    Die Anlage laeuft ueber den Gesundheitszweig
    (`reimbursement_service.create_person`), damit es keine zweite
    Personenliste und keinen zweiten Schreibweg zu denselben Menschen gibt.
    """
    session, household_id = ctx
    return await insurance_coverage_service.add_person(
        session, household_id, policy_id, payload, user_id=user.id
    )


@router.delete(
    "/policies/{policy_id}/persons/{link_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_policy_person(
    policy_id: UUID, link_id: UUID, ctx: HouseholdEditorSession
) -> Response:
    """Loest die Zuordnung, nicht die Person.

    Geloescht wird die Verknuepfungszeile - die Person bleibt im Haushalt, mit
    ihren Vorgaengen und ihren Freigaben.
    """
    session, household_id = ctx
    await insurance_coverage_service.remove_person(session, household_id, policy_id, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Deckungsbausteine (P4)
# ---------------------------------------------------------------------------


@router.get("/policies/{policy_id}/coverages", response_model=list[PolicyCoverage])
async def list_policy_coverages(policy_id: UUID, ctx: HouseholdSession) -> list[PolicyCoverage]:
    """Die tatsaechlich erfassten Bausteine dieser Police.

    Sobald hier Zeilen stehen, beschreibt die Police ihren Deckungsumfang
    selbst - `GET /policies/{id}/purpose` wechselt dann von "Typischerweise
    enthalten" auf "In dieser Police enthalten".
    """
    session, household_id = ctx
    return await insurance_coverage_service.list_coverages(session, household_id, policy_id)


@router.put("/policies/{policy_id}/coverages", response_model=list[PolicyCoverage])
async def put_policy_coverages(
    policy_id: UUID,
    payload: PolicyCoverageReplacement,
    ctx: HouseholdEditorSession,
) -> list[PolicyCoverage]:
    """PUT ersetzt die Menge - ein Rundlauf statt n.

    Der Bausteineditor einer Hausratpolice haelt ueber zwanzig Zeilen; sie
    einzeln zu senden hiesse, jeden Zwischenstand zu speichern und bei jedem
    Abbruch eine halbe Wahrheit zu hinterlassen.
    """
    session, household_id = ctx
    return await insurance_coverage_service.replace_coverages(
        session, household_id, policy_id, payload
    )


# ---------------------------------------------------------------------------
# Objekte an einer Police (P4)
# ---------------------------------------------------------------------------


@router.get("/policies/{policy_id}/objects", response_model=list[PolicyObject])
async def list_policy_objects(policy_id: UUID, ctx: HouseholdSession) -> list[PolicyObject]:
    session, household_id = ctx
    return await insurance_coverage_service.list_policy_objects(
        session, household_id, policy_id
    )


@router.post(
    "/policies/{policy_id}/objects",
    response_model=PolicyObject,
    status_code=status.HTTP_201_CREATED,
)
async def attach_policy_object(
    policy_id: UUID, payload: PolicyObjectCreate, ctx: HouseholdEditorSession
) -> PolicyObject:
    """Verknuepft ein bestehendes Objekt oder legt es im selben Aufruf an."""
    session, household_id = ctx
    return await insurance_coverage_service.attach_object(
        session, household_id, policy_id, payload
    )


@router.delete(
    "/policies/{policy_id}/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def detach_policy_object(
    policy_id: UUID, object_id: UUID, ctx: HouseholdEditorSession
) -> Response:
    """Loest die Zuordnung. Das Objekt bleibt samt Halterhistorie bestehen -
    es kann an einer zweiten Police haengen."""
    session, household_id = ctx
    await insurance_coverage_service.detach_object(session, household_id, policy_id, object_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/policies/{policy_id}/bundle", response_model=PolicyBundleResult)
async def set_policy_bundle(
    policy_id: UUID,
    payload: PolicyBundle,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> PolicyBundleResult:
    """Haengt eine Police als Baustein unter eine Hauptpolice, oder loest das
    Buendel wieder auf.

    Ohne diesen Weg waere die Buendelspalte aus dem Schema ueber die API nicht
    erreichbar und der Fall "Kfz-Haftpflicht und Vollkasko unter einer
    Versicherungsscheinnummer" bliebe unerfassbar. Genau EINE Ebene: der Versuch,
    ein Kind unter ein Kind zu haengen, antwortet 422
    `insurance_policy_nesting_too_deep`.
    """
    session, household_id = ctx
    expected = insurance_service.parse_policy_if_match(if_match, required=False)
    result, etag = await insurance_coverage_service.set_bundle_parent(
        session, household_id, policy_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = etag
    return result


# ---------------------------------------------------------------------------
# Objekte (P4)
# ---------------------------------------------------------------------------


@router.get("/objects", response_model=list[InsuredObject])
async def list_objects(
    ctx: HouseholdSession,
    kind: InsuredObjectKind | None = None,
    include_disposed: bool = False,
) -> list[InsuredObject]:
    """Die versicherten Sachen des Haushalts.

    Abgegebene Objekte bleiben erhalten - sonst verloeren alte Buchungen und
    Schaeden ihren Bezug -, erscheinen aber nur mit `include_disposed`.
    """
    session, household_id = ctx
    return await insurance_coverage_service.list_objects(
        session, household_id, kind=kind, include_disposed=include_disposed
    )


@router.post("/objects", response_model=InsuredObject, status_code=status.HTTP_201_CREATED)
async def create_object(
    payload: InsuredObjectCreate, ctx: HouseholdEditorSession, response: Response
) -> InsuredObject:
    """Legt eine versicherte Sache an.

    `acquired_on` ist beim Fahrzeug kein Beiwerk: es ist der Fristanker des
    Paragraphen 96 Abs. 2 S. 2 VVG. Ohne das Erwerbsdatum gibt es fuer den
    Erwerber keine Frist, sondern eine Rueckfrage.
    """
    session, household_id = ctx
    objekt = await insurance_coverage_service.create_object(session, household_id, payload)
    response.headers["ETag"] = entity_etag(objekt.version)
    return objekt


@router.get("/objects/{object_id}", response_model=InsuredObject)
async def get_object(
    object_id: UUID, ctx: HouseholdSession, response: Response
) -> InsuredObject:
    session, household_id = ctx
    objekt = await insurance_coverage_service.get_object(session, household_id, object_id)
    response.headers["ETag"] = entity_etag(objekt.version)
    return objekt


@router.patch("/objects/{object_id}", response_model=InsuredObject)
async def update_object(
    object_id: UUID,
    payload: InsuredObjectUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> InsuredObject:
    """Teilaenderung mit `If-Match`.

    Kennzeichen und Halter stehen ausdruecklich NICHT im Feldsatz: sie sind
    Historie und werden ueber `/objects/{id}/registrations` fortgeschrieben.
    Waeren sie hier aenderbar, verloere ein Kennzeichenwechsel die Zuordnung
    alter Buchungen und Schaeden.
    """
    session, household_id = ctx
    expected = parse_if_match(if_match)
    objekt = await insurance_coverage_service.update_object(
        session, household_id, object_id, payload, expected=expected
    )
    response.headers["ETag"] = entity_etag(objekt.version)
    return objekt


@router.delete("/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(object_id: UUID, ctx: HouseholdEditorSession) -> Response:
    """Loescht ein Objekt, das an keiner Police mehr haengt.

    Haengt es noch, antwortet der Dienst 409 `insurance_object_in_use`. Der
    fachliche Ausweg ist `disposed_on`, nicht das Loeschen.
    """
    session, household_id = ctx
    await insurance_coverage_service.delete_object(session, household_id, object_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/objects/{object_id}/registrations", response_model=list[ObjectRegistration])
async def list_object_registrations(
    object_id: UUID, ctx: HouseholdSession
) -> list[ObjectRegistration]:
    """Halter- und Kennzeichenhistorie, juengster Eintrag zuerst."""
    session, household_id = ctx
    return await insurance_coverage_service.list_registrations(
        session, household_id, object_id
    )


@router.post(
    "/objects/{object_id}/registrations",
    response_model=ObjectRegistration,
    status_code=status.HTTP_201_CREATED,
)
async def add_object_registration(
    object_id: UUID, payload: ObjectRegistrationCreate, ctx: HouseholdEditorSession
) -> ObjectRegistration:
    """Ein Kennzeichen- oder Halterwechsel ist eine neue Zeile.

    Der Aufruf schliesst die laufende Zeile zum Vortag und oeffnet eine neue.
    Ein Ueberschreiben wuerde die Zuordnung alter Buchungen und Schaeden
    verlieren - deshalb gibt es fuer Kennzeichen und Halter kein PATCH.
    """
    session, household_id = ctx
    return await insurance_coverage_service.add_registration(
        session, household_id, object_id, payload
    )


# ---------------------------------------------------------------------------
# Schaden- und Leistungsfaelle (P5)
# ---------------------------------------------------------------------------
# Die Nebenlaeufigkeit ist hier die vierte Form im Modul und wieder eine
# einfache: ein Vorgang traegt ein ETag aus seiner EIGENEN Version. Das
# zusammengesetzte ETag der Police haette keinen Sinn - eine parallele
# Betragsaenderung am Beitrag aendert nichts an dem, was in der Schadenakte
# steht. Zahlungen dagegen tragen kein eigenes ETag: sie sind eine Menge am
# Vorgang, keine Felder von ihm. Ihre Aenderung bumpt allerdings die Version des
# Vorgangs, weil sie seine Summe verschiebt.


@router.get("/claims", response_model=list[InsuranceClaim])
async def list_claims(
    ctx: HouseholdSession,
    policy_id: UUID | None = None,
    claim_status: Annotated[str | None, Query(alias="status")] = None,
    include_closed: bool = True,
) -> list[InsuranceClaim]:
    """Alle Vorgaenge des Haushalts, juengster Schadentag zuerst.

    Haushaltsweit und nicht nur je Police, weil ein Vorgang auch ohne Police
    existiert: der Auffahrunfall, bei dem die Gegenseite reguliert.
    """
    session, household_id = ctx
    return await insurance_claim_service.list_claims(
        session,
        household_id,
        today=date.today(),
        policy_id=policy_id,
        claim_status=claim_status,
        include_closed=include_closed,
    )


@router.post("/claims", response_model=InsuranceClaim, status_code=status.HTTP_201_CREATED)
async def create_claim(
    payload: InsuranceClaimCreate,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsuranceClaim:
    """Legt einen Vorgang an - mit oder ohne eigene Police.

    Auf einer Police mit `is_health_scoped = true` antwortet der Dienst 422
    `insurance_claim_health_scoped` samt Verweis auf `/v1/reimbursements/cases`.
    Krankheitskosten haben dort ihr Vorgangsmodell mit Traegersplit; ein zweites
    hier waere eine zweite Wahrheit ueber dieselbe Rechnung.
    """
    session, household_id = ctx
    claim = await insurance_claim_service.create_claim(
        session, household_id, payload, today=date.today()
    )
    response.headers["ETag"] = insurance_claim_service.claim_etag(claim.version)
    return claim


@router.get("/policies/{policy_id}/claims", response_model=list[InsuranceClaim])
async def list_policy_claims(policy_id: UUID, ctx: HouseholdSession) -> list[InsuranceClaim]:
    """Die Vorgaenge einer Police - der Weg aus dem Policendetail."""
    session, household_id = ctx
    return await insurance_claim_service.list_claims(
        session, household_id, today=date.today(), policy_id=policy_id
    )


@router.post(
    "/policies/{policy_id}/claims",
    response_model=InsuranceClaim,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy_claim(
    policy_id: UUID,
    payload: InsuranceClaimCreate,
    ctx: HouseholdEditorSession,
    response: Response,
) -> InsuranceClaim:
    """Derselbe Vorgang, nur mit der Police aus dem Pfad.

    Die Police im Pfad gewinnt gegen ein Feld im Koerper - der Nutzer steht auf
    der Seite dieser Police, und ein abweichendes Feld waere ein Tippfehler und
    keine Absicht.
    """
    session, household_id = ctx
    claim = await insurance_claim_service.create_claim(
        session, household_id, payload, today=date.today(), policy_id=policy_id
    )
    response.headers["ETag"] = insurance_claim_service.claim_etag(claim.version)
    return claim


@router.get("/claims/{claim_id}", response_model=InsuranceClaimDetail)
async def get_claim(
    claim_id: UUID, ctx: HouseholdSession, response: Response
) -> InsuranceClaimDetail:
    """Der Vorgang mit Zahlungen und Zeitstrahl.

    `special_termination.suggestion` traegt den Vorschlag fuer den Fristanker -
    aber nur, wo es einen gibt: bei einer Teilregulierung entsteht bewusst
    keiner, weil dort die Entscheidung der Anfang der Verhandlung ist.
    """
    session, household_id = ctx
    claim = await insurance_claim_service.get_claim(
        session, household_id, claim_id, today=date.today()
    )
    response.headers["ETag"] = insurance_claim_service.claim_etag(claim.version)
    return claim


@router.patch("/claims/{claim_id}", response_model=InsuranceClaim)
async def update_claim(
    claim_id: UUID,
    payload: InsuranceClaimUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> InsuranceClaim:
    """Teilaenderung mit `If-Match`.

    Der Fristanker steht bewusst in keinem Feldsatz: `negotiations_closed_on`
    entsteht ausschliesslich ueber `/claims/{id}/close-negotiations`, samt Grund
    und Sicherheit. Ein Datum, das per PATCH hereinkaeme, waere eine Behauptung
    ohne Herkunft - dieselbe Haltung wie bei `main_due_source` an der Police.
    """
    session, household_id = ctx
    expected = parse_if_match(if_match)
    claim = await insurance_claim_service.update_claim(
        session, household_id, claim_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = insurance_claim_service.claim_etag(claim.version)
    return claim


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_claim(claim_id: UUID, ctx: HouseholdEditorSession) -> Response:
    """Loescht den Vorgang samt Zahlungen und Zeitstrahl.

    Die zugeordneten Buchungen bleiben unberuehrt - geloescht wird die
    Zuordnung, nicht das Geld.
    """
    session, household_id = ctx
    await insurance_claim_service.delete_claim(session, household_id, claim_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/claims/{claim_id}/status", response_model=InsuranceClaim)
async def change_claim_status(
    claim_id: UUID,
    payload: ClaimStatusChange,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> InsuranceClaim:
    """Statuswechsel als Einfeldaktion; `If-Match` ist optional.

    Sie setzt KEIN Fristdatum. Beim Uebergang nach `reported` entsteht der
    Zustand `open_undetermined` - das Recht besteht ab Eintritt des
    Versicherungsfalles (Paragraph 92 Abs. 1 VVG), sein Ende steht noch nicht
    fest. Die alte Regel "`special_termination_until = decided_on + 1 Monat`"
    ist ersatzlos entfallen; sie haette bei einer Teilregulierung ein
    bestehendes Recht als verfallen gemeldet.
    """
    session, household_id = ctx
    expected = parse_if_match(if_match, required=False)
    claim = await insurance_claim_service.change_status(
        session, household_id, claim_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = insurance_claim_service.claim_etag(claim.version)
    return claim


@router.post("/claims/{claim_id}/close-negotiations", response_model=InsuranceClaim)
async def close_claim_negotiations(
    claim_id: UUID,
    payload: ClaimCloseNegotiations,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> InsuranceClaim:
    """Setzt den Fristanker - die einzige Stelle, an der er entsteht.

    Erst danach wechselt das Fenster von `open_undetermined` auf `open_until`.
    Die Sicherheit leitet der Dienst ab und uebernimmt sie nicht: mit
    zugeordnetem Kontoumsatz `belegt`, aus dem Entscheidungsdatum `abgeleitet`,
    sonst `geschaetzt`. Ein Client kann sie nicht behaupten.
    """
    session, household_id = ctx
    expected = parse_if_match(if_match, required=False)
    claim = await insurance_claim_service.close_negotiations(
        session, household_id, claim_id, payload, today=date.today(), expected=expected
    )
    response.headers["ETag"] = insurance_claim_service.claim_etag(claim.version)
    return claim


@router.get(
    "/claims/{claim_id}/transaction-candidates",
    response_model=list[ClaimTransactionCandidate],
)
async def claim_transaction_candidates(
    claim_id: UUID,
    ctx: HouseholdSession,
    direction: ClaimPaymentDirection = "inbound",
) -> list[ClaimTransactionCandidate]:
    """Buchungen, die zu diesem Vorgang passen koennten - gerankt, nie gesetzt.

    `direction=outbound` sucht die Gegenrichtung: die selbst bezahlte Rechnung
    und die Selbstbeteiligung. `remaining_minor` sagt, was von einer Buchung
    noch frei ist - eine Sammelueberweisung deckt mehrere Vorgaenge.
    """
    session, household_id = ctx
    return await insurance_claim_service.transaction_candidates(
        session, household_id, claim_id, today=date.today(), direction=direction
    )


@router.get("/claims/{claim_id}/payments", response_model=list[ClaimPayment])
async def list_claim_payments(claim_id: UUID, ctx: HouseholdSession) -> list[ClaimPayment]:
    session, household_id = ctx
    return await insurance_claim_service.list_payments(session, household_id, claim_id)


@router.post(
    "/claims/{claim_id}/payments",
    response_model=ClaimPayment,
    status_code=status.HTTP_201_CREATED,
)
async def add_claim_payment(
    claim_id: UUID, payload: ClaimPaymentCreate, ctx: HouseholdEditorSession
) -> ClaimPayment:
    """Ordnet eine Zahlung zu.

    Ohne `transaction_id` ist es die Direktzahlung an die Werkstatt: keine
    Buchung, aber sehr wohl eine Zahlung. Sie setzt `paid_to_third_party` und
    zaehlt in `settled_minor` mit - sonst waere ein vollstaendig regulierter
    Schaden in der Uebersicht ein offener.
    """
    session, household_id = ctx
    return await insurance_claim_service.create_payment(
        session, household_id, claim_id, payload
    )


@router.delete(
    "/claims/{claim_id}/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_claim_payment(
    claim_id: UUID, payment_id: UUID, ctx: HouseholdEditorSession
) -> Response:
    """Nimmt eine Zuordnung zurueck. Die Buchung bleibt, die Summe wird neu gebildet."""
    session, household_id = ctx
    await insurance_claim_service.delete_payment(
        session, household_id, claim_id, payment_id, today=date.today()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Bedarfsabgleich (P6, Plan 4.9 und 10)
# ---------------------------------------------------------------------------
# Kein Endpunkt dieses Blocks gibt eine Empfehlung, einen Anbieter, einen Tarif
# oder eine Note aus. Sie nennen einzelne Befunde mit der Regel, die sie erzeugt
# hat, den Angaben, auf denen sie beruhen, und der Quelle mit Pruefdatum - oder
# sie schweigen.
#
# Die Pfade folgen Plan 5.5 (`/needs/profile`, `/needs/check`,
# `/needs/completeness`, `/needs/{rule_key}/dismiss`, `/duplicates`) und nicht
# den kuerzeren Arbeitsnamen aus dem Auftrag: der Plan ist verbindlich, und der
# Web-Client ruft bereits genau diese Pfade auf. Zwei Namen fuer denselben
# Endpunkt waeren die zweite Wahrheit, die dieses Modul ueberall vermeidet.

RuleKeyPath = Annotated[str, Path(pattern=r"^[A-Z][0-9]{1,3}[a-z]?$")]


@router.get("/needs/profile", response_model=NeedsProfile)
async def get_needs_profile(ctx: HouseholdSession) -> NeedsProfile:
    """Die Angaben, auf denen der Abgleich beruht.

    Eine fehlende Zeile ist kein Fehler, sondern der Anfangszustand: `exists`
    ist dann false, und der Abgleich meldet ueberall "Angabe fehlt". `persons`
    enthaelt nur die Personen, die der Aufrufer sehen darf - eine leere Liste
    heisst deshalb nicht "keine Personen erfasst".
    """
    session, household_id = ctx
    return await insurance_needs_service.get_profile(session, household_id, today=date.today())


@router.put("/needs/profile", response_model=NeedsProfile)
async def put_needs_profile(
    ctx: HouseholdEditorSession, payload: NeedsProfileInput
) -> NeedsProfile:
    """Schreibt genau die Felder, die im Koerper stehen.

    Ein gesendetes `null` loescht die Antwort ("nicht beantwortet"), ein
    weggelassenes Feld bleibt unveraendert. Ohne diese Unterscheidung loeschte
    jeder Client, der eine Frage nicht anbietet, still die Antwort darauf - und
    aus der geloeschten Antwort wuerde beim naechsten Lauf eine Rueckfrage.
    """
    session, household_id = ctx
    return await insurance_needs_service.save_profile(
        session, household_id, payload, today=date.today()
    )


@router.get("/needs/profile/persons/{person_id}", response_model=PersonNeedsProfile)
async def get_person_needs_profile(
    person_id: UUID, ctx: HouseholdSession
) -> PersonNeedsProfile:
    """Die Bedarfsangaben einer Person - der sensibelste Datensatz des Moduls.

    Der Zugriff haengt an `health_can_access_person`, also am Gesundheitszweig
    und nicht an einem zweiten Rechtesystem. Wer die Person nicht sehen darf,
    bekommt 404 und nicht 403: die Existenz einer fremden Person wird nicht
    verraten.
    """
    session, household_id = ctx
    return await insurance_needs_service.get_person_profile(session, household_id, person_id)


@router.put("/needs/profile/persons/{person_id}", response_model=PersonNeedsProfile)
async def put_person_needs_profile(
    person_id: UUID, payload: PersonNeedsProfileInput, ctx: HouseholdEditorSession
) -> PersonNeedsProfile:
    """Dieselben Angaben schreibend, mit `health_can_manage_person` als Schranke.

    `died_on` geht in denselben Aufruf, landet aber an der Person selbst: es ist
    eine Spalte des Gesundheitszweigs und keine zweite Personenliste.
    """
    session, household_id = ctx
    return await insurance_needs_service.save_person_profile(
        session, household_id, person_id, payload
    )


@router.get("/needs/check", response_model=NeedsCheck)
async def check_needs(ctx: HouseholdSession) -> NeedsCheck:
    """Der Abgleich: Befunde mit Begruendung, Herkunft und Quelle.

    Bei jedem Aufruf frisch gerechnet, nichts davon gespeichert. `unclear` ist
    ein vollwertiges Ergebnis und wird nie als Luecke gezaehlt; `disclaimer`
    steht im Koerper und nicht nur in der Fusszeile.
    """
    session, household_id = ctx
    return await insurance_needs_service.run_check(session, household_id, today=date.today())


@router.get("/needs/completeness", response_model=NeedsCompleteness)
async def needs_completeness(ctx: HouseholdSession) -> NeedsCompleteness:
    """Welche fehlende Angabe blockiert welche Regel.

    Die Fuehrung durch den Fragebogen: sie entsteht aus demselben Durchlauf wie
    die Befunde, damit im Formular nicht steht, eine Angabe sei noetig, die der
    Abgleich gar nicht vermisst.
    """
    session, household_id = ctx
    return await insurance_needs_service.get_completeness(
        session, household_id, today=date.today()
    )


@router.post("/needs/{rule_key}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def decide_rule(
    rule_key: RuleKeyPath,
    payload: RuleDecisionRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> Response:
    """Was der Nutzer zu einem Befund sagt - und was ihn schlaegt.

    Ein Endpunkt fuer alle drei Antworten, weil alle drei dieselbe Zeile
    schreiben: `dismissed` legt den Befund weg, `accepted` nimmt ihn an,
    `snoozed` stellt ihn bis `remind_on` zurueck. `policy_id` bleibt im
    Normalfall leer - die meisten Regeln zeigen auf eine FEHLENDE Police.
    """
    session, household_id = ctx
    await insurance_needs_service.save_decision(
        session, household_id, rule_key, payload, user_id=user.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/needs/{rule_key}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def clear_rule_decision(
    rule_key: RuleKeyPath,
    ctx: HouseholdEditorSession,
    policy_id: UUID | None = None,
) -> Response:
    """Nimmt die Entscheidung zurueck; der Befund steht danach wieder da.

    Idempotent und ohne 404: war nichts zu loeschen, ist der Zustand danach
    genau der gewuenschte.
    """
    session, household_id = ctx
    await insurance_needs_service.clear_decision(session, household_id, rule_key, policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/duplicates", response_model=list[DoubleInsuranceFinding])
async def list_duplicates(ctx: HouseholdSession) -> list[DoubleInsuranceFinding]:
    """Zwei Policen fuer dieselbe Sache (Plan 10.4).

    Erkannt ueber `insurance_lines.covered_by_keys` - "Glasbruch steckt schon in
    Hausrat und Gebaeude" ist eine Katalogaussage, keine Sonderlogik. Der Befund
    nennt beide Policen und die Rechtsfolge, nie einen Kuendigungsrat. Solange
    die Bausteine der deckenden Police fehlen, traegt er `certainty = 'unclear'`
    und sagt das.
    """
    session, household_id = ctx
    return await insurance_needs_service.find_double_insurance(
        session, household_id, today=date.today()
    )


# ---------------------------------------------------------------------------
# Erstattungsbruecke (P5, Plan 9.1)
# ---------------------------------------------------------------------------
# Der Endpunkt gehoert fachlich zu diesem Modul - er schlaegt eine POLICE vor -,
# liegt aber unter `/v1/reimbursements/payers/{id}`, weil er einen Kostentraeger
# aendert und die Oberflaeche ihn dort aufruft. Er wird deshalb am Router des
# Erstattungsmoduls angemeldet statt an dem hier: ein zweiter Router mit
# eigenem Praefix muesste in `api/v1/router.py` eingebunden werden, und diese
# Datei ist die einzige, die der Auftrag anfassen darf. Die Registrierung
# geschieht beim Import dieses Moduls und damit vor jedem
# `include_router`-Aufruf - alle Routenmodule sind zu diesem Zeitpunkt bereits
# importiert.


@reimbursements_router.post(
    "/payers/{payer_id}/suggest-policy", response_model=PayerPolicySuggestion, tags=["insurance"]
)
async def suggest_policy_for_payer(
    payer_id: UUID,
    ctx: HouseholdEditorSession,
    payload: PayerPolicyLink | None = None,
) -> PayerPolicySuggestion:
    """Welche Police gehoert zu diesem Kostentraeger?

    Ohne Koerper schreibt der Aufruf nichts: er matcht den Namen des Zahlers
    gegen `insurance_policies.insurer_name` in `payee_key()`-Normalform und
    bietet die Verknuepfung an. Mit `link_policy_id` wird sie gesetzt, mit
    `unlink` geloest. Kein Zwang und keine Datenmigration - das Erstattungsmodul
    funktioniert unveraendert weiter, auch wenn nie eine Police entsteht. Genau
    darauf kommt es an, weil dort echte Nutzerdaten liegen.

    Editorrecht auch fuer den reinen Vorschlag: die Antwort nennt Policen samt
    Sparte, und wer den Zahler nicht bearbeiten darf, braucht diese Liste nicht.
    """
    session, household_id = ctx
    return await insurance_claim_service.suggest_policy_for_payer(
        session, household_id, payer_id, payload
    )
