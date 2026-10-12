"""Exportiert das OpenAPI-Schema von app.main.app als JSON nach stdout.

Wird von packages/contracts/scripts/generate-api-types.mjs aufgerufen, um
daraus TypeScript-Typen zu generieren (siehe packages/contracts/README.md).
Braucht keine laufende DB/kein Supabase - FastAPI introspiziert dafuer nur
die deklarierten Routen und Pydantic-Modelle, verbindet sich nirgends."""

import json
import sys
from pathlib import Path

# Script-Aufruf (nicht `python -m`) setzt sys.path[0] auf scripts/ statt auf
# den Elternordner - app/ waere sonst nicht importierbar.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

print(json.dumps(app.openapi()))
