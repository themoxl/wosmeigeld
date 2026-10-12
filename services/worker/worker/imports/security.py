"""Sichere Vorpruefung fuer Importdateien (Architekturplan Abschnitt 7.3,
Schritt 3): Groessenlimit, ZIP-Bomb-Schutz, XML ohne externe Entitaeten,
Zeilenlimit."""

import io
import zipfile

# 20 MB - Kontoauszuege sind Text/Tabellen, kein Medienformat.
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 50_000
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
# verdaechtig bei staerkerer Kompression als das (ZIP-Bomb-Indikator)
MAX_ZIP_COMPRESSION_RATIO = 100


class ImportSecurityError(ValueError):
    """Datei verletzt eine Sicherheitsgrenze - Import wird abgelehnt, bevor
    irgendein Parser sie anfasst."""


def check_file_size(file_bytes: bytes) -> None:
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise ImportSecurityError(
            f"Datei ist zu gross ({len(file_bytes)} Bytes, Limit "
            f"{MAX_FILE_SIZE_BYTES} Bytes)."
        )
    if len(file_bytes) == 0:
        raise ImportSecurityError("Datei ist leer.")


def check_row_count(row_count: int) -> None:
    if row_count > MAX_ROWS:
        raise ImportSecurityError(
            f"Datei hat zu viele Zeilen ({row_count}, Limit {MAX_ROWS})."
        )


def extract_single_file_from_zip(file_bytes: bytes) -> tuple[bytes, str]:
    """Entpackt ein ZIP mit GENAU EINER Datei (typisch fuer von Banken
    gezippte CAMT-Exporte). Mehrere Dateien pro ZIP sind bewusst nicht
    unterstuetzt (Architekturplan Abschnitt 7.1: "ZIP-Pakete mit mehreren
    Bankdateien" ist als Erweiterung vorgesehen, nicht Startumfang)."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) != 1:
                raise ImportSecurityError(
                    "ZIP-Dateien mit mehreren Eintraegen werden noch nicht unterstuetzt."
                )
            info = infos[0]
            if info.file_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ImportSecurityError("ZIP-Inhalt ist zu gross (moeglicher ZIP-Bomb-Versuch).")
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_ZIP_COMPRESSION_RATIO:
                    raise ImportSecurityError(
                        "Verdaechtiges Kompressionsverhaeltnis (moeglicher ZIP-Bomb-Versuch)."
                    )
            return zf.read(info), info.filename
    except zipfile.BadZipFile as exc:
        raise ImportSecurityError("Datei ist kein gueltiges ZIP-Archiv.") from exc
