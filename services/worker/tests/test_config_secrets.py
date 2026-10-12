"""Siehe services/api/tests/test_config_secrets.py fuer die Begruendung -
Secrets-Dateien muessen gegen .env/Umgebungsvariablen gewinnen."""

from pydantic_settings import SettingsConfigDict

from worker.config import Settings


def test_secrets_dir_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://from-env/postgres")
    (tmp_path / "database_url").write_text("postgresql+psycopg://from-secret-file/postgres")

    class SecretsTestSettings(Settings):
        model_config = SettingsConfigDict(env_file=None, extra="ignore", secrets_dir=str(tmp_path))

    settings = SecretsTestSettings()

    assert settings.database_url == "postgresql+psycopg://from-secret-file/postgres"


def test_missing_secrets_dir_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://from-env-only/postgres")

    class NoSecretsDirSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=None, extra="ignore", secrets_dir="/does/not/exist"
        )

    settings = NoSecretsDirSettings()

    assert settings.database_url == "postgresql+psycopg://from-env-only/postgres"
