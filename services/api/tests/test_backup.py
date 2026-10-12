"""Haushaltssicherung: herunterladen, wieder einspielen, und was dabei nicht
passieren darf.

Die Eigenschaften, an denen dieses Modul gemessen wird:

  1. Was hineingeht, kommt wieder heraus - auf den Cent genau.
  2. Ein Einspielen fasst keinen anderen Haushalt an.
  3. Kein Einspielen ohne vorherige Auskunft und ohne Bestaetigung des Namens.
  4. Eine fremde oder beschaedigte Datei wird abgewiesen, nicht geraten.
"""

import io
import json
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _neuer_haushalt(name: str = "Sicherungs-Testhaushalt") -> tuple[dict[str, str], str]:
    token = _signup(f"backup-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    haushalt = client.post(
        "/v1/households", json={"name": name}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": haushalt["id"]}, str(haushalt["id"])


@pytest.fixture()
def haushalt_mit_daten() -> tuple[dict[str, str], dict[str, object]]:
    """Ein Haushalt mit Konto und drei Buchungen - darunter krumme Betraege,
    an denen sich eine Rundung sofort zeigen wuerde."""
    headers, _ = _neuer_haushalt()
    konto = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking", "currency": "EUR"},
        headers=headers,
    ).json()

    betraege = [-123456, -1, 999999]
    for index, betrag in enumerate(betraege):
        antwort = client.post(
            "/v1/transactions",
            json={
                "account_id": konto["id"],
                "booked_date": f"2026-03-0{index + 1}",
                "amount_minor": betrag,
                "currency": "EUR",
                "payee_label": f"Empfaenger {index}",
                "note": f"Verwendung {index}",
            },
            headers=headers,
        )
        assert antwort.status_code == 201, antwort.text
    return headers, {"konto": konto, "betraege": betraege}


def _buchungen(headers: dict[str, str]) -> list[dict[str, object]]:
    antwort = client.get("/v1/transactions?limit=500", headers=headers)
    assert antwort.status_code == 200, antwort.text
    return list(antwort.json())


def _exportiere(headers: dict[str, str]) -> bytes:
    antwort = client.get("/v1/backup/export", headers=headers)
    assert antwort.status_code == 200, antwort.text
    assert antwort.headers["content-type"] == "application/zip"
    return antwort.content


def _datei(daten: bytes) -> dict[str, tuple[str, io.BytesIO, str]]:
    return {"file": ("sicherung.zip", io.BytesIO(daten), "application/zip")}


# ---------------------------------------------------------------------------
# Auskunft und Export
# ---------------------------------------------------------------------------


def test_info_nennt_zeilen_und_was_fehlt(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    antwort = client.get("/v1/backup/info", headers=headers)
    assert antwort.status_code == 200

    daten = antwort.json()
    assert daten["household_name"] == "Sicherungs-Testhaushalt"
    assert daten["total_rows"] > 0
    tabellen = {eintrag["table"]: eintrag["rows"] for eintrag in daten["tables"]}
    assert tabellen["transactions"] == 3
    assert tabellen["accounts"] == 1
    # Was die Sicherung NICHT kann, muss sie sagen - sonst verspricht das Wort
    # "Sicherung" mehr, als hier drinsteht.
    assert any("Dokumente" in satz for satz in daten["excluded"])


def test_export_ist_ein_lesbares_archiv_mit_manifest(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    archiv = zipfile.ZipFile(io.BytesIO(_exportiere(headers)))

    manifest = json.loads(archiv.read("manifest.json"))
    assert manifest["format"] == "finanzplaner-haushaltssicherung"
    assert manifest["version"] == 1
    assert manifest["haushalt"]["name"] == "Sicherungs-Testhaushalt"
    assert manifest["tabellen"]["transactions"] == 3
    assert manifest["zeilen_gesamt"] >= 4
    assert manifest["schema_fingerabdruck"].startswith("sha256:")

    # Leere Tabellen stehen nicht in der Datei - sonst waeren es 90 leere
    # Eintraege, durch die niemand mehr hindurchsieht.
    eintraege = [n for n in archiv.namelist() if n.startswith("tabellen/")]
    assert "tabellen/transactions.json" in eintraege
    assert len(eintraege) == len(manifest["tabellen"])


def test_export_enthaelt_keine_fremden_haushalte(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    fremde_headers, fremde_id = _neuer_haushalt("Fremder Haushalt")
    client.post(
        "/v1/accounts",
        json={"name": "Fremdkonto", "account_type": "checking", "currency": "EUR"},
        headers=fremde_headers,
    )

    archiv = zipfile.ZipFile(io.BytesIO(_exportiere(headers)))
    inhalt = archiv.read("tabellen/accounts.json").decode("utf-8")
    assert "Fremdkonto" not in inhalt
    assert fremde_id not in inhalt


# ---------------------------------------------------------------------------
# Der eigentliche Kreis: hinaus und wieder herein
# ---------------------------------------------------------------------------


def test_einspielen_stellt_geloeschte_daten_wieder_her(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, gegeben = haushalt_mit_daten
    sicherung = _exportiere(headers)

    assert len(_buchungen(headers)) == 3

    # Alles wegwerfen, was der Haushalt hat.
    client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Sicherungs-Testhaushalt"},
        headers=headers,
    )
    assert _buchungen(headers) == []

    antwort = client.post(
        "/v1/backup/import",
        files=_datei(sicherung),
        data={"confirm_name": "Sicherungs-Testhaushalt"},
        headers=headers,
    )
    assert antwort.status_code == 200, antwort.text
    ergebnis = antwort.json()
    assert ergebnis["imported_per_table"]["transactions"] == 3
    assert ergebnis["schema_matches"] is True
    # Aus derselben Instanz: kein Nutzerverweis darf ins Leere zeigen.
    assert ergebnis["cleared_user_references"] == 0

    nachher = _buchungen(headers)
    assert len(nachher) == 3
    assert {b["amount_minor"] for b in nachher} == set(gegeben["betraege"])  # type: ignore[arg-type]


def test_betraege_ueberstehen_den_umweg_auf_den_cent(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    """Der teuerste denkbare Fehler waere ein Betrag, der sich beim Sichern
    um einen Cent verschiebt. Deshalb Zeile fuer Zeile verglichen, nicht nur
    die Summe."""
    headers, _ = haushalt_mit_daten
    sicherung = _exportiere(headers)

    vorher = {
        (b["booked_date"], b["payee_label"], b["amount_minor"])
        for b in _buchungen(headers)
    }

    client.post(
        "/v1/backup/import",
        files=_datei(sicherung),
        data={"confirm_name": "Sicherungs-Testhaushalt"},
        headers=headers,
    )

    nachher = {
        (b["booked_date"], b["payee_label"], b["amount_minor"])
        for b in _buchungen(headers)
    }
    assert nachher == vorher


def test_einspielen_ersetzt_den_zwischenzeitlichen_stand(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    """Wiederherstellen heisst zuruecksetzen, nicht zusammenfuehren: was nach
    der Sicherung entstanden ist, muss danach weg sein."""
    headers, gegeben = haushalt_mit_daten
    sicherung = _exportiere(headers)

    konto = gegeben["konto"]
    client.post(
        "/v1/transactions",
        json={
            "account_id": konto["id"],  # type: ignore[index]
            "booked_date": "2026-04-01",
            "amount_minor": -5000,
            "currency": "EUR",
            "payee_label": "Nach der Sicherung",
            "note": "darf verschwinden",
        },
        headers=headers,
    )
    assert len(_buchungen(headers)) == 4

    client.post(
        "/v1/backup/import",
        files=_datei(sicherung),
        data={"confirm_name": "Sicherungs-Testhaushalt"},
        headers=headers,
    )

    danach = _buchungen(headers)
    assert len(danach) == 3
    assert all(b["payee_label"] != "Nach der Sicherung" for b in danach)


def test_einspielen_laesst_fremde_haushalte_unberuehrt(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    sicherung = _exportiere(headers)

    fremde_headers, _ = _neuer_haushalt("Unbeteiligter Haushalt")
    fremdes_konto = client.post(
        "/v1/accounts",
        json={"name": "Bleibt", "account_type": "checking", "currency": "EUR"},
        headers=fremde_headers,
    ).json()

    client.post(
        "/v1/backup/import",
        files=_datei(sicherung),
        data={"confirm_name": "Sicherungs-Testhaushalt"},
        headers=headers,
    )

    konten = client.get("/v1/accounts", headers=fremde_headers).json()
    assert [k["id"] for k in konten] == [fremdes_konto["id"]]


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


def test_vorschau_nennt_beide_seiten_und_aendert_nichts(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    sicherung = _exportiere(headers)

    antwort = client.post("/v1/backup/preview", files=_datei(sicherung), headers=headers)
    assert antwort.status_code == 200, antwort.text
    vorschau = antwort.json()

    assert vorschau["household_name"] == "Sicherungs-Testhaushalt"
    assert vorschau["source_household_name"] == "Sicherungs-Testhaushalt"
    assert vorschau["schema_matches"] is True
    assert vorschau["rows_in_file"] > 0
    assert vorschau["unknown_tables"] == []

    tabellen = {e["table"]: e for e in vorschau["tables"]}
    assert tabellen["transactions"]["rows_in_file"] == 3
    assert tabellen["transactions"]["rows_in_household"] == 3
    assert tabellen["transactions"]["handling"] == "ersetzen"
    # Kategorien lassen sich nicht loeschen, nur ueberschreiben - die Vorschau
    # muss das sagen, statt "ersetzen" zu versprechen.
    assert tabellen["categories"]["handling"] == "ueberschreiben"

    # Und nach alldem steht der Haushalt unveraendert da.
    assert len(_buchungen(headers)) == 3


# ---------------------------------------------------------------------------
# Was abgewiesen werden muss
# ---------------------------------------------------------------------------


def test_falscher_haushaltsname_verhindert_das_einspielen(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    sicherung = _exportiere(headers)

    antwort = client.post(
        "/v1/backup/import",
        files=_datei(sicherung),
        data={"confirm_name": "Irgendein anderer Haushalt"},
        headers=headers,
    )
    assert antwort.status_code == 400
    assert antwort.json()["detail"] == "household_name_mismatch"
    assert len(_buchungen(headers)) == 3


def test_fremde_datei_wird_abgewiesen(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    for inhalt, erwartet in (
        (b"Das ist kein ZIP.", "ZIP-Archiv"),
        (b"", None),
    ):
        antwort = client.post("/v1/backup/preview", files=_datei(inhalt), headers=headers)
        assert antwort.status_code == 400, antwort.text
        if erwartet:
            assert erwartet in antwort.json()["detail"]


def test_zip_ohne_manifest_wird_abgewiesen(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    headers, _ = haushalt_mit_daten
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as archiv:
        archiv.writestr("irgendwas.txt", "hallo")

    antwort = client.post("/v1/backup/preview", files=_datei(puffer.getvalue()), headers=headers)
    assert antwort.status_code == 400
    assert "manifest.json" in antwort.json()["detail"]


def test_sicherung_aus_neuerer_fassung_wird_abgewiesen(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    """Eine Datei aus einer spaeteren Fassung koennte Felder tragen, die diese
    hier stillschweigend fallenliesse. Lieber verweigern als halb einspielen."""
    headers, _ = haushalt_mit_daten
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as archiv:
        archiv.writestr(
            "manifest.json",
            json.dumps({"format": "finanzplaner-haushaltssicherung", "version": 99}),
        )

    antwort = client.post("/v1/backup/preview", files=_datei(puffer.getvalue()), headers=headers)
    assert antwort.status_code == 400
    assert "neueren Fassung" in antwort.json()["detail"]


# ---------------------------------------------------------------------------
# Rechte
# ---------------------------------------------------------------------------


def test_ohne_haushaltskopf_kein_zugriff() -> None:
    token = _signup(f"backup-nohh-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    for pfad in ("/v1/backup/info", "/v1/backup/export"):
        assert client.get(pfad, headers=_auth_headers(token)).status_code == 400


def test_fremdes_mitglied_kommt_nicht_an_die_sicherung(
    haushalt_mit_daten: tuple[dict[str, str], dict[str, object]],
) -> None:
    """Der Haushaltskopf allein reicht nicht - ohne Mitgliedschaft ist Schluss.
    Sonst waere die Sicherung der bequemste Weg, einen fremden Haushalt am
    Stueck mitzunehmen."""
    headers, _ = haushalt_mit_daten
    fremder_token = _signup(
        f"backup-fremd-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    fremde_headers = {
        **_auth_headers(fremder_token),
        "X-Household-Id": headers["X-Household-Id"],
    }
    assert client.get("/v1/backup/export", headers=fremde_headers).status_code == 403
    assert client.get("/v1/backup/info", headers=fremde_headers).status_code == 403
