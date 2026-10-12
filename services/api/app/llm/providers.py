"""KI-Anbieter: wohin ein Aufruf geht und was dafuer gilt (Etappe 3).

Vorbild ist die Anbieterwahl in SIA (server/ki/anbieter.py) und im Audit
Companion (audit_companion/ai/providers.py). Jeder Eintrag sagt offen, was
gilt: welche Adresse feststeht, ob ein API-Schluessel noetig ist, ob die Daten
eines Aufrufs den Haushalt verlassen und ob ein Modell ausdruecklich gewaehlt
sein muss. Die Oberflaeche zeigt genau diese Liste (AISettings.providers).

Fest gewaehlt, kein stiller Wechsel (Entscheidung des Besitzers, 11.10.2026):
der Haushalt waehlt einen Anbieter, und nur der wird angesprochen. Faellt er
aus, scheitert der Aufruf mit benanntem Grund - es gibt keinen Rueckfall von
Claude auf LM Studio oder umgekehrt.

Rein: keine Netz-, keine DB-Zugriffe.
"""

from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

LOCAL: Final = "local"
ANTHROPIC: Final = "anthropic"

KeyNeed = Literal["none", "required"]


@dataclass(frozen=True)
class Provider:
    """Ein waehlbarer KI-Anbieter.

    url             feste Adresse; None = die Adressen der Einstellung
                    (custom_base_url, dann die Servervorgabe)
    key             "required": ohne hinterlegten Schluessel geht kein Aufruf
                    hinaus
    leaves_house    True: Daten eines Aufrufs gehen an einen fremden Dienst
    model_required  True: ohne ausdrueckliche Modellwahl kein Aufruf - bei
                    einem Cloud-Anbieter waehlt der Finanzplaner nie selbst
                    ein (womoeglich teures) Modell"""

    id: str
    name: str
    url: str | None
    key: KeyNeed
    leaves_house: bool
    model_required: bool
    hint: str


PROVIDERS: Final[tuple[Provider, ...]] = (
    Provider(
        id=LOCAL,
        name="Lokal (LM Studio, Ollama, eigener Server)",
        url=None,
        key="none",
        leaves_house=False,
        model_required=False,
        hint=(
            "Auf diesem oder einem anderen Rechner im eigenen Netz. Kontextfenster und "
            "Denkoptionen liest der Finanzplaner selbst ab."
        ),
    ),
    Provider(
        id=ANTHROPIC,
        name="Anthropic (Claude)",
        url="https://api.anthropic.com",
        key="required",
        leaves_house=True,
        model_required=True,
        hint=(
            "Claude über die Messages-API. Buchungen, Verträge, Dokumenttexte und Fragen "
            "aus dem KI-Feld gehen an Anthropic. Fenster, Antwortgrenze und Denkstufen liest "
            "der Finanzplaner aus der Modellliste; „Kontext höchstens“ begrenzt die Kosten je "
            "Aufruf."
        ),
    ),
)


class ProviderError(ValueError):
    """Unbekannter Anbieter oder unbrauchbare Adresse - der Text sagt, was zu tun ist."""


def provider(provider_id: str | None) -> Provider:
    """Der Eintrag aus PROVIDERS. Ohne Kennung gilt "lokal" (Einstellungen von
    vor der Anbieterwahl); eine unbekannte Kennung scheitert laut."""
    wanted = provider_id or LOCAL
    for entry in PROVIDERS:
        if entry.id == wanted:
            return entry
    raise ProviderError(
        f"Unbekannter KI-Anbieter „{str(wanted)}“ – unter Einstellungen › KI neu wählen."
    )


def target_of(url: str) -> str:
    """Schema://host:port einer Adresse - auch der Standardport wird
    ausgeschrieben, damit http://h und http://h:80 dasselbe Ziel sind.

    Daran ist ein API-Schluessel gebunden (er geht nur an genau dieses Ziel),
    und daran haengt die Messung eines Modells (app/llm/measurements.py)."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}:{port}"


def readable_target(value: str) -> str:
    """Ziel zum Lesen: der Standardport muss nicht dastehen."""
    if value.startswith("https://") and value.endswith(":443"):
        return value.removesuffix(":443")
    if value.startswith("http://") and value.endswith(":80"):
        return value.removesuffix(":80")
    return value
