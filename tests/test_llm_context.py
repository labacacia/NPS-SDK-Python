# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

from nps_sdk.nwp.llm import (
    LLM_COMPLETE,
    LLM_CONTEXT_RELEASE,
    LLM_CONTEXT_STATUS,
    LlmCompleteActionRequest,
    LlmContextOperation,
    LlmContextReleaseRequestDto,
    LlmContextRequestDto,
    LlmContextStatusRequestDto,
    LlmMessageDto,
    context_release_action_frame,
    context_status_action_frame,
)
from nps_sdk.core.status_codes import NPS_LIMIT_RESOURCE, to_http_status
from nps_sdk.nwp.error_codes import LLM_CONTEXT_LIMIT_EXCEEDED, NWP_ERROR_TO_NPS_STATUS


def test_stateful_complete_round_trip_uses_canonical_wire_fields():
    request = LlmCompleteActionRequest(
        model="willow-small",
        messages=(LlmMessageDto(role="user", content="Hello"),),
        context=LlmContextRequestDto(
            operation=LlmContextOperation.CREATE,
            ttl_seconds=600,
        ),
    )
    wire = request.to_dict()
    assert wire["kind"] == LLM_COMPLETE
    assert wire["context"] == {"operation": "create", "ttl_seconds": 600}
    frame = request.to_action_frame(idempotency_key="create-1")
    assert LlmCompleteActionRequest.from_action_frame(frame) == request


def test_context_lifecycle_action_helpers():
    status = context_status_action_frame(
        LlmContextStatusRequestDto(idempotency_key="create-1")
    )
    assert status.action_id == LLM_CONTEXT_STATUS
    assert status.params == {"idempotency_key": "create-1"}

    release = context_release_action_frame(
        LlmContextReleaseRequestDto(
            context_id="AQIDBAUGBwgJCgsMDQ4PEA",
            base_version=7,
        ),
        idempotency_key="release-1",
    )
    assert release.action_id == LLM_CONTEXT_RELEASE
    assert release.idempotency_key == "release-1"
    assert release.params["base_version"] == 7


def test_context_error_status_mapping():
    assert NWP_ERROR_TO_NPS_STATUS[LLM_CONTEXT_LIMIT_EXCEEDED] == NPS_LIMIT_RESOURCE
    assert to_http_status(NPS_LIMIT_RESOURCE) == 429
