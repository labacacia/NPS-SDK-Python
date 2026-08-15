# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Official NWP LLM ActionFrame payload contracts (NPS-2 §7.5–§7.6)."""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, TypeVar

from nps_sdk.nwp.frames import ActionFrame

LLM_COMPLETE = "llm.complete"
LLM_CONTEXT_STATUS = "llm.context.status"
LLM_CONTEXT_RELEASE = "llm.context.release"
LLM_COMPLETE_RESPONSE_ANCHOR = "nps:system:llm.complete:response"
LLM_COMPLETE_STREAM_ANCHOR = "nps:system:llm.complete:stream"
LLM_CONTEXT_STATUS_RESPONSE_ANCHOR = "nps:system:llm.context.status:response"
LLM_CONTEXT_RELEASE_RESPONSE_ANCHOR = "nps:system:llm.context.release:response"

CAPABILITY_LLM_COMPLETE = "llm:complete"
CAPABILITY_LLM_CONTEXT = "llm:context"
CAPABILITY_LLM_STREAM = "llm:stream"
CAPABILITY_LLM_TOOL_CALL = "llm:tool_call"


class LlmStopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    LENGTH = "length"
    ERROR = "error"


class LlmContextOperation(str, Enum):
    CREATE = "create"
    APPEND = "append"
    FORK = "fork"
    RESET = "reset"
    RELEASE = "release"


class LlmContextState(str, Enum):
    BUSY = "busy"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    FAILED = "failed"


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


@dataclasses.dataclass(frozen=True)
class LlmToolCallDto:
    call_id: str
    tool_name: str
    arguments_json: str


@dataclasses.dataclass(frozen=True)
class ToolParameterDto:
    name: str
    type: str
    description: str | None = None
    required: bool = False


@dataclasses.dataclass(frozen=True)
class LlmToolDefinitionDto:
    name: str
    description: str | None = None
    parameters: tuple[ToolParameterDto, ...] | None = None


@dataclasses.dataclass(frozen=True)
class LlmMessageDto:
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: tuple[LlmToolCallDto, ...] | None = None


@dataclasses.dataclass(frozen=True)
class LlmContextRequestDto:
    operation: LlmContextOperation
    context_id: str | None = None
    base_version: int | None = None
    ttl_seconds: int | None = None


@dataclasses.dataclass(frozen=True)
class LlmContextReceiptDto:
    context_id: str
    version: int
    operation: LlmContextOperation
    state: LlmContextState
    expires_at: str | None = None
    parent_context_id: str | None = None
    parent_version: int | None = None


@dataclasses.dataclass(frozen=True)
class LlmContextStatusRequestDto:
    context_id: str | None = None
    idempotency_key: str | None = None


@dataclasses.dataclass(frozen=True)
class LlmContextReleaseRequestDto:
    context_id: str
    base_version: int


@dataclasses.dataclass(frozen=True)
class LlmContextStatusDto:
    state: LlmContextState
    context_id: str | None = None
    version: int | None = None
    expires_at: str | None = None
    request_id: str | None = None
    error_code: str | None = None


@dataclasses.dataclass(frozen=True)
class LlmUsageDto:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_hit: bool | None = None
    reused_tokens: int | None = None
    evaluated_tokens: int | None = None
    wire_input_bytes: int | None = None


@dataclasses.dataclass(frozen=True)
class LlmCompleteActionRequest:
    model: str
    messages: tuple[LlmMessageDto, ...]
    kind: str = LLM_COMPLETE
    max_tokens: int | None = None
    stream: bool = False
    tools: tuple[LlmToolDefinitionDto, ...] | None = None
    context: LlmContextRequestDto | None = None

    def to_dict(self) -> dict[str, Any]:
        return _wire(self)

    def to_action_frame(
        self,
        *,
        idempotency_key: str | None = None,
        timeout_ms: int = 5000,
        async_: bool = False,
        request_id: str | None = None,
    ) -> ActionFrame:
        return ActionFrame(
            action_id=LLM_COMPLETE,
            params=self.to_dict(),
            idempotency_key=idempotency_key,
            timeout_ms=timeout_ms,
            async_=async_,
            request_id=request_id,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LlmCompleteActionRequest":
        messages = tuple(
            LlmMessageDto(
                role=item["role"],
                content=item.get("content"),
                tool_call_id=item.get("tool_call_id"),
                tool_name=item.get("tool_name"),
                tool_calls=_tool_calls(item.get("tool_calls")),
            )
            for item in data["messages"]
        )
        tools = data.get("tools")
        context = data.get("context")
        return cls(
            kind=data.get("kind", LLM_COMPLETE),
            model=data["model"],
            max_tokens=data.get("max_tokens"),
            stream=bool(data.get("stream", False)),
            messages=messages,
            tools=None if tools is None else tuple(
                LlmToolDefinitionDto(
                    name=item["name"],
                    description=item.get("description"),
                    parameters=None if item.get("parameters") is None else tuple(
                        ToolParameterDto(**parameter) for parameter in item["parameters"]
                    ),
                ) for item in tools
            ),
            context=None if context is None else LlmContextRequestDto(
                operation=LlmContextOperation(context["operation"]),
                context_id=context.get("context_id"),
                base_version=context.get("base_version"),
                ttl_seconds=context.get("ttl_seconds"),
            ),
        )

    @classmethod
    def from_action_frame(cls, frame: ActionFrame) -> "LlmCompleteActionRequest":
        if frame.action_id != LLM_COMPLETE:
            raise ValueError(f"unexpected action_id: {frame.action_id}")
        request = cls.from_dict(frame.params)
        if request.kind != LLM_COMPLETE:
            raise ValueError(f"unexpected payload kind: {request.kind}")
        return request


@dataclasses.dataclass(frozen=True)
class LlmCompleteActionResponse:
    stop_reason: LlmStopReason
    content: str | None = None
    tool_calls: tuple[LlmToolCallDto, ...] | None = None
    error: str | None = None
    usage: LlmUsageDto | None = None
    context: LlmContextReceiptDto | None = None

    def to_dict(self) -> dict[str, Any]:
        return _wire(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LlmCompleteActionResponse":
        usage = data.get("usage")
        context = data.get("context")
        return cls(
            stop_reason=LlmStopReason(data["stop_reason"]),
            content=data.get("content"),
            tool_calls=_tool_calls(data.get("tool_calls")),
            error=data.get("error"),
            usage=None if usage is None else LlmUsageDto(**usage),
            context=None if context is None else LlmContextReceiptDto(
                context_id=context["context_id"],
                version=context["version"],
                operation=LlmContextOperation(context["operation"]),
                state=LlmContextState(context["state"]),
                expires_at=context.get("expires_at"),
                parent_context_id=context.get("parent_context_id"),
                parent_version=context.get("parent_version"),
            ),
        )


@dataclasses.dataclass(frozen=True)
class LlmCompleteStreamChunkDto:
    content_delta: str | None = None
    tool_calls: tuple[LlmToolCallDto, ...] | None = None
    stop_reason: LlmStopReason | None = None
    error: str | None = None
    usage: LlmUsageDto | None = None
    context: LlmContextReceiptDto | None = None

    def to_dict(self) -> dict[str, Any]:
        return _wire(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LlmCompleteStreamChunkDto":
        usage = data.get("usage")
        context = data.get("context")
        return cls(
            content_delta=data.get("content_delta"),
            tool_calls=_tool_calls(data.get("tool_calls")),
            stop_reason=(None if data.get("stop_reason") is None
                         else LlmStopReason(data["stop_reason"])),
            error=data.get("error"),
            usage=None if usage is None else LlmUsageDto(**usage),
            context=None if context is None else LlmContextReceiptDto(
                context_id=context["context_id"],
                version=context["version"],
                operation=LlmContextOperation(context["operation"]),
                state=LlmContextState(context["state"]),
                expires_at=context.get("expires_at"),
                parent_context_id=context.get("parent_context_id"),
                parent_version=context.get("parent_version"),
            ),
        )


def context_status_action_frame(
    request: LlmContextStatusRequestDto,
    *,
    request_id: str | None = None,
) -> ActionFrame:
    return ActionFrame(
        action_id=LLM_CONTEXT_STATUS,
        params=_wire(request),
        request_id=request_id,
    )


def context_release_action_frame(
    request: LlmContextReleaseRequestDto,
    *,
    idempotency_key: str,
    request_id: str | None = None,
) -> ActionFrame:
    return ActionFrame(
        action_id=LLM_CONTEXT_RELEASE,
        params=_wire(request),
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


def _tool_calls(value: Any) -> tuple[LlmToolCallDto, ...] | None:
    if value is None:
        return None
    return tuple(LlmToolCallDto(**item) for item in value)
