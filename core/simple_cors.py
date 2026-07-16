"""
Simple CORS middleware that adds CORS headers to every response.
Placed as outermost middleware to ensure CORS headers are always present.
"""
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SimpleCORSMiddleware(BaseHTTPMiddleware):
    """Simple CORS middleware - adds headers to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Handle preflight
        if request.method == "OPTIONS":
            response = Response(
                content="",
                status_code=200,
                media_type="text/plain",
            )
        else:
            response = await call_next(request)

        # Add CORS headers to every response
        origin = request.headers.get("origin", "")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"

        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, X-API-Key"
        response.headers["Access-Control-Max-Age"] = "0"
        response.headers["Vary"] = "Origin"

        return response
