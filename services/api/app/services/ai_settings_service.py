"""Wirksame KI-Einstellungen eines Haushalts - Sicht und Aenderung fuer die API.

Die Aufloesung selbst steht im KI-Kern (app/llm/settings.py) und wird von API
und Worker gleich benutzt. Dieses Modul ergaenzt, was nur die API braucht:
die Sicht fuer die Oberflaeche (Wert, Herkunft, Spaltenvorgabe) und die
Teilaenderung samt Zuruecksetzen.

Vorgaben stehen ausschliesslich im Datenbankschema. Jeder Haushalt hat genau
eine Einstellungszeile (Trigger beim Anlegen des Haushalts); "Zuruecksetzen"
heisst deshalb: die Spalte auf ihre Schemavorgabe (SQL DEFAULT) setzen -
NULL, wo die Spalte keine hat (z. B. Modell = automatisch).
"""

from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import keys, providers
from app.llm import settings as llm_settings
from app.llm.settings import (
    DEFAULT_MIN_P,
    DEFAULT_PRESENCE_PENALTY,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    LLMSettings,
    SettingsRowMissingError,
)
from app.schemas.ai import (
    AIKeyStatus,
    AIProviderInfo,
    AISettings,
    AISettingsUpdate,
    ProviderId,
    SamplingMode,
    SettingSource,
)

__all__ = [
    "DEFAULT_MIN_P",
    "DEFAULT_PRESENCE_PENALTY",
    "DEFAULT_REPEAT_PENALTY",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TOP_K",
    "DEFAULT_TOP_P",
    "ResolvedAISettings",
    "SettingsRowMissingError",
    "get_settings_view",
    "key_status",
    "provider_catalog",
    "remove_key",
    "resolve",
    "store_key",
    "update",
]

# Die wirksamen Einstellungen SIND die des Kerns. Der Name bleibt fuer die
# Aufrufer der API; ein eigenes Feld (frueher das Uebergangsbudget
# max_output_tokens) gibt es nicht mehr.
ResolvedAISettings = LLMSettings


# Feste Spaltenliste statt der Payload-Schluessel, damit kein Feldname
# ungeprueft in ein SET-Fragment wandert (gleiches Muster wie routes/rules.py).
_UPDATABLE: Final[tuple[str, ...]] = (
    "is_enabled",
    "provider",
    "auto_categorize_min_confidence",
    "custom_base_url",
    "model_name",
    "temperature",
    "sampling_mode",
    "top_p",
    "top_k",
    "repeat_penalty",
    "min_p",
    "presence_penalty",
    "request_timeout_seconds",
    "context_length",
    "reasoning",
    "searxng_base_url",
    "context_cap_tokens",
    "answer_reserve_share",
    "result_page_share",
    "run_time_limit_minutes",
    "start_chars_per_token",
    "start_estimate_error",
    "context_line_enabled",
    "assistant_retention_days",
    "legacy_max_output_tokens",
)

# Payload-Feld -> Spalte, wo die Namen abweichen.
_PAYLOAD_COLUMN: Final[dict[str, str]] = {"base_url": "custom_base_url"}


def _source(value: Any) -> SettingSource:
    return "household" if value is not None else "environment"


def _sampling_mode(value: Any) -> SamplingMode:
    return "manual" if value == "manual" else "model"


def _default_source(value: Any, default: Any) -> SettingSource:
    """"default", solange der Wert der Spaltenvorgabe entspricht."""
    if value is None or default is None:
        return "household" if value is not None else "default"
    try:
        return "default" if float(value) == float(default) else "household"
    except (TypeError, ValueError):
        return "default" if value == default else "household"


async def resolve(session: AsyncSession, household_id: UUID) -> ResolvedAISettings:
    """Einstellungen, wie jeder Modellaufruf sie braucht (siehe app/llm)."""
    return await llm_settings.load_settings(session, household_id)


def provider_catalog() -> list[AIProviderInfo]:
    """Die waehlbaren Anbieter - genau die Liste aus app/llm/providers.py."""
    return [
        AIProviderInfo(
            id=_provider_id(entry.id),
            name=entry.name,
            url=entry.url,
            key_required=entry.key == "required",
            leaves_house=entry.leaves_house,
            model_required=entry.model_required,
            hint=entry.hint,
        )
        for entry in providers.PROVIDERS
    ]


def _provider_id(value: str) -> ProviderId:
    return "anthropic" if value == providers.ANTHROPIC else "local"


def _key_target(provider_id: str) -> str:
    """Ziel, an das der Schluessel des Anbieters gebunden ist. Wirft
    ProviderError fuer einen Anbieter ohne Schluessel."""
    entry = providers.provider(provider_id)
    if entry.key == "none" or entry.url is None:
        raise providers.ProviderError(f"{entry.name} braucht keinen API-Schlüssel.")
    return providers.target_of(entry.url)


async def key_status(session: AsyncSession, household_id: UUID, provider_id: str) -> AIKeyStatus:
    """Stand des Schluessels - nie der Wert. Ein hinterlegter, aber nicht
    lesbarer Schluessel traegt den Grund in `problem`."""
    target = _key_target(provider_id)
    status = await keys.key_status(session, household_id, target)
    problem: str | None = None
    if status.stored:
        try:
            await keys.load_key(session, household_id, target)
        except keys.ProviderKeyError as exc:
            problem = str(exc)
    return AIKeyStatus(
        provider=_provider_id(provider_id),
        target=providers.readable_target(target),
        stored=status.stored,
        ending=status.ending,
        since=status.since,
        problem=problem,
    )


async def store_key(
    session: AsyncSession, household_id: UUID, provider_id: str, raw: str
) -> AIKeyStatus:
    await keys.store_key(session, household_id, _key_target(provider_id), raw)
    return await key_status(session, household_id, provider_id)


async def remove_key(session: AsyncSession, household_id: UUID, provider_id: str) -> AIKeyStatus:
    await keys.remove_key(session, household_id, _key_target(provider_id))
    return await key_status(session, household_id, provider_id)


async def get_settings_view(session: AsyncSession, household_id: UUID) -> AISettings:
    """Dieselben Werte fuer die Oberflaeche - zusaetzlich mit Herkunft und
    Spaltenvorgabe je Feld."""
    row = await llm_settings.load_settings_row(session, household_id)
    core = llm_settings.settings_from_row(row)
    defaults = await llm_settings.schema_defaults(session)
    provider_keys = [
        await key_status(session, household_id, entry.id)
        for entry in providers.PROVIDERS
        if entry.key != "none" and entry.url is not None
    ]
    return AISettings(
        is_enabled=core.is_enabled,
        auto_categorize_min_confidence=core.min_confidence,
        provider=_provider_id(core.provider),
        providers=provider_catalog(),
        provider_keys=provider_keys,
        base_url=row["custom_base_url"] or get_settings().lm_studio_base_url,
        base_url_source=_source(row["custom_base_url"]),
        model_name=row["model_name"],
        # Ohne ausdrueckliche Wahl bestimmt die Discovery das Modell.
        model_source="household" if row["model_name"] else "automatic",
        temperature=core.temperature,
        temperature_source=("household" if row["temperature"] is not None else "automatic"),
        sampling_mode=_sampling_mode(row["sampling_mode"]),
        sampling_mode_source=("household" if row["sampling_mode"] is not None else "automatic"),
        top_p=core.top_p,
        top_p_source=("household" if row["top_p"] is not None else "automatic"),
        top_k=core.top_k,
        top_k_source=("household" if row["top_k"] is not None else "automatic"),
        repeat_penalty=core.repeat_penalty,
        repeat_penalty_source=("household" if row["repeat_penalty"] is not None else "automatic"),
        min_p=core.min_p,
        min_p_source=("household" if row["min_p"] is not None else "automatic"),
        presence_penalty=core.presence_penalty,
        presence_penalty_source=(
            "household" if row["presence_penalty"] is not None else "automatic"
        ),
        request_timeout_seconds=int(core.request_timeout_seconds),
        request_timeout_seconds_source=_default_source(
            row["request_timeout_seconds"], defaults.get("request_timeout_seconds")
        ),
        context_length=core.context_length,
        context_length_source=_source(row["context_length"]),
        reasoning=row["reasoning"],
        reasoning_source=_source(row["reasoning"]),
        searxng_base_url=row["searxng_base_url"],
        searxng_base_url_source=_source(row["searxng_base_url"]),
        context_cap_tokens=core.context_cap_tokens,
        answer_reserve_share=_required(core.answer_reserve_share, "answer_reserve_share"),
        result_page_share=_required(core.result_page_share, "result_page_share"),
        run_time_limit_minutes=int(
            _required(core.run_time_limit_minutes, "run_time_limit_minutes")
        ),
        start_chars_per_token=_required(core.start_chars_per_token, "start_chars_per_token"),
        start_estimate_error=_required(core.start_estimate_error, "start_estimate_error"),
        context_line_enabled=bool(core.context_line_enabled),
        assistant_retention_days=int(
            _required(row.get("assistant_retention_days"), "assistant_retention_days")
        ),
        legacy_max_output_tokens=row.get("legacy_max_output_tokens"),
        schema_defaults=defaults,
    )


def _required(value: float | int | None, column: str) -> float:
    """NOT-NULL-Spalten mit Schemavorgabe - leer heisst: Schema nicht angewendet."""
    if value is None:
        raise SettingsRowMissingError(
            f"ai_household_settings.{column} ist leer - das Schema ist nicht angewendet."
        )
    return float(value)


async def update(
    session: AsyncSession, household_id: UUID, payload: AISettingsUpdate
) -> AISettings:
    """Teilaenderung mit ausdruecklichem Zuruecksetzen auf die Schemavorgabe."""
    changes: dict[str, Any] = {}
    data = payload.model_dump(exclude_unset=True, exclude={"clear_fields"})
    for key, value in data.items():
        if value is None:
            continue
        changes[_PAYLOAD_COLUMN.get(key, key)] = value

    # Zuruecksetzen zuletzt: so gewinnt ein ausdrueckliches clear_fields gegen
    # einen im selben Request mitgesendeten Wert, statt von der Reihenfolge im
    # JSON abzuhaengen.
    reset = {field for field in payload.clear_fields}
    for field in reset:
        changes.pop(field, None)

    columns = [column for column in _UPDATABLE if column in changes]
    reset_columns = [column for column in _UPDATABLE if column in reset]
    if not columns and not reset_columns:
        return await get_settings_view(session, household_id)

    # SQL DEFAULT setzt die Spaltenvorgabe des Schemas bzw. NULL, wo es
    # keine gibt - die Vorgabe steht damit nur an einer Stelle.
    assignments = [f"{column} = :{column}" for column in columns]
    assignments += [f"{column} = default" for column in reset_columns]
    params: dict[str, Any] = {"household_id": str(household_id)}
    params.update({column: changes[column] for column in columns})

    # Die Zeile existiert immer (Trigger); das insert ... do nothing faengt
    # nur einen Haushalt ab, der vor dem Trigger ohne Zeile entstanden ist
    # und vom Backfill nicht erreicht wurde.
    await session.execute(
        text(
            "insert into ai_household_settings (household_id) values (:household_id) "
            "on conflict (household_id) do nothing"
        ),
        {"household_id": str(household_id)},
    )
    await session.execute(
        text(
            "update ai_household_settings set "
            + ", ".join(assignments)
            + ", updated_at = now() where household_id = :household_id"
        ),
        params,
    )
    return await get_settings_view(session, household_id)
