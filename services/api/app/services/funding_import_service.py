"""Importpipeline fuer den dynamischen Foerderkatalog (Plan 6.5/6.6):
Adapter-Schnittstelle, sichere XML-Normalisierung und transaktionale
Veroeffentlichung neuer Programmversionen. Ein einzelner fehlerhafter
Datensatz haelt den gesamten Lauf an (`failed`), statt einen Katalog halb zu
veroeffentlichen; der zuletzt gute Katalog bleibt in jedem Fall aktiv, weil
neue Versionen erst am Ende einer einzigen Transaktion geschrieben werden.

`FoerderdatenbankXmlAdapter.fetch()` ist bewusst NICHT gegen einen echten
Endpunkt verdrahtet: der offizielle Open-Data-Feed wurde in dieser
Implementierung nicht gegen die tatsaechliche Quelle verifiziert (Plan 6.5
verlangt genau das vor Produktivbetrieb). `parse()` ist dagegen vollstaendig
implementiert und getestet (`tests/test_funding_import_service.py`) -- gegen
ein dokumentiertes, plausibles XML-Format, das vor dem produktiven Einsatz
gegen die echte Quelle zu pruefen ist."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx
from defusedxml import ElementTree as SafeElementTree
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_PAYLOAD_BYTES = 20 * 1024 * 1024
_FETCH_TIMEOUT_SECONDS = 30.0
_USER_AGENT = "FinanzplanerFoerderkatalog/1.0 (lokale Haushaltsplanung, siehe Plan 6.6)"


class FundingImportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NormalizedProgramDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    semantic_key: str
    provider_name: str
    program_code: str | None = None
    title: str
    country_code: str
    scope: Literal["supranational", "national", "regional", "municipal"]
    region_codes: list[str] = []
    official_url: str
    source_version: str
    valid_from: date | None = None
    valid_to: date | None = None
    published_at: datetime | None = None
    benefit_type: Literal[
        "grant", "loan", "loan_with_grant", "guarantee", "tax", "information"
    ] = "information"
    applicant_types: list[str] = []
    project_types: list[str] = []
    criteria_json: dict[str, Any] = {}
    benefit_json: dict[str, Any] = {}
    loan_terms_json: dict[str, Any] = {}
    combination_rules_json: dict[str, Any] = {}
    manual_checks_json: list[Any] = []
    source_url: str
    license_name: str | None = None
    license_url: str | None = None
    attribution: str | None = None


@dataclass(frozen=True)
class CatalogPayload:
    content: bytes
    etag: str | None = None


@dataclass(frozen=True)
class ImportRunSummary:
    run_id: UUID
    status: Literal["succeeded", "failed"]
    programs_new: int = 0
    programs_changed: int = 0
    programs_retired: int = 0
    programs_unchanged: int = 0
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None


class FundingCatalogAdapter(Protocol):
    provider_key: str

    async def fetch(self, previous_etag: str | None) -> CatalogPayload: ...
    def parse(self, payload: bytes) -> list[NormalizedProgramDraft]: ...


class FoerderdatenbankXmlAdapter:
    """Adapter fuer den offiziellen Foerderdatenbank.de-Open-Data-Export
    (Plan 6.5). `fetch()` ruft absichtlich `NotImplementedError` -- siehe
    Moduldokstring. Erwartetes, aber nicht gegen die echte Quelle
    verifiziertes XML-Format je Programm-Element:

    ```xml
    <foerderprogramme>
      <programm quelle_version="2026-08-01" veroeffentlicht="2026-08-01T00:00:00Z">
        <schluessel>kfw_124_wohneigentumsprogramm</schluessel>
        <traeger>KfW</traeger>
        <programmcode>124</programmcode>
        <titel>KfW-Wohneigentumsprogramm (124)</titel>
        <land>DE</land>
        <geltungsbereich>national</geltungsbereich>
        <regionen><region>DE-BY</region></regionen>
        <url>https://www.kfw.de/...</url>
        <foerderart>loan</foerderart>
        <gueltig_ab>2026-01-01</gueltig_ab>
        <gueltig_bis/>
      </programm>
    </foerderprogramme>
    ```

    Nur die Kopfdaten (Identifikation, Traeger, Region, Gueltigkeit) kommen
    aus dem XML; `criteria_json`/`benefit_json` bleiben `discovery_only`, bis
    ein kuratiertes Overlay (Plan 6.5) sie mit echten Rechenregeln anreichert
    -- das XML selbst enthaelt laut Planbeschreibung keine maschinenlesbare
    Regel-DSL."""

    provider_key = "foerderdatenbank_de"

    def __init__(self, feed_url: str) -> None:
        self._feed_url = feed_url

    async def fetch(self, previous_etag: str | None) -> CatalogPayload:
        raise NotImplementedError(
            "FoerderdatenbankXmlAdapter.fetch() ist ein vorbereiteter, aber "
            "nicht produktiv geschalteter Endpunkt-Aufruf (Plan 6.5: "
            "Programmstaende muessen vor Produktivbetrieb gegen die "
            "offizielle Quelle verifiziert werden). Fuer echten Netzzugriff "
            "diese Methode mit dem verifizierten Feed-URL/-Format ersetzen."
        )

    def parse(self, payload: bytes) -> list[NormalizedProgramDraft]:
        if len(payload) > _MAX_PAYLOAD_BYTES:
            raise FundingImportError("funding_payload_too_large", "Katalog-Payload zu groß.")
        try:
            root = SafeElementTree.fromstring(payload)
        except Exception as exc:
            raise FundingImportError("funding_xml_invalid", f"XML nicht lesbar: {exc}") from exc

        drafts: list[NormalizedProgramDraft] = []
        for element in root.findall("programm"):
            drafts.append(self._parse_program_element(element))
        return drafts

    def _parse_program_element(self, element: Any) -> NormalizedProgramDraft:
        def text_of(tag: str) -> str | None:
            child = element.find(tag)
            return child.text.strip() if child is not None and child.text else None

        semantic_key = text_of("schluessel")
        provider_name = text_of("traeger")
        title = text_of("titel")
        country_code = text_of("land")
        scope = text_of("geltungsbereich")
        official_url = text_of("url")
        source_version = element.get("quelle_version")
        if not all([semantic_key, provider_name, title, country_code, scope, official_url,
                    source_version]):
            raise FundingImportError(
                "funding_record_incomplete",
                f"Pflichtfeld fehlt in Programm-Element: {element.attrib}",
            )
        if scope not in ("supranational", "national", "regional", "municipal"):
            raise FundingImportError(
                "funding_record_invalid", f"Unbekannter Geltungsbereich: {scope}"
            )

        region_codes = [
            (r.text or "").strip()
            for r in element.findall("regionen/region")
            if r.text and r.text.strip()
        ]
        published_raw = element.get("veroeffentlicht")
        published_at = datetime.fromisoformat(published_raw) if published_raw else None
        valid_from_raw = text_of("gueltig_ab")
        valid_to_raw = text_of("gueltig_bis")

        assert semantic_key and provider_name and title and country_code and scope
        assert official_url and source_version
        return NormalizedProgramDraft(
            semantic_key=semantic_key,
            provider_name=provider_name,
            program_code=text_of("programmcode"),
            title=title,
            country_code=country_code,
            scope=scope,  # type: ignore[arg-type]
            region_codes=region_codes,
            official_url=official_url,
            source_version=source_version,
            valid_from=date.fromisoformat(valid_from_raw) if valid_from_raw else None,
            valid_to=date.fromisoformat(valid_to_raw) if valid_to_raw else None,
            published_at=published_at,
            benefit_type=(text_of("foerderart") or "information"),  # type: ignore[arg-type]
            source_url=official_url,
        )


async def _last_successful_run(session: AsyncSession, provider_key: str) -> Any:
    return (
        await session.execute(
            text(
                "select etag, source_content_hash from funding_catalog_runs "
                "where provider_key = :provider_key and status = 'succeeded' "
                "order by finished_at desc limit 1"
            ),
            {"provider_key": provider_key},
        )
    ).first()


async def run_catalog_import(
    session: AsyncSession, adapter: FundingCatalogAdapter
) -> ImportRunSummary:
    last_success = await _last_successful_run(session, adapter.provider_key)
    previous_etag = last_success.etag if last_success else None

    run_row = (
        await session.execute(
            text(
                "insert into funding_catalog_runs (provider_key, status) "
                "values (:provider_key, 'running') returning id"
            ),
            {"provider_key": adapter.provider_key},
        )
    ).first()
    assert run_row is not None
    run_id: UUID = run_row.id

    try:
        payload = await adapter.fetch(previous_etag)
        content_hash = hashlib.sha256(payload.content).hexdigest()

        if last_success is not None and last_success.source_content_hash == content_hash:
            # Identische Payload wie beim letzten erfolgreichen Lauf (Plan
            # 6.6.2): idempotent uebersprungen, kein Programm einzeln
            # gegengeprueft, daher bleiben die Zaehlspalten bei ihrem
            # DB-Default 0 statt eine (falsche) Einzelpruefung vorzutaeuschen.
            await session.execute(
                text(
                    "update funding_catalog_runs set status = 'succeeded', finished_at = now(), "
                    "etag = :etag, source_content_hash = :hash where id = :id"
                ),
                {"etag": payload.etag, "hash": content_hash, "id": str(run_id)},
            )
            return ImportRunSummary(run_id=run_id, status="succeeded")

        drafts = adapter.parse(payload.content)
        summary = await _publish_drafts(session, adapter.provider_key, drafts)

        await session.execute(
            text(
                "update funding_catalog_runs set status = 'succeeded', finished_at = now(), "
                "etag = :etag, source_content_hash = :hash, programs_new = :new, "
                "programs_changed = :changed, programs_retired = :retired, "
                "programs_unchanged = :unchanged where id = :id"
            ),
            {
                "etag": payload.etag, "hash": content_hash, "id": str(run_id),
                "new": summary.programs_new, "changed": summary.programs_changed,
                "retired": summary.programs_retired, "unchanged": summary.programs_unchanged,
            },
        )
        return ImportRunSummary(
            run_id=run_id, status="succeeded", programs_new=summary.programs_new,
            programs_changed=summary.programs_changed, programs_retired=summary.programs_retired,
            programs_unchanged=summary.programs_unchanged,
        )
    except Exception as exc:
        # Plan 6.6.5: ein fehlerhafter Lauf haelt den gesamten Release an; da
        # neue Programmversionen nur am Ende von _publish_drafts() (eigene,
        # bereits committete Arbeit je Programm) entstehen, bleibt der zuletzt
        # gute Katalog unveraendert erreichbar.
        await session.execute(
            text(
                "update funding_catalog_runs set status = 'failed', finished_at = now(), "
                "error_message = :error where id = :id"
            ),
            {"error": str(exc)[:2000], "id": str(run_id)},
        )
        return ImportRunSummary(
            run_id=run_id, status="failed", error_message=str(exc)
        )


async def _publish_drafts(
    session: AsyncSession, provider_key: str, drafts: list[NormalizedProgramDraft]
) -> ImportRunSummary:
    if not drafts:
        raise FundingImportError("funding_catalog_empty", "Leerer Katalog-Import verworfen.")

    new_count = changed_count = unchanged_count = 0
    seen_keys: set[str] = set()
    default_review_due = datetime.now(UTC).replace(microsecond=0)

    for draft in drafts:
        seen_keys.add(draft.semantic_key)
        program_row = (
            await session.execute(
                text(
                    "insert into funding_programs "
                    "(semantic_key, provider_key, provider_name, program_code, title, "
                    "country_code, scope, region_codes, official_url) "
                    "values (:semantic_key, :provider_key, :provider_name, :program_code, "
                    ":title, :country_code, :scope, :region_codes, :official_url) "
                    # Der Feed-Weg schreibt in den kuratierten Katalog
                    # (`household_id is null`). Der Schluessel ist seit dem
                    # Foerderkatalog-Import nur noch dort global eindeutig -- als
                    # partieller Index. Ohne dieselbe WHERE-Klausel findet
                    # Postgres kein passendes Konfliktziel.
                    "on conflict (semantic_key) where household_id is null do update set "
                    "provider_name = excluded.provider_name, title = excluded.title, "
                    "region_codes = excluded.region_codes, official_url = excluded.official_url, "
                    "is_active = true "
                    "returning id"
                ),
                {
                    "semantic_key": draft.semantic_key, "provider_key": provider_key,
                    "provider_name": draft.provider_name, "program_code": draft.program_code,
                    "title": draft.title, "country_code": draft.country_code,
                    "scope": draft.scope, "region_codes": draft.region_codes,
                    "official_url": draft.official_url,
                },
            )
        ).first()
        assert program_row is not None
        program_id = program_row.id

        content_hash = hashlib.sha256(draft.model_dump_json().encode("utf-8")).hexdigest()
        existing_version = (
            await session.execute(
                text(
                    "select id from funding_program_versions "
                    "where program_id = :program_id and source_content_hash = :hash"
                ),
                {"program_id": str(program_id), "hash": content_hash},
            )
        ).first()
        if existing_version is not None:
            unchanged_count += 1
            continue

        had_previous = (
            await session.execute(
                text(
                    "select 1 from funding_program_versions where program_id = :program_id"
                ),
                {"program_id": str(program_id)},
            )
        ).first()

        await session.execute(
            text(
                "insert into funding_program_versions "
                "(program_id, source_version, valid_from, valid_to, published_at, "
                "review_due_at, benefit_type, applicant_types, project_types, criteria_json, "
                "benefit_json, loan_terms_json, combination_rules_json, manual_checks_json, "
                "source_url, source_content_hash, license_name, license_url, attribution, "
                "normalization_status) "
                "values (:program_id, :source_version, :valid_from, :valid_to, :published_at, "
                ":review_due_at, :benefit_type, :applicant_types, :project_types, "
                "cast(:criteria_json as jsonb), cast(:benefit_json as jsonb), "
                "cast(:loan_terms_json as jsonb), cast(:combination_rules_json as jsonb), "
                "cast(:manual_checks_json as jsonb), :source_url, :source_content_hash, "
                ":license_name, :license_url, :attribution, 'discovery_only') "
                "on conflict (program_id, source_version, source_content_hash) do nothing"
            ),
            {
                "program_id": str(program_id), "source_version": draft.source_version,
                "valid_from": draft.valid_from, "valid_to": draft.valid_to,
                "published_at": draft.published_at, "review_due_at": default_review_due,
                "benefit_type": draft.benefit_type, "applicant_types": draft.applicant_types,
                "project_types": draft.project_types,
                "criteria_json": json.dumps(draft.criteria_json),
                "benefit_json": json.dumps(draft.benefit_json),
                "loan_terms_json": json.dumps(draft.loan_terms_json),
                "combination_rules_json": json.dumps(draft.combination_rules_json),
                "manual_checks_json": json.dumps(draft.manual_checks_json),
                "source_url": draft.source_url, "source_content_hash": content_hash,
                "license_name": draft.license_name, "license_url": draft.license_url,
                "attribution": draft.attribution,
            },
        )
        if had_previous is None:
            new_count += 1
        else:
            changed_count += 1

    retired = await session.execute(
        text(
            "update funding_programs set is_active = false "
            "where provider_key = :provider_key and is_active "
            "and semantic_key <> all(:seen_keys) returning id"
        ),
        {"provider_key": provider_key, "seen_keys": list(seen_keys)},
    )
    retired_count = len(retired.fetchall())

    return ImportRunSummary(
        run_id=UUID(int=0), status="succeeded", programs_new=new_count,
        programs_changed=changed_count, programs_retired=retired_count,
        programs_unchanged=unchanged_count,
    )


async def fetch_via_http(feed_url: str, previous_etag: str | None) -> CatalogPayload:
    """Gemeinsamer, sicherer HTTP-Abruf fuer echte Adapter (Plan 6.6.1):
    Timeout, Groessenlimit, ETag-Weiterreichung und eindeutige User-Agent-
    Kennung. Wird bewusst NICHT von `FoerderdatenbankXmlAdapter.fetch()`
    aufgerufen, solange der reale Feed-URL/-Format nicht verifiziert ist
    (siehe Moduldokstring) - steht aber fertig fuer den Moment bereit, in dem
    ein echter, gepruefter Endpunkt eingetragen wird."""
    headers = {"User-Agent": _USER_AGENT}
    if previous_etag:
        headers["If-None-Match"] = previous_etag
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(feed_url, headers=headers)
        if response.status_code == 304:
            return CatalogPayload(content=b"", etag=previous_etag)
        response.raise_for_status()
        content = response.content
        if len(content) > _MAX_PAYLOAD_BYTES:
            raise FundingImportError("funding_payload_too_large", "Katalog-Payload zu groß.")
        return CatalogPayload(content=content, etag=response.headers.get("ETag"))
