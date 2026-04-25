"""
Robust, thread-safe SQLite database manager for the Telegram game platform.

Features:
  - Thread-local connections via get_db()
  - Schema initialization via init_db()
  - Convenience helpers: execute(), fetchone(), fetchall()
  - Async wrappers: async_execute(), async_fetchone(), async_fetchall()
  - Transaction context manager with auto-commit/rollback
  - WAL mode for concurrent read/write performance
  - Busy timeout for lock contention
  - Foreign key enforcement
"""

import asyncio
import sqlite3
import threading
import logging
from contextlib import contextmanager, asynccontextmanager
from typing import Any, Optional, List, Dict

from .schema import SCHEMA_SQL
import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-local storage – each thread gets its own sqlite3.Connection.
# ---------------------------------------------------------------------------
_local = threading.local()


def _connection_kwargs() -> dict:
    """Return standardised kwargs used for every new connection."""
    return {
        "database": str(config.DB_PATH),
        "timeout": config.DB_TIMEOUT,
        "check_same_thread": False,
        "isolation_level": None,  # autocommit; we manage transactions ourselves
        "cached_statements": 128,
    }


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply pragmatic pragmas and row factory to a connection."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout={}".format(config.DB_TIMEOUT * 1000))
    conn.execute("PRAGMA cache_size=-64000")  # 64 MB
    conn.execute("PRAGMA temp_store=MEMORY")


def get_db() -> sqlite3.Connection:
    """
    Return a thread-local sqlite3.Connection.

    On first call within a thread a new connection is opened, configured,
    and stored in threading.local(). Subsequent calls in the same thread
    return the same connection.

    If the stored connection has been closed (or is otherwise unusable),
    a fresh one is transparently created.
    """
    conn: Optional[sqlite3.Connection] = getattr(_local, "connection", None)

    if conn is not None:
        try:
            # Lightweight liveness check – will raise if the connection
            # underlying handle has been closed.
            conn.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            logger.warning("Stale DB connection detected – reconnecting.")
            try:
                conn.close()
            except Exception:
                pass
            conn = None

    if conn is None:
        conn = sqlite3.connect(**_connection_kwargs())
        _configure_connection(conn)
        _local.connection = conn
        logger.debug("New DB connection opened for thread %s", threading.current_thread().name)

    return conn


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_schema_initialized = threading.Event()


def init_db() -> None:
    """
    Create all tables defined in SCHEMA_SQL if they do not already exist.

    Safe to call multiple times; subsequent calls are no-ops after the
    first successful execution within the process.
    """
    if _schema_initialized.is_set():
        return

    conn = get_db()
    try:
        # executescript() implicitly commits any pending transaction before
        # running, and auto-commits each statement inside the script.
        # Since all statements use IF NOT EXISTS, this is safe for idempotent
        # re-runs and does not need an outer BEGIN/COMMIT wrapper.
        conn.executescript(SCHEMA_SQL)
        logger.info("Database schema initialised / verified.")
    except sqlite3.Error as exc:
        logger.error("Failed to initialise schema: %s", exc)
        raise
    finally:
        _schema_initialized.set()


# ---------------------------------------------------------------------------
# Convenience query helpers
# ---------------------------------------------------------------------------

def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """
    Execute a single SQL statement and return the cursor.

    Automatically commits after the statement unless the caller is inside
    a `transaction()` block (detected via thread-local flag).
    """
    conn = get_db()
    in_tx = getattr(_local, "in_transaction", False)

    if in_tx:
        cursor = conn.execute(sql, params)
    else:
        conn.execute("BEGIN")
        try:
            cursor = conn.execute(sql, params)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return cursor


def fetchone(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Execute *sql* and return a single row (or None)."""
    conn = get_db()
    cursor = conn.execute(sql, params)
    return cursor.fetchone()


def fetchall(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Execute *sql* and return all matching rows."""
    conn = get_db()
    cursor = conn.execute(sql, params)
    return cursor.fetchall()


# ---------------------------------------------------------------------------
# Transaction context manager
# ---------------------------------------------------------------------------

@contextmanager
def transaction():
    """
    Context manager that wraps a block in a single database transaction.

    Usage::

        with transaction():
            execute("UPDATE wallets SET balance = balance + ? WHERE user_id = ?", (amount, uid))
            execute("INSERT INTO transactions ...", (...))

    On normal exit the transaction is COMMIT-ed.  If an exception escapes
    the block the transaction is ROLLBACK-ed and the exception propagates.

    Nested usage within the same thread is safe: only the outermost block
    controls the actual BEGIN/COMMIT/ROLLBACK.
    """
    conn = get_db()

    # Track nesting depth so inner with-blocks don't prematurely commit.
    depth = getattr(_local, "tx_depth", 0)

    if depth == 0:
        conn.execute("BEGIN")
        _local.in_transaction = True
        _local.tx_depth = 1
    else:
        _local.tx_depth = depth + 1

    try:
        yield conn
    except Exception:
        if _local.tx_depth == 1:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error as rollback_exc:
                logger.error("ROLLBACK failed: %s", rollback_exc)
            finally:
                _local.in_transaction = False
                _local.tx_depth = 0
        else:
            _local.tx_depth -= 1
        raise
    else:
        if _local.tx_depth == 1:
            try:
                conn.execute("COMMIT")
            except sqlite3.Error as commit_exc:
                logger.error("COMMIT failed – rolling back: %s", commit_exc)
                conn.execute("ROLLBACK")
                raise
            finally:
                _local.in_transaction = False
                _local.tx_depth = 0
        else:
            _local.tx_depth -= 1


# ---------------------------------------------------------------------------
# Cleanup helper
# ---------------------------------------------------------------------------

def close_db() -> None:
    """
    Close the current thread's connection (if any).

    Typically called during application shutdown or in thread teardown.
    """
    conn: Optional[sqlite3.Connection] = getattr(_local, "connection", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        finally:
            _local.connection = None
            _local.in_transaction = False
            _local.tx_depth = 0


# ---------------------------------------------------------------------------
# Async wrappers – run synchronous DB operations in a thread executor
# so they can be `await`-ed from asyncio handlers without blocking.
# ---------------------------------------------------------------------------

async def async_execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """Async version of execute(). Runs the query in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: execute(sql, params))


async def async_fetchone(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Async version of fetchone(). Runs the query in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fetchone(sql, params))


async def async_fetchall(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Async version of fetchall(). Runs the query in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fetchall(sql, params))


@asynccontextmanager
async def async_transaction():
    """
    Async version of transaction(). Runs the transaction in a thread executor.

    Usage::

        async with async_transaction():
            await async_execute("UPDATE wallets SET balance = ...", (...))
            await async_execute("INSERT INTO transactions ...", (...))
    """
    loop = asyncio.get_running_loop()
    # Start the transaction in a thread
    await loop.run_in_executor(None, _begin_transaction)
    try:
        yield
    except Exception:
        await loop.run_in_executor(None, _rollback_transaction)
        raise
    else:
        await loop.run_in_executor(None, _commit_transaction)


def _begin_transaction() -> None:
    """Begin a transaction (called from thread executor)."""
    conn = get_db()
    depth = getattr(_local, "tx_depth", 0)
    if depth == 0:
        conn.execute("BEGIN")
        _local.in_transaction = True
        _local.tx_depth = 1
    else:
        _local.tx_depth = depth + 1


def _commit_transaction() -> None:
    """Commit the current transaction (called from thread executor)."""
    if getattr(_local, "tx_depth", 0) == 1:
        try:
            conn = get_db()
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            logger.error("COMMIT failed – rolling back: %s", exc)
            conn.execute("ROLLBACK")
            raise
        finally:
            _local.in_transaction = False
            _local.tx_depth = 0
    else:
        _local.tx_depth = getattr(_local, "tx_depth", 1) - 1


def _rollback_transaction() -> None:
    """Rollback the current transaction (called from thread executor)."""
    if getattr(_local, "tx_depth", 0) == 1:
        try:
            conn = get_db()
            conn.execute("ROLLBACK")
        except sqlite3.Error as exc:
            logger.error("ROLLBACK failed: %s", exc)
        finally:
            _local.in_transaction = False
            _local.tx_depth = 0
    else:
        _local.tx_depth = getattr(_local, "tx_depth", 1) - 1
