"""Haelt die Aufraeumliste vollstaendig - gegen den Datenbankkatalog, nicht gegen
eine Aufzaehlung von Hand.

Buchungen werden archiviert statt geloescht. Zeigt eine Tabelle auf eine Buchung
und wird dieser Verweis beim Entfernen nicht geloest, behauptet sie hinterher
etwas ueber Geld, das im Bestand nicht mehr steht. Genau so sind fuenfzehn
Lesepfade entstanden, die falsche Summen ausgaben - gefunden wurden sie erst
lange danach, weil niemand die Liste der Verweise je vollstaendig hatte.

Dieser Test macht die Datenbank zur Wahrheitsquelle: er liest ALLE Spalten, die
auf `transactions` zeigen, und verlangt fuer jede eine ausdrueckliche
Entscheidung in transaction_detach_service.REFERENCE_DECISIONS. Wer eine neue
Tabelle mit Buchungsbezug anlegt, bekommt hier einen roten Test und muss sich
entscheiden - statt es erst zu merken, wenn eine Auswertung falsch rechnet.
"""

from sqlalchemy.sql import text

from app.core.db import get_session_factory

# Der Import setzt unter Windows die Event-Loop-Policy, ohne die psycopg keine
# asynchrone Verbindung aufbauen kann (app/main.py). Die uebrigen Testdateien
# ziehen ihn ueber `from app.main import app` mit herein; hier wird die App
# selbst nicht gebraucht.
from app.main import app as _app  # noqa: F401
from app.services.transaction_detach_service import REFERENCE_DECISIONS

# Erfasst beides: echte Fremdschluessel auf transactions(id) UND Spalten, die
# nur der Benennung nach auf eine Buchung zeigen. Der zweite Zweig ist kein
# Luxus - er faengt kuenftige Verweise ohne REFERENCES ab, wie sie im Bestand
# schon vorkommen.
#
# Der Fremdschluessel-Zweig geht ueber pg_catalog statt information_schema:
# mehrere Tabellen verweisen zusammengesetzt auf transactions(id, household_id),
# und information_schema liefert dafuer BEIDE Spalten, also auch household_id.
# Ueber conkey/confkey laesst sich dagegen genau die Spalte herausziehen, die
# auf transactions.id zeigt.
_REFERENZ_SPALTEN_SQL = """
select c.table_name, c.column_name
  from information_schema.columns c
 where c.table_schema = 'public'
   and c.column_name like '%%transaction_id'
   and c.table_name <> 'transactions'
union
select rel.relname as table_name, att.attname as column_name
  from pg_constraint con
  join pg_class rel on rel.oid = con.conrelid
  join pg_class frel on frel.oid = con.confrelid
  join pg_namespace ns on ns.oid = rel.relnamespace
  cross join lateral unnest(con.conkey, con.confkey) as paar(attnum, fattnum)
  join pg_attribute att
    on att.attrelid = rel.oid and att.attnum = paar.attnum
  join pg_attribute fatt
    on fatt.attrelid = frel.oid and fatt.attnum = paar.fattnum
 where con.contype = 'f'
   and ns.nspname = 'public'
   and frel.relname = 'transactions'
   and fatt.attname = 'id'
   and rel.relname <> 'transactions'
"""


async def test_every_reference_to_a_transaction_has_a_decision() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        spalten = {
            (row.table_name, row.column_name)
            for row in (await session.execute(text(_REFERENZ_SPALTEN_SQL))).all()
        }

    assert spalten, "Kein einziger Verweis gefunden - die Katalogabfrage ist kaputt."

    unentschieden = sorted(spalten - set(REFERENCE_DECISIONS))
    assert not unentschieden, (
        "Diese Spalten zeigen auf eine Buchung, ohne dass entschieden waere, was "
        "beim Entfernen der Buchung mit ihnen geschieht:\n  "
        + "\n  ".join(f"{tabelle}.{spalte}" for tabelle, spalte in unentschieden)
        + "\n\nTrage sie in transaction_detach_service.REFERENCE_DECISIONS ein - "
        "'geloest' (Verweis wird aufgeraeumt), 'gehoert' (Teil der Buchung) oder "
        "'herkunft' (Nachweis, dessen Lesepfade selbst filtern). Bei 'geloest' "
        "gehoert zusaetzlich eine Anweisung in detach_transactions dazu."
    )

    # Die Gegenrichtung: ein Eintrag fuer eine Spalte, die es nicht mehr gibt,
    # taeuscht eine Abdeckung vor, die ins Leere laeuft.
    verwaist = sorted(set(REFERENCE_DECISIONS) - spalten)
    assert not verwaist, (
        "Diese Eintraege in REFERENCE_DECISIONS haben keine Spalte mehr im Schema: "
        + ", ".join(f"{tabelle}.{spalte}" for tabelle, spalte in verwaist)
    )


# Tabellen, die zwar geloest werden, aber bewusst NICHT vor dem Zuruecknehmen
# eines Imports warnen. Begruendung je Eintrag - eine leere Ausnahmeliste waere
# ehrlicher, aber eine unbegruendete Ausnahme ist schlimmer als keine.
_KEINE_WARNUNG_NOETIG: dict[str, str] = {
    # Eine noch nicht uebernommene Staging-Zeile ist keine Arbeit des Nutzers,
    # sondern ein Befund des Importers. Sie zurueckzunehmen kostet nichts.
    "import_rows": "offene Staging-Zeile ist keine Nutzerarbeit",
}


def test_the_warning_list_covers_everything_that_gets_untangled() -> None:
    """Was beim Entfernen geloest wird, muss vorher gewarnt haben.

    Die Zusage des Zuruecknehmens lautet: ohne den ausdruecklichen Haken bleibt
    stehen, woran inzwischen etwas haengt. Sie gilt nur, solange die Warnliste
    jeden Fall kennt, den detach_transactions anfasst. Fehlte einer, waere die
    betroffene Buchung in der Vorschau unauffaellig, ginge ohne Rueckfrage mit -
    und die daran haengende Arbeit waere weg. Genau das war fuer die
    Versicherungszahlung eine Zeit lang der Fall.
    """
    from app.services.import_service import _REVERT_REASON_CHECKS

    gewarnte_tabellen = {tabelle for _grund, tabelle, _abfrage in _REVERT_REASON_CHECKS}
    ungewarnt = sorted(
        tabelle
        for (tabelle, _spalte), entscheidung in REFERENCE_DECISIONS.items()
        if entscheidung == "geloest"
        and tabelle not in _KEINE_WARNUNG_NOETIG
        and tabelle not in gewarnte_tabellen
    )
    assert not ungewarnt, (
        "Diese Tabellen werden beim Entfernen geloest, tauchen aber in der "
        "Warnliste _REVERT_REASON_CHECKS nicht auf - eine Buchung, an der nur "
        "so etwas haengt, gilt faelschlich als unverknuepft und geht ohne "
        "Rueckfrage mit:\n  " + "\n  ".join(ungewarnt)
    )


async def test_every_untangled_reference_is_actually_touched() -> None:
    """Jede als 'geloest' erklaerte Spalte muss in detach_transactions auch
    wirklich vorkommen - sonst steht die Zusage nur in der Tabelle."""
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "transaction_detach_service.py"
    ).read_text(encoding="utf-8")
    # Nur der ausfuehrende Teil, nicht die Entscheidungstabelle darueber.
    rumpf = quelle[quelle.index("async def detach_transactions(") :]

    fehlend = sorted(
        tabelle
        for (tabelle, _spalte), entscheidung in REFERENCE_DECISIONS.items()
        if entscheidung == "geloest" and tabelle not in rumpf
    )
    assert not fehlend, (
        "Als 'geloest' erklaert, aber in detach_transactions nicht angefasst: "
        + ", ".join(fehlend)
    )


def test_the_warning_query_skips_tables_this_database_does_not_have() -> None:
    """Eine Installation kann hinter dem Schema zurueckliegen.

    In einer aelteren Installation fehlten die Tabellen des Schadenmoduls
    vollstaendig. Ein fester
    Verweis darauf haette dort nicht bloss nichts gefunden, sondern jede Vorschau
    und jedes Zuruecknehmen mit einem Fehler abgebrochen - Postgres stolpert
    schon beim Planen ueber einen unbekannten Namen.
    """
    from app.services.import_service import _revert_candidates_sql

    ohne_schadenmodul = _revert_candidates_sql(
        {"transfer_links", "contract_occurrences", "transaction_splits"}
    )
    assert "insurance_claim_payments" not in ohne_schadenmodul
    assert "health_transaction_allocations" not in ohne_schadenmodul
    # Was es gibt, wird weiterhin geprueft ...
    assert "transfer_links" in ohne_schadenmodul
    # ... und der Fall, der ganz ohne Zusatztabelle auskommt, bleibt immer drin.
    assert "when k.version > 1 then 'bearbeitet'" in ohne_schadenmodul

    # Ohne jede Zusatztabelle bleibt eine gueltige Abfrage uebrig.
    nacktes_schema = _revert_candidates_sql(set())
    assert "case" in nacktes_schema and "else null" in nacktes_schema
