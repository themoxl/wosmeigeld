import dramatiq
from dramatiq import broker as dramatiq_broker
from dramatiq.brokers.redis import RedisBroker
from periodiq import PeriodiqMiddleware

from worker.config import get_settings
from worker.heartbeat import HeartbeatMiddleware
from worker.logging_config import configure_logging
from worker.tracing import configure_tracing

# Dieses Modul hat bewusst KEINE Seiteneffekte beim Import: weder Logging noch
# Tracing noch der globale Dramatiq-Broker werden angefasst, solange niemand
# setup_broker() ruft. Grund: services/api importiert ueber die Routen
# transitiv worker.actors, und ein Testprozess, der vorher einen StubBroker
# gesetzt hat, darf davon nicht ueberschrieben werden (siehe
# services/api/tests/conftest.py).


def build_broker() -> RedisBroker:
    """Baut den Redis-Broker inklusive Middleware und richtet Logging und
    Tracing des Prozesses ein. Setzt den Broker NICHT global - das macht
    ausschliesslich setup_broker()."""
    # Muss vor jedem Actor-Import/-Aufruf laufen (siehe worker/actors.py, das
    # setup_broker() als erstes ruft) - sonst liefe der Worker-Prozess mit
    # Dramatiqs Klartext-Standardausgabe statt strukturierten JSON-Logs.
    configure_logging()

    settings = get_settings()
    configure_tracing(settings)

    broker = RedisBroker(url=settings.redis_url)  # type: ignore[no-untyped-call]
    broker.add_middleware(HeartbeatMiddleware(settings))  # type: ignore[no-untyped-call]
    # Erlaubt periodische Actors (siehe worker/actors.py, @dramatiq.actor(periodic=cron(...)))
    # ueber den bestehenden Redis-Broker, ohne neue Infrastruktur - der eigentliche
    # Scheduler laeuft als separater Prozess (`periodiq -v worker.actors`, siehe
    # docker-compose.yml), skip_delay verwirft Nachrichten, die aus irgendeinem
    # Grund (z. B. Neustart) mehr als 30s zu spaet verarbeitet wuerden.
    broker.add_middleware(PeriodiqMiddleware(skip_delay=30))  # type: ignore[no-untyped-call]
    return broker


def setup_broker() -> dramatiq.Broker:
    """Konfiguriert den globalen Dramatiq-Broker fuer diesen Prozess und gibt
    ihn zurueck.

    Aufrufer ist der jeweilige Prozess-Entrypoint, bevor die Actors importiert
    werden: der Worker-/Scheduler-Prozess ueber `dramatiq worker.actors` bzw.
    `periodiq -v worker.actors` (siehe services/worker/Dockerfile und
    docker-compose.yml) und der API-Prozess, der die Actors nur einreiht - beide
    kommen ueber worker/actors.py hier an.

    Hat der Prozess bereits einen Broker gesetzt, bleibt dieser aktiv und es
    wird KEINE Redis-Verbindung konfiguriert. Genau davon lebt der Testbetrieb:
    services/api/tests/conftest.py setzt vor dem ersten Import von app.main
    einen StubBroker, an den sich die Actors dann binden (Dramatiq bindet
    Actors beim Dekorieren fest an den zu dem Zeitpunkt aktiven Broker, nicht
    dynamisch beim Senden)."""
    existing = dramatiq_broker.global_broker
    if existing is not None:
        return existing

    broker = build_broker()
    dramatiq.set_broker(broker)
    return broker
