"""Messung je Modell: Zeichen je Token und Schaetzabstand (ai_model_measurements).

Die Budgetrechnung (app/llm/budget.py) braucht zwei Zahlen, die vom Modell
abhaengen: wie viele Zeichen ein Token im Schnitt sind, und wie weit diese
Schaetzung danebenliegt. Beides wird aus den usage.prompt_tokens echter
Aufrufe gelernt. Bis zur ersten Messung gelten die Startwerte aus den
Haushaltseinstellungen (start_chars_per_token, start_estimate_error) - sie
sind sichtbar und haben ihre Vorgabe im Schema (E3).

Verbindungstest und Kanarienvogel messen NICHT: bei 18 Token ueberwiegt der
Rahmen der Chatvorlage, das Verhaeltnis waere falsch.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.budget import MeasurementSums
from app.llm.settings import LLMSettings

_SELECT_SQL: Final = text(
    "select sum_chars, sum_prompt_tokens, sum_estimate_error, sum_checked_tokens, calls, "
    "largest_need_tokens, measured_at from ai_model_measurements "
    "where household_id = :household_id and server_target = :target and model_key = :model"
)

# Fortschreiben in SQL, damit zwei gleichzeitige Aufrufe keine Messung
# verlieren. Die Ausdruecke im UPDATE-Teil sehen die ALTE Zeile - genau das
# braucht der Schaetzfehler: er vergleicht die Schaetzung mit dem bisherigen
# Verhaeltnis (vor diesem Aufruf) mit der echten Zahl. Identische Rechnung wie
# budget.update_measurement().
_UPSERT_SQL: Final = text(
    "insert into ai_model_measurements "
    "(household_id, server_target, model_key, sum_chars, sum_prompt_tokens, "
    "sum_estimate_error, sum_checked_tokens, calls, largest_need_tokens, measured_at) "
    "values (:household_id, :target, :model, :chars, :prompt, 0, 0, 1, :need, now()) "
    "on conflict (household_id, server_target, model_key) do update set "
    "sum_estimate_error = ai_model_measurements.sum_estimate_error + case "
    "  when ai_model_measurements.sum_prompt_tokens > 0 and ai_model_measurements.sum_chars > 0 "
    "  then abs(:chars / (ai_model_measurements.sum_chars::double precision "
    "            / ai_model_measurements.sum_prompt_tokens) - :prompt) "
    "  else 0 end, "
    "sum_checked_tokens = ai_model_measurements.sum_checked_tokens + case "
    "  when ai_model_measurements.sum_prompt_tokens > 0 and ai_model_measurements.sum_chars > 0 "
    "  then :prompt else 0 end, "
    "sum_chars = ai_model_measurements.sum_chars + :chars, "
    "sum_prompt_tokens = ai_model_measurements.sum_prompt_tokens + :prompt, "
    "calls = ai_model_measurements.calls + 1, "
    "largest_need_tokens = greatest(ai_model_measurements.largest_need_tokens, :need), "
    "measured_at = now()"
)


def server_target_of(base_url: str) -> str:
    """`http://127.0.0.1:1234/v1` -> `http://127.0.0.1:1234`.

    Schluessel der Messung: dasselbe Modell auf einem anderen Server kann eine
    andere Chatvorlage haben. Der Port wird ausgeschrieben, auch der
    Standardport, damit http://h und http://h:80 nicht zwei Messungen sind."""
    parts = urlsplit(base_url.strip())
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return f"{scheme}://{host}:{port}"


@dataclass(frozen=True)
class Measurement:
    """Was die Budgetrechnung fuer ein Modell verwendet.

    chars_per_token/estimate_error sind None, wenn weder eine Messung noch
    ein Startwert vorliegt - dann wirft die Budgetrechnung."""

    chars_per_token: float | None
    estimate_error: float | None
    # True: das Verhaeltnis ist noch der Startwert aus der Einstellung.
    is_start_value: bool
    # True: der Schaetzabstand ist noch der Startwert (er braucht mindestens
    # zwei gemessene Aufrufe).
    error_is_start_value: bool
    calls: int = 0
    largest_need_tokens: int | None = None
    measured_at: datetime | None = None
    sums: MeasurementSums | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chars_per_token": self.chars_per_token,
            "estimate_error": self.estimate_error,
            "is_start_value": self.is_start_value,
            "error_is_start_value": self.error_is_start_value,
            "calls": self.calls,
            "largest_need_tokens": self.largest_need_tokens,
        }


def start_measurement(settings: LLMSettings) -> Measurement:
    """Startwerte aus den Einstellungen - noch keine Messung."""
    return Measurement(
        chars_per_token=settings.start_chars_per_token,
        estimate_error=settings.start_estimate_error,
        is_start_value=True,
        error_is_start_value=True,
    )


def measurement_from_sums(
    sums: MeasurementSums | None, settings: LLMSettings, measured_at: datetime | None = None
) -> Measurement:
    """Summen -> Messung; was noch nicht gemessen ist, kommt aus den
    Startwerten (sichtbar markiert)."""
    if sums is None:
        return start_measurement(settings)
    ratio = sums.chars_per_token
    error = sums.estimate_error
    return Measurement(
        chars_per_token=ratio if ratio is not None else settings.start_chars_per_token,
        estimate_error=error if error is not None else settings.start_estimate_error,
        is_start_value=ratio is None,
        error_is_start_value=error is None,
        calls=sums.calls,
        largest_need_tokens=sums.largest_need_tokens,
        measured_at=measured_at,
        sums=sums,
    )


async def load_measurement(
    session: AsyncSession,
    household_id: UUID,
    base_url: str,
    model: str,
    settings: LLMSettings,
) -> Measurement:
    """Messung des Modells auf diesem Server, sonst die Startwerte."""
    row = (
        await session.execute(
            _SELECT_SQL,
            {
                "household_id": str(household_id),
                "target": server_target_of(base_url),
                "model": model,
            },
        )
    ).first()
    if row is None:
        return start_measurement(settings)
    sums = MeasurementSums(
        sum_chars=int(row.sum_chars),
        sum_prompt_tokens=int(row.sum_prompt_tokens),
        sum_estimate_error=float(row.sum_estimate_error),
        sum_checked_tokens=int(row.sum_checked_tokens),
        calls=int(row.calls),
        largest_need_tokens=(
            int(row.largest_need_tokens) if row.largest_need_tokens is not None else None
        ),
    )
    return measurement_from_sums(sums, settings, row.measured_at)


async def record_measurement(
    session: AsyncSession,
    household_id: UUID,
    base_url: str,
    model: str,
    *,
    chars: int,
    prompt_tokens: int,
    need_tokens: int | None = None,
) -> None:
    """Messung nach einem Aufruf fortschreiben (in SQL inkrementiert).

    Ohne Zeichen oder ohne gemeldete prompt_tokens gibt es nichts zu messen."""
    if chars <= 0 or prompt_tokens <= 0:
        return
    await session.execute(
        _UPSERT_SQL,
        {
            "household_id": str(household_id),
            "target": server_target_of(base_url),
            "model": model,
            "chars": chars,
            "prompt": prompt_tokens,
            "need": need_tokens if need_tokens and need_tokens > 0 else None,
        },
    )
