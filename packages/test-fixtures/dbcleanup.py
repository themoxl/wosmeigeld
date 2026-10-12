"""Automatisches Aufraeumen der Testdaten nach jedem Test.

Warum das hier zentral steht und nicht in jeder Fixture einzeln:

Die Testsuiten legen ihre Haushalte ueber `POST /v1/households` bzw. direkt
per SQL an - verteilt auf ueber 50 Dateien und rund 80 Fixtures, alle mit
`return` statt `yield` und damit ohne Abbau. Jeder Testlauf hinterliess so
dauerhaft einen Haushalt samt Anmeldung und allem, was der Test darin
anlegte. Gemessen am 05.08.2026: 22.109 Haushalte, 22.536 Anmeldungen,
1.058.734 Kategorien, 997 MB Datenbank - bei 649 echten Buchungen.

Statt 80 Fixtures anzufassen (und jede kuenftige neu daran zu erinnern),
haengen hier zwei `autouse`-Sicherungen: nach JEDEM Test wird dessen Differenz
entfernt, und am Ende des GESAMTEN Testlaufs noch einmal die Laufdifferenz.
Die zweite Ebene ist fuer module-/session-scoped Fixtures noetig, die bereits
vor dem ersten function-scoped Cleanup erzeugt werden.

Das Verfahren ist bewusst der Ausschnittvergleich und kein Zeitstempel:
vor dem Test werden die vorhandenen IDs gemerkt, nach dem Test wird die
Differenz geloescht. Damit kann per Konstruktion nichts geloescht werden, was
vor dem Test schon da war - der echte Haushalt ist dadurch unberuehrbar, ohne
dass er irgendwo namentlich ausgenommen werden muss.

`households` und `auth.users` reichen als Angriffspunkte: alle fachlichen
Tabellen haengen ueber `household_id` mit `on delete cascade` daran (geprueft
gegen supabase/migrations/0001_schema.sql, kein einziger Fremdschluessel ohne
Abraeumregel).
"""

import os
from collections.abc import Iterator

import psycopg
import pytest

# Die Testumgebungen setzen DATABASE_URL im SQLAlchemy-Dialekt; psycopg
# selbst kennt das '+psycopg' nicht.
_DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"

# Kennung der Datenbanksperre, die Testlaeufe serialisiert (siehe
# testlauf_exklusiv). Frei gewaehlte Konstante - sie muss nur in API- und
# Worker-Suite dieselbe sein, damit sich beide gegenseitig sehen.
_TESTLAUF_SPERRE = 815_243_001


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _ids(connection: psycopg.Connection, table: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(f"select id from {table}")  # noqa: S608 - feste Tabellennamen
        return {str(row[0]) for row in cur.fetchall()}


def _delete(connection: psycopg.Connection, table: str, ids: set[str]) -> None:
    if not ids:
        return
    with connection.cursor() as cur:
        cur.execute(f"delete from {table} where id = any(%s)", (list(ids),))  # noqa: S608


@pytest.fixture(scope="session")
def _cleanup_connection() -> Iterator[psycopg.Connection | None]:
    """Eigene, synchrone Verbindung - bewusst getrennt von der asynchronen
    Engine der Anwendung, damit das Aufraeumen keine Transaktion des Tests
    beeinflusst."""
    try:
        connection = psycopg.connect(_dsn(), autocommit=True)
    except psycopg.Error:
        # Ohne erreichbare Datenbank laufen ohnehin nur die reinen
        # Logiktests - die legen nichts an, das aufzuraeumen waere.
        yield None
        return
    with connection:
        yield connection


@pytest.fixture(scope="session", autouse=True)
def testlauf_exklusiv(_cleanup_connection: psycopg.Connection | None) -> Iterator[None]:
    """Laesst nur EINEN Testlauf gleichzeitig auf diese Datenbank.

    Ohne das zerstoeren sich zwei parallel laufende Suiten gegenseitig: das
    Aufraeumen unten erkennt "waehrend des Tests entstanden" am Vergleich
    vorher/nachher - und ein zweiter Prozess, der zeitgleich seine eigenen
    Haushalte anlegt, faellt genau in dieses Fenster. Gemessen: API- und
    Worker-Suite gleichzeitig gestartet ergaben 8 bzw. 2 Fehlschlaege, jede
    fuer sich lief gruen durch.

    Die Sperre haengt an der Verbindung; bricht der Lauf ab, gibt PostgreSQL
    sie beim Verbindungsabbau von selbst frei."""
    if _cleanup_connection is None:
        yield
        return

    with _cleanup_connection.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(%s)", (_TESTLAUF_SPERRE,))
        lock_row = cur.fetchone()
        if lock_row is None or not bool(lock_row[0]):
            # Sichtbar nur mit 'pytest -s' - die Ausgabefanger stehen zu
            # diesem Zeitpunkt schon. Ohne die Meldung sieht ein wartender
            # Lauf einfach nach einem langsamen Start aus, deshalb steht sie
            # trotzdem hier.
            print(
                "\n[dbcleanup] Ein anderer Testlauf arbeitet gerade auf dieser "
                "Datenbank - warte, bis er fertig ist ..."
            )
            cur.execute("select pg_advisory_lock(%s)", (_TESTLAUF_SPERRE,))

    # Der Funktions-Cleanup unten sieht nur Daten aus function-scoped
    # Fixtures. Module-/Session-Fixtures werden von pytest bereits VOR ihm
    # aufgebaut und landeten deshalb bisher dauerhaft in der Datenbank. Der
    # zweite Ausschnitt ueber den kompletten Lauf schliesst genau dieses Loch
    # und ist zugleich der Fallback fuer xdist, wo der Funktions-Cleanup
    # bewusst deaktiviert ist.
    haushalte_vor_lauf = _ids(_cleanup_connection, "households")
    nutzer_vor_lauf = _ids(_cleanup_connection, "auth.users")
    try:
        yield
    finally:
        try:
            _delete(
                _cleanup_connection,
                "households",
                _ids(_cleanup_connection, "households") - haushalte_vor_lauf,
            )
            _delete(
                _cleanup_connection,
                "auth.users",
                _ids(_cleanup_connection, "auth.users") - nutzer_vor_lauf,
            )
        finally:
            with _cleanup_connection.cursor() as cur:
                cur.execute("select pg_advisory_unlock(%s)", (_TESTLAUF_SPERRE,))


@pytest.fixture(autouse=True)
def entferne_testdaten(
    _cleanup_connection: psycopg.Connection | None,
    testlauf_exklusiv: None,
) -> Iterator[None]:
    """Loescht nach jedem Test die dabei entstandenen Haushalte und Anmeldungen."""
    if _cleanup_connection is None or os.environ.get("PYTEST_XDIST_WORKER"):
        # Unter xdist teilen sich mehrere Prozesse EINE Sperre (sie stammen aus
        # demselben Lauf), koennen sich aber genauso gegenseitig die Haushalte
        # wegnehmen. Dann lieber nicht aufraeumen als fremde Tests zerstoeren.
        yield
        return

    haushalte_vorher = _ids(_cleanup_connection, "households")
    nutzer_vorher = _ids(_cleanup_connection, "auth.users")
    try:
        yield
    finally:
        # Haushalte zuerst: sie raeumen ueber die Fremdschluessel auch die
        # Mitgliedschaften ab, die sonst am Loeschen der Anmeldung haengen.
        _delete(
            _cleanup_connection,
            "households",
            _ids(_cleanup_connection, "households") - haushalte_vorher,
        )
        _delete(
            _cleanup_connection,
            "auth.users",
            _ids(_cleanup_connection, "auth.users") - nutzer_vorher,
        )
