# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

import datetime as dt
import dataclasses
import json
from collections import deque
from pathlib import Path

import pytest

from nps_sdk.nwp import error_codes
from nps_sdk.nwp.context_store import (
    InMemoryLlmContextStore,
    LlmContextBinding,
    LlmContextMutationRequest,
    LlmContextOwner,
    LlmContextStoreError,
    LlmContextStoreOptions,
)
from nps_sdk.nwp.llm import (
    LlmContextOperation,
    LlmContextState,
    LlmMessageDto,
    LlmUsageDto,
)

ALICE = LlmContextOwner("urn:nps:agent:labacacia:alice", "workspace-a")
BOB = LlmContextOwner("urn:nps:agent:labacacia:bob", "workspace-a")
FIXTURE = (
    Path(__file__).parents[3]
    / "spec"
    / "conformance"
    / "nwp"
    / "llm_context_vectors.json"
)


def system(content: str) -> LlmMessageDto:
    return LlmMessageDto(role="system", content=content)


def user(content: str) -> LlmMessageDto:
    return LlmMessageDto(role="user", content=content)


def assistant(content: str) -> LlmMessageDto:
    return LlmMessageDto(role="assistant", content=content)


def binding(
    model: str = "willow-small", runtime: str = "runtime-1"
) -> LlmContextBinding:
    prompt = "Be concise." if model == "willow-small" else "Use JSON."
    return LlmContextBinding(model, (system(prompt),), runtime)


class Harness:
    def __init__(
        self,
        *,
        max_contexts: int = 32,
        default_ttl: int = 3600,
        tombstone: int = 86400,
        supported: frozenset[LlmContextOperation] | None = None,
    ) -> None:
        self.now = dt.datetime(2026, 8, 12, tzinfo=dt.timezone.utc)
        self.ids = deque(
            (
                "AQIDBAUGBwgJCgsMDQ4PEA",
                "ERITFBUWFxgZGhscHR4fIA",
                "ISIjJCUmJygpKissLS4vMA",
                "MTIzNDU2Nzg5Ojs8PT4_QA",
            )
        )
        self.store = InMemoryLlmContextStore(
            LlmContextStoreOptions(
                max_contexts_per_principal=max_contexts,
                default_ttl_seconds=default_ttl,
                max_ttl_seconds=3600,
                tombstone_seconds=tombstone,
                supported_operations=supported or frozenset(LlmContextOperation),
                clock=lambda: self.now,
                context_id_factory=self.ids.popleft,
            )
        )

    def request(
        self,
        operation: LlmContextOperation,
        key: str,
        context_id: str | None = None,
        base_version: int | None = None,
        *,
        selected_binding: LlmContextBinding | None = None,
        messages: tuple[LlmMessageDto, ...] | None = None,
        ttl: int | None = None,
    ) -> LlmContextMutationRequest:
        if messages is None:
            messages = (
                (system("Be concise."), user("One"))
                if operation == LlmContextOperation.CREATE
                else (user("Continue"),)
            )
        return LlmContextMutationRequest(
            operation=operation,
            owner=ALICE,
            context_id=context_id,
            base_version=base_version,
            binding=selected_binding or binding(),
            messages=messages,
            ttl_seconds=ttl,
            idempotency_key=key,
            request_id=f"req-{key}",
        )

    def create(
        self, key: str = "create-1", ttl: int | None = None
    ):
        reservation = self.store.reserve(
            self.request(LlmContextOperation.CREATE, key, ttl=ttl)
        )
        return self.store.commit(reservation, assistant("First"))

    def advance(self, seconds: int) -> None:
        self.now += dt.timedelta(seconds=seconds)


with FIXTURE.open(encoding="utf-8") as stream:
    VECTORS = json.load(stream)["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda item: item["id"])
def test_shared_context_vector(vector):
    cases = {
        "nwp.llm-context.001": _stateless_compatibility,
        "nwp.llm-context.002": _create_commits_v1,
        "nwp.llm-context.003": _append_commits_delta,
        "nwp.llm-context.004": _cas_conflicts,
        "nwp.llm-context.005": _fork_snapshots_parent,
        "nwp.llm-context.006": _reset_replaces_state,
        "nwp.llm-context.007": _binding_mismatch,
        "nwp.llm-context.008": _owner_boundary,
        "nwp.llm-context.009": _abort_preserves_state,
        "nwp.llm-context.010": _lost_create_recovery,
        "nwp.llm-context.011": _release_and_expiry,
        "nwp.llm-context.012": _usage_accounting,
        "nwp.llm-context.013": _advertised_operations,
        "nwp.llm-context.014": _process_restart,
        "nwp.llm-context.015": _completed_idempotency,
        "nwp.llm-context.016": _revocation_abort,
        "nwp.llm-context.017": _principal_limit,
        "nwp.llm-context.018": _unsupported_operation,
        "nwp.llm-context.019": _missing_idempotency,
    }
    assert vector["id"] in cases
    cases[vector["id"]]()


def _stateless_compatibility():
    from nps_sdk.nwp.llm import LlmCompleteActionRequest

    request = LlmCompleteActionRequest("willow-small", (user("Hello"),))
    assert request.context is None


def _create_commits_v1():
    h = Harness()
    reservation = h.store.reserve(h.request(LlmContextOperation.CREATE, "create-1"))
    busy = h.store.status(ALICE, idempotency_key="create-1")
    assert busy.state == LlmContextState.BUSY
    assert busy.context_id is None
    h.advance(5)
    receipt = h.store.commit(reservation, assistant("First"))
    assert receipt.version == 1
    assert dt.datetime.fromisoformat(receipt.expires_at) == h.now + dt.timedelta(hours=1)
    assert len(h.store.snapshot(ALICE, receipt.context_id).transcript) == 3


def _append_commits_delta():
    h = Harness()
    created = h.create()
    reservation = h.store.reserve(
        h.request(
            LlmContextOperation.APPEND,
            "append-1",
            created.context_id,
            1,
            messages=(user("Two"),),
        )
    )
    receipt = h.store.commit(reservation, assistant("Second"))
    snapshot = h.store.snapshot(ALICE, created.context_id)
    assert receipt.version == 2
    assert len(snapshot.transcript) == 5
    assert snapshot.transcript[-2].content == "Two"


def _cas_conflicts():
    h = Harness()
    created = h.create()
    winner = h.store.reserve(
        h.request(LlmContextOperation.APPEND, "winner", created.context_id, 1)
    )
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(
            h.request(LlmContextOperation.APPEND, "loser", created.context_id, 1)
        )
    assert caught.value.error_code == error_codes.LLM_CONTEXT_VERSION_CONFLICT
    assert caught.value.current_version == 1
    h.store.abort(winner)
    with pytest.raises(LlmContextStoreError) as stale:
        h.store.reserve(
            h.request(LlmContextOperation.APPEND, "stale", created.context_id, 0)
        )
    assert stale.value.error_code == error_codes.LLM_CONTEXT_VERSION_CONFLICT


def _fork_snapshots_parent():
    h = Harness()
    parent = h.create()
    fork = h.store.reserve(
        h.request(
            LlmContextOperation.FORK,
            "fork-1",
            parent.context_id,
            1,
            messages=(),
        )
    )
    append = h.store.reserve(
        h.request(LlmContextOperation.APPEND, "parent-append", parent.context_id, 1)
    )
    h.store.commit(append, assistant("Parent moved"))
    child = h.store.commit(fork, assistant("Branch"))
    assert child.parent_context_id == parent.context_id
    assert child.parent_version == 1
    assert h.store.snapshot(ALICE, parent.context_id).version == 2
    assert len(h.store.snapshot(ALICE, child.context_id).transcript) == 4


def _reset_replaces_state():
    h = Harness()
    created = h.create()
    reset = h.store.reserve(
        h.request(
            LlmContextOperation.RESET,
            "reset-1",
            created.context_id,
            1,
            selected_binding=binding("willow-medium", "runtime-2"),
            messages=(system("Use JSON."), user("Restart")),
        )
    )
    receipt = h.store.commit(reset, assistant("{}"))
    snapshot = h.store.snapshot(ALICE, created.context_id)
    assert receipt.version == 2
    assert snapshot.binding.model == "willow-medium"
    assert len(snapshot.transcript) == 3
    assert all(item.content != "One" for item in snapshot.transcript)


def _binding_mismatch():
    h = Harness()
    created = h.create()
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(
            h.request(
                LlmContextOperation.APPEND,
                "bad-binding",
                created.context_id,
                1,
                selected_binding=binding("willow-large"),
            )
        )
    assert caught.value.error_code == error_codes.LLM_CONTEXT_BINDING_MISMATCH


def _owner_boundary():
    h = Harness()
    created = h.create()
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.status(BOB, context_id=created.context_id)
    assert caught.value.error_code == error_codes.LLM_CONTEXT_FORBIDDEN


def _abort_preserves_state():
    h = Harness(default_ttl=10)
    created = h.create(ttl=10)
    mutation = h.store.reserve(
        h.request(LlmContextOperation.APPEND, "abort-1", created.context_id, 1)
    )
    h.advance(11)
    h.store.abort(mutation, "NPS-SERVER-TIMEOUT")
    assert h.store.status(ALICE, context_id=created.context_id).state == LlmContextState.EXPIRED
    failed = h.store.status(ALICE, idempotency_key="abort-1")
    assert failed.error_code == "NPS-SERVER-TIMEOUT"


def _lost_create_recovery():
    h = Harness(default_ttl=10, tombstone=5)
    reservation = h.store.reserve(
        h.request(LlmContextOperation.CREATE, "lost-create")
    )
    assert h.store.status(ALICE, idempotency_key="lost-create").context_id is None
    h.store.commit(reservation, assistant("First"))
    active = h.store.status(ALICE, idempotency_key="lost-create")
    h.advance(16)
    h.store.sweep_expired()
    retained = h.store.status(ALICE, idempotency_key="lost-create")
    assert retained.context_id == active.context_id
    assert retained.version == 1


def _release_and_expiry():
    h = Harness(default_ttl=10, tombstone=5)
    created = h.create(ttl=10)
    released = h.store.release(ALICE, created.context_id, 1, "create-1")
    assert released.version == 2
    assert h.store.release(ALICE, created.context_id, 1, "create-1") == released
    with pytest.raises(LlmContextStoreError) as collision:
        h.store.release(ALICE, "ERITFBUWFxgZGhscHR4fIA", 1, "create-1")
    assert collision.value.error_code == error_codes.ACTION_IDEMPOTENCY_CONFLICT
    assert h.store.status(ALICE, context_id=created.context_id).state == LlmContextState.RELEASED
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(
            h.request(LlmContextOperation.APPEND, "after-release", created.context_id, 2)
        )
    assert caught.value.error_code == error_codes.LLM_CONTEXT_NOT_FOUND

    expiring = h.create("create-expiring", ttl=10)
    h.advance(11)
    h.store.sweep_expired()
    assert h.store.status(ALICE, context_id=expiring.context_id).state == LlmContextState.EXPIRED
    with pytest.raises(LlmContextStoreError) as expired:
        h.store.snapshot(ALICE, expiring.context_id)
    assert expired.value.error_code == error_codes.LLM_CONTEXT_EXPIRED
    h.advance(6)
    h.store.sweep_expired()
    with pytest.raises(LlmContextStoreError) as gone:
        h.store.status(ALICE, context_id=expiring.context_id)
    assert gone.value.error_code == error_codes.LLM_CONTEXT_NOT_FOUND


def _usage_accounting():
    usage = LlmUsageDto(
        input_tokens=1200,
        reused_tokens=1000,
        evaluated_tokens=200,
        output_tokens=80,
        cache_hit=True,
        wire_input_bytes=384,
    )
    assert usage.input_tokens == usage.reused_tokens + usage.evaluated_tokens
    assert usage.cache_hit and usage.wire_input_bytes < 4096


def _advertised_operations():
    supported = frozenset(LlmContextOperation) - {LlmContextOperation.FORK}
    h = Harness(supported=supported)
    created = h.create()
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(
            h.request(
                LlmContextOperation.FORK,
                "fork-disabled",
                created.context_id,
                1,
                messages=(),
            )
        )
    assert caught.value.error_code == error_codes.LLM_CONTEXT_OPERATION_UNSUPPORTED


def _process_restart():
    first = Harness()
    created = first.create()
    restarted = Harness()
    with pytest.raises(LlmContextStoreError) as caught:
        restarted.store.reserve(
            restarted.request(
                LlmContextOperation.APPEND, "after-restart", created.context_id, 1
            )
        )
    assert caught.value.error_code == error_codes.LLM_CONTEXT_NOT_FOUND


def _completed_idempotency():
    h = Harness()
    created = h.create("stream-replay")
    assert h.store.status(ALICE, idempotency_key="stream-replay").context_id == created.context_id
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(h.request(LlmContextOperation.CREATE, "stream-replay"))
    assert caught.value.error_code == error_codes.ACTION_IDEMPOTENCY_CONFLICT
    assert h.store.snapshot(ALICE, created.context_id).version == 1


def _revocation_abort():
    h = Harness()
    created = h.create()
    mutation = h.store.reserve(
        h.request(LlmContextOperation.APPEND, "revoked", created.context_id, 1)
    )
    h.store.abort(mutation, error_codes.AUTH_NID_REVOKED)
    assert h.store.snapshot(ALICE, created.context_id).version == 1
    assert h.store.status(ALICE, idempotency_key="revoked").error_code == error_codes.AUTH_NID_REVOKED


def _principal_limit():
    h = Harness(max_contexts=1)
    h.create()
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(h.request(LlmContextOperation.CREATE, "over-limit"))
    assert caught.value.error_code == error_codes.LLM_CONTEXT_LIMIT_EXCEEDED


def _unsupported_operation():
    _advertised_operations()


def _missing_idempotency():
    h = Harness()
    with pytest.raises(LlmContextStoreError) as caught:
        h.store.reserve(h.request(LlmContextOperation.CREATE, ""))
    assert caught.value.error_code == error_codes.ACTION_PARAMS_INVALID
    assert h.store.sweep_expired() == 0


def test_validation_and_internal_safety_edges():
    h = Harness()
    request = h.request(LlmContextOperation.CREATE, "key")
    with pytest.raises(LlmContextStoreError):
        h.store.reserve(dataclasses.replace(request, ttl_seconds=0))
    with pytest.raises(LlmContextStoreError):
        h.store.status(ALICE)
    with pytest.raises(LlmContextStoreError):
        h.store.status(ALICE, context_id="bad", idempotency_key="also-bad")
    with pytest.raises(LlmContextStoreError):
        h.store.status(ALICE, context_id="bad")
