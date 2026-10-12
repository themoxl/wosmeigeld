"""KI-Feld: Chat zu den eigenen Finanzen (Etappe 2, docs/KI-FELD-SCHNITTSTELLE.md).

Bausteine:
  loop.py          Agentenschleife (Port von sia_public lauf())
  compaction.py    Verdichtung in drei Stufen
  number_check.py  Zahlenpruefung der Antwort gegen die Werkzeugergebnisse
  tools/           nur lesende Werkzeuge ueber die bestehenden Dienste
  runner.py        Ausfuehrung eines Laufs im Worker (ai_tasks 'assistant_chat')
  store.py         Datenbankzugriff und Vertragsformen
  events.py        Live-Ereignisse ueber Redis (bzw. im Speicher fuer Tests)
  streaming.py     NDJSON-Strom fuer GET /v1/assistant/runs/{id}/stream
"""
