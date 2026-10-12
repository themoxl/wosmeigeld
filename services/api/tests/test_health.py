from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app

client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api"}


def test_system_health_returns_all_services() -> None:
    # Laeuft ohne echte DB/Redis/LM-Studio-Verbindung (CI-Umgebung): die
    # einzelnen Checks duerfen "down"/"degraded" melden, der Endpunkt selbst
    # muss aber immer antworten und alle bekannten Dienste auflisten.
    response = client.get("/v1/system/health")
    assert response.status_code == 200

    body = response.json()
    assert "status" in body
    service_names = {entry["service"] for entry in body["services"]}
    assert service_names == {"api", "database", "redis", "worker", "storage", "lm_studio"}


def test_me_requires_authentication() -> None:
    # Funktionale Tests fuer /v1/me (Phase 1) siehe test_household_isolation.py -
    # hier nur der Zugriffsschutz ohne Bearer-Token.
    response = client.get("/v1/me")
    assert response.status_code == 401


def test_instance_config_falls_back_to_supabase_url() -> None:
    # Ohne SUPABASE_PUBLIC_URL: supabase_url dient als Fallback (Standardfall,
    # wenn Client und API dieselbe Adresse erreicht).
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key-value",
        instance_display_name="Meine NAS",
    )
    try:
        response = client.get("/v1/system/instance-config")
        assert response.status_code == 200
        assert response.json() == {
            "supabase_url": "https://example.supabase.co",
            "supabase_anon_key": "anon-key-value",
            "instance_display_name": "Meine NAS",
        }
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_instance_config_prefers_public_url_over_internal_url() -> None:
    # supabase_url ist die Sicht der API selbst (z. B. 127.0.0.1 nach
    # scripts/dev.py's docker_host_overrides oder ein Compose-interner
    # Hostname) - fuer die Mobile-App bei "Eigener Server" muss stattdessen
    # die client-erreichbare supabase_public_url gewinnen. Regressionstest
    # fuer genau den Bug, der die Anmeldung vom Handy aus mit "Network
    # request failed" scheitern liess, obwohl die API selbst erreichbar war.
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://127.0.0.1:55321",
        supabase_public_url="http://192.168.1.23:55321",
        supabase_anon_key="anon-key-value",
    )
    try:
        response = client.get("/v1/system/instance-config")
        assert response.status_code == 200
        assert response.json()["supabase_url"] == "http://192.168.1.23:55321"
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_instance_config_503_when_not_configured() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="", supabase_anon_key=""
    )
    try:
        response = client.get("/v1/system/instance-config")
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_settings, None)
