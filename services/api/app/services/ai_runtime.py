"""Laufzeitsteuerung von LM Studio ueber dessen `/api/v1`-Schnittstelle.

Warum ein eigenes Modul neben ai_discovery.py: Discovery liest, dieses Modul
schreibt. `ai_discovery` beantwortet "welche Modelle gibt es und welches ist
geladen" ueber `/api/v0/models` bzw. die OpenAI-kompatible Liste. Hier geht es
um den Zustand *aendern* - Modell mit einem bestimmten Kontextfenster laden,
alte Instanz entladen. Das ist ein anderes Fehlerbild (dauert Minuten, kann am
Speicher scheitern) und ein anderer Vertrag.

Gemessener Vertrag (LM Studio, 03.08.2026):

    GET  /api/v1/models
        -> {"models": [{"key", "max_context_length", "type",
                        "loaded_instances": [{"id", "config": {"context_length"}}],
                        "capabilities": {"reasoning": {"allowed_options": [...]}}}]}
    POST /api/v1/models/load    {"model": "<key>", "context_length": <int>}
        -> {"instance_id", "load_time_seconds", "status"}
    POST /api/v1/models/unload  {"instance_id": "<id>"}

Zwei Eigenheiten, die den Ablauf hier bestimmen:

  * Ein zweiter Ladevorgang legt eine ZWEITE Instanz an ("<key>:2") statt die
    bestehende umzukonfigurieren. Ohne vorheriges Entladen belegt der Rechner
    dasselbe Modell doppelt im Speicher - deshalb entlaedt `ensure_loaded`
    zuerst und laedt dann.
  * `ttl` wird nicht akzeptiert (HTTP 400 "Unrecognized key(s)"). Geladene
    Modelle bleiben also geladen, bis LM Studio selbst aufraeumt.
"""

import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

# Laden heisst: Gewichte von der Platte in den Speicher. Gemessen 38,7 s fuer
# ein 15-GB-Modell mit 32k Fenster; ein grosses Modell mit grossem Fenster
# darf deutlich laenger brauchen. Zu knapp bemessen waere schlimmer als zu
# grosszuegig - ein Abbruch mitten im Laden laesst LM Studio in einem
# Zwischenzustand zurueck.
LOAD_TIMEOUT_SECONDS: Final = 600.0
# Lesen und Entladen sind schnell.
QUERY_TIMEOUT_SECONDS: Final = 15.0
UNLOAD_TIMEOUT_SECONDS: Final = 120.0


class LMStudioRuntimeError(RuntimeError):
    """LM Studio hat die Laufzeitaenderung abgelehnt oder war nicht erreichbar.

    Traegt eine Meldung, die der Oberflaeche direkt vorgelegt werden kann -
    "nicht genug Speicher fuer 262144 Tokens" ist eine Antwort, mit der der
    Nutzer etwas anfangen kann, ein Stacktrace nicht."""


@dataclass(frozen=True)
class LoadedInstance:
    instance_id: str
    context_length: int | None


@dataclass(frozen=True)
class RuntimeModel:
    """Ein Modell aus der v1-Liste - reicher als AIModelInfo aus /api/v0."""

    key: str
    kind: str
    max_context_length: int | None
    instances: list[LoadedInstance]
    reasoning_options: list[str]
    # LM Studio meldet neben den groben Optionen auch, was ohne
    # reasoning_effort passiert. Das ist fuer "Modellstandard" entscheidend:
    # beim Gemma ist der Standard "on", also gerade nicht neutral.
    reasoning_default: str | None = None
    # Beschreibende Felder derselben Antwort. Sie beantworten die Frage
    # "womit wird gerade gerechnet" (Groesse, Quantisierung, Architektur) und
    # bleiben None, wenn die LM-Studio-Version sie nicht liefert - geraten
    # wird hier nichts.
    display_name: str | None = None
    architecture: str | None = None
    quantization: str | None = None
    parameters: str | None = None
    size_bytes: int | None = None

    @property
    def is_loaded(self) -> bool:
        return bool(self.instances)

    def loaded_context_length(self) -> int | None:
        """Fenster der ersten geladenen Instanz."""
        return self.instances[0].context_length if self.instances else None

    def loaded_window_tokens(self) -> int | None:
        """Geladenes Fenster nach der Regel des KI-Kerns (app/llm/window.py,
        Leser /api/v1/models): die Instanz, die den Modellschluessel als
        Kennung traegt, sonst die kleinste aller Instanzen - welche LM Studio
        ohne ausdrueckliche Adresse waehlt, ist nicht festgelegt."""
        addressed = [item for item in self.instances if item.instance_id == self.key]
        known = [
            item.context_length
            for item in (addressed or self.instances)
            if item.context_length is not None and item.context_length > 0
        ]
        return min(known) if known else None


def _root_of(base_url: str) -> str:
    """`http://host:1234/v1` -> `http://host:1234`.

    Die Verwaltungsschnittstelle liegt neben der OpenAI-kompatiblen, nicht
    darunter."""
    return base_url.rstrip("/").removesuffix("/v1").rstrip("/")


def parse_models(payload: dict[str, Any]) -> list[RuntimeModel]:
    """Antwort von GET /api/v1/models -> Modelle. Unbekannte Felder werden
    ignoriert, fehlende bleiben None statt geraten zu werden."""
    models: list[RuntimeModel] = []
    for entry in payload.get("models", []):
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        instances = [
            LoadedInstance(
                instance_id=str(item.get("id") or ""),
                context_length=_as_int((item.get("config") or {}).get("context_length")),
            )
            for item in (entry.get("loaded_instances") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        reasoning_capability = (entry.get("capabilities") or {}).get("reasoning") or {}
        reasoning = reasoning_capability.get("allowed_options")
        quantization = entry.get("quantization")
        if isinstance(quantization, dict):
            quantization = quantization.get("name")
        models.append(
            RuntimeModel(
                key=str(entry["key"]),
                kind=str(entry.get("type") or "unknown"),
                max_context_length=_as_int(entry.get("max_context_length")),
                instances=instances,
                reasoning_options=[str(option) for option in reasoning or []],
                reasoning_default=_as_text(reasoning_capability.get("default")),
                display_name=_as_text(entry.get("display_name")),
                architecture=_as_text(entry.get("architecture")),
                quantization=_as_text(quantization),
                parameters=_as_text(entry.get("params_string")),
                size_bytes=_as_int(entry.get("size_bytes")),
            )
        )
    return models


def _as_text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


async def list_models(base_url: str) -> list[RuntimeModel]:
    """Modelle samt Ladezustand. Leere Liste, wenn der Server die
    v1-Schnittstelle nicht anbietet - das ist kein Fehler, sondern eine
    aeltere LM-Studio-Version."""
    root = _root_of(base_url)
    try:
        async with httpx.AsyncClient(timeout=QUERY_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{root}/api/v1/models")
            if response.status_code != 200:
                return []
            return parse_models(response.json())
    except Exception:  # noqa: BLE001 - fehlende v1-API ist ein Normalfall
        logger.debug("v1-Modellliste nicht verfuegbar (%s)", root, exc_info=True)
        return []


def _error_message(response: httpx.Response) -> str:
    """LM Studios Fehlerform ist uneinheitlich: mal {"error": "text"}, mal
    {"error": {"message": ...}}. Beides auf einen Satz bringen.

    Die Kuerzungen betreffen nur die Anzeige einer Serverantwort (Laden und
    Entladen von Modellen), nie Material fuer das Modell."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"  # fest: Anzeige
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]  # fest: Anzeige einer Servermeldung
    if isinstance(error, str):
        return error[:300]  # fest: Anzeige einer Servermeldung
    return f"HTTP {response.status_code}"


async def unload_instance(base_url: str, instance_id: str) -> None:
    root = _root_of(base_url)
    async with httpx.AsyncClient(timeout=UNLOAD_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                f"{root}/api/v1/models/unload", json={"instance_id": instance_id}
            )
        except httpx.HTTPError as exc:
            raise LMStudioRuntimeError(
                f"LM Studio nicht erreichbar beim Entladen von „{instance_id}“: {exc}"
            ) from exc
    # 404 heisst: schon weg. Das ist der gewuenschte Endzustand, kein Fehler.
    if response.status_code not in (200, 404):
        raise LMStudioRuntimeError(
            f"„{instance_id}“ liess sich nicht entladen: {_error_message(response)}"
        )


async def load_model(
    base_url: str, model_key: str, context_length: int | None
) -> tuple[str, float]:
    """Laedt `model_key`, optional mit vorgegebenem Kontextfenster.

    Gibt Instanz-ID und Ladedauer zurueck."""
    root = _root_of(base_url)
    body: dict[str, Any] = {"model": model_key}
    if context_length:
        body["context_length"] = context_length
    async with httpx.AsyncClient(timeout=LOAD_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(f"{root}/api/v1/models/load", json=body)
        except httpx.TimeoutException as exc:
            raise LMStudioRuntimeError(
                f"Das Laden von „{model_key}“ hat laenger als "
                f"{LOAD_TIMEOUT_SECONDS:.0f} Sekunden gedauert. LM Studio laedt "
                "unter Umstaenden weiter - der Status zeigt, sobald es fertig ist."
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioRuntimeError(f"LM Studio nicht erreichbar: {exc}") from exc

    if response.status_code != 200:
        raise LMStudioRuntimeError(
            f"„{model_key}“ liess sich nicht laden: {_error_message(response)}"
        )
    payload = response.json()
    return str(payload.get("instance_id") or model_key), float(
        payload.get("load_time_seconds") or 0.0
    )


@dataclass(frozen=True)
class EnsureResult:
    """Was `ensure_loaded` getan hat - die Oberflaeche soll den Unterschied
    zwischen "war schon richtig" und "wurde neu geladen" benennen koennen."""

    changed: bool
    instance_id: str | None
    context_length: int | None
    load_time_seconds: float
    message: str


async def ensure_loaded(
    base_url: str, model_key: str, context_length: int | None
) -> EnsureResult:
    """Sorgt dafuer, dass `model_key` mit `context_length` geladen ist.

    Laeuft es bereits mit genau diesem Fenster, passiert nichts - Laden kostet
    hier eine halbe Minute und mehr, das darf kein Nebeneffekt eines
    Speichern-Klicks sein. Sonst: alle Instanzen dieses Modells entladen (LM
    Studio wuerde sonst eine zweite anlegen) und neu laden."""
    models = await list_models(base_url)
    if not models:
        raise LMStudioRuntimeError(
            "Diese LM-Studio-Version bietet die Verwaltungsschnittstelle "
            "(/api/v1/models) nicht an. Modell und Kontextfenster muessen dann "
            "in LM Studio selbst eingestellt werden."
        )

    model = next((item for item in models if item.key == model_key), None)
    if model is None:
        raise LMStudioRuntimeError(
            f"„{model_key}“ ist in LM Studio nicht vorhanden. "
            f"Verfuegbar: {', '.join(item.key for item in models) or 'keine Modelle'}."
        )
    if model.max_context_length and context_length and context_length > model.max_context_length:
        raise LMStudioRuntimeError(
            f"„{model_key}“ unterstuetzt hoechstens {model.max_context_length} Tokens "
            f"Kontext, angefordert sind {context_length}."
        )

    if model.is_loaded and (
        context_length is None or model.loaded_context_length() == context_length
    ):
        return EnsureResult(
            changed=False,
            instance_id=model.instances[0].instance_id,
            context_length=model.loaded_context_length(),
            load_time_seconds=0.0,
            message=(
                f"„{model_key}“ ist bereits mit "
                f"{model.loaded_context_length() or 'unbekanntem'} Tokens Kontext geladen."
            ),
        )

    for instance in model.instances:
        await unload_instance(base_url, instance.instance_id)

    instance_id, load_time = await load_model(base_url, model_key, context_length)

    # Nachlesen statt annehmen: was wirklich geladen ist, weiss nur LM Studio.
    # Ein "geladen mit 200000" in der Oberflaeche, waehrend real 32768 laufen,
    # waere schlimmer als gar keine Anzeige.
    reloaded = next(
        (item for item in await list_models(base_url) if item.key == model_key), None
    )
    effective = reloaded.loaded_context_length() if reloaded else context_length
    return EnsureResult(
        changed=True,
        instance_id=instance_id,
        context_length=effective,
        load_time_seconds=load_time,
        message=(
            f"„{model_key}“ neu geladen mit {effective or 'unbekannt'} Tokens Kontext "
            f"({load_time:.1f} s)."
        ),
    )
