"""Dependency injection providers for NETfix FastAPI backend.

Guarantees:
1. Read-only database access via SQLite 'mode=ro' URI parameter.
2. Zero instantiation of RemediationController or LangGraph agent.
3. Safe extraction of environment configurations without system side effects.
"""

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sqlite3
from typing import Generator, List, Optional

logger = logging.getLogger("netfix.api.dependencies")


def get_db_path() -> str:
    """Return the configured path to the NETfix experiment SQLite database."""
    return os.environ.get("NETFIX_DB_PATH", "data/netfix.db")


def get_data_dir() -> Path:
    """Return the root data directory path."""
    return Path(os.environ.get("NETFIX_DATA_DIR", "data"))


def get_diagnosis_db_path() -> Path:
    """Return configured path to diagnosis checkpoints SQLite database."""
    default_p = get_data_dir() / "diagnosis_checkpoints.db"
    return Path(os.environ.get("NETFIX_DIAGNOSIS_DB_PATH", str(default_p)))


def get_read_only_connection(
    db_path: Optional[str] = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Yield a strictly read-only SQLite connection.

    Uses the SQLite URI 'file:<path>?mode=ro' to enforce kernel- and library-level
    write protection, ensuring that API read queries cannot mutate tables under any condition.
    """
    target = db_path if db_path is not None else get_db_path()
    resolved_path = Path(target).resolve()

    if not resolved_path.exists():
        logger.warning("Database file does not exist at: %s", resolved_path)
        raise FileNotFoundError(f"Database file not found at: {resolved_path}")

    # Enforce read-only URI mode
    uri_path = f"file:{resolved_path}?mode=ro"
    conn = sqlite3.connect(uri_path, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def read_only_session(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for non-FastAPI dependency usage of read-only SQLite connections."""
    for conn in get_read_only_connection(db_path):
        yield conn


def get_safe_executor_mode() -> str:
    """Determine the active executor mode without initializing the RemediationController.

    Returns 'LIVE_GATED' if live execution flags are present in the environment,
    otherwise defaults safely to 'MOCK'.
    """
    if os.environ.get("NETFIX_ALLOW_LIVE_EXECUTION", "").lower() in ("1", "true", "yes"):
        return "LIVE_GATED"
    return os.environ.get("NETFIX_EXECUTOR_MODE", "MOCK").upper()


def get_cors_origins() -> List[str]:
    """Return allowlisted CORS origins for frontend development and production."""
    raw = os.environ.get(
        "NETFIX_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
