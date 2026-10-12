import logging
import threading
import time
from typing import Any

import dramatiq
import redis

from worker.config import Settings

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "worker:heartbeat"


class HeartbeatMiddleware(dramatiq.Middleware):
    """Schreibt periodisch einen Zeitstempel nach Redis, damit
    `GET /v1/system/health` in services/api erkennen kann, ob ein
    Worker-Prozess aktiv ist. Siehe Architekturplan Abschnitt 16.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop_event = threading.Event()

    def after_worker_boot(self, broker: dramatiq.Broker, worker: Any) -> None:
        thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        thread.start()

    def before_worker_shutdown(self, broker: dramatiq.Broker, worker: Any) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        client = redis.Redis.from_url(self._settings.redis_url)
        interval = self._settings.worker_heartbeat_interval_seconds
        # Verfallszeit statt unbegrenztem Schluessel: ohne sie ueberlebt der
        # letzte Zeitstempel den Worker beliebig lange (gemessen: 4,8 Stunden
        # nach dem Ende des Prozesses lag er unveraendert in Redis, TTL -1).
        # Der Schluessel beweist damit nur, dass IRGENDWANN ein Worker lief -
        # genau die Aussage, die niemanden interessiert. Mit dem Dreifachen des
        # Intervalls ueberlebt er zwei ausgefallene Schreibversuche und
        # verschwindet danach, sodass sein blosses Fehlen "kein Worker" heisst.
        while not self._stop_event.is_set():
            try:
                client.set(HEARTBEAT_KEY, time.time(), ex=max(interval * 3, 1))
            except Exception:
                logger.exception("Heartbeat konnte nicht nach Redis geschrieben werden")
            self._stop_event.wait(interval)
