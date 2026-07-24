# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
SQL-backed Memory Node providers (port of ``PostgreSqlMemoryNodeProvider`` +
``SqlServerMemoryNodeProvider``).

The providers build on :class:`~nps_sdk.nwp.memory_node_sql.SqlQueryBuilder`
and delegate execution to an **injectable async DB executor**
(:class:`IAsyncDbExecutor`), so they are fully testable without a live database.

A concrete :class:`SqliteMemoryNodeProvider` backed by the Python stdlib
``sqlite3`` module is provided for local/embedded use and tests. Concrete
Postgres / SQL Server bindings (``asyncpg`` / ``aioodbc``) are **deferred** in
this offline environment — wire an :class:`IAsyncDbExecutor` over your driver of
choice to use :class:`SqlMemoryNodeProvider` against those engines.
"""

from __future__ import annotations

import abc
import asyncio
import sqlite3
from typing import Any, Sequence

from nps_sdk.nwp.frames import QueryFrame
from nps_sdk.nwp.memory_node_server import (
    IMemoryNodeProvider,
    MemoryNodeOptions,
    MemoryNodeQueryResult,
    MemoryNodeRow,
    MemoryNodeSchema,
)
from nps_sdk.nwp.memory_node_sql import (
    DatabaseDialect,
    ParamStyle,
    SqlQueryBuilder,
    encode_cursor,
)


class IAsyncDbExecutor(abc.ABC):
    """Minimal async row-executor abstraction the SQL providers depend on.

    Implementations translate a parameterized SQL string + a params dict into a
    list of row dicts. Kept intentionally tiny so any driver (asyncpg, aioodbc,
    aiosqlite, or a stdlib wrapper) can satisfy it.
    """

    @abc.abstractmethod
    async def query_rows(self, sql: str, params: dict[str, Any]) -> list[MemoryNodeRow]:
        ...


class SqlMemoryNodeProvider(IMemoryNodeProvider):
    """Memory Node provider that translates queries via :class:`SqlQueryBuilder`
    and executes them through an injected :class:`IAsyncDbExecutor`.

    Fetches ``limit + 1`` rows so a ``next_cursor`` can be emitted without a
    separate COUNT round-trip (matches the .NET provider's paging behaviour).
    """

    def __init__(
        self,
        schema: MemoryNodeSchema,
        dialect: DatabaseDialect,
        executor: IAsyncDbExecutor,
        param_style: ParamStyle = ParamStyle.AT_NAME,
    ) -> None:
        self._executor = executor
        self._builder = SqlQueryBuilder(schema, dialect, param_style)
        self._style = param_style

    async def query(self, frame: QueryFrame, opts: MemoryNodeOptions) -> MemoryNodeQueryResult:
        # Probe one extra row to detect a further page.
        limit = min(
            opts.default_limit if frame.limit == 0 else frame.limit,
            opts.max_limit,
        )
        sql, params = self._builder.build(frame, opts)
        # Bump the emitted limit by one to peek at the next page.
        params["_limit"] = int(limit) + 1

        rows = await self._executor.query_rows(sql, params)

        offset = params["_offset"]
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = encode_cursor(offset + limit) if has_more else None
        return MemoryNodeQueryResult(rows=page, next_cursor=next_cursor)


# ── Concrete sqlite executor (stdlib sqlite3, run off-thread) ───────────────────

class SqliteDbExecutor(IAsyncDbExecutor):
    """:class:`IAsyncDbExecutor` over the stdlib ``sqlite3`` module.

    The synchronous driver runs in a worker thread so it never blocks the event
    loop. Expects SQL rendered with :data:`ParamStyle.NAMED_COLON`.

    The connection MUST be opened with ``check_same_thread=False`` since queries
    execute off the event-loop thread; a per-executor lock serialises access so
    that relaxation stays safe.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    async def query_rows(self, sql: str, params: dict[str, Any]) -> list[MemoryNodeRow]:
        async with self._lock:
            return await asyncio.to_thread(self._query_sync, sql, params)

    def _query_sync(self, sql: str, params: dict[str, Any]) -> list[MemoryNodeRow]:
        cur = self._conn.execute(sql, params)
        try:
            return [dict(r) for r in cur.fetchall()]
        finally:
            cur.close()


class SqliteMemoryNodeProvider(SqlMemoryNodeProvider):
    """Convenience :class:`SqlMemoryNodeProvider` bound to a sqlite connection.

    Uses the PostgreSQL quoting dialect (``"col"``) since sqlite accepts
    double-quoted identifiers, and the ``:name`` placeholder style the stdlib
    driver expects.
    """

    def __init__(self, schema: MemoryNodeSchema, connection: sqlite3.Connection) -> None:
        super().__init__(
            schema,
            DatabaseDialect.POSTGRESQL,
            SqliteDbExecutor(connection),
            ParamStyle.NAMED_COLON,
        )
