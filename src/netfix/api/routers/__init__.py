"""Modular FastAPI routers for NETfix Web Dashboard."""

from netfix.api.routers.dashboard import router as dashboard_router
from netfix.api.routers.experiments import router as experiments_router
from netfix.api.routers.health import router as health_router
from netfix.api.routers.incidents import router as incidents_router
from netfix.api.routers.topology import router as topology_router

__all__ = [
    "health_router",
    "dashboard_router",
    "topology_router",
    "experiments_router",
    "incidents_router",
]
