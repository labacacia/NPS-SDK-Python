# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Provider-neutral reference state machine for NWP stateful LLM contexts."""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import json
import re
import secrets
import threading
import uuid
from collections.abc import Callable

from nps_sdk.nwp import error_codes
from nps_sdk.nwp.llm import (
    LlmContextOperation,
    LlmContextReceiptDto,
    LlmContextState,
    LlmContextStatusDto,
    LlmMessageDto,
    LlmToolDefinitionDto,
)

_COMPLETE_ACTION = "llm.complete"
_RELEASE_ACTION = "llm.context.release"
_CONTEXT_ID = re.compile(r"^[A-Za-z0-9_-]{22,128}$")


@dataclasses.dataclass(frozen=True)
class LlmContextOwner:
    nid: str
    security_scope: str


@dataclasses.dataclass(frozen=True)
class LlmContextBinding:
    model: str
    system_messages: tuple[LlmMessageDto, ...]
    runtime_revision: str
    tools: tuple[LlmToolDefinitionDto, ...] | None = None

    def fingerprint(self) -> str:
        def wire(value):
            if dataclasses.is_dataclass(value):
                return {
                    field.name: wire(getattr(value, field.name))
                    for field in dataclasses.fields(value)
                    if getattr(value, field.name) is not None
                }
            if isinstance(value, tuple):
                return [wire(item) for item in value]
            return value

        payload = json.dumps(
            wire(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclasses.dataclass(frozen=True)
class LlmContextMutationRequest:
    operation: LlmContextOperation
    owner: LlmContextOwner
    binding: LlmContextBinding
    messages: tuple[LlmMessageDto, ...]
    idempotency_key: str
    request_id: str
    context_id: str | None = None
    base_version: int | None = None
    ttl_seconds: int | None = None


@dataclasses.dataclass(frozen=True)
class LlmContextMutationReservation:
    _reservation_id: str
    _request: LlmContextMutationRequest
    _binding_fingerprint: str
    _base_transcript: tuple[LlmMessageDto, ...]
    _effective_ttl_seconds: int | None
    _parent_context_id: str | None = None
    _parent_version: int | None = None

    @property
    def operation(self) -> LlmContextOperation:
        return self._request.operation

    @property
    def request_id(self) -> str:
        return self._request.request_id


@dataclasses.dataclass(frozen=True)
class LlmContextSnapshot:
    context_id: str
    version: int
    state: LlmContextState
    transcript: tuple[LlmMessageDto, ...]
    binding: LlmContextBinding
    expires_at: dt.datetime | None


class LlmContextStoreError(Exception):
    def __init__(
        self, error_code: str, message: str, current_version: int | None = None
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.current_version = current_version


def _new_context_id() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")


@dataclasses.dataclass(frozen=True)
class LlmContextStoreOptions:
    max_contexts_per_principal: int = 32
    default_ttl_seconds: int = 3600
    max_ttl_seconds: int = 3600
    tombstone_seconds: int = 86400
    idempotency_ttl: dt.timedelta = dt.timedelta(hours=24)
    supported_operations: frozenset[LlmContextOperation] = dataclasses.field(
        default_factory=lambda: frozenset(LlmContextOperation)
    )
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    context_id_factory: Callable[[], str] = _new_context_id


@dataclasses.dataclass(frozen=True)
class LlmContextStoreDescriptor:
    operations: tuple[LlmContextOperation, ...]
    persistence: str
    max_contexts_per_principal: int
    max_ttl_seconds: int
    tombstone_seconds: int


@dataclasses.dataclass
class _Entry:
    context_id: str
    owner: LlmContextOwner
    version: int
    state: LlmContextState
    binding: LlmContextBinding
    binding_fingerprint: str
    transcript: list[LlmMessageDto]
    ttl_seconds: int
    expires_at: dt.datetime | None
    tombstone_until: dt.datetime | None = None
    reservation_id: str | None = None


@dataclasses.dataclass
class _IdempotencyEntry:
    state: str
    retain_until: dt.datetime
    request_id: str | None = None
    reservation_id: str | None = None
    error_code: str | None = None
    receipt: LlmContextReceiptDto | None = None
    context_id: str | None = None
    base_version: int | None = None


class InMemoryLlmContextStore:
    """Thread-safe process-local implementation of NWP 0.21 context semantics."""

    def __init__(self, options: LlmContextStoreOptions | None = None) -> None:
        self._options = options or LlmContextStoreOptions()
        self._lock = threading.RLock()
        self._contexts: dict[str, _Entry] = {}
        self._idempotency: dict[tuple[str, str, str, str], _IdempotencyEntry] = {}
        self._reservations: dict[str, LlmContextMutationReservation] = {}

    @property
    def descriptor(self) -> LlmContextStoreDescriptor:
        return LlmContextStoreDescriptor(
            operations=tuple(
                operation
                for operation in LlmContextOperation
                if operation in self._options.supported_operations
            ),
            persistence="process",
            max_contexts_per_principal=self._options.max_contexts_per_principal,
            max_ttl_seconds=self._options.max_ttl_seconds,
            tombstone_seconds=self._options.tombstone_seconds,
        )

    def reserve(
        self, request: LlmContextMutationRequest
    ) -> LlmContextMutationReservation:
        with self._lock:
            self._sweep(self._now())
            self._validate_request(request)
            self._ensure_supported(request.operation)
            idem_key = self._owner_key(
                request.owner, _COMPLETE_ACTION, request.idempotency_key
            )
            if idem_key in self._idempotency:
                raise LlmContextStoreError(
                    error_codes.ACTION_IDEMPOTENCY_CONFLICT,
                    "An outcome already exists for this idempotency key.",
                )

            if request.operation == LlmContextOperation.CREATE:
                self._ensure_allocation_available(request.owner)
                reservation = self._new_reservation(
                    request,
                    (),
                    self._clamp_ttl(
                        request.ttl_seconds or self._options.default_ttl_seconds
                    ),
                )
            else:
                entry = self._require_mutable(request.owner, request.context_id or "")
                if (
                    entry.reservation_id is not None
                    or entry.version != request.base_version
                ):
                    raise LlmContextStoreError(
                        error_codes.LLM_CONTEXT_VERSION_CONFLICT,
                        "The context version is stale or a mutation is running.",
                        entry.version,
                    )
                binding = request.binding.fingerprint()
                if (
                    request.operation
                    in (LlmContextOperation.APPEND, LlmContextOperation.FORK)
                    and entry.binding_fingerprint != binding
                ):
                    raise LlmContextStoreError(
                        error_codes.LLM_CONTEXT_BINDING_MISMATCH,
                        "The request binding differs from the retained binding.",
                    )
                if request.operation == LlmContextOperation.FORK:
                    self._ensure_allocation_available(request.owner)
                ttl = self._effective_ttl(request, entry)
                reservation = self._new_reservation(
                    request,
                    tuple(entry.transcript),
                    ttl,
                    entry.context_id if request.operation == LlmContextOperation.FORK else None,
                    entry.version if request.operation == LlmContextOperation.FORK else None,
                )
                if request.operation != LlmContextOperation.FORK:
                    entry.reservation_id = reservation._reservation_id

            self._reservations[reservation._reservation_id] = reservation
            self._idempotency[idem_key] = _IdempotencyEntry(
                state="busy",
                request_id=request.request_id,
                reservation_id=reservation._reservation_id,
                retain_until=self._now() + self._options.idempotency_ttl,
            )
            return reservation

    def commit(
        self,
        reservation: LlmContextMutationReservation,
        assistant_result: LlmMessageDto,
    ) -> LlmContextReceiptDto:
        with self._lock:
            current = self._require_reservation(reservation)
            request = current._request
            now = self._now()
            expiry = (
                now + dt.timedelta(seconds=current._effective_ttl_seconds)
                if current._effective_ttl_seconds is not None
                else None
            )
            if request.operation in (
                LlmContextOperation.CREATE,
                LlmContextOperation.FORK,
            ):
                context_id = self._next_context_id()
                transcript = (
                    list(current._base_transcript) if request.operation == LlmContextOperation.FORK else []
                )
                transcript.extend(request.messages)
                transcript.append(assistant_result)
                entry = _Entry(
                    context_id=context_id,
                    owner=request.owner,
                    version=1,
                    state=LlmContextState.ACTIVE,
                    binding=request.binding,
                    binding_fingerprint=current._binding_fingerprint,
                    transcript=transcript,
                    ttl_seconds=current._effective_ttl_seconds or 0,
                    expires_at=expiry,
                )
                self._contexts[context_id] = entry
                version = 1
            else:
                entry = self._require_entry(request.context_id or "")
                context_id = entry.context_id
                version = entry.version + 1
                entry.version = version
                entry.state = LlmContextState.ACTIVE
                entry.reservation_id = None
                entry.expires_at = expiry
                entry.ttl_seconds = current._effective_ttl_seconds or 0
                if request.operation == LlmContextOperation.RESET:
                    entry.binding = request.binding
                    entry.binding_fingerprint = current._binding_fingerprint
                    entry.transcript = [*request.messages, assistant_result]
                else:
                    entry.transcript.extend((*request.messages, assistant_result))

            receipt = LlmContextReceiptDto(
                context_id=context_id,
                version=version,
                operation=request.operation,
                state=LlmContextState.ACTIVE,
                expires_at=expiry.isoformat() if expiry else None,
                parent_context_id=current._parent_context_id,
                parent_version=current._parent_version,
            )
            self._complete_idempotency(current, receipt)
            del self._reservations[current._reservation_id]
            return receipt

    def abort(
        self,
        reservation: LlmContextMutationReservation,
        error_code: str | None = None,
    ) -> None:
        with self._lock:
            current = self._require_reservation(reservation)
            self._clear_reservation(current)
            del self._reservations[current._reservation_id]
            request = current._request
            self._idempotency[
                self._owner_key(request.owner, _COMPLETE_ACTION, request.idempotency_key)
            ] = _IdempotencyEntry(
                state="failed",
                request_id=request.request_id,
                error_code=error_code,
                retain_until=self._now() + self._options.idempotency_ttl,
            )
            self._sweep(self._now())

    def release(
        self,
        owner: LlmContextOwner,
        context_id: str,
        base_version: int,
        idempotency_key: str,
    ) -> LlmContextReceiptDto:
        with self._lock:
            self._sweep(self._now())
            self._ensure_supported(LlmContextOperation.RELEASE)
            self._validate_context_id(context_id)
            if not idempotency_key.strip():
                self._params_invalid("release requires idempotency_key.")
            key = self._owner_key(owner, _RELEASE_ACTION, idempotency_key)
            prior = self._idempotency.get(key)
            if prior is not None:
                if (
                    prior.state == "completed"
                    and prior.receipt is not None
                    and prior.context_id == context_id
                    and prior.base_version == base_version
                ):
                    return prior.receipt
                raise LlmContextStoreError(
                    error_codes.ACTION_IDEMPOTENCY_CONFLICT,
                    "A release with this idempotency key already exists.",
                )
            entry = self._require_mutable(owner, context_id)
            if entry.reservation_id is not None or entry.version != base_version:
                raise LlmContextStoreError(
                    error_codes.LLM_CONTEXT_VERSION_CONFLICT,
                    "The context version is stale or a mutation is running.",
                    entry.version,
                )
            entry.version += 1
            entry.state = LlmContextState.RELEASED
            entry.expires_at = None
            entry.tombstone_until = self._now() + dt.timedelta(
                seconds=self._options.tombstone_seconds
            )
            receipt = LlmContextReceiptDto(
                context_id=context_id,
                version=entry.version,
                operation=LlmContextOperation.RELEASE,
                state=LlmContextState.RELEASED,
            )
            self._idempotency[key] = _IdempotencyEntry(
                state="completed",
                receipt=receipt,
                context_id=context_id,
                base_version=base_version,
                retain_until=self._now() + self._options.idempotency_ttl,
            )
            return receipt

    def status(
        self,
        owner: LlmContextOwner,
        *,
        context_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> LlmContextStatusDto:
        with self._lock:
            self._sweep(self._now())
            if (context_id is None) == (idempotency_key is None):
                self._params_invalid(
                    "status requires exactly one of context_id or idempotency_key."
                )
            if idempotency_key is not None:
                outcome = self._idempotency.get(
                    self._owner_key(owner, _COMPLETE_ACTION, idempotency_key)
                )
                if outcome is None:
                    self._not_found()
                if outcome.state == "busy":
                    return LlmContextStatusDto(
                        state=LlmContextState.BUSY, request_id=outcome.request_id
                    )
                if outcome.state == "failed":
                    return LlmContextStatusDto(
                        state=LlmContextState.FAILED,
                        request_id=outcome.request_id,
                        error_code=outcome.error_code,
                    )
                return self._status_from_receipt(owner, outcome.receipt)

            self._validate_context_id(context_id or "")
            entry = self._contexts.get(context_id or "")
            if entry is None:
                self._not_found()
            self._ensure_owner(entry, owner)
            request_id = None
            if entry.reservation_id:
                active = self._reservations.get(entry.reservation_id)
                request_id = active.request_id if active else None
            return LlmContextStatusDto(
                state=LlmContextState.BUSY if entry.reservation_id else entry.state,
                context_id=entry.context_id,
                version=entry.version,
                expires_at=entry.expires_at.isoformat() if entry.expires_at else None,
                request_id=request_id,
            )

    def snapshot(
        self, owner: LlmContextOwner, context_id: str
    ) -> LlmContextSnapshot:
        with self._lock:
            self._sweep(self._now())
            entry = self._require_mutable(owner, context_id)
            return LlmContextSnapshot(
                entry.context_id,
                entry.version,
                entry.state,
                tuple(entry.transcript),
                entry.binding,
                entry.expires_at,
            )

    def sweep_expired(self) -> int:
        with self._lock:
            return self._sweep(self._now())

    def _new_reservation(
        self,
        request: LlmContextMutationRequest,
        transcript: tuple[LlmMessageDto, ...],
        ttl: int | None,
        parent_id: str | None = None,
        parent_version: int | None = None,
    ) -> LlmContextMutationReservation:
        return LlmContextMutationReservation(
            uuid.uuid4().hex,
            request,
            request.binding.fingerprint(),
            transcript,
            ttl,
            parent_id,
            parent_version,
        )

    def _validate_request(self, request: LlmContextMutationRequest) -> None:
        if request.operation == LlmContextOperation.RELEASE:
            self._params_invalid("release uses the lifecycle action.")
        if not request.idempotency_key.strip():
            self._params_invalid("A stateful request requires idempotency_key.")
        if request.ttl_seconds is not None and request.ttl_seconds <= 0:
            self._params_invalid("ttl_seconds must be greater than zero.")
        if request.operation == LlmContextOperation.CREATE:
            if request.context_id is not None or request.base_version is not None:
                self._params_invalid("create forbids context_id and base_version.")
        else:
            if request.context_id is None or request.base_version is None:
                self._params_invalid("append/fork/reset require context_id and base_version.")
            self._validate_context_id(request.context_id)
        if request.operation != LlmContextOperation.FORK and not request.messages:
            self._params_invalid("Only fork may carry an empty message delta.")
        if request.operation in (
            LlmContextOperation.APPEND,
            LlmContextOperation.FORK,
        ) and any(message.role.lower() == "system" for message in request.messages):
            raise LlmContextStoreError(
                error_codes.LLM_CONTEXT_BINDING_MISMATCH,
                "append/fork deltas must not contain system messages.",
            )

    def _effective_ttl(
        self, request: LlmContextMutationRequest, entry: _Entry
    ) -> int | None:
        if request.ttl_seconds is not None:
            return self._clamp_ttl(request.ttl_seconds)
        if request.operation == LlmContextOperation.FORK:
            if entry.expires_at is None:
                return None
            return max(1, int((entry.expires_at - self._now()).total_seconds() + 0.999999))
        return entry.ttl_seconds or None

    def _ensure_allocation_available(self, owner: LlmContextOwner) -> None:
        live = sum(
            entry.owner == owner and entry.state == LlmContextState.ACTIVE
            for entry in self._contexts.values()
        )
        pending = sum(
            item._request.owner == owner
            and item.operation in (LlmContextOperation.CREATE, LlmContextOperation.FORK)
            for item in self._reservations.values()
        )
        if live + pending >= self._options.max_contexts_per_principal:
            raise LlmContextStoreError(
                error_codes.LLM_CONTEXT_LIMIT_EXCEEDED,
                "The principal's live context limit has been reached.",
            )

    def _ensure_supported(self, operation: LlmContextOperation) -> None:
        if operation not in self._options.supported_operations:
            raise LlmContextStoreError(
                error_codes.LLM_CONTEXT_OPERATION_UNSUPPORTED,
                f"Context operation '{operation.value}' is not advertised.",
            )

    def _require_mutable(self, owner: LlmContextOwner, context_id: str) -> _Entry:
        entry = self._require_entry(context_id)
        self._ensure_owner(entry, owner)
        if entry.state == LlmContextState.EXPIRED:
            raise LlmContextStoreError(
                error_codes.LLM_CONTEXT_EXPIRED,
                "The context expired.",
                entry.version,
            )
        if entry.state == LlmContextState.RELEASED:
            self._not_found()
        return entry

    def _require_entry(self, context_id: str) -> _Entry:
        entry = self._contexts.get(context_id)
        if entry is None:
            self._not_found()
        return entry

    def _require_reservation(
        self, reservation: LlmContextMutationReservation
    ) -> LlmContextMutationReservation:
        current = self._reservations.get(reservation._reservation_id)
        if current is not reservation:
            raise RuntimeError("The context reservation is not active.")
        return current

    def _clear_reservation(self, reservation: LlmContextMutationReservation) -> None:
        context_id = reservation._request.context_id
        entry = self._contexts.get(context_id or "")
        if entry is not None and entry.reservation_id == reservation._reservation_id:
            entry.reservation_id = None

    def _complete_idempotency(
        self,
        reservation: LlmContextMutationReservation,
        receipt: LlmContextReceiptDto,
    ) -> None:
        request = reservation._request
        self._idempotency[
            self._owner_key(request.owner, _COMPLETE_ACTION, request.idempotency_key)
        ] = _IdempotencyEntry(
            state="completed",
            request_id=request.request_id,
            receipt=receipt,
            retain_until=self._now() + self._options.idempotency_ttl,
        )

    def _status_from_receipt(
        self, owner: LlmContextOwner, receipt: LlmContextReceiptDto | None
    ) -> LlmContextStatusDto:
        if receipt is None:
            self._not_found()
        if receipt.context_id in self._contexts:
            return self.status(owner, context_id=receipt.context_id)
        return LlmContextStatusDto(
            state=receipt.state,
            context_id=receipt.context_id,
            version=receipt.version,
            expires_at=receipt.expires_at,
        )

    def _next_context_id(self) -> str:
        for _ in range(8):
            context_id = self._options.context_id_factory()
            self._validate_context_id(context_id)
            if context_id not in self._contexts:
                return context_id
        raise RuntimeError("Context ID factory repeatedly produced collisions.")

    def _sweep(self, now: dt.datetime) -> int:
        changed = 0
        for entry in self._contexts.values():
            if (
                entry.state == LlmContextState.ACTIVE
                and entry.reservation_id is None
                and entry.expires_at is not None
                and entry.expires_at <= now
            ):
                entry.state = LlmContextState.EXPIRED
                entry.expires_at = None
                entry.tombstone_until = now + dt.timedelta(
                    seconds=self._options.tombstone_seconds
                )
                changed += 1
        expired = [
            key
            for key, entry in self._contexts.items()
            if entry.state in (LlmContextState.EXPIRED, LlmContextState.RELEASED)
            and entry.tombstone_until is not None
            and entry.tombstone_until <= now
        ]
        for key in expired:
            del self._contexts[key]
            changed += 1
        old_outcomes = [
            key
            for key, item in self._idempotency.items()
            if item.state != "busy" and item.retain_until <= now
        ]
        for key in old_outcomes:
            del self._idempotency[key]
            changed += 1
        return changed

    def _clamp_ttl(self, seconds: int) -> int:
        return min(seconds, self._options.max_ttl_seconds)

    def _now(self) -> dt.datetime:
        value = self._options.clock()
        if value.tzinfo is None:
            raise ValueError("LlmContextStore clock must return a timezone-aware datetime.")
        return value

    @staticmethod
    def _owner_key(
        owner: LlmContextOwner, action: str, key: str
    ) -> tuple[str, str, str, str]:
        return owner.nid, owner.security_scope, action, key

    @staticmethod
    def _validate_context_id(value: str) -> None:
        if _CONTEXT_ID.fullmatch(value) is None:
            InMemoryLlmContextStore._params_invalid(
                "context_id must be a 22-128 character unpadded base64url locator."
            )

    @staticmethod
    def _ensure_owner(entry: _Entry, owner: LlmContextOwner) -> None:
        if entry.owner != owner:
            raise LlmContextStoreError(
                error_codes.LLM_CONTEXT_FORBIDDEN,
                "The caller does not own this context.",
            )

    @staticmethod
    def _params_invalid(message: str):
        raise LlmContextStoreError(error_codes.ACTION_PARAMS_INVALID, message)

    @staticmethod
    def _not_found():
        raise LlmContextStoreError(
            error_codes.LLM_CONTEXT_NOT_FOUND,
            "The context or retained outcome was not found.",
        )
