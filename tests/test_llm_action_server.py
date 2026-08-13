# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""HTTP contract tests for the stateful LLM Action Node coordinator."""

from __future__ import annotations

import asyncio
from enum import Enum

import httpx
import pytest

from nps_sdk.core.status_codes import NPS_AUTH_UNAUTHENTICATED
from nps_sdk.nwp import error_codes
from nps_sdk.nwp.action_node_server import (
    SYSTEM_TASK_CANCEL,
    SYSTEM_TASK_STATUS,
    ActionContext,
    ActionExecutionError,
    ActionExecutionResult,
    ActionNodeApp,
    ActionNodeOptions,
    IActionNodeProvider,
)
from nps_sdk.nwp.context_store import (
    InMemoryLlmContextStore,
    LlmContextOwner,
    LlmContextStoreOptions,
)
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.llm import (
    LLM_COMPLETE,
    LLM_CONTEXT_RELEASE,
    LLM_CONTEXT_STATUS,
    LlmContextOperation,
)
from nps_sdk.nwp.llm_action_server import (
    LlmAuthorizationStage,
    StatefulLlmActionOptions,
    StatefulLlmActionProvider,
)

PREFIX = "/llm"
NODE_ID = "urn:nps:node:llm.example:willow"
ALICE = "urn:nps:agent:labacacia:alice"
BOB = "urn:nps:agent:labacacia:bob"


class ProviderMode(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    MODEL_ERROR = "model_error"


class _LlmProvider(IActionNodeProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.mode = ProviderMode.SUCCESS
        self.delay = 0.0
        self.suppress_cancellation = False
        self.started = asyncio.Event()

    async def execute(
        self, frame: ActionFrame, context: ActionContext
    ) -> ActionExecutionResult:
        self.calls += 1
        self.started.set()
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                if not self.suppress_cancellation:
                    raise
        if self.mode == ProviderMode.FAILURE:
            raise ActionExecutionError(
                500, "NPS-SERVER-INTERNAL", error_codes.NODE_UNAVAILABLE, "provider failed"
            )
        if self.mode == ProviderMode.MODEL_ERROR:
            return ActionExecutionResult(
                result={
                    "stop_reason": "error",
                    "error": "model unavailable",
                    "context": {
                        "context_id": "AQIDBAUGBwgJCgsMDQ4PEA",
                        "version": 99,
                        "operation": "create",
                        "state": "active",
                    },
                }
            )
        return ActionExecutionResult(
            result={
                "stop_reason": "end_turn",
                "content": "First",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "wire_input_bytes": 128,
                },
            },
            token_est=1,
        )


class _Harness:
    def __init__(self) -> None:
        self.inner = _LlmProvider()
        self.store = InMemoryLlmContextStore(
            LlmContextStoreOptions(
                max_contexts_per_principal=7,
                max_ttl_seconds=900,
                tombstone_seconds=120,
                supported_operations=frozenset(
                    {
                        LlmContextOperation.CREATE,
                        LlmContextOperation.APPEND,
                        LlmContextOperation.RESET,
                        LlmContextOperation.RELEASE,
                    }
                ),
            )
        )
        self.options = StatefulLlmActionOptions(
            "workspace-a",
            "runtime-1",
            provider_name="willow",
            default_model="willow-small",
        )
        self.coordinator = StatefulLlmActionProvider(
            self.inner, self.store, self.options
        )
        node = ActionNodeOptions(node_id=NODE_ID, path_prefix=PREFIX)
        self.coordinator.configure_node(node)
        self.app = ActionNodeApp(node, self.coordinator)

    def client(self, agent: str | None = ALICE) -> httpx.AsyncClient:
        headers = {"X-NWP-Agent": agent} if agent is not None else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://llm",
            headers=headers,
        )


@pytest.fixture
def harness() -> _Harness:
    return _Harness()


def _create_params() -> dict:
    return {
        "kind": LLM_COMPLETE,
        "model": "willow-small",
        "stream": False,
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "One"},
        ],
        "context": {"operation": "create", "ttl_seconds": 600},
    }


async def _invoke(
    http: httpx.AsyncClient,
    action: str,
    params: dict,
    key: str | None = None,
    *,
    async_: bool = False,
) -> httpx.Response:
    frame = {"action_id": action, "params": params, "async": async_}
    if key is not None:
        frame["idempotency_key"] = key
    return await http.post(f"{PREFIX}/invoke", json=frame)


def _data(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"][0]


async def test_nwm_advertises_exact_actions_and_process_limits(harness: _Harness) -> None:
    async with harness.client() as http:
        response = await http.get(f"{PREFIX}/.nwm")
        actions = (await http.get(f"{PREFIX}/actions")).json()["actions"]
    profile = response.json()["profiles"]["llm"]
    assert profile["profile_version"] == "0.2"
    assert profile["provider"] == "willow"
    assert profile["supports_stream"] is False
    assert profile["context"] == {
        "supported": True,
        "operations": ["create", "append", "reset", "release"],
        "persistence": "process",
        "max_contexts_per_principal": 7,
        "max_ttl_seconds": 900,
        "tombstone_seconds": 120,
    }
    assert actions[LLM_CONTEXT_STATUS]["required_capability"] == "llm:context"
    assert actions[LLM_CONTEXT_RELEASE]["required_capability"] == "llm:context"


async def test_synchronous_create_commits_and_status_recovers_it(
    harness: _Harness,
) -> None:
    async with harness.client() as http:
        completion = _data(await _invoke(http, LLM_COMPLETE, _create_params(), "create-1"))
        receipt = completion["context"]
        status = _data(await _invoke(
            http, LLM_CONTEXT_STATUS, {"context_id": receipt["context_id"]}
        ))
    assert receipt["version"] == 1
    assert receipt["operation"] == "create"
    assert receipt["state"] == "active"
    assert completion["usage"]["wire_input_bytes"] == 128
    assert status["state"] == "active"
    assert status["version"] == 1
    assert harness.inner.calls == 1


async def test_append_commits_delta_and_release_creates_tombstone(
    harness: _Harness,
) -> None:
    async with harness.client() as http:
        created = _data(await _invoke(http, LLM_COMPLETE, _create_params(), "create-1"))
        context_id = created["context"]["context_id"]
        appended = _data(await _invoke(
            http,
            LLM_COMPLETE,
            {
                "kind": LLM_COMPLETE,
                "model": "willow-small",
                "messages": [{"role": "user", "content": "Two"}],
                "context": {
                    "operation": "append",
                    "context_id": context_id,
                    "base_version": 1,
                },
            },
            "append-1",
        ))
        assert len(harness.store.snapshot(_owner(ALICE), context_id).transcript) == 5
        released = _data(await _invoke(
            http,
            LLM_CONTEXT_RELEASE,
            {"context_id": context_id, "base_version": 2},
            "release-1",
        ))
    assert appended["context"]["version"] == 2
    assert released["state"] == "released"
    assert released["version"] == 3


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [(ProviderMode.FAILURE, 500), (ProviderMode.MODEL_ERROR, 200)],
)
async def test_provider_and_model_errors_abort_without_allocating_context(
    harness: _Harness, mode: ProviderMode, expected_status: int
) -> None:
    harness.inner.mode = mode
    async with harness.client() as http:
        response = await _invoke(http, LLM_COMPLETE, _create_params(), mode.value)
        assert response.status_code == expected_status
        if mode == ProviderMode.MODEL_ERROR:
            assert "context" not in _data(response)
        status = _data(await _invoke(
            http, LLM_CONTEXT_STATUS, {"idempotency_key": mode.value}
        ))
    assert status["state"] == "failed"
    assert "context_id" not in status


async def test_commit_reauthorization_failure_aborts_and_surfaces_auth_error(
    harness: _Harness,
) -> None:
    def authorize(_owner, _action, stage, _context) -> None:
        if stage == LlmAuthorizationStage.COMMIT:
            raise ActionExecutionError(
                401,
                NPS_AUTH_UNAUTHENTICATED,
                error_codes.AUTH_NID_REVOKED,
                "revoked before commit",
            )

    harness.options.authorizer = authorize
    async with harness.client() as http:
        response = await _invoke(http, LLM_COMPLETE, _create_params(), "revoked")
        status = _data(await _invoke(
            http, LLM_CONTEXT_STATUS, {"idempotency_key": "revoked"}
        ))
    assert response.status_code == 401
    assert response.json()["error"] == error_codes.AUTH_NID_REVOKED
    assert status["state"] == "failed"
    assert status["error_code"] == error_codes.AUTH_NID_REVOKED


async def test_async_completion_puts_receipt_only_in_terminal_result(
    harness: _Harness,
) -> None:
    harness.inner.delay = 0.02
    async with harness.client() as http:
        accepted = await _invoke(
            http, LLM_COMPLETE, _create_params(), "async-create", async_=True
        )
        assert accepted.status_code == 202
        assert "context" not in accepted.json()
        task = await _wait_for_terminal(http, accepted.json()["task_id"])
    assert task["status"] == "completed"
    assert task["result"]["context"]["version"] == 1


async def test_async_cancellation_aborts_reservation(harness: _Harness) -> None:
    harness.inner.delay = 5
    harness.inner.suppress_cancellation = True
    async with harness.client() as http:
        accepted = await _invoke(
            http, LLM_COMPLETE, _create_params(), "cancelled", async_=True
        )
        await asyncio.wait_for(harness.inner.started.wait(), timeout=1)
        cancelled = await _invoke(
            http, SYSTEM_TASK_CANCEL, {"task_id": accepted.json()["task_id"]}
        )
        assert cancelled.status_code == 200
        status = await _wait_for_context_outcome(http, "cancelled")
    assert status["state"] == "failed"
    assert "context_id" not in status


async def test_async_task_status_and_cancel_are_caller_scoped(harness: _Harness) -> None:
    harness.inner.delay = 5
    async with harness.client(ALICE) as alice:
        accepted = await _invoke(
            alice, LLM_COMPLETE, _create_params(), "private-task", async_=True
        )
        await asyncio.wait_for(harness.inner.started.wait(), timeout=1)
        task_id = accepted.json()["task_id"]
        async with harness.client(BOB) as bob:
            status = await _invoke(bob, SYSTEM_TASK_STATUS, {"task_id": task_id})
            cancel = await _invoke(bob, SYSTEM_TASK_CANCEL, {"task_id": task_id})
        assert status.status_code == 403
        assert cancel.status_code == 403
        assert (await _invoke(
            alice, SYSTEM_TASK_CANCEL, {"task_id": task_id}
        )).status_code == 200


async def test_response_idempotency_is_owner_scoped_and_does_not_recommit(
    harness: _Harness,
) -> None:
    async with harness.client(ALICE) as alice, harness.client(BOB) as bob:
        alice_first = _data(await _invoke(
            alice, LLM_COMPLETE, _create_params(), "shared-key"
        ))
        alice_replay = _data(await _invoke(
            alice, LLM_COMPLETE, _create_params(), "shared-key"
        ))
        bob_first = _data(await _invoke(
            bob, LLM_COMPLETE, _create_params(), "shared-key"
        ))
    alice_id = alice_first["context"]["context_id"]
    assert alice_replay["context"]["context_id"] == alice_id
    assert bob_first["context"]["context_id"] != alice_id
    assert harness.inner.calls == 2


async def test_cached_replay_rechecks_authorization(harness: _Harness) -> None:
    admitted = True

    def authorize(_owner, _action, stage, _context) -> None:
        if stage == LlmAuthorizationStage.ADMISSION and not admitted:
            raise ActionExecutionError(
                401,
                NPS_AUTH_UNAUTHENTICATED,
                error_codes.AUTH_NID_REVOKED,
                "caller was revoked",
            )

    harness.options.authorizer = authorize
    async with harness.client() as http:
        assert (await _invoke(
            http, LLM_COMPLETE, _create_params(), "cached"
        )).status_code == 200
        admitted = False
        replay = await _invoke(http, LLM_COMPLETE, _create_params(), "cached")
    assert replay.status_code == 401
    assert harness.inner.calls == 1


async def test_malformed_stateful_requests_fail_before_provider_dispatch(
    harness: _Harness,
) -> None:
    requests = [
        (_create_params(), None),
        ({**_create_params(), "kind": "wrong.kind"}, "wrong-kind"),
        ({**_create_params(), "stream": True}, "streamed"),
        ({**_create_params(), "tools": [{"name": "lookup"}]}, "tools"),
        (
            {**_create_params(), "context": {"operation": "reset"}},
            "reset-without-version",
        ),
    ]
    async with harness.client() as http:
        for params, key in requests:
            response = await _invoke(http, LLM_COMPLETE, params, key)
            assert response.status_code == 422
            assert response.json()["error"] == error_codes.ACTION_PARAMS_INVALID
    assert harness.inner.calls == 0


async def test_lifecycle_actions_require_authentication_and_owner(
    harness: _Harness,
) -> None:
    async with harness.client(None) as anonymous:
        unauthenticated = await _invoke(
            anonymous,
            LLM_CONTEXT_STATUS,
            {"context_id": "AQIDBAUGBwgJCgsMDQ4PEA"},
        )
    assert unauthenticated.status_code == 401

    async with harness.client(ALICE) as alice:
        created = _data(await _invoke(
            alice, LLM_COMPLETE, _create_params(), "owned"
        ))
    context_id = created["context"]["context_id"]
    async with harness.client(BOB) as bob:
        forbidden = await _invoke(
            bob, LLM_CONTEXT_STATUS, {"context_id": context_id}
        )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == error_codes.LLM_CONTEXT_FORBIDDEN


async def _wait_for_terminal(http: httpx.AsyncClient, task_id: str) -> dict:
    for _ in range(50):
        await asyncio.sleep(0.02)
        response = await _invoke(http, SYSTEM_TASK_STATUS, {"task_id": task_id})
        task = _data(response)
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
    raise TimeoutError("async task did not reach a terminal state")


async def _wait_for_context_outcome(http: httpx.AsyncClient, key: str) -> dict:
    for _ in range(50):
        await asyncio.sleep(0.02)
        response = await _invoke(
            http, LLM_CONTEXT_STATUS, {"idempotency_key": key}
        )
        if response.status_code == 200:
            status = _data(response)
            if status["state"] == "failed":
                return status
    raise TimeoutError("context outcome did not reach failed state")


def _owner(nid: str) -> LlmContextOwner:
    return LlmContextOwner(nid, "workspace-a")
