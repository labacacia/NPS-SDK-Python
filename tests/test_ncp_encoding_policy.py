# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Parity tests for NcpEncodingPolicy allow/deny + negotiation helpers."""

import pytest

from nps_sdk.core.exceptions import NpsEncodingUnsupportedError
from nps_sdk.core.frames import EncodingTier, FrameFlags, FrameHeader, FrameType
from nps_sdk.ncp.encoding_policy import NcpEncodingPolicy


def test_encoding_token() -> None:
    assert NcpEncodingPolicy.encoding_token(EncodingTier.JSON) == "json"
    assert NcpEncodingPolicy.encoding_token(EncodingTier.MSGPACK) == "msgpack"
    assert NcpEncodingPolicy.encoding_token(EncodingTier.BINARY_VECTOR) == "binary_vector.v1"


def test_enabled_encodings_default_only() -> None:
    policy = NcpEncodingPolicy(EncodingTier.MSGPACK)
    assert policy.enabled_encodings == ("msgpack",)


def test_enabled_encodings_with_binary_vector() -> None:
    policy = NcpEncodingPolicy(EncodingTier.MSGPACK, binary_vector_enabled=True)
    assert policy.enabled_encodings == ("msgpack", "binary_vector.v1")


def test_from_enabled_encodings() -> None:
    p1 = NcpEncodingPolicy.from_enabled_encodings(EncodingTier.JSON, ["json"])
    assert not p1.binary_vector_enabled
    p2 = NcpEncodingPolicy.from_enabled_encodings(
        EncodingTier.MSGPACK, ["msgpack", "binary_vector.v1"]
    )
    assert p2.binary_vector_enabled
    p3 = NcpEncodingPolicy.from_enabled_encodings(EncodingTier.JSON, None)
    assert not p3.binary_vector_enabled


def test_allows_default_tier_for_any_frame() -> None:
    policy = NcpEncodingPolicy(EncodingTier.MSGPACK)
    assert policy.allows(EncodingTier.MSGPACK, FrameType.CAPS)
    assert not policy.allows(EncodingTier.JSON, FrameType.CAPS)


def test_allows_binary_vector_only_for_query_when_enabled() -> None:
    policy = NcpEncodingPolicy(EncodingTier.MSGPACK, binary_vector_enabled=True)
    assert policy.allows(EncodingTier.BINARY_VECTOR, FrameType.QUERY)
    assert not policy.allows(EncodingTier.BINARY_VECTOR, FrameType.CAPS)

    disabled = NcpEncodingPolicy(EncodingTier.MSGPACK, binary_vector_enabled=False)
    assert not disabled.allows(EncodingTier.BINARY_VECTOR, FrameType.QUERY)


def test_ensure_allows_passes_and_raises() -> None:
    policy = NcpEncodingPolicy(EncodingTier.MSGPACK)
    ok_header = FrameHeader(FrameType.CAPS, FrameFlags.TIER2_MSGPACK, 0)
    policy.ensure_allows(ok_header)  # no raise

    bad_header = FrameHeader(FrameType.CAPS, FrameFlags.TIER1_JSON, 0)
    with pytest.raises(NpsEncodingUnsupportedError):
        policy.ensure_allows(bad_header)
