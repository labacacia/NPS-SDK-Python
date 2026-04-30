# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Parity tests for NPS-RFC-0001 NCP native-mode connection preamble."""

import io

import pytest

from nps_sdk.ncp import preamble
from nps_sdk.ncp.preamble import NcpPreambleInvalidError


SPEC_BYTES = bytes([0x4E, 0x50, 0x53, 0x2F, 0x31, 0x2E, 0x30, 0x0A])


def test_bytes_are_exactly_the_spec_constant() -> None:
    assert preamble.LENGTH == 8
    assert preamble.LITERAL == "NPS/1.0\n"
    assert preamble.BYTES == SPEC_BYTES


def test_matches_returns_true_for_exact_preamble() -> None:
    assert preamble.matches(preamble.BYTES)


def test_matches_returns_true_when_preamble_is_at_start_of_longer_buffer() -> None:
    combined = preamble.BYTES + b"\x06" + b"\x00" * 7
    assert preamble.matches(combined)


@pytest.mark.parametrize("length", [0, 1, 7])
def test_matches_returns_false_on_short_reads(length: int) -> None:
    assert not preamble.matches(preamble.BYTES[:length])


def test_try_validate_accepts_exact_preamble() -> None:
    ok, reason = preamble.try_validate(preamble.BYTES)
    assert ok and reason == ""


def test_try_validate_rejects_short_read_with_reason() -> None:
    ok, reason = preamble.try_validate(b"\x00\x00\x00")
    assert not ok and "short read" in reason and "3/8" in reason


def test_try_validate_rejects_arbitrary_garbage() -> None:
    ok, reason = preamble.try_validate(b"GET / HTT")
    assert not ok and "future" not in reason and "not speaking NPS" in reason


def test_try_validate_flags_future_major_distinctly() -> None:
    ok, reason = preamble.try_validate(b"NPS/2.0\n")
    assert not ok and "future-major" in reason


def test_validate_throws_with_codes_exposed() -> None:
    with pytest.raises(NcpPreambleInvalidError) as excinfo:
        preamble.validate(b"BADXXXXX")
    err = excinfo.value
    assert err.error_code == "NCP-PREAMBLE-INVALID"
    assert err.status_code == "NPS-PROTO-PREAMBLE-INVALID"
    assert str(err)


def test_write_emits_exactly_the_constant_bytes() -> None:
    buf = io.BytesIO()
    preamble.write(buf)
    assert buf.getvalue() == SPEC_BYTES


def test_status_and_error_code_constants_match_spec() -> None:
    assert preamble.ERROR_CODE  == "NCP-PREAMBLE-INVALID"
    assert preamble.STATUS_CODE == "NPS-PROTO-PREAMBLE-INVALID"
