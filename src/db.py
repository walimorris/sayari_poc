"""DuckDB connection helper. See README.md for the overall data flow."""
import time
from pathlib import Path

import duckdb

from src.config import DB_PATH, CACHE_DIR


def _clear_stale_wal() -> None:
    """
    DuckDB tries to delete a leftover write-ahead-log file when opening a
    database. On some mounted/synced filesystems, file deletion is blocked
    even though rename is allowed, which turns that cleanup step into a fatal
    IOException on connect. Renaming any stale .wal out of the way first
    avoids the failure without needing delete permission.
    """
    wal_path = DB_PATH.with_suffix(DB_PATH.suffix + ".wal")
    if wal_path.exists():
        backup_dir = CACHE_DIR / "duckdb_wal_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        wal_path.rename(backup_dir / f"{wal_path.name}.{int(time.time())}")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if not read_only:
        _clear_stale_wal()
    return duckdb.connect(str(DB_PATH), read_only=read_only)
