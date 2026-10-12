"""Optimistische Nebenlaeufigkeit ueber ETag/If-Match.

Buchungen tragen dieses Muster seit Phase 9 in `routes/transactions.py`, dort
allerdings mit optionalem `expected_version` im Body. Die Kategorienverwaltung
verlangt `If-Match` verpflichtend (Plan: "ein fehlender Header liefert 428,
ein veralteter Stand 409"), weshalb die Parserlogik hier einmal zentral liegt
statt in jeder Route erneut.
"""

from typing import Final

from fastapi import HTTPException, status

INVALID_IF_MATCH: Final = "invalid_if_match"
IF_MATCH_REQUIRED: Final = "if_match_required"
VERSION_CONFLICT: Final = "version_conflict"


def entity_etag(version: int) -> str:
    """ETag-Darstellung einer Entitaetsversion - identisch zum Format in
    `routes/transactions.py`, damit Clients nur einen Parser brauchen."""
    return f'"{version}"'


def parse_if_match(if_match: str | None, *, required: bool = True) -> int | None:
    """Liest die erwartete Version aus dem If-Match-Header.

    Akzeptiert `"3"`, `W/"3"` und `3`. Ein fehlender Header liefert bei
    `required=True` 428 Precondition Required - bewusst nicht 400: der Client
    hat nichts falsch formuliert, ihm fehlt nur die Vorbedingung."""
    if if_match is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail=IF_MATCH_REQUIRED,
            )
        return None

    candidate = if_match.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:].strip()
    candidate = candidate.strip('"')
    if not candidate.isdigit() or int(candidate) < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_IF_MATCH
        )
    return int(candidate)


def raise_version_conflict(current_version: int) -> None:
    """409 mit dem aktuellen Stand als ETag, damit der Client ohne zweiten
    Request neu aufsetzen kann."""
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=VERSION_CONFLICT,
        headers={"ETag": entity_etag(current_version)},
    )
