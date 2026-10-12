"""Importpipeline-Referenzfaelle (Plan 15.3, Teilmenge): sichere XML-
Normalisierung, transaktionale Veroeffentlichung und Idempotenz. Der Adapter
selbst ist ohne verifizierten Live-Feed getestet (siehe Moduldokstring in
`funding_import_service.py`); diese Tests decken `parse()` und die
Transaktionslogik von `run_catalog_import()`/`_publish_drafts()` ab.

Schreibt direkt gegen `funding_programs`/`funding_program_versions`/
`funding_catalog_runs` -- dieselben Tabellen haben `revoke insert/update/
delete from authenticated` (siehe `test_project_schema_isolation.py`), ein
echter Importlauf braeuchte also ohnehin erweiterte Rechte wie
`services/worker` sie nutzt (kein `SET LOCAL ROLE authenticated`, siehe
`worker/db.py`). Der Test nutzt deshalb dieselbe Superuser-Session wie
`app.core.db.get_session_factory()` sie per Default liefert."""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app  # noqa: F401  -- setzt beim Import die Windows-Event-Loop-Policy
from app.services.funding_import_service import (
    CatalogPayload,
    FoerderdatenbankXmlAdapter,
    FundingImportError,
    NormalizedProgramDraft,
    run_catalog_import,
)

_VALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<foerderprogramme>
  <programm quelle_version="2026-08-01" veroeffentlicht="2026-08-01T00:00:00+00:00">
    <schluessel>test_import_programm_a</schluessel>
    <traeger>Testtraeger</traeger>
    <programmcode>T1</programmcode>
    <titel>Test-Foerderprogramm A</titel>
    <land>DE</land>
    <geltungsbereich>national</geltungsbereich>
    <regionen></regionen>
    <url>https://example.test/programm-a</url>
    <foerderart>grant</foerderart>
    <gueltig_ab>2026-01-01</gueltig_ab>
    <gueltig_bis></gueltig_bis>
  </programm>
</foerderprogramme>
"""

_INCOMPLETE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<foerderprogramme>
  <programm quelle_version="2026-08-01">
    <schluessel>test_import_broken</schluessel>
    <traeger>Testtraeger</traeger>
    <titel>Fehlt: Land/Geltungsbereich/URL</titel>
  </programm>
</foerderprogramme>
"""

_XXE_XML = """<?xml version="1.0"?>
<!DOCTYPE foerderprogramme [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<foerderprogramme>
  <programm quelle_version="2026-08-01">
    <schluessel>&xxe;</schluessel>
  </programm>
</foerderprogramme>
"""


@dataclass
class _FakeAdapter:
    """Testadapter: liefert eine feste Payload statt echten Netzzugriff.
    `parse()` bleibt der echte `FoerderdatenbankXmlAdapter`-Code."""

    payload: bytes
    provider_key: str

    async def fetch(self, previous_etag: str | None) -> CatalogPayload:
        return CatalogPayload(content=self.payload, etag="fixed-etag")

    def parse(self, payload: bytes) -> list[NormalizedProgramDraft]:
        return FoerderdatenbankXmlAdapter("https://example.test/feed.xml").parse(payload)


def test_parse_extracts_program_from_valid_xml() -> None:
    adapter = FoerderdatenbankXmlAdapter("https://example.test/feed.xml")
    drafts = adapter.parse(_VALID_XML.encode("utf-8"))
    assert len(drafts) == 1
    assert drafts[0].semantic_key == "test_import_programm_a"
    assert drafts[0].scope == "national"
    assert drafts[0].benefit_type == "grant"


def test_parse_rejects_xxe_entities_safely() -> None:
    adapter = FoerderdatenbankXmlAdapter("https://example.test/feed.xml")
    with pytest.raises(FundingImportError) as exc_info:
        adapter.parse(_XXE_XML.encode("utf-8"))
    # defusedxml lehnt die DOCTYPE/Entity-Deklaration ab, statt sie
    # aufzuloesen - kein Dateiinhalt landet im geparsten Schluessel.
    assert exc_info.value.code == "funding_xml_invalid"


async def _cleanup(provider_key: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("delete from funding_programs where semantic_key like 'test_import_%'")
        )
        await session.execute(
            text("delete from funding_catalog_runs where provider_key = :key"),
            {"key": provider_key},
        )


@pytest.mark.asyncio
async def test_incomplete_record_fails_whole_run_last_good_catalog_stays() -> None:
    provider_key = f"test-provider-{uuid.uuid4().hex}"
    try:
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            good_adapter = _FakeAdapter(
                payload=_VALID_XML.encode("utf-8"), provider_key=provider_key
            )
            good_run = await run_catalog_import(session, good_adapter)
            assert good_run.status == "succeeded"
            assert good_run.programs_new == 1

        async with session_factory() as session, session.begin():
            broken_adapter = _FakeAdapter(
                payload=_INCOMPLETE_XML.encode("utf-8"), provider_key=provider_key
            )
            broken_run = await run_catalog_import(session, broken_adapter)
            assert broken_run.status == "failed"

        async with session_factory() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "select is_active from funding_programs "
                        "where semantic_key = 'test_import_programm_a'"
                    )
                )
            ).first()
            assert row is not None
            assert row.is_active is True
    finally:
        await _cleanup(provider_key)


@pytest.mark.asyncio
async def test_identical_payload_is_skipped_idempotently() -> None:
    provider_key = f"test-provider-{uuid.uuid4().hex}"
    try:
        session_factory = get_session_factory()
        adapter = _FakeAdapter(payload=_VALID_XML.encode("utf-8"), provider_key=provider_key)

        async with session_factory() as session, session.begin():
            first = await run_catalog_import(session, adapter)
            assert first.programs_new == 1

        async with session_factory() as session, session.begin():
            second = await run_catalog_import(session, adapter)
            assert second.status == "succeeded"
            assert second.programs_new == 0
            assert second.programs_changed == 0

        async with session_factory() as session, session.begin():
            version_count = (
                await session.execute(
                    text(
                        "select count(*) as n from funding_program_versions v "
                        "join funding_programs p on p.id = v.program_id "
                        "where p.semantic_key = 'test_import_programm_a'"
                    )
                )
            ).first()
            assert version_count is not None
            assert version_count.n == 1
    finally:
        await _cleanup(provider_key)
