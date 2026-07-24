# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Filter→SQL translation tests (port of NwpFilterTranslatorTests /
SqlQueryBuilderTests). Pure logic — no database."""

from __future__ import annotations

import pytest

from nps_sdk.nwp.frames import QueryFrame, QueryOrderClause
from nps_sdk.nwp.memory_node_server import (
    MemoryNodeField,
    MemoryNodeOptions,
    MemoryNodeSchema,
)
from nps_sdk.nwp.memory_node_sql import (
    DatabaseDialect,
    NwpFilterError,
    NwpFilterTranslator,
    ParamStyle,
    SqlQueryBuilder,
    decode_cursor,
    encode_cursor,
)


def _schema() -> MemoryNodeSchema:
    return MemoryNodeSchema(
        table_name="docs",
        primary_key="id",
        fields=[
            MemoryNodeField("id", "number"),
            MemoryNodeField("title", "string"),
            MemoryNodeField("score", "number"),
            MemoryNodeField("active", "boolean"),
            # column_name differs from name to exercise projection aliasing.
            MemoryNodeField("author", "string", column_name="author_name"),
        ],
    )


def _translate(filt, dialect=DatabaseDialect.POSTGRESQL):
    t = NwpFilterTranslator(_schema(), dialect)
    params: dict = {}
    where = t.translate(filt, params)
    return where, params


# ── Operator table ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("op,sql_op", [
    ("$eq", "="), ("$ne", "<>"), ("$lt", "<"),
    ("$lte", "<="), ("$gt", ">"), ("$gte", ">="),
])
def test_simple_comparison_operators(op, sql_op):
    where, params = _translate({"score": {op: 5}})
    assert where == f'"score" {sql_op} @p0'
    assert params == {"p0": 5}


def test_contains_wraps_in_like_wildcards():
    where, params = _translate({"title": {"$contains": "hello"}})
    assert where == '"title" LIKE @p0'
    assert params == {"p0": "%hello%"}


def test_bare_equality_shorthand():
    where, params = _translate({"title": "x"})
    assert where == '"title" = @p0'
    assert params == {"p0": "x"}


def test_in_expands_to_placeholder_list():
    where, params = _translate({"id": {"$in": [1, 2, 3]}})
    assert where == '"id" IN (@p0, @p1, @p2)'
    assert params == {"p0": 1, "p1": 2, "p2": 3}


def test_nin():
    where, params = _translate({"id": {"$nin": [7]}})
    assert where == '"id" NOT IN (@p0)'
    assert params == {"p0": 7}


def test_empty_in_is_always_false_empty_nin_always_true():
    where_in, _ = _translate({"id": {"$in": []}})
    where_nin, _ = _translate({"id": {"$nin": []}})
    assert where_in == "1=0"
    assert where_nin == "1=1"


def test_between():
    where, params = _translate({"score": {"$between": [1, 10]}})
    assert where == '"score" BETWEEN @p0 AND @p1'
    assert params == {"p0": 1, "p1": 10}


def test_between_requires_two_values():
    with pytest.raises(NwpFilterError):
        _translate({"score": {"$between": [1]}})


# ── Boolean nesting ─────────────────────────────────────────────────────────

def test_and_of_conditions():
    where, params = _translate({"$and": [{"score": {"$gte": 1}}, {"active": True}]})
    assert where == '("score" >= @p0 AND "active" = @p1)'
    assert params == {"p0": 1, "p1": True}


def test_or_of_conditions():
    where, _ = _translate({"$or": [{"id": 1}, {"id": 2}]})
    assert where == '("id" = @p0 OR "id" = @p1)'


def test_not_negates_inner():
    where, _ = _translate({"$not": {"active": True}})
    assert where == 'NOT "active" = @p0'


def test_nested_and_or():
    filt = {"$and": [
        {"active": True},
        {"$or": [{"score": {"$lt": 5}}, {"score": {"$gt": 90}}]},
    ]}
    where, params = _translate(filt)
    assert where == (
        '("active" = @p0 AND ("score" < @p1 OR "score" > @p2))')
    assert params == {"p0": True, "p1": 5, "p2": 90}


def test_multiple_top_level_fields_joined_by_and():
    where, _ = _translate({"active": True, "score": {"$gt": 3}})
    assert where == '("active" = @p0 AND "score" > @p1)'


def test_unknown_field_raises():
    with pytest.raises(NwpFilterError) as exc:
        _translate({"nope": 1})
    assert exc.value.nwp_error_code == "NWP-QUERY-FIELD-UNKNOWN"


def test_unknown_operator_raises():
    with pytest.raises(NwpFilterError):
        _translate({"score": {"$bogus": 1}})


def test_none_filter_is_empty_where():
    where, params = _translate(None)
    assert where == ""
    assert params == {}


def test_sqlserver_dialect_uses_bracket_quoting():
    where, _ = _translate({"score": {"$eq": 1}}, DatabaseDialect.SQL_SERVER)
    assert where == "[score] = @p0"


def test_named_colon_param_style():
    t = NwpFilterTranslator(_schema(), DatabaseDialect.POSTGRESQL,
                            ParamStyle.NAMED_COLON)
    params: dict = {}
    where = t.translate({"score": {"$eq": 1}}, params)
    assert where == '"score" = :p0'


# ── SqlQueryBuilder ─────────────────────────────────────────────────────────

def _opts():
    return MemoryNodeOptions(node_id="n", schema=_schema(),
                             default_limit=20, max_limit=1000)


def test_build_full_select_default_projection_and_order():
    frame = QueryFrame(limit=10)
    sql, params = SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build(frame, _opts())
    assert sql == (
        'SELECT "id", "title", "score", "active", "author_name" '
        'FROM "docs" ORDER BY "id" LIMIT @_limit OFFSET @_offset')
    assert params == {"_limit": 10, "_offset": 0}


def test_build_with_filter_order_and_projection_alias():
    frame = QueryFrame(
        limit=5,
        filter={"score": {"$gte": 2}},
        fields=("author", "title"),
        order=(QueryOrderClause("score", "DESC"),),
    )
    sql, params = SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build(frame, _opts())
    assert sql == (
        'SELECT "author_name" AS "author", "title" FROM "docs" '
        'WHERE "score" >= @p0 ORDER BY "score" DESC LIMIT @_limit OFFSET @_offset')
    assert params == {"p0": 2, "_limit": 5, "_offset": 0}


def test_build_zero_limit_uses_default_limit():
    frame = QueryFrame(limit=0)
    sql, params = SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build(frame, _opts())
    assert params["_limit"] == 20


def test_build_clamps_to_max_limit():
    frame = QueryFrame(limit=99999)
    sql, params = SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build(frame, _opts())
    assert params["_limit"] == 1000


def test_build_sqlserver_pagination_syntax():
    frame = QueryFrame(limit=10)
    sql, _ = SqlQueryBuilder(_schema(), DatabaseDialect.SQL_SERVER).build(frame, _opts())
    assert "OFFSET @_offset ROWS FETCH NEXT @_limit ROWS ONLY" in sql
    assert sql.startswith("SELECT [id], [title]")


def test_build_count():
    frame = QueryFrame(filter={"active": True})
    sql, params = SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build_count(frame)
    assert sql == 'SELECT COUNT(*) FROM "docs" WHERE "active" = @p0'
    assert params == {"p0": True}


def test_build_unknown_projection_field_raises():
    frame = QueryFrame(fields=("ghost",))
    with pytest.raises(NwpFilterError):
        SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build(frame, _opts())


def test_build_unknown_order_field_raises():
    frame = QueryFrame(order=(QueryOrderClause("ghost", "ASC"),))
    with pytest.raises(NwpFilterError):
        SqlQueryBuilder(_schema(), DatabaseDialect.POSTGRESQL).build(frame, _opts())


def test_builder_requires_table_and_primary_key():
    schema = MemoryNodeSchema(fields=[MemoryNodeField("id", "number")])
    with pytest.raises(NwpFilterError):
        SqlQueryBuilder(schema, DatabaseDialect.POSTGRESQL)


# ── Cursor round-trip ───────────────────────────────────────────────────────

def test_cursor_round_trip():
    cur = encode_cursor(40)
    assert cur is not None
    assert "=" not in cur
    assert decode_cursor(cur) == 40


def test_cursor_zero_is_none():
    assert encode_cursor(0) is None
    assert encode_cursor(-5) is None


def test_decode_invalid_cursor_returns_zero():
    assert decode_cursor(None) == 0
    assert decode_cursor("") == 0
    assert decode_cursor("!!!not-base64!!!") == 0
