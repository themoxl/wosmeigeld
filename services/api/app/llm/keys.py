"""API-Schluessel der KI-Anbieter: verschluesselt je Haushalt und Ziel.

Vorbild ist die Schluesselablage des Audit Companion (ai/providers.py): ein
Schluessel ist an das Ziel seiner Adresse gebunden (scheme://host:port) und
geht nie an ein anderes; die Oberflaeche erfaehrt nur "hinterlegt (...abcd)".

Anders als dort (Windows-DPAPI je Rechner) laeuft der Finanzplaner auf einem
Server fuer mehrere Mitglieder. Deshalb:

* Gespeichert wird in ai_provider_keys nur der Geheimtext (AES-GCM). Der
  Schluessel zum Entschluesseln wird mit HKDF aus SUPABASE_JWT_SECRET
  abgeleitet und liegt nie in der Datenbank - wer das JWT-Geheimnis hat, kann
  ohnehin jede Sitzung faelschen; ein zweites Geheimnis waere nur eine zweite
  Stelle, an der etwas fehlen kann.
* Haushalt und Ziel sind als zusaetzliche Daten in die Verschluesselung
  eingebunden: ein Geheimtext laesst sich nicht in eine andere Zeile kopieren.
* Lesen duerfen alle Mitglieder (RLS), schreiben nur der Owner. Wird
  SUPABASE_JWT_SECRET erneuert, ist ein hinterlegter Schluessel nicht mehr
  lesbar - das wird als Grund gemeldet ("bitte neu hinterlegen"), nie still
  uebergangen.

Kein FastAPI, kein app.services: der Worker laedt dieses Modul mit dem Kern.
"""

import base64
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.providers import readable_target

# Laenge des AES-GCM-Nonce in Byte (Vorgabe des Verfahrens, 96 Bit) und des
# abgeleiteten Schluessels (AES-256). Keine KI-Parameter.
_NONCE_BYTES: Final = 12
_KEY_BYTES: Final = 32
_INFO: Final = b"finanzplaner/ki-anbieter-schluessel/v1"

# Ab dieser Laenge zeigt die Oberflaeche die letzten Zeichen ("...abcd") -
# bei kuerzeren Werten verriete das zu viel vom Schluessel.
_SHOW_ENDING_FROM: Final = 16


class ProviderKeyError(ValueError):
    """Schluessel fehlt, ist unbrauchbar oder nicht lesbar - der Text sagt, was zu tun ist."""


class ApiKey:
    """Ein entschluesselter API-Schluessel.

    Absichtlich keine dataclass: repr() und str() zeigen nie den Wert, damit
    er weder in einem Log noch in einer Fehlermeldung landet. Den Wert gibt
    nur reveal() heraus - an genau einer Stelle, beim Bau des Clients."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    @property
    def ending(self) -> str | None:
        return ending_of(self._value)

    def mask(self, message: str) -> str:
        """Ein Dienst, der den Schluessel in seiner Fehlermeldung wiederholt,
        bekommt ihn hier ersetzt - bevor die Meldung in Protokoll oder
        Oberflaeche landet."""
        if not self._value or self._value not in message:
            return message
        return message.replace(self._value, self.ending or "…")

    def __repr__(self) -> str:
        return "ApiKey(…)"

    __str__ = __repr__


@dataclass(frozen=True)
class KeyStatus:
    """Was die Oberflaeche ueber einen Schluessel erfaehrt - nie den Wert."""

    target: str
    stored: bool
    ending: str | None = None
    since: datetime | None = None


def ending_of(value: str) -> str | None:
    if len(value) < _SHOW_ENDING_FROM:
        return None
    return "…" + value[-4:]  # fest: Anzeige der letzten vier Zeichen, kein Promptmaterial


def check_key(raw: str) -> str:
    """Der Schluessel geht als Kopfzeile hinaus: ein mitkopierter
    Zeilenumbruch oder ein Umlaut darf nie dorthin gelangen."""
    key = str(raw or "").strip()
    if not key:
        raise ProviderKeyError("Kein API-Schlüssel eingegeben – zum Entfernen „Entfernen“ wählen.")
    if not key.isascii() or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise ProviderKeyError(
            "Der API-Schlüssel enthält Leer-, Steuer- oder Sonderzeichen – beim Einfügen ist "
            "vermutlich etwas mitgekommen."
        )
    return key


def _cipher() -> AESGCM:
    secret = get_settings().supabase_jwt_secret
    if not secret:
        raise ProviderKeyError(
            "Auf dem Server fehlt SUPABASE_JWT_SECRET – ohne dieses Geheimnis lässt sich kein "
            "API-Schlüssel verschlüsseln oder lesen."
        )
    derived = HKDF(algorithm=hashes.SHA256(), length=_KEY_BYTES, salt=None, info=_INFO).derive(
        secret.encode("utf-8")
    )
    return AESGCM(derived)


def _associated(household_id: UUID, target: str) -> bytes:
    return f"{household_id}|{target}".encode()


def encrypt(household_id: UUID, target: str, key: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    sealed = _cipher().encrypt(nonce, key.encode("utf-8"), _associated(household_id, target))
    return base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(household_id: UUID, target: str, blob: str) -> str:
    # Ausserhalb des try: ein fehlendes Servergeheimnis ist ein eigener Grund,
    # kein "beschaedigter Schluessel".
    cipher = _cipher()
    try:
        raw = base64.b64decode(blob, validate=True)
        opened = cipher.decrypt(
            raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _associated(household_id, target)
        )
        return opened.decode("utf-8")
    except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
        raise ProviderKeyError(
            f"Der API-Schlüssel für {readable_target(target)} ist nicht lesbar (beschädigt oder "
            "das Servergeheimnis wurde erneuert) – bitte unter Einstellungen › KI neu hinterlegen."
        ) from exc


_SELECT_SQL: Final = text(
    "select ciphertext, key_ending, created_at from ai_provider_keys "
    "where household_id = :household_id and target = :target"
)

_UPSERT_SQL: Final = text(
    "insert into ai_provider_keys (household_id, target, ciphertext, key_ending, created_at) "
    "values (:household_id, :target, :ciphertext, :ending, now()) "
    "on conflict (household_id, target) do update set ciphertext = excluded.ciphertext, "
    "key_ending = excluded.key_ending, created_at = now(), created_by = auth.uid()"
)

_DELETE_SQL: Final = text(
    "delete from ai_provider_keys where household_id = :household_id and target = :target"
)


async def key_status(session: AsyncSession, household_id: UUID, target: str) -> KeyStatus:
    """Ist fuer das Ziel ein Schluessel hinterlegt? Nie der Schluessel selbst."""
    row = (
        await session.execute(_SELECT_SQL, {"household_id": str(household_id), "target": target})
    ).first()
    if row is None:
        return KeyStatus(target=target, stored=False)
    return KeyStatus(target=target, stored=True, ending=row.key_ending, since=row.created_at)


async def load_key(session: AsyncSession, household_id: UUID, target: str) -> ApiKey | None:
    """Der Schluessel fuer genau dieses Ziel - oder keiner. Ein nicht lesbarer
    Schluessel wirft ProviderKeyError (mit Abhilfe), statt als "keiner" zu
    gelten."""
    row = (
        await session.execute(_SELECT_SQL, {"household_id": str(household_id), "target": target})
    ).first()
    if row is None:
        return None
    return ApiKey(decrypt(household_id, target, str(row.ciphertext)))


async def store_key(
    session: AsyncSession, household_id: UUID, target: str, raw: str
) -> KeyStatus:
    key = check_key(raw)
    await session.execute(
        _UPSERT_SQL,
        {
            "household_id": str(household_id),
            "target": target,
            "ciphertext": encrypt(household_id, target, key),
            "ending": ending_of(key),
        },
    )
    return await key_status(session, household_id, target)


async def remove_key(session: AsyncSession, household_id: UUID, target: str) -> KeyStatus:
    await session.execute(_DELETE_SQL, {"household_id": str(household_id), "target": target})
    return await key_status(session, household_id, target)
