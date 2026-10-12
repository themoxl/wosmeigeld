"""Womit gerechnet wird: Zustand der NVIDIA-GPU des KI-Rechners.

LM Studio selbst gibt darueber keine Auskunft - weder `/api/v0/models` noch
die Verwaltungsschnittstelle `/api/v1/models` nennen Geraet, Speicher oder
Auslastung (gemessen am 06.08.2026 gegen LM Studio auf 127.0.0.1:1234). Die
einzige verlaessliche Quelle ist deshalb `nvidia-smi` auf dem Rechner, auf dem
dieser Dienst laeuft.

Daraus folgt die wichtigste Einschraenkung, die auch die Oberflaeche nennt:
laeuft die API im Container ohne durchgereichte GPU, gibt es hier keine Werte,
obwohl LM Studio auf dem Host sehr wohl auf der GPU rechnet. "Keine Angabe"
ist dann die richtige Antwort - eine geratene waere schlechter als keine.

Gestartet wird `nvidia-smi` bewusst SYNCHRON in einem Thread, nicht ueber
`asyncio.create_subprocess_exec`. Grund ist eine Falle unter Windows: uvicorn
schaltet im Reload-Modus auf `WindowsSelectorEventLoopPolicy` um
(`uvicorn/loops/asyncio.py`), und der Selector-Loop unterstuetzt ueberhaupt
keine Kindprozesse - `create_subprocess_exec` wirft dort ein
`NotImplementedError` ganz ohne Text. Genau das stand vorher als
"nvidia-smi liess sich nicht starten: " mit leerer Begruendung im
Statusstreifen: die GPU war da, nur der Weg zu ihr nicht. Der Thread-Weg
funktioniert auf jedem Loop und auf jeder Plattform.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_QUERY = "name,memory.used,memory.total,utilization.gpu,temperature.gpu"
_TIMEOUT_SECONDS = 3.0
# Unter Windows sonst ein kurz aufblitzendes Konsolenfenster je Messung; auf
# POSIX ist 0 der Standardwert und damit wirkungslos.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Der Statusstreifen fragt im Sekundentakt nach, `nvidia-smi` braucht aber je
# Aufruf einen Prozessstart. Ein kurzer Cache haelt die Anzeige lebendig, ohne
# dass die Messung selbst zur Last wird.
_CACHE_SECONDS = 2.0

@dataclass(frozen=True)
class GpuReading:
    name: str
    memory_used_mb: int
    memory_total_mb: int
    utilization_percent: int
    temperature_celsius: int | None


@dataclass(frozen=True)
class GpuStatus:
    """Entweder Messwerte oder der Grund, warum es keine gibt."""

    gpus: list[GpuReading]
    detail: str | None


_cache: tuple[float, GpuStatus] | None = None


def _parse(output: str) -> list[GpuReading]:
    readings: list[GpuReading] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4 or not parts[0]:
            continue
        try:
            readings.append(
                GpuReading(
                    name=parts[0],
                    memory_used_mb=int(float(parts[1])),
                    memory_total_mb=int(float(parts[2])),
                    utilization_percent=int(float(parts[3])),
                    temperature_celsius=(
                        int(float(parts[4])) if len(parts) > 4 and parts[4].isdigit() else None
                    ),
                )
            )
        except ValueError:
            # "[N/A]" statt einer Zahl kommt bei virtualisierten Geraeten vor.
            continue
    return readings


def _reason(exc: BaseException) -> str:
    """Nie eine leere Begruendung: `NotImplementedError()` traegt keinen Text,
    und "liess sich nicht starten: " ohne Fortsetzung ist keine Auskunft."""
    return str(exc).strip() or exc.__class__.__name__


def _read_nvidia_smi(executable: str) -> GpuStatus:
    """Synchroner Teil - laeuft im Thread, siehe Moduldoc."""
    try:
        completed = subprocess.run(  # noqa: S603 - fester Pfad aus shutil.which
            [executable, f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return GpuStatus(gpus=[], detail="nvidia-smi hat nicht rechtzeitig geantwortet.")
    except Exception as exc:  # noqa: BLE001 - eine Statusanzeige darf nie werfen
        logger.debug("nvidia-smi nicht ausfuehrbar", exc_info=True)
        return GpuStatus(gpus=[], detail=f"nvidia-smi liess sich nicht starten: {_reason(exc)}")

    if completed.returncode != 0:
        message = (
            completed.stderr.decode(errors="replace").strip()
            or f"Exitcode {completed.returncode}"
        )
        return GpuStatus(gpus=[], detail=f"nvidia-smi meldete einen Fehler: {message[:200]}")

    readings = _parse(completed.stdout.decode(errors="replace"))
    if not readings:
        return GpuStatus(gpus=[], detail="nvidia-smi lieferte keine lesbaren Werte.")
    return GpuStatus(gpus=readings, detail=None)


async def _run_nvidia_smi() -> GpuStatus:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return GpuStatus(
            gpus=[],
            detail=(
                "nvidia-smi ist auf diesem Rechner nicht verfuegbar. Laeuft die API "
                "im Container, sagt das nichts ueber die GPU des Hosts aus."
            ),
        )
    return await asyncio.to_thread(_read_nvidia_smi, executable)


async def probe(*, now: float | None = None) -> GpuStatus:
    """Aktueller GPU-Zustand, hoechstens alle `_CACHE_SECONDS` frisch gemessen."""
    global _cache
    current = time.monotonic() if now is None else now
    if _cache is not None and current - _cache[0] < _CACHE_SECONDS:
        return _cache[1]
    status = await _run_nvidia_smi()
    _cache = (current, status)
    return status


def reset_cache() -> None:
    """Nur fuer Tests: erzwingt eine frische Messung."""
    global _cache
    _cache = None
