"""Conservative per-connection SQLite tuning for the local desktop deployment."""
from __future__ import annotations

import sqlite3


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    """Apply safe session settings without changing the database journal format."""
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA cache_size=-32768")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection
