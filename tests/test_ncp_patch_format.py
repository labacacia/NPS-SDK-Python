# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Parity tests for NcpPatchFormat constants and helpers (NPS-1 §4.2)."""

import pytest

from nps_sdk.core import patch_format
from nps_sdk.core.frames import EncodingTier


def test_constants_match_spec() -> None:
    assert patch_format.JSON_PATCH == "json_patch"
    assert patch_format.BINARY_BITSET == "binary_bitset"
    assert patch_format.ALL == frozenset({"json_patch", "binary_bitset"})


def test_is_valid() -> None:
    assert patch_format.is_valid("json_patch")
    assert patch_format.is_valid("binary_bitset")
    assert not patch_format.is_valid("nope")


def test_validate_json_patch_allowed_on_all_tiers() -> None:
    for tier in (EncodingTier.JSON, EncodingTier.MSGPACK, EncodingTier.BINARY_VECTOR):
        patch_format.validate("json_patch", tier)  # no raise


def test_validate_binary_bitset_only_on_msgpack() -> None:
    patch_format.validate("binary_bitset", EncodingTier.MSGPACK)  # no raise
    with pytest.raises(ValueError):
        patch_format.validate("binary_bitset", EncodingTier.JSON)
    with pytest.raises(ValueError):
        patch_format.validate("binary_bitset", EncodingTier.BINARY_VECTOR)


def test_validate_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        patch_format.validate("bogus", EncodingTier.MSGPACK)
