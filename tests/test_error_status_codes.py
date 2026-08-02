# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests — error/status code parity with spec/error-codes.md and spec/status-codes.md."""

from __future__ import annotations

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# core / status_codes
# ─────────────────────────────────────────────────────────────────────────────
from nps_sdk.core.status_codes import (
    NPS_OK,
    NPS_OK_ACCEPTED,
    NPS_OK_NO_CONTENT,
    NPS_CLIENT_BAD_FRAME,
    NPS_CLIENT_BAD_PARAM,
    NPS_CLIENT_NOT_FOUND,
    NPS_CLIENT_CONFLICT,
    NPS_CLIENT_GONE,
    NPS_CLIENT_UNPROCESSABLE,
    NPS_AUTH_UNAUTHENTICATED,
    NPS_AUTH_FORBIDDEN,
    NPS_LIMIT_RATE,
    NPS_LIMIT_BUDGET,
    NPS_LIMIT_PAYLOAD,
    NPS_SERVER_INTERNAL,
    NPS_SERVER_UNSUPPORTED,
    NPS_SERVER_UNAVAILABLE,
    NPS_SERVER_TIMEOUT,
    NPS_SERVER_ENCODING_UNSUPPORTED,
    NPS_DOWNSTREAM_UNAVAILABLE,
    NPS_STREAM_SEQ_GAP,
    NPS_STREAM_NOT_FOUND,
    NPS_STREAM_LIMIT,
    NPS_PROTO_VERSION_INCOMPATIBLE,
    NPS_PROTO_PREAMBLE_INVALID,
    NPS_STATUS_TO_HTTP,
    to_http_status,
)


class TestStatusCodeValues:
    """String values must match the spec exactly."""

    def test_nps_ok(self):
        assert NPS_OK == "NPS-OK"

    def test_nps_ok_accepted(self):
        assert NPS_OK_ACCEPTED == "NPS-OK-ACCEPTED"

    def test_nps_ok_no_content(self):
        assert NPS_OK_NO_CONTENT == "NPS-OK-NO-CONTENT"

    def test_client_bad_frame(self):
        assert NPS_CLIENT_BAD_FRAME == "NPS-CLIENT-BAD-FRAME"

    def test_client_bad_param(self):
        assert NPS_CLIENT_BAD_PARAM == "NPS-CLIENT-BAD-PARAM"

    def test_client_not_found(self):
        assert NPS_CLIENT_NOT_FOUND == "NPS-CLIENT-NOT-FOUND"

    def test_client_conflict(self):
        assert NPS_CLIENT_CONFLICT == "NPS-CLIENT-CONFLICT"

    def test_client_gone(self):
        assert NPS_CLIENT_GONE == "NPS-CLIENT-GONE"

    def test_client_unprocessable(self):
        assert NPS_CLIENT_UNPROCESSABLE == "NPS-CLIENT-UNPROCESSABLE"

    def test_auth_unauthenticated(self):
        assert NPS_AUTH_UNAUTHENTICATED == "NPS-AUTH-UNAUTHENTICATED"

    def test_auth_forbidden(self):
        assert NPS_AUTH_FORBIDDEN == "NPS-AUTH-FORBIDDEN"

    def test_limit_rate(self):
        assert NPS_LIMIT_RATE == "NPS-LIMIT-RATE"

    def test_limit_budget(self):
        assert NPS_LIMIT_BUDGET == "NPS-LIMIT-BUDGET"

    def test_limit_payload(self):
        assert NPS_LIMIT_PAYLOAD == "NPS-LIMIT-PAYLOAD"

    def test_server_internal(self):
        assert NPS_SERVER_INTERNAL == "NPS-SERVER-INTERNAL"

    def test_server_unsupported(self):
        assert NPS_SERVER_UNSUPPORTED == "NPS-SERVER-UNSUPPORTED"

    def test_server_unavailable(self):
        assert NPS_SERVER_UNAVAILABLE == "NPS-SERVER-UNAVAILABLE"

    def test_server_timeout(self):
        assert NPS_SERVER_TIMEOUT == "NPS-SERVER-TIMEOUT"

    def test_server_encoding_unsupported(self):
        assert NPS_SERVER_ENCODING_UNSUPPORTED == "NPS-SERVER-ENCODING-UNSUPPORTED"

    def test_downstream_unavailable(self):
        assert NPS_DOWNSTREAM_UNAVAILABLE == "NPS-DOWNSTREAM-UNAVAILABLE"

    def test_stream_seq_gap(self):
        assert NPS_STREAM_SEQ_GAP == "NPS-STREAM-SEQ-GAP"

    def test_stream_not_found(self):
        assert NPS_STREAM_NOT_FOUND == "NPS-STREAM-NOT-FOUND"

    def test_stream_limit(self):
        assert NPS_STREAM_LIMIT == "NPS-STREAM-LIMIT"

    def test_proto_version_incompatible(self):
        assert NPS_PROTO_VERSION_INCOMPATIBLE == "NPS-PROTO-VERSION-INCOMPATIBLE"

    def test_proto_preamble_invalid(self):
        assert NPS_PROTO_PREAMBLE_INVALID == "NPS-PROTO-PREAMBLE-INVALID"


class TestStatusCodeCount:
    """All 25 spec codes must be in NPS_STATUS_TO_HTTP."""

    _EXPECTED = {
        "NPS-OK", "NPS-OK-ACCEPTED", "NPS-OK-NO-CONTENT",
        "NPS-CLIENT-BAD-FRAME", "NPS-CLIENT-BAD-PARAM", "NPS-CLIENT-NOT-FOUND",
        "NPS-CLIENT-CONFLICT", "NPS-CLIENT-GONE", "NPS-CLIENT-UNPROCESSABLE",
        "NPS-AUTH-UNAUTHENTICATED", "NPS-AUTH-FORBIDDEN",
        "NPS-LIMIT-RATE", "NPS-LIMIT-BUDGET", "NPS-LIMIT-PAYLOAD",
        "NPS-SERVER-INTERNAL", "NPS-SERVER-UNSUPPORTED", "NPS-SERVER-UNAVAILABLE",
        "NPS-SERVER-TIMEOUT", "NPS-SERVER-ENCODING-UNSUPPORTED",
        "NPS-DOWNSTREAM-UNAVAILABLE",
        "NPS-STREAM-SEQ-GAP", "NPS-STREAM-NOT-FOUND", "NPS-STREAM-LIMIT",
        "NPS-PROTO-VERSION-INCOMPATIBLE", "NPS-PROTO-PREAMBLE-INVALID",
    }

    def test_all_spec_codes_in_http_map(self):
        missing = self._EXPECTED - set(NPS_STATUS_TO_HTTP.keys())
        assert not missing, f"Missing from NPS_STATUS_TO_HTTP: {missing}"

    def test_http_map_count(self):
        assert len(NPS_STATUS_TO_HTTP) == 25

    def test_http_values_are_ints(self):
        for code, http in NPS_STATUS_TO_HTTP.items():
            assert isinstance(http, int), f"{code} maps to non-int {http!r}"

    def test_to_http_status_known(self):
        assert to_http_status("NPS-OK") == 200
        assert to_http_status("NPS-AUTH-UNAUTHENTICATED") == 401
        assert to_http_status("NPS-AUTH-FORBIDDEN") == 403
        assert to_http_status("NPS-CLIENT-NOT-FOUND") == 404
        assert to_http_status("NPS-SERVER-INTERNAL") == 500
        assert to_http_status("NPS-LIMIT-PAYLOAD") == 413
        assert to_http_status("NPS-PROTO-VERSION-INCOMPATIBLE") == 426

    def test_to_http_status_unknown_returns_500(self):
        assert to_http_status("NPS-UNKNOWN-CODE") == 500


# ─────────────────────────────────────────────────────────────────────────────
# NCP error codes
# ─────────────────────────────────────────────────────────────────────────────
from nps_sdk.ncp.error_codes import (
    NCP_ANCHOR_NOT_FOUND,
    NCP_ANCHOR_SCHEMA_INVALID,
    NCP_ANCHOR_ID_MISMATCH,
    NCP_ANCHOR_STALE,
    NCP_FRAME_UNKNOWN_TYPE,
    NCP_FRAME_PAYLOAD_TOO_LARGE,
    NCP_FRAME_FLAGS_INVALID,
    NCP_STREAM_SEQ_GAP,
    NCP_STREAM_NOT_FOUND,
    NCP_STREAM_LIMIT_EXCEEDED,
    NCP_STREAM_WINDOW_OVERFLOW,
    NCP_ENCODING_UNSUPPORTED,
    NCP_DIFF_FORMAT_UNSUPPORTED,
    NCP_ENC_NOT_NEGOTIATED,
    NCP_ENC_AUTH_FAILED,
    NCP_VERSION_INCOMPATIBLE,
    NCP_PREAMBLE_INVALID,
    NCP_NID_MISMATCH,
    NCP_ERROR_TO_NPS_STATUS,
)


class TestNcpErrorCodeValues:
    def test_anchor_not_found(self):
        assert NCP_ANCHOR_NOT_FOUND == "NCP-ANCHOR-NOT-FOUND"

    def test_anchor_schema_invalid(self):
        assert NCP_ANCHOR_SCHEMA_INVALID == "NCP-ANCHOR-SCHEMA-INVALID"

    def test_anchor_id_mismatch(self):
        assert NCP_ANCHOR_ID_MISMATCH == "NCP-ANCHOR-ID-MISMATCH"

    def test_anchor_stale(self):
        assert NCP_ANCHOR_STALE == "NCP-ANCHOR-STALE"

    def test_frame_unknown_type(self):
        assert NCP_FRAME_UNKNOWN_TYPE == "NCP-FRAME-UNKNOWN-TYPE"

    def test_frame_payload_too_large(self):
        assert NCP_FRAME_PAYLOAD_TOO_LARGE == "NCP-FRAME-PAYLOAD-TOO-LARGE"

    def test_frame_flags_invalid(self):
        assert NCP_FRAME_FLAGS_INVALID == "NCP-FRAME-FLAGS-INVALID"

    def test_stream_seq_gap(self):
        assert NCP_STREAM_SEQ_GAP == "NCP-STREAM-SEQ-GAP"

    def test_stream_not_found(self):
        assert NCP_STREAM_NOT_FOUND == "NCP-STREAM-NOT-FOUND"

    def test_stream_limit_exceeded(self):
        assert NCP_STREAM_LIMIT_EXCEEDED == "NCP-STREAM-LIMIT-EXCEEDED"

    def test_stream_window_overflow(self):
        assert NCP_STREAM_WINDOW_OVERFLOW == "NCP-STREAM-WINDOW-OVERFLOW"

    def test_encoding_unsupported(self):
        assert NCP_ENCODING_UNSUPPORTED == "NCP-ENCODING-UNSUPPORTED"

    def test_diff_format_unsupported(self):
        assert NCP_DIFF_FORMAT_UNSUPPORTED == "NCP-DIFF-FORMAT-UNSUPPORTED"

    def test_enc_not_negotiated(self):
        assert NCP_ENC_NOT_NEGOTIATED == "NCP-ENC-NOT-NEGOTIATED"

    def test_enc_auth_failed(self):
        assert NCP_ENC_AUTH_FAILED == "NCP-ENC-AUTH-FAILED"

    def test_version_incompatible(self):
        assert NCP_VERSION_INCOMPATIBLE == "NCP-VERSION-INCOMPATIBLE"

    def test_preamble_invalid(self):
        assert NCP_PREAMBLE_INVALID == "NCP-PREAMBLE-INVALID"

    def test_nid_mismatch(self):
        # RFC-0006 §6.3–§6.4; NPS-CR-0009 §3.3 reuses it as the failover trigger.
        assert NCP_NID_MISMATCH == "NCP-NID-MISMATCH"


class TestNcpMappingDict:
    _EXPECTED_CODES = {
        "NCP-ANCHOR-NOT-FOUND", "NCP-ANCHOR-SCHEMA-INVALID", "NCP-ANCHOR-ID-MISMATCH",
        "NCP-ANCHOR-STALE", "NCP-FRAME-UNKNOWN-TYPE", "NCP-FRAME-PAYLOAD-TOO-LARGE",
        "NCP-FRAME-FLAGS-INVALID", "NCP-STREAM-SEQ-GAP", "NCP-STREAM-NOT-FOUND",
        "NCP-STREAM-LIMIT-EXCEEDED", "NCP-STREAM-WINDOW-OVERFLOW",
        "NCP-ENCODING-UNSUPPORTED", "NCP-DIFF-FORMAT-UNSUPPORTED",
        "NCP-ENC-NOT-NEGOTIATED", "NCP-ENC-AUTH-FAILED",
        "NCP-VERSION-INCOMPATIBLE", "NCP-PREAMBLE-INVALID",
        "NCP-NID-MISMATCH",
    }

    def test_all_codes_covered(self):
        missing = self._EXPECTED_CODES - set(NCP_ERROR_TO_NPS_STATUS.keys())
        assert not missing, f"NCP_ERROR_TO_NPS_STATUS missing: {missing}"

    def test_mapping_count(self):
        # 17 through alpha.16; +1 for NCP-NID-MISMATCH, which spec/error-codes.md has
        # carried since RFC-0006 but the Python SDK had never declared. NPS-CR-0009's
        # failover connector matches on it, so it is now a first-class constant.
        assert len(NCP_ERROR_TO_NPS_STATUS) == 18

    def test_spot_checks(self):
        assert NCP_ERROR_TO_NPS_STATUS[NCP_ANCHOR_NOT_FOUND] == "NPS-CLIENT-NOT-FOUND"
        assert NCP_ERROR_TO_NPS_STATUS[NCP_FRAME_PAYLOAD_TOO_LARGE] == "NPS-LIMIT-PAYLOAD"
        assert NCP_ERROR_TO_NPS_STATUS[NCP_VERSION_INCOMPATIBLE] == "NPS-PROTO-VERSION-INCOMPATIBLE"
        assert NCP_ERROR_TO_NPS_STATUS[NCP_PREAMBLE_INVALID] == "NPS-PROTO-PREAMBLE-INVALID"
        assert NCP_ERROR_TO_NPS_STATUS[NCP_NID_MISMATCH] == "NPS-AUTH-UNAUTHENTICATED"


# ─────────────────────────────────────────────────────────────────────────────
# NWP error codes
# ─────────────────────────────────────────────────────────────────────────────
from nps_sdk.nwp.error_codes import (
    AUTH_NID_SCOPE_VIOLATION,
    AUTH_NID_EXPIRED,
    AUTH_NID_REVOKED,
    AUTH_NID_UNTRUSTED_ISSUER,
    AUTH_NID_CAPABILITY_MISSING,
    AUTH_ASSURANCE_TOO_LOW,
    AUTH_REPUTATION_BLOCKED,
    REPUTATION_THROTTLED,
    REPUTATION_REJECTED,
    REPUTATION_BANNED,
    QUERY_FILTER_INVALID,
    QUERY_FIELD_UNKNOWN,
    QUERY_CURSOR_INVALID,
    QUERY_REGEX_UNSAFE,
    QUERY_VECTOR_UNSUPPORTED,
    QUERY_AGGREGATE_UNSUPPORTED,
    QUERY_AGGREGATE_INVALID,
    QUERY_STREAM_UNSUPPORTED,
    ACTION_NOT_FOUND,
    ACTION_PARAMS_INVALID,
    ACTION_IDEMPOTENCY_CONFLICT,
    TASK_NOT_FOUND,
    TASK_ALREADY_CANCELLED,
    TASK_ALREADY_COMPLETED,
    TASK_ALREADY_FAILED,
    SUBSCRIBE_STREAM_NOT_FOUND,
    SUBSCRIBE_LIMIT_EXCEEDED,
    SUBSCRIBE_FILTER_UNSUPPORTED,
    SUBSCRIBE_INTERRUPTED,
    SUBSCRIBE_SEQ_TOO_OLD,
    BUDGET_EXCEEDED,
    CGN_LIMIT_EXCEEDED,
    DEPTH_EXCEEDED,
    GRAPH_CYCLE,
    NODE_UNAVAILABLE,
    MANIFEST_VERSION_UNSUPPORTED,
    MANIFEST_NODE_TYPE_REMOVED,
    MANIFEST_NODE_TYPE_UNKNOWN,
    TOPOLOGY_UNAUTHORIZED,
    TOPOLOGY_UNSUPPORTED_SCOPE,
    TOPOLOGY_DEPTH_UNSUPPORTED,
    TOPOLOGY_FILTER_UNSUPPORTED,
    RESERVED_TYPE_UNSUPPORTED,
    RATE_LIMIT_EXCEEDED,
    HTTP_ORIGIN_FORBIDDEN,
    HTTP_CONTENT_TYPE_UNSUPPORTED,
    HTTP_ACCEPT_UNSATISFIABLE,
    HTTP_REQUEST_ID_MISMATCH,
    HTTP_FRAME_BODY_MALFORMED,
    CAPABILITY_ADVERTISED_UNIMPLEMENTED,
    NWP_ERROR_TO_NPS_STATUS,
)


class TestNwpNewRepCodes:
    """The three previously-missing reputation codes."""

    def test_reputation_throttled(self):
        assert REPUTATION_THROTTLED == "NWP-REPUTATION-THROTTLED"

    def test_reputation_rejected(self):
        assert REPUTATION_REJECTED == "NWP-REPUTATION-REJECTED"

    def test_reputation_banned(self):
        assert REPUTATION_BANNED == "NWP-REPUTATION-BANNED"


class TestNwpMappingDict:
    _EXPECTED_CODES = {
        "NWP-AUTH-NID-SCOPE-VIOLATION", "NWP-AUTH-NID-EXPIRED", "NWP-AUTH-NID-REVOKED",
        "NWP-AUTH-NID-UNTRUSTED-ISSUER", "NWP-AUTH-NID-CAPABILITY-MISSING",
        "NWP-AUTH-ASSURANCE-TOO-LOW", "NWP-AUTH-REPUTATION-BLOCKED",
        "NWP-REPUTATION-THROTTLED", "NWP-REPUTATION-REJECTED", "NWP-REPUTATION-BANNED",
        "NWP-QUERY-FILTER-INVALID", "NWP-QUERY-FIELD-UNKNOWN", "NWP-QUERY-CURSOR-INVALID",
        "NWP-QUERY-REGEX-UNSAFE", "NWP-QUERY-VECTOR-UNSUPPORTED",
        "NWP-QUERY-AGGREGATE-UNSUPPORTED", "NWP-QUERY-AGGREGATE-INVALID",
        "NWP-QUERY-STREAM-UNSUPPORTED",
        "NWP-ACTION-NOT-FOUND", "NWP-ACTION-PARAMS-INVALID", "NWP-ACTION-IDEMPOTENCY-CONFLICT",
        "NWP-TASK-NOT-FOUND", "NWP-TASK-ALREADY-CANCELLED",
        "NWP-TASK-ALREADY-COMPLETED", "NWP-TASK-ALREADY-FAILED",
        "NWP-SUBSCRIBE-STREAM-NOT-FOUND", "NWP-SUBSCRIBE-LIMIT-EXCEEDED",
        "NWP-SUBSCRIBE-FILTER-UNSUPPORTED", "NWP-SUBSCRIBE-INTERRUPTED",
        "NWP-SUBSCRIBE-SEQ-TOO-OLD",
        "NWP-BUDGET-EXCEEDED", "NWP-CGN-LIMIT-EXCEEDED", "NWP-DEPTH-EXCEEDED",
        "NWP-GRAPH-CYCLE", "NWP-NODE-UNAVAILABLE", "NWP-RATE-LIMIT-EXCEEDED",
        "NWP-MANIFEST-VERSION-UNSUPPORTED", "NWP-MANIFEST-NODE-TYPE-REMOVED",
        "NWP-MANIFEST-NODE-TYPE-UNKNOWN",
        "NWP-HTTP-ORIGIN-FORBIDDEN", "NWP-HTTP-CONTENT-TYPE-UNSUPPORTED",
        "NWP-HTTP-ACCEPT-UNSATISFIABLE", "NWP-HTTP-REQUEST-ID-MISMATCH",
        "NWP-HTTP-FRAME-BODY-MALFORMED", "NWP-CAPABILITY-ADVERTISED-UNIMPLEMENTED",
        "NWP-TOPOLOGY-UNAUTHORIZED", "NWP-TOPOLOGY-UNSUPPORTED-SCOPE",
        "NWP-TOPOLOGY-DEPTH-UNSUPPORTED", "NWP-TOPOLOGY-FILTER-UNSUPPORTED",
        "NWP-RESERVED-TYPE-UNSUPPORTED",
        # NPS-CR-0009 multi-Anchor HA
        "NWP-ANCHOR-NOT-LEADER", "NWP-ANCHOR-EPOCH-FENCED",
        # NPS-CR-0001 outbound / NPS-CR-0010 inbound Bridge
        "NWP-BRIDGE-DIRECTION-UNSUPPORTED", "NWP-BRIDGE-TARGET-INVALID",
        "NWP-BRIDGE-PROTOCOL-UNSUPPORTED", "NWP-BRIDGE-ENDPOINT-INVALID",
        "NWP-BRIDGE-UPSTREAM-FAILED", "NWP-BRIDGE-SERVER-TOOL-NOT-FOUND",
        "NWP-BRIDGE-SERVER-DISPATCHER-MISSING", "NWP-BRIDGE-SERVER-DISPATCH-FAILED",
    }

    def test_all_codes_covered(self):
        missing = self._EXPECTED_CODES - set(NWP_ERROR_TO_NPS_STATUS.keys())
        assert not missing, f"NWP_ERROR_TO_NPS_STATUS missing: {missing}"

    def test_reputation_mappings(self):
        assert NWP_ERROR_TO_NPS_STATUS[REPUTATION_REJECTED] == "NPS-AUTH-FORBIDDEN"
        assert NWP_ERROR_TO_NPS_STATUS[REPUTATION_BANNED] == "NPS-AUTH-FORBIDDEN"
        # REPUTATION_THROTTLED maps to a rate-limited variant
        assert "RATE" in NWP_ERROR_TO_NPS_STATUS[REPUTATION_THROTTLED]

    def test_spot_checks(self):
        assert NWP_ERROR_TO_NPS_STATUS[AUTH_NID_EXPIRED] == "NPS-AUTH-UNAUTHENTICATED"
        assert NWP_ERROR_TO_NPS_STATUS[BUDGET_EXCEEDED] == "NPS-LIMIT-BUDGET"
        assert NWP_ERROR_TO_NPS_STATUS[NODE_UNAVAILABLE] == "NPS-SERVER-UNAVAILABLE"
        assert NWP_ERROR_TO_NPS_STATUS[HTTP_ORIGIN_FORBIDDEN] == "NPS-AUTH-FORBIDDEN"
        assert NWP_ERROR_TO_NPS_STATUS[HTTP_CONTENT_TYPE_UNSUPPORTED] == "NPS-CLIENT-BAD-FRAME"
        assert NWP_ERROR_TO_NPS_STATUS[HTTP_ACCEPT_UNSATISFIABLE] == "NPS-CLIENT-BAD-PARAM"
        assert NWP_ERROR_TO_NPS_STATUS[HTTP_REQUEST_ID_MISMATCH] == "NPS-CLIENT-BAD-PARAM"
        assert NWP_ERROR_TO_NPS_STATUS[HTTP_FRAME_BODY_MALFORMED] == "NPS-CLIENT-BAD-FRAME"
        assert NWP_ERROR_TO_NPS_STATUS[CAPABILITY_ADVERTISED_UNIMPLEMENTED] == "NPS-SERVER-UNSUPPORTED"


# ─────────────────────────────────────────────────────────────────────────────
# NIP error codes
# ─────────────────────────────────────────────────────────────────────────────
from nps_sdk.nip.error_codes import (
    CERT_EXPIRED,
    CERT_REVOKED,
    CERT_SIGNATURE_INVALID,
    CERT_UNTRUSTED_ISSUER,
    CERT_CAPABILITY_MISSING,
    CERT_SCOPE_VIOLATION,
    CERT_FORMAT_INVALID,
    CERT_EKU_MISSING,
    CERT_SUBJECT_NID_MISMATCH,
    CERT_PARENT_REVOKED,
    CERT_NODE_ROLES_MISMATCH,
    CERT_CAPABILITIES_EXCEEDED,
    CA_NID_NOT_FOUND,
    CA_NID_ALREADY_EXISTS,
    CA_SERIAL_DUPLICATE,
    CA_RENEWAL_TOO_EARLY,
    CA_SCOPE_EXPANSION_DENIED,
    CA_GROUP_REVOKED,
    CA_PARENT_NOT_FOUND,
    CA_PARENT_NOT_GROUP,
    CA_SESSION_VALIDITY_INVALID,
    CA_JWS_INVALID,
    CA_JWS_EXPIRED,
    OCSP_UNAVAILABLE,
    OCSP_STAPLE_EXPIRED,
    TRUST_FRAME_INVALID,
    TRUST_FRAME_EXPIRED,
    TRUST_FRAME_GRANTOR_REVOKED,
    TRUST_FRAME_SCOPE_EXCEEDS_GRANTOR,
    TRUST_FRAME_NODES_PATTERN_INVALID,
    REVOKE_FRAME_INVALID,
    REVOKE_FRAME_UNAUTHORIZED_ISSUER,
    REVOKE_FRAME_SERIAL_MISMATCH,
    REVOKE_FRAME_REASON_UNKNOWN,
    ASSURANCE_MISMATCH,
    ASSURANCE_UNKNOWN,
    REPUTATION_ENTRY_INVALID,
    REPUTATION_LOG_UNREACHABLE,
    REPUTATION_GOSSIP_FORK,
    REPUTATION_GOSSIP_SIG_INVALID,
    ACME_CHALLENGE_FAILED,
    NIP_ERROR_TO_NPS_STATUS,
)


class TestNipNewCodes:
    """All codes that were missing from the previous implementation."""

    def test_cert_parent_revoked(self):
        assert CERT_PARENT_REVOKED == "NIP-CERT-PARENT-REVOKED"

    def test_cert_node_roles_mismatch(self):
        assert CERT_NODE_ROLES_MISMATCH == "NIP-CERT-NODE-ROLES-MISMATCH"

    def test_cert_capabilities_exceeded(self):
        assert CERT_CAPABILITIES_EXCEEDED == "NIP-CERT-CAPABILITIES-EXCEEDED"

    def test_ocsp_staple_expired(self):
        assert OCSP_STAPLE_EXPIRED == "NIP-OCSP-STAPLE-EXPIRED"

    def test_trust_frame_expired(self):
        assert TRUST_FRAME_EXPIRED == "NIP-TRUST-FRAME-EXPIRED"

    def test_trust_frame_grantor_revoked(self):
        assert TRUST_FRAME_GRANTOR_REVOKED == "NIP-TRUST-FRAME-GRANTOR-REVOKED"

    def test_trust_frame_scope_exceeds_grantor(self):
        assert TRUST_FRAME_SCOPE_EXCEEDS_GRANTOR == "NIP-TRUST-FRAME-SCOPE-EXCEEDS-GRANTOR"

    def test_trust_frame_nodes_pattern_invalid(self):
        assert TRUST_FRAME_NODES_PATTERN_INVALID == "NIP-TRUST-FRAME-NODES-PATTERN-INVALID"

    def test_revoke_frame_invalid(self):
        assert REVOKE_FRAME_INVALID == "NIP-REVOKE-FRAME-INVALID"

    def test_revoke_frame_unauthorized_issuer(self):
        assert REVOKE_FRAME_UNAUTHORIZED_ISSUER == "NIP-REVOKE-FRAME-UNAUTHORIZED-ISSUER"

    def test_revoke_frame_serial_mismatch(self):
        assert REVOKE_FRAME_SERIAL_MISMATCH == "NIP-REVOKE-FRAME-SERIAL-MISMATCH"

    def test_revoke_frame_reason_unknown(self):
        assert REVOKE_FRAME_REASON_UNKNOWN == "NIP-REVOKE-FRAME-REASON-UNKNOWN"

    def test_ca_group_revoked(self):
        assert CA_GROUP_REVOKED == "NIP-CA-GROUP-REVOKED"

    def test_ca_parent_not_found(self):
        assert CA_PARENT_NOT_FOUND == "NIP-CA-PARENT-NOT-FOUND"

    def test_ca_parent_not_group(self):
        assert CA_PARENT_NOT_GROUP == "NIP-CA-PARENT-NOT-GROUP"

    def test_ca_session_validity_invalid(self):
        assert CA_SESSION_VALIDITY_INVALID == "NIP-CA-SESSION-VALIDITY-INVALID"

    def test_ca_jws_invalid(self):
        assert CA_JWS_INVALID == "NIP-CA-JWS-INVALID"

    def test_ca_jws_expired(self):
        assert CA_JWS_EXPIRED == "NIP-CA-JWS-EXPIRED"


class TestNipMappingDict:
    _EXPECTED_CODES = {
        "NIP-CERT-EXPIRED", "NIP-CERT-REVOKED", "NIP-CERT-SIGNATURE-INVALID",
        "NIP-CERT-UNTRUSTED-ISSUER", "NIP-CERT-CAPABILITY-MISSING",
        "NIP-CERT-SCOPE-VIOLATION", "NIP-CERT-FORMAT-INVALID", "NIP-CERT-EKU-MISSING",
        "NIP-CERT-SUBJECT-NID-MISMATCH", "NIP-CERT-PARENT-REVOKED",
        "NIP-CA-NID-NOT-FOUND", "NIP-CA-NID-ALREADY-EXISTS", "NIP-CA-SERIAL-DUPLICATE",
        "NIP-CA-RENEWAL-TOO-EARLY", "NIP-CA-SCOPE-EXPANSION-DENIED",
        "NIP-CA-GROUP-REVOKED", "NIP-CA-PARENT-NOT-FOUND", "NIP-CA-PARENT-NOT-GROUP",
        "NIP-CA-SESSION-VALIDITY-INVALID", "NIP-CA-JWS-INVALID", "NIP-CA-JWS-EXPIRED",
        "NIP-OCSP-UNAVAILABLE", "NIP-OCSP-STAPLE-EXPIRED",
        "NIP-TRUST-FRAME-INVALID", "NIP-TRUST-FRAME-EXPIRED",
        "NIP-TRUST-FRAME-GRANTOR-REVOKED", "NIP-TRUST-FRAME-SCOPE-EXCEEDS-GRANTOR",
        "NIP-TRUST-FRAME-NODES-PATTERN-INVALID",
        "NIP-REVOKE-FRAME-INVALID", "NIP-REVOKE-FRAME-UNAUTHORIZED-ISSUER",
        "NIP-REVOKE-FRAME-SERIAL-MISMATCH", "NIP-REVOKE-FRAME-REASON-UNKNOWN",
        "NIP-ASSURANCE-MISMATCH", "NIP-ASSURANCE-UNKNOWN",
        "NIP-REPUTATION-ENTRY-INVALID", "NIP-REPUTATION-LOG-UNREACHABLE",
        "NIP-REPUTATION-GOSSIP-FORK", "NIP-REPUTATION-GOSSIP-SIG-INVALID",
        "NIP-ACME-CHALLENGE-FAILED",
        # NIP v0.12 §7.5 Phase-3 enforcement
        "NIP-CERT-NODE-ROLES-MISMATCH", "NIP-CERT-CAPABILITIES-EXCEEDED",
    }

    def test_all_codes_covered(self):
        missing = self._EXPECTED_CODES - set(NIP_ERROR_TO_NPS_STATUS.keys())
        assert not missing, f"NIP_ERROR_TO_NPS_STATUS missing: {missing}"

    def test_ocsp_staple_expired_maps_to_unauthenticated(self):
        assert NIP_ERROR_TO_NPS_STATUS[OCSP_STAPLE_EXPIRED] == "NPS-AUTH-UNAUTHENTICATED"

    def test_spot_checks(self):
        assert NIP_ERROR_TO_NPS_STATUS[CERT_EXPIRED] == "NPS-AUTH-UNAUTHENTICATED"
        assert NIP_ERROR_TO_NPS_STATUS[CERT_SCOPE_VIOLATION] == "NPS-AUTH-FORBIDDEN"
        assert NIP_ERROR_TO_NPS_STATUS[CA_GROUP_REVOKED] == "NPS-AUTH-FORBIDDEN"
        assert NIP_ERROR_TO_NPS_STATUS[CA_JWS_INVALID] == "NPS-AUTH-UNAUTHENTICATED"
        assert NIP_ERROR_TO_NPS_STATUS[TRUST_FRAME_SCOPE_EXCEEDS_GRANTOR] == "NPS-AUTH-FORBIDDEN"
        assert NIP_ERROR_TO_NPS_STATUS[REPUTATION_LOG_UNREACHABLE] == "NPS-DOWNSTREAM-UNAVAILABLE"


# ─────────────────────────────────────────────────────────────────────────────
# NDP error codes
# ─────────────────────────────────────────────────────────────────────────────
from nps_sdk.ndp.error_codes import (
    NDP_RESOLVE_NOT_FOUND,
    NDP_RESOLVE_AMBIGUOUS,
    NDP_RESOLVE_TIMEOUT,
    NDP_RESOLVE_STALE,
    NDP_ANNOUNCE_SIGNATURE_INVALID,
    NDP_ANNOUNCE_NID_MISMATCH,
    NDP_ANNOUNCE_ROLE_REMOVED,
    NDP_ANNOUNCE_ROLE_UNKNOWN,
    NDP_ANNOUNCE_CONFLICT,
    NDP_ANNOUNCE_PROFILE_VIOLATION,
    NDP_ANNOUNCE_STALE,
    NDP_GRAPH_SEQ_ROLLBACK,
    NDP_GRAPH_SEQ_GAP,
    NDP_GRAPH_INVALID,
    NDP_GRAPH_TOO_LARGE,
    NDP_FEDERATION_LOOP,
    NDP_CLUSTER_SPLIT,
    NDP_ISSUER_NOT_ALLOWED,
    NDP_CA_ATTEST_REQUIRED,
    NDP_REGISTRY_UNAVAILABLE,
    NDP_ERROR_TO_NPS_STATUS,
)


class TestNdpErrorCodeValues:
    def test_resolve_not_found(self):
        assert NDP_RESOLVE_NOT_FOUND == "NDP-RESOLVE-NOT-FOUND"

    def test_resolve_ambiguous(self):
        assert NDP_RESOLVE_AMBIGUOUS == "NDP-RESOLVE-AMBIGUOUS"

    def test_resolve_timeout(self):
        assert NDP_RESOLVE_TIMEOUT == "NDP-RESOLVE-TIMEOUT"

    def test_resolve_stale(self):
        assert NDP_RESOLVE_STALE == "NDP-RESOLVE-STALE"

    def test_announce_signature_invalid(self):
        assert NDP_ANNOUNCE_SIGNATURE_INVALID == "NDP-ANNOUNCE-SIGNATURE-INVALID"

    def test_announce_nid_mismatch(self):
        assert NDP_ANNOUNCE_NID_MISMATCH == "NDP-ANNOUNCE-NID-MISMATCH"

    def test_announce_role_removed(self):
        assert NDP_ANNOUNCE_ROLE_REMOVED == "NDP-ANNOUNCE-ROLE-REMOVED"

    def test_announce_role_unknown(self):
        assert NDP_ANNOUNCE_ROLE_UNKNOWN == "NDP-ANNOUNCE-ROLE-UNKNOWN"

    def test_announce_conflict(self):
        assert NDP_ANNOUNCE_CONFLICT == "NDP-ANNOUNCE-CONFLICT"

    def test_announce_profile_violation(self):
        assert NDP_ANNOUNCE_PROFILE_VIOLATION == "NDP-ANNOUNCE-PROFILE-VIOLATION"

    def test_announce_stale(self):
        assert NDP_ANNOUNCE_STALE == "NDP-ANNOUNCE-STALE"

    def test_graph_seq_rollback(self):
        assert NDP_GRAPH_SEQ_ROLLBACK == "NDP-GRAPH-SEQ-ROLLBACK"

    def test_graph_seq_gap(self):
        assert NDP_GRAPH_SEQ_GAP == "NDP-GRAPH-SEQ-GAP"

    def test_graph_invalid(self):
        assert NDP_GRAPH_INVALID == "NDP-GRAPH-INVALID"

    def test_graph_too_large(self):
        assert NDP_GRAPH_TOO_LARGE == "NDP-GRAPH-TOO-LARGE"

    def test_federation_loop(self):
        assert NDP_FEDERATION_LOOP == "NDP-FEDERATION-LOOP"

    def test_cluster_split(self):
        assert NDP_CLUSTER_SPLIT == "NDP-CLUSTER-SPLIT"

    def test_issuer_not_allowed(self):
        assert NDP_ISSUER_NOT_ALLOWED == "NDP-ISSUER-NOT-ALLOWED"

    def test_ca_attest_required(self):
        assert NDP_CA_ATTEST_REQUIRED == "NDP-CA-ATTEST-REQUIRED"

    def test_registry_unavailable(self):
        assert NDP_REGISTRY_UNAVAILABLE == "NDP-REGISTRY-UNAVAILABLE"


class TestNdpMappingDict:
    _EXPECTED_CODES = {
        "NDP-RESOLVE-NOT-FOUND", "NDP-RESOLVE-AMBIGUOUS", "NDP-RESOLVE-TIMEOUT",
        "NDP-RESOLVE-STALE",
        "NDP-ANNOUNCE-SIGNATURE-INVALID", "NDP-ANNOUNCE-NID-MISMATCH",
        "NDP-ANNOUNCE-ROLE-REMOVED", "NDP-ANNOUNCE-ROLE-UNKNOWN", "NDP-ANNOUNCE-CONFLICT",
        "NDP-ANNOUNCE-PROFILE-VIOLATION", "NDP-ANNOUNCE-STALE",
        "NDP-GRAPH-SEQ-ROLLBACK", "NDP-GRAPH-SEQ-GAP",
        "NDP-GRAPH-INVALID", "NDP-GRAPH-TOO-LARGE", "NDP-FEDERATION-LOOP",
        "NDP-ISSUER-NOT-ALLOWED", "NDP-CA-ATTEST-REQUIRED", "NDP-REGISTRY-UNAVAILABLE",
        # NPS-CR-0009 multi-Anchor HA
        "NDP-CLUSTER-SPLIT",
    }

    def test_all_codes_covered(self):
        missing = self._EXPECTED_CODES - set(NDP_ERROR_TO_NPS_STATUS.keys())
        assert not missing, f"NDP_ERROR_TO_NPS_STATUS missing: {missing}"

    def test_alpha11_mappings(self):
        assert NDP_ERROR_TO_NPS_STATUS[NDP_GRAPH_INVALID] == "NPS-CLIENT-BAD-FRAME"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_GRAPH_TOO_LARGE] == "NPS-LIMIT-PAYLOAD"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_FEDERATION_LOOP] == "NPS-CLIENT-CONFLICT"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_RESOLVE_STALE] == "NPS-CLIENT-NOT-FOUND"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_ANNOUNCE_STALE] == "NPS-CLIENT-NOT-FOUND"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_ANNOUNCE_PROFILE_VIOLATION] == "NPS-AUTH-FORBIDDEN"

    def test_spot_checks(self):
        assert NDP_ERROR_TO_NPS_STATUS[NDP_RESOLVE_NOT_FOUND] == "NPS-CLIENT-NOT-FOUND"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_RESOLVE_TIMEOUT] == "NPS-SERVER-TIMEOUT"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_ANNOUNCE_SIGNATURE_INVALID] == "NPS-AUTH-UNAUTHENTICATED"
        assert NDP_ERROR_TO_NPS_STATUS[NDP_CA_ATTEST_REQUIRED] == "NPS-AUTH-UNAUTHENTICATED"


# ─────────────────────────────────────────────────────────────────────────────
# NOP error codes
# ─────────────────────────────────────────────────────────────────────────────
from nps_sdk.nop.error_codes import (
    NOP_TASK_NOT_FOUND,
    NOP_TASK_TIMEOUT,
    NOP_TASK_DAG_INVALID,
    NOP_TASK_DAG_CYCLE,
    NOP_TASK_DAG_TOO_LARGE,
    NOP_TASK_ALREADY_COMPLETED,
    NOP_TASK_CANCELLED,
    NOP_DELEGATE_SCOPE_VIOLATION,
    NOP_DELEGATE_REJECTED,
    NOP_DELEGATE_CHAIN_TOO_DEEP,
    NOP_DELEGATE_TIMEOUT,
    NOP_SYNC_TIMEOUT,
    NOP_SYNC_DEPENDENCY_FAILED,
    NOP_STREAM_SEQ_GAP,
    NOP_STREAM_NID_MISMATCH,
    NOP_STREAM_NAK,
    NOP_RESOURCE_INSUFFICIENT,
    NOP_CONDITION_EVAL_ERROR,
    NOP_INPUT_MAPPING_ERROR,
    NOP_COMPENSATION_FAILED,
    NOP_COMPENSATION_PARTIAL_FAILED,
    NOP_COMPENSATION_NOT_SUPPORTED,
    NOP_CALLBACK_HMAC_MISSING,
    NOP_CALLBACK_INVALID,
    NOP_CALLBACK_HMAC_INVALID,
    NOP_CLAIM_CONFLICT,
    NOP_SPAWN_SPEC_INVALID,
    NOP_RUNTIME_IDLE_TIMEOUT,
    NOP_RUNTIME_MAX_RUNTIME,
    NOP_TASK_RESULT_EXPIRED,
    NOP_STREAM_NAK_UNRESOLVABLE,
    NOP_ERROR_TO_NPS_STATUS,
)


class TestNopErrorCodeValues:
    def test_task_not_found(self):
        assert NOP_TASK_NOT_FOUND == "NOP-TASK-NOT-FOUND"

    def test_task_timeout(self):
        assert NOP_TASK_TIMEOUT == "NOP-TASK-TIMEOUT"

    def test_task_dag_invalid(self):
        assert NOP_TASK_DAG_INVALID == "NOP-TASK-DAG-INVALID"

    def test_task_dag_cycle(self):
        assert NOP_TASK_DAG_CYCLE == "NOP-TASK-DAG-CYCLE"

    def test_task_dag_too_large(self):
        assert NOP_TASK_DAG_TOO_LARGE == "NOP-TASK-DAG-TOO-LARGE"

    def test_task_already_completed(self):
        assert NOP_TASK_ALREADY_COMPLETED == "NOP-TASK-ALREADY-COMPLETED"

    def test_task_cancelled(self):
        assert NOP_TASK_CANCELLED == "NOP-TASK-CANCELLED"

    def test_delegate_scope_violation(self):
        assert NOP_DELEGATE_SCOPE_VIOLATION == "NOP-DELEGATE-SCOPE-VIOLATION"

    def test_delegate_rejected(self):
        assert NOP_DELEGATE_REJECTED == "NOP-DELEGATE-REJECTED"

    def test_delegate_chain_too_deep(self):
        assert NOP_DELEGATE_CHAIN_TOO_DEEP == "NOP-DELEGATE-CHAIN-TOO-DEEP"

    def test_delegate_timeout(self):
        assert NOP_DELEGATE_TIMEOUT == "NOP-DELEGATE-TIMEOUT"

    def test_sync_timeout(self):
        assert NOP_SYNC_TIMEOUT == "NOP-SYNC-TIMEOUT"

    def test_sync_dependency_failed(self):
        assert NOP_SYNC_DEPENDENCY_FAILED == "NOP-SYNC-DEPENDENCY-FAILED"

    def test_stream_seq_gap(self):
        assert NOP_STREAM_SEQ_GAP == "NOP-STREAM-SEQ-GAP"

    def test_stream_nid_mismatch(self):
        assert NOP_STREAM_NID_MISMATCH == "NOP-STREAM-NID-MISMATCH"

    def test_stream_nak(self):
        assert NOP_STREAM_NAK == "NOP-STREAM-NAK"

    def test_resource_insufficient(self):
        assert NOP_RESOURCE_INSUFFICIENT == "NOP-RESOURCE-INSUFFICIENT"

    def test_condition_eval_error(self):
        assert NOP_CONDITION_EVAL_ERROR == "NOP-CONDITION-EVAL-ERROR"

    def test_input_mapping_error(self):
        assert NOP_INPUT_MAPPING_ERROR == "NOP-INPUT-MAPPING-ERROR"

    def test_compensation_failed(self):
        assert NOP_COMPENSATION_FAILED == "NOP-COMPENSATION-FAILED"

    def test_compensation_not_supported(self):
        assert NOP_COMPENSATION_NOT_SUPPORTED == "NOP-COMPENSATION-NOT-SUPPORTED"

    def test_callback_hmac_missing(self):
        assert NOP_CALLBACK_HMAC_MISSING == "NOP-CALLBACK-HMAC-MISSING"


class TestNopMappingDict:
    _EXPECTED_CODES = {
        "NOP-TASK-NOT-FOUND", "NOP-TASK-TIMEOUT", "NOP-TASK-DAG-INVALID",
        "NOP-TASK-DAG-CYCLE", "NOP-TASK-DAG-TOO-LARGE",
        "NOP-TASK-ALREADY-COMPLETED", "NOP-TASK-CANCELLED",
        "NOP-DELEGATE-SCOPE-VIOLATION", "NOP-DELEGATE-REJECTED",
        "NOP-DELEGATE-CHAIN-TOO-DEEP", "NOP-DELEGATE-TIMEOUT",
        "NOP-SYNC-TIMEOUT", "NOP-SYNC-DEPENDENCY-FAILED",
        "NOP-STREAM-SEQ-GAP", "NOP-STREAM-NID-MISMATCH", "NOP-STREAM-NAK",
        "NOP-RESOURCE-INSUFFICIENT",
        "NOP-CONDITION-EVAL-ERROR", "NOP-INPUT-MAPPING-ERROR",
        "NOP-COMPENSATION-FAILED", "NOP-COMPENSATION-PARTIAL-FAILED",
        "NOP-COMPENSATION-NOT-SUPPORTED", "NOP-CALLBACK-HMAC-MISSING",
        "NOP-CALLBACK-INVALID", "NOP-CALLBACK-HMAC-INVALID",
        "NOP-CLAIM-CONFLICT", "NOP-SPAWN-SPEC-INVALID",
        "NOP-RUNTIME-IDLE-TIMEOUT", "NOP-RUNTIME-MAX-RUNTIME",
        "NOP-TASK-RESULT-EXPIRED", "NOP-STREAM-NAK-UNRESOLVABLE",
    }

    def test_all_codes_covered(self):
        missing = self._EXPECTED_CODES - set(NOP_ERROR_TO_NPS_STATUS.keys())
        assert not missing, f"NOP_ERROR_TO_NPS_STATUS missing: {missing}"

    def test_alpha11_mappings(self):
        assert NOP_ERROR_TO_NPS_STATUS[NOP_STREAM_NAK] == "NPS-STREAM-SEQ-GAP"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_CALLBACK_HMAC_MISSING] == "NPS-AUTH-UNAUTHENTICATED"

    def test_spot_checks(self):
        assert NOP_ERROR_TO_NPS_STATUS[NOP_TASK_TIMEOUT] == "NPS-SERVER-TIMEOUT"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_DELEGATE_SCOPE_VIOLATION] == "NPS-AUTH-FORBIDDEN"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_STREAM_SEQ_GAP] == "NPS-STREAM-SEQ-GAP"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_STREAM_NID_MISMATCH] == "NPS-AUTH-UNAUTHENTICATED"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_COMPENSATION_PARTIAL_FAILED] == "NPS-CLIENT-UNPROCESSABLE"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_CALLBACK_INVALID] == "NPS-CLIENT-BAD-PARAM"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_CALLBACK_HMAC_INVALID] == "NPS-AUTH-UNAUTHENTICATED"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_CLAIM_CONFLICT] == "NPS-CLIENT-CONFLICT"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_SPAWN_SPEC_INVALID] == "NPS-CLIENT-BAD-PARAM"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_RUNTIME_IDLE_TIMEOUT] == "NPS-SERVER-TIMEOUT"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_RUNTIME_MAX_RUNTIME] == "NPS-SERVER-TIMEOUT"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_TASK_RESULT_EXPIRED] == "NPS-CLIENT-NOT-FOUND"
        assert NOP_ERROR_TO_NPS_STATUS[NOP_STREAM_NAK_UNRESOLVABLE] == "NPS-STREAM-SEQ-GAP"
