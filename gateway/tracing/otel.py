"""OpenTelemetry tracing and structured logging setup for tool calls."""

import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import cast

import structlog
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span as ApiSpan


class TracingManager:
    """Own a tracer provider and expose scoped tool-call spans."""

    def __init__(self, provider: TracerProvider) -> None:
        """Initialize a manager around a configured provider."""

        self._provider = provider
        self._tracer = provider.get_tracer("mcp-gateway")

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, str | int | bool] | None = None,
    ) -> Iterator[ApiSpan]:
        """Yield an active span with optional primitive attributes."""

        with self._tracer.start_as_current_span(
            name, attributes=dict(attributes or {})
        ) as span:
            yield span

    def force_flush(self) -> bool:
        """Flush completed spans to the configured exporter."""

        return self._provider.force_flush()

    def shutdown(self) -> None:
        """Flush spans and release exporter resources."""

        self._provider.shutdown()


def configure_logging(log_level: str) -> structlog.stdlib.BoundLogger:
    """Configure JSON logs on stderr and return a gateway logger."""

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    logger = structlog.get_logger().bind(log_level=log_level)
    return cast(structlog.stdlib.BoundLogger, logger)


def create_tracing(
    endpoint: str | None = None,
    exporter: SpanExporter | None = None,
    service_name: str = "mcp-enterprise-agent-gateway",
) -> TracingManager:
    """Create a tracing manager with an optional OTLP or test exporter."""

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    processor: SpanProcessor
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter)
    elif endpoint is not None:
        processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, insecure=True)
        )
    else:
        processor = SimpleSpanProcessor(_DiscardingExporter())
    provider.add_span_processor(processor)
    return TracingManager(provider)


class _DiscardingExporter(SpanExporter):
    """Discard spans when a caller intentionally disables exporting."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Accept spans without network or file side effects."""

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Release the no-op exporter without performing work."""

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Report successful flushing for the no-op exporter."""

        return True
