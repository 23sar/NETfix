"""Health and readiness router for NETfix API.

Guarantees:
- Read-only database check.
- Zero instantiation of RemediationController or LangGraph agent.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3

from fastapi import APIRouter, Depends

from netfix.api.dependencies import get_db_path, get_safe_executor_mode
from netfix.api.schemas import HealthResponse

logger = logging.getLogger("netfix.api.routers.health")
router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="API Health and Readiness",
    description="Verify API availability, database connectivity, and safe executor status.",
)
def get_health(
    db_path: str = Depends(get_db_path),
) -> HealthResponse:
    """Check API and database readiness without operational side effects."""
    db_file = Path(db_path).resolve()
    db_connected = False

    if db_file.exists():
        try:
            # Test read-only connectivity
            uri = f"file:{db_file}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT 1;")
            cur.fetchone()
            conn.close()
            db_connected = True
        except Exception as exc:
            logger.warning("Database read-only check failed for %s: %s", db_file, exc)
            db_connected = False

    return HealthResponse(
        status="ok" if db_connected else "degraded",
        api_version="v1",
        app_name="NETfix",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        read_only=True,
        database_connected=db_connected,
        database_path=str(db_file),
        executor_mode=get_safe_executor_mode(),
    )
