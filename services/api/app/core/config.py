from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Umgebungen, in denen die Demo-Zugangsdaten aus .env.example in Ordnung sind:
# ein lokal gebundenes `supabase start` plus die Testsuite. Alles andere gilt
# als erreichbarer Betrieb.
_LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "test", "testing"})

# Standardwerte, die `supabase start` fuer JEDES lokale Projekt identisch
# erzeugt (siehe Kopf von .env.example). Sie sind oeffentlich dokumentiert und
# stehen genau deshalb auch im Repository - als Secret taugen sie nicht:
#
#   * Mit SUPABASE_JWT_SECRET lassen sich HS256-Tokens fuer eine beliebige
#     Nutzer-ID selbst unterschreiben. app/auth/jwt.py prueft nur Signatur und
#     Audience - ein solches Token ist von einem echten nicht zu unterscheiden.
#   * Der Service-Role-Key haengt die Zeilensicherheit komplett aus.
#
# Solange die Instanz nur an localhost lauscht, ist das folgenlos. Sobald sie
# es nicht mehr tut - NAS, Tailscale, Reverse Proxy - ist es ein offener
# Zugang zu allen Haushaltsdaten. Deshalb hier ein harter Abbruch beim Start
# statt einer Warnung, die im Log untergeht.
_SUPABASE_DEMO_JWT_SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"
# Base64url von '{"iss":"supabase-demo"' - der Nutzlastanfang der Demo-Keys.
# Auf den Anbieter statt auf den ganzen Schluessel zu pruefen faengt auch die
# Varianten ab, die sich nur in Rolle oder Ablaufdatum unterscheiden.
_SUPABASE_DEMO_KEY_MARKER = "eyJpc3MiOiJzdXBhYmFzZS1kZW1v"


class Settings(BaseSettings):
    # secrets_dir="/run/secrets": no-op, wenn das Verzeichnis nicht existiert
    # (Standard-Dev/Home-Server-Pfad mit .env bleibt unveraendert). Nur
    # gefuellt, wenn docker-compose.prod.yml Secret-Dateien mountet (siehe
    # dort) - settings_customise_sources unten sorgt dafuer, dass diese
    # Dateien dann auch tatsaechlich Vorrang vor .env/env_file bekommen
    # (pydantic-settings' Default-Reihenfolge wuerde .env sonst gewinnen
    # lassen, selbst wenn eine Secret-Datei vorhanden ist).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", secrets_dir="/run/secrets")

    environment: str = "development"
    api_port: int = 8882
    api_cors_origins: str = "http://localhost:5173,http://localhost:8081"

    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/finanzplaner"
    redis_url: str = "redis://redis:6379/0"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # supabase_url ist die Adresse, unter der DIE API SELBST Supabase
    # erreicht - beim nativen Dev-Start via scripts/dev.py auf 127.0.0.1
    # umgebogen (siehe dortige docker_host_overrides()), im Docker-Betrieb oft
    # ein Compose-interner Hostname. Das ist nicht dieselbe Adresse, unter der
    # ein CLIENT (Handy, Browser auf einem anderen Geraet) Supabase erreicht -
    # deshalb eigenes Feld statt Wiederverwendung von supabase_url. Leer =
    # Fallback auf supabase_url (Standardfall: Client und API sind im selben
    # Netz / dieselbe Adresse passt fuer beide).
    supabase_public_url: str = ""

    # Anzeigename dieser Instanz, den die Mobile-App bei "Eigener Server"
    # nach erfolgreicher Discovery zeigt (GET /v1/system/instance-config).
    # Optional, kein Secret.
    instance_display_name: str = ""

    lm_studio_base_url: str = "http://host.docker.internal:1234/v1"

    # Basis-URL der selbstgehosteten SearXNG-Instanz fuer die optionale
    # Websuche der KI-Kostenschaetzung (Vorhaben-Modul). Leer = keine
    # Umgebungsvorgabe - anders als LM Studio gibt es dafuer keinen
    # plausiblen Standardwert, den jede Installation teilt.
    searxng_base_url: str = ""

    # Ein Token-Budget je Antwort steht hier nicht (frueher
    # AI_MAX_TOKENS=4000): das Antwortbudget jedes Modellaufrufs ergibt sich
    # aus dem geladenen Kontextfenster und dem Reserveanteil der
    # Haushaltseinstellung (app/llm/budget.py). Ebenso hat das Zeitbudget je
    # Anfrage seine Vorgabe nur im Datenbankschema
    # (ai_household_settings.request_timeout_seconds) - frueher zusaetzlich
    # AI_REQUEST_TIMEOUT_SECONDS als Rueckfall.

    # Zeitbudget fuer die reine Discovery (GET /models). Bewusst kurz: hier
    # wird nichts generiert, und es werden nacheinander mehrere Kandidaten-
    # URLs probiert - ein langes Budget wuerde den Systemstatus bei nicht
    # laufendem LM Studio unnoetig blockieren.
    ai_discovery_timeout_seconds: float = 5.0

    storage_backend: str = "supabase"
    s3_endpoint: str = ""
    s3_bucket: str = "finanzplaner"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    health_documents_bucket: str = "health-documents"
    health_documents_local_path: str = ".health-storage"
    health_document_max_bytes: int = 25 * 1024 * 1024
    health_document_max_pages: int = 120

    # Ab dieser Gesamtkonfidenz uebernimmt der Worker eine Extraktion ohne
    # Rueckfrage. Die Konfidenz erreicht 0.80 erst, wenn Anbieter, Datum und
    # Betrag alle gefunden wurden; die restlichen 0.15 kommen aus der
    # Selbsteinschaetzung des Modells. 0.85 verlangt also vollstaendige
    # Kernfelder plus ein Modell, das sich seiner Sache sicher ist. Ein Wert
    # groesser 1 schaltet die Uebernahme ab - dann landet wieder jedes
    # Dokument in der Handpruefung.
    health_document_auto_confirm_confidence: float = 0.85

    # Pfad zur Tesseract-Binary. Leer = auf PATH suchen und danach die
    # ueblichen Installationsorte abklappern. Unter Windows legt der
    # Installer die Binary nach "C:\\Program Files\\Tesseract-OCR", ohne sie
    # in den PATH zu haengen - ohne diesen Fallback laeuft beim nativen
    # Dienststart gar keine OCR, waehrend sie im Container funktioniert.
    tesseract_binary: str = ""

    worker_heartbeat_interval_seconds: int = 15

    # Leer = OpenTelemetry-Instrumentierung ist ein no-op (siehe
    # app/core/tracing.py) - kein Tracing-Overhead ohne konfigurierten
    # Sammler, kein Zwang zu neuer Infrastruktur fuer Privat/Home-Server.
    otel_exporter_otlp_endpoint: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def is_local_environment(self) -> bool:
        return self.environment.strip().lower() in _LOCAL_ENVIRONMENTS

    @model_validator(mode="after")
    def _reject_demo_credentials(self) -> "Settings":
        """Bricht ausserhalb der lokalen Entwicklung bei Demo-/Leerwerten ab.

        Greift beim ersten get_settings() und damit beim Start von API und
        Worker - der Dienst faehrt gar nicht erst hoch, statt mit einem
        faelschbaren Token-Secret erreichbar zu sein.
        """
        if self.is_local_environment:
            return self

        offenders: list[str] = []
        if not self.supabase_jwt_secret.strip():
            offenders.append("SUPABASE_JWT_SECRET ist leer")
        elif self.supabase_jwt_secret == _SUPABASE_DEMO_JWT_SECRET:
            offenders.append("SUPABASE_JWT_SECRET ist der Supabase-Demo-Wert")
        for name, value in (
            ("SUPABASE_ANON_KEY", self.supabase_anon_key),
            ("SUPABASE_SERVICE_ROLE_KEY", self.supabase_service_role_key),
        ):
            if _SUPABASE_DEMO_KEY_MARKER in value:
                offenders.append(f"{name} ist ein Supabase-Demo-Schluessel")

        if offenders:
            raise ValueError(
                f"ENVIRONMENT={self.environment!r} verlangt eigene Supabase-Zugangsdaten, "
                + "; ".join(offenders)
                + ". Die Werte aus .env.example sind oeffentlich dokumentiert und erlauben "
                "gefaelschte Tokens fuer beliebige Nutzer-IDs. Projekteigene Werte setzen "
                "(Supabase-Dashboard > Project Settings > API) oder ENVIRONMENT auf "
                "'development' stellen, solange die Instanz nur an localhost lauscht."
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Secret-Dateien (falls vorhanden) gewinnen gegen .env/env_file -
        # sonst wuerde ein in .env leer/gesetzt vorhandenes Feld die
        # gemountete Secret-Datei stillschweigend maskieren.
        return (init_settings, file_secret_settings, env_settings, dotenv_settings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
