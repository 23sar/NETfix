"""NETfix Web Dashboard API package.

Provides a modular, read-only FastAPI application serving network telemetry,
topology layout, experiment history, and system health to the NETfix frontend.
"""

from netfix.api.app import create_app

__all__ = ["create_app"]
