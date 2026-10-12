"""Muss vor jedem Test-Modul-Import laufen (pytest laedt conftest.py vor der
Testsammlung): app.core.config.get_settings() und app.core.db.get_engine()
sind @lru_cache-dekoriert und "einfrieren" beim allerersten Aufruf im
Prozess - ohne zentrale Env-Vorgabe haette die Reihenfolge der Testdateien
Einfluss darauf, welche Settings global gelten.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
)

os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:55321")
os.environ.setdefault(
    "SUPABASE_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long"
)

# Muss ebenfalls hier auf Modulebene passieren, bevor irgendein Testmodul
# `app.main` importiert: `app.api.v1.routes.imports` importiert beim Laden
# `worker.actors`, und dessen `@dramatiq.actor`-Dekoratoren binden die Actors
# fest an den dann aktiven Broker (Dramatiq bindet beim Dekorieren, nicht
# dynamisch beim Senden). `worker.actors` ruft dafuer `setup_broker()`, das
# einen bereits gesetzten Broker unangetastet laesst - steht der StubBroker
# hier also frueh genug, binden sich die Actors an ihn; sonst wuerde
# `setup_broker()` den echten Redis-Broker konfigurieren und Upload-Tests
# braeuchten einen echten Redis- und Worker-Prozess. Der StubBroker haelt die
# Nachrichten stattdessen im Testprozess (Standard-Dramatiq-Testmuster);
# abgearbeitet werden sie kontrolliert in wait_for_import_processing() unten.
from queue import Empty  # noqa: E402
from typing import Any  # noqa: E402

import dramatiq  # noqa: E402
from dramatiq import Message  # noqa: E402
from dramatiq.brokers.stub import StubBroker  # noqa: E402
from periodiq import PeriodiqMiddleware  # noqa: E402

_stub_broker = StubBroker()
# `worker.actors.notify_due_recurring` ist mit periodic=cron(...) dekoriert -
# diese Actor-Option kennt nur die PeriodiqMiddleware, ohne sie lehnt Dramatiq
# den Dekorator mit "The following actor options are undefined" ab. Der
# Scheduler selbst laeuft hier nicht (das ist im Betrieb ein eigener
# periodiq-Prozess, siehe docker-compose.yml); die Middleware macht die Option
# lediglich bekannt.
_stub_broker.add_middleware(PeriodiqMiddleware(skip_delay=30))  # type: ignore[no-untyped-call]
_stub_broker.emit_after("process_boot")
dramatiq.set_broker(_stub_broker)


def _drain_stub_queue(actor: Any) -> None:
    """Arbeitet die Nachrichten GENAU EINES Actors ab und legt alle uebrigen
    unveraendert in die Queue zurueck.

    Bewusst KEIN mitlaufender dramatiq.Worker-Thread:

    1. Der Test kontrolliert, wann die Verarbeitung startet, und kann so den
       pending-Zustand vor dem Worker sowie den fertigen Zustand danach
       pruefen. Upload und Reparse committen vor dem Einreihen; der separate
       Regressionstest in test_imports_usability prueft den sofort startenden
       Worker, der frueher einen noch unsichtbaren Lauf verlieren konnte.
    2. Alle Actors teilen sich die 'default'-Queue. Ein Worker wuerde auch die
       Klassifikationslaeufe aus tests/test_ai_phase6.py starten, die echte
       LM-Studio-Aufrufe versuchen - deshalb wird hier gezielt nur ein
       Actor-Name abgearbeitet, der Rest bleibt liegen.

    Fehler im Actor schlagen direkt im Test durch, statt in Dead Letters zu
    verschwinden."""
    queue = _stub_broker.queues[actor.queue_name]
    untouched: list[bytes] = []
    while True:
        try:
            encoded = queue.get_nowait()
        except Empty:
            break
        message = Message.decode(encoded)
        if message.actor_name == actor.actor_name:
            actor(*message.args, **message.kwargs)
        else:
            untouched.append(encoded)
        queue.task_done()

    for encoded in untouched:
        queue.put(encoded)


def wait_for_import_processing() -> None:
    """Arbeitet die im Test-StubBroker eingereihten `process_import`-Nachrichten
    ab - synchron im Testthread, nach dem Ende des ausloesenden Requests."""
    from worker.actors import process_import

    _drain_stub_queue(process_import)


def wait_for_contract_reconcile() -> None:
    """Gegenstueck fuer den Vertragsabgleich nach einem Import-Commit.

    `POST /v1/imports/{id}/commit` reiht `reconcile_contracts` ein (siehe
    app/api/v1/routes/imports.py). Ohne diesen Aufruf bliebe die Nachricht im
    StubBroker liegen und ein Test pruefte einen Zustand, der nie eingetreten
    ist - statt zu scheitern, saehe er schlicht keine Faelligkeiten.

    Nur aus SYNCHRONEN Tests aufrufbar: der Actor faehrt `asyncio.run`, das in
    einer bereits laufenden Ereignisschleife wirft."""
    from worker.actors import reconcile_contracts

    _drain_stub_queue(reconcile_contracts)


# Gemeinsames Aufraeumen fuer API- und Worker-Suite, siehe
# packages/test-fixtures/dbcleanup.py: eine autouse-Fixture entfernt nach jedem
# Test die dabei entstandenen Haushalte und Anmeldungen. Ohne sie wuchs die
# Entwicklungsdatenbank ueber wenige Tage auf 22.000 Haushalte und 1 Million
# Kategorien an.
#
# Der Import steht bewusst am Dateiende: oben haelt die Reihenfolge der
# Anweisungen den Broker- und Settings-Aufbau zusammen, dazwischen wuerde er
# nur stoeren. Fuer die Fixture selbst spielt der Zeitpunkt keine Rolle -
# pytest sammelt sie aus dem fertigen Modulnamensraum ein, und die
# Datenbankverbindung entsteht erst beim ersten Test.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "test-fixtures"))

from dbcleanup import (  # noqa: E402, F401
    _cleanup_connection,
    entferne_testdaten,
    testlauf_exklusiv,
)
