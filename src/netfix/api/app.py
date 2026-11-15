"""FastAPI application factory for NETfix Advisory and Remediation Web Dashboard.

Enforces:
1. Strict read-only API access.
2. Standardized error response wrappers.
3. Explicit CORS restriction for local development.
4. No background mutation workers, mininet threads, or controller initialization.
"""

from datetime import datetime, timezone
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from netfix.api.dependencies import get_cors_origins
from netfix.api.routers import (
    dashboard_router,
    experiments_router,
    health_router,
    incidents_router,
    topology_router,
)

logger = logging.getLogger("netfix.api")


def create_app() -> FastAPI:
    """Create and configure the NETfix read-only FastAPI application."""
    app = FastAPI(
        title="NETfix Advisory & Remediation API",
        description=(
            "Production-oriented read-only REST API serving network telemetry, "
            "topology layouts, congestion detection incidents, and experiment analytics."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 1. Configure CORS
    origins = get_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    # 2. Standardized Error Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = f"HTTP_{exc.status_code}"
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            code = "RESOURCE_NOT_FOUND"
        elif exc.status_code == status.HTTP_400_BAD_REQUEST:
            code = "INVALID_REQUEST"
        elif exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            code = "SERVICE_UNAVAILABLE"

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                    "details": None,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request parameter validation failed.",
                    "details": exc.errors(),
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled API exception on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An internal server error occurred while processing the request.",
                    "details": None,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # 3. Mount Modular Routers under /api/v1
    api_v1_prefix = "/api/v1"
    app.include_router(health_router, prefix=api_v1_prefix)
    app.include_router(dashboard_router, prefix=api_v1_prefix)
    app.include_router(topology_router, prefix=api_v1_prefix)
    app.include_router(experiments_router, prefix=api_v1_prefix)
    app.include_router(incidents_router, prefix=api_v1_prefix)

    return app


# Default application instance for ASGI servers (e.g. uvicorn netfix.api.app:app)
app = create_app()


def run_dev_server() -> None:
    """Launch the development Uvicorn server."""
    import uvicorn

    host = os.environ.get("NETFIX_API_HOST", "127.0.0.1")
    port = int(os.environ.get("NETFIX_API_PORT", "8000"))
    reload = os.environ.get("NETFIX_API_RELOAD", "false").lower() in ("true", "1")

    print(f"Starting NETfix Read-Only API on http://{host}:{port}")
    print(f"Interactive documentation available at http://{host}:{port}/docs")
    uvicorn.run("netfix.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    run_dev_server()
