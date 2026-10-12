"""Lokale PDF-/OCR-Lesung mit pruefbarer Wortgeometrie.

Wie im Audit Companion: Textebene je Seite beurteilen, sonst Tesseract TSV,
unsichere Ausschnitte hoeher aufgeloest nachlesen. Dokumenttext bleibt im
Speicher; nach aussen gehen nur Seitenqualitaet und Fundstellen erlaubter Felder.
"""

from __future__ import annotations

import csv
import io
import math
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

import pymupdf as fitz
from PIL import Image, ImageOps

# PyMuPDF liefert keine vollstaendigen Typen fuer seine Geometrie/Factory-API.
PDF: Any = fitz

MAX_EDGE = 5000
OCR_DPI = 300
CROP_DPI = 400
MIN_CONFIDENCE = 70.0


def unrotate_box(box: tuple[float, ...], rotation: int) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    if rotation == 90:
        return y0, 1 - x1, y1, 1 - x0
    if rotation == 180:
        return 1 - x1, 1 - y1, 1 - x0, 1 - y0
    if rotation == 270:
        return 1 - y1, x0, 1 - y0, x1
    return x0, y0, x1, y1


def rotate_box(box: tuple[float, ...], rotation: int) -> tuple[float, float, float, float]:
    return unrotate_box(box, (360 - rotation) % 360)


@dataclass(frozen=True)
class Word:
    text: str
    box: tuple[float, float, float, float]
    confidence: float | None = None
    line: tuple[int, int, int] = (0, 0, 0)


@dataclass
class PageReading:
    number: int
    words: list[Word] = field(default_factory=list)
    method: str = "empty"
    warnings: list[str] = field(default_factory=list)
    rotation: int = 0
    width: float = 0
    height: float = 0
    native_words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        def text_box(word: Word) -> tuple[float, float, float, float]:
            # OCR-Zeilen beziehen sich auf das gerenderte Bild. Ihre gespeicherte
            # Geometrie bleibt trotzdem in der ungedrehten PDF-CropBox.
            if self.method in {"ocr", "mixed", "ocr_crop", "vision", "vision_crop"}:
                return rotate_box(word.box, self.rotation)
            return word.box

        lines: dict[tuple[int, int, int], list[Word]] = {}
        for word in self.words:
            lines.setdefault(word.line, []).append(word)
        # Zeilen aus benachbarten Spalten auf derselben Hoehe zusammenfuehren.
        rows: list[tuple[float, float, list[Word]]] = []
        for words in lines.values():
            y = min(text_box(word)[1] for word in words)
            height = max(text_box(word)[3] - text_box(word)[1] for word in words)
            rows.append((y, height, words))
        merged: list[tuple[float, float, list[Word]]] = []
        for y, height, words in sorted(rows, key=lambda row: row[0]):
            if merged and abs(y - merged[-1][0]) < min(height, merged[-1][1]) * 0.5:
                merged[-1][2].extend(words)
            else:
                merged.append((y, height, list(words)))
        return "\n".join(
            " ".join(word.text for word in sorted(words, key=lambda word: text_box(word)[0]))
            for _, _, words in merged
        )

    def summary(self) -> dict[str, Any]:
        return {
            "page_number": self.number, "method": self.method,
            "word_count": len(self.words), "rotation": self.rotation,
            "width": self.width, "height": self.height,
            "warnings": self.warnings,
        }


def _box(rect: Any, page: Any, *, displayed: bool = False) -> tuple[float, float, float, float]:
    area = PDF.Rect(rect)
    if displayed:
        area = area * page.derotation_matrix
    width, height = page.cropbox.width, page.cropbox.height
    return (
        max(0.0, min(1.0, area.x0 / width)),
        max(0.0, min(1.0, area.y0 / height)),
        max(0.0, min(1.0, area.x1 / width)),
        max(0.0, min(1.0, area.y1 / height)),
    )


def text_usable(words: list[Word]) -> bool:
    chars = sum(len(word.text) for word in words)
    if len(words) < 12 or chars < 60:
        return False
    alnum = sum(character.isalnum() for word in words for character in word.text)
    tops = [word.box[1] for word in words]
    return alnum / chars >= 0.35 and max(tops) - min(tops) >= 0.04


def image_needs_reading(page: Any, words: list[Word]) -> bool:
    """Ein Scan zwischen digitalem Briefkopf und Fusszeile braucht ebenfalls OCR."""
    for image in page.get_image_info():
        x0, y0, x1, y1 = _box(image["bbox"], page)
        area = max(0, x1 - x0) * max(0, y1 - y0)
        if area < 0.25:
            continue
        inside = sum(x0 <= (word.box[0] + word.box[2]) / 2 <= x1 and
                     y0 <= (word.box[1] + word.box[3]) / 2 <= y1 for word in words)
        if inside < max(12, int(area * 50)):
            return True
    return False


def native_pages(content: bytes, mime_type: str) -> list[PageReading]:
    if mime_type != "application/pdf":
        return [PageReading(1)]
    result: list[PageReading] = []
    with PDF.open(stream=content, filetype="pdf") as document:
        for index, page in enumerate(document):
            words: list[Word] = []
            warnings: list[str] = []
            try:
                for entry in page.get_text("words"):
                    if str(entry[4]).strip():
                        words.append(Word(str(entry[4]), _box(entry[:4], page),
                                          line=(int(entry[5]), 0, int(entry[6]))))
            except Exception:
                warnings.append("pdf_text_failed")
            method = "pdf_text" if text_usable(words) else "sparse"
            try:
                if method == "pdf_text" and image_needs_reading(page, words):
                    method = "mixed"
            except Exception:
                warnings.append("pdf_image_check_failed")
            result.append(PageReading(
                index + 1, words, method,
                warnings, int(page.rotation), page.cropbox.width, page.cropbox.height,
            ))
    return result


def _command(
    binary: str, arguments: list[str], **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [binary, *arguments], capture_output=True, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs,
    )


def languages_for(binary: str) -> str:
    try:
        result = _command(binary, ["--list-langs"], timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("ocr_unavailable") from exc
    if result.returncode:
        raise RuntimeError("ocr_unavailable")
    installed = set(result.stdout.decode("utf-8", "replace").splitlines()[1:])
    wanted = [language for language in ("deu", "eng") if language in installed]
    if not wanted:
        raise RuntimeError("ocr_languages_missing")
    return "+".join(wanted)


def _deskew(image: Image.Image) -> tuple[Image.Image, float]:
    """Nur bei klarer Verbesserung drehen; Abbildung zurueck bleibt bekannt."""
    small = image.copy()
    small.thumbnail((500, 700))
    ink = small.point(lambda value: 255 if value < 160 else 0)

    def score(angle: float) -> float:
        rotated = ink.rotate(angle, fillcolor=0)
        values = [value if isinstance(value, int) else 0
                  for value in rotated.get_flattened_data()]
        width = rotated.width
        rows = [sum(values[start:start + width]) for start in range(0, len(values), width)]
        return float(sum((right - left) ** 2 for left, right in zip(rows, rows[1:], strict=False)))

    baseline = score(0)
    if not baseline:
        return image, 0.0
    choices = [(score(angle / 2), angle / 2) for angle in range(-6, 7)]
    best, angle = max(choices)
    if abs(angle) >= 0.5 and best > baseline * 1.2:
        return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255), angle
    return image, 0.0


def _unskew_rect(rect: tuple[float, float, float, float], angle: float,
                 width: int, height: int) -> tuple[float, float, float, float]:
    if not angle:
        return rect
    cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    points = []
    for x in (rect[0], rect[2]):
        for y in (rect[1], rect[3]):
            dx, dy = x - width / 2, y - height / 2
            points.append((width / 2 + cosine * dx - sine * dy,
                           height / 2 + sine * dx + cosine * dy))
    return (min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points))


def ocr_page(content: bytes, mime_type: str, number: int, binary: str, languages: str,
             clip: tuple[float, float, float, float] | None = None) -> PageReading:
    """TSV statt Freitext; stdin vermeidet Windows-Probleme mit Unicode-Pfaden."""
    document: Any = None
    page: Any = None
    origin_x = origin_y = 0.0
    zoom = 1.0
    try:
        if mime_type == "application/pdf":
            document = PDF.open(stream=content, filetype="pdf")
            page = document[number - 1]
            area = page.rect
            if clip:
                # Feldkoordinaten liegen in der ungedrehten CropBox.
                area = PDF.Rect(
                    clip[0] * page.cropbox.width, clip[1] * page.cropbox.height,
                    clip[2] * page.cropbox.width, clip[3] * page.cropbox.height,
                ) * page.rotation_matrix
                area &= page.rect
            zoom = min((CROP_DPI if clip else OCR_DPI) / 72,
                       (MAX_EDGE - 2) / max(area.width, area.height, 1))
            pixmap = page.get_pixmap(matrix=PDF.Matrix(zoom, zoom),
                                     colorspace=fitz.csGRAY, clip=area, alpha=False)
            origin_x, origin_y = pixmap.x, pixmap.y
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
        else:
            with Image.open(io.BytesIO(content)) as original:
                image = ImageOps.exif_transpose(original).convert("L")
            image.thumbnail((MAX_EDGE, MAX_EDGE))
        image = ImageOps.autocontrast(image, cutoff=1)
        image, angle = _deskew(image)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        result = _command(binary, ["stdin", "stdout", "-l", languages,
                                   "--psm", "6" if clip else "3", "tsv"],
                          input=stream.getvalue(), timeout=90)
        if result.returncode:
            raise RuntimeError("ocr_failed")
        words: list[Word] = []
        for entry in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")),
                                    delimiter="\t", quoting=csv.QUOTE_NONE):
            try:
                if entry.get("level") != "5" or not entry.get("text", "").strip():
                    continue
                x, y = float(entry["left"]), float(entry["top"])
                rect = _unskew_rect((x, y, x + float(entry["width"]),
                                     y + float(entry["height"])), angle, image.width, image.height)
                if page is not None:
                    box = _box(((rect[0] + origin_x) / zoom, (rect[1] + origin_y) / zoom,
                                (rect[2] + origin_x) / zoom, (rect[3] + origin_y) / zoom),
                               page, displayed=True)
                else:
                    box = (rect[0] / image.width, rect[1] / image.height,
                           rect[2] / image.width, rect[3] / image.height)
                words.append(Word(entry["text"].strip(), box, float(entry["conf"]),
                                  (int(entry["block_num"]), int(entry["par_num"]),
                                   int(entry["line_num"]))))
            except (ValueError, KeyError):
                continue
        return PageReading(number, words, "ocr_crop" if clip else "ocr",
                           [] if words else ["ocr_text_empty"],
                           int(page.rotation) if page is not None else 0,
                           page.cropbox.width if page is not None else image.width,
                           page.cropbox.height if page is not None else image.height)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("ocr_failed") from exc
    finally:
        if document is not None:
            document.close()


def marked_text(pages: list[PageReading]) -> str:
    return "".join(f"\n--- Seite {page.number} ---\n{page.text}" for page in pages)


def _normalized(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold())


def field_locations(value: Any, key: str, pages: list[PageReading]) -> list[dict[str, Any]]:
    """Wertsuche gegen gelesene Woerter; keine vom Modell erfundene Geometrie."""
    if value is None or isinstance(value, list | dict):
        return []
    targets = {_normalized(str(value))}
    if key.endswith("_minor"):
        amount = int(value)
        targets = {_normalized(f"{amount // 100},{amount % 100:02d}"),
                   _normalized(f"{amount / 100:,.2f}")}
    elif key.endswith("_date") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        year, month, day = str(value).split("-")
        targets |= {_normalized(f"{day}.{month}.{year}"),
                    _normalized(f"{int(day)}.{int(month)}.{year}")}
    targets.discard("")
    found: list[dict[str, Any]] = []
    for page in pages:
        for start, word in enumerate(page.words):
            candidates = [word]
            for offset in range(1, min(10, len(page.words) - start) + 1):
                if offset > 1:
                    next_word = page.words[start + offset - 1]
                    if next_word.line != word.line:
                        break
                    candidates.append(next_word)
                text = _normalized(" ".join(item.text for item in candidates))
                if text in targets:
                    confidences = [item.confidence for item in candidates
                                   if item.confidence is not None]
                    found.append({
                        "page_number": page.number,
                        "box": [min(item.box[0] for item in candidates),
                                min(item.box[1] for item in candidates),
                                max(item.box[2] for item in candidates),
                                max(item.box[3] for item in candidates)],
                        "method": page.method,
                        "confidence": min(confidences) if confidences else None,
                    })
                    break
    return found


def render_image(content: bytes, number: int,
                 clip: tuple[float, float, float, float] | None = None) -> bytes:
    """Begrenztes Seiten-/Ausschnittsbild fuer Viewer und optionale Bildlesung."""
    with PDF.open(stream=content, filetype="pdf") as document:
        page = document[number - 1]
        area = page.rect
        if clip:
            area = PDF.Rect(clip[0] * page.cropbox.width, clip[1] * page.cropbox.height,
                             clip[2] * page.cropbox.width, clip[3] * page.cropbox.height)
            area = area * page.rotation_matrix & page.rect
        zoom = min(2.0, 2000 / max(area.width, area.height, 1))
        return bytes(page.get_pixmap(matrix=PDF.Matrix(zoom, zoom), clip=area,
                                     alpha=False).tobytes("png"))
