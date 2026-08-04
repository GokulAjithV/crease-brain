"""
OpenSearch logging service and HTTP request telemetry middleware for CREASE Brain.

Features:
- Non-blocking asynchronous log shipping (queued worker thread).
- Structured JSON formatting (@timestamp, service, level, logger, message, stack trace).
- HTTP API Request telemetry capturing duration, status, path, method, and IP.
- Graceful degradation: falls back to standard console logging if OpenSearch is disabled or unreachable.
"""

import os
import time
import queue
import logging
import datetime
import traceback
import threading
from typing import Optional, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Load OpenSearch Configurations
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "http://localhost:9200")
OPENSEARCH_INDEX_PREFIX = os.getenv("OPENSEARCH_INDEX", "crease-app-logs")
OPENSEARCH_USERNAME = os.getenv("OPENSEARCH_USERNAME", "")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "")
OPENSEARCH_ENABLED = os.getenv("OPENSEARCH_ENABLED", "false").lower() in ("true", "1", "yes")
OPENSEARCH_VERIFY_CERTS = os.getenv("OPENSEARCH_VERIFY_CERTS", "false").lower() in ("true", "1", "yes")

_client = None
_log_queue = queue.Queue(maxsize=5000)
_worker_thread = None
_stop_event = threading.Event()


def _init_opensearch_client():
    """Initialise OpenSearch client lazily."""
    global _client
    if _client is not None:
        return _client

    try:
        from opensearchpy import OpenSearch

        auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD) if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD else None
        
        _client = OpenSearch(
            hosts=[OPENSEARCH_HOST],
            http_auth=auth,
            use_ssl=OPENSEARCH_HOST.startswith("https"),
            verify_certs=OPENSEARCH_VERIFY_CERTS,
            ssl_show_warn=False,
            timeout=10,
            max_retries=3,
            retry_on_timeout=True,
        )
        return _client
    except Exception as e:
        logger.error("Failed to initialize OpenSearch client: %s", e)
        return None


def _get_target_index() -> str:
    """Generate daily index name for log lifecycle management (e.g. crease-app-logs-2026.08)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"{OPENSEARCH_INDEX_PREFIX}-{now.strftime('%Y.%m')}"


def _worker_loop():
    """Background thread loop that drains log queue and posts documents to OpenSearch."""
    client = None
    while not _stop_event.is_set() or not _log_queue.empty():
        try:
            try:
                record_doc = _log_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if client is None and OPENSEARCH_ENABLED:
                client = _init_opensearch_client()

            if client and OPENSEARCH_ENABLED:
                try:
                    target_index = _get_target_index()
                    client.index(
                        index=target_index,
                        body=record_doc,
                        refresh=False
                    )
                except Exception as ex:
                    # Print directly to stderr to avoid logging loops
                    print(f"[OpenSearchLogHandler] Error sending log to OpenSearch: {ex}", flush=True)

            _log_queue.task_done()
        except Exception as ex:
            print(f"[OpenSearchLogHandler] Worker error: {ex}", flush=True)


class OpenSearchLogHandler(logging.Handler):
    """
    Custom logging handler that formats records into structured JSON
    and enqueues them for asynchronous shipping to OpenSearch.
    """

    def __init__(self, level=logging.NOTSET):
        super().__init__(level=level)
        self.service_name = "crease-brain"
        self.environment = os.getenv("ENVIRONMENT", "development")

    def emit(self, record: logging.LogRecord):
        # Prevent recursion if log was produced by opensearch client itself
        if record.name.startswith("opensearch") or record.name.startswith("urllib3"):
            return

        try:
            # Format exception details if present
            exc_text = None
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))

            now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

            doc = {
                "@timestamp": now_utc,
                "service": self.service_name,
                "environment": self.environment,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "filename": record.filename,
                "line": record.lineno,
                "func": record.funcName,
            }

            if exc_text:
                doc["exception"] = exc_text

            # Include extra metadata fields if attached to log record
            if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
                doc.update(record.extra_fields)

            # Push to queue non-blockingly
            try:
                _log_queue.put_nowait(doc)
            except queue.Full:
                print("[OpenSearchLogHandler] Queue full, dropping log record.", flush=True)

        except Exception as e:
            self.handleError(record)


class OpenSearchTelemetryMiddleware(BaseHTTPMiddleware):
    """
    Middleware that captures API HTTP request telemetry and logs
    structured timing and endpoint metrics to OpenSearch.
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
                        "telemetry": {
                            "method": method,
                            "path": path,
                            "status_code": status_code,
                            "process_time_ms": process_time_ms,
                            "client_ip": client_ip,
                            "user_agent": request.headers.get("user-agent", "unknown")
                        }
                    }
                }
                
                telemetry_logger.log(log_level, msg, extra=extra_data)


def setup_opensearch_logging(app=None):
    """
    Initialize background OpenSearch log worker thread, attach logging handler,
    and register HTTP request telemetry middleware.
    """
    global _worker_thread

    # 1. Start worker thread if enabled
    if OPENSEARCH_ENABLED and (_worker_thread is None or not _worker_thread.is_alive()):
        logger.info("Initializing OpenSearch Log Handler pointing to %s (index: %s)", OPENSEARCH_HOST, OPENSEARCH_INDEX_PREFIX)
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="OpenSearchLogWorker")
        _worker_thread.start()

        # Attach handler to root logger
        root_logger = logging.getLogger()
        handler = OpenSearchLogHandler()
        handler.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    else:
        logger.info("OpenSearch log shipping is currently disabled (OPENSEARCH_ENABLED=false). Logs streaming to console.")

    # 2. Add telemetry middleware if FastAPI app is provided
    if app:
        app.add_middleware(OpenSearchTelemetryMiddleware)
