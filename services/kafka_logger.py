"""
Kafka logging service and HTTP request telemetry middleware for CREASE Brain.

Features:
- Non-blocking asynchronous log shipping to Kafka.
- Structured JSON formatting matching the Sentri Log Event Schema.
- HTTP API Request telemetry capturing duration, status, path, method, and IP.
- Graceful degradation: falls back to standard console logging if Kafka is disabled or unreachable.
"""

import os
import time
import json
import logging
import datetime
import traceback
import contextvars
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import uuid

logger = logging.getLogger(__name__)

# Context variable for trace ID
trace_id_var = contextvars.ContextVar("trace_id", default="no-trace")

# Load Kafka Configurations
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "crease-logs")
KAFKA_ENABLED = os.getenv("KAFKA_ENABLED", "false").lower() in ("true", "1", "yes")

_producer = None
_last_producer_error_time = 0
PRODUCER_RETRY_INTERVAL_SEC = 60

def _get_kafka_producer():
    """Initialise Kafka producer lazily with a circuit breaker."""
    global _producer, _last_producer_error_time
    if _producer is not None:
        return _producer

    if not KAFKA_ENABLED:
        return None

    now = time.time()
    # Circuit breaker: don't attempt to reconnect on every single log if Kafka is down
    if now - _last_producer_error_time < PRODUCER_RETRY_INTERVAL_SEC:
        return None

    try:
        from kafka import KafkaProducer
        _producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BOOTSTRAP_SERVERS],
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            retries=1,
            acks=1,
            max_block_ms=2000, # Do not block API requests for more than 2s if Kafka is unreachable
            request_timeout_ms=2000,
            api_version_auto_timeout_ms=2000
        )
        # Use standard print to avoid infinite logging loops in the logging handler itself
        print(f"Initialized KafkaProducer for {KAFKA_BOOTSTRAP_SERVERS}")
        return _producer
    except Exception as e:
        _last_producer_error_time = time.time()
        import sys
        print(f"Failed to initialize Kafka producer (will retry in 60s): {e}", file=sys.stderr)
        return None


class KafkaLogHandler(logging.Handler):
    """
    Custom logging handler that formats records into structured JSON
    and enqueues them for asynchronous shipping to Kafka.
    """

    def __init__(self, level=logging.NOTSET):
        super().__init__(level=level)
        self.service_name = "crease-brain"
        self.owner = "team-crease-backend"
        self.environment = os.getenv("ENVIRONMENT", "development")

    def emit(self, record: logging.LogRecord):
        # Prevent recursion if log was produced by kafka-python client itself
        if record.name.startswith("kafka"):
            return

        try:
            # Format exception details if present
            exc_text = None
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))

            now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            trace_id = trace_id_var.get()

            # Sentri Log Event Schema
            doc = {
                "timestamp": now_utc,
                "service_name": self.service_name,
                "owner": self.owner,
                "severity": record.levelname,
                "trace_id": trace_id,
                "message": record.getMessage(),
                "environment": self.environment,
                "metadata": {
                    "logger": record.name,
                    "module": record.module,
                    "filename": record.filename,
                    "line": record.lineno,
                    "func": record.funcName,
                }
            }

            if exc_text:
                doc["stack_trace"] = exc_text

            # Include extra metadata fields if attached to log record
            if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
                doc["metadata"].update(record.extra_fields)

            producer = _get_kafka_producer()
            if producer:
                # OpenSearch Pull-based ingestion requires the document to be wrapped in a _source field
                payload = {"_source": doc}
                
                # The KafkaProducer in python is already asynchronous (it queues internally)
                producer.send(KAFKA_TOPIC, payload)
        except Exception as e:
            self.handleError(record)


class TraceIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that generates a unique trace_id for each request
    and stores it in contextvars.
    """
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = str(uuid.uuid4())[:8] # Short trace id like "a1b2c3d4"
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            trace_id_var.reset(token)


class KafkaTelemetryMiddleware(BaseHTTPMiddleware):
    """
    Middleware that captures API HTTP request telemetry and logs
    structured timing and endpoint metrics to Kafka.
    """
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()
        path = request.url.path
        method = request.method
        client_ip = request.client.host if request.client else "unknown"

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            status_code = 500
            raise exc from None
        finally:
            process_time_ms = round((time.time() - start_time) * 1000, 2)
            
            # Skip noise like health check logs if desired
            if path not in ["/health", "/"]:
                telemetry_logger = logging.getLogger("api.telemetry")
                log_level = logging.ERROR if status_code >= 500 else logging.INFO
                
                msg = f"{method} {path} -> {status_code} ({process_time_ms}ms)"
                extra_data = {
                    "extra_fields": {
                        "endpoint": path,
                        "method": method,
                        "status_code": status_code,
                        "process_time_ms": process_time_ms,
                        "client_ip": client_ip,
                        "user_agent": request.headers.get("user-agent", "unknown")
                    }
                }
                
                telemetry_logger.log(log_level, msg, extra=extra_data)


def setup_kafka_logging(app=None):
    """
    Attach Kafka logging handler, and register HTTP request telemetry middleware.
    """
    if KAFKA_ENABLED:
        logger.info("Initializing Kafka Log Handler pointing to %s (topic: %s)", KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC)
        root_logger = logging.getLogger()
        handler = KafkaLogHandler()
        handler.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    else:
        logger.info("Kafka log shipping is currently disabled (KAFKA_ENABLED=false). Logs streaming to console.")

    # 2. Add telemetry middleware if FastAPI app is provided
    if app:
        # Middlewares execute in reverse order of addition.
        # By adding telemetry first and then trace_id, the TraceIDMiddleware will be the outer wrapper.
        app.add_middleware(KafkaTelemetryMiddleware)
        app.add_middleware(TraceIDMiddleware)
