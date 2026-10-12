"""Paket-/Dateiimport und Export fuer den Foerderkatalog (Import-Plan S3).

Abgrenzung zu `funding_import_service.py`: das dort liegende Geruest bedient den
automatischen Feed-Weg (Adapter, ETag, alles-oder-nichts). Dieser Dienst bedient
den Nutzerweg aus der Oberflaeche: Vorschau, abwaehlbare Zeilen, Rest laeuft
durch. Beide schreiben in dieselben Tabellen.

Zwei Entscheidungen praegen den Code:

1. **Import heisst Version anhaengen, nie Zeile aendern.** `funding_program_
   versions` ist unveraenderlich. Ein aktualisiertes Programm bekommt eine neue
   Version; die alte bleibt stehen. Vorhaben, die den alten Stand als
   Finanzierungsquelle uebernommen haben, zeigen weiter darauf und aendern ihre
   Zahlen nicht rueckwirkend.

2. **Der Inhaltshash wird berechnet, nicht uebernommen.** Ein mitgelieferter
   Hash waere eine Behauptung; nur ein aus dem Inhalt gebildeter erkennt
   Aenderungen zuverlaessig.
"""

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import funding_packages
from app.schemas.funding import (
    FundingExportRequest,
    FundingImportRequest,
    FundingImportResult,
    FundingPackageMeta,
    FundingPackagePayload,
    FundingPreviewItem,
    FundingPreviewResponse,
    FundingProgramDetail,
    FundingProgramTermsUpdate,
    PortableFundingProgram,
    PreviewStatus,
)
from app.services import funding_catalog_service
from app.services.funding_rule_validation import validate_freshness, validate_rule_json

# Felder, deren Aenderung dem Nutzer in der Vorschau im Klartext genannt wird.
_COMPARED_FIELDS: dict[str, str] = {
    "benefit_json": "Förderbetrag",
    "criteria_json": "Bedingungen",
    "loan_terms_json": "Darlehenskonditionen",
    "combination_rules_json": "Kombinierbarkeit",
    "benefit_type": "Förderart",
    "project_types": "Vorhabenarten",
    "applicant_types": "Antragstellerarten",
    "valid_from": "Gültig ab",
    "valid_to": "Gültig bis",
    "normalization_status": "Recherchestand",
}


def _content_hash(program: PortableFundingProgram) -> str:
    """Hash ueber genau die Felder, die den fachlichen Stand ausmachen.

    Bewusst ohne `source_version`: sonst erzeugte eine reine Versionsumbenennung
    ohne inhaltliche Aenderung eine neue Programmversion.
    """
    payload = json.dumps(
        {
            "criteria_json": program.criteria_json,
            "benefit_json": program.benefit_json,
            "loan_terms_json": program.loan_terms_json,
            "combination_rules_json": program.combination_rules_json,
            "manual_checks_json": program.manual_checks_json,
            "benefit_type": program.benefit_type,
            "applicant_types": sorted(program.applicant_types),
            "project_types": sorted(program.project_types),
            "valid_from": program.valid_from.isoformat() if program.valid_from else None,
            "valid_to": program.valid_to.isoformat() if program.valid_to else None,
            "normalization_status": program.normalization_status,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_loan_terms(terms: dict[str, Any]) -> list[str]:
    """Prueft die vom Formular gepflegten Standard-Kreditbausteine."""
    limits = {
        "max_term_months": (1, 600),
        "fixed_rate_months": (1, 600),
        "max_repayment_free_months": (0, 120),
        "nominal_rate_bps_annual": (0, 100_000),
        "initial_repayment_rate_bps": (0, 10_000),
        "subsidized_rate_bps_annual": (0, 100_000),
        "subsidized_months": (1, 600),
    }
    problems: list[str] = []
    for key, (minimum, maximum) in limits.items():
        value = terms.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"loan_terms_json.{key} muss eine ganze Zahl sein")
        elif not minimum <= value <= maximum:
            problems.append(f"loan_terms_json.{key} muss zwischen {minimum} und {maximum} liegen")
    if (terms.get("subsidized_rate_bps_annual") is None) != (
        terms.get("subsidized_months") is None
    ):
        problems.append("Zinsverbilligter Zinssatz und Dauer muessen gemeinsam angegeben werden")
    note = terms.get("note")
    if note is not None and not isinstance(note, str):
        problems.append("loan_terms_json.note muss Text sein")

    rate_as_of = terms.get("rate_as_of")
    if rate_as_of is not None:
        if not isinstance(rate_as_of, str):
            problems.append("loan_terms_json.rate_as_of muss ein ISO-Datum sein")
        else:
            try:
                date.fromisoformat(rate_as_of)
            except ValueError:
                problems.append("loan_terms_json.rate_as_of muss ein ISO-Datum sein")

    variants = terms.get("variants")
    if variants is not None:
        if not isinstance(variants, list) or not variants:
            problems.append("loan_terms_json.variants muss eine nicht leere Liste sein")
            return problems
        seen_ids: set[str] = set()
        for index, raw in enumerate(variants):
            prefix = f"loan_terms_json.variants[{index}]"
            if not isinstance(raw, dict):
                problems.append(f"{prefix} muss ein Objekt sein")
                continue
            variant_id = raw.get("id")
            if not isinstance(variant_id, str) or not variant_id.strip():
                problems.append(f"{prefix}.id muss Text sein")
            elif variant_id in seen_ids:
                problems.append(f"{prefix}.id ist doppelt: {variant_id}")
            else:
                seen_ids.add(variant_id)
            if not isinstance(raw.get("label"), str) or not raw.get("label", "").strip():
                problems.append(f"{prefix}.label muss Text sein")
            repayment_type = raw.get("repayment_type")
            if repayment_type not in ("annuity", "bullet"):
                problems.append(f"{prefix}.repayment_type muss annuity oder bullet sein")

            variant_limits = {
                "min_term_months": (1, 600),
                "max_term_months": (1, 600),
                "fixed_rate_months": (1, 600),
                "min_repayment_free_months": (0, 120),
                "max_repayment_free_months": (0, 120),
                "nominal_rate_bps_annual": (0, 100_000),
                "effective_rate_bps_annual": (0, 100_000),
            }
            for key, (minimum, maximum) in variant_limits.items():
                value = raw.get(key)
                if isinstance(value, bool) or not isinstance(value, int):
                    problems.append(f"{prefix}.{key} muss eine ganze Zahl sein")
                elif not minimum <= value <= maximum:
                    problems.append(f"{prefix}.{key} muss zwischen {minimum} und {maximum} liegen")

            min_term = raw.get("min_term_months")
            max_term = raw.get("max_term_months")
            min_free = raw.get("min_repayment_free_months")
            max_free = raw.get("max_repayment_free_months")
            if isinstance(min_term, int) and isinstance(max_term, int) and min_term > max_term:
                problems.append(f"{prefix}: Mindestlaufzeit darf Höchstlaufzeit nicht übersteigen")
            if isinstance(min_free, int) and isinstance(max_free, int) and min_free > max_free:
                problems.append(
                    f"{prefix}: Mindest-Anlaufzeit darf Höchst-Anlaufzeit nicht übersteigen"
                )
            if repayment_type == "bullet" and (min_free != 0 or max_free != 0):
                problems.append(f"{prefix}: endfällige Varianten haben keine Anlaufzeit")

    rate_options = terms.get("rate_options")
    if rate_options is not None:
        if not isinstance(rate_options, list) or not rate_options:
            problems.append("loan_terms_json.rate_options muss eine nicht leere Liste sein")
        else:
            seen_option_ids: set[str] = set()
            for index, raw in enumerate(rate_options):
                prefix = f"loan_terms_json.rate_options[{index}]"
                if not isinstance(raw, dict):
                    problems.append(f"{prefix} muss ein Objekt sein")
                    continue
                option_id = raw.get("id")
                if not isinstance(option_id, str) or not option_id.strip():
                    problems.append(f"{prefix}.id muss Text sein")
                elif option_id in seen_option_ids:
                    problems.append(f"{prefix}.id ist doppelt: {option_id}")
                else:
                    seen_option_ids.add(option_id)
                if not isinstance(raw.get("label"), str) or not raw.get("label", "").strip():
                    problems.append(f"{prefix}.label muss Text sein")
                for key, minimum, maximum in (
                    ("fixed_rate_months", 1, 600),
                    ("nominal_rate_bps_annual", 0, 100_000),
                    ("effective_rate_bps_annual", 0, 100_000),
                ):
                    value = raw.get(key)
                    if isinstance(value, bool) or not isinstance(value, int):
                        problems.append(f"{prefix}.{key} muss eine ganze Zahl sein")
                    elif not minimum <= value <= maximum:
                        problems.append(
                            f"{prefix}.{key} muss zwischen {minimum} und {maximum} liegen"
                        )
        if variants is not None:
            problems.append("loan_terms_json darf nicht variants und rate_options kombinieren")
    return problems


# ---------------------------------------------------------------------------
# Mitgelieferte Pakete
# ---------------------------------------------------------------------------


def get_package(package_id: str) -> FundingPackagePayload:
    raw = funding_packages.get_package(package_id)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_package_not_found"
        )
    return FundingPackagePayload.model_validate(raw)


async def list_packages(session: AsyncSession, household_id: UUID) -> list[FundingPackageMeta]:
    """Pakete inklusive Installationsstand im Katalog des Haushalts.

    `installed_count` zaehlt Programme, die es schon gibt; `outdated_count` die
    davon, deren aktive Version inhaltlich vom Paket abweicht. Daraus wird in
    der Oberflaeche „Installieren“ / „Ergänzen“ / „Aktualisieren“ / „Aktuell“.
    """
    existing = await _existing_by_key(session, household_id)

    metas: list[FundingPackageMeta] = []
    for raw in funding_packages.list_packages():
        package = FundingPackagePayload.model_validate(raw)
        installed = 0
        outdated = 0
        for program in package.programs:
            current = existing.get(program.semantic_key)
            # Ein kuratierter Eintrag ist noch keine Installation dieses
            # Pakets. Aktualisierungspakete duerfen ihn haushaltsseitig
            # ueberlagern, gelten davor aber als nicht installiert.
            if current is None or current["household_id"] is None:
                continue
            installed += 1
            if current["source_content_hash"] != _content_hash(program):
                outdated += 1
        metas.append(
            FundingPackageMeta(
                id=package.id,
                name=package.name,
                description=package.description,
                icon=package.icon,
                publisher=package.publisher,
                package_version=package.package_version,
                researched_on=package.researched_on,
                program_count=len(package.programs),
                installed_count=installed,
                outdated_count=outdated,
            )
        )
    return metas


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


async def _existing_by_key(session: AsyncSession, household_id: UUID) -> dict[str, dict[str, Any]]:
    """Aktive Version je `semantic_key`, aus eigenem UND kuratiertem Katalog.

    Der kuratierte Bestand muss mit hinein: nur so kann die Vorschau erkennen,
    dass ein Paket ein mitgeliefertes Programm haushaltsseitig ueberschreiben
    wuerde (Status `conflict`).
    """
    rows = await session.execute(
        text(
            "select p.id as program_id, p.household_id, p.semantic_key, "
            "v.id as version_id, v.source_version, v.source_content_hash, v.retrieved_at, "
            "v.criteria_json, v.benefit_json, v.loan_terms_json, v.combination_rules_json, "
            "v.benefit_type, v.applicant_types, v.project_types, v.valid_from, v.valid_to, "
            "v.normalization_status "
            "from funding_programs p "
            "left join lateral ("
            "  select * from funding_program_versions x where x.program_id = p.id and "
            "  ((p.household_id is null and x.household_id is null) or "
            "   x.household_id = p.household_id) "
            "  order by x.retrieved_at desc limit 1"
            ") v on true "
            "where p.household_id is null or p.household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = dict(row._mapping)
        current = result.get(entry["semantic_key"])
        # Bei gleichem Schluessel gewinnt die Haushaltszeile -- sie ist der Stand,
        # gegen den ein erneuter Import tatsaechlich verglichen werden muss.
        if current is None or (
            current["household_id"] is None and entry["household_id"] is not None
        ):
            result[entry["semantic_key"]] = entry
    return result


def _changed_fields(program: PortableFundingProgram, existing: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    incoming: dict[str, Any] = {
        "benefit_json": program.benefit_json,
        "criteria_json": program.criteria_json,
        "loan_terms_json": program.loan_terms_json,
        "combination_rules_json": program.combination_rules_json,
        "benefit_type": program.benefit_type,
        "project_types": sorted(program.project_types),
        "applicant_types": sorted(program.applicant_types),
        "valid_from": program.valid_from,
        "valid_to": program.valid_to,
        "normalization_status": program.normalization_status,
    }
    for field, label in _COMPARED_FIELDS.items():
        current = existing.get(field)
        if isinstance(current, list):
            current = sorted(current)
        if incoming.get(field) != current:
            changed.append(label)
    return changed


async def _adoption_counts(session: AsyncSession, household_id: UUID) -> dict[UUID, int]:
    rows = await session.execute(
        text(
            "select funding_program_version_id as version_id, count(*) as n "
            "from project_financing_sources "
            "where household_id = :household_id and funding_program_version_id is not null "
            "group by funding_program_version_id"
        ),
        {"household_id": str(household_id)},
    )
    return {row.version_id: int(row.n) for row in rows}


async def preview_import(
    session: AsyncSession, household_id: UUID, payload: FundingImportRequest
) -> FundingPreviewResponse:
    existing = await _existing_by_key(session, household_id)
    adoptions = await _adoption_counts(session, household_id)
    today = datetime.now(UTC).date()

    items: list[FundingPreviewItem] = []
    for index, program in enumerate(payload.package.programs):
        problems = validate_rule_json(program.criteria_json, program.benefit_json, as_of=today)
        problems += _validate_loan_terms(program.loan_terms_json)
        problems += validate_freshness(program.review_due_on, as_of=today)

        current = existing.get(program.semantic_key)
        item_status: PreviewStatus
        changed: list[str] = []
        if problems:
            item_status = "invalid"
        elif current is None:
            item_status = "new"
        elif current["household_id"] is None:
            # Der Schluessel gehoert dem kuratierten Katalog. Ein Import legt eine
            # eigene Zeile an, die den mitgelieferten Stand im Fördercheck
            # verdeckt. Bei mitgelieferten Aktualisierungspaketen ist das die
            # beabsichtigte Installation, bei hochgeladenen Dateien nicht.
            item_status = "conflict"
            changed = _changed_fields(program, current)
        elif current["source_content_hash"] == _content_hash(program):
            item_status = "unchanged"
        else:
            item_status = "updated"
            changed = _changed_fields(program, current)

        is_builtin = funding_packages.get_package(payload.package.id) is not None
        items.append(
            FundingPreviewItem(
                index=index,
                semantic_key=program.semantic_key,
                title=program.title,
                provider_name=program.provider_name,
                benefit_type=program.benefit_type,
                status=item_status,
                recommended=item_status in ("new", "updated")
                or (item_status == "conflict" and is_builtin),
                changed_fields=changed,
                existing_source_version=current["source_version"] if current else None,
                existing_retrieved_at=current["retrieved_at"] if current else None,
                adopted_by_projects=(adoptions.get(current["version_id"], 0) if current else 0),
                problems=problems,
            )
        )

    return FundingPreviewResponse(
        package_id=payload.package.id,
        package_name=payload.package.name,
        items=items,
        new_count=sum(1 for i in items if i.status == "new"),
        updated_count=sum(1 for i in items if i.status == "updated"),
        unchanged_count=sum(1 for i in items if i.status == "unchanged"),
        conflict_count=sum(1 for i in items if i.status == "conflict"),
        invalid_count=sum(1 for i in items if i.status == "invalid"),
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


async def _upsert_program(
    session: AsyncSession, household_id: UUID, program: PortableFundingProgram
) -> tuple[UUID, bool]:
    """Programmkopf des Haushalts anlegen oder auffrischen. True = neu angelegt."""
    row = (
        await session.execute(
            text(
                "select id from funding_programs "
                "where household_id = :household_id and semantic_key = :semantic_key"
            ),
            {"household_id": str(household_id), "semantic_key": program.semantic_key},
        )
    ).first()
    params = {
        "household_id": str(household_id),
        "semantic_key": program.semantic_key,
        "provider_key": program.provider_key,
        "provider_name": program.provider_name,
        "program_code": program.program_code,
        "title": program.title,
        "country_code": program.country_code,
        "scope": program.scope,
        "region_codes": program.region_codes,
        "official_url": program.official_url,
    }
    if row is not None:
        await session.execute(
            text(
                "update funding_programs set provider_key = :provider_key, "
                "provider_name = :provider_name, program_code = :program_code, "
                "title = :title, country_code = :country_code, scope = :scope, "
                "region_codes = :region_codes, official_url = :official_url, "
                "is_active = true "
                "where id = :id"
            ),
            {**params, "id": str(row.id)},
        )
        return UUID(str(row.id)), False

    created = (
        await session.execute(
            text(
                "insert into funding_programs (household_id, semantic_key, provider_key, "
                "provider_name, program_code, title, country_code, scope, region_codes, "
                "official_url) values (:household_id, :semantic_key, :provider_key, "
                ":provider_name, :program_code, :title, :country_code, :scope, :region_codes, "
                ":official_url) returning id"
            ),
            params,
        )
    ).first()
    assert created is not None
    return UUID(str(created.id)), True


async def _insert_version(
    session: AsyncSession,
    household_id: UUID,
    program_id: UUID,
    program: PortableFundingProgram,
    content_hash: str,
) -> None:
    await session.execute(
        text(
            "insert into funding_program_versions (program_id, household_id, source_version, "
            "valid_from, valid_to, published_at, review_due_at, currency, benefit_type, "
            "applicant_types, project_types, criteria_json, benefit_json, loan_terms_json, "
            "combination_rules_json, manual_checks_json, source_url, source_content_hash, "
            "license_name, license_url, attribution, normalization_status) "
            "values (:program_id, :household_id, :source_version, :valid_from, :valid_to, "
            ":published_at, :review_due_at, :currency, :benefit_type, :applicant_types, "
            ":project_types, cast(:criteria_json as jsonb), cast(:benefit_json as jsonb), "
            "cast(:loan_terms_json as jsonb), cast(:combination_rules_json as jsonb), "
            "cast(:manual_checks_json as jsonb), :source_url, :source_content_hash, "
            ":license_name, :license_url, :attribution, :normalization_status)"
        ),
        {
            "program_id": str(program_id),
            "household_id": str(household_id),
            "source_version": program.source_version,
            "valid_from": program.valid_from,
            "valid_to": program.valid_to,
            "published_at": program.published_at,
            "review_due_at": datetime.combine(
                program.review_due_on, datetime.min.time(), tzinfo=UTC
            ),
            "currency": program.currency,
            "benefit_type": program.benefit_type,
            "applicant_types": program.applicant_types,
            "project_types": program.project_types,
            "criteria_json": json.dumps(program.criteria_json, default=str),
            "benefit_json": json.dumps(program.benefit_json, default=str),
            "loan_terms_json": json.dumps(program.loan_terms_json, default=str),
            "combination_rules_json": json.dumps(program.combination_rules_json, default=str),
            "manual_checks_json": json.dumps(program.manual_checks_json, default=str),
            "source_url": program.source_url,
            "source_content_hash": content_hash,
            "license_name": program.license_name,
            "license_url": program.license_url,
            "attribution": program.attribution,
            "normalization_status": program.normalization_status,
        },
    )


async def execute_import(
    session: AsyncSession, household_id: UUID, payload: FundingImportRequest
) -> FundingImportResult:
    """Spielt die ausgewaehlten Programme in den Katalog des Haushalts.

    Jedes Programm laeuft in einem eigenen Savepoint: ohne ihn nimmt ein
    einziger Konflikt die ganze Transaktion mit, und ein Paket aus zehn
    Programmen bliebe wegen einer Zeile komplett aussen vor.
    """
    preview = await preview_import(session, household_id, payload)
    deselected = set(payload.skip_indices)

    programs_created = 0
    versions_created = 0
    unchanged = 0
    skipped_deselected = 0
    invalid = 0
    errors: list[str] = []

    for item in preview.items:
        program = payload.package.programs[item.index]
        if item.status == "invalid":
            invalid += 1
            continue
        if item.index in deselected:
            skipped_deselected += 1
            continue
        if item.status == "unchanged":
            unchanged += 1
            continue

        try:
            async with session.begin_nested():
                program_id, created = await _upsert_program(session, household_id, program)
                await _insert_version(
                    session, household_id, program_id, program, _content_hash(program)
                )
        except Exception as exc:  # noqa: BLE001 - Grund gehoert in die Antwort
            errors.append(f"Fehler bei Programm '{program.title}': {exc}")
            continue

        programs_created += 1 if created else 0
        versions_created += 1

    run_id = await _record_run(
        session,
        household_id,
        payload.package,
        programs_new=programs_created,
        programs_changed=versions_created - programs_created,
        programs_unchanged=unchanged,
        errors=errors,
    )
    return FundingImportResult(
        programs_created=programs_created,
        versions_created=versions_created,
        unchanged=unchanged,
        skipped_deselected=skipped_deselected,
        invalid=invalid,
        errors=errors,
        run_id=run_id,
    )


async def _record_run(
    session: AsyncSession,
    household_id: UUID,
    package: FundingPackagePayload,
    *,
    programs_new: int,
    programs_changed: int,
    programs_unchanged: int,
    errors: list[str],
) -> UUID | None:
    """Auch UI-Importe erscheinen im Katalogstatus, nicht nur Feed-Laeufe."""
    is_builtin = funding_packages.get_package(package.id) is not None
    provider_key = package.programs[0].provider_key if package.programs else package.id
    row = (
        await session.execute(
            text(
                "insert into funding_catalog_runs (household_id, source, package_id, "
                "provider_key, status, finished_at, programs_new, programs_changed, "
                "programs_unchanged, warnings, error_message) "
                "values (:household_id, :source, :package_id, :provider_key, :status, now(), "
                ":programs_new, :programs_changed, :programs_unchanged, "
                "cast(:warnings as jsonb), :error_message) returning id"
            ),
            {
                "household_id": str(household_id),
                "source": "package" if is_builtin else "file",
                "package_id": package.id,
                "provider_key": provider_key,
                "status": "failed" if errors and not programs_new else "succeeded",
                "programs_new": programs_new,
                "programs_changed": programs_changed,
                "programs_unchanged": programs_unchanged,
                "warnings": json.dumps(errors),
                "error_message": errors[0] if errors else None,
            },
        )
    ).first()
    return UUID(str(row.id)) if row is not None else None


# ---------------------------------------------------------------------------
# Export und Deaktivierung
# ---------------------------------------------------------------------------


async def export_programs(
    session: AsyncSession, household_id: UUID, payload: FundingExportRequest
) -> FundingPackagePayload:
    owner_condition = (
        "(p.household_id = :household_id or p.household_id is null)"
        if payload.include_curated
        else "p.household_id = :household_id"
    )
    conditions = [owner_condition]
    params: dict[str, Any] = {"household_id": str(household_id)}
    if payload.program_ids:
        conditions.append("p.id = any(cast(:program_ids as uuid[]))")
        params["program_ids"] = [str(pid) for pid in payload.program_ids]

    rows = await session.execute(
        text(
            "select p.semantic_key, p.provider_key, p.provider_name, p.program_code, p.title, "
            "p.country_code, p.scope, p.region_codes, p.official_url, "
            "v.source_version, v.valid_from, v.valid_to, v.published_at, v.review_due_at, "
            "v.currency, v.benefit_type, v.applicant_types, v.project_types, v.criteria_json, "
            "v.benefit_json, v.loan_terms_json, v.combination_rules_json, v.manual_checks_json, "
            "v.source_url, v.license_name, v.license_url, v.attribution, v.normalization_status "
            "from funding_programs p "
            "join lateral ("
            "  select * from funding_program_versions x where x.program_id = p.id and "
            "  ((p.household_id is null and x.household_id is null) or "
            "   x.household_id = p.household_id) "
            "  order by x.retrieved_at desc limit 1"
            ") v on true "
            "where " + " and ".join(conditions) + " order by p.provider_name, p.title"
        ),
        params,
    )

    programs = [
        PortableFundingProgram(
            semantic_key=row.semantic_key,
            provider_key=row.provider_key,
            provider_name=row.provider_name,
            program_code=row.program_code,
            title=row.title,
            country_code=row.country_code,
            scope=row.scope,
            region_codes=list(row.region_codes),
            official_url=row.official_url,
            source_version=row.source_version,
            source_url=row.source_url,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            published_at=row.published_at,
            review_due_on=row.review_due_at.date(),
            currency=row.currency,
            benefit_type=row.benefit_type,
            applicant_types=list(row.applicant_types),
            project_types=list(row.project_types),
            criteria_json=row.criteria_json,
            benefit_json=row.benefit_json,
            loan_terms_json=row.loan_terms_json,
            combination_rules_json=row.combination_rules_json,
            manual_checks_json=list(row.manual_checks_json),
            license_name=row.license_name,
            license_url=row.license_url,
            attribution=row.attribution,
            normalization_status=row.normalization_status,
        )
        for row in rows
    ]

    return FundingPackagePayload(
        id="export",
        name=payload.package_name,
        description=payload.description,
        publisher="",
        package_version=date.today().isoformat(),
        researched_on=date.today(),
        programs=programs,
    )


async def update_program_terms(
    session: AsyncSession,
    household_id: UUID,
    program_id: UUID,
    payload: FundingProgramTermsUpdate,
) -> FundingProgramDetail:
    """Erzeugt aus manuellen Konditionen eine neue, unveraenderliche Version.

    Auch bei einem MITGELIEFERTEN Programm erlaubt: die neue Version wird ueber
    `_insert_version` dem Haushalt zugeordnet und legt sich als Overlay ueber
    den kuratierten Stand (die Versionsauswahl nimmt schlicht die zuletzt
    abgerufene). Der kuratierte Eintrag selbst bleibt unangetastet und wird
    beim naechsten Paketimport weiter aktualisiert - es entsteht also keine
    Abspaltung, sondern eine eigene Konditionsebene darueber.

    Das ist noetig, weil die wirtschaftlich wichtigste Groesse, der Zinssatz,
    in keinem kuratierten Programm steht: Foerderbanken setzen ihn taeglich neu
    fest, weshalb er bewusst nicht mitgeliefert wird. Ohne diesen Weg koennte
    ihn niemand nachtragen, und der Tilgungsplan bei der Uebernahme entstuende
    nie.

    Bereits uebernommene Finanzierungen zeigen weiterhin auf ihre alte Version;
    erst ein neuer Foerdercheck verwendet diesen Stand.
    """
    row = (
        await session.execute(
            text(
                "select p.id, p.household_id, p.semantic_key, p.provider_key, "
                "p.provider_name, p.program_code, p.title, p.country_code, p.scope, "
                "p.region_codes, p.official_url, v.source_version, v.valid_from, "
                "v.valid_to, v.published_at, v.review_due_at, v.currency, v.benefit_type, "
                "v.applicant_types, v.project_types, v.criteria_json, v.benefit_json, "
                "v.loan_terms_json, v.combination_rules_json, v.manual_checks_json, "
                "v.source_url, v.source_content_hash, v.license_name, v.license_url, "
                "v.attribution, v.normalization_status "
                "from funding_programs p "
                "join lateral ("
                "  select * from funding_program_versions x where x.program_id = p.id and "
                "  ((p.household_id is null and x.household_id is null) or "
                "   x.household_id = p.household_id) "
                "  order by x.retrieved_at desc limit 1"
                ") v on true where p.id = :program_id"
            ),
            {"program_id": str(program_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_program_not_found"
        )
    # household_id is None == mitgeliefertes Programm: bearbeitbar, siehe
    # Docstring. Nur ein Programm, das einem ANDEREN Haushalt gehoert, bleibt
    # unsichtbar - dort waere schon die Existenz eine Information.
    if row.household_id is not None and UUID(str(row.household_id)) != household_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_program_not_found"
        )

    effective = funding_catalog_service._effective_program_content(
        row.semantic_key,
        row.source_version,
        row.criteria_json,
        row.benefit_json,
        row.loan_terms_json,
        allow_bundled_compatibility=row.household_id is None,
    )
    problems = validate_rule_json(effective["criteria_json"], payload.benefit_json)
    problems.extend(_validate_loan_terms(payload.loan_terms_json))
    problems.extend(validate_freshness(payload.review_due_on))
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "funding_terms_invalid", "problems": problems},
        )

    program = PortableFundingProgram(
        semantic_key=row.semantic_key,
        provider_key=row.provider_key,
        provider_name=row.provider_name,
        program_code=row.program_code,
        title=row.title,
        country_code=row.country_code,
        scope=row.scope,
        region_codes=list(row.region_codes),
        official_url=row.official_url,
        source_version=payload.source_version,
        source_url=payload.source_url,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        published_at=row.published_at,
        review_due_on=payload.review_due_on,
        currency=row.currency,
        benefit_type=effective["benefit_type"] or row.benefit_type,
        applicant_types=list(row.applicant_types),
        project_types=list(row.project_types),
        criteria_json=effective["criteria_json"],
        benefit_json=payload.benefit_json,
        loan_terms_json=payload.loan_terms_json,
        combination_rules_json=row.combination_rules_json,
        manual_checks_json=list(row.manual_checks_json),
        license_name=row.license_name,
        license_url=row.license_url,
        attribution=row.attribution,
        normalization_status=row.normalization_status,
    )
    content_hash = _content_hash(program)
    if (
        content_hash == row.source_content_hash
        and payload.source_version == row.source_version
        and payload.source_url == row.source_url
        and payload.review_due_on == row.review_due_at.date()
    ):
        return await funding_catalog_service.get_funding_program(session, household_id, program_id)

    target_program_id = program_id
    if row.household_id is None:
        # Ein manueller Stand eines globalen Seeds ist Eigentum des Haushalts.
        # Deshalb bekommt er einen eigenen Programmkopf; andernfalls koennte
        # dessen neueste Version in den Katalog anderer Haushalte durchsickern.
        target_program_id, _ = await _upsert_program(session, household_id, program)
    await _insert_version(session, household_id, target_program_id, program, content_hash)
    return await funding_catalog_service.get_funding_program(
        session, household_id, target_program_id
    )


async def deactivate_program(
    session: AsyncSession, household_id: UUID, program_id: UUID
) -> FundingProgramDetail:
    row = (
        await session.execute(
            text("select household_id from funding_programs where id = :id"),
            {"id": str(program_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_program_not_found"
        )
    if row.household_id is None:
        # Der kuratierte Katalog gehoert dem Code. Wer ihn ersetzen will,
        # importiert ein eigenes Programm mit demselben semantic_key -- das
        # verdeckt den mitgelieferten Stand, ohne ihn anzutasten.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="curated_program_not_editable"
        )
    if UUID(str(row.household_id)) != household_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="funding_program_not_found"
        )

    await session.execute(
        text(
            "update funding_programs set is_active = false "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(program_id), "household_id": str(household_id)},
    )
    return await funding_catalog_service.get_funding_program(session, household_id, program_id)
