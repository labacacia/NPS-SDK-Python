# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""SQL MemoryNode provider tests — sqlite-backed round-trip + injectable
executor SQL-generation assertions."""

from __future__ import annotations

import sqlite3

import pytest

from nps_sdk.nwp.frames import QueryFrame, QueryOrderClause
from nps_sdk.nwp.memory_node_server import (
    MemoryNodeField,
    MemoryNodeOptions,
    MemoryNodeQueryResult,
    MemoryNodeSchema,
)
from nps_sdk.nwp.memory_node_providers import (
    IAsyncDbExecutor,
    SqlMemoryNodeProvider,
    SqliteMemoryNodeProvider,
)
from nps_sdk.nwp.memory_node_sql import DatabaseDialect, ParamStyle, decode_cursor


def _schema() -> MemoryNodeSchema:
    return MemoryNodeSchema(
        table_name="docs",
        primary_key="id",
        fields=[
            MemoryNodeField("id", "number"),
            MemoryNodeField("title", "string"),
            MemoryNodeField("score", "number"),
        ],
    )


def _opts() -> MemoryNodeOptions:
    return MemoryNodeOptions(node_id="n", schema=_schema(),
                             default_limit=20, max_limit=1000)


# ── Injectable executor: SQL generation assertion ───────────────────────────

class _RecordingExecutor(IAsyncDbExecutor):
    def __init__(self, rows):
        self.rows = rows
        self.last_sql = None
        self.last_params = None

    async def query_rows(self, sql, params):
        self.last_sql = sql
        self.last_params = params
        return list(self.rows)


@pytest.mark.asyncio
async def test_sql_provider_generates_parameterized_sql_via_executor():
    execu = _RecordingExecutor(rows=[{"id": 1, "title": "a", "score": 5}])
    provider = SqlMemoryNodeProvider(_schema(), DatabaseDialect.POSTGRESQL, execu)
    frame = QueryFrame(limit=10, filter={"score": {"$gte": 3}})
    result = await provider.query(frame, _opts())

    assert isinstance(result, MemoryNodeQueryResult)
    assert '"score" >= @p0' in execu.last_sql
    assert execu.last_params["p0"] == 3
    # limit is bumped by 1 to peek at the next page.
    assert execu.last_params["_limit"] == 11
    assert execu.last_params["_offset"] == 0


@pytest.mark.asyncio
async def test_sql_provider_emits_next_cursor_when_extra_row_present():
    # limit=2 → provider asks for 3; return 3 → has_more.
    execu = _RecordingExecutor(rows=[{"id": i} for i in (1, 2, 3)])
    provider = SqlMemoryNodeProvider(_schema(), DatabaseDialect.POSTGRESQL, execu)
    result = await provider.query(QueryFrame(limit=2), _opts())
    assert len(result.rows) == 2
    assert result.next_cursor is not None
    assert decode_cursor(result.next_cursor) == 2


# ── Concrete sqlite round-trip ──────────────────────────────────────────────

@pytest.fixture()
def sqlite_conn():
    # check_same_thread=False: the executor runs the driver off the event loop
    # thread (see SqliteDbExecutor), so cross-thread use must be permitted.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, score INTEGER)")
    conn.executemany(
        "INSERT INTO docs (id, title, score) VALUES (?, ?, ?)",
        [(1, "alpha", 10), (2, "beta", 20), (3, "gamma", 30), (4, "delta", 40)],
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.asyncio
async def test_sqlite_provider_filter_and_order(sqlite_conn):
    provider = SqliteMemoryNodeProvider(_schema(), sqlite_conn)
    frame = QueryFrame(
        limit=10,
        filter={"score": {"$gte": 20}},
        order=(QueryOrderClause("score", "DESC"),),
    )
    result = await provider.query(frame, _opts())
    scores = [r["score"] for r in result.rows]
    assert scores == [40, 30, 20]
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_sqlite_provider_pagination(sqlite_conn):
    provider = SqliteMemoryNodeProvider(_schema(), sqlite_conn)
    page1 = await provider.query(QueryFrame(limit=2), _opts())
    assert [r["id"] for r in page1.rows] == [1, 2]
    assert page1.next_cursor is not None

    page2 = await provider.query(
        QueryFrame(limit=2, cursor=page1.next_cursor), _opts())
    assert [r["id"] for r in page2.rows] == [3, 4]
    assert page2.next_cursor is None


@pytest.mark.asyncio
async def test_sqlite_provider_in_and_projection(sqlite_conn):
    provider = SqliteMemoryNodeProvider(_schema(), sqlite_conn)
    frame = QueryFrame(limit=10, filter={"id": {"$in": [1, 3]}}, fields=("title",))
    result = await provider.query(frame, _opts())
    titles = sorted(r["title"] for r in result.rows)
    assert titles == ["alpha", "gamma"]
    # Projection restricts columns.
    assert set(result.rows[0].keys()) == {"title"}
