# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NipIdentVerifier — Node-side IdentFrame verifier implementing the full
NPS-3 §7 six-step verification flow (parity with the .NET reference
``NPS.NIP.Verification.NipIdentVerifier``).

The six steps (ALL MUST pass):

1. Expiry:        ``expires_at > now`` (``context.as_of`` or ``utcnow``).
2. Trusted issuer: ``issued_by`` is in :attr:`NipVerifierOptions.trusted_issuers`.
3. Signature:     Ed25519 signature verifies against the issuer CA public key,
                  PLUS X.509 chain check when ``cert_format == "v2-x509"`` AND
                  :attr:`NipVerifierOptions.trusted_x509_roots` is configured.
                  A v1-only verifier (no X.509 roots) ignores ``cert_chain``.
4. Revocation:    local CRL → optional ``revocation_check`` callback → optional
                  ``revocation_store`` → optional OCSP GET ``{ocsp_url}/{nid}``.
                  OCSP transport failure honours ``ocsp_fail_open``.
                  Pass-through when nothing is configured.
5. Capabilities:  frame capabilities ⊇ ``context.required_capabilities``.
6. Scope:         ``context.target_node_path`` matched by one of ``scope.nodes``.

Step 4 (revocation) may make an async OCSP call, so :meth:`verify` is a
coroutine. Steps 1–3 and 5–6 are synchronous but wrapped in the same
coroutine for a single ergonomic entry point (Python is async / httpx).
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, runtime_checkable

import httpx
from cryptography import x509

from nps_sdk.nip import cert_format, error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.frames import IdentFrame
from nps_sdk.nip.identity import NipIdentity
from nps_sdk.nip.phase3 import NipPhase3Enforcer
from nps_sdk.nip.revocation_policy import (
    NipRevocationEvaluation,
    NipRevocationMode,
    NipRevocationOutcome,
    NipRevocationSource,
)
from nps_sdk.nip.x509.verifier import NipX509Verifier


# ── Result ────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NipIdentVerifyResult:
    """Result of a Node-side :class:`NipIdentVerifier` check (NPS-3 §7)."""

    valid:        bool
    step_failed:  int = 0                # 0 = none; 1..6 = the failing step
    error_code:   str | None = None
    message:      str | None = None

    @classmethod
    def ok(cls) -> "NipIdentVerifyResult":
        return cls(valid=True)

    @classmethod
    def fail(cls, step: int, error_code: str, message: str) -> "NipIdentVerifyResult":
        return cls(valid=False, step_failed=step, error_code=error_code, message=message)


# ── Revocation store protocol (mirror of .NET INipCaStore.GetBySerialAsync) ────

@dataclasses.dataclass(frozen=True)
class NipCertRecord:
    """
    Minimal persisted certificate record used as a revocation source
    (subset of the .NET ``NipCertRecord`` — only the revocation fields are
    consulted by the verifier).
    """

    serial:        str
    revoked_at:    str | None = None
    revoke_reason: str | None = None


@runtime_checkable
class NipRevocationStore(Protocol):
    """
    Live revocation source keyed by certificate serial (mirror of the .NET
    ``INipCaStore.GetBySerialAsync``). A returned record whose ``revoked_at``
    is populated causes the identity to be rejected.
    """

    async def get_by_serial(self, serial: str) -> NipCertRecord | None: ...


# Live revocation callback (mirror of the .NET ``NipRevocationCheck`` delegate).
# Return a failing result to reject the identity, or ``None`` / an OK result to
# continue to the next configured revocation source.
NipRevocationCheck = Callable[[IdentFrame], Awaitable["NipIdentVerifyResult | None"]]


# ── Per-request context (mirror of .NET NipVerifyContext) ──────────────────────

@dataclasses.dataclass(frozen=True)
class NipVerifyContext:
    """
    Per-request context passed to :class:`NipIdentVerifier` for NPS-3 §7
    steps 5–6 (and the expiry clock). All fields are optional — omit to skip
    the corresponding check.
    """

    # Capabilities the Node requires the Agent to hold (Step 5).
    # None or empty → capability check skipped.
    required_capabilities: Sequence[str] | None = None

    # Full NWP node path the Agent is trying to access (Step 6).
    # None → scope check skipped.
    target_node_path: str | None = None

    # Clock override for testing (replaces utcnow in the expiry check).
    as_of: datetime.datetime | None = None

    # Minimum required assurance level (NPS-RFC-0003 §5.1.1). Carried through
    # (Phase 1: not enforced by the reference verifier).
    min_assurance_level: AssuranceLevel | None = None


# ── Options (mirror of .NET NipVerifierOptions) ────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NipVerifierOptions:
    """Configuration for :class:`NipIdentVerifier` (NPS-3 §7)."""

    # Trusted CA issuers, keyed by issuer NID. Value is the CA public key in
    # "ed25519:<base64url(DER)>" format. Used by Step 2 and Step 3.
    trusted_issuers: Mapping[str, str] = dataclasses.field(default_factory=dict)

    # X.509 trust anchors for v2-x509 frames (Step 3b). Empty/None means the
    # verifier is v1-only and ignores cert_chain on incoming v2 frames.
    trusted_x509_roots: tuple[x509.Certificate, ...] = dataclasses.field(default_factory=tuple)

    # Local set of revoked certificate serials, checked before any network call.
    local_revoked_serials: frozenset[str] | None = None

    # Live revocation callback (runs after the local CRL, before store / OCSP).
    revocation_check: NipRevocationCheck | None = None

    # Live revocation store consulted by serial.
    revocation_store: NipRevocationStore | None = None

    # OCSP endpoint. When set, the verifier issues GET {ocsp_url}/{nid}.
    ocsp_url: str | None = None

    # When True, OCSP transport failures are treated as pass-through. The
    # secure default is fail-closed (returns NIP-OCSP-UNAVAILABLE).
    ocsp_fail_open: bool = False

    # Required rejects certificates when no revocation source is configured.
    revocation_mode: NipRevocationMode = NipRevocationMode.IF_CONFIGURED

    # Minimum required assurance level (NPS-RFC-0003). None disables the check.
    min_assurance_level: AssuranceLevel | None = None

    # NIP v0.12 §7.5 hard failure switch for v2-x509 frames. Defaults false so
    # Phase-1/2 deployments keep treating CA attestation as advisory.
    phase3_enforcement: bool = False

    # Optional injected httpx client for OCSP (mainly for tests / connection reuse).
    http_client: httpx.AsyncClient | None = None

    # ── Backward-compatible alias ─────────────────────────────────────────────
    # The pre-parity verifier keyed the trusted-issuer map on
    # `trusted_ca_public_keys`. Accept it as an alias so existing callers keep
    # working; it is merged into `trusted_issuers`.
    trusted_ca_public_keys: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.trusted_ca_public_keys:
            merged = dict(self.trusted_ca_public_keys)
            merged.update(self.trusted_issuers)
            object.__setattr__(self, "trusted_issuers", merged)


# ── Verifier ───────────────────────────────────────────────────────────────────

class NipIdentVerifier:
    """Node-side IdentFrame verifier implementing the NPS-3 §7 six-step flow."""

    def __init__(self, options: NipVerifierOptions) -> None:
        self._opts = options

    async def verify(
        self,
        frame: IdentFrame,
        context: NipVerifyContext | None = None,
    ) -> NipIdentVerifyResult:
        """Verify *frame* against *context* per NPS-3 §7 (all six steps)."""
        context = context or NipVerifyContext()
        now = context.as_of or _utcnow()

        # ── Step 1: Expiry ────────────────────────────────────────────────────
        expires_at = _parse_timestamp(frame.expires_at)
        if expires_at is None or expires_at <= now:
            return NipIdentVerifyResult.fail(
                1, error_codes.CERT_EXPIRED,
                f"Certificate expired at {frame.expires_at}.")

        # ── Step 2: Trusted issuer ────────────────────────────────────────────
        issuer_pub_key = self._opts.trusted_issuers.get(frame.issued_by)
        if issuer_pub_key is None:
            return NipIdentVerifyResult.fail(
                2, error_codes.CERT_UNTRUSTED_ISSUER,
                f"Issuer {frame.issued_by!r} is not in the trusted issuers list.")

        # ── Step 3: Signature (v1 Ed25519) ────────────────────────────────────
        if not NipIdentity.verify_signature(
                issuer_pub_key, frame.unsigned_dict(), frame.signature):
            return NipIdentVerifyResult.fail(
                3, error_codes.CERT_SIGNATURE_INVALID,
                "Certificate signature verification failed.")

        # ── Step 3b: X.509 chain (NPS-RFC-0002, only when cert_format=v2-x509 ──
        #             AND X.509 roots are configured; v1-only verifier ignores).
        has_v2_trust = bool(self._opts.trusted_x509_roots)
        is_v2_frame = frame.cert_format == cert_format.V2_X509
        if has_v2_trust and is_v2_frame:
            x509_result = NipX509Verifier.verify(
                cert_chain_b64u_der=frame.cert_chain or (),
                asserted_nid=frame.nid,
                asserted_assurance_level=frame.assurance_level,
                trusted_root_certs=self._opts.trusted_x509_roots,
            )
            if not x509_result.valid:
                return NipIdentVerifyResult.fail(
                    3,
                    x509_result.error_code or error_codes.CERT_FORMAT_INVALID,
                    x509_result.message or "X.509 chain validation failed.")
            if self._opts.phase3_enforcement:
                if x509_result.leaf is None:
                    return NipIdentVerifyResult.fail(
                        3,
                        error_codes.CERT_FORMAT_INVALID,
                        "X.509 verifier did not return a leaf certificate.")
                phase3_result = NipPhase3Enforcer.enforce(frame, x509_result.leaf, now)
                if not phase3_result.valid:
                    return phase3_result

        # ── Step 4: Revocation ────────────────────────────────────────────────
        revocation_result = await self._check_revocation(frame)
        if not revocation_result.valid:
            return revocation_result

        # ── Step 5: Capabilities ──────────────────────────────────────────────
        required = context.required_capabilities
        if required:
            frame_caps = set(frame.capabilities)
            missing = [c for c in required if c not in frame_caps]
            if missing:
                return NipIdentVerifyResult.fail(
                    5, error_codes.CERT_CAPABILITY_MISSING,
                    f"Certificate is missing required capabilities: {', '.join(missing)}.")

        # ── Step 6: Scope ─────────────────────────────────────────────────────
        if context.target_node_path is not None:
            scope_result = _check_scope(frame, context.target_node_path)
            if not scope_result.valid:
                return scope_result

        return NipIdentVerifyResult.ok()

    # ── Revocation (Step 4) ────────────────────────────────────────────────────

    async def _check_revocation(self, frame: IdentFrame) -> NipIdentVerifyResult:
        evaluation = NipRevocationEvaluation(
            self._opts.revocation_mode, self._opts.ocsp_fail_open)

        # Local CRL first (fast, no network).
        if self._opts.local_revoked_serials is not None:
            outcome = (
                NipRevocationOutcome.REVOKED
                if frame.serial in self._opts.local_revoked_serials
                else NipRevocationOutcome.GOOD
            )
            result = evaluation.observe(NipRevocationSource.LOCAL_CRL, outcome)
            if result is not None:
                return _revocation_result(result)

        if self._opts.revocation_check is not None:
            try:
                callback_result = await self._opts.revocation_check(frame)
                if callback_result is not None and not callback_result.valid:
                    return callback_result
                result = evaluation.observe(
                    NipRevocationSource.CALLBACK,
                    NipRevocationOutcome.GOOD)
            except Exception:
                result = evaluation.observe(
                    NipRevocationSource.CALLBACK,
                    NipRevocationOutcome.UNAVAILABLE)
            if result is not None:
                return _revocation_result(result)

        if self._opts.revocation_store is not None:
            try:
                record = await self._opts.revocation_store.get_by_serial(frame.serial)
                outcome = (
                    NipRevocationOutcome.REVOKED
                    if record is not None and record.revoked_at is not None
                    else NipRevocationOutcome.GOOD
                )
                result = evaluation.observe(
                    NipRevocationSource.CA_STORE, outcome)
            except Exception:
                result = evaluation.observe(
                    NipRevocationSource.CA_STORE,
                    NipRevocationOutcome.UNAVAILABLE)
            if result is not None:
                return _revocation_result(result)

        # OCSP call to the CA server (optional).
        if self._opts.ocsp_url is not None:
            ocsp = await self._ocsp_check(frame.nid)
            if not ocsp.valid:
                return ocsp
            result = evaluation.observe(
                NipRevocationSource.OCSP, NipRevocationOutcome.GOOD)
            if result is not None:
                return _revocation_result(result)

        return _revocation_result(evaluation.complete())

    async def _ocsp_check(self, nid: str) -> NipIdentVerifyResult:
        url = f"{self._opts.ocsp_url.rstrip('/')}/{_escape(nid)}"
        client = self._opts.http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.get(url)
            if resp.status_code < 200 or resp.status_code >= 300:
                return NipIdentVerifyResult.fail(
                    4, error_codes.OCSP_UNAVAILABLE,
                    f"OCSP endpoint returned {resp.status_code}.")
            body = resp.json()
            is_valid = bool(body.get("valid", False)) if isinstance(body, dict) else False
            if not is_valid:
                error_code = error_codes.CERT_REVOKED
                if isinstance(body, dict) and body.get("error_code"):
                    error_code = str(body["error_code"])
                return NipIdentVerifyResult.fail(
                    4, error_code, f"OCSP check failed for NID {nid}.")
            return NipIdentVerifyResult.ok()
        except (httpx.HTTPError, ValueError) as exc:
            if self._opts.ocsp_fail_open:
                return NipIdentVerifyResult.ok()
            return NipIdentVerifyResult.fail(
                4, error_codes.OCSP_UNAVAILABLE,
                f"OCSP call failed for NID {nid}: {exc}")
        finally:
            if owns_client:
                await client.aclose()


def _revocation_result(decision: Any) -> NipIdentVerifyResult:
    if decision.valid:
        return NipIdentVerifyResult.ok()
    return NipIdentVerifyResult.fail(
        decision.failed_step,
        decision.error_code or error_codes.OCSP_UNAVAILABLE,
        "Live revocation verification failed.")


# ── Scope / path matching ──────────────────────────────────────────────────────

def _check_scope(frame: IdentFrame, target_path: str) -> NipIdentVerifyResult:
    scope = frame.scope
    nodes = scope.get("nodes") if isinstance(scope, Mapping) else None
    if not isinstance(nodes, (list, tuple)):
        return NipIdentVerifyResult.fail(
            6, error_codes.CERT_SCOPE_VIOLATION,
            "IdentFrame scope is missing 'nodes' field.")

    for pattern in nodes:
        if isinstance(pattern, str) and nwp_path_matches(pattern, target_path):
            return NipIdentVerifyResult.ok()

    return NipIdentVerifyResult.fail(
        6, error_codes.CERT_SCOPE_VIOLATION,
        f"Target path {target_path!r} is not covered by the certificate scope.")


def nwp_path_matches(pattern: str, path: str) -> bool:
    """
    Match a NWP path against a scope pattern (parity with the .NET
    ``NipIdentVerifier.NwpPathMatches``):

    * a bare ``*`` matches any path;
    * a trailing ``/*`` matches the prefix and any path under it
      (boundary at ``/``);
    * all other patterns are exact case-insensitive matches.
    """
    if pattern == "*":
        return True

    if pattern.endswith("/*"):
        prefix = pattern[:-2]  # strip "/*"
        lowered = path.lower()
        lowered_prefix = prefix.lower()
        return lowered.startswith(lowered_prefix) and (
            len(path) == len(prefix) or path[len(prefix)] == "/")

    return pattern.lower() == path.lower()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_timestamp(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _escape(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
