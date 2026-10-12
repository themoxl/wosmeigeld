from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import Settings

# Optionales Live-Tracing (Architekturplan Abschnitt 16). Bewusst kein
# no-op-vs-Sammler-Zwiespalt: ohne OTEL_EXPORTER_OTLP_ENDPOINT passiert hier
# schlicht nichts - kein TracerProvider, kein Overhead, keine neue
# Infrastruktur fuer Privat/Home-Server-Betrieb. Ist der Endpunkt gesetzt
# (z.B. der optionale Collector aus docker-compose.monitoring.yml, oder ein
# selbst betriebener Sammler), bekommt jeder Request einen Span mit
# Route/Status/Dauer. Job-/Importmetriken (Dauer, Format, Zeilenanzahl)
# bleiben bewusst in import_runs/llm_calls (Postgres) die massgebliche,
# historische Quelle - dieser Span dient nur der Live-Sicht waehrend ein
# Request laeuft, keine zweite Metrik-Ablage.


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "finanzplaner-api"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
