"""OpenTelemetry bootstrap: no-op by default, OTLP when configured."""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_service_name = "reelforge-api"


def setup_tracing(otlp_endpoint: str = "") -> None:
    """Install a TracerProvider. Empty endpoint -> no-op (no exporter)."""
    provider = TracerProvider(resource=Resource.create({"service.name": _service_name}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    else:
        # Console exporter at debug only is noisy; no-op keeps default sampler cheap.
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
