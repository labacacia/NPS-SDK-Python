# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests — the single NWP §16.3 error mapping (NPS-CR-0010 §5)."""

from __future__ import annotations

import pytest

from nps_sdk.nwp.error_codes import NWP_ERROR_TO_NPS_STATUS
from nps_sdk.nwp.inbound import (
    BridgeErrorCodes,
    BridgeErrorMap,
    BridgeJsonRpcErrorCodes,
)


class TestToJsonRpc:
    @pytest.mark.parametrize("status,expected", [
        ("NPS-CLIENT-BAD-FRAME", -32600),
        ("NPS-CLIENT-BAD-PARAM", -32602),
        ("NPS-CLIENT-UNPROCESSABLE", -32602),
        ("NPS-CLIENT-GONE", -32602),
        ("NPS-CLIENT-NOT-FOUND", -32601),
        ("NPS-CLIENT-CONFLICT", -32004),
        ("NPS-AUTH-UNAUTHENTICATED", -32001),
        ("NPS-AUTH-FORBIDDEN", -32003),
        ("NPS-LIMIT-RATE", -32005),
        ("NPS-LIMIT-BUDGET", -32005),
        ("NPS-LIMIT-PAYLOAD", -32005),
        ("NPS-SERVER-UNSUPPORTED", -32601),
        ("NPS-SERVER-INTERNAL", -32603),
        ("NPS-SERVER-UNAVAILABLE", -32603),
        ("NPS-SERVER-TIMEOUT", -32603),
        ("NPS-DOWNSTREAM-UNAVAILABLE", -32603),
    ])
    def test_normative_rows(self, status, expected):
        assert BridgeErrorMap.to_json_rpc(status) == expected

    def test_unknown_status_falls_back_to_internal_error(self):
        assert BridgeErrorMap.to_json_rpc("NPS-SOMETHING-ELSE") == -32603
        assert BridgeErrorMap.to_json_rpc(None) == -32603

    def test_not_found_is_the_only_param_sensitive_row(self):
        # unknown TOOL in tools/call is a missing method...
        assert BridgeErrorMap.to_json_rpc("NPS-CLIENT-NOT-FOUND") == -32601
        # ...unknown URI in resources/read is a bad argument.
        assert BridgeErrorMap.to_json_rpc("NPS-CLIENT-NOT-FOUND", resource_read=True) == -32602
        # No other row changes under resource_read.
        for status in ("NPS-AUTH-FORBIDDEN", "NPS-SERVER-UNSUPPORTED", "NPS-CLIENT-CONFLICT"):
            assert (BridgeErrorMap.to_json_rpc(status)
                    == BridgeErrorMap.to_json_rpc(status, resource_read=True))

    def test_auth_classes_are_not_collapsed(self):
        assert (BridgeErrorMap.to_json_rpc("NPS-AUTH-UNAUTHENTICATED")
                != BridgeErrorMap.to_json_rpc("NPS-AUTH-FORBIDDEN"))

    def test_the_retired_tool_not_found_code_is_never_emitted(self):
        assert BridgeJsonRpcErrorCodes.RESERVED_TOOL_NOT_FOUND == -32002
        emitted = {BridgeErrorMap.to_json_rpc(s) for s in (
            "NPS-CLIENT-BAD-FRAME", "NPS-CLIENT-BAD-PARAM", "NPS-CLIENT-NOT-FOUND",
            "NPS-CLIENT-CONFLICT", "NPS-AUTH-UNAUTHENTICATED", "NPS-AUTH-FORBIDDEN",
            "NPS-LIMIT-RATE", "NPS-SERVER-UNSUPPORTED", "NPS-SERVER-INTERNAL")}
        assert -32002 not in emitted


class TestToGrpcStatus:
    @pytest.mark.parametrize("status,expected", [
        ("NPS-CLIENT-BAD-FRAME", "INVALID_ARGUMENT"),
        ("NPS-CLIENT-BAD-PARAM", "INVALID_ARGUMENT"),
        ("NPS-CLIENT-UNPROCESSABLE", "INVALID_ARGUMENT"),
        ("NPS-CLIENT-NOT-FOUND", "NOT_FOUND"),
        ("NPS-CLIENT-GONE", "NOT_FOUND"),
        ("NPS-CLIENT-CONFLICT", "ABORTED"),
        ("NPS-AUTH-UNAUTHENTICATED", "UNAUTHENTICATED"),
        ("NPS-AUTH-FORBIDDEN", "PERMISSION_DENIED"),
        ("NPS-LIMIT-RATE", "RESOURCE_EXHAUSTED"),
        ("NPS-LIMIT-BUDGET", "RESOURCE_EXHAUSTED"),
        ("NPS-LIMIT-PAYLOAD", "RESOURCE_EXHAUSTED"),
        ("NPS-SERVER-UNSUPPORTED", "UNIMPLEMENTED"),
        ("NPS-SERVER-INTERNAL", "INTERNAL"),
        ("NPS-SERVER-UNAVAILABLE", "UNAVAILABLE"),
        ("NPS-DOWNSTREAM-UNAVAILABLE", "UNAVAILABLE"),
        ("NPS-SERVER-TIMEOUT", "DEADLINE_EXCEEDED"),
        ("NPS-WHATEVER", "INTERNAL"),
        (None, "INTERNAL"),
    ])
    def test_rows(self, status, expected):
        assert BridgeErrorMap.to_grpc_status(status) == expected

    def test_server_classes_are_not_all_collapsed_onto_unavailable(self):
        # The old ingress collapsed every 5xx onto UNAVAILABLE; §16.3 forbids it.
        assert len({BridgeErrorMap.to_grpc_status(s) for s in (
            "NPS-SERVER-INTERNAL", "NPS-SERVER-UNAVAILABLE", "NPS-SERVER-TIMEOUT",
            "NPS-SERVER-UNSUPPORTED")}) == 4

    def test_auth_classes_are_not_collapsed_onto_permission_denied(self):
        assert BridgeErrorMap.to_grpc_status("NPS-AUTH-UNAUTHENTICATED") == "UNAUTHENTICATED"
        assert BridgeErrorMap.to_grpc_status("NPS-AUTH-FORBIDDEN") == "PERMISSION_DENIED"


class TestReverseDirection:
    @pytest.mark.parametrize("http,expected", [
        (400, "NPS-CLIENT-BAD-PARAM"),
        (401, "NPS-AUTH-UNAUTHENTICATED"),
        (403, "NPS-AUTH-FORBIDDEN"),
        (404, "NPS-CLIENT-NOT-FOUND"),
        (408, "NPS-SERVER-TIMEOUT"),
        (409, "NPS-CLIENT-CONFLICT"),
        (410, "NPS-CLIENT-GONE"),
        (413, "NPS-LIMIT-PAYLOAD"),
        (415, "NPS-SERVER-ENCODING-UNSUPPORTED"),
        (422, "NPS-CLIENT-UNPROCESSABLE"),
        (429, "NPS-LIMIT-RATE"),
        (501, "NPS-SERVER-UNSUPPORTED"),
        (502, "NPS-DOWNSTREAM-UNAVAILABLE"),
        (503, "NPS-SERVER-UNAVAILABLE"),
        (504, "NPS-DOWNSTREAM-UNAVAILABLE"),
        (500, "NPS-SERVER-INTERNAL"),
        (599, "NPS-SERVER-INTERNAL"),
        (418, "NPS-CLIENT-BAD-PARAM"),
        (204, "NPS-OK"),
    ])
    def test_from_http_status(self, http, expected):
        assert BridgeErrorMap.from_http_status(http) == expected

    @pytest.mark.parametrize("code,expected", [
        (-32700, "NPS-CLIENT-BAD-FRAME"),
        (-32600, "NPS-CLIENT-BAD-FRAME"),
        (-32601, "NPS-CLIENT-NOT-FOUND"),
        (-32602, "NPS-CLIENT-BAD-PARAM"),
        (-32603, "NPS-SERVER-INTERNAL"),
        (-32001, "NPS-AUTH-UNAUTHENTICATED"),
        (-32003, "NPS-AUTH-FORBIDDEN"),
        (-32004, "NPS-CLIENT-CONFLICT"),
        (-32005, "NPS-LIMIT-RATE"),
        (-32000, "NPS-DOWNSTREAM-UNAVAILABLE"),
        (-32099, "NPS-SERVER-INTERNAL"),
    ])
    def test_from_json_rpc(self, code, expected):
        assert BridgeErrorMap.from_json_rpc(code) == expected

    @pytest.mark.parametrize("grpc,expected", [
        ("OK", "NPS-OK"),
        ("INVALID_ARGUMENT", "NPS-CLIENT-BAD-PARAM"),
        ("FAILED_PRECONDITION", "NPS-CLIENT-UNPROCESSABLE"),
        ("NOT_FOUND", "NPS-CLIENT-NOT-FOUND"),
        ("ALREADY_EXISTS", "NPS-CLIENT-CONFLICT"),
        ("ABORTED", "NPS-CLIENT-CONFLICT"),
        ("UNAUTHENTICATED", "NPS-AUTH-UNAUTHENTICATED"),
        ("PERMISSION_DENIED", "NPS-AUTH-FORBIDDEN"),
        ("RESOURCE_EXHAUSTED", "NPS-LIMIT-RATE"),
        ("UNIMPLEMENTED", "NPS-SERVER-UNSUPPORTED"),
        ("UNAVAILABLE", "NPS-SERVER-UNAVAILABLE"),
        ("DEADLINE_EXCEEDED", "NPS-SERVER-TIMEOUT"),
        ("INTERNAL", "NPS-SERVER-INTERNAL"),
        ("UNKNOWN", "NPS-SERVER-INTERNAL"),
        ("DATA_LOSS", "NPS-SERVER-INTERNAL"),
        ("aborted", "NPS-CLIENT-CONFLICT"),
        ("CANCELLED", "NPS-SERVER-INTERNAL"),
        (None, "NPS-SERVER-INTERNAL"),
    ])
    def test_from_grpc_status(self, grpc, expected):
        assert BridgeErrorMap.from_grpc_status(grpc) == expected


class TestMustBeProtocolError:
    def test_the_infrastructure_set(self):
        for status in ("NPS-AUTH-UNAUTHENTICATED", "NPS-AUTH-FORBIDDEN",
                       "NPS-LIMIT-RATE", "NPS-LIMIT-BUDGET", "NPS-LIMIT-PAYLOAD",
                       "NPS-SERVER-UNSUPPORTED", "NPS-SERVER-INTERNAL",
                       "NPS-SERVER-UNAVAILABLE", "NPS-SERVER-TIMEOUT",
                       "NPS-DOWNSTREAM-UNAVAILABLE"):
            assert BridgeErrorMap.must_be_protocol_error(status), status

    def test_the_client_domain_classes_stay_is_error_content(self):
        for status in ("NPS-CLIENT-BAD-FRAME", "NPS-CLIENT-BAD-PARAM",
                       "NPS-CLIENT-NOT-FOUND", "NPS-CLIENT-CONFLICT",
                       "NPS-CLIENT-GONE", "NPS-CLIENT-UNPROCESSABLE", "NPS-OK", None):
            assert not BridgeErrorMap.must_be_protocol_error(status), status


class TestBridgeErrorCodeRegistration:
    def test_every_bridge_code_maps_to_its_normative_status(self):
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.DIRECTION_UNSUPPORTED] == "NPS-SERVER-UNSUPPORTED"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.TARGET_INVALID] == "NPS-CLIENT-UNPROCESSABLE"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.PROTOCOL_UNSUPPORTED] == "NPS-SERVER-UNSUPPORTED"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.ENDPOINT_INVALID] == "NPS-CLIENT-UNPROCESSABLE"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.UPSTREAM_FAILED] == "NPS-DOWNSTREAM-UNAVAILABLE"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.SERVER_TOOL_NOT_FOUND] == "NPS-CLIENT-NOT-FOUND"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.SERVER_DISPATCHER_MISSING] == "NPS-SERVER-INTERNAL"
        assert NWP_ERROR_TO_NPS_STATUS[
            BridgeErrorCodes.SERVER_DISPATCH_FAILED] == "NPS-SERVER-INTERNAL"

    def test_the_invented_statuses_removed_by_cr0010_are_not_reintroduced(self):
        forbidden = {"NPS-SERVER-NOT-IMPLEMENTED", "NPS-SERVER-ERROR",
                     "NPS-CLIENT-UNAUTHORIZED", "NPS-CLIENT-BAD-REQUEST",
                     "NPS-SERVER-UPSTREAM-FAILED"}
        assert not forbidden & set(NWP_ERROR_TO_NPS_STATUS.values())
