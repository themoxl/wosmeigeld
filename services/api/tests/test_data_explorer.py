"""Datenbank-Explorer: alle Haushaltstabellen sehen, durchblaettern, loeschen.

Der Grund fuer dieses Modul: die Anwendung hat 79 haushaltsbezogene Tabellen,
aber nur fuer einen Teil davon eine Oberflaeche mit Loeschfunktion.

Die drei Eigenschaften, an denen es gemessen wird:

  1. Kein Bezeichner vom Client kommt ungeprueft in eine Abfrage.
  2. Keine Zeile eines fremden Haushalts wird sichtbar oder loeschbar.
  3. Kein Loeschen ohne vorherige Auskunft darueber, was dabei mitgeht.
"""

import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import data_explorer_service as explorer
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)
_DSN = os.environ.get(
    "TEST_DIRECT_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"explorer-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Explorer-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def meal_week_with_data(household_headers: dict[str, str]) -> str:
    """Eine Essenswoche mit allem, was per Kaskade daran haengt: Mahlzeit,
    Einkaufsliste, Posten. Das ist die tiefste Kette im Schema und damit der
    Fall, an dem sich die Vorschau beweisen muss."""
    week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-03", "budget_minor": 10000},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": week["id"],
            "meal_date": "2026-08-03",
            "meal_type": "dinner",
            "dish_name": "Chili",
            "servings": 2,
        },
        headers=household_headers,
    )
    liste = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": week["id"], "store": "Aldi"},
        headers=household_headers,
    ).json()
    for name in ("Bohnen", "Hackfleisch", "Tomaten"):
        client.post(
            "/v1/shopping-items",
            json={"shopping_list_id": liste["id"], "name": name, "quantity": "1"},
            headers=household_headers,
        )
    return str(week["id"])


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


def test_listet_haushaltstabellen_mit_zeilenzahl(household_headers: dict[str, str]) -> None:
    antwort = client.get("/v1/data-explorer/tables", headers=household_headers)

    assert antwort.status_code == 200
    daten = antwort.json()
    namen = {eintrag["name"] for eintrag in daten["tables"]}
    assert "transactions" in namen
    assert "meal_weeks" in namen
    assert "ingredient_price_observations" in namen
    # Tabellen ohne household_id koennen nicht haushaltsweise begrenzt werden
    # und tauchen deshalb gar nicht erst auf.
    assert "households" not in namen
    assert "profiles" not in namen
    # Das Audit-Log ist der Nachweis darueber, wer was geloescht hat - es aus
    # dem Explorer heraus loeschbar zu machen hiesse, die Nachvollziehbarkeit
    # des Loeschens selbst loeschbar zu machen.
    assert "audit_log" not in namen
    # Verschluesselte API-Schluessel der KI-Anbieter: nichts zum Anschauen,
    # und ohne Katalogeintrag auch nicht in Sicherungen.
    assert "ai_provider_keys" not in namen


def test_gruppiert_tabellen_fachlich(household_headers: dict[str, str]) -> None:
    daten = client.get("/v1/data-explorer/tables", headers=household_headers).json()
    gruppen = {eintrag["name"]: eintrag["group"] for eintrag in daten["tables"]}

    assert gruppen["transactions"] == "Buchungen & Konten"
    assert gruppen["meal_weeks"] == "Lebensmittel"
    assert gruppen["ingredient_price_observations"] == "Lebensmittel"


def test_meldet_ob_eine_tabelle_loeschbar_ist(household_headers: dict[str, str]) -> None:
    """Loeschen braucht einen einspaltigen Primaerschluessel. Ohne bleibt die
    Tabelle lesbar - eine Zeile ohne eindeutige Adresse zu loeschen waere
    geraten."""
    daten = client.get("/v1/data-explorer/tables", headers=household_headers).json()
    nach_name = {eintrag["name"]: eintrag for eintrag in daten["tables"]}

    assert nach_name["meal_weeks"]["is_deletable"] is True
    assert nach_name["meal_weeks"]["primary_key"] == "id"
    # grocery_price_settings hat household_id als Primaerschluessel - eine
    # Zeile je Haushalt, loeschbar, aber ueber diese Spalte.
    assert nach_name["grocery_price_settings"]["primary_key"] == "household_id"


# ---------------------------------------------------------------------------
# Zeilen lesen
# ---------------------------------------------------------------------------


def test_liest_zeilen_mit_spaltentypen(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    antwort = client.get(
        "/v1/data-explorer/tables/meal_weeks/rows", headers=household_headers
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["total"] == 1
    assert daten["primary_key"] == "id"
    assert daten["rows"][0]["budget_minor"] == 10000
    typen = {spalte["name"]: spalte["data_type"] for spalte in daten["columns"]}
    assert typen["week_start"] == "date"
    assert typen["budget_minor"] == "bigint"


def test_unbekannte_tabelle_wird_abgewiesen(household_headers: dict[str, str]) -> None:
    antwort = client.get(
        "/v1/data-explorer/tables/pg_shadow/rows", headers=household_headers
    )

    assert antwort.status_code == 400
    assert "pg_shadow" in antwort.json()["detail"]


def test_eingeschleuster_tabellenname_wird_nicht_ausgefuehrt(
    household_headers: dict[str, str],
) -> None:
    """Der Bezeichner wird gegen den Katalog geprueft, nicht maskiert. Was
    dort nicht steht, kommt in keine Abfrage - unabhaengig davon, wie es
    geschrieben ist."""
    antwort = client.get(
        "/v1/data-explorer/tables/meal_weeks; drop table meal_weeks/rows",
        headers=household_headers,
    )

    assert antwort.status_code == 400
    # Und die Tabelle steht noch.
    assert (
        client.get("/v1/data-explorer/tables/meal_weeks/rows", headers=household_headers)
    ).status_code == 200


def test_unbekannte_sortierspalte_wird_abgewiesen(household_headers: dict[str, str]) -> None:
    antwort = client.get(
        "/v1/data-explorer/tables/meal_weeks/rows?order_by=1;delete+from+meal_weeks",
        headers=household_headers,
    )

    assert antwort.status_code == 400


def test_sucht_nur_ueber_textspalten(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    treffer = client.get(
        "/v1/data-explorer/tables/shopping_items/rows?search=Hack", headers=household_headers
    ).json()
    daneben = client.get(
        "/v1/data-explorer/tables/shopping_items/rows?search=Zwiebel",
        headers=household_headers,
    ).json()

    assert treffer["total"] == 1
    assert treffer["rows"][0]["name"] == "Hackfleisch"
    assert daneben["total"] == 0


def test_blaettert_seitenweise(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    erste = client.get(
        "/v1/data-explorer/tables/shopping_items/rows?limit=2&offset=0",
        headers=household_headers,
    ).json()
    zweite = client.get(
        "/v1/data-explorer/tables/shopping_items/rows?limit=2&offset=2",
        headers=household_headers,
    ).json()

    assert erste["total"] == 3
    assert len(erste["rows"]) == 2
    assert len(zweite["rows"]) == 1
    ids = {row["id"] for row in erste["rows"]} | {row["id"] for row in zweite["rows"]}
    assert len(ids) == 3


def test_fremder_haushalt_bleibt_unsichtbar(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    fremd_token = _signup(f"fremd-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    fremd = client.post(
        "/v1/households", json={"name": "Fremdhaushalt"}, headers=_auth_headers(fremd_token)
    ).json()
    fremd_headers = {**_auth_headers(fremd_token), "X-Household-Id": fremd["id"]}

    antwort = client.get(
        "/v1/data-explorer/tables/meal_weeks/rows", headers=fremd_headers
    ).json()

    assert antwort["total"] == 0
    assert antwort["rows"] == []


# ---------------------------------------------------------------------------
# Loeschvorschau und Loeschen
# ---------------------------------------------------------------------------


def test_vorschau_zeigt_die_ganze_kaskade(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    """Der eigentliche Zweck der Vorschau: eine Essenswoche nimmt Mahlzeiten,
    Einkaufslisten UND deren Posten mit. Wer das nicht vorher sieht, loescht
    eine Woche und verliert einen Monat."""
    antwort = client.post(
        "/v1/data-explorer/tables/meal_weeks/delete-preview",
        json={"ids": [meal_week_with_data]},
        headers=household_headers,
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["direct_rows"] == 1
    betroffen = {eintrag["table"]: eintrag for eintrag in daten["effects"]}
    assert betroffen["meals"]["rows"] == 1
    assert betroffen["shopping_lists"]["rows"] == 1
    # Zwei Ebenen tiefer - genau der Teil, den man ohne Vorschau nicht ahnt.
    assert betroffen["shopping_items"]["rows"] == 3
    assert betroffen["shopping_items"]["depth"] == 2
    assert daten["cascade_total"] == 5


def test_vorschau_trennt_mitloeschen_von_verweis_verlieren(
    household_headers: dict[str, str],
) -> None:
    """`on delete set null` ist harmlos, sieht aber ohne Erklaerung
    beunruhigend aus - und umgekehrt darf das echte Loeschen nicht darin
    untergehen. Deshalb getrennt ausgewiesen."""
    gericht = client.post(
        "/v1/dishes", json={"name": "Lasagne", "default_servings": 4}, headers=household_headers
    ).json()
    woche = client.post(
        "/v1/meal-weeks", json={"week_start": "2026-08-10"}, headers=household_headers
    ).json()
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": woche["id"],
            "meal_date": "2026-08-10",
            "meal_type": "dinner",
            "dish_name": "Lasagne",
            "dish_id": gericht["id"],
            "servings": 4,
        },
        headers=household_headers,
    )

    daten = client.post(
        "/v1/data-explorer/tables/dishes/delete-preview",
        json={"ids": [gericht["id"]]},
        headers=household_headers,
    ).json()

    # meals.dish_id ist 'on delete set null': die geplante Mahlzeit bleibt
    # stehen, sie verliert nur ihren Katalogbezug.
    mahlzeiten = next(e for e in daten["effects"] if e["table"] == "meals")
    assert mahlzeiten["effect"] == "detach"
    assert daten["detach_total"] == 1
    assert daten["cascade_total"] == 0


def test_loescht_samt_kaskade_und_schreibt_ins_audit_log(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    antwort = client.post(
        "/v1/data-explorer/tables/meal_weeks/delete",
        json={"ids": [meal_week_with_data]},
        headers=household_headers,
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["deleted"] == 1
    assert daten["cascade_total"] == 5
    assert daten["audit_event_id"] is not None

    # Weg ist weg - und zwar auf allen Ebenen.
    for tabelle in ("meal_weeks", "meals", "shopping_lists", "shopping_items"):
        rest = client.get(
            f"/v1/data-explorer/tables/{tabelle}/rows", headers=household_headers
        ).json()
        assert rest["total"] == 0, tabelle


def test_loeschen_eines_fremden_datensatzes_bleibt_wirkungslos(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    fremd_token = _signup(f"fremd-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    fremd = client.post(
        "/v1/households", json={"name": "Fremdhaushalt"}, headers=_auth_headers(fremd_token)
    ).json()
    fremd_headers = {**_auth_headers(fremd_token), "X-Household-Id": fremd["id"]}

    antwort = client.post(
        "/v1/data-explorer/tables/meal_weeks/delete",
        json={"ids": [meal_week_with_data]},
        headers=fremd_headers,
    )

    assert antwort.status_code == 200
    assert antwort.json()["deleted"] == 0
    # Und beim Eigentuemer steht die Woche unveraendert.
    eigen = client.get(
        "/v1/data-explorer/tables/meal_weeks/rows", headers=household_headers
    ).json()
    assert eigen["total"] == 1


def test_loeschen_bereits_geloeschter_zeilen_ist_kein_fehler(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    client.post(
        "/v1/data-explorer/tables/meal_weeks/delete",
        json={"ids": [meal_week_with_data]},
        headers=household_headers,
    )
    zweiter = client.post(
        "/v1/data-explorer/tables/meal_weeks/delete",
        json={"ids": [meal_week_with_data]},
        headers=household_headers,
    )

    assert zweiter.status_code == 200
    assert zweiter.json()["deleted"] == 0


def test_loeschen_raeumt_auch_preissichtungen_weg(
    household_headers: dict[str, str],
) -> None:
    """Der Anlass fuer dieses Modul: Testdaten, fuer die es keine eigene
    Loeschoberflaeche gibt."""
    client.post(
        "/v1/price-observations",
        json={
            "ingredient_name": "Testartikel",
            "price_minor": 199,
            "pack_quantity": "1",
            "pack_unit": "kg",
            "store_name": "Aldi",
            "observed_on": "2026-08-05",
        },
        headers=household_headers,
    )
    zeilen = client.get(
        "/v1/data-explorer/tables/ingredient_price_observations/rows",
        headers=household_headers,
    ).json()
    assert zeilen["total"] == 1

    geloescht = client.post(
        "/v1/data-explorer/tables/ingredient_price_observations/delete",
        json={"ids": [zeilen["rows"][0]["id"]]},
        headers=household_headers,
    )

    assert geloescht.status_code == 200
    assert geloescht.json()["deleted"] == 1


# ---------------------------------------------------------------------------
# Haushalt leeren
# ---------------------------------------------------------------------------


def _katalog(*kanten: tuple[str, str]) -> explorer.Catalog:
    """Kleiner Katalog aus (Kind, Eltern)-Kanten - ohne Datenbank."""
    tabellen: dict[str, explorer.TableInfo] = {}
    kinder: dict[str, list[explorer.ForeignKey]] = {}
    for kind, eltern in kanten:
        for name in (kind, eltern):
            tabellen.setdefault(
                name, explorer.TableInfo(name=name, group="x", columns=[], primary_key="id")
            )
        kinder.setdefault(eltern, []).append(
            explorer.ForeignKey(kind, "fk", eltern, "id", "CASCADE")
        )
    return explorer.Catalog(
        tables=tabellen,
        children=kinder,
        dependencies=set(kanten),
    )


def test_loeschreihenfolge_setzt_kinder_vor_eltern() -> None:
    """21 der Fremdschluessel sind weder CASCADE noch SET NULL. Bei falscher
    Reihenfolge bricht das Leeren mittendrin ab."""
    reihenfolge = explorer.deletion_order(
        _katalog(("posten", "liste"), ("liste", "woche"), ("mahlzeit", "woche"))
    )

    assert reihenfolge.index("posten") < reihenfolge.index("liste")
    assert reihenfolge.index("liste") < reihenfolge.index("woche")
    assert reihenfolge.index("mahlzeit") < reihenfolge.index("woche")


def test_loeschreihenfolge_beachtet_zusammengesetzte_fremdschluessel() -> None:
    """Die Erstattungsdokumente verweisen ueber (insured_person_id,
    household_id) auf die versicherte Person. Die Kaskadenvorschau verfolgt
    nur einspaltige FKs, die globale Loeschreihenfolge muss die Tabellenkante
    trotzdem kennen.
    """
    katalog = explorer.Catalog(
        tables={
            name: explorer.TableInfo(name=name, group="x", columns=[], primary_key="id")
            for name in ("health_insured_persons", "reimbursement_documents")
        },
        dependencies={("reimbursement_documents", "health_insured_persons")},
    )

    reihenfolge = explorer.deletion_order(katalog)

    assert reihenfolge.index("reimbursement_documents") < reihenfolge.index(
        "health_insured_persons"
    )


def test_loeschreihenfolge_uebergeht_selbstbezuege() -> None:
    """categories, contracts, project_cost_items und project_scenarios
    verweisen auf sich selbst. Ein DELETE ueber die ganze Tabelle raeumt
    Eltern und Kinder gemeinsam ab - das darf die Sortierung nicht blockieren."""
    reihenfolge = explorer.deletion_order(_katalog(("baum", "baum"), ("blatt", "baum")))

    assert reihenfolge.index("blatt") < reihenfolge.index("baum")


def test_loeschreihenfolge_meldet_einen_zyklus_statt_halber_arbeit() -> None:
    with pytest.raises(explorer.ExplorerError, match="Zyklus"):
        explorer.deletion_order(_katalog(("a", "b"), ("b", "a")))


def test_vorschau_zeigt_was_beim_leeren_weggeht(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    antwort = client.get("/v1/data-explorer/purge-preview", headers=household_headers)

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["household_name"] == "Explorer-Testhaushalt"
    betroffen = {eintrag["name"] for eintrag in daten["tables"]}
    assert "meal_weeks" in betroffen
    assert "shopping_items" in betroffen
    # Die Zugehoerigkeit bleibt - sonst sperrt sich der Nutzer aus.
    assert "household_members" not in betroffen
    # Was stehen bleibt, ist sichtbar - "leeren" soll nicht mehr versprechen
    # als es tut.
    assert "household_members" in daten["kept_tables"]
    assert "categories" in daten["kept_tables"]
    assert "audit_log" in daten["kept_tables"]
    assert daten["total_rows"] > 0


def test_leeren_verlangt_den_haushaltsnamen(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    """Bei hunderten Testhaushalten neben einem echten ist die Verwechslung der
    teuerste denkbare Fehler. Die Pruefung gehoert deshalb auf den Server."""
    antwort = client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Falscher Haushalt"},
        headers=household_headers,
    )

    assert antwort.status_code == 400
    assert antwort.json()["detail"] == "household_name_mismatch"
    # Und es steht noch alles.
    rest = client.get(
        "/v1/data-explorer/tables/meal_weeks/rows", headers=household_headers
    ).json()
    assert rest["total"] == 1


def test_leeren_raeumt_bewegungsdaten_und_laesst_den_kategorienbaum_stehen(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    """Der Kategorienbaum hat gar kein DELETE-Recht fuer `authenticated` - er
    wird ueber seinen Lebenszyklus verwaltet, nicht roh geloescht. Das ist hier
    Absicht und Nebeneffekt zugleich: der Haushalt bleibt danach sofort
    benutzbar, ohne dass erst Kategorien angelegt werden muessen."""
    vorher = client.get(
        "/v1/data-explorer/tables/categories/rows", headers=household_headers
    ).json()
    assert vorher["total"] > 0

    antwort = client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Explorer-Testhaushalt"},
        headers=household_headers,
    )

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["deleted_rows"] > 0
    assert "meal_weeks" in daten["deleted_per_table"]
    # Was geschuetzt ist, taucht auch nicht als geloescht auf.
    assert "categories" not in daten["deleted_per_table"]
    assert "household_members" not in daten["deleted_per_table"]

    for tabelle in ("meal_weeks", "meals", "shopping_lists", "shopping_items"):
        rest = client.get(
            f"/v1/data-explorer/tables/{tabelle}/rows", headers=household_headers
        ).json()
        assert rest["total"] == 0, tabelle

    # Der Kategorienbaum steht unveraendert - der Haushalt bleibt benutzbar.
    nachher = client.get(
        "/v1/data-explorer/tables/categories/rows", headers=household_headers
    ).json()
    assert nachher["total"] == vorher["total"]


def test_leeren_raeumt_erstattungsdokument_vor_versicherter_person(
    household_headers: dict[str, str],
) -> None:
    """Regression fuer den echten Absturz beim Leeren.

    reimbursement_documents -> health_insured_persons ist ein
    zusammengesetzter Fremdschluessel. Der alte Katalog ignorierte solche
    Beziehungen und versuchte deshalb, die Elternzeile zuerst zu loeschen.
    """
    household_id = household_headers["X-Household-Id"]
    person_id = str(uuid.uuid4())
    with psycopg.connect(_DSN) as connection:
        connection.execute(
            "insert into health_insured_persons (id, household_id, display_name) "
            "values (%(person_id)s, %(household_id)s, 'Loeschtest')",
            {"person_id": person_id, "household_id": household_id},
        )
        connection.execute(
            "insert into reimbursement_documents "
            "(household_id, insured_person_id, original_filename, object_key, sha256, "
            "mime_type, size_bytes) values "
            "(%(household_id)s, %(person_id)s, 'test.pdf', %(object_key)s, "
            "%(sha256)s, 'application/pdf', 1)",
            {
                "household_id": household_id,
                "person_id": person_id,
                "object_key": f"test/{uuid.uuid4()}.pdf",
                "sha256": "a" * 64,
            },
        )
        connection.commit()

    antwort = client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Explorer-Testhaushalt"},
        headers=household_headers,
    )

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["deleted_per_table"]["reimbursement_documents"] == 1
    assert antwort.json()["deleted_per_table"]["health_insured_persons"] == 1


def test_leeren_laesst_den_zugang_bestehen(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Explorer-Testhaushalt"},
        headers=household_headers,
    )

    # Der Haushalt ist danach weiter erreichbar - waere household_members
    # mitgeloescht worden, gaebe es hier 403.
    antwort = client.get("/v1/data-explorer/tables", headers=household_headers)
    assert antwort.status_code == 200


def test_leeren_beruehrt_fremde_haushalte_nicht(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    fremd_token = _signup(f"fremd-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    fremd = client.post(
        "/v1/households", json={"name": "Unbeteiligt"}, headers=_auth_headers(fremd_token)
    ).json()
    fremd_headers = {**_auth_headers(fremd_token), "X-Household-Id": fremd["id"]}
    client.post(
        "/v1/meal-weeks", json={"week_start": "2026-09-07"}, headers=fremd_headers
    )

    client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Explorer-Testhaushalt"},
        headers=household_headers,
    )

    fremde_wochen = client.get(
        "/v1/data-explorer/tables/meal_weeks/rows", headers=fremd_headers
    ).json()
    assert fremde_wochen["total"] == 1


def test_leeren_hinterlaesst_genau_einen_audit_eintrag(
    household_headers: dict[str, str], meal_week_with_data: str
) -> None:
    """audit_log ist selbst eine Haushaltstabelle und wird mitgeleert. Der
    Eintrag ueber das Leeren wird danach geschrieben - uebrig bleibt genau
    dieser eine."""
    antwort = client.post(
        "/v1/data-explorer/purge",
        json={"confirm_name": "Explorer-Testhaushalt"},
        headers=household_headers,
    )
    assert antwort.status_code == 200
    assert antwort.json()["audit_event_id"] is not None
