# services/worker

Dramatiq-Worker mit Redis-Broker. Uebernimmt ab Phase 3 Import- und
Analysejobs, ab Phase 6 KI-Klassifikationsjobs (Architekturplan Abschnitt 17).

## Lokale Entwicklung ohne Docker

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
dramatiq worker.actors --processes 1 --threads 4
```

Erwartet einen erreichbaren Redis unter `REDIS_URL` (siehe `.env`).

## Tests, Lint, Typpruefung

```bash
pytest
ruff check .
mypy worker
```

## Heartbeat

`worker/heartbeat.py` schreibt alle `WORKER_HEARTBEAT_INTERVAL_SECONDS`
Sekunden einen Zeitstempel unter dem Redis-Key `worker:heartbeat`.
`services/api` liest diesen Key fuer `GET /v1/system/health`, damit die
Statusseite erkennt, ob ein Worker-Prozess laeuft.

## PDF-Lesung fuer PKV und Beihilfe

`worker/health_documents/reading.py` prueft die Textebene pro Seite mit
PyMuPDF. Scans und grosse ungelesene Bildbereiche werden mit Tesseract als
TSV bei 300 dpi gelesen; unsichere Feldstellen werden bei 400 dpi erneut
gelesen. Gespeichert werden Abrechnungsfelder, Wortfundstellen und
Seitenqualitaet, kein medizinischer Volltext. Fundstellen liegen immer in
Anteilen der ungedrehten PDF-CropBox; der Viewer bildet die Seitendrehung ab.

Tesseract braucht die Sprachdaten `deu` und/oder `eng`. Der Container installiert
beide; unter Windows wird auch `C:/Program Files/Tesseract-OCR/tesseract.exe`
erkannt. Fehlende OCR wird als Pruefgrund angezeigt. Die optionale Bildlesung
verwendet ausschliesslich ein bereits ausgewaehltes lokales Modell mit
Bildfaehigkeit ueber den KI-Kern. Ihre Ergebnisse verlangen immer Handpruefung.
Der Aufruf zur Bildabschrift erhaelt keine erwarteten Feldwerte.

Der gemeinsame synthetische Vergleichsbestand liegt in
`scripts/benchmark_pkv_pdf.py`. Aus dem Repo-Root:

```powershell
./services/worker/.venv/Scripts/python.exe scripts/benchmark_pkv_pdf.py --audit-root C:/Pfad/zum/Audit-Companion
```

Der optionale Audit-Pfad muss dessen vorhandenes Venv enthalten. Ohne ihn
werden nur alter und neuer PKV-Leser verglichen. Der JSON-Bericht wird unter
`dogfood-output/pkv-pdf-benchmark.json` abgelegt. Die sechs Varianten pruefen
Digital-PDF, Scan, Mischdokument, Scan mit digitaler Fusszeile, niedrigen
Kontrast und Drehung. Das ist ein Regressionstest ohne Modellaufruf und keine
Erkennungsquote fuer echte Unterlagen.
