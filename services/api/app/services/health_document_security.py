"""Defensive Eingangspruefung fuer private Gesundheitsdokumente.

Client-Dateiname und Content-Type sind nie die Quelle der Wahrheit. Erst nach
Magic-Byte-, Struktur- und Groessenpruefung darf ein Dokument in die private
Ablage gelangen. Dokumentinhalte werden dabei weder geloggt noch in
Fehlermeldungen aufgenommen.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Any, NoReturn

from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.config import Settings

logger = logging.getLogger(__name__)

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9A-Za-zÄÖÜäöüß._() -]+")
_MAX_IMAGE_PIXELS = 80_000_000

# Annotationen sind in echten Versicherungs-PDFs die Regel, nicht die Ausnahme:
# jeder Hyperlink im Fussbereich ist ein /Link. Abgelehnt wird deshalb nicht
# das blosse Vorhandensein von /Annots, sondern nur die Bauart, die ausserhalb
# des Betrachters etwas ausloesen oder Daten mitschleppen kann.
_BLOCKED_ANNOTATION_SUBTYPES = frozenset(
    {"/Widget", "/FileAttachment", "/Movie", "/Sound", "/Screen", "/RichMedia", "/3D"}
)
_BLOCKED_ACTION_TYPES = frozenset(
    {
        "/GoToE",
        "/GoToR",
        "/Hide",
        "/ImportData",
        "/JavaScript",
        "/Launch",
        "/Movie",
        "/Rendition",
        "/ResetForm",
        "/SetOCGState",
        "/Sound",
        "/SubmitForm",
        "/Trans",
    }
)
_MAX_ACTION_CHAIN = 16


@dataclass(frozen=True)
class ValidatedHealthDocument:
    filename: str
    mime_type: str
    extension: str
    page_count: int


def _reject(
    detail: str, http_status: int = status.HTTP_422_UNPROCESSABLE_ENTITY
) -> NoReturn:
    raise HTTPException(status_code=http_status, detail=detail)


def sanitize_filename(value: str | None, extension: str) -> str:
    raw = PurePath((value or f"Dokument{extension}").replace("\\", "/")).name
    cleaned = " ".join(_SAFE_FILENAME.sub("_", raw).split()).strip(" .")
    if not cleaned:
        cleaned = f"Dokument{extension}"
    if not cleaned.lower().endswith(extension):
        cleaned = f"{cleaned[: 255 - len(extension)]}{extension}"
    return cleaned[:255]


def _resolved(value: Any) -> Any:
    try:
        return value.get_object()
    except Exception:
        return value


def _has_blocked_action(value: Any) -> bool:
    action = _resolved(value)
    for _ in range(_MAX_ACTION_CHAIN):
        if not isinstance(action, dict):
            return False
        if str(_resolved(action.get("/S"))) in _BLOCKED_ACTION_TYPES:
            return True
        action = _resolved(action.get("/Next"))
    # Laengere Ketten sind in Abrechnungs-PDFs kein legitimes Muster.
    return True


def _active_annotation_reason(annotation: Any) -> str | None:
    obj = _resolved(annotation)
    if not isinstance(obj, dict):
        # Nicht aufloesbare Annotation: nicht pruefbar, also nicht vertrauen.
        return "annotation_unresolvable"
    subtype = str(_resolved(obj.get("/Subtype")))
    if subtype in _BLOCKED_ANNOTATION_SUBTYPES:
        return f"annotation_subtype{subtype}"
    for key in ("/AA", "/FS", "/RichMediaContent"):
        if key in obj:
            return f"annotation_key{key}"
    if _has_blocked_action(obj.get("/A")):
        return f"annotation_action{_resolved(_resolved(obj.get('/A')).get('/S'))}"
    return None


def _active_content_reason(reader: PdfReader) -> str | None:
    """Nennt die Bauart, die zur Ablehnung fuehrt - oder None.

    Der Grund ist rein strukturell (Annotationstyp, Aktionstyp) und enthaelt
    nie Dokumentinhalt. Er wird geloggt, damit eine Ablehnung im Betrieb
    nachvollziehbar ist, statt nur "aktiver Inhalt" zu heissen.
    """
    root = _resolved(reader.trailer.get("/Root"))
    if not isinstance(root, dict):
        return None
    if "/AA" in root:
        return "root_additional_actions"
    # /OpenAction ist haeufig nur ein Sprungziel (Array oder /GoTo). Erst eine
    # ausfuehrende Aktion darin ist ein Grund zur Ablehnung.
    if _has_blocked_action(root.get("/OpenAction")):
        return "root_open_action"
    acro_form = _resolved(root.get("/AcroForm"))
    if isinstance(acro_form, dict):
        if "/XFA" in acro_form:
            return "acroform_xfa"
        fields = _resolved(acro_form.get("/Fields"))
        if isinstance(fields, list) and fields:
            return "acroform_fields"
    names = _resolved(root.get("/Names"))
    if isinstance(names, dict):
        for key in ("/JavaScript", "/EmbeddedFiles"):
            if key in names:
                return f"names{key}"
    for index, page in enumerate(reader.pages):
        page_obj = _resolved(page)
        if not isinstance(page_obj, dict):
            continue
        if "/AA" in page_obj:
            return f"page{index + 1}_additional_actions"
        annotations = _resolved(page_obj.get("/Annots"))
        if annotations is None:
            continue
        if not isinstance(annotations, list):
            return f"page{index + 1}_annots_not_a_list"
        for annotation in annotations:
            reason = _active_annotation_reason(annotation)
            if reason is not None:
                return f"page{index + 1}_{reason}"
    return None


def validate_health_document(
    content: bytes, filename: str | None, settings: Settings
) -> ValidatedHealthDocument:
    size = len(content)
    if size == 0:
        _reject("health_document_empty")
    if size > settings.health_document_max_bytes:
        _reject("health_document_too_large", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    if content.startswith(b"%PDF-"):
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                _reject("health_document_encrypted")
            page_count = len(reader.pages)
            if page_count < 1 or page_count > settings.health_document_max_pages:
                _reject("health_document_page_limit")
            active_reason = _active_content_reason(reader)
            if active_reason is not None:
                # Der Grund gehoert ins Log, nicht in die Antwort: er ist fuer
                # den Betrieb da, nicht fuer den Client.
                logger.info(
                    "Gesundheitsdokument abgelehnt: aktiver PDF-Inhalt (%s)", active_reason
                )
                _reject("health_document_active_content")
        except HTTPException:
            raise
        except (PdfReadError, ValueError, TypeError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="health_document_invalid_pdf",
            ) from exc
        return ValidatedHealthDocument(
            filename=sanitize_filename(filename, ".pdf"),
            mime_type="application/pdf",
            extension=".pdf",
            page_count=page_count,
        )

    signatures = (
        (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
        (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    )
    image_type = next((item for item in signatures if content.startswith(item[0])), None)
    if image_type is None:
        _reject("health_document_unsupported_type")
    _, mime_type, extension = image_type
    try:
        with Image.open(BytesIO(content)) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
                _reject("health_document_image_limit")
            image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="health_document_invalid_image",
        ) from exc
    return ValidatedHealthDocument(
        filename=sanitize_filename(filename, extension),
        mime_type=mime_type,
        extension=extension,
        page_count=1,
    )
