# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
SQL-backed NIP CA certificate store (port of ``SqliteNipCaStore`` /
``PostgreSqlNipCaStore``, NPS-3 §8).

:class:`SqlNipCaStore` implements the :class:`~nps_sdk.nip.ca.store.INipCaStore`
protocol over an injectable async DB executor
(:class:`INipCaDbExecutor`) so the SQL generation and record mapping are
testable without a live database.

A concrete :class:`SqliteNipCaStore` backed by the Python stdlib ``sqlite3``
module is provided (schema + migrations mirror the .NET ``SqliteNipCaStore``).
Concrete Postgres bindings (``asyncpg``) are **deferred** in this offline
environment — supply an :class:`INipCaDbExecutor` over your driver to run
:class:`SqlNipCaStore` against PostgreSQL.
"""

from __future__ import annotations

import abc
import asyncio
import datetime
import json
import sqlite3
from typing import Any

from nps_sdk.nip.ca.store import NipCaCertRecord


class INipCaDbExecutor(abc.ABC):
    """Async DB executor for the SQL CA store. Placeholders use ``:name`` style."""

    @abc.abstractmethod
    async def execute(self, sql: str, params: dict[str, Any]) -> int:
        """Run a non-query statement; return affected row count."""

    @abc.abstractmethod
    async def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a query; return a list of row dicts."""

    @abc.abstractmethod
    async def next_serial(self) -> str:
        """Atomically reserve and return the next serial (``0x{n:X}``)."""


# ── SQL fragments (column order matches .NET SqliteNipCaStore) ──────────────────

_INSERT_SQL = """
INSERT INTO nip_certs
    (nid, entity_type, serial, pub_key, capabilities_json, scope_json,
     issued_by, issued_at, expires_at, metadata_json,
     nid_role, parent_nid, lineage_json)
VALUES
    (:nid, :entity_type, :serial, :pub_key, :capabilities_json, :scope_json,
     :issued_by, :issued_at, :expires_at, :metadata_json,
     :nid_role, :parent_nid, :lineage_json)
""".strip()

_GET_BY_NID_SQL = (
    "SELECT * FROM nip_certs WHERE nid = :nid ORDER BY issued_at DESC LIMIT 1")
_GET_BY_SERIAL_SQL = "SELECT * FROM nip_certs WHERE serial = :serial LIMIT 1"
_REVOKE_SQL = (
    "UPDATE nip_certs SET revoked_at = :revoked_at, revoke_reason = :reason "
    "WHERE nid = :nid AND revoked_at IS NULL")
_LIST_SQL = "SELECT * FROM nip_certs ORDER BY issued_at DESC"
_GET_REVOKED_SQL = (
    "SELECT * FROM nip_certs WHERE revoked_at IS NOT NULL ORDER BY revoked_at DESC")
_GET_BY_PARENT_SQL = (
    "SELECT * FROM nip_certs WHERE parent_nid = :parent_nid ORDER BY issued_at DESC")


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def _parse_dt(raw: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(raw)


def _record_to_params(record: NipCaCertRecord) -> dict[str, Any]:
    return {
        "nid": record.nid,
        "entity_type": record.entity_type,
        "serial": record.serial,
        "pub_key": record.pub_key,
        "capabilities_json": json.dumps(list(record.capabilities), separators=(",", ":")),
        "scope_json": record.scope_json,
        "issued_by": record.issued_by,
        "issued_at": _iso(record.issued_at),
        "expires_at": _iso(record.expires_at),
        "metadata_json": record.metadata_json,
        "nid_role": record.nid_role,
        "parent_nid": record.parent_nid,
        "lineage_json": record.lineage_json,
    }


def _row_to_record(row: dict[str, Any]) -> NipCaCertRecord:
    caps_raw = row.get("capabilities_json") or "[]"
    revoked = row.get("revoked_at")
    return NipCaCertRecord(
        nid=row["nid"],
        entity_type=row["entity_type"],
        serial=row["serial"],
        pub_key=row["pub_key"],
        capabilities=tuple(json.loads(caps_raw)),
        scope_json=row["scope_json"],
        issued_by=row["issued_by"],
        issued_at=_parse_dt(row["issued_at"]),
        expires_at=_parse_dt(row["expires_at"]),
        revoked_at=_parse_dt(revoked) if revoked else None,
        revoke_reason=row.get("revoke_reason"),
        metadata_json=row.get("metadata_json"),
        nid_role=row.get("nid_role"),
        parent_nid=row.get("parent_nid"),
        lineage_json=row.get("lineage_json"),
    )


class SqlNipCaStore:
    """:class:`INipCaStore` over an injectable :class:`INipCaDbExecutor`."""

    def __init__(self, executor: INipCaDbExecutor) -> None:
        self._executor = executor

    async def save(self, record: NipCaCertRecord) -> None:
        await self._executor.execute(_INSERT_SQL, _record_to_params(record))

    async def get_by_nid(self, nid: str) -> NipCaCertRecord | None:
        rows = await self._executor.query(_GET_BY_NID_SQL, {"nid": nid})
        return _row_to_record(rows[0]) if rows else None

    async def get_by_serial(self, serial: str) -> NipCaCertRecord | None:
        rows = await self._executor.query(_GET_BY_SERIAL_SQL, {"serial": serial})
        return _row_to_record(rows[0]) if rows else None

    async def revoke(self, nid: str, reason: str,
                     revoked_at: datetime.datetime) -> bool:
        affected = await self._executor.execute(_REVOKE_SQL, {
            "nid": nid, "reason": reason, "revoked_at": _iso(revoked_at),
        })
        return affected > 0

    async def next_serial(self) -> str:
        return await self._executor.next_serial()

    async def list(self) -> list[NipCaCertRecord]:
        rows = await self._executor.query(_LIST_SQL, {})
        return [_row_to_record(r) for r in rows]

    async def get_revoked(self) -> list[NipCaCertRecord]:
        rows = await self._executor.query(_GET_REVOKED_SQL, {})
        return [_row_to_record(r) for r in rows]

    async def get_by_parent_nid(self, parent_nid: str) -> list[NipCaCertRecord]:
        rows = await self._executor.query(_GET_BY_PARENT_SQL, {"parent_nid": parent_nid})
        return [_row_to_record(r) for r in rows]


# ── DDL migrations (mirror .NET SqliteNipCaStore.MigrateAsync) ──────────────────

_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS nip_certs (
        nid               TEXT NOT NULL,
        entity_type       TEXT NOT NULL,
        serial            TEXT NOT NULL UNIQUE,
        pub_key           TEXT NOT NULL,
        capabilities_json TEXT NOT NULL DEFAULT '[]',
        scope_json        TEXT NOT NULL DEFAULT '{}',
        issued_by         TEXT NOT NULL,
        issued_at         TEXT NOT NULL,
        expires_at        TEXT NOT NULL,
        revoked_at        TEXT,
        revoke_reason     TEXT,
        metadata_json     TEXT,
        nid_role          TEXT,
        parent_nid        TEXT,
        lineage_json      TEXT
    )
    """.strip(),
    "CREATE INDEX IF NOT EXISTS idx_nip_certs_nid        ON nip_certs (nid)",
    "CREATE INDEX IF NOT EXISTS idx_nip_certs_serial     ON nip_certs (serial)",
    "CREATE INDEX IF NOT EXISTS idx_nip_certs_parent_nid ON nip_certs (parent_nid)",
    """
    CREATE TABLE IF NOT EXISTS nip_serial (
        id   INTEGER PRIMARY KEY,
        seq  INTEGER NOT NULL DEFAULT 0
    )
    """.strip(),
    "INSERT OR IGNORE INTO nip_serial (id, seq) VALUES (1, 0)",
)


class SqliteNipCaDbExecutor(INipCaDbExecutor):
    """:class:`INipCaDbExecutor` over the stdlib ``sqlite3`` module."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    def migrate_sync(self) -> None:
        for stmt in _MIGRATIONS:
            self._conn.execute(stmt)
        self._conn.commit()

    async def execute(self, sql: str, params: dict[str, Any]) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: dict[str, Any]) -> int:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        try:
            return cur.rowcount
        finally:
            cur.close()

    async def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._query_sync, sql, params)

    def _query_sync(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cur = self._conn.execute(sql, params)
        try:
            return [dict(r) for r in cur.fetchall()]
        finally:
            cur.close()

    async def next_serial(self) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._next_serial_sync)

    def _next_serial_sync(self) -> str:
        self._conn.execute("UPDATE nip_serial SET seq = seq + 1 WHERE id = 1")
        cur = self._conn.execute("SELECT seq FROM nip_serial WHERE id = 1")
        seq = cur.fetchone()[0]
        cur.close()
        self._conn.commit()
        return f"0x{seq:X}"


class SqliteNipCaStore(SqlNipCaStore):
    """SQLite-backed NIP CA store. Use :meth:`open` to create + migrate."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        executor = SqliteNipCaDbExecutor(connection)
        executor.migrate_sync()
        super().__init__(executor)
        self._connection = connection

    @classmethod
    async def open(cls, path: str = ":memory:") -> "SqliteNipCaStore":
        conn = sqlite3.connect(path, check_same_thread=False)
        return cls(conn)
