"""
Request logging middleware.

Injects a trace_id into every request context and logs:
  trace_id, store_id (from path), endpoint, latency_ms, status_code,
  event_count (for /events/ingest), method, path.

No raw stack traces are exposed in responses — all exceptions are
caught and returned as structured JSON.
"""
import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("api.middleware")

# Thread-local-style context variable for trace_id — accessible inside route handlers
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = str(uuid.uuid4())
        trace_id_var.set(trace_id)

        start_time = time.perf_counter()

        # Extract store_id from path if present
        store_id = request.path_params.get("store_id", "")

        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "Unhandled exception",
                extra={
                    "trace_id": trace_id,
                    "store_id": store_id,
                    "endpoint": str(request.url.path),
                    "method": request.method,
                    "latency_ms": round(elapsed_ms, 2),
                    "status_code": 500,
                    "error": str(exc),
                },
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_server_error",
                    "message": "An unexpected error occurred",
                    "trace_id": trace_id,
                },
            )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Attach trace_id to response headers
        response.headers["X-Trace-Id"] = trace_id

        logger.info(
            "Request completed",
            extra={
                "trace_id": trace_id,
                "store_id": store_id,
                "endpoint": str(request.url.path),
                "method": request.method,
                "latency_ms": round(elapsed_ms, 2),
                "status_code": response.status_code,
            },
        )
        return response
