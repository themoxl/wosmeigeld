"""Secrets-Dateien (docker-compose.prod.yml, /run/secrets/<feld>) muessen
gegen .env/Umgebungsvariablen gewinnen - siehe app/core/config.py
(settings_customise_sources). Reiner Unit-Test, keine echte DB/Supabase
noetig."""

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings


def test_secrets_dir_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "from-env")
    (tmp_path / "supabase_jwt_secret").write_text("from-secret-file")

    class SecretsTestSettings(Settings):
        model_config = SettingsConfigDict(env_file=None, extra="ignore", secrets_dir=str(tmp_path))

    settings = SecretsTestSettings()

    assert settings.supabase_jwt_secret == "from-secret-file"


def test_missing_secrets_dir_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "from-env-only")

    class NoSecretsDirSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=None, extra="ignore", secrets_dir="/does/not/exist"
        )

    settings = NoSecretsDirSettings()

    assert settings.supabase_jwt_secret == "from-env-only"


# --- Demo-Zugangsdaten ausserhalb der lokalen Entwicklung ------------------
# Die SUPABASE_*-Werte aus .env.example erzeugt `supabase start` fuer jedes
# lokale Projekt identisch; sie sind oeffentlich dokumentiert. Wer das
# JWT-Secret kennt, unterschreibt sich selbst ein Token fuer eine beliebige
# Nutzer-ID - app/auth/jwt.py prueft nur Signatur und Audience. Sobald die
# Instanz nicht mehr nur an localhost lauscht, darf der Dienst damit nicht
# starten.

_DEMO_JWT_SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"
_DEMO_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9."
    "CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
)


class _IsolatedSettings(Settings):
    """Ohne .env, damit der Test nicht von der lokalen Konfiguration abhaengt."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", secrets_dir="/does/not/exist")


def test_demo_jwt_secret_is_allowed_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _DEMO_JWT_SECRET)
    monkeypatch.setenv("SUPABASE_ANON_KEY", _DEMO_ANON_KEY)

    assert _IsolatedSettings().supabase_jwt_secret == _DEMO_JWT_SECRET


def test_demo_jwt_secret_is_rejected_outside_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _DEMO_JWT_SECRET)

    with pytest.raises(ValidationError, match="SUPABASE_JWT_SECRET ist der Supabase-Demo-Wert"):
        _IsolatedSettings()


def test_demo_anon_key_is_rejected_outside_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "eigenes-projektspezifisches-secret")
    monkeypatch.setenv("SUPABASE_ANON_KEY", _DEMO_ANON_KEY)

    with pytest.raises(ValidationError, match="SUPABASE_ANON_KEY ist ein Supabase-Demo-Schluessel"):
        _IsolatedSettings()


def test_empty_jwt_secret_is_rejected_outside_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")

    with pytest.raises(ValidationError, match="SUPABASE_JWT_SECRET ist leer"):
        _IsolatedSettings()


def test_own_credentials_start_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "eigenes-projektspezifisches-secret")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzdXBhYmFzZSJ9.sig")
    monkeypatch.setenv(
        "SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzdXBhYmFzZSJ9.sig"
    )

    assert _IsolatedSettings().environment == "production"
