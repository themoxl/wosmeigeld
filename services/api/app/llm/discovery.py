"""Modellserver finden, Modell waehlen, Fenster lesen: das Ziel eines Aufrufs.

Discovery ist Infrastruktur fuer Statusendpunkt, Modellauswahl, Klassifikation
(API und Worker) und jede weitere KI-Aufrufstelle. Frueher lag sie zweimal
vor (app/services/ai_discovery.py und worker/ai/classifier.py); jetzt hier.

Ablauf von resolve_target():
  0. KI im Haushalt ausgeschaltet -> sofort ai_disabled, ohne einen Server
     anzusprechen. Aufrufer muessen den Schalter deshalb nicht selbst
     pruefen. (locate_target() laesst diesen Schritt aus - nur fuer die
     Statusanzeige, die auch bei ausgeschalteter KI das Modell zeigt.)
  1. Kandidaten-URLs der Einstellungen der Reihe nach: `GET /api/v0/models`
     (LM Studio, mit Typ und Ladezustand), sonst die OpenAI-kompatible Liste.
  2. Modellwahl: das ausdruecklich gewaehlte, sonst ein geladenes
     chatfaehiges, sonst irgendein chatfaehiges. Ein gewaehltes, aber
     verschwundenes Modell wird NICHT still ersetzt (model_missing).
  3. Fenster lesen (app/llm/window.py) - unbekannt heisst
     model_context_unknown; das Ziel wird trotzdem zurueckgegeben, damit die
     Statusanzeige zeigen kann, was sie weiss.

Anthropic (Einstellung provider = "anthropic", app/llm/providers.py) hat
einen eigenen, kurzen Weg: genau eine Adresse, ein Schluessel (sonst
model_key_missing, ohne Anfrage), die Modellliste GET /v1/models, und das
Modell muss ausdruecklich gewaehlt sein (model_not_chosen) - bei einem
Cloud-Anbieter waehlt der Finanzplaner nie selbst eines. Fenster und
Antwortgrenze stehen in derselben Liste (app/llm/anthropic_api.py).
"""

from dataclasses import dataclass, field
from typing import Any, Final, cast

import anthropic
import httpx
import httpx2

from app.core.config import get_settings
from app.llm import anthropic_api, providers, reasons
from app.llm.settings import LLMSettings
from app.llm.window import WindowInfo, read_window
from app.schemas.ai import AIModelInfo

# Modelltypen, die LM Studio meldet. "embeddings" kann keine Chat-Completion.
_CHAT_CAPABLE_KINDS: Final = frozenset({"llm", "vlm"})

# Heuristik fuer die Rueckfallebene ohne /api/v0/models: Namen, hinter denen
# erfahrungsgemaess ein Einbettungsmodell steckt. Bewusst nur dort angewandt -
# wo der Typ bekannt ist, entscheidet der Typ.
_EMBEDDING_NAME_HINTS: Final = ("embed", "embedding", "bge-", "e5-", "gte-")


def _as_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def looks_like_embedding(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(hint in lowered for hint in _EMBEDDING_NAME_HINTS)


def parse_detailed_model(entry: dict[str, Any]) -> AIModelInfo:
    """Eintrag aus /api/v0/models.

    loaded_context_tokens nur, wenn das Modell geladen ist: LM Studio laesst
    das Feld bei nicht geladenen Modellen teils stehen - ein Fenster, das
    gerade niemand geladen hat, ist keins."""
    model_id = str(entry["id"])
    kind = str(entry.get("type") or "unknown")
    state = str(entry.get("state") or "unknown")
    # Ausgeschlossen wird nur, was nachweislich nicht chatten kann.
    chat_capable = kind in _CHAT_CAPABLE_KINDS or (
        kind not in ("embeddings",) and not looks_like_embedding(model_id)
    )
    return AIModelInfo(
        id=model_id,
        kind=cast(Any, kind if kind in ("llm", "vlm", "embeddings") else "unknown"),
        state=cast(Any, state if state in ("loaded", "not-loaded") else "unknown"),
        publisher=(str(entry["publisher"]) if entry.get("publisher") else None),
        arch=(str(entry["arch"]) if entry.get("arch") else None),
        quantization=(str(entry["quantization"]) if entry.get("quantization") else None),
        max_context_tokens=_as_int(entry.get("max_context_length")),
        loaded_context_tokens=(
            _as_int(entry.get("loaded_context_length")) if state == "loaded" else None
        ),
        capabilities=[str(item) for item in (entry.get("capabilities") or [])],
        is_chat_capable=chat_capable,
    )


def parse_plain_model(entry: dict[str, Any]) -> AIModelInfo:
    """Eintrag aus der OpenAI-kompatiblen /v1/models: Zustand und
    Kontextlaenge sind dort nicht enthalten und bleiben unbekannt."""
    model_id = str(entry["id"])
    embedding = looks_like_embedding(model_id)
    return AIModelInfo(
        id=model_id,
        kind="embeddings" if embedding else "unknown",
        state="unknown",
        publisher=None,
        arch=None,
        quantization=None,
        max_context_tokens=None,
        loaded_context_tokens=None,
        capabilities=[],
        is_chat_capable=not embedding,
    )


async def fetch_models(
    client: httpx.AsyncClient, base_url: str
) -> tuple[list[AIModelInfo], bool] | None:
    """Modelle einer Basis-URL: (Modelle, detaillierte API verfuegbar) oder
    None, wenn die URL nicht antwortet. Eine leere Liste ist KEIN None -
    "laeuft, aber kein Modell installiert" ist eine andere Aussage."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        response = await client.get(f"{root}/api/v0/models")
        if response.status_code == 200:
            entries = response.json().get("data", [])
            return [
                parse_detailed_model(entry)
                for entry in entries
                if isinstance(entry, dict) and entry.get("id")
            ], True
    except Exception:  # noqa: BLE001 - fehlende LM-Studio-API ist ein Normalfall
        pass
    try:
        response = await client.get(f"{base_url.rstrip('/')}/models")
        if response.status_code == 200:
            entries = response.json().get("data", [])
            return [
                parse_plain_model(entry)
                for entry in entries
                if isinstance(entry, dict) and entry.get("id")
            ], False
    except Exception:  # noqa: BLE001
        return None
    return None


def select_model(models: list[AIModelInfo], preferred: str | None) -> AIModelInfo | None:
    """Gewaehltes Modell, sonst ein geladenes chatfaehiges, sonst irgendein
    chatfaehiges. Ein gewaehltes, aber verschwundenes Modell ergibt None."""
    chat_models = [model for model in models if model.is_chat_capable]
    if preferred:
        return next((model for model in chat_models if model.id == preferred), None)
    loaded = [model for model in chat_models if model.state == "loaded"]
    if loaded:
        return loaded[0]
    return chat_models[0] if chat_models else None


@dataclass(frozen=True)
class CloudListing:
    """Modellliste eines Cloud-Anbieters - oder der benannte Grund, warum es
    keine gibt (kein Schluessel, Schluessel abgelehnt, nicht erreichbar)."""

    base_url: str
    models: list[anthropic_api.ClaudeModel]
    reason_code: str | None = None
    message: str | None = None


async def anthropic_listing(settings: LLMSettings) -> CloudListing:
    """GET /v1/models mit dem hinterlegten Schluessel. Ohne Schluessel geht
    keine Anfrage hinaus."""
    url = settings.provider_url() or ""
    if settings.api_key is None:
        return CloudListing(
            base_url=url,
            models=[],
            reason_code=reasons.MODEL_KEY_MISSING,
            message=reasons.describe(reasons.MODEL_KEY_MISSING, settings.key_problem),
        )
    try:
        models = await anthropic_api.list_models(settings, url)
    except (anthropic.APIError, httpx2.TransportError) as exc:
        code, detail = anthropic_api.failure_of(exc)
        return CloudListing(
            base_url=url,
            models=[],
            reason_code=code,
            message=reasons.describe(code, settings.api_key.mask(detail)),
        )
    return CloudListing(base_url=url, models=models)


async def discover(
    settings: LLMSettings, *, client: httpx.AsyncClient | None = None
) -> tuple[str | None, list[AIModelInfo], bool]:
    """Erste antwortende Basis-URL samt Modellliste:
    (URL oder None, Modelle, ob die detaillierte API verfuegbar war)."""
    if settings.provider == providers.ANTHROPIC:
        listing = await anthropic_listing(settings)
        if listing.reason_code is not None:
            return None, [], False
        return listing.base_url, [model.info for model in listing.models], True
    if client is None:
        timeout = httpx.Timeout(get_settings().ai_discovery_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as own_client:
            return await discover(settings, client=own_client)
    for base_url in settings.candidate_urls():
        found = await fetch_models(client, base_url)
        if found is not None:
            models, detailed = found
            return base_url, models, detailed
    return None, [], False


@dataclass(frozen=True)
class Target:
    """Wohin ein Aufruf geht und mit welchem Fenster gerechnet wird.

    base_url  OpenAI-kompatible Basis (z. B. http://127.0.0.1:1234/v1)
    model     Modell-ID, wie sie im Chat-Aufruf steht
    window    gelesenes Fenster (kann unbekannt sein - dann sendet der
              Gateway nicht, model_context_unknown)"""

    base_url: str
    model: str
    window: WindowInfo
    selected: AIModelInfo | None = None
    models: tuple[AIModelInfo, ...] = field(default_factory=tuple)
    detailed: bool = False


@dataclass(frozen=True)
class TargetResult:
    """Ergebnis von resolve_target().

    reason_code None heisst: target ist vollstaendig und das Fenster bekannt.
    Bei model_context_unknown ist target trotzdem gesetzt (Statusanzeige)."""

    target: Target | None
    base_url: str | None
    models: list[AIModelInfo]
    detailed: bool
    reason_code: str | None = None
    message: str | None = None

    @property
    def ready(self) -> bool:
        return self.reason_code is None and self.target is not None


async def resolve_target(
    settings: LLMSettings, *, client: httpx.AsyncClient | None = None
) -> TargetResult:
    """Server, Modell und Fenster fuer die Einstellungen eines Haushalts.

    Ist die KI im Haushalt ausgeschaltet, wird kein Server angesprochen:
    ai_disabled mit Klartext."""
    if not settings.is_enabled:
        return TargetResult(
            target=None,
            base_url=None,
            models=[],
            detailed=False,
            reason_code=reasons.AI_DISABLED,
            message=reasons.describe(reasons.AI_DISABLED),
        )
    return await locate_target(settings, client=client)


async def locate_target(
    settings: LLMSettings, *, client: httpx.AsyncClient | None = None
) -> TargetResult:
    """Wie resolve_target(), aber ohne den KI-Schalter: nur fuer die
    Statusanzeige, die auch bei ausgeschalteter KI zeigen soll, welches Modell
    mit welchem Fenster bereitstuende. Kein Modellaufruf darf sich darauf
    stuetzen."""
    if settings.provider == providers.ANTHROPIC:
        return await _locate_anthropic(settings)
    if client is None:
        timeout = httpx.Timeout(get_settings().ai_discovery_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as own_client:
            return await locate_target(settings, client=own_client)

    base_url, models, detailed = await discover(settings, client=client)
    if base_url is None:
        return TargetResult(
            target=None,
            base_url=None,
            models=[],
            detailed=False,
            reason_code=reasons.MODEL_UNAVAILABLE,
            message=reasons.describe(
                reasons.MODEL_UNAVAILABLE,
                "Keine der Adressen antwortet: " + ", ".join(settings.candidate_urls()) + ".",
            ),
        )
    selected = select_model(models, settings.model_name)
    if selected is None:
        if settings.model_name:
            code = reasons.MODEL_MISSING
            detail = f"Eingestellt ist „{settings.model_name}“."
        else:
            code = reasons.MODEL_UNAVAILABLE
            detail = (
                "Der Server läuft, aber es ist kein chatfähiges Modell installiert "
                "(Einbettungsmodelle können nicht chatten)."
            )
        return TargetResult(
            target=None,
            base_url=base_url,
            models=models,
            detailed=detailed,
            reason_code=code,
            message=reasons.describe(code, detail),
        )
    window = await read_window(
        base_url, selected.id, cap=settings.context_cap_tokens, client=client
    )
    target = Target(
        base_url=base_url,
        model=selected.id,
        window=window,
        selected=selected,
        models=tuple(models),
        detailed=detailed,
    )
    if not window.known:
        return TargetResult(
            target=target,
            base_url=base_url,
            models=models,
            detailed=detailed,
            reason_code=reasons.MODEL_CONTEXT_UNKNOWN,
            message=reasons.describe(reasons.MODEL_CONTEXT_UNKNOWN, window.reason),
        )
    return TargetResult(target=target, base_url=base_url, models=models, detailed=detailed)


async def _locate_anthropic(settings: LLMSettings) -> TargetResult:
    """Ziel bei Anthropic: Schluessel, Modellliste, ausdrueckliche Modellwahl,
    Fenster aus derselben Liste."""
    listing = await anthropic_listing(settings)
    if listing.reason_code is not None:
        return TargetResult(
            target=None,
            base_url=None,
            models=[],
            detailed=False,
            reason_code=listing.reason_code,
            message=listing.message,
        )
    infos = [model.info for model in listing.models]
    if not settings.model_name:
        return TargetResult(
            target=None,
            base_url=listing.base_url,
            models=infos,
            detailed=True,
            reason_code=reasons.MODEL_NOT_CHOSEN,
            message=reasons.describe(reasons.MODEL_NOT_CHOSEN),
        )
    chosen = next(
        (model for model in listing.models if model.info.id == settings.model_name), None
    )
    if chosen is None:
        return TargetResult(
            target=None,
            base_url=listing.base_url,
            models=infos,
            detailed=True,
            reason_code=reasons.MODEL_MISSING,
            message=reasons.describe(
                reasons.MODEL_MISSING,
                f"Eingestellt ist „{settings.model_name}“; Anthropic bietet es mit diesem "
                "Schlüssel nicht an.",
            ),
        )
    window = anthropic_api.window_of(chosen, settings.context_cap_tokens)
    target = Target(
        base_url=listing.base_url,
        model=chosen.info.id,
        window=window,
        selected=chosen.info,
        models=tuple(infos),
        detailed=True,
    )
    if not window.known:
        return TargetResult(
            target=target,
            base_url=listing.base_url,
            models=infos,
            detailed=True,
            reason_code=reasons.MODEL_CONTEXT_UNKNOWN,
            message=reasons.describe(reasons.MODEL_CONTEXT_UNKNOWN, window.reason),
        )
    return TargetResult(target=target, base_url=listing.base_url, models=infos, detailed=True)
