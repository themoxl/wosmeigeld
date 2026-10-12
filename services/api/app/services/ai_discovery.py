"""Modell-Discovery gegen LM Studio - liegt seit dem KI-Kern in app/llm/discovery.py.

Dieses Modul bleibt als stabiler Importpfad fuer die API (Modellkatalog,
Gesundheitsdokumente, Tests). Das wirksame Kontextfenster liefert nicht mehr
`context_tokens_of` (die fiel auf max_context_length zurueck und
ueberschaetzte das Fenster nicht geladener Modelle), sondern
app/llm/window.py::read_window.
"""

from app.llm.discovery import (
    fetch_models,
    looks_like_embedding,
    parse_detailed_model,
    parse_plain_model,
    select_model,
)

__all__ = [
    "fetch_models",
    "looks_like_embedding",
    "parse_detailed_model",
    "parse_plain_model",
    "select_model",
]
