"""Gleicher anonymisierter PDF-Bestand fuer PKV-Leser und Audit Companion.

Verglichen wird die lokale Lesung ohne Modellaufruf, nicht eine behauptete
Erkennungsquote fuer Nutzerunterlagen. PDFs werden nur im Speicher erzeugt.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/worker"), str(ROOT / "services/api")]

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from worker.health_documents import reading  # noqa: E402
from worker.health_documents.processor import _deterministic_fields, _tesseract_binary  # noqa: E402

LINES = [
    "Muster Krankenversicherung AG", "Leistungsabrechnung Nr. 26 00 00004",
    "Bescheiddatum: 11.10.2026", "Anna Muster, geb. 01.01.1990",
    "Rechnungsnummer Datum Rechnungsbetrag Bewilligt",
    "R-100 01.10.2026 200,00 125,00", "R-101 02.10.2026 200,00 125,00",
    "Summe der Rechnungsbetraege / Erstattung 400,00 250,00", "Auszahlungsbetrag 250,00",
]


def fixtures() -> dict[str, bytes]:
    with fitz.open() as document:
        page = document.new_page()
        for index, line in enumerate(LINES):
            page.insert_text((50, 60 + index * 30), line, fontsize=12)
        digital = bytes(document.tobytes())
        png = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).tobytes("png")
    result = {"digital": digital}
    for name in ("scan", "mixed", "scan_with_native_footer", "low_contrast", "rotated"):
        with fitz.open() as document:
            if name == "mixed":
                cover = document.new_page()
                for index, line in enumerate([
                    "Muster Krankenversicherung AG", "Leistungsabrechnung Nr. 26 00 00004",
                    "Die Einzelabrechnung steht auf der folgenden Seite.",
                    "Dieser Brief enthaelt keine zusaetzlichen Rechnungspositionen.",
                ]):
                    cover.insert_text((50, 60 + index * 30), line, fontsize=12)
            image = Image.open(io.BytesIO(png)).convert("L")
            if name == "low_contrast":
                image = image.point(lambda value: 220 + int(value * 35 / 255))
            if name == "rotated":
                image = image.rotate(90, expand=True)
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            page = document.new_page(width=842 if name == "rotated" else 595,
                                     height=595 if name == "rotated" else 842)
            page.insert_image(page.rect, stream=stream.getvalue())
            if name == "rotated":
                page.set_rotation(90)
            if name == "scan_with_native_footer":
                page.insert_text((30, 815), "Footer " * 18, fontsize=7)
            result[name] = bytes(document.tobytes())
    return result


def pkv_read(content: bytes, binary: str, languages: str) -> str:
    pages = reading.native_pages(content, "application/pdf")
    for index, page in enumerate(pages):
        if page.method != "pdf_text":
            pages[index] = reading.ocr_page(content, "application/pdf", page.number, binary, languages)
    return reading.marked_text(pages)


def old_read(content: bytes, binary: str) -> str:
    import pypdfium2 as pdfium

    parts: list[str] = []
    reader = PdfReader(io.BytesIO(content))
    with pdfium.PdfDocument(content) as pdf:
        for index, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if len(text) < 80:
                bitmap = pdf[index].render(scale=2)
                stream = io.BytesIO()
                bitmap.to_pil().save(stream, format="PNG")
                response = subprocess.run([binary, "stdin", "stdout", "-l", "deu+eng",
                                           "--psm", "6"], input=stream.getvalue(),
                                          capture_output=True, check=True, timeout=90,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                text = response.stdout.decode("utf-8", "replace")
            parts.append(text)
    return "\n".join(parts)


AUDIT_READER = """
import base64, json, sys, time
from pathlib import Path
import pymupdf as fitz
from audit_companion.services import pdf, ocr
data=json.load(sys.stdin)
tool=ocr.OcrTool(Path(data['binary']),data['languages'],'gemeinsames Tesseract')
ocr.get_tool=lambda languages=None: tool
output={}
for name, encoded in data['documents'].items():
 started=time.perf_counter()
 parts=[]
 with fitz.open(stream=base64.b64decode(encoded),filetype='pdf') as document:
  for page in document:
   frame,lines=pdf.read_text_layer(page)
   usable,_=pdf.assess_text_quality(lines)
   if not usable: lines=ocr.ocr_page(page,frame,dpi=300).lines
   parts.extend(line.text for line in lines)
 output[name]={'text':'\\n'.join(parts),'seconds':round(time.perf_counter()-started,3)}
print(json.dumps(output))
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "dogfood-output/pkv-pdf-benchmark.json")
    args = parser.parse_args()
    binary = _tesseract_binary()
    if binary is None:
        raise SystemExit("Tesseract ist nicht installiert.")
    languages = reading.languages_for(binary)
    documents = fixtures()
    engines: dict[str, dict] = {"pkv_previous_reading": {}, "pkv_new_reading": {}}
    for name, content in documents.items():
        for engine, read in (("pkv_previous_reading", old_read), ("pkv_new_reading", pkv_read)):
            started = time.perf_counter()
            text = read(content, binary) if engine == "pkv_previous_reading" else read(content, binary, languages)
            engines[engine][name] = {"text": text, "seconds": round(time.perf_counter() - started, 3)}
    if args.audit_root:
        response = subprocess.run(
            [str(args.audit_root / ".venv/Scripts/python.exe"), "-c", AUDIT_READER],
            input=json.dumps({"binary": binary, "languages": languages,
                              "documents": {key: base64.b64encode(value).decode("ascii")
                                            for key, value in documents.items()}}),
            cwd=args.audit_root / "app", capture_output=True, text=True, encoding="utf-8",
            check=True, timeout=240,
        )
        engines["audit_companion"] = json.loads(response.stdout)
    report = {"scope": "synthetic local text/OCR reading, no model", "fixtures": len(documents),
              "engines": {}}
    for engine, results in engines.items():
        report["engines"][engine] = {}
        for name, result in results.items():
            fields = _deterministic_fields(result["text"])
            report["engines"][engine][name] = {
                "payout_correct": fields.get("approved_minor") == 25000,
                "invoice_numbers_correct": {item["invoice_number"] for item in fields["items"]}
                    == {"R-100", "R-101"},
                "seconds": result["seconds"],
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
