# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""SQLite persistence layer for NIP CA Server."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = Path(__file__).parent / "db" / "001_init.sql"


@dataclass
class CertRecord:
    id: int
    nid: str
    entity_type: str
    serial: str
    pub_key: str
    capabilities: list
    scope: dict
    issued_by: str
    issued_at: str
    expires_at: str
    revoked_at: str | None
    revoke_reason: str | None
    metadata: dict | None


class CaDb:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self._tx() as conn:
            conn.executescript(SCHEMA.read_text())

    def next_serial(self) -> str:
        with self._tx() as conn:
            cur = conn.execute(
                "SELECT COALESCE(MAX(CAST(REPLACE(serial,'0x','') AS INTEGER)),0)+1 FROM nip_certificates"
            )
            n = cur.fetchone()[0]
            return f"0x{n:06X}"

    def insert(self, rec: dict) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                """INSERT INTO nip_certificates
                   (nid, entity_type, serial, pub_key, capabilities, scope_json,
                    issued_by, issued_at, expires_at, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec["nid"], rec["entity_type"], rec["serial"], rec["pub_key"],
                    json.dumps(rec["capabilities"]), json.dumps(rec["scope"]),
                    rec["issued_by"], rec["issued_at"], rec["expires_at"],
                    json.dumps(rec.get("metadata")) if rec.get("metadata") else None,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_active(self, nid: str) -> CertRecord | None:
        conn = self._connect()
        try:
            cur = conn.execute(
                """SELECT * FROM nip_certificates
                   WHERE nid=? AND revoked_at IS NULL
                   ORDER BY issued_at DESC LIMIT 1""",
                (nid,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return _row_to_record(row) if row else None

    def get_by_serial(self, serial: str) -> CertRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM nip_certificates WHERE serial=?", (serial,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_record(row) if row else None

    def revoke(self, nid: str, reason: str) -> bool:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._tx() as conn:
            cur = conn.execute(
                """UPDATE nip_certificates
                   SET revoked_at=?, revoke_reason=?
                   WHERE nid=? AND revoked_at IS NULL""",
                (now, reason, nid),
            )
            return cur.rowcount > 0

    def crl(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT serial, nid, revoked_at, revoke_reason
                   FROM nip_certificates WHERE revoked_at IS NOT NULL
                   ORDER BY revoked_at DESC"""
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]


def _row_to_record(row: sqlite3.Row) -> CertRecord:
    return CertRecord(
        id=row["id"],
        nid=row["nid"],
        entity_type=row["entity_type"],
        serial=row["serial"],
        pub_key=row["pub_key"],
        capabilities=json.loads(row["capabilities"]),
        scope=json.loads(row["scope_json"]),
        issued_by=row["issued_by"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        revoke_reason=row["revoke_reason"],
        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
    )
