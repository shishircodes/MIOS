"""Database layer. Talks to Neon PostgreSQL in deployment, SQLite locally.

Why both, rather than a clean cut to Postgres:

* Neon is the deployment target — set `DATABASE_URL` and everything (pipeline,
  API, digest, KPI harness) uses it.
* SQLite remains the fallback when `DATABASE_URL` is unset, which keeps
  `pytest` runnable offline and lets someone clone the repo and run the whole
  pipeline without provisioning a database. That reproducibility is a stated
  goal of the project, and a Postgres-only cut would have thrown it away.

The abstraction is deliberately thin. It is *not* an ORM: modules keep writing
plain SQL with `?` placeholders, and this module handles the three things that
actually differ between the two drivers:

1. **Placeholders** — sqlite3 uses `?`, psycopg uses `%s`. `_to_pg` rewrites
   them, skipping anything inside string literals.
2. **Rows** — `sqlite3.Row` supports both `row["col"]` and `row[0]`, and callers
   rely on both (including tuple unpacking). `Row` below reproduces that for
   psycopg, so no query-consuming code had to change.
3. **Duplicate-key errors** — `sqlite3.IntegrityError` vs
   `psycopg.errors.UniqueViolation`, unified as `UniqueViolation` here.

The schema in `schema.sql` is written in the dialect-neutral subset both
engines accept (`CREATE TABLE IF NOT EXISTS`, partial unique indexes,
`ON CONFLICT ... DO UPDATE`), so there is only one copy of the DDL.
"""
from __future__ import annotations

import atexit
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from config.settings import settings

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

#: Prefixes that mean "this is a PostgreSQL DSN, not a file path".
PG_SCHEMES = ("postgresql://", "postgres://", "postgresql+psycopg://")


class UniqueViolation(Exception):
    """A duplicate-key error, normalised across both backends."""


# --------------------------------------------------------------------------
# DSN resolution
# --------------------------------------------------------------------------


def is_postgres(target: str | Path | None) -> bool:
    return isinstance(target, str) and target.startswith(PG_SCHEMES)


def normalise_pg_dsn(dsn: str) -> str:
    """Make a Neon connection string safe for psycopg.

    Neon's console hands out `postgresql://...` URLs, but some tools (and older
    Heroku-style configs) emit `postgres://`, which psycopg rejects. SQLAlchemy's
    `postgresql+psycopg://` form is accepted too, for people copying from there.

    Also defaults `sslmode=require`: Neon refuses plaintext connections, and the
    failure without it is an opaque server-closed-connection rather than
    anything that mentions TLS.
    """
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn[len("postgresql+psycopg://"):]
    elif dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]

    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    return dsn


def resolve_target(target: str | Path | None = None) -> str | Path:
    """Work out what to connect to.

    `None` means "use configuration": `DATABASE_URL` if set, otherwise the
    SQLite file at `DB_PATH`. An explicit argument always wins, so tests can
    pass a `tmp_path` and callers can point at a specific database.
    """
    if target is not None:
        return target
    if settings.database_url:
        return settings.database_url
    return settings.db_path


# --------------------------------------------------------------------------
# Placeholder translation
# --------------------------------------------------------------------------

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _to_pg(sql: str) -> str:
    """Rewrite `?` placeholders as `%s`, leaving string literals alone.

    Also escapes any literal `%` so psycopg doesn't read it as a placeholder of
    its own — `LIKE '%foo%'` would otherwise break.
    """
    out: list[str] = []
    last = 0
    for m in _STRING_LITERAL.finditer(sql):
        out.append(sql[last:m.start()].replace("%", "%%").replace("?", "%s"))
        out.append(m.group(0).replace("%", "%%"))
        last = m.end()
    out.append(sql[last:].replace("%", "%%").replace("?", "%s"))
    return "".join(out)


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


class Row(dict):
    """A dict row that also supports positional access.

    `sqlite3.Row` allows `row["company_name"]`, `row[0]` and tuple unpacking;
    plain psycopg dict rows allow only the first. Callers in this codebase use
    all three, so psycopg rows are wrapped in this to keep them interchangeable.
    """

    __slots__ = ("_order",)

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        super().__init__(zip(columns, values))
        self._order = list(columns)

    def __getitem__(self, key: Any) -> Any:
        # dict.__getitem__ explicitly rather than super(): the zero-argument form
        # has no __class__ cell inside a genexp/comprehension and raises there.
        if isinstance(key, int):
            return dict.__getitem__(self, self._order[key])
        if isinstance(key, slice):
            return tuple(dict.__getitem__(self, k) for k in self._order[key])
        return dict.__getitem__(self, key)

    def __iter__(self) -> Iterator[Any]:
        # Iterating a sqlite3.Row yields values, which is what tuple-unpacking
        # callers expect. A plain dict would yield keys.
        return (dict.__getitem__(self, k) for k in self._order)

    def keys(self) -> list[str]:  # type: ignore[override]
        return list(self._order)

    def __len__(self) -> int:
        return len(self._order)


def _pg_row_factory(cursor):
    columns = [c.name for c in (cursor.description or [])]

    def make(values):
        return Row(columns, values)

    return make


# --------------------------------------------------------------------------
# Connection wrapper
# --------------------------------------------------------------------------


class Connection:
    """Uniform connection surface over sqlite3 and psycopg.

    Exposes `execute` / `executemany` / `commit`, all taking `?`-style SQL, and
    raises `UniqueViolation` for duplicate keys on either backend.
    """

    def __init__(self, raw: Any, backend: str):
        self._raw = raw
        self.backend = backend

    @property
    def raw(self) -> Any:
        """The underlying driver connection, for backend-specific work."""
        return self._raw

    @property
    def is_postgres(self) -> bool:
        return self.backend == "postgres"

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        params = tuple(params or ())
        if self.backend == "postgres":
            import psycopg

            cur = self._raw.cursor()
            try:
                if params:
                    cur.execute(_to_pg(sql), params)
                else:
                    # Send verbatim when there is nothing to bind. psycopg only
                    # unescapes `%%` when parameters are supplied, so running a
                    # param-free query through _to_pg would leave a literal
                    # `LIKE 'foo_%%'` in the SQL and silently match nothing.
                    cur.execute(sql)
            except psycopg.errors.UniqueViolation as exc:
                raise UniqueViolation(str(exc)) from exc
            return cur
        try:
            return self._raw.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            # SQLite reports every constraint failure as IntegrityError; only
            # uniqueness is a duplicate, the rest are genuine bugs worth raising.
            if "unique" in str(exc).lower():
                raise UniqueViolation(str(exc)) from exc
            raise

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(r) for r in seq]
        if not rows:
            return
        if self.backend == "postgres":
            cur = self._raw.cursor()
            cur.executemany(_to_pg(sql), rows)
            return
        self._raw.executemany(sql, rows)

    def executescript(self, sql: str) -> None:
        """Run multi-statement DDL."""
        if self.backend == "postgres":
            # psycopg runs a multi-statement string in one implicit transaction.
            self._raw.execute(sql)
            return
        self._raw.executescript(sql)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()


# --------------------------------------------------------------------------
# Connection pooling
# --------------------------------------------------------------------------
#
# Opening a Postgres connection is not one network round trip, it is roughly
# seven: TCP, then the TLS handshake, then Postgres startup and authentication.
# Measured against Neon in ap-southeast-2 that is ~77 ms against an ~11.5 ms
# round trip — about 87% of the cost of a short query is getting the connection,
# not running it. Deploy the API in a different region from the database and
# every one of those round trips is paid at intercontinental latency.
#
# So Postgres connections are pooled and reused. SQLite is left alone: opening a
# local file is free, and a pool there would only add machinery.

_pool: Any = None
_pool_dsn: str | None = None
_pool_lock = threading.Lock()

#: Small on purpose. This is one API process talking to a serverless database
#: with its own connection ceiling, and the workload is a handful of short
#: queries per request rather than sustained concurrency.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 8

#: Neon suspends an idle compute, which kills pooled connections with it. This
#: recycles them first, so the pool does not hand out a socket the far end has
#: already dropped.
POOL_MAX_IDLE_SECONDS = 240


def _get_pool(dsn: str):
    """The process-wide pool for `dsn`, created on first use.

    Lazy rather than created at import: the CLI entry points (`loader.check`,
    `pipeline.live`) import this module too, and most of them run a single query
    and exit.
    """
    global _pool, _pool_dsn
    with _pool_lock:
        if _pool is not None and _pool_dsn == dsn:
            return _pool
        if _pool is not None:
            _pool.close()
            _pool = None
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            dsn,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            max_idle=POOL_MAX_IDLE_SECONDS,
            # Autocommit is the *pool's* default because reads are the hot path
            # and a read has nothing to commit. Writers are not left unprotected:
            # `connect()` opens an explicit transaction unless asked for a read,
            # so all-or-nothing behaviour is unchanged for everything that
            # writes. Measured, this is the difference between 2 and 3.6 network
            # round trips per query.
            kwargs={"row_factory": _pg_row_factory, "autocommit": True},
            # Costs one round trip per checkout, and buys back six. Without it a
            # connection the database has closed underneath us surfaces as a
            # failed request rather than a transparently replaced socket.
            check=ConnectionPool.check_connection,
            timeout=15.0,
            open=True,
            name="mios",
        )
        _pool_dsn = dsn
        log.info("db: opened a connection pool (min=%d max=%d)", POOL_MIN_SIZE, POOL_MAX_SIZE)
        return _pool


def close_pool() -> None:
    """Shut the pool down. Safe to call when there isn't one."""
    global _pool, _pool_dsn
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
            _pool_dsn = None


# A pool holds background worker threads; without this an interpreter that
# exits mid-request can hang waiting on them.
atexit.register(close_pool)


@contextmanager
def connect(target: str | Path | None = None, *, readonly: bool = False) -> Iterator[Connection]:
    """Open a connection to Postgres or SQLite and commit on clean exit.

    Rolls back and re-raises on error, so a failed run never half-writes.

    Postgres connections come from a pool and are returned to it rather than
    closed — see above.

    `readonly=True` skips the surrounding transaction, which is worth about one
    network round trip per call. It is opt-in rather than inferred: a caller
    that writes inside a read-only block would lose all-or-nothing behaviour
    silently, and that is not a thing to get wrong by default. Anything that
    writes must leave it alone.
    """
    resolved = resolve_target(target)

    if is_postgres(resolved):
        pool = _get_pool(normalise_pg_dsn(str(resolved)))
        with pool.connection() as conn:
            if readonly:
                yield Connection(conn, "postgres")
            else:
                # Explicit, because the pool hands out autocommit connections.
                # Commits on a clean exit, rolls back on an exception — the same
                # contract this function has always had.
                with conn.transaction():
                    yield Connection(conn, "postgres")
        return

    path = Path(resolved)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    wrapped = Connection(conn, "sqlite")
    try:
        yield wrapped
        wrapped.commit()
    except Exception:
        wrapped.rollback()
        raise
    finally:
        conn.close()


def backend_label(target: str | Path | None = None) -> str:
    """Engine name for display, with no host or credentials in it.

    `describe` includes the hostname, which is fine for logs but not for a
    browser payload; this is the safe-to-render version.
    """
    return "Neon PostgreSQL" if is_postgres(resolve_target(target)) else "SQLite"


def describe(target: str | Path | None = None) -> str:
    """Human-readable label for the active database, safe to log.

    Credentials are stripped — a Neon DSN embeds the password, so the raw string
    must never reach a log line or an API response.
    """
    resolved = resolve_target(target)
    if not is_postgres(resolved):
        return f"sqlite:{resolved}"
    from urllib.parse import urlparse

    u = urlparse(normalise_pg_dsn(str(resolved)))
    return f"postgres:{u.hostname or '?'}{u.path or ''}"
