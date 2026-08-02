# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NIP v0.12 §7.5 Phase-3 enforcement.

Turns the Phase-1–2 *advisory* CA-attestation checks into hard failures. Applies only
to ``v2-x509`` frames, so self-declared / v1 NIDs and v1-only verifiers are entirely
unaffected. Each attribute check applies only when the corresponding certificate
extension is present; the role and capability checks are **subset** checks — the frame
MUST NOT claim more than the CA attested.

Evaluation order is fixed: ``node_roles`` → ``capabilities`` → OCSP staple.

Everything here is stateless and pure — no I/O, no network. The clock is injectable via
``now`` so freshness checks are deterministic in tests.
"""

from __future__ import annotations

import base64
import datetime as _dt
from typing import Any, Sequence

from cryptography import x509

from nps_sdk.nip import error_codes
from nps_sdk.nip.frames import IdentFrame
from nps_sdk.nip.x509.oids import NpsX509Oids

__all__ = ["NipPhase3Enforcer", "DerParseError"]


class DerParseError(ValueError):
    """Raised internally when a DER structure does not match the expected shape."""


# ── Minimal DER reader ────────────────────────────────────────────────────────
#
# `cryptography` exposes no general-purpose ASN.1 reader, and the OCSP/extension walks
# below need only a handful of primitives, so a ~90-line reader is preferable to taking
# on a new runtime dependency (asn1crypto/pyasn1) for two call sites.

_TAG_SEQUENCE = 0x30
_TAG_OCTET_STRING = 0x04
_TAG_OID = 0x06
_TAG_ENUMERATED = 0x0A
_TAG_UTF8_STRING = 0x0C
_TAG_GENERALIZED_TIME = 0x18
_CLASS_CONTEXT = 0x80


class _DerReader:
    """A cursor over a DER byte string, exposing just the primitives Phase 3 needs."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    @property
    def has_data(self) -> bool:
        return self._pos < len(self._data)

    def peek_tag(self) -> int:
        if not self.has_data:
            raise DerParseError("unexpected end of DER input")
        return self._data[self._pos]

    def read_tlv(self, expected_tag: int | None = None) -> tuple[int, bytes]:
        """Read one TLV and return ``(tag, content)``."""
        if self._pos + 2 > len(self._data):
            raise DerParseError("truncated DER TLV header")
        tag = self._data[self._pos]
        if expected_tag is not None and tag != expected_tag:
            raise DerParseError(f"expected DER tag {expected_tag:#04x}, found {tag:#04x}")
        self._pos += 1
        length = self._read_length()
        end = self._pos + length
        if end > len(self._data):
            raise DerParseError("truncated DER TLV content")
        content = self._data[self._pos:end]
        self._pos = end
        return tag, content

    def read_sequence(self, expected_tag: int = _TAG_SEQUENCE) -> "_DerReader":
        return _DerReader(self.read_tlv(expected_tag)[1])

    def read_encoded_value(self) -> bytes:
        """Skip over one complete TLV, returning its raw content."""
        return self.read_tlv()[1]

    def read_utf8_string(self) -> str:
        return self.read_tlv(_TAG_UTF8_STRING)[1].decode("utf-8")

    def read_generalized_time(self) -> _dt.datetime:
        raw = self.read_tlv(_TAG_GENERALIZED_TIME)[1].decode("ascii")
        return _parse_generalized_time(raw)

    def _read_length(self) -> int:
        first = self._data[self._pos]
        self._pos += 1
        if first < 0x80:
            return first
        count = first & 0x7F
        if count == 0 or count > 4:
            raise DerParseError("unsupported DER length encoding")
        if self._pos + count > len(self._data):
            raise DerParseError("truncated DER length")
        value = int.from_bytes(self._data[self._pos:self._pos + count], "big")
        self._pos += count
        return value


def _parse_generalized_time(raw: str) -> _dt.datetime:
    """Parse an ASN.1 ``GeneralizedTime`` (``YYYYMMDDHHMMSS[.fff]Z``) as UTC."""
    text = raw.strip()
    if not text.endswith("Z"):
        raise DerParseError(f"GeneralizedTime must be UTC ('Z'-suffixed): {raw!r}")
    body = text[:-1]
    fmt = "%Y%m%d%H%M%S.%f" if "." in body else "%Y%m%d%H%M%S"
    try:
        parsed = _dt.datetime.strptime(body, fmt)
    except ValueError as exc:
        raise DerParseError(f"malformed GeneralizedTime {raw!r}") from exc
    return parsed.replace(tzinfo=_dt.timezone.utc)


def _from_base64url(value: str) -> bytes:
    """Decode base64url with the padding restored. Raises ``ValueError`` when invalid."""
    padded = value.replace("-", "+").replace("_", "/")
    padded += "=" * ((4 - len(padded) % 4) % 4)
    return base64.b64decode(padded, validate=True)


# ── The enforcer ──────────────────────────────────────────────────────────────

class NipPhase3Enforcer:
    """Stateless NIP v0.12 §7.5 Phase-3 checks against a leaf certificate."""

    @staticmethod
    def enforce(
        frame: IdentFrame,
        leaf: x509.Certificate,
        now: _dt.datetime | None = None,
    ) -> "Any":
        """Run the Phase-3 checks. Returns a ``NipIdentVerifyResult``.

        Failures always report step 3 — Phase 3 sits inside the verifier's X.509 step,
        after the chain check and before revocation.
        """
        # Imported lazily: `verifier` imports this module for step 3c.
        from nps_sdk.nip.verifier import NipIdentVerifyResult

        if frame is None:
            raise ValueError("frame is required")
        if leaf is None:
            raise ValueError("leaf certificate is required")
        when = now or _dt.datetime.now(_dt.timezone.utc)

        # 1. node_roles ⊆ id-nps-node-roles — only when the extension is present.
        attested_roles = NipPhase3Enforcer.read_utf8_sequence_extension(
            leaf, NpsX509Oids.ID_NPS_NODE_ROLES)
        if attested_roles is not None:
            excess = _excess(frame.node_roles or (), attested_roles)
            if excess:
                return NipIdentVerifyResult.fail(
                    3, error_codes.CERT_NODE_ROLES_MISMATCH,
                    "IdentFrame.node_roles claims role(s) not attested by "
                    f"id-nps-node-roles: {', '.join(excess)}.")

        # 2. capabilities ⊆ id-nps-capabilities — only when the extension is present.
        attested_caps = NipPhase3Enforcer.read_utf8_sequence_extension(
            leaf, NpsX509Oids.ID_NPS_CAPABILITIES)
        if attested_caps is not None:
            excess = _excess(frame.capabilities or (), attested_caps)
            if excess:
                return NipIdentVerifyResult.fail(
                    3, error_codes.CERT_CAPABILITIES_EXCEEDED,
                    "IdentFrame.capabilities claims capabilit(ies) not attested by "
                    f"id-nps-capabilities: {', '.join(excess)}.")

        # 3. OCSP staple — the one unconditional check. Every failure mode below
        #    collapses onto NIP-OCSP-STAPLE-EXPIRED: fail closed.
        if not frame.ocsp_staple:
            return NipIdentVerifyResult.fail(
                3, error_codes.OCSP_STAPLE_EXPIRED,
                "Phase-3 enforcement requires ocsp_staple on v2-x509 IdentFrames; "
                "none was supplied.")
        try:
            staple_der = _from_base64url(frame.ocsp_staple)
        except Exception:  # noqa: BLE001 — any decode fault is the same fault
            return NipIdentVerifyResult.fail(
                3, error_codes.OCSP_STAPLE_EXPIRED, "ocsp_staple is not valid base64url.")

        ok, next_update = NipPhase3Enforcer.try_get_ocsp_next_update(staple_der)
        if not ok or next_update is None:
            return NipIdentVerifyResult.fail(
                3, error_codes.OCSP_STAPLE_EXPIRED,
                "ocsp_staple could not be parsed as a DER OCSPResponse with a nextUpdate.")
        # `<=`, not `<`: a staple that expires exactly now is already stale.
        if next_update <= when:
            return NipIdentVerifyResult.fail(
                3, error_codes.OCSP_STAPLE_EXPIRED,
                f"ocsp_staple nextUpdate {next_update.isoformat()} has elapsed.")

        return NipIdentVerifyResult.ok()

    @staticmethod
    def read_utf8_sequence_extension(
        cert: x509.Certificate,
        oid: x509.ObjectIdentifier,
    ) -> list[str] | None:
        """Read a ``SEQUENCE OF UTF8String`` extension. Tri-state, and the tri-state IS the rule.

        * extension **absent** ⇒ ``None`` ⇒ the caller skips the check entirely;
        * present and well-formed ⇒ the parsed list, possibly ``[]``;
        * present but malformed ⇒ ``[]`` — the strictest reading, so any claim then
          exceeds it and fails.

        Collapsing "absent" onto "empty" would turn a fail-closed case into a skip.
        """
        raw: bytes | None = None
        for ext in cert.extensions:
            if ext.oid == oid:
                value = ext.value
                raw = value.value if isinstance(value, x509.UnrecognizedExtension) \
                    else value.public_bytes()
                break
        if raw is None:
            return None

        try:
            seq = _DerReader(raw).read_sequence()
            values: list[str] = []
            while seq.has_data:
                values.append(seq.read_utf8_string())
            return values
        except (DerParseError, UnicodeDecodeError, IndexError):
            return []

    @staticmethod
    def try_get_ocsp_next_update(der: bytes) -> tuple[bool, _dt.datetime | None]:
        """Minimal RFC 6960 DER walk to the **first** ``SingleResponse.nextUpdate``.

        Signature verification of the staple is the full OCSP pipeline's job; the
        Phase-3 gate needs only freshness. Returns ``(False, None)`` when
        ``responseBytes`` is absent, ``responses`` is empty, ``nextUpdate`` is absent,
        or any ASN.1 content is malformed.
        """
        try:
            # OCSPResponse ::= SEQUENCE { responseStatus ENUMERATED,
            #                             responseBytes [0] EXPLICIT ... OPTIONAL }
            root = _DerReader(der).read_sequence()
            root.read_tlv(_TAG_ENUMERATED)                       # responseStatus
            if not root.has_data:
                return False, None
            wrap = root.read_sequence(_CLASS_CONTEXT | 0x20 | 0)  # [0] EXPLICIT
            # ResponseBytes ::= SEQUENCE { responseType OID, response OCTET STRING }
            resp_bytes = wrap.read_sequence()
            resp_bytes.read_tlv(_TAG_OID)                        # id-pkix-ocsp-basic
            basic_der = resp_bytes.read_tlv(_TAG_OCTET_STRING)[1]
            # BasicOCSPResponse ::= SEQUENCE { tbsResponseData ResponseData, ... }
            basic = _DerReader(basic_der).read_sequence()
            tbs = basic.read_sequence()                          # ResponseData
            # ResponseData ::= SEQUENCE { version [0] EXPLICIT OPTIONAL,
            #                             responderID CHOICE [1]/[2],
            #                             producedAt GeneralizedTime,
            #                             responses SEQUENCE OF SingleResponse }
            if tbs.peek_tag() == (_CLASS_CONTEXT | 0x20 | 0):
                tbs.read_tlv()                                   # version
            if tbs.peek_tag() & 0xC0 == _CLASS_CONTEXT:
                tbs.read_encoded_value()                         # responderID
            tbs.read_generalized_time()                          # producedAt
            responses = tbs.read_sequence()                      # SEQUENCE OF SingleResponse
            if not responses.has_data:
                return False, None
            single = responses.read_sequence()
            # SingleResponse ::= SEQUENCE { certID SEQUENCE, certStatus CHOICE,
            #                               thisUpdate, nextUpdate [0] EXPLICIT OPTIONAL, ... }
            single.read_sequence()                               # certID
            single.read_encoded_value()                          # certStatus
            single.read_generalized_time()                       # thisUpdate
            if not single.has_data or single.peek_tag() != (_CLASS_CONTEXT | 0x20 | 0):
                return False, None
            next_update = single.read_sequence(_CLASS_CONTEXT | 0x20 | 0).read_generalized_time()
            return True, next_update
        except (DerParseError, ValueError, IndexError):
            return False, None


def _excess(claimed: Sequence[str], attested: Sequence[str]) -> list[str]:
    """``claimed \\ attested`` preserving claim order. Ordinal / exact-byte equality."""
    allowed = set(attested)
    seen: set[str] = set()
    out: list[str] = []
    for value in claimed:
        if value not in allowed and value not in seen:
            seen.add(value)
            out.append(value)
    return out
