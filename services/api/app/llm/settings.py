"""Wirksame KI-Einstellungen eines Haushalts - EINE Quelle fuer API und Worker.

Frueher gab es zwei Kopien (app/services/ai_settings_service.py und
worker/ai/classifier.py), und beide fielen bei leeren Feldern auf Werte aus
dem Code oder der Umgebung zurueck (AI_MAX_TOKENS=4000). Beides ist vorbei:

* Jeder Haushalt hat genau eine Zeile in ai_household_settings (Trigger nach
  dem Anlegen des Haushalts, Backfill fuer den Bestand). Fehlt sie trotzdem,
  ist das ein Fehler und wird so gemeldet (SettingsRowMissingError) - kein
  "dann eben die Vorgabe aus dem Code".
* Die Vorgaben stehen ausschliesslich als Spaltenvorgaben im Schema
  (supabase/migrations/0001_schema.sql). schema_defaults() liest sie aus
  information_schema, damit die Oberflaeche "Vorgabe: 25 %" anzeigen und
  zuruecksetzen kann.

Die Budgetfelder (answer_reserve_share, start_chars_per_token, ...) sind im
Dataclass optional: von Hand gebaute Einstellungen (Tests, Werkzeuge) ohne
diese Werte fuehren im Gateway zu model_settings_invalid statt zu einer
erfundenen Zahl.

Dieses Modul importiert bewusst weder app.services noch FastAPI - der Worker
laedt es direkt (Grenztest tests/test_llm_kern_grenze.py).
"""

from dataclasses import dataclass, field, fields, replace
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import keys, providers
from app.llm.profiles import matching_profile

# Neutrale Startwerte fuer den MANUELLEN Sampling-Modus (unveraendert aus
# ai_settings_service uebernommen). Im Modellmodus werden sie nicht gesendet:
# dort gilt das sichtbare Herstellerprofil oder der Serverstandard.
DEFAULT_TEMPERATURE: Final = 1.0
DEFAULT_TOP_P: Final = 0.95
DEFAULT_TOP_K: Final = 64
DEFAULT_REPEAT_PENALTY: Final = 1.0
DEFAULT_MIN_P: Final = 0.0
DEFAULT_PRESENCE_PENALTY: Final = 0.0

# Alle Spalten, die der Kern liest. legacy_max_output_tokens wird nur fuer den
# einmaligen Hinweis in den Einstellungen mitgelesen und nirgends verwendet;
# assistant_retention_days nur fuer die Einstellungsansicht (aufgeraeumt wird
# in app/assistant/maintenance.py, aufgerufen vom Worker).
SETTINGS_COLUMNS: Final[tuple[str, ...]] = (
    "is_enabled",
    "auto_categorize_min_confidence",
    "custom_base_url",
    "model_name",
    "legacy_max_output_tokens",
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
    "provider",
)

_SELECT_SQL: Final = text(
    "select "
    + ", ".join(SETTINGS_COLUMNS)
    + " from ai_household_settings where household_id = :household_id"
)

_DEFAULTS_SQL: Final = text(
    "select column_name, column_default from information_schema.columns "
    "where table_schema = 'public' and table_name = 'ai_household_settings'"
)


class SettingsRowMissingError(RuntimeError):
    """Der Haushalt hat keine Zeile in ai_household_settings.

    Der Trigger households_ai_settings_row legt sie beim Anlegen des
    Haushalts an. Fehlt sie, ist das Schema nicht angewendet - das soll
    sichtbar scheitern, statt mit Code-Vorgaben weiterzulaufen."""


@dataclass(frozen=True)
class LLMSettings:
    """Was ein Modellaufruf von den Haushaltseinstellungen braucht."""

    is_enabled: bool
    min_confidence: float
    custom_base_url: str | None
    model_name: str | None
    temperature: float
    request_timeout_seconds: float
    # model = sichtbares Herstellerprofil bzw. Serverstandard; manual = exakt
    # die sechs folgenden Werte senden.
    sampling_mode: str = "model"
    top_p: float = DEFAULT_TOP_P
    top_k: int = DEFAULT_TOP_K
    repeat_penalty: float = DEFAULT_REPEAT_PENALTY
    min_p: float = DEFAULT_MIN_P
    presence_penalty: float = DEFAULT_PRESENCE_PENALTY
    # Wunschfenster beim LADEN in LM Studio (POST /api/v1/models/load). Nicht
    # zu verwechseln mit context_cap_tokens (E7).
    context_length: int | None = None
    # None = Modellstandard; sonst on/off oder eine Effort-Stufe.
    reasoning: str | None = None
    searxng_base_url: str | None = None
    # --- Relatives Budget (Vorgaben nur im Schema) ------------------------
    context_cap_tokens: int | None = None
    answer_reserve_share: float | None = None
    result_page_share: float | None = None
    run_time_limit_minutes: int | None = None
    start_chars_per_token: float | None = None
    start_estimate_error: float | None = None
    context_line_enabled: bool | None = None
    # --- Anbieter (Etappe 3, app/llm/providers.py) ------------------------
    provider: str = providers.LOCAL
    # Entschluesselter Schluessel des Anbieters (nur wenn er einen verlangt).
    # Nie in repr/Vergleich - ApiKey zeigt seinen Wert ohnehin nicht.
    api_key: keys.ApiKey | None = field(default=None, repr=False, compare=False)
    # Warum kein Schluessel da ist, obwohl einer hinterlegt sein sollte (nicht
    # lesbar, Servergeheimnis fehlt) - None, wenn schlicht keiner hinterlegt ist.
    key_problem: str | None = None

    @property
    def provider_info(self) -> providers.Provider:
        return providers.provider(self.provider)

    def provider_url(self) -> str | None:
        """Feste Adresse des Anbieters; None beim lokalen Anbieter (dort gelten
        die Kandidaten aus candidate_urls())."""
        return self.provider_info.url

    def effective_reasoning(self, override: str | None = None) -> str | None:
        """Gewaehlte Reasoning-Stufe dieses Aufrufs: eine Uebersteuerung des
        Aufrufers (z. B. Denken-Schalter eines Chats) vor der Einstellung."""
        return override if override is not None else self.reasoning

    def reasoning_effort(self, override: str | None = None) -> str | None:
        """Wert fuer `reasoning_effort` im Chat-Aufruf.

        None (Modellstandard) sendet bewusst nichts. LM Studios
        OpenAI-kompatibler Endpunkt lehnt den Capability-Wert "on" als
        reasoning_effort ab; "on" wird deshalb auf die akzeptierte Stufe
        "high" abgebildet, "off" auf "none". Abgestufte Werte gehen
        unveraendert durch."""
        chosen = self.effective_reasoning(override)
        if not chosen:
            return None
        if chosen == "off":
            return "none"
        if chosen == "on":
            return "high"
        return chosen

    def sampling_parameters(
        self, model_name: str, reasoning_override: str | None = None
    ) -> dict[str, float | int]:
        """Sampling-Felder dieses Aufrufs.

        Der manuelle Modus ist woertlich. Im Modellmodus stammen Werte nur aus
        einem sichtbaren Profil; ohne Treffer wird nichts ueberschrieben."""
        if self.sampling_mode == "manual":
            return {
                "temperature": self.temperature,
                "top_p": self.top_p,
                "top_k": self.top_k,
                "repeat_penalty": self.repeat_penalty,
                "min_p": self.min_p,
                "presence_penalty": self.presence_penalty,
            }
        profile = matching_profile(model_name, self.effective_reasoning(reasoning_override))
        return profile.parameters() if profile is not None else {}

    def candidate_urls(self) -> list[str]:
        """Kandidaten-URLs in Probierreihenfolge.

        Die Haushalts-URL steht vorn - sie ist die ausdrueckliche Angabe. Die
        Umgebungskandidaten bleiben dahinter, damit ein Tippfehler in der
        Einstellung nicht die gesamte KI abschaltet, sondern nur den ersten
        Versuch kostet.

        Ein Anbieter mit fester Adresse (Anthropic) hat genau diese eine - ein
        Rueckfall auf LM Studio waere ein stiller Anbieterwechsel, und der
        Schluessel darf nie an eine der Ersatzadressen gehen."""
        fixed = self.provider_url()
        if fixed is not None:
            return [fixed]
        urls: list[str] = []
        if self.custom_base_url:
            urls.append(self.custom_base_url)
        for url in (
            get_settings().lm_studio_base_url,
            "http://host.docker.internal:1234/v1",
            "http://127.0.0.1:1234/v1",
            "http://localhost:1234/v1",
        ):
            if url not in urls:
                urls.append(url)
        return urls

    def searxng_url(self) -> str | None:
        """Wirksame SearXNG-Basis-URL, oder None ohne Suchinstanz."""
        if self.searxng_base_url:
            return self.searxng_base_url.rstrip("/")
        env_url = get_settings().searxng_base_url
        return env_url.rstrip("/") if env_url else None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, int | float | Decimal) else None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    return int(value) if isinstance(value, int | Decimal) else None


def sampling_mode_of(value: Any) -> str:
    """Normalisiert den DB-Wert auf den engen Vertrag (model/manual)."""
    return "manual" if value == "manual" else "model"


async def load_settings_row(session: AsyncSession, household_id: UUID) -> dict[str, Any]:
    """Die Einstellungszeile als Mapping. Wirft SettingsRowMissingError."""
    row = (await session.execute(_SELECT_SQL, {"household_id": str(household_id)})).first()
    if row is None:
        raise SettingsRowMissingError(
            f"Haushalt {household_id} hat keine KI-Einstellungszeile. Das Schema "
            "(Trigger households_ai_settings_row) ist nicht angewendet."
        )
    return dict(row._mapping)


def settings_from_row(row: dict[str, Any]) -> LLMSettings:
    """Zeile -> LLMSettings. Leere Sampling-Felder bekommen die neutralen
    Startwerte des manuellen Modus (siehe oben); alle Budgetfelder kommen
    unveraendert aus der Zeile - deren Vorgaben stehen im Schema."""
    temperature = _float(row.get("temperature"))
    top_p = _float(row.get("top_p"))
    top_k = _int(row.get("top_k"))
    repeat_penalty = _float(row.get("repeat_penalty"))
    min_p = _float(row.get("min_p"))
    presence_penalty = _float(row.get("presence_penalty"))
    timeout = _float(row.get("request_timeout_seconds"))
    if timeout is None:
        raise SettingsRowMissingError(
            "request_timeout_seconds ist leer - die Spalte hat seit dem relativen "
            "KI-Budget eine Vorgabe im Schema; das Schema ist nicht angewendet."
        )
    enabled = row.get("is_enabled")
    return LLMSettings(
        is_enabled=bool(enabled),
        min_confidence=float(row["auto_categorize_min_confidence"]),
        custom_base_url=row.get("custom_base_url"),
        model_name=row.get("model_name"),
        temperature=DEFAULT_TEMPERATURE if temperature is None else temperature,
        request_timeout_seconds=timeout,
        sampling_mode=sampling_mode_of(row.get("sampling_mode")),
        top_p=DEFAULT_TOP_P if top_p is None else top_p,
        top_k=DEFAULT_TOP_K if top_k is None else top_k,
        repeat_penalty=DEFAULT_REPEAT_PENALTY if repeat_penalty is None else repeat_penalty,
        min_p=DEFAULT_MIN_P if min_p is None else min_p,
        presence_penalty=(
            DEFAULT_PRESENCE_PENALTY if presence_penalty is None else presence_penalty
        ),
        context_length=_int(row.get("context_length")),
        reasoning=row.get("reasoning"),
        searxng_base_url=row.get("searxng_base_url"),
        context_cap_tokens=_int(row.get("context_cap_tokens")),
        answer_reserve_share=_float(row.get("answer_reserve_share")),
        result_page_share=_float(row.get("result_page_share")),
        run_time_limit_minutes=_int(row.get("run_time_limit_minutes")),
        start_chars_per_token=_float(row.get("start_chars_per_token")),
        start_estimate_error=_float(row.get("start_estimate_error")),
        context_line_enabled=(
            None
            if row.get("context_line_enabled") is None
            else bool(row.get("context_line_enabled"))
        ),
        provider=str(row.get("provider") or providers.LOCAL),
    )


async def with_api_key(
    session: AsyncSession, household_id: UUID, settings: LLMSettings
) -> LLMSettings:
    """Haengt den Schluessel des gewaehlten Anbieters an - nur wenn er einen
    verlangt. Ein nicht lesbarer Schluessel wird nicht verschwiegen: er steht
    als key_problem in den Einstellungen, und der Aufruf scheitert mit
    model_key_missing samt diesem Satz."""
    info = settings.provider_info
    if info.key == "none" or info.url is None:
        return settings
    target = providers.target_of(info.url)
    try:
        api_key = await keys.load_key(session, household_id, target)
    except keys.ProviderKeyError as exc:
        return replace(settings, api_key=None, key_problem=str(exc))
    return replace(settings, api_key=api_key, key_problem=None)


async def load_settings(session: AsyncSession, household_id: UUID) -> LLMSettings:
    """Einstellungen des Haushalts, wie jeder Modellaufruf sie braucht -
    samt dem Schluessel des gewaehlten Anbieters.

    Der Worker verbindet sich ohne RLS; der Filter auf household_id steht
    deshalb ausdruecklich im Statement."""
    settings = settings_from_row(await load_settings_row(session, household_id))
    return await with_api_key(session, household_id, settings)


def settings_field_names() -> tuple[str, ...]:
    return tuple(field.name for field in fields(LLMSettings))


def parse_column_default(raw: str | None) -> bool | int | float | str | None:
    """Spaltenvorgabe aus information_schema -> Python-Wert.

    Postgres liefert sie als SQL-Text: `0.250`, `20`, `true`,
    `'model'::text`, `NULL::integer`. Funktionsaufrufe (now(),
    current_household_id()) sind keine Werte fuer die Oberflaeche und kommen
    als Text zurueck - schema_defaults() blendet sie ueber die Spaltenliste aus."""
    if raw is None:
        return None
    value = raw.strip()
    while "::" in value and not value.endswith(")"):
        value = value.rsplit("::", 1)[0].strip()
    if value.startswith("(") and value.endswith(")") and "(" not in value[1:-1]:
        value = value[1:-1].strip()
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered == "null":
        return None
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


# Spalten, deren Vorgabe die Oberflaeche zeigt und auf die sie zuruecksetzen
# kann. Spalten ohne Vorgabe (NULL = "nicht gesetzt") stehen mit None darin.
EDITABLE_WITH_DEFAULT: Final[tuple[str, ...]] = (
    "is_enabled",
    "auto_categorize_min_confidence",
    "request_timeout_seconds",
    "context_cap_tokens",
    "answer_reserve_share",
    "result_page_share",
    "run_time_limit_minutes",
    "start_chars_per_token",
    "start_estimate_error",
    "context_line_enabled",
    "assistant_retention_days",
)


async def schema_defaults(session: AsyncSession) -> dict[str, bool | int | float | str | None]:
    """Spaltenvorgaben der Einstellungen, gelesen aus dem Schema selbst."""
    rows = (await session.execute(_DEFAULTS_SQL)).all()
    raw = {str(row.column_name): row.column_default for row in rows}
    return {name: parse_column_default(raw.get(name)) for name in EDITABLE_WITH_DEFAULT}
