"""DB-Orchestrierung fuer den dynamischen Foerderkatalog (Plan 6/7.5):
Foerderprofil, Katalogstatus, Matchlauf, Kombinationspruefung und Uebernahme
eines Treffers als Finanzierungsquelle. Reine Regelauswertung lebt in
`funding_matching_service.py`; dieser Dienst laedt/schreibt Zeilen und bindet
Programmversionen, Projektkosten (fuer die foerderfaehige Kostenbasis je
`funding_tags`) und Finanzierungsquellen zusammen."""

import hashlib
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, Final, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import funding_packages
from app.schemas.debt_plans import (
    AmortizationMode,
    DebtPlanCreate,
    RatePeriodInput,
    SpecialRepaymentInput,
)
from app.schemas.funding import (
    AdoptMatchRequest,
    AdoptMatchResponse,
    CatalogStatus,
    CombinationEvaluateResponse,
    CombinationResultSchema,
    CriterionResultSchema,
    EligibilityProfile,
    EligibilityProfileInput,
    FundingCheckRunResponse,
    FundingMatch,
    FundingProgramDetail,
    FundingProgramSummary,
    FundingProgramVersionDetail,
    ProviderCatalogStatus,
)
from app.schemas.projects import FinancingSourceCreate
from app.services import debt_plan_service, project_service
from app.services.audit_service import record_audit_event
from app.services.funding_matching_service import (
    EligibilityFacts,
    ProgramMatchInput,
    ProgramMatchResult,
    evaluate_combinations,
    match_program,
)

FUNDING_CHECK_RUN_ENTITY_TYPE: Final = "project_funding_check_run"
ENGINE_VERSION: Final = "v2"
EFFECTIVE_FUNDING_CONTENT_VERSION: Final = "bundled-overlays-2026-08-09-2"
_BAYERNLABO_SEMANTIC_KEY: Final = "bayernlabo_eigenwohnraumfoerderung"

_PROGRAM_VERSION_COLUMNS = (
    "pv.id, pv.program_id, pv.source_version, pv.valid_from, pv.valid_to, pv.published_at, "
    "pv.retrieved_at, pv.review_due_at, pv.currency, pv.benefit_type, pv.applicant_types, "
    "pv.project_types, pv.criteria_json, pv.benefit_json, pv.loan_terms_json, "
    "pv.combination_rules_json, pv.manual_checks_json, pv.source_url, pv.source_content_hash, "
    "pv.license_name, pv.license_url, pv.attribution, pv.normalization_status"
)


def _effective_loan_terms(semantic_key: str, loan_terms: dict[str, Any] | None) -> dict[str, Any]:
    """Hebt alte installierte KfW-308-Stände auf das Variantenmodell an.

    Vor August 2026 wurde das Paket ohne Zinsen und nur mit einer maximalen
    Laufzeit ausgeliefert. Solche Haushaltsversionen überlagern den aktuellen
    Paketstand weiterhin, bis das Paket aktualisiert wird. Für neue Auswahl-
    und Pflegevorgänge liefern wir deshalb sofort die mit dem Code geprüften
    Konditionsvarianten. Die gespeicherte alte Version bleibt unverändert;
    bestehende Finanzierungsquellen rechnen weiter mit ihrem Snapshot.
    """
    current = dict(loan_terms or {})
    if semantic_key != "kfw_308_jung_kauft_alt" or isinstance(current.get("variants"), list):
        return current
    raw_package = funding_packages.get_package("kfw_jung_kauft_alt")
    if raw_package is None:
        return current
    for program in raw_package.get("programs", []):
        if program.get("semantic_key") == semantic_key:
            bundled = program.get("loan_terms_json")
            if isinstance(bundled, dict) and isinstance(bundled.get("variants"), list):
                return dict(bundled)
    return current


def _bundled_program(semantic_key: str) -> dict[str, Any] | None:
    for package_id in ("bayernlabo_eigenwohnraum", "kfw_jung_kauft_alt"):
        package = funding_packages.get_package(package_id)
        if package is None:
            continue
        for program in package.get("programs", []):
            if program.get("semantic_key") == semantic_key:
                return dict(program)
    return None


def _effective_program_content(
    semantic_key: str,
    source_version: str,
    criteria_json: dict[str, Any] | None,
    benefit_json: dict[str, Any] | None,
    loan_terms_json: dict[str, Any] | None,
    *,
    allow_bundled_compatibility: bool,
) -> dict[str, Any]:
    """Liefert den wirksamen Inhalt, ohne gespeicherte Versionen umzuschreiben.

    Die BayernLabo-Ausnahme ist bewusst eng: nur der bekannte globale Seed mit
    seinem Null-Zuschuss und leeren Darlehenskonditionen wird durch den
    mitgelieferten, geprueften Stand ersetzt. Eigene Haushaltsversionen werden
    nie pauschal aktualisiert.
    """
    current_benefit = dict(benefit_json or {})
    current_terms = _effective_loan_terms(semantic_key, loan_terms_json)
    result = {
        "source_version": source_version,
        "criteria_json": dict(criteria_json or {}),
        "benefit_json": current_benefit,
        "loan_terms_json": current_terms,
        "benefit_type": None,
        "source_url": None,
    }
    is_legacy_bayern = (
        allow_bundled_compatibility
        and semantic_key == _BAYERNLABO_SEMANTIC_KEY
        and source_version == "curated-2026-08-04"
        and current_benefit == {"type": "fixed_grant", "amount_minor": 0}
        and not loan_terms_json
    )
    if not is_legacy_bayern:
        return result

    bundled = _bundled_program(semantic_key)
    if bundled is None:
        return result
    result.update(
        source_version=str(bundled["source_version"]),
        criteria_json=dict(bundled["criteria_json"]),
        benefit_json=dict(bundled["benefit_json"]),
        loan_terms_json=dict(bundled["loan_terms_json"]),
        benefit_type=bundled["benefit_type"],
        source_url=bundled["source_url"],
    )
    return result


# ---------------------------------------------------------------------------
# Foerderprofil
# ---------------------------------------------------------------------------


async def get_eligibility_profile(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> EligibilityProfile | None:
    await project_service.require_project(session, household_id, project_id)
    row = (
        await session.execute(
            text(
                "select project_id, facts, schema_version, version, updated_at "
                "from project_eligibility_profiles "
                "where project_id = :project_id and household_id = :household_id"
            ),
            {"project_id": str(project_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    facts = dict(row.facts)
    manual = facts.pop("manual_confirmations", [])
    return EligibilityProfile(
        project_id=row.project_id,
        facts=facts,
        manual_confirmations=list(manual),
        schema_version=row.schema_version,
        version=row.version,
        updated_at=row.updated_at,
    )


async def save_eligibility_profile(
    session: AsyncSession, household_id: UUID, project_id: UUID, payload: EligibilityProfileInput
) -> EligibilityProfile:
    await project_service.require_project(session, household_id, project_id)
    stored_facts = {**payload.facts, "manual_confirmations": payload.manual_confirmations}
    await session.execute(
        text(
            "insert into project_eligibility_profiles (project_id, household_id, facts) "
            "values (:project_id, :household_id, cast(:facts as jsonb)) "
            "on conflict (project_id) do update set "
            "facts = excluded.facts, updated_at = now()"
        ),
        {
            "project_id": str(project_id),
            "household_id": str(household_id),
            "facts": json.dumps(stored_facts, default=str),
        },
    )
    profile = await get_eligibility_profile(session, household_id, project_id)
    assert profile is not None
    return profile


def _facts_from_profile(profile: EligibilityProfile | None) -> EligibilityFacts:
    if profile is None:
        return EligibilityFacts()
    known = {
        "application_date",
        "applicant_type",
        "household_income_year_minor",
        "children_count",
        "self_occupied",
        "existing_homeowner",
        "disability_severity_percent",
    }
    named = {k: v for k, v in profile.facts.items() if k in known}
    extra = {k: v for k, v in profile.facts.items() if k not in known}
    return EligibilityFacts(
        **named, manual_confirmations=frozenset(profile.manual_confirmations), extra=extra
    )


# ---------------------------------------------------------------------------
# Katalog (global, nur lesbar)
# ---------------------------------------------------------------------------


async def list_funding_programs(
    session: AsyncSession,
    household_id: UUID,
    country_code: str,
    region_code: str | None,
    project_type: str | None,
    *,
    include_inactive: bool = False,
) -> list[FundingProgramSummary]:
    rows = await session.execute(
        text(
            "select p.id, p.household_id, p.semantic_key, p.provider_key, p.provider_name, "
            "p.program_code, p.title, p.country_code, p.scope, p.region_codes, p.official_url, "
            "p.is_active, cv.id as current_version_id, cv.benefit_type, cv.normalization_status, "
            "cv.review_due_at, cv.retrieved_at, cv.source_version, cv.source_url, "
            "cv.project_types, cv.criteria_json, cv.benefit_json, cv.loan_terms_json "
            "from funding_programs p "
            "left join lateral ("
            # `v.id` bricht Gleichstaende beim Abrufdatum: ein Import schreibt
            # mehrere Versionen in derselben Sekunde, und ohne zweites Kriterium
            # entscheidet der Zufall, welche Kondition gilt.
            "  select * from funding_program_versions v where v.program_id = p.id and "
            "  ((p.household_id is null and v.household_id is null) or "
            "   v.household_id = p.household_id) "
            "  order by v.retrieved_at desc, v.id desc limit 1"
            ") cv on true "
            "where p.country_code = :country_code "
            "and (p.household_id is null or p.household_id = :household_id) "
            "and (p.is_active or :include_inactive)"
        ),
        {
            "country_code": country_code,
            "household_id": str(household_id),
            "include_inactive": include_inactive,
        },
    )
    today = datetime.now(UTC)
    results = []
    for row in rows:
        effective = _effective_program_content(
            row.semantic_key,
            row.source_version or "",
            row.criteria_json,
            row.benefit_json,
            row.loan_terms_json,
            allow_bundled_compatibility=row.household_id is None,
        )
        # Nach Region wird nur gefiltert, wenn eine verlangt wurde. Vorher fiel
        # ohne `region_code` JEDES regionale Programm heraus - auf der
        # Katalogseite, die nie eine Region mitschickt, waren Landesprogramme
        # wie die bayerische Eigenwohnraumfoerderung damit unsichtbar, obwohl
        # sie im Katalog stehen. Ein Register soll zeigen, was da ist; das
        # Eingrenzen auf das passende Bundesland ist Sache des Foerderchecks.
        if region_code is not None and row.scope in ("regional", "municipal") and row.region_codes:
            if region_code not in row.region_codes:
                continue
        if project_type and row.project_types and project_type not in row.project_types:
            continue
        results.append(
            FundingProgramSummary(
                id=row.id,
                semantic_key=row.semantic_key,
                provider_key=row.provider_key,
                provider_name=row.provider_name,
                program_code=row.program_code,
                title=row.title,
                country_code=row.country_code,
                scope=row.scope,
                region_codes=list(row.region_codes),
                official_url=row.official_url,
                current_version_id=row.current_version_id,
                benefit_type=effective["benefit_type"] or row.benefit_type,
                normalization_status=row.normalization_status,
                review_due_at=row.review_due_at,
                is_stale=row.review_due_at is not None and row.review_due_at < today,
                is_own=row.household_id is not None,
                is_active=row.is_active,
                source_version=effective["source_version"],
                retrieved_at=row.retrieved_at,
                source_url=effective["source_url"] or row.source_url,
            )
        )
    # Ein eigenes Programm mit demselben fachlichen Schluessel ist die bewusst
    # installierte Aktualisierung und verdeckt deshalb den kuratierten Seed.
    by_key: dict[str, FundingProgramSummary] = {}
    for entry in results:
        current = by_key.get(entry.semantic_key)
        if current is None or (not current.is_own and entry.is_own):
            by_key[entry.semantic_key] = entry

    visible = list(by_key.values())
    visible.sort(key=lambda entry: (not entry.is_own, entry.provider_name, entry.title))
    return visible


async def get_funding_program(
    session: AsyncSession, household_id: UUID, program_id: UUID
) -> FundingProgramDetail:
    program_row = (
        await session.execute(
            text(
                "select id, household_id, semantic_key, provider_key, provider_name, "
                "program_code, title, country_code, scope, region_codes, official_url, is_active "
                "from funding_programs "
                "where id = :id and (household_id is null or household_id = :household_id)"
            ),
            {"id": str(program_id), "household_id": str(household_id)},
        )
    ).first()
    if program_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_program_not_found"
        )

    version_owner_clause = (
        "and household_id is null"
        if program_row.household_id is None
        else "and household_id = :program_household_id"
    )
    version_row = (
        await session.execute(
            text(
                "select id, source_version, valid_from, valid_to, published_at, retrieved_at, "
                "review_due_at, currency, benefit_type, applicant_types, project_types, "
                "criteria_json, benefit_json, loan_terms_json, combination_rules_json, "
                "manual_checks_json, source_url, license_name, license_url, attribution, "
                "normalization_status "
                "from funding_program_versions where program_id = :program_id "
                f"{version_owner_clause} "
                # `id` als zweites Sortierkriterium: zwei Versionen koennen
                # dieselbe Sekunde tragen (ein Import schreibt mehrere auf
                # einmal). Ohne den Gleichstandsbrecher entscheidet der Zufall,
                # welche Kondition gilt - und das Ergebnis kann sich zwischen
                # zwei Abfragen aendern, ohne dass jemand etwas geaendert hat.
                "order by retrieved_at desc, id desc limit 1"
            ),
            {
                "program_id": str(program_id),
                "program_household_id": (
                    str(program_row.household_id) if program_row.household_id is not None else None
                ),
            },
        )
    ).first()

    current_version = None
    if version_row is not None:
        effective = _effective_program_content(
            program_row.semantic_key,
            version_row.source_version,
            version_row.criteria_json,
            version_row.benefit_json,
            version_row.loan_terms_json,
            allow_bundled_compatibility=program_row.household_id is None,
        )
        current_version = FundingProgramVersionDetail(
            id=version_row.id,
            source_version=effective["source_version"],
            valid_from=version_row.valid_from,
            valid_to=version_row.valid_to,
            published_at=version_row.published_at,
            retrieved_at=version_row.retrieved_at,
            review_due_at=version_row.review_due_at,
            currency=version_row.currency,
            benefit_type=effective["benefit_type"] or version_row.benefit_type,
            applicant_types=list(version_row.applicant_types),
            project_types=list(version_row.project_types),
            criteria_json=effective["criteria_json"],
            benefit_json=effective["benefit_json"],
            loan_terms_json=effective["loan_terms_json"],
            combination_rules_json=version_row.combination_rules_json,
            manual_checks_json=list(version_row.manual_checks_json),
            source_url=effective["source_url"] or version_row.source_url,
            license_name=version_row.license_name,
            license_url=version_row.license_url,
            attribution=version_row.attribution,
            normalization_status=version_row.normalization_status,
        )

    today = datetime.now(UTC)
    return FundingProgramDetail(
        id=program_row.id,
        semantic_key=program_row.semantic_key,
        provider_key=program_row.provider_key,
        provider_name=program_row.provider_name,
        program_code=program_row.program_code,
        title=program_row.title,
        country_code=program_row.country_code,
        scope=program_row.scope,
        region_codes=list(program_row.region_codes),
        official_url=program_row.official_url,
        current_version_id=current_version.id if current_version else None,
        benefit_type=current_version.benefit_type if current_version else None,
        normalization_status=current_version.normalization_status if current_version else None,
        review_due_at=current_version.review_due_at if current_version else None,
        is_stale=bool(current_version and current_version.review_due_at < today),
        is_own=program_row.household_id is not None,
        is_active=program_row.is_active,
        source_version=current_version.source_version if current_version else None,
        retrieved_at=current_version.retrieved_at if current_version else None,
        source_url=current_version.source_url if current_version else None,
        current_version=current_version,
    )


async def get_catalog_status(session: AsyncSession, household_id: UUID) -> CatalogStatus:
    """Katalogstand je Traeger, ueber kuratierten und eigenen Katalog hinweg.

    Auch Paket-/Dateiimporte schreiben eine Zeile in `funding_catalog_runs`,
    damit die Statusanzeige nicht nur automatische Feed-Laeufe kennt.
    """
    rows = await session.execute(
        text(
            "select distinct provider_key from funding_catalog_runs "
            "where household_id is null or household_id = :household_id "
            "union select distinct provider_key from funding_programs "
            "where household_id is null or household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    provider_keys = [row.provider_key for row in rows]

    providers = []
    for provider_key in provider_keys:
        success = (
            await session.execute(
                text(
                    "select finished_at, programs_new, programs_changed, programs_retired "
                    "from funding_catalog_runs where provider_key = :key and status = 'succeeded' "
                    "and (household_id is null or household_id = :household_id) "
                    "order by finished_at desc limit 1"
                ),
                {"key": provider_key, "household_id": str(household_id)},
            )
        ).first()
        failure = (
            await session.execute(
                text(
                    "select finished_at, error_message from funding_catalog_runs "
                    "where provider_key = :key and status = 'failed' "
                    "and (household_id is null or household_id = :household_id) "
                    "order by finished_at desc limit 1"
                ),
                {"key": provider_key, "household_id": str(household_id)},
            )
        ).first()
        latest = (
            await session.execute(
                text(
                    "select status from funding_catalog_runs where provider_key = :key "
                    "and (household_id is null or household_id = :household_id) "
                    "order by started_at desc limit 1"
                ),
                {"key": provider_key, "household_id": str(household_id)},
            )
        ).first()
        age_days = None
        if success is not None and success.finished_at is not None:
            age_days = (datetime.now(UTC) - success.finished_at).days
        providers.append(
            ProviderCatalogStatus(
                provider_key=provider_key,
                last_run_status=latest.status if latest else None,
                last_success_at=success.finished_at if success else None,
                last_failure_at=failure.finished_at if failure else None,
                last_error_message=failure.error_message if failure else None,
                age_days=age_days,
                programs_new=success.programs_new if success else 0,
                programs_changed=success.programs_changed if success else 0,
                programs_retired=success.programs_retired if success else 0,
            )
        )
    return CatalogStatus(providers=providers)


# ---------------------------------------------------------------------------
# Matchlauf
# ---------------------------------------------------------------------------


async def _current_program_versions(
    session: AsyncSession, household_id: UUID, country_code: str, project_type: str
) -> list[Any]:
    """Jeweils neueste Version je Programm, aus kuratiertem und eigenem Katalog.

    Hat ein Haushalt ein Programm mit demselben `semantic_key` eingespielt,
    gewinnt seine Zeile -- sie ist der bewusst eingespielte, neuere Stand. Ohne
    diese Deduplizierung stuende dasselbe Programm zweimal im Fördercheck.
    """
    rows = await session.execute(
        text(
            f"select {_PROGRAM_VERSION_COLUMNS}, p.household_id as program_household_id, "
            "p.semantic_key as program_semantic_key, "
            "p.scope as program_scope, p.region_codes as program_region_codes, "
            "p.title as program_title, p.provider_name as program_provider_name "
            "from funding_program_versions pv "
            "join funding_programs p on p.id = pv.program_id "
            "join lateral ("
            "  select max(retrieved_at) as latest from funding_program_versions "
            "  where program_id = pv.program_id "
            "  and household_id is not distinct from pv.household_id"
            ") latest_pv on pv.retrieved_at = latest_pv.latest "
            "where p.is_active and p.country_code = :country_code "
            "and (p.household_id is null or p.household_id = :household_id) "
            "and ((p.household_id is null and pv.household_id is null) or "
            "     pv.household_id = p.household_id) "
            "and (pv.project_types = '{}' or :project_type = any(pv.project_types))"
        ),
        {
            "country_code": country_code,
            "project_type": project_type,
            "household_id": str(household_id),
        },
    )
    by_key: dict[str, Any] = {}
    for row in rows:
        effective = _effective_program_content(
            row.program_semantic_key,
            row.source_version,
            row.criteria_json,
            row.benefit_json,
            row.loan_terms_json,
            allow_bundled_compatibility=row.program_household_id is None,
        )
        values = dict(row._mapping)
        values.update(
            source_version=effective["source_version"],
            criteria_json=effective["criteria_json"],
            benefit_json=effective["benefit_json"],
            loan_terms_json=effective["loan_terms_json"],
            benefit_type=effective["benefit_type"] or row.benefit_type,
        )
        effective_row = SimpleNamespace(**values)
        existing = by_key.get(effective_row.program_semantic_key)
        if existing is None or (
            existing.program_household_id is None and effective_row.program_household_id is not None
        ):
            by_key[effective_row.program_semantic_key] = effective_row
    return list(by_key.values())


async def _eligible_cost_basis_minor(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, funding_tags: list[str] | None
) -> int | None:
    if not funding_tags:
        return None
    items = await project_service.list_cost_items(session, household_id, scenario_id)
    eligible = set(funding_tags)
    return int(
        sum(
            item.forecast_minor
            for item in items
            if item.status != "cancelled"
            and item.cost_kind == "cash"
            and eligible.intersection(item.funding_tags)
        )
    )


def _input_fingerprint(
    scenario_version: int, facts: EligibilityFacts, program_version_ids: list[str]
) -> str:
    payload = json.dumps(
        {
            "scenario_version": scenario_version,
            "facts": facts.model_dump(mode="json"),
            "program_version_ids": sorted(program_version_ids),
            "effective_content_version": EFFECTIVE_FUNDING_CONTENT_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def run_funding_check(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID, user_id: UUID
) -> FundingCheckRunResponse:
    project = await project_service.require_project(session, household_id, project_id)
    scenario = await project_service.require_scenario(
        session, household_id, project_id, scenario_id
    )
    profile = await get_eligibility_profile(session, household_id, project_id)
    facts = _facts_from_profile(profile)
    as_of = facts.application_date or scenario.as_of_date

    version_rows = await _current_program_versions(
        session, household_id, project.country_code, project.project_type
    )
    fingerprint = _input_fingerprint(scenario.version, facts, [str(row.id) for row in version_rows])

    existing = (
        await session.execute(
            text(
                "select id, scenario_version, input_fingerprint, engine_version, "
                "warnings_json, created_at from project_funding_check_runs "
                "where scenario_id = :scenario_id and input_fingerprint = :fingerprint"
            ),
            {"scenario_id": str(scenario_id), "fingerprint": fingerprint},
        )
    ).first()
    if existing is not None:
        return await _load_check_run_response(session, household_id, existing.id)

    match_results: list[tuple[Any, ProgramMatchResult]] = []
    for row in version_rows:
        cost_basis = await _eligible_cost_basis_minor(
            session, household_id, scenario_id, row.benefit_json.get("eligible_funding_tags")
        )
        program_input = ProgramMatchInput(
            program_version_id=str(row.id),
            program_semantic_key=row.program_semantic_key,
            country_code=project.country_code,
            region_codes=tuple(row.program_region_codes),
            scope=row.program_scope,
            applicant_types=tuple(row.applicant_types),
            project_types=tuple(row.project_types),
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            review_due_at=row.review_due_at.date(),
            normalization_status=row.normalization_status,
            criteria_json=row.criteria_json,
            benefit_json=row.benefit_json,
            combination_rules_json=row.combination_rules_json,
        )
        result = match_program(
            program_input,
            facts,
            project_type=project.project_type,
            country_code=project.country_code,
            region_code=project.region_code,
            eligible_cost_basis_minor=cost_basis,
            as_of=as_of,
        )
        if result is not None:
            match_results.append((row, result))

    run_row = (
        await session.execute(
            text(
                "insert into project_funding_check_runs "
                "(household_id, scenario_id, scenario_version, input_fingerprint, "
                "engine_version, used_program_version_ids, warnings_json, created_by) "
                "values (:household_id, :scenario_id, :scenario_version, :input_fingerprint, "
                ":engine_version, :used_program_version_ids, '[]'::jsonb, :created_by) "
                "returning id, created_at"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                "scenario_version": scenario.version,
                "input_fingerprint": fingerprint,
                "engine_version": ENGINE_VERSION,
                "used_program_version_ids": [str(row.id) for row in version_rows],
                "created_by": str(user_id),
            },
        )
    ).first()
    assert run_row is not None
    run_id = run_row.id

    for row, result in match_results:
        catalog_age = (datetime.now(UTC).date() - row.retrieved_at.date()).days
        await session.execute(
            text(
                "insert into project_funding_matches "
                "(household_id, check_run_id, program_version_id, input_fingerprint, "
                "input_snapshot, status, estimated_grant_min_minor, estimated_grant_max_minor, "
                "estimated_loan_min_minor, estimated_loan_max_minor, criteria_met_json, "
                "criteria_violated_json, criteria_missing_json, criteria_manual_json, "
                "match_engine_version, catalog_age_days) "
                "values (:household_id, :check_run_id, :program_version_id, :input_fingerprint, "
                "cast(:input_snapshot as jsonb), :status, :grant_min, :grant_max, :loan_min, "
                ":loan_max, cast(:met as jsonb), cast(:violated as jsonb), "
                "cast(:missing as jsonb), cast(:manual as jsonb), :engine_version, "
                ":catalog_age_days)"
            ),
            {
                "household_id": str(household_id),
                "check_run_id": str(run_id),
                "program_version_id": str(row.id),
                "input_fingerprint": fingerprint,
                "input_snapshot": facts.model_dump_json(),
                "status": result.status,
                "grant_min": result.benefit.grant_min_minor,
                "grant_max": result.benefit.grant_max_minor,
                "loan_min": result.benefit.loan_min_minor,
                "loan_max": result.benefit.loan_max_minor,
                "met": json.dumps([c.model_dump(mode="json") for c in result.criteria_met]),
                "violated": json.dumps(
                    [c.model_dump(mode="json") for c in result.criteria_violated]
                ),
                "missing": json.dumps([c.model_dump(mode="json") for c in result.criteria_missing]),
                "manual": json.dumps([c.model_dump(mode="json") for c in result.criteria_manual]),
                "engine_version": ENGINE_VERSION,
                "catalog_age_days": catalog_age,
            },
        )

    await record_audit_event(
        session,
        household_id,
        user_id,
        FUNDING_CHECK_RUN_ENTITY_TYPE,
        run_id,
        "create",
        metadata={"scenario_id": str(scenario_id), "matches": len(match_results)},
    )
    return await _load_check_run_response(session, household_id, run_id)


async def _load_check_run_response(
    session: AsyncSession, household_id: UUID, run_id: UUID
) -> FundingCheckRunResponse:
    run_row = (
        await session.execute(
            text(
                "select id, scenario_id, scenario_version, input_fingerprint, engine_version, "
                "warnings_json, created_at from project_funding_check_runs "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(run_id), "household_id": str(household_id)},
        )
    ).first()
    if run_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_check_run_not_found"
        )

    match_rows = await session.execute(
        text(
            "select m.id, m.program_version_id, m.status, m.estimated_grant_min_minor, "
            "m.estimated_grant_max_minor, m.estimated_loan_min_minor, "
            "m.estimated_loan_max_minor, m.criteria_met_json, m.criteria_violated_json, "
            "m.criteria_missing_json, m.criteria_manual_json, m.catalog_age_days, "
            "m.checked_at, pv.program_id, p.semantic_key, p.title, p.provider_name "
            "from project_funding_matches m "
            "join funding_program_versions pv on pv.id = m.program_version_id "
            "join funding_programs p on p.id = pv.program_id "
            "where m.check_run_id = :run_id order by m.status, p.title"
        ),
        {"run_id": str(run_id)},
    )
    matches = [
        FundingMatch(
            id=row.id,
            program_version_id=row.program_version_id,
            program_id=row.program_id,
            program_semantic_key=row.semantic_key,
            program_title=row.title,
            provider_name=row.provider_name,
            status=row.status,
            estimated_grant_min_minor=row.estimated_grant_min_minor,
            estimated_grant_max_minor=row.estimated_grant_max_minor,
            estimated_loan_min_minor=row.estimated_loan_min_minor,
            estimated_loan_max_minor=row.estimated_loan_max_minor,
            criteria_met=[CriterionResultSchema(**c) for c in row.criteria_met_json],
            criteria_violated=[CriterionResultSchema(**c) for c in row.criteria_violated_json],
            criteria_missing=[CriterionResultSchema(**c) for c in row.criteria_missing_json],
            criteria_manual=[CriterionResultSchema(**c) for c in row.criteria_manual_json],
            catalog_age_days=row.catalog_age_days,
            checked_at=row.checked_at,
        )
        for row in match_rows
    ]
    return FundingCheckRunResponse(
        id=run_row.id,
        scenario_id=run_row.scenario_id,
        scenario_version=run_row.scenario_version,
        input_fingerprint=run_row.input_fingerprint,
        engine_version=run_row.engine_version,
        matches=matches,
        warnings=list(run_row.warnings_json),
        created_at=run_row.created_at,
    )


async def get_latest_funding_check(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> FundingCheckRunResponse | None:
    row = (
        await session.execute(
            text(
                "select id from project_funding_check_runs "
                "where scenario_id = :scenario_id and household_id = :household_id "
                "order by created_at desc limit 1"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    return await _load_check_run_response(session, household_id, row.id)


# ---------------------------------------------------------------------------
# Kombinationspruefung
# ---------------------------------------------------------------------------


async def evaluate_match_combinations(
    session: AsyncSession, household_id: UUID, match_ids: list[UUID]
) -> CombinationEvaluateResponse:
    result = await session.execute(
        text(
            "select m.id as match_id, pv.id as program_version_id, p.semantic_key, p.title, "
            "pv.combination_rules_json, p.scope, p.country_code, pv.applicant_types, "
            "pv.project_types, pv.valid_from, pv.valid_to, pv.review_due_at, "
            "pv.normalization_status, pv.criteria_json, pv.benefit_json, p.region_codes "
            "from project_funding_matches m "
            "join funding_program_versions pv on pv.id = m.program_version_id "
            "join funding_programs p on p.id = pv.program_id "
            "where m.id = any(:ids) and m.household_id = :household_id"
        ),
        {"ids": [str(i) for i in match_ids], "household_id": str(household_id)},
    )
    rows = list(result)
    if len(rows) != len(match_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="funding_match_not_found")

    # program_version_id traegt hier bewusst die Match-ID, nicht die
    # Programmversions-ID: die Kombinationsantwort soll match_ids referenzieren,
    # damit das Web die Kriterienliste des jeweiligen Treffers nachschlagen kann.
    programs = [
        ProgramMatchInput(
            program_version_id=str(row.match_id),
            program_semantic_key=row.semantic_key,
            country_code=row.country_code,
            region_codes=tuple(row.region_codes),
            scope=row.scope,
            applicant_types=tuple(row.applicant_types),
            project_types=tuple(row.project_types),
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            review_due_at=row.review_due_at.date(),
            normalization_status=row.normalization_status,
            criteria_json=row.criteria_json,
            benefit_json=row.benefit_json,
            combination_rules_json=row.combination_rules_json,
        )
        for row in rows
    ]
    titles_by_match_id = {str(row.match_id): row.title for row in rows}
    results = evaluate_combinations(programs)

    combinations = [
        CombinationResultSchema(
            match_ids=[UUID(i) for i in result.program_version_ids],
            program_titles=[titles_by_match_id[i] for i in result.program_version_ids],
            status=result.status,
        )
        for result in results
    ]
    return CombinationEvaluateResponse(combinations=combinations)


# ---------------------------------------------------------------------------
# Match uebernehmen
# ---------------------------------------------------------------------------


def _clamped(value: Any, *, maximum: int, warnings: list[str], label: str) -> int | None:
    """Programmangaben koennen ausserhalb der Schemagrenzen liegen (z. B. eine
    tilgungsfreie Zeit ueber 120 Monaten). Statt die Uebernahme daran scheitern
    zu lassen, wird gekappt und die Kappung benannt."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    if value > maximum:
        warnings.append(f"{label}_gekappt")
        return maximum
    return value


def _debt_plan_from_loan_terms(
    loan_terms: dict[str, Any],
    *,
    financing_source_id: UUID,
    principal_minor: int,
    name: str,
    starts_on: date,
    warnings: list[str],
    loan_term_variant_id: str | None = None,
    loan_rate_option_id: str | None = None,
    selected_term_months: int | None = None,
    selected_repayment_free_months: int | None = None,
    follow_up_rate_bps_annual: int | None = None,
) -> tuple[DebtPlanCreate, list[RatePeriodInput], list[SpecialRepaymentInput]] | None:
    """Baut aus `loan_terms_json` einen Tilgungsplan-Entwurf.

    Es wird nichts geraten: fehlt der Nominalzins, entsteht KEIN Plan. Die KfW
    aktualisiert den effektiven Jahreszins taeglich, der Katalog haelt ihn
    deshalb bewusst nicht vor (siehe Seed-Kommentar zu KfW 124). Ein erfundener
    Zins waere schlimmer als gar keiner -- er saehe aus wie eine Zusage.

    Die erzeugten Zinsphasen tragen `rate_kind='assumed'`, damit im Tilgungsplan
    sichtbar bleibt, was aus dem Programm stammt und was vertraglich zugesagt ist.
    """
    if principal_minor <= 0:
        return None

    resolved_terms = dict(loan_terms)
    repayment_type = "annuity"
    selected_rate_option = False
    variants = loan_terms.get("variants")
    if isinstance(variants, list) and variants:
        if loan_term_variant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_term_variant_required",
            )
        selected = next(
            (
                item
                for item in variants
                if isinstance(item, dict) and item.get("id") == loan_term_variant_id
            ),
            None,
        )
        if selected is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_term_variant_invalid",
            )
        min_term = selected.get("min_term_months")
        max_term = selected.get("max_term_months")
        min_free = selected.get("min_repayment_free_months")
        max_free = selected.get("max_repayment_free_months")
        if selected_term_months is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_term_months_required",
            )
        if (
            not isinstance(min_term, int)
            or not isinstance(max_term, int)
            or not min_term <= selected_term_months <= max_term
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_term_months_out_of_range",
            )
        if selected_repayment_free_months is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_repayment_free_months_required",
            )
        if (
            not isinstance(min_free, int)
            or not isinstance(max_free, int)
            or not min_free <= selected_repayment_free_months <= max_free
            or selected_repayment_free_months >= selected_term_months
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_repayment_free_months_out_of_range",
            )
        fixed_rate_months = selected.get("fixed_rate_months")
        if (
            isinstance(fixed_rate_months, int)
            and selected_term_months > fixed_rate_months
            and follow_up_rate_bps_annual is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_follow_up_rate_required",
            )
        resolved_terms.update(selected)
        resolved_terms["max_term_months"] = selected_term_months
        resolved_terms["max_repayment_free_months"] = selected_repayment_free_months
        repayment_type = str(selected.get("repayment_type", "annuity"))

    rate_options = loan_terms.get("rate_options")
    if isinstance(rate_options, list) and rate_options:
        if loan_rate_option_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_rate_option_required",
            )
        selected_option = next(
            (
                item
                for item in rate_options
                if isinstance(item, dict) and item.get("id") == loan_rate_option_id
            ),
            None,
        )
        if selected_option is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_rate_option_invalid",
            )
        if follow_up_rate_bps_annual is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="loan_follow_up_rate_required",
            )
        resolved_terms.update(selected_option)
        selected_rate_option = True

    rate_bps = resolved_terms.get("nominal_rate_bps_annual")
    if not isinstance(rate_bps, int) or isinstance(rate_bps, bool) or rate_bps < 0:
        warnings.append("kein_zinssatz_im_katalog")
        return None

    term_months = _clamped(
        resolved_terms.get("max_term_months"), maximum=600, warnings=warnings, label="laufzeit"
    )
    initial_repayment_bps = resolved_terms.get("initial_repayment_rate_bps")
    has_initial_repayment = (
        isinstance(initial_repayment_bps, int)
        and not isinstance(initial_repayment_bps, bool)
        and 0 <= initial_repayment_bps <= 10000
    )
    mode: AmortizationMode
    if repayment_type == "bullet":
        mode = "initial_repayment"
        initial_repayment_bps = 0
        has_initial_repayment = True
    elif term_months is not None:
        mode = "full_repayment_term"
    elif has_initial_repayment:
        mode = "initial_repayment"
    else:
        warnings.append("keine_tilgungsvorgabe_im_katalog")
        return None

    repayment_free = (
        _clamped(
            resolved_terms.get("max_repayment_free_months"),
            maximum=120,
            warnings=warnings,
            label="tilgungsfreie_zeit",
        )
        or 0
    )

    target_term_months: int | None = None
    if mode == "full_repayment_term":
        assert term_months is not None
        target_term_months = term_months - repayment_free if variants else term_months

    plan = DebtPlanCreate(
        name=name[:160],
        status="draft",
        principal_limit_minor=principal_minor,
        opening_balance_minor=principal_minor,
        starts_on=starts_on.isoformat(),
        amortization_mode=mode,
        target_term_months=target_term_months,
        initial_repayment_rate_bps=(
            int(cast(int, initial_repayment_bps)) if mode == "initial_repayment" else None
        ),
        repayment_free_months=repayment_free,
        project_financing_source_id=financing_source_id,
    )

    # Zinsverbilligung als zwei Phasen: verbilligt bis `subsidized_months`,
    # danach der Anschlusszins. `debt_rate_periods` bildet das exakt ab.
    subsidized_bps = resolved_terms.get("subsidized_rate_bps_annual")
    subsidized_months = resolved_terms.get("subsidized_months")
    periods: list[RatePeriodInput] = []
    if selected_rate_option:
        fixed_rate_months = int(resolved_terms["fixed_rate_months"])
        assert follow_up_rate_bps_annual is not None
        periods.append(
            RatePeriodInput(
                starts_after_month=0,
                ends_after_month=fixed_rate_months - 1,
                nominal_rate_bps_annual=rate_bps,
                rate_kind="contracted",
                label="Veröffentlichte BayernLabo-Kondition",
            )
        )
        periods.append(
            RatePeriodInput(
                starts_after_month=fixed_rate_months,
                nominal_rate_bps_annual=follow_up_rate_bps_annual,
                rate_kind="assumed",
                label="Anschlusszins (Planungsannahme)",
            )
        )
    elif (
        isinstance(subsidized_bps, int)
        and not isinstance(subsidized_bps, bool)
        and subsidized_bps >= 0
        and isinstance(subsidized_months, int)
        and not isinstance(subsidized_months, bool)
        and subsidized_months > 0
    ):
        periods.append(
            RatePeriodInput(
                starts_after_month=0,
                ends_after_month=subsidized_months - 1,
                nominal_rate_bps_annual=subsidized_bps,
                rate_kind="assumed",
                label="Zinsverbilligte Phase (aus Förderprogramm)",
            )
        )
        periods.append(
            RatePeriodInput(
                starts_after_month=subsidized_months,
                nominal_rate_bps_annual=rate_bps,
                rate_kind="assumed",
                label="Anschlusszins (aus Förderprogramm)",
            )
        )
    elif (
        term_months is not None
        and isinstance(resolved_terms.get("fixed_rate_months"), int)
        and resolved_terms["fixed_rate_months"] < term_months
    ):
        fixed_rate_months = int(resolved_terms["fixed_rate_months"])
        assert follow_up_rate_bps_annual is not None
        periods.append(
            RatePeriodInput(
                starts_after_month=0,
                ends_after_month=fixed_rate_months - 1,
                nominal_rate_bps_annual=rate_bps,
                rate_kind="contracted",
                label="Veröffentlichte KfW-Kondition",
            )
        )
        periods.append(
            RatePeriodInput(
                starts_after_month=fixed_rate_months,
                nominal_rate_bps_annual=follow_up_rate_bps_annual,
                rate_kind="assumed",
                label="Anschlusszins (Planungsannahme)",
            )
        )
    else:
        periods.append(
            RatePeriodInput(
                starts_after_month=0,
                nominal_rate_bps_annual=rate_bps,
                rate_kind="contracted" if variants else "assumed",
                label=(
                    "Veröffentlichte KfW-Kondition" if variants else "Aus Förderprogramm übernommen"
                ),
            )
        )
    special_repayments = (
        [
            SpecialRepaymentInput(
                occurs_after_month=term_months - 1,
                amount_minor=principal_minor,
                status="planned",
                label="Endfällige Gesamttilgung",
            )
        ]
        if repayment_type == "bullet" and term_months is not None
        else []
    )
    return plan, periods, special_repayments


async def adopt_match(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    match_id: UUID,
    user_id: UUID,
    payload: AdoptMatchRequest,
) -> AdoptMatchResponse:
    match_row = (
        await session.execute(
            text(
                "select m.id, m.status, m.program_version_id, m.estimated_grant_max_minor, "
                "m.estimated_loan_max_minor, pv.benefit_type, pv.loan_terms_json, "
                "pv.source_version, pv.criteria_json, pv.benefit_json, "
                "p.semantic_key, p.title, p.household_id as program_household_id "
                "from project_funding_matches m "
                "join funding_program_versions pv on pv.id = m.program_version_id "
                "join funding_programs p on p.id = pv.program_id "
                "where m.id = :id and m.household_id = :household_id"
            ),
            {"id": str(match_id), "household_id": str(household_id)},
        )
    ).first()
    if match_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="funding_match_not_found")

    loan_marker = f"request_id:{payload.request_id}" if payload.request_id else None
    grant_marker = f"request_id:{payload.request_id}#grant" if payload.request_id else None
    if payload.request_id is not None:
        # Beide Marker pruefen: bei `loan_with_grant` entstehen zwei Quellen, und
        # ein zweiter Klick darf keine davon doppelt anlegen.
        existing = (
            await session.execute(
                text(
                    "select id, source_type from project_financing_sources "
                    "where household_id = :household_id and funding_program_version_id = :pv_id "
                    "and note in (:loan_marker, :grant_marker) "
                    "order by case when source_type = 'subsidized_loan' then 0 else 1 end"
                ),
                {
                    "household_id": str(household_id),
                    "pv_id": str(match_row.program_version_id),
                    "loan_marker": loan_marker,
                    "grant_marker": grant_marker,
                },
            )
        ).fetchall()
        if existing:
            loan_row = next((r for r in existing if r.source_type == "subsidized_loan"), None)
            grant_row = next((r for r in existing if r.source_type == "public_grant"), None)
            primary = loan_row or grant_row
            assert primary is not None
            plan_row = (
                await session.execute(
                    text("select id from debt_plans where project_financing_source_id = :sid"),
                    {"sid": str(primary.id)},
                )
            ).first()
            return AdoptMatchResponse(
                financing_source_id=primary.id,
                already_applied=True,
                grant_financing_source_id=grant_row.id if loan_row and grant_row else None,
                debt_plan_id=plan_row.id if plan_row else None,
            )

    warnings: list[str] = []
    effective = _effective_program_content(
        match_row.semantic_key,
        match_row.source_version,
        match_row.criteria_json,
        match_row.benefit_json,
        match_row.loan_terms_json,
        allow_bundled_compatibility=match_row.program_household_id is None,
    )
    benefit_type = effective["benefit_type"] or match_row.benefit_type
    is_loan = benefit_type in ("loan", "loan_with_grant")
    loan_terms = effective["loan_terms_json"]
    base_name = payload.name or match_row.title

    loan_source_id: UUID | None = None
    grant_source_id: UUID | None = None
    debt_plan_id: UUID | None = None

    if is_loan:
        loan_amount = match_row.estimated_loan_max_minor or 0
        loan_source = await project_service.create_financing_source(
            session,
            household_id,
            payload.scenario_id,
            user_id,
            FinancingSourceCreate(
                name=base_name,
                source_type="subsidized_loan",
                status="idea",
                planned_amount_minor=loan_amount,
                funding_program_version_id=match_row.program_version_id,
                note=loan_marker,
            ),
            # Die Konditionen wandern mit in die Quelle, damit der
            # Tilgungsplan-Dialog sie auch dann vorbelegen kann, wenn hier
            # (mangels Zinssatz) noch kein Plan entsteht.
            source_snapshot={"loan_terms": loan_terms} if loan_terms else None,
        )
        loan_source_id = loan_source.id

        starts_on = await _project_start_date(session, household_id, project_id)
        built = _debt_plan_from_loan_terms(
            loan_terms,
            financing_source_id=loan_source.id,
            principal_minor=loan_amount,
            name=base_name,
            starts_on=starts_on,
            warnings=warnings,
            loan_term_variant_id=payload.loan_term_variant_id,
            loan_rate_option_id=payload.loan_rate_option_id,
            selected_term_months=payload.term_months,
            selected_repayment_free_months=payload.repayment_free_months,
            follow_up_rate_bps_annual=payload.follow_up_rate_bps_annual,
        )
        if built is not None:
            plan_input, periods, special_repayments = built
            plan = await debt_plan_service.create_debt_plan(
                session, household_id, user_id, plan_input
            )
            await debt_plan_service.replace_rate_periods(session, household_id, plan.id, periods)
            if special_repayments:
                await debt_plan_service.replace_special_repayments(
                    session, household_id, plan.id, special_repayments
                )
            debt_plan_id = plan.id

    # Zuschuss: eigenstaendige Quelle. Bei `loan_with_grant` ging der Anteil
    # frueher verloren, weil nur der Darlehensbetrag gebucht wurde.
    grant_amount = match_row.estimated_grant_max_minor or 0
    if not is_loan or grant_amount > 0:
        grant_source = await project_service.create_financing_source(
            session,
            household_id,
            payload.scenario_id,
            user_id,
            FinancingSourceCreate(
                name=f"{base_name} (Zuschuss)" if is_loan else base_name,
                source_type="public_grant",
                status="idea",
                planned_amount_minor=grant_amount,
                funding_program_version_id=match_row.program_version_id,
                note=grant_marker if is_loan else loan_marker,
            ),
        )
        grant_source_id = grant_source.id

    primary_id = loan_source_id or grant_source_id
    assert primary_id is not None
    return AdoptMatchResponse(
        financing_source_id=primary_id,
        already_applied=False,
        grant_financing_source_id=grant_source_id if loan_source_id else None,
        debt_plan_id=debt_plan_id,
        warnings=warnings,
    )


async def _project_start_date(session: AsyncSession, household_id: UUID, project_id: UUID) -> date:
    """Startdatum des Tilgungsplans: der geplante Projektstart, sonst heute."""
    row = (
        await session.execute(
            text(
                "select planned_start_on from major_projects "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(project_id), "household_id": str(household_id)},
        )
    ).first()
    if row is not None and row.planned_start_on is not None:
        return cast(date, row.planned_start_on)
    return datetime.now(UTC).date()
