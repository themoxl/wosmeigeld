"""Welches Kontextfenster hat das Modell WIRKLICH geladen?

Das ist die Groesse, an der jedes Antwortbudget haengt (W). Gelesen wird nur,
was der Server ueber das GELADENE Fenster meldet - nie die moegliche
Hoechstlaenge (`max_context_length`): LM Studio laedt ein 262k-Modell
haeufig mit 8192 Token, und wer mit dem Maximum rechnet, stellt Anfragen, die
der Server mit "Context size has been exceeded" ablehnt. max_context_length
steht nur zur Information in WindowInfo.max_context.

Leser in fester Reihenfolge (Port von sia_public/server/jet_server.py,
_ki_lesen_*), es gilt der erste, der ein Fenster nennt:

  1. LM Studio  GET {wurzel}/api/v1/models
                loaded_instances[].config.context_length der adressierten
                Instanz, dazu config.parallel und capabilities.reasoning
  2. LM Studio  GET {wurzel}/api/v0/models
                loaded_context_length - NUR wenn state == "loaded"
  3. Ollama     GET {wurzel}/api/ps  (context_length des laufenden Modells)
  4. OpenAI-kompatibel  GET {basis}/models
                max_model_len (vLLM) / context_length / context_window

Danach: W = min(geladenes Fenster, "Kontext hoechstens"). Ist kein Fenster
gemeldet, aber der Deckel gesetzt, gilt der Deckel (Quelle sagt das). Ohne
beides bleibt W unbekannt - dann findet kein Modellaufruf statt
(model_context_unknown), und `reason` sagt, warum.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import httpx

from app.core.config import get_settings

ReasoningKind = Literal["switchable", "always", "none", "unknown"]

SOURCE_LMSTUDIO_V1 = "/api/v1/models"
SOURCE_LMSTUDIO_V0 = "/api/v0/models"
SOURCE_OLLAMA = "Ollama /api/ps"
SOURCE_CAP = "Kontext höchstens (Einstellung)"
SOURCE_ANTHROPIC = "Anthropic /v1/models (max_input_tokens)"


@dataclass(frozen=True)
class ReasoningCapability:
    """Was das Modell zum Denken (Reasoning) meldet.

    kind:
      switchable  laesst sich ein- und ausschalten (allowed_options enthaelt
                  off/none)
      always      denkt immer (Optionen ohne off/none)
      none        LM Studio /api/v1 meldet keine Denkfaehigkeit
      unknown     der Server meldet dazu nichts (aeltere API, Ollama, vLLM)"""

    kind: ReasoningKind = "unknown"
    allowed_options: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class WindowInfo:
    loaded: int | None
    cap: int | None
    effective: int | None
    source: str | None
    reason: str | None = None
    parallel: int | None = None
    reasoning_capability: ReasoningCapability = field(default_factory=ReasoningCapability)
    # Nur zur Information - nie als Fenster verwenden.
    max_context: int | None = None
    # None = der Server sagt nichts dazu.
    model_loaded: bool | None = None
    # Hoechste Antwortlaenge, die das Modell schreiben darf (Anthropic
    # /v1/models max_tokens). Das Antwortbudget wird darauf gedeckelt - ohne
    # sie verlangte ein 1-Mio.-Fenster mehr Antwort, als das Modell darf.
    # None = der Server nennt keine (lokale Server).
    output_limit: int | None = None

    @property
    def known(self) -> bool:
        return self.effective is not None and self.effective > 0

    def as_dict(self) -> dict[str, Any]:
        """Form wie RunLog.window im Vertrag des KI-Felds."""
        return {
            "loaded": self.loaded,
            "cap": self.cap,
            "effective": self.effective,
            "source": self.source,
        }


@dataclass(frozen=True)
class _Reading:
    """Was ein einzelner Leser herausgefunden hat."""

    answered: bool
    loaded: int | None = None
    source: str | None = None
    reason: str | None = None
    parallel: int | None = None
    reasoning: ReasoningCapability | None = None
    max_context: int | None = None
    model_loaded: bool | None = None
    # True: der Leser ist fuer dieses Modell massgeblich, weitere Leser
    # koennen nichts Richtigeres sagen (LM Studio v1 kennt das Modell).
    authoritative: bool = False


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value > 0 else None


def root_of(base_url: str) -> str:
    """`http://host:1234/v1` -> `http://host:1234` (Verwaltungs-APIs liegen
    neben, nicht unter der OpenAI-kompatiblen Basis)."""
    return base_url.rstrip("/").removesuffix("/v1").rstrip("/")


def reasoning_capability_of(raw: Any) -> ReasoningCapability:
    """capabilities.reasoning aus /api/v1/models -> ReasoningCapability.

    None/fehlend heisst bei v1: das Modell denkt nicht ("none"). Eine
    unbekannte Form ist "unknown" - lieber nichts behaupten."""
    if raw is None or raw is False:
        return ReasoningCapability(kind="none")
    if raw is True:
        return ReasoningCapability(kind="unknown")
    if not isinstance(raw, dict):
        return ReasoningCapability(kind="unknown")
    options_raw = raw.get("allowed_options")
    options = (
        tuple(str(option) for option in options_raw) if isinstance(options_raw, list) else ()
    )
    default = raw.get("default")
    default_text = str(default) if default is not None else None
    if not options:
        return ReasoningCapability(kind="unknown", default=default_text)
    can_switch_off = any(option.lower() in ("off", "none", "false") for option in options)
    return ReasoningCapability(
        kind="switchable" if can_switch_off else "always",
        allowed_options=options,
        default=default_text,
    )


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[int | None, Any, str | None]:
    """(Status, JSON, Fehlertext) - nie eine Ausnahme."""
    try:
        response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - jeder Leser darf scheitern
        return None, None, f"{type(exc).__name__}: {exc}"
    status = response.status_code
    if status != 200:
        return status, None, f"HTTP {status}"
    try:
        return status, response.json(), None
    except Exception as exc:  # noqa: BLE001
        return status, None, f"keine JSON-Antwort ({type(exc).__name__})"


async def _read_lmstudio_v1(client: httpx.AsyncClient, root: str, model: str) -> _Reading:
    _status, payload, error = await _get_json(client, f"{root}/api/v1/models")
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return _Reading(
            answered=False, reason=f"{SOURCE_LMSTUDIO_V1}: {error or 'keine Modellliste'}"
        )
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        instances = [item for item in entry.get("loaded_instances") or [] if isinstance(item, dict)]
        addressed = [item for item in instances if str(item.get("id") or "") == model]
        if str(entry.get("key") or "") != model and not addressed:
            continue
        capability = reasoning_capability_of((entry.get("capabilities") or {}).get("reasoning"))
        max_context = _positive_int(entry.get("max_context_length"))
        chosen = addressed or instances
        windows = [
            _positive_int((item.get("config") or {}).get("context_length")) for item in chosen
        ]
        known = [value for value in windows if value is not None]
        parallels = [
            _positive_int((item.get("config") or {}).get("parallel")) for item in chosen
        ]
        parallel = next((value for value in parallels if value is not None), None)
        if not instances:
            return _Reading(
                answered=True,
                source=SOURCE_LMSTUDIO_V1,
                reason=(
                    f"LM Studio hat „{model}“ gerade nicht geladen - einmal laden "
                    "(Einstellungen › KI › Modell laden) oder „Kontext höchstens“ eintragen."
                ),
                reasoning=capability,
                max_context=max_context,
                model_loaded=False,
                authoritative=True,
            )
        if not known:
            return _Reading(
                answered=True,
                source=SOURCE_LMSTUDIO_V1,
                reason=f"LM Studio meldet für „{model}“ kein geladenes Kontextfenster.",
                reasoning=capability,
                max_context=max_context,
                model_loaded=True,
                parallel=parallel,
            )
        # Mehrere Instanzen desselben Modells ohne ausdrueckliche Adresse:
        # welche LM Studio waehlt, ist nicht festgelegt - also die kleinste.
        return _Reading(
            answered=True,
            loaded=min(known),
            source=SOURCE_LMSTUDIO_V1,
            reason=(
                f"{len(known)} geladene Instanzen - es gilt das kleinste Fenster."
                if len(known) > 1
                else None
            ),
            parallel=parallel,
            reasoning=capability,
            max_context=max_context,
            model_loaded=True,
            authoritative=True,
        )
    return _Reading(
        answered=True,
        reason=f"{SOURCE_LMSTUDIO_V1}: „{model}“ ist dort nicht aufgeführt.",
    )


async def _read_lmstudio_v0(client: httpx.AsyncClient, root: str, model: str) -> _Reading:
    _status, payload, error = await _get_json(client, f"{root}/api/v0/models")
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return _Reading(
            answered=False, reason=f"{SOURCE_LMSTUDIO_V0}: {error or 'keine Modellliste'}"
        )
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("id") or "") != model:
            continue
        max_context = _positive_int(entry.get("max_context_length"))
        state = entry.get("state")
        if state != "loaded":
            return _Reading(
                answered=True,
                source=SOURCE_LMSTUDIO_V0,
                reason=(
                    f"LM Studio hat „{model}“ gerade nicht geladen (state={state or 'unbekannt'})"
                    " - einmal laden oder „Kontext höchstens“ eintragen."
                ),
                max_context=max_context,
                model_loaded=False if state == "not-loaded" else None,
                # "not-loaded" ist eine klare Aussage von LM Studio; die
                # uebrigen Leser koennen kein Fenster eines nicht geladenen
                # Modells kennen.
                authoritative=state == "not-loaded",
            )
        loaded = _positive_int(entry.get("loaded_context_length"))
        return _Reading(
            answered=True,
            loaded=loaded,
            source=SOURCE_LMSTUDIO_V0,
            reason=None if loaded else f"{SOURCE_LMSTUDIO_V0}: kein loaded_context_length.",
            max_context=max_context,
            model_loaded=True,
        )
    return _Reading(
        answered=True, reason=f"{SOURCE_LMSTUDIO_V0}: „{model}“ ist dort nicht aufgeführt."
    )


async def _read_ollama(client: httpx.AsyncClient, root: str, model: str) -> _Reading:
    _status, payload, error = await _get_json(client, f"{root}/api/ps")
    running = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(running, list):
        return _Reading(answered=False, reason=f"{SOURCE_OLLAMA}: {error or 'keine Modellliste'}")
    for entry in running:
        if not isinstance(entry, dict):
            continue
        if model not in (entry.get("name"), entry.get("model")):
            continue
        loaded = _positive_int(entry.get("context_length"))
        return _Reading(
            answered=True,
            loaded=loaded,
            source=SOURCE_OLLAMA,
            reason=(
                None
                if loaded
                else "Diese Ollama-Fassung meldet kein Kontextfenster - unter "
                "„Kontext höchstens“ eintragen."
            ),
            model_loaded=True,
        )
    return _Reading(
        answered=True,
        source=SOURCE_OLLAMA,
        reason=(
            f"Ollama hat „{model}“ gerade nicht geladen - einmal starten (ollama run {model}) "
            "oder „Kontext höchstens“ eintragen."
        ),
        model_loaded=False,
    )


async def _read_openai(client: httpx.AsyncClient, base_url: str, model: str) -> _Reading:
    base = base_url.rstrip("/")
    _status, payload, error = await _get_json(client, f"{base}/models")
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return _Reading(answered=False, reason=f"/models: {error or 'keine Modellliste'}")
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("id") or "") != model:
            continue
        for name in ("max_model_len", "context_length", "context_window"):
            loaded = _positive_int(entry.get(name))
            if loaded:
                return _Reading(answered=True, loaded=loaded, source=f"/models ({name})")
        return _Reading(
            answered=True,
            reason=(
                "Der Server nennt das Modell, aber kein Kontextfenster - unter "
                "„Kontext höchstens“ eintragen."
            ),
        )
    return _Reading(answered=True, reason=f"/models: „{model}“ ist dort nicht aufgeführt.")


async def _v1(client: httpx.AsyncClient, root: str, base_url: str, model: str) -> _Reading:
    return await _read_lmstudio_v1(client, root, model)


async def _v0(client: httpx.AsyncClient, root: str, base_url: str, model: str) -> _Reading:
    return await _read_lmstudio_v0(client, root, model)


async def _ollama(client: httpx.AsyncClient, root: str, base_url: str, model: str) -> _Reading:
    return await _read_ollama(client, root, model)


async def _openai(client: httpx.AsyncClient, root: str, base_url: str, model: str) -> _Reading:
    return await _read_openai(client, base_url, model)


# Feste Reihenfolge der Leser (siehe Moduldoc).
_READERS: Final[
    tuple[Callable[[httpx.AsyncClient, str, str, str], Awaitable[_Reading]], ...]
] = (_v1, _v0, _ollama, _openai)


def effective_window(
    loaded: int | None,
    cap: int | None,
    *,
    source: str | None,
    reason: str | None = None,
    why_unknown: str | None = None,
    parallel: int | None = None,
    reasoning_capability: ReasoningCapability | None = None,
    max_context: int | None = None,
    model_loaded: bool | None = None,
    output_limit: int | None = None,
) -> WindowInfo:
    """Die Regel fuer W an EINER Stelle: W = min(geladenes Fenster, "Kontext
    hoechstens"). Ohne gemeldetes Fenster gilt der Deckel (Quelle sagt das),
    ohne beides ist W unbekannt (`why_unknown` sagt warum).

    read_window() benutzt sie nach dem Lesen; Aufrufer, die das geladene
    Fenster schon aus einer eigenen Anfrage kennen (Statusstreifen
    GET /v1/ai/runtime), rechnen damit genauso wie der Kern."""
    common: dict[str, Any] = {
        "parallel": parallel,
        "reasoning_capability": reasoning_capability or ReasoningCapability(kind="unknown"),
        "max_context": max_context,
        "model_loaded": model_loaded,
        "output_limit": output_limit,
    }
    if loaded is not None:
        return WindowInfo(
            loaded=loaded,
            cap=cap,
            effective=min(loaded, cap) if cap is not None else loaded,
            source=source,
            reason=reason,
            **common,
        )
    why = why_unknown or "keine Angabe des Servers"
    if cap is not None:
        return WindowInfo(
            loaded=None,
            cap=cap,
            effective=cap,
            source=SOURCE_CAP,
            reason=f"Geladenes Fenster unbekannt ({why}) - es gilt „Kontext höchstens“.",
            **common,
        )
    return WindowInfo(
        loaded=None,
        cap=None,
        effective=None,
        source=None,
        reason=f"Das geladene Kontextfenster ist unbekannt: {why}",
        **common,
    )


def combine(reading: _Reading | None, cap: int | None, reasons: list[str]) -> WindowInfo:
    """Gelesenes Fenster und Deckel zu W verbinden."""
    return effective_window(
        reading.loaded if reading else None,
        cap,
        source=reading.source if reading else None,
        reason=reading.reason if reading else None,
        why_unknown="; ".join(reason for reason in reasons if reason) or None,
        parallel=reading.parallel if reading else None,
        reasoning_capability=reading.reasoning if reading else None,
        max_context=reading.max_context if reading else None,
        model_loaded=reading.model_loaded if reading else None,
    )


async def read_window(
    base_url: str,
    model: str,
    *,
    cap: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> WindowInfo:
    """Liest das geladene Fenster von `model` auf dem Server hinter
    `base_url` (OpenAI-kompatible Basis, z. B. http://127.0.0.1:1234/v1) und
    verbindet es mit dem Deckel `cap` ("Kontext hoechstens").

    Wirft nie: ein nicht erreichbarer Server ergibt ein unbekanntes Fenster
    mit Grund. `client` erlaubt Tests und Aufrufern, eine bestehende
    Verbindung mitzugeben; sonst gilt das kurze Discovery-Zeitbudget."""
    if client is None:
        timeout = httpx.Timeout(get_settings().ai_discovery_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as own_client:
            return await read_window(base_url, model, cap=cap, client=own_client)

    root = root_of(base_url)
    reasons: list[str] = []
    first_known: _Reading | None = None
    for read in _READERS:
        reading = await read(client, root, base_url, model)
        if reading.reason:
            reasons.append(reading.reason)
        if reading.loaded is not None:
            if first_known is not None and first_known.reasoning and not reading.reasoning:
                # v1 kannte das Modell (Denkfaehigkeit), aber ohne Fenster -
                # die Faehigkeit bleibt trotzdem die von v1.
                reading = _Reading(
                    answered=True,
                    loaded=reading.loaded,
                    source=reading.source,
                    reason=reading.reason,
                    parallel=reading.parallel or first_known.parallel,
                    reasoning=first_known.reasoning,
                    max_context=reading.max_context or first_known.max_context,
                    model_loaded=reading.model_loaded,
                )
            return combine(reading, cap, reasons)
        if reading.answered and reading.source is not None and first_known is None:
            first_known = reading
        if reading.authoritative:
            break
    return combine(first_known, cap, reasons)
