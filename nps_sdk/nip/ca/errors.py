# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
CA-side exception + error-code namespace.

``NipCaException`` mirrors the .NET ``NPS.NIP.Ca.NipCaException`` — it carries a
machine-readable ``error_code`` (one of :mod:`nps_sdk.nip.error_codes`) that the
HTTP router maps to an exact status code for cross-SDK interop.
"""

from __future__ import annotations

from nps_sdk.nip import error_codes


class NipCaException(Exception):
    """Raised when a NIP CA operation cannot be completed (NPS-3 §9)."""

    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code

    @property
    def errorCode(self) -> str:
        return self.error_code


class _CaErrorCodes:
    """Attribute-style alias of the NIP error-code wire constants used by the CA.

    Mirrors the .NET ``NipErrorCodes`` static class so ports read the same. The
    canonical string values live in :mod:`nps_sdk.nip.error_codes`.
    """

    CERT_EXPIRED = error_codes.CERT_EXPIRED
    CERT_REVOKED = error_codes.CERT_REVOKED
    CERT_CAPABILITY_MISSING = error_codes.CERT_CAPABILITY_MISSING
    CERT_FORMAT_INVALID = error_codes.CERT_FORMAT_INVALID

    NID_NOT_FOUND = error_codes.CA_NID_NOT_FOUND
    NID_ALREADY_EXISTS = error_codes.CA_NID_ALREADY_EXISTS
    SERIAL_DUPLICATE = error_codes.CA_SERIAL_DUPLICATE
    RENEWAL_TOO_EARLY = error_codes.CA_RENEWAL_TOO_EARLY
    SCOPE_EXPANSION = error_codes.CA_SCOPE_EXPANSION_DENIED

    GROUP_REVOKED = error_codes.CA_GROUP_REVOKED
    PARENT_NOT_FOUND = error_codes.CA_PARENT_NOT_FOUND
    PARENT_NOT_GROUP = error_codes.CA_PARENT_NOT_GROUP
    SESSION_VALIDITY_INVALID = error_codes.CA_SESSION_VALIDITY_INVALID
    JWS_INVALID = error_codes.CA_JWS_INVALID
    JWS_EXPIRED = error_codes.CA_JWS_EXPIRED
    PARENT_REVOKED = error_codes.CERT_PARENT_REVOKED

    RA_TOKEN_INVALID = error_codes.RA_TOKEN_INVALID
    RA_TOKEN_EXPIRED = error_codes.RA_TOKEN_EXPIRED
    RA_NID_NOT_ALLOWED = error_codes.RA_NID_NOT_ALLOWED
    RA_PENDING_REJECTED = error_codes.RA_PENDING_REJECTED


ca_error_codes = _CaErrorCodes()
