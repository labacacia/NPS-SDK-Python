# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Registration-Authority enrollment policies + stores (NPS-CR-0005 §3).

Three tiers, each an :class:`IEnrollmentPolicy`:

* Tier 1 :class:`AllowlistPolicy` — glob allowlist match.
* Tier 2 :class:`BootstrapTokenPolicy` — single-use token (+ :class:`IBootstrapTokenStore`).
* Tier 3 :class:`PendingQueuePolicy` — operator approval queue (+ :class:`IPendingStore`).

:func:`create_enrollment_policy` is the factory keyed on
:class:`~nps_sdk.nip.ca.options.EnrollmentTier`.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import hashlib
import hmac
import re
import secrets
import threading
import uuid
from typing import Protocol, Sequence, runtime_checkable

from nps_sdk.nip import error_codes
from nps_sdk.nip.ca.errors import NipCaException
from nps_sdk.nip.ca.options import EnrollmentTier, NipCaOptions


# ── Pending exception ──────────────────────────────────────────────────────────

class NipRaPendingException(Exception):
    """Raised by a Tier-3 policy when a registration is queued.

    The router translates this into a ``202 Accepted`` response carrying the
    ``pending_id``.
    """

    def __init__(self, pending_id: str) -> None:
        super().__init__(f"Registration queued with pending id: {pending_id}")
        self.pending_id = pending_id


# ── Policy protocol ─────────────────────────────────────────────────────────────

@runtime_checkable
class IEnrollmentPolicy(Protocol):
    """Gate that must pass before the CA issues an IdentFrame (NPS-CR-0005 §3)."""

    async def check(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        metadata_json: str | None,
        enrollment_token: str | None,
    ) -> None: ...


# ── Tier 1: Allowlist ───────────────────────────────────────────────────────────

class AllowlistPolicy:
    """Admits registrations whose ``identifier`` matches at least one glob
    pattern (NPS-CR-0005 §3.2). Pattern ``*`` matches anything (open CA).
    """

    def __init__(self, patterns: Sequence[str]) -> None:
        self._compiled = [self._glob_to_regex(p) for p in patterns]

    async def check(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        metadata_json: str | None,
        enrollment_token: str | None,
    ) -> None:
        for rx in self._compiled:
            if rx.match(identifier):
                return
        raise NipCaException(
            f"Identifier '{identifier}' does not match any enrollment allowlist pattern.",
            error_codes.RA_NID_NOT_ALLOWED,
        )

    @staticmethod
    def _glob_to_regex(pattern: str) -> re.Pattern[str]:
        if pattern == "*":
            return re.compile(".*")
        escaped = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        return re.compile(f"^{escaped}$")


# ── Tier 2: Bootstrap token ─────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class BootstrapTokenInfo:
    """Public metadata for a bootstrap token (token value excluded)."""

    id: str
    label: str | None
    created_at: datetime.datetime
    expires_at: datetime.datetime
    consumed: bool
    revoked: bool


@runtime_checkable
class IBootstrapTokenStore(Protocol):
    """Store for single-use enrollment bootstrap tokens (NPS-CR-0005 §3.3)."""

    async def create(
        self, label: str | None, expires_at: datetime.datetime
    ) -> str: ...

    async def validate_and_consume(self, token: str) -> bool: ...

    async def list(self) -> list[BootstrapTokenInfo]: ...

    async def revoke(self, token_id: str) -> bool: ...


class InMemoryBootstrapTokenStore:
    """In-memory :class:`IBootstrapTokenStore`. Tokens are stored as SHA-256
    hashes; the raw value is returned only once at creation.
    """

    @dataclasses.dataclass
    class _Entry:
        id: str
        hash: bytes
        label: str | None
        created_at: datetime.datetime
        expires_at: datetime.datetime
        consumed: bool
        revoked: bool

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: list[InMemoryBootstrapTokenStore._Entry] = []

    async def create(
        self, label: str | None, expires_at: datetime.datetime
    ) -> str:
        raw = "nps-bootstrap-" + secrets.token_bytes(16).hex()
        h = hashlib.sha256(raw.encode("utf-8")).digest()
        token_id = uuid.uuid4().hex
        with self._lock:
            self._tokens.append(
                self._Entry(token_id, h, label, _utcnow(), expires_at, False, False)
            )
        return raw

    async def validate_and_consume(self, token: str) -> bool:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        with self._lock:
            for e in self._tokens:
                if e.consumed or e.revoked:
                    continue
                if _utcnow() > e.expires_at:
                    continue
                if not hmac.compare_digest(h, e.hash):
                    continue
                e.consumed = True
                return True
            return False

    async def list(self) -> list[BootstrapTokenInfo]:
        with self._lock:
            return [
                BootstrapTokenInfo(
                    e.id, e.label, e.created_at, e.expires_at, e.consumed, e.revoked
                )
                for e in self._tokens
            ]

    async def revoke(self, token_id: str) -> bool:
        with self._lock:
            for e in self._tokens:
                if e.id != token_id:
                    continue
                if e.consumed or e.revoked:
                    return False
                e.revoked = True
                return True
            return False


class BootstrapTokenPolicy:
    """Tier 2: caller must present a valid single-use bootstrap token
    (NPS-CR-0005 §3.3). The token is consumed atomically on success.
    """

    def __init__(self, store: IBootstrapTokenStore) -> None:
        self._store = store

    async def check(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        metadata_json: str | None,
        enrollment_token: str | None,
    ) -> None:
        if not enrollment_token or not enrollment_token.startswith("nps-bootstrap-"):
            raise NipCaException(
                "A bootstrap token (prefix 'nps-bootstrap-') is required for enrollment.",
                error_codes.RA_TOKEN_INVALID,
            )
        valid = await self._store.validate_and_consume(enrollment_token)
        if not valid:
            raise NipCaException(
                "Bootstrap token is invalid, expired, or already consumed.",
                error_codes.RA_TOKEN_EXPIRED,
            )


# ── Tier 3: Pending queue ───────────────────────────────────────────────────────

class PendingStatus(enum.IntEnum):
    PENDING = 0
    APPROVED = 1
    REJECTED = 2


@dataclasses.dataclass(frozen=True)
class PendingRegistration:
    """A registration request waiting for operator approval (NPS-CR-0005 §3.4)."""

    id: str
    entity_type: str
    identifier: str
    pub_key: str
    capabilities: tuple[str, ...]
    scope_json: str
    metadata_json: str | None
    requested_at: datetime.datetime
    status: PendingStatus
    reject_reason: str | None


@runtime_checkable
class IPendingStore(Protocol):
    """Store for pending registration requests (NPS-CR-0005 §3.4)."""

    async def enqueue(self, request: PendingRegistration) -> str: ...

    async def list(self) -> list[PendingRegistration]: ...

    async def get(self, id: str) -> PendingRegistration | None: ...

    async def approve(self, id: str) -> bool: ...

    async def reject(self, id: str, reason: str) -> bool: ...

    @property
    def pending_count(self) -> int: ...


class InMemoryPendingStore:
    """In-memory :class:`IPendingStore` with an on-access sweep of aged
    non-pending records to bound memory growth.
    """

    def __init__(self, max_age_seconds: int = 7 * 24 * 60 * 60) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, PendingRegistration] = {}
        self._max_age = datetime.timedelta(seconds=max_age_seconds)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for r in self._records.values() if r.status == PendingStatus.PENDING
            )

    async def enqueue(self, request: PendingRegistration) -> str:
        with self._lock:
            self._sweep_locked()
            self._records[request.id] = request
        return request.id

    async def list(self) -> list[PendingRegistration]:
        with self._lock:
            return list(self._records.values())

    async def get(self, id: str) -> PendingRegistration | None:
        with self._lock:
            return self._records.get(id)

    async def approve(self, id: str) -> bool:
        with self._lock:
            r = self._records.get(id)
            if r is None or r.status != PendingStatus.PENDING:
                return False
            self._records[id] = dataclasses.replace(r, status=PendingStatus.APPROVED)
            return True

    async def reject(self, id: str, reason: str) -> bool:
        with self._lock:
            r = self._records.get(id)
            if r is None or r.status != PendingStatus.PENDING:
                return False
            self._records[id] = dataclasses.replace(
                r, status=PendingStatus.REJECTED, reject_reason=reason
            )
            return True

    def _sweep_locked(self) -> None:
        cutoff = _utcnow() - self._max_age
        expired = [
            r.id
            for r in self._records.values()
            if r.status != PendingStatus.PENDING and r.requested_at < cutoff
        ]
        for rid in expired:
            del self._records[rid]


class PendingQueuePolicy:
    """Tier 3: every inbound registration is queued as a
    :class:`PendingRegistration`; the CA replies ``202`` with a ``pending_id``
    (NPS-CR-0005 §3.4).
    """

    def __init__(self, store: IPendingStore, max_size: int) -> None:
        self._store = store
        self._max_size = max_size

    async def check(
        self,
        entity_type: str,
        identifier: str,
        pub_key: str,
        capabilities: Sequence[str],
        scope_json: str,
        metadata_json: str | None,
        enrollment_token: str | None,
    ) -> None:
        if self._store.pending_count >= self._max_size:
            raise NipCaException(
                f"Pending enrollment queue is full (max {self._max_size}). Retry later.",
                error_codes.RA_TOKEN_INVALID,
            )
        pid = uuid.uuid4().hex
        req = PendingRegistration(
            id=pid,
            entity_type=entity_type,
            identifier=identifier,
            pub_key=pub_key,
            capabilities=tuple(capabilities),
            scope_json=scope_json,
            metadata_json=metadata_json,
            requested_at=_utcnow(),
            status=PendingStatus.PENDING,
            reject_reason=None,
        )
        await self._store.enqueue(req)
        raise NipRaPendingException(pid)


# ── Factory ─────────────────────────────────────────────────────────────────────

def create_enrollment_policy(
    opts: NipCaOptions,
    bootstrap_token_store: IBootstrapTokenStore | None = None,
    pending_store: IPendingStore | None = None,
) -> IEnrollmentPolicy:
    """Construct the :class:`IEnrollmentPolicy` selected by
    ``opts.enrollment_tier`` (mirror of .NET ``CreateEnrollmentPolicy``).
    """
    tier = opts.enrollment_tier
    if tier == EnrollmentTier.ALLOWLIST:
        return AllowlistPolicy(opts.enrollment_allowlist_patterns)
    if tier == EnrollmentTier.BOOTSTRAP_TOKEN:
        if bootstrap_token_store is None:
            raise ValueError(
                "EnrollmentTier.BOOTSTRAP_TOKEN requires an IBootstrapTokenStore."
            )
        return BootstrapTokenPolicy(bootstrap_token_store)
    if tier == EnrollmentTier.PENDING_QUEUE:
        if pending_store is None:
            raise ValueError(
                "EnrollmentTier.PENDING_QUEUE requires an IPendingStore."
            )
        return PendingQueuePolicy(pending_store, opts.pending_queue_max_size)
    raise ValueError(f"Unknown EnrollmentTier: {tier}")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)
