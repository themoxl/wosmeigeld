import io
import zipfile

import pytest

from worker.imports.security import (
    ImportSecurityError,
    check_file_size,
    check_row_count,
    extract_single_file_from_zip,
)


def test_check_file_size_rejects_empty() -> None:
    with pytest.raises(ImportSecurityError):
        check_file_size(b"")


def test_check_file_size_rejects_too_large() -> None:
    with pytest.raises(ImportSecurityError):
        check_file_size(b"x" * (21 * 1024 * 1024))


def test_check_file_size_accepts_normal_file() -> None:
    check_file_size(b"some,csv,content\n")  # darf nicht werfen


def test_check_row_count_rejects_too_many_rows() -> None:
    with pytest.raises(ImportSecurityError):
        check_row_count(50_001)


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_extract_single_file_from_zip_returns_content_and_name() -> None:
    zip_bytes = _make_zip({"statement.xml": b"<Document/>"})
    content, name = extract_single_file_from_zip(zip_bytes)
    assert content == b"<Document/>"
    assert name == "statement.xml"


def test_extract_single_file_from_zip_rejects_multiple_entries() -> None:
    zip_bytes = _make_zip({"a.xml": b"<A/>", "b.xml": b"<B/>"})
    with pytest.raises(ImportSecurityError):
        extract_single_file_from_zip(zip_bytes)


def test_extract_single_file_from_zip_rejects_invalid_zip() -> None:
    with pytest.raises(ImportSecurityError):
        extract_single_file_from_zip(b"not a zip file")
