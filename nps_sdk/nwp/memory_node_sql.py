# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Filter→SQL translation for NWP Memory Nodes (NPS-2 §5.2 / §5.3).

Pure-logic port of the .NET reference ``NwpFilterTranslator`` +
``SqlQueryBuilder``. Translates the NWP filter DSL
(``$eq``/``$ne``/``$lt``/``$lte``/``$gt``/``$gte``/``$in``/``$nin``/
``$contains``/``$between``/``$and``/``$or``/``$not``) plus ordering, projection,
limit, and cursor pagination into a **parameterized** SQL string and a
parameter dict — with zero database dependency.

Field names are validated against the :class:`MemoryNodeSchema` before they
reach the SQL string, so untrusted filter input can never inject identifiers.

Placeholder form matches the .NET/Dapper wire form (``@name``) by default; the
sqlite provider requests :data:`ParamStyle.NAMED_COLON` (``:name``) since that
is the placeholder the Python ``sqlite3`` driver understands.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import enum
import json
from typing import Any

from nps_sdk.nwp.frames import QueryFrame, QueryOrderClause
from nps_sdk.nwp.memory_node_server import (
    MemoryNodeField,
    MemoryNodeOptions,
    MemoryNodeSchema,
)


# ── Error codes (mirror NPS.NWP.Http.NwpErrorCodes) ────────────────────────────

QUERY_FILTER_INVALID = "NWP-QUERY-FILTER-INVALID"
QUERY_FIELD_UNKNOWN = "NWP-QUERY-FIELD-UNKNOWN"


class NwpFilterError(Exception):
    """Raised when an NWP filter cannot be translated to SQL (mirrors
    .NET ``NwpFilterException``)."""

    def __init__(self, message: str, error_code: str = QUERY_FILTER_INVALID) -> None:
        super().__init__(message)
        self.nwp_error_code = error_code


class DatabaseDialect(enum.Enum):
    """Supported SQL dialects for quoting and pagination syntax."""

    SQL_SERVER = "sqlserver"
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"


class ParamStyle(enum.Enum):
    """Placeholder rendering for the emitted SQL.

    ``AT_NAME`` (``@name``) matches the .NET/Dapper wire form; ``NAMED_COLON``
    (``:name``) is what the Python ``sqlite3`` driver expects.
    """

    AT_NAME = "@"
    NAMED_COLON = ":"


# ── Filter translator ──────────────────────────────────────────────────────────

_SIMPLE_OPS = {
    "$eq": "=", "$ne": "<>", "$lt": "<", "$lte": "<=", "$gt": ">", "$gte": ">=",
}


class NwpFilterTranslator:
    """Translates an NWP filter predicate into a parameterized WHERE fragment."""

    def __init__(self, schema: MemoryNodeSchema, dialect: DatabaseDialect,
                 param_style: ParamStyle = ParamStyle.AT_NAME) -> None:
        self._schema = schema
        self._dialect = dialect
        self._style = param_style
        self._param_index = 0

    def _ph(self, name: str) -> str:
        return f"{self._style.value}{name}"

    def translate(self, filt: Any, params: dict[str, Any]) -> str:
        """Translate ``filt`` into a WHERE fragment, filling ``params``.

        Returns an empty string when ``filt`` is None. The translator does not
        reset ``self._param_index`` per call — the enclosing builder resets it
        to keep parameter names aligned with the .NET reference (``p0``, ``p1``,
        ...).
        """
        if filt is None:
            return ""
        return self._build_object(filt, params)

    # ── Private ────────────────────────────────────────────────────────────────

    def _build_object(self, obj: Any, p: dict[str, Any]) -> str:
        if not isinstance(obj, dict):
            raise NwpFilterError("Filter node must be an object.")
        clauses: list[str] = []
        for name, value in obj.items():
            if name.startswith("$"):
                clauses.append(self._build_logical(name, value, p))
            else:
                field = self._validate_field(name)
                clauses.append(self._build_field_condition(field, value, p))

        clauses = [c for c in clauses if c]
        if not clauses:
            return ""
        if len(clauses) == 1:
            return clauses[0]
        return "(" + " AND ".join(clauses) + ")"

    def _build_logical(self, op: str, value: Any, p: dict[str, Any]) -> str:
        if op == "$not":
            inner = self._build_object(value, p)
            return "" if not inner else f"NOT {inner}"

        if op not in ("$and", "$or"):
            raise NwpFilterError(f"Unknown logical operator '{op}'.")
        if not isinstance(value, (list, tuple)):
            raise NwpFilterError(f"Logical operator '{op}' requires an array value.")

        separator = " AND " if op == "$and" else " OR "
        parts = [self._build_object(el, p) for el in value]
        parts = [s for s in parts if s]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return "(" + separator.join(parts) + ")"

    def _build_field_condition(self, field: MemoryNodeField, condition: Any,
                               p: dict[str, Any]) -> str:
        col = self._quote_column(field.resolved_column_name)

        # Bare-equality shorthand: {"field": value} → field = value.
        if not isinstance(condition, dict):
            name = self._next_param(p, condition)
            return f"{col} = {self._ph(name)}"

        parts: list[str] = []
        for op, operand in condition.items():
            if op == "$in":
                parts.append(self._build_in(col, operand, p, negate=False))
            elif op == "$nin":
                parts.append(self._build_in(col, operand, p, negate=True))
            elif op == "$between":
                parts.append(self._build_between(col, operand, p))
            else:
                parts.append(self._build_simple(col, op, field.name, operand, p))

        if len(parts) == 1:
            return parts[0]
        return "(" + " AND ".join(parts) + ")"

    def _build_simple(self, col: str, op: str, field_name: str, value: Any,
                      p: dict[str, Any]) -> str:
        if op == "$contains":
            name = self._next_param(p, f"%{value}%")
            return f"{col} LIKE {self._ph(name)}"
        sql_op = _SIMPLE_OPS.get(op)
        if sql_op is None:
            raise NwpFilterError(
                f"Unknown filter operator '{op}' on field '{field_name}'.")
        name = self._next_param(p, value)
        return f"{col} {sql_op} {self._ph(name)}"

    def _build_in(self, col: str, arr: Any, p: dict[str, Any], negate: bool) -> str:
        if not isinstance(arr, (list, tuple)):
            raise NwpFilterError("$in/$nin requires an array value.")
        values = list(arr)
        if not values:
            # empty IN → always false; empty NIN → always true (matches .NET).
            return "1=1" if negate else "1=0"

        placeholders = []
        for v in values:
            name = self._next_param(p, v)
            placeholders.append(self._ph(name))
        joined = ", ".join(placeholders)
        keyword = "NOT IN" if negate else "IN"
        return f"{col} {keyword} ({joined})"

    def _build_between(self, col: str, arr: Any, p: dict[str, Any]) -> str:
        if not isinstance(arr, (list, tuple)) or len(arr) != 2:
            raise NwpFilterError(
                "$between requires an array of exactly two values [low, high].")
        low = self._next_param(p, arr[0])
        high = self._next_param(p, arr[1])
        return f"{col} BETWEEN {self._ph(low)} AND {self._ph(high)}"

    def _validate_field(self, name: str) -> MemoryNodeField:
        field = self._schema.get_field(name)
        if field is None:
            raise NwpFilterError(f"Unknown field '{name}'.", QUERY_FIELD_UNKNOWN)
        return field

    def _next_param(self, p: dict[str, Any], value: Any) -> str:
        name = f"p{self._param_index}"
        self._param_index += 1
        p[name] = value
        return name

    def _quote_column(self, col: str) -> str:
        if self._dialect == DatabaseDialect.SQL_SERVER:
            return f"[{col}]"
        return f'"{col}"'


# ── Query builder ──────────────────────────────────────────────────────────────

class SqlQueryBuilder:
    """Builds a full parameterized SELECT from a :class:`QueryFrame`."""

    def __init__(self, schema: MemoryNodeSchema, dialect: DatabaseDialect,
                 param_style: ParamStyle = ParamStyle.AT_NAME) -> None:
        if schema.table_name is None:
            raise NwpFilterError("Schema.table_name is required for SQL queries.")
        if schema.primary_key is None:
            raise NwpFilterError("Schema.primary_key is required for SQL queries.")
        self._schema = schema
        self._dialect = dialect
        self._style = param_style

    def _ph(self, name: str) -> str:
        return f"{self._style.value}{name}"

    def build(self, frame: QueryFrame, options: MemoryNodeOptions) -> tuple[str, dict[str, Any]]:
        p: dict[str, Any] = {}
        translator = NwpFilterTranslator(self._schema, self._dialect, self._style)

        limit = min(
            options.default_limit if frame.limit == 0 else frame.limit,
            options.max_limit,
        )
        offset = decode_cursor(frame.cursor)

        sql = ["SELECT ", self._build_select_list(frame.fields)]
        sql += [" FROM ", self._quote_table(self._schema.table_name)]

        where = translator.translate(frame.filter, p)
        if where:
            sql += [" WHERE ", where]

        if frame.order:
            sql += [" ORDER BY ", self._build_order_by(frame.order)]
        else:
            sql += [" ORDER BY ", self._quote_column(self._schema.primary_key)]

        if self._dialect == DatabaseDialect.SQL_SERVER:
            sql += [f" OFFSET {self._ph('_offset')} ROWS FETCH NEXT {self._ph('_limit')} ROWS ONLY"]
        else:
            sql += [f" LIMIT {self._ph('_limit')} OFFSET {self._ph('_offset')}"]

        p["_limit"] = int(limit)
        p["_offset"] = int(offset)
        return "".join(sql), p

    def build_count(self, frame: QueryFrame) -> tuple[str, dict[str, Any]]:
        p: dict[str, Any] = {}
        translator = NwpFilterTranslator(self._schema, self._dialect, self._style)
        sql = ["SELECT COUNT(*) FROM ", self._quote_table(self._schema.table_name)]
        where = translator.translate(frame.filter, p)
        if where:
            sql += [" WHERE ", where]
        return "".join(sql), p

    # ── Private ────────────────────────────────────────────────────────────────

    def _build_select_list(self, fields: Any) -> str:
        if not fields:
            return ", ".join(
                self._quote_column(f.resolved_column_name) for f in self._schema.fields)

        for name in fields:
            if not self._schema.has_field(name):
                raise NwpFilterError(f"Unknown field '{name}'.", QUERY_FIELD_UNKNOWN)

        parts = []
        for name in fields:
            f = self._schema.get_field(name)
            col = self._quote_column(f.resolved_column_name)
            if f.column_name is not None:
                parts.append(f"{col} AS {self._quote_column(f.name)}")
            else:
                parts.append(col)
        return ", ".join(parts)

    def _build_order_by(self, order: Any) -> str:
        parts = []
        for clause in order:
            field = self._schema.get_field(clause.field)
            if field is None:
                raise NwpFilterError(
                    f"Unknown order field '{clause.field}'.", QUERY_FIELD_UNKNOWN)
            direction = "DESC" if clause.dir.upper() == "DESC" else "ASC"
            parts.append(f"{self._quote_column(field.resolved_column_name)} {direction}")
        return ", ".join(parts)

    def _quote_column(self, col: str) -> str:
        if self._dialect == DatabaseDialect.SQL_SERVER:
            return f"[{col}]"
        return f'"{col}"'

    def _quote_table(self, table: str) -> str:
        if self._dialect == DatabaseDialect.SQL_SERVER:
            return f"[{table}]"
        return f'"{table}"'


# ── Cursor (Base64-URL opaque offset; matches .NET SqlQueryBuilder) ────────────

def encode_cursor(next_offset: int) -> str | None:
    if next_offset <= 0:
        return None
    raw = json.dumps({"o": next_offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded)
        doc = json.loads(raw)
        return int(doc.get("o", 0))
    except (ValueError, binascii.Error, TypeError):
        return 0
