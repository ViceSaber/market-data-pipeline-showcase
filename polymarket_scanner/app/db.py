import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "polymarket.db"

EXPECTED_PRAGMAS = {
    "journal_mode": "wal",
    "synchronous": 1,  # NORMAL=1 in WAL mode
    "busy_timeout": 30000,
    "foreign_keys": 1,
}

SQLITE_RETRY_ATTEMPTS = 5
SQLITE_RETRY_BASE_DELAY = 0.05
SQLITE_RETRY_BACKOFF = 2.0


def is_lock_error(exc: Exception) -> bool:
    """Return True when an exception is SQLite lock/busy related."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "database is locked" in text or "database is busy" in text


def run_with_retry(fn, *, attempts: int = SQLITE_RETRY_ATTEMPTS,
                   base_delay: float = SQLITE_RETRY_BASE_DELAY,
                   backoff: float = SQLITE_RETRY_BACKOFF,
                   label: str = "sqlite op"):
    """Retry a small SQLite operation on lock/busy errors.

    Use only for short write sections (single execute / commit / tiny transaction),
    not for large read-modify-write workflows.
    """
    delay = base_delay
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not is_lock_error(exc) or attempt >= attempts:
                raise
            last_exc = exc
            log.warning("%s hit lock on attempt %d/%d; retrying in %.2fs",
                        label, attempt, attempts, delay)
            time.sleep(delay)
            delay *= backoff
    if last_exc:
        raise last_exc


def execute_with_retry(conn: sqlite3.Connection, sql: str, params=(), *,
                       attempts: int = SQLITE_RETRY_ATTEMPTS,
                       label: str = "sqlite execute"):
    """Retry a single execute on lock/busy errors."""
    return run_with_retry(lambda: conn.execute(sql, params), attempts=attempts, label=label)



def commit_with_retry(conn: sqlite3.Connection, *, attempts: int = SQLITE_RETRY_ATTEMPTS,
                      label: str = "sqlite commit"):
    """Retry commit on lock/busy errors."""
    return run_with_retry(conn.commit, attempts=attempts, label=label)



def get_conn() -> sqlite3.Connection:
    """Return a SQLite connection with hardened PRAGMAs.

    Validates expected PRAGMAs after setting them and warns on mismatch.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")  # wait 30s before raising lock error
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")

    # Validate PRAGMAs were applied
    try:
        actual = {
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0].lower(),
            "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
            "busy_timeout": conn.execute("PRAGMA busy_timeout").fetchone()[0],
            "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
        }
        for key, expected in EXPECTED_PRAGMAS.items():
            if actual[key] != expected:
                log.warning("PRAGMA %s mismatch: expected=%s actual=%s",
                            key, expected, actual[key])
    except Exception as e:
        log.warning("PRAGMA validation failed: %s", e)

    log.debug("DB connection created: %s", DB_PATH)
    return conn
