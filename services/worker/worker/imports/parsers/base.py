from dataclasses import dataclass
from typing import Protocol

from worker.imports.canonical import CanonicalTransaction


@dataclass
class DetectionResult:
    matched: bool
    format: str
    confidence: float
    reason: str = ""


class ImportParser(Protocol):
    format_name: str

    def detect(self, file_bytes: bytes, filename: str) -> DetectionResult: ...

    def parse(self, file_bytes: bytes) -> list[CanonicalTransaction]: ...
