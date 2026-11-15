"""Database connection manager and initialization for NETfix SQLite storage.

Provides connection helpers, foreign-key enforcement, WAL mode configuration,
and schema initialization.
"""

from contextlib import contextmanager
import logging
import os
import sqlite3
from typing import Generator, Optional

from netfix.storage.schema import create_schema

logger = logging.getLogger("netfix.storage.database")


def get_default_db_path() -> str:
    """Return the canonical default path to data/netfix.db derived from project root."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    return os.path.join(project_root, "data", "netfix.db")


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a configured SQLite connection with foreign keys and WAL mode enabled.

    Args:
        db_path: Path to database file, ':memory:', or None (defaults to data/netfix.db).

    Returns:
        Configured sqlite3.Connection object with row_factory=sqlite3.Row.
    """
    target_path = db_path if db_path is not None else get_default_db_path()

    if target_path != ":memory:":
        target_path = os.path.abspath(target_path)
        dir_name = os.path.dirname(target_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row

    # Always enforce foreign keys on every connection
    conn.execute("PRAGMA foreign_keys = ON;")

    # Enable WAL mode and normal synchronization for disk-backed databases
    if target_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")

    return conn


def init_db(db_path: Optional[str] = None) -> str:
    """Initialize database schema idempotently.

    Args:
        db_path: Database path or None for default.

    Returns:
        The canonical path to the initialized database.
    """
    target_path = db_path if db_path is not None else get_default_db_path()
    conn = get_connection(target_path)
    try:
        create_schema(conn)
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Initialized NETfix SQLite database at: {target_path}")
    return target_path


class Database:
    """High-level SQLite database manager for NETfix."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Initialize database manager and guarantee schema readiness.

        Args:
            db_path: Path to SQLite database or None for default.
        """
        if db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = os.path.abspath(db_path) if db_path else get_default_db_path()

        init_db(self.db_path)

    def connect(self) -> sqlite3.Connection:
        """Open a new configured database connection."""
        return get_connection(self.db_path)

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding an active connection, closing it upon exit."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()
