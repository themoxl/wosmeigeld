"""Excel-/CSV-Kostenplanimport: Preview/Apply mit Fingerprint, Idempotenz,
explizite Zeilenzuordnung (kein stilles Ueberschreiben) und Sicherheits-
limits (Plan Abschnitt 10, P4)."""

import io
import json
import uuid
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

_MAPPING = {
    "section_name": "Abschnitt",
    "item_name": "Position",
    "quantity": "Menge",
    "unit_label": "Einheit",
    "unit_price_minor": "Einzelpreis",
    "estimate_manual_minor": "Schätzung",
    "note": "Notiz",
}

_CSV = (
    "Abschnitt;Position;Menge;Einheit;Einzelpreis;Schätzung;Notiz\n"
    "Rohbau;Fundament;;;;15000,00;Testnotiz\n"
    "Rohbau;Mauerwerk;10;m2;250,50;;\n"
).encode("utf-8-sig")


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"costimport-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8")
    household = client.post(
        "/v1/households", json={"name": "Import-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/v1/projects",
        json={"name": "Import-Projekt", "project_type": "home_build"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _baseline_scenario(headers: dict[str, str], project_id: str) -> dict[str, Any]:
    scenarios = client.get(f"/v1/projects/{project_id}/scenarios", headers=headers).json()
    return dict(scenarios[0])


def _preview(
    headers: dict[str, str], project_id: str, scenario_id: str, content: bytes = _CSV
) -> dict[str, Any]:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-import/preview",
        data={"mapping_json": json.dumps(_MAPPING)},
        files={"file": ("kostenplan.csv", content, "text/csv")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _apply(
    headers: dict[str, str],
    project_id: str,
    scenario_id: str,
    preview: dict[str, Any],
    resolutions: list[dict[str, Any]],
    request_id: str,
    content: bytes = _CSV,
    fingerprint: str | None = None,
) -> Any:
    return client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-import/apply",
        data={
            "mapping_json": json.dumps(_MAPPING),
            "resolutions_json": json.dumps(resolutions),
            "request_id": request_id,
            "preview_fingerprint": fingerprint or preview["preview_fingerprint"],
        },
        files={"file": ("kostenplan.csv", content, "text/csv")},
        headers=headers,
    )


def _create_resolutions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"row_number": row["row_number"], "action": "create"} for row in preview["rows"]]


def test_preview_normalizes_rows_without_writing_anything(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    preview = _preview(household_headers, project["id"], scenario["id"])
    assert preview["sums"]["row_count"] == 2
    assert preview["sums"]["new_items_count"] == 2
    assert preview["sums"]["rows_with_errors_count"] == 0

    fundament = next(r for r in preview["rows"] if r["item_name"] == "Fundament")
    assert fundament["calculation_mode"] == "fixed"
    assert fundament["estimate_manual_minor"] == 1_500_000
    assert fundament["matched_item_id"] is None

    mauerwerk = next(r for r in preview["rows"] if r["item_name"] == "Mauerwerk")
    assert mauerwerk["calculation_mode"] == "quantity"
    assert mauerwerk["unit_price_minor"] == 25_050

    items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        headers=household_headers,
    ).json()
    assert items == []


def test_apply_creates_sections_and_items(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    preview = _preview(household_headers, project["id"], scenario["id"])

    applied = _apply(
        household_headers,
        project["id"],
        scenario["id"],
        preview,
        _create_resolutions(preview),
        request_id=str(uuid.uuid4()),
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert len(result["created_item_ids"]) == 2
    assert result["idempotent_replay"] is False

    items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        headers=household_headers,
    ).json()
    assert len(items) == 2
    sections = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        headers=household_headers,
    ).json()
    assert len(sections) == 1
    assert sections[0]["name"] == "Rohbau"


def test_apply_is_idempotent_for_same_request_id(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    preview = _preview(household_headers, project["id"], scenario["id"])
    request_id = str(uuid.uuid4())

    first = _apply(
        household_headers, project["id"], scenario["id"], preview,
        _create_resolutions(preview), request_id=request_id,
    )
    assert first.status_code == 200, first.text
    assert first.json()["idempotent_replay"] is False

    second = _apply(
        household_headers, project["id"], scenario["id"], preview,
        _create_resolutions(preview), request_id=request_id,
    )
    assert second.status_code == 200, second.text
    assert second.json()["idempotent_replay"] is True
    assert second.json()["created_item_ids"] == first.json()["created_item_ids"]

    items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        headers=household_headers,
    ).json()
    assert len(items) == 2  # keine doppelte Uebernahme trotz zweitem Apply-Aufruf


def test_apply_rejects_stale_fingerprint(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    preview = _preview(household_headers, project["id"], scenario["id"])

    changed_csv = _CSV + b"Rohbau;Zusatzposition;;;;500,00;\n"
    response = _apply(
        household_headers, project["id"], scenario["id"], preview,
        _create_resolutions(preview), request_id=str(uuid.uuid4()), content=changed_csv,
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "preview_fingerprint_mismatch"


def test_apply_with_wrong_fingerprint_is_rejected(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    preview = _preview(household_headers, project["id"], scenario["id"])

    response = _apply(
        household_headers, project["id"], scenario["id"], preview,
        _create_resolutions(preview), request_id=str(uuid.uuid4()),
        fingerprint="0" * 64,
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "preview_fingerprint_mismatch"


def test_update_resolution_merges_without_clearing_unset_fields(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    section = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        json={"name": "Rohbau", "cost_class": "direct"},
        headers=household_headers,
    ).json()
    existing = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "name": "Fundament",
            "section_id": section["id"],
            "calculation_mode": "fixed",
            "estimate_manual_minor": 1_000_000,
            "vendor_label": "Bestandsfirma GmbH",
        },
        headers=household_headers,
    ).json()

    preview = _preview(household_headers, project["id"], scenario["id"])
    fundament_row = next(r for r in preview["rows"] if r["item_name"] == "Fundament")
    assert fundament_row["matched_item_id"] == existing["id"]

    resolutions = [
        {
            "row_number": r["row_number"],
            "action": "update" if r["item_name"] == "Fundament" else "create",
            "target_item_id": existing["id"] if r["item_name"] == "Fundament" else None,
        }
        for r in preview["rows"]
    ]
    applied = _apply(
        household_headers, project["id"], scenario["id"], preview, resolutions,
        request_id=str(uuid.uuid4()),
    )
    assert applied.status_code == 200, applied.text
    assert existing["id"] in applied.json()["updated_item_ids"]

    items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        headers=household_headers,
    ).json()
    updated = next(i for i in items if i["id"] == existing["id"])
    assert updated["estimate_manual_minor"] == 1_500_000  # aus der Importzeile uebernommen
    # Notiz stand nicht in dieser Zeile -> bestehendes Feld bleibt erhalten.
    assert updated["vendor_label"] == "Bestandsfirma GmbH"


def test_oversized_file_is_rejected(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    huge_content = b"Abschnitt;Position;Schaetzung\n" + (b"A;B;1\n" * 2_000_000)
    response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-import/preview",
        data={"mapping_json": json.dumps(_MAPPING)},
        files={"file": ("riesig.csv", huge_content, "text/csv")},
        headers=household_headers,
    )
    assert response.status_code == 400, response.text


def test_macro_enabled_extension_is_rejected(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-import/preview",
        data={"mapping_json": json.dumps(_MAPPING)},
        files={
            "file": (
                "kostenplan.xlsm",
                b"PK\x03\x04irrelevanter Inhalt",
                "application/vnd.ms-excel.sheet.macroEnabled.12",
            )
        },
        headers=household_headers,
    )
    assert response.status_code == 400, response.text


def test_update_resolution_rejects_target_from_different_scenario(
    household_headers: dict[str, str],
) -> None:
    """Adversarielle Review (P4): target_item_id kommt vom Aufrufer und darf
    keine Position aus einem ANDEREN Projekt/Szenario desselben Haushalts
    treffen, nur weil require_cost_item allein die Haushaltsgrenze prueft."""
    other_project = _create_project(household_headers)
    other_scenario = _baseline_scenario(household_headers, other_project["id"])
    other_section = client.post(
        f"/v1/projects/{other_project['id']}/scenarios/{other_scenario['id']}/cost-sections",
        json={"name": "Fremd", "cost_class": "direct"},
        headers=household_headers,
    ).json()
    foreign_item = client.post(
        f"/v1/projects/{other_project['id']}/scenarios/{other_scenario['id']}/cost-items",
        json={
            "name": "Fremdposition", "section_id": other_section["id"],
            "calculation_mode": "fixed", "estimate_manual_minor": 1_00,
        },
        headers=household_headers,
    ).json()

    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    preview = _preview(household_headers, project["id"], scenario["id"])

    resolutions = [
        {
            "row_number": r["row_number"],
            "action": "update" if r["item_name"] == "Fundament" else "create",
            "target_item_id": foreign_item["id"] if r["item_name"] == "Fundament" else None,
        }
        for r in preview["rows"]
    ]
    response = _apply(
        household_headers, project["id"], scenario["id"], preview, resolutions,
        request_id=str(uuid.uuid4()),
    )
    assert response.status_code == 422, response.text


def test_idempotency_key_is_scoped_per_scenario(household_headers: dict[str, str]) -> None:
    """Adversarielle Review (P4): dieselbe request_id fuer ein ANDERES
    Szenario desselben Projekts darf nicht das zwischengespeicherte Ergebnis
    des ersten Szenarios zurueckliefern, ohne je gegen das zweite zu laufen."""
    project = _create_project(household_headers)
    scenario_a = _baseline_scenario(household_headers, project["id"])
    scenario_b = client.post(
        f"/v1/projects/{project['id']}/scenarios",
        json={"name": "Zweitszenario"},
        headers=household_headers,
    ).json()

    shared_request_id = str(uuid.uuid4())

    preview_a = _preview(household_headers, project["id"], scenario_a["id"])
    applied_a = _apply(
        household_headers, project["id"], scenario_a["id"], preview_a,
        _create_resolutions(preview_a), request_id=shared_request_id,
    )
    assert applied_a.status_code == 200, applied_a.text
    assert applied_a.json()["idempotent_replay"] is False

    preview_b = _preview(household_headers, project["id"], scenario_b["id"])
    applied_b = _apply(
        household_headers, project["id"], scenario_b["id"], preview_b,
        _create_resolutions(preview_b), request_id=shared_request_id,
    )
    assert applied_b.status_code == 200, applied_b.text
    # Muss WIRKLICH gelaufen sein, nicht das zwischengespeicherte Ergebnis von
    # Szenario A zurueckgeben.
    assert applied_b.json()["idempotent_replay"] is False
    assert applied_b.json()["created_item_ids"] != applied_a.json()["created_item_ids"]

    items_b = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario_b['id']}/cost-items",
        headers=household_headers,
    ).json()
    assert len(items_b) == 2


def _minimal_xlsx_with_relationship(rel_type: str, target: str, target_mode: str | None) -> bytes:
    """Baut ein minimal gueltiges XLSX mit einer zusaetzlichen Beziehung in
    xl/_rels/workbook.xml.rels - fuer den Regressionstest der
    beziehungsbasierten Erkennung (Plan 10.2/adversarielle Review): ein
    VBA-Projekt oder externes Objekt muss ueber die Type-/TargetMode-
    Attribute erkannt werden, nicht nur ueber den festen Pfad
    xl/vbaproject.bin - Excel adressiert Teile ueber Beziehungen, nicht ueber
    Konventionspfade."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        "</sheets></workbook>"
    )
    target_mode_attr = f' TargetMode="{target_mode}"' if target_mode else ""
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId99" Type="{rel_type}" Target="{target}"{target_mode_attr}/>'
        "</Relationships>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData/></worksheet>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def test_relocated_vba_relationship_is_rejected(household_headers: dict[str, str]) -> None:
    """Adversarielle Review (P4): ein VBA-Projekt unter einem anderen Pfad
    als xl/vbaproject.bin (per Relationship-Indirektion, wie es echte
    Makro-Scanner umgeht) muss trotzdem erkannt werden."""
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    malicious = _minimal_xlsx_with_relationship(
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/vbaProject",
        "xl/binData/module1.bin",
        target_mode=None,
    )
    response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-import/preview",
        data={"mapping_json": json.dumps(_MAPPING)},
        files={"file": ("getarnt.xlsx", malicious, "application/octet-stream")},
        headers=household_headers,
    )
    assert response.status_code == 400, response.text


def test_external_ole_relationship_is_rejected(household_headers: dict[str, str]) -> None:
    """Adversarielle Review (P4): ein verknuepftes (statt eingebettetes)
    OLE-Objekt mit TargetMode=External ausserhalb von xl/embeddings/ muss
    ebenfalls erkannt werden."""
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    malicious = _minimal_xlsx_with_relationship(
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject",
        "http://attacker.example.com/payload.exe",
        target_mode="External",
    )
    response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-import/preview",
        data={"mapping_json": json.dumps(_MAPPING)},
        files={"file": ("getarnt2.xlsx", malicious, "application/octet-stream")},
        headers=household_headers,
    )
    assert response.status_code == 400, response.text


def test_plain_hyperlink_relationship_is_accepted(household_headers: dict[str, str]) -> None:
    """Gegenprobe: eine normale externe Hyperlink-Beziehung (z. B. ein
    Weblink in einer Zelle) darf NICHT als Sicherheitsverstoss abgelehnt
    werden - sonst waeren gewoehnliche, harmlose Tabellen kollateral
    blockiert."""
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    harmless = _minimal_xlsx_with_relationship(
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        "https://example.com/",
        target_mode="External",
    )
    response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-import/preview",
        data={"mapping_json": json.dumps(_MAPPING)},
        files={"file": ("harmlos.xlsx", harmless, "application/octet-stream")},
        headers=household_headers,
    )
    # Kein Sicherheitsverstoss - kann trotzdem 400 wegen fehlender Datums-/
    # Betragsspalten sein (das minimalgebaute XLSX hat kein echtes Kosten-
    # Datenblatt), aber niemals wegen einer externen Verknuepfung.
    if response.status_code == 400:
        assert "externe Verknuepfung" not in response.json()["detail"]


def test_mapping_profile_crud(household_headers: dict[str, str]) -> None:
    created = client.post(
        "/v1/cost-import-profiles",
        json={"name": "Standard-Mapping", "format": "csv_generic", "column_mapping": _MAPPING},
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    profile = created.json()

    listed = client.get("/v1/cost-import-profiles", headers=household_headers).json()
    assert any(p["id"] == profile["id"] for p in listed)

    deleted = client.delete(
        f"/v1/cost-import-profiles/{profile['id']}", headers=household_headers
    )
    assert deleted.status_code == 204

    listed_after = client.get("/v1/cost-import-profiles", headers=household_headers).json()
    assert all(p["id"] != profile["id"] for p in listed_after)
