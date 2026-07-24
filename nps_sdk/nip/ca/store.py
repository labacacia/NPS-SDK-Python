# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
CA certificate persistence (NPS-3 §8).

``NipCaCertRecord`` is the *server-side* persisted record — a superset of the
client-side revocation subset in :mod:`nps_sdk.nip.verifier` (``NipCertRecord``).
It is named distinctly to avoid a symbol clash with that client type.
"""

from __future__ import annotations

import dataclasses
import datetime
import threading
from typing import Protocol, runtime_checkable


@dataclasses.dataclass(frozen=True)
class NipCaCertRecord:
    """Persisted record of a NIP certificate (NPS-3 §5.1)."""

    nid: str
    entity_type: str                 # "agent" | "node" | "operator"
    serial: str
    pub_key: str
    capabilities: tuple[str, ...]
    scope_json: str                  # JSON blob
    issued_by: str
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    revoked_at: datetime.datetime | None = None
    revoke_reason: str | None = None
    metadata_json: str | None = None

    # NPS-CR-0003 §5.1.3 lineage columns.
    nid_role: str | None = None      # "group" | "session" | None
    parent_nid: str | None = None
    lineage_json: str | None = None

    def with_revocation(
        self, revoked_at: datetime.datetime, reason: str
    ) -> "NipCaCertRecord":
        return dataclasses.replace(self, revoked_at=revoked_at, revoke_reason=reason)


@runtime_checkable
class INipCaStore(Protocol):
    """Persistence abstraction for NIP CA certificate storage (NPS-3 §8)."""

    async def save(self, record: NipCaCertRecord) -> None: ...

    async def get_by_nid(self, nid: str) -> NipCaCertRecord | None: ...

    async def get_by_serial(self, serial: str) -> NipCaCertRecord | None: ...

    async def revoke(
        self, nid: str, reason: str, revoked_at: datetime.datetime
    ) -> bool: ...

    async def next_serial(self) -> str: ...

    async def list(self) -> list[NipCaCertRecord]: ...

    async def get_revoked(self) -> list[NipCaCertRecord]: ...

    async def get_by_parent_nid(self, parent_nid: str) -> list[NipCaCertRecord]: ...


class InMemoryNipCaStore:
    """In-memory :class:`INipCaStore` for tests, demos, and single-process
    development stacks. Not durable — use a real backend for production.
    """

    def __init__(self) -> None:
        self._gate = threading.Lock()
        self._records: list[NipCaCertRecord] = []
        self._serial = 0

    async def save(self, record: NipCaCertRecord) -> None:
        with self._gate:
            # Serial is globally unique. NID is NOT: renewal appends a new
            # record for the same NID (higher serial), and get_by_nid returns
            # the latest — mirroring the .NET store's LastOrDefault semantics.
            # Duplicate-NID rejection at *registration* time is enforced by
            # NipCaService (which calls get_by_nid first), matching .NET.
            if any(r.serial == record.serial for r in self._records):
                raise ValueError(f"Serial already exists: {record.serial}")
            self._records.append(record)

    async def get_by_nid(self, nid: str) -> NipCaCertRecord | None:
        with self._gate:
            # LastOrDefault — a renewed record supersedes an earlier one.
            for r in reversed(self._records):
                if r.nid == nid:
                    return r
            return None

    async def get_by_serial(self, serial: str) -> NipCaCertRecord | None:
        with self._gate:
            for r in self._records:
                if r.serial == serial:
                    return r
            return None

    async def revoke(
        self, nid: str, reason: str, revoked_at: datetime.datetime
    ) -> bool:
        with self._gate:
            # FindLastIndex of a not-yet-revoked record with this NID.
            for i in range(len(self._records) - 1, -1, -1):
                r = self._records[i]
                if r.nid == nid and r.revoked_at is None:
                    self._records[i] = r.with_revocation(revoked_at, reason)
                    return True
            return False

    async def next_serial(self) -> str:
        with self._gate:
            self._serial += 1
            # Match .NET: 0x{n:X} — uppercase hex, no zero padding.
            return f"0x{self._serial:X}"

    async def list(self) -> list[NipCaCertRecord]:
        with self._gate:
            return list(self._records)

    async def get_revoked(self) -> list[NipCaCertRecord]:
        with self._gate:
            return [r for r in self._records if r.revoked_at is not None]

    async def get_by_parent_nid(self, parent_nid: str) -> list[NipCaCertRecord]:
        with self._gate:
            return [r for r in self._records if r.parent_nid == parent_nid]
