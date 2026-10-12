from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from worker.config import Settings

# Siehe services/api/app/core/tracing.py fuer die ausfuehrliche Begruendung.
# Hier bewusst EIN handgeschriebener Span um process_import statt
# Auto-Instrumentierung (z.B. eines Dramatiq-Contrib-Pakets) - deren Reife
# ist ungewiss, ein einzelner Span reicht, um die Pipeline zu beweisen und
# Live-Latenz zu sehen. Dauer/Format/Zeilenanzahl bleiben die Domaene von
# import_runs/llm_calls (Postgres), nicht dieses Spans.

_TRACER_NAME = "finanzplaner-worker"


def configure_tracing(settings: Settings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: _TRACER_NAME}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)


@contextmanager
def job_span(name: str, **attributes: str) -> Iterator[None]:
    # trace.get_tracer() liefert ohne konfigurierten TracerProvider einen
    # funktionsfaehigen No-op-Tracer (OpenTelemetry-API-Standardverhalten) -
    # dieser Context-Manager ist deshalb immer sicher aufrufbar, auch ohne
    # configure_tracing() bzw. ohne gesetzten OTEL_EXPORTER_OTLP_ENDPOINT.
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name, attributes=attributes):
        yield
