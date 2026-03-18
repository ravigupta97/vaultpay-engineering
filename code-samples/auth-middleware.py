"""
VaultPay Request ID Middleware
==============================
Adds a unique X-Request-ID to every request/response for distributed tracing.

WHY REQUEST IDs?
  Correlates frontend → VaultPay → AuthShield across service boundaries.
  Every log line, audit record, and error response carries this ID.

HOW IT WORKS:
  1. If the client sends X-Request-ID, we reuse it (supports API gateways)
  2. If not, we generate a new UUID4
  3. Stored on request.state.request_id for access in:
       - Structured logging
       - Audit logs (links user actions to specific requests)
       - Error responses (helps support team trace issues)
  4. Echoed back in the response X-Request-ID header
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that assigns and propagates X-Request-ID.

    Registration in main.py:
        app.add_middleware(RequestIDMiddleware)

    Accessing in a dependency:
        def get_request_id(request: Request) -> str:
            return request.state.request_id
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Reuse existing ID or generate new one
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Store on request.state for access in dependencies and endpoints
        request.state.request_id = request_id

        # Process the request
        response = await call_next(request)

        # Echo back in response header
        response.headers["X-Request-ID"] = request_id

        return response
