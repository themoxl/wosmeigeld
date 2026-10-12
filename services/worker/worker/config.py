from functools import lru_cache

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    # Siehe services/api/app/core/config.py fuer die Begruendung
    # (secrets_dir + settings_customise_sources: no-op ohne
    # /run/secrets, gewinnt gegen .env, wenn vorhanden).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", secrets_dir="/run/secrets")

    environment: str = "development"
    redis_url: str = "redis://redis:6379/0"
    worker_heartbeat_interval_seconds: int = 15
    database_url: str = "postgresql+psycopg://postgres:postgres@host.docker.internal:55322/postgres"
    lm_studio_base_url: str = "http://host.docker.internal:1234/v1"

    # KI-Budget und Zeitbudgets stehen hier nicht mehr: der Worker benutzt den
    # KI-Kern der API (services/api/app/llm, ueber PYTHONPATH). Das
    # Antwortbudget kommt dort aus dem geladenen Kontextfenster, das Zeitbudget
    # je Anfrage aus der Haushaltseinstellung (Vorgabe im Datenbankschema),
    # das Discovery-Zeitbudget aus services/api/app/core/config.py.

    # Siehe services/api/app/core/config.py - identische Begruendung.
    otel_exporter_otlp_endpoint: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, file_secret_settings, env_settings, dotenv_settings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
