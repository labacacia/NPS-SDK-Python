# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Action Node coordinator for the NWP stateful LLM context contract."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from nps_sdk.core.status_codes import (
    NPS_AUTH_UNAUTHENTICATED,
    NPS_SERVER_INTERNAL,
    to_http_status,
)
from nps_sdk.nwp import error_codes
from nps_sdk.nwp.action_node_server import (
    ActionContext,
    ActionExecutionError,
    ActionExecutionResult,
    ActionNodeOptions,
    ActionSpec,
    IActionNodeProvider,
)
from nps_sdk.nwp.context_store import (
    InMemoryLlmContextStore,
    LlmContextBinding,
    LlmContextMutationRequest,
    LlmContextMutationReservation,
    LlmContextOwner,
    LlmContextStoreError,
)
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.llm import (
    CAPABILITY_LLM_COMPLETE,
    CAPABILITY_LLM_CONTEXT,
    LLM_COMPLETE,
    LLM_COMPLETE_RESPONSE_ANCHOR,
    LLM_CONTEXT_RELEASE,
    LLM_CONTEXT_RELEASE_RESPONSE_ANCHOR,
    LLM_CONTEXT_STATUS,
    LLM_CONTEXT_STATUS_RESPONSE_ANCHOR,
    LlmCompleteActionRequest,
    LlmCompleteActionResponse,
    LlmContextOperation,
    LlmContextReleaseRequestDto,
    LlmContextStatusRequestDto,
    LlmMessageDto,
    LlmStopReason,
)

COMPLETE_REQUEST_ANCHOR = "nps:system:llm.complete:request"
STATUS_REQUEST_ANCHOR = "nps:system:llm.context.status:request"
RELEASE_REQUEST_ANCHOR = "nps:system:llm.context.release:request"


class LlmAuthorizationStage(str, Enum):
    ADMISSION = "admission"
    COMMIT = "commit"


LlmContextAuthorizer = Callable[
    [LlmContextOwner, str, LlmAuthorizationStage, ActionContext],
    None | Awaitable[None],
]


@dataclasses.dataclass
class StatefulLlmActionOptions:
    """Deployment-owned settings that must never be sourced from request payloads."""

    security_scope: str
    runtime_revision: str
    provider_name: str | None = None
    default_model: str | None = None
    supports_tools: bool = False
    supports_json_mode: bool = False
    reasoning_visibility: str | None = None
    authorizer: LlmContextAuthorizer | None = None

    def __post_init__(self) -> None:
        if not self.security_scope.strip():
            raise ValueError("security_scope must not be empty")
        if not self.runtime_revision.strip():
            raise ValueError("runtime_revision must not be empty")


class StatefulLlmActionProvider(IActionNodeProvider):
    """Wrap an ordinary LLM provider with the official context lifecycle."""

    def __init__(
        self,
        inner: IActionNodeProvider,
        store: InMemoryLlmContextStore,
        options: StatefulLlmActionOptions,
    ) -> None:
        self._inner = inner
        self.store = store
        self._options = options

    def configure_node(self, node: ActionNodeOptions) -> None:
        current = node.actions.get(LLM_COMPLETE)
        node.actions[LLM_COMPLETE] = ActionSpec(
            async_=current.async_ if current is not None else True,
            description=current.description if current is not None else "Complete an LLM request",
            params_anchor=COMPLETE_REQUEST_ANCHOR,
            result_anchor=LLM_COMPLETE_RESPONSE_ANCHOR,
            idempotent=True,
            timeout_ms_default=current.timeout_ms_default if current is not None else None,
            timeout_ms_max=current.timeout_ms_max if current is not None else None,
            required_capability=CAPABILITY_LLM_COMPLETE,
        )
        node.actions[LLM_CONTEXT_STATUS] = ActionSpec(
            description="Inspect an LLM context or retained create outcome",
            params_anchor=STATUS_REQUEST_ANCHOR,
            result_anchor=LLM_CONTEXT_STATUS_RESPONSE_ANCHOR,
            required_capability=CAPABILITY_LLM_CONTEXT,
        )
        node.actions[LLM_CONTEXT_RELEASE] = ActionSpec(
            description="Release an LLM context",
            params_anchor=RELEASE_REQUEST_ANCHOR,
            result_anchor=LLM_CONTEXT_RELEASE_RESPONSE_ANCHOR,
            idempotent=True,
            required_capability=CAPABILITY_LLM_CONTEXT,
        )

        descriptor = self.store.descriptor
        profile: dict[str, Any] = {
            "profile_version": "0.2",
            "actions": [LLM_COMPLETE, LLM_CONTEXT_STATUS, LLM_CONTEXT_RELEASE],
            "supports_stream": False,
            "supports_tools": self._options.supports_tools,
            "supports_json_mode": self._options.supports_json_mode,
            "context": {
                "supported": True,
                "operations": [operation.value for operation in descriptor.operations],
                "persistence": descriptor.persistence,
                "max_contexts_per_principal": descriptor.max_contexts_per_principal,
                "max_ttl_seconds": descriptor.max_ttl_seconds,
                "tombstone_seconds": descriptor.tombstone_seconds,
            },
        }
        if self._options.provider_name is not None:
            profile["provider"] = self._options.provider_name
        if self._options.default_model is not None:
            profile["default_model"] = self._options.default_model
        if self._options.reasoning_visibility is not None:
            profile["reasoning_visibility"] = self._options.reasoning_visibility
        node.profiles = {**(node.profiles or {}), "llm": profile}

    async def authorize(self, frame: ActionFrame, context: ActionContext) -> None:
        requires_context_auth = frame.action_id in (LLM_CONTEXT_STATUS, LLM_CONTEXT_RELEASE)
        if frame.action_id == LLM_COMPLETE and isinstance(frame.params, dict):
            requires_context_auth = frame.params.get("context") is not None
        if not requires_context_auth:
            return
        owner = self._owner(context)
        await self._check_authorization(
            owner, frame.action_id, LlmAuthorizationStage.ADMISSION, context
        )

    async def execute(
        self, frame: ActionFrame, context: ActionContext
    ) -> ActionExecutionResult:
        if frame.action_id == LLM_COMPLETE:
            return await self._complete(frame, context)
        if frame.action_id == LLM_CONTEXT_STATUS:
            return self._status(frame, context)
        if frame.action_id == LLM_CONTEXT_RELEASE:
            return self._release(frame, context)
        return await self._inner.execute(frame, context)

    async def _complete(
        self, frame: ActionFrame, context: ActionContext
    ) -> ActionExecutionResult:
        try:
            request = LlmCompleteActionRequest.from_action_frame(frame)
        except (KeyError, TypeError, ValueError) as ex:
            raise _params_error(str(ex)) from ex
        if not request.model.strip():
            raise _params_error("llm.complete requires a non-empty model")
        if not self._options.supports_tools and request.tools:
            raise _params_error("this node does not advertise LLM tool-definition support")
        if request.context is None:
            return await self._inner.execute(frame, context)
        if request.stream:
            raise _params_error(
                "the Action Server context coordinator supports unary/async completion, not streaming"
            )
        if request.context.operation in (
            LlmContextOperation.APPEND,
            LlmContextOperation.FORK,
            LlmContextOperation.RESET,
        ) and (
            not request.context.context_id or request.context.base_version is None
        ):
            raise _params_error(
                "append/fork/reset require context_id and base_version"
            )

        owner = self._owner(context)
        mutation = LlmContextMutationRequest(
            operation=request.context.operation,
            owner=owner,
            context_id=request.context.context_id,
            base_version=request.context.base_version,
            binding=self._resolve_binding(owner, request),
            messages=request.messages,
            ttl_seconds=request.context.ttl_seconds,
            idempotency_key=frame.idempotency_key or "",
            request_id=frame.request_id or "",
        )
        try:
            reservation = self.store.reserve(mutation)
        except LlmContextStoreError as ex:
            raise _store_error(ex) from ex

        try:
            result = await self._inner.execute(frame, context)
        except asyncio.CancelledError:
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise
        except ActionExecutionError as ex:
            self._abort(reservation, ex.error_code)
            raise
        except Exception:
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise

        task = asyncio.current_task()
        if task is not None and task.cancelling():
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise asyncio.CancelledError
        if not isinstance(result.result, dict):
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise _internal_error("stateful llm.complete returned no official response object")
        try:
            response = LlmCompleteActionResponse.from_dict(result.result)
        except (KeyError, TypeError, ValueError) as ex:
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise _internal_error(
                f"stateful llm.complete returned an invalid official response: {ex}"
            ) from ex

        if response.stop_reason == LlmStopReason.ERROR:
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            return _completion_result(dataclasses.replace(response, context=None), result)

        try:
            await self._check_authorization(
                owner, frame.action_id, LlmAuthorizationStage.COMMIT, context
            )
        except asyncio.CancelledError:
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise
        except ActionExecutionError as ex:
            self._abort(reservation, ex.error_code)
            raise
        except Exception:
            self._abort(reservation, error_codes.NODE_UNAVAILABLE)
            raise

        assistant = LlmMessageDto(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )
        try:
            receipt = self.store.commit(reservation, assistant)
        except LlmContextStoreError as ex:
            raise _store_error(ex) from ex
        return _completion_result(dataclasses.replace(response, context=receipt), result)

    def _status(self, frame: ActionFrame, context: ActionContext) -> ActionExecutionResult:
        params = _params(frame, LLM_CONTEXT_STATUS)
        request = LlmContextStatusRequestDto(
            context_id=params.get("context_id"),
            idempotency_key=params.get("idempotency_key"),
        )
        try:
            status = self.store.status(
                self._owner(context),
                context_id=request.context_id,
                idempotency_key=request.idempotency_key,
            )
        except LlmContextStoreError as ex:
            raise _store_error(ex) from ex
        return ActionExecutionResult(
            result=_wire(status), anchor_ref=LLM_CONTEXT_STATUS_RESPONSE_ANCHOR
        )

    def _release(self, frame: ActionFrame, context: ActionContext) -> ActionExecutionResult:
        params = _params(frame, LLM_CONTEXT_RELEASE)
        try:
            request = LlmContextReleaseRequestDto(
                context_id=params["context_id"], base_version=params["base_version"]
            )
        except (KeyError, TypeError) as ex:
            raise _params_error(f"invalid {LLM_CONTEXT_RELEASE} params: {ex}") from ex
        try:
            receipt = self.store.release(
                self._owner(context),
                request.context_id,
                request.base_version,
                frame.idempotency_key or "",
            )
        except LlmContextStoreError as ex:
            raise _store_error(ex) from ex
        return ActionExecutionResult(
            result=_wire(receipt), anchor_ref=LLM_CONTEXT_RELEASE_RESPONSE_ANCHOR
        )

    def _resolve_binding(
        self, owner: LlmContextOwner, request: LlmCompleteActionRequest
    ) -> LlmContextBinding:
        context = request.context
        assert context is not None
        if context.operation in (LlmContextOperation.APPEND, LlmContextOperation.FORK):
            if context.context_id is None:
                raise _params_error("append/fork require context_id and base_version")
            try:
                snapshot = self.store.snapshot(owner, context.context_id)
            except LlmContextStoreError as ex:
                raise _store_error(ex) from ex
            return LlmContextBinding(
                model=request.model,
                system_messages=snapshot.binding.system_messages,
                tools=request.tools if request.tools is not None else snapshot.binding.tools,
                runtime_revision=self._options.runtime_revision,
            )
        return LlmContextBinding(
            model=request.model,
            system_messages=tuple(
                message for message in request.messages if message.role.lower() == "system"
            ),
            tools=request.tools,
            runtime_revision=self._options.runtime_revision,
        )

    def _owner(self, context: ActionContext) -> LlmContextOwner:
        if context.agent_nid is None or not context.agent_nid.strip():
            raise ActionExecutionError(
                401,
                NPS_AUTH_UNAUTHENTICATED,
                error_codes.AUTH_NID_SCOPE_VIOLATION,
                "stateful LLM context actions require an authenticated agent NID",
            )
        return LlmContextOwner(context.agent_nid, self._options.security_scope)

    async def _check_authorization(
        self,
        owner: LlmContextOwner,
        action_id: str,
        stage: LlmAuthorizationStage,
        context: ActionContext,
    ) -> None:
        if self._options.authorizer is None:
            return
        result = self._options.authorizer(owner, action_id, stage, context)
        if inspect.isawaitable(result):
            await result

    def _abort(
        self, reservation: LlmContextMutationReservation, error_code: str
    ) -> None:
        try:
            self.store.abort(reservation, error_code)
        except RuntimeError as ex:
            raise _internal_error(f"failed to abort LLM context reservation: {ex}") from ex


def _params(frame: ActionFrame, action_id: str) -> dict[str, Any]:
    if not isinstance(frame.params, dict):
        raise _params_error(f"{action_id} requires an object params payload")
    return frame.params


def _wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {
            field.name: _wire(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if getattr(value, field.name) is not None
        }
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _completion_result(
    response: LlmCompleteActionResponse, provider_result: ActionExecutionResult
) -> ActionExecutionResult:
    return ActionExecutionResult(
        result=response.to_dict(),
        anchor_ref=provider_result.anchor_ref or LLM_COMPLETE_RESPONSE_ANCHOR,
        token_est=provider_result.token_est,
    )


def _params_error(message: str) -> ActionExecutionError:
    return ActionExecutionError(
        422,
        error_codes.NWP_ERROR_TO_NPS_STATUS[error_codes.ACTION_PARAMS_INVALID],
        error_codes.ACTION_PARAMS_INVALID,
        message,
    )


def _internal_error(message: str) -> ActionExecutionError:
    return ActionExecutionError(
        500, NPS_SERVER_INTERNAL, error_codes.NODE_UNAVAILABLE, message
    )


def _store_error(error: LlmContextStoreError) -> ActionExecutionError:
    nps_status = error_codes.NWP_ERROR_TO_NPS_STATUS.get(
        error.error_code, NPS_SERVER_INTERNAL
    )
    return ActionExecutionError(
        to_http_status(nps_status), nps_status, error.error_code, str(error)
    )
