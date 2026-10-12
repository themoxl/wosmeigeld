"""Duenner Client fuer eine selbstgehostete SearXNG-Instanz.

Genutzt von `project_cost_ai_suggestion_service.py`, um die KI-Kostenschaetzung
optional mit aktuellen Marktpreis-/Regionalinformationen aus dem Web zu
erden. Bewusst EIN Suchaufruf mit der Nutzerbeschreibung als Anfrage, keine
mehrstufige "KI erzeugt erst Suchanfragen"-Orchestrierung - das waere fuer den
Nutzen hier nicht robust genug zu rechtfertigen (Plan-Nachtrag, siehe
VORHABEN-GROSSINVESTITIONEN-PLAN.md).

SearXNG selbst ist keine Abhaengigkeit dieses Repos - nur die JSON-API
(`GET {base_url}/search?q=...&format=json`), die jede SearXNG-Instanz mit
aktivierter `json`-Ausgabeformat-Einstellung anbietet. Ist die Instanz nicht
erreichbar oder liefert kein JSON, faellt der Aufruf auf eine leere
Ergebnisliste zurueck: die Treffer sind nur Zusatzmaterial, die Schaetzung
laeuft dann ohne sie weiter (die Vorschau zeigt keine Treffer). Anders als ein
gescheiterter Modellaufruf, der immer einen benannten Grund des KI-Kerns
traegt (app/llm/reasons.py).
"""

from dataclasses import dataclass
from typing import Any, Final

import httpx

# Anfragelaenge deckeln: eine sehr lange Projektbeschreibung als rohe
# Query-URL zu senden waere unnoetig und manche SearXNG-Instanzen weisen
# ueberlange Anfragen ab.
_MAX_QUERY_LENGTH: Final = 300

_MAX_RESULTS: Final = 5

_REQUEST_TIMEOUT_SECONDS: Final = 8.0


@dataclass(frozen=True)
class SearxngResult:
    title: str
    url: str
    snippet: str


async def search(base_url: str, query: str) -> list[SearxngResult]:
    """Bis zu `_MAX_RESULTS` Treffer, oder eine leere Liste bei jedem Fehler."""
    trimmed_query = " ".join(query.split())[:_MAX_QUERY_LENGTH]
    if not trimmed_query:
        return []
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/search",
                params={"q": trimmed_query, "format": "json"},
            )
            if response.status_code != 200:
                return []
            payload = response.json()
    except Exception:
        return []

    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return []

    results: list[SearxngResult] = []
    for entry in raw_results[:_MAX_RESULTS]:
        if not isinstance(entry, dict):
            continue
        results.append(_result_from_entry(entry))
    return results


def _result_from_entry(entry: dict[str, Any]) -> SearxngResult:
    return SearxngResult(
        title=str(entry.get("title") or "").strip()[:200],
        url=str(entry.get("url") or "").strip()[:500],
        snippet=str(entry.get("content") or "").strip()[:500],
    )
