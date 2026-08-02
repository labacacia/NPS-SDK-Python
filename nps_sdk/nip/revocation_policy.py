# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NIP v0.13 portable live-revocation policy."""

from __future__ import annotations

import dataclasses
import enum

from nps_sdk.nip import error_codes


class NipRevocationMode(enum.Enum):
    """Whether a verifier may proceed without a configured revocation source."""

    IF_CONFIGURED = "if_configured"
    REQUIRED = "required"


class NipRevocationSource(enum.Enum):
    """Portable revocation sources in normative consultation order."""

    LOCAL_CRL = "local_crl"
    CALLBACK = "callback"
    CA_STORE = "ca_store"
    OCSP = "ocsp"


class NipRevocationOutcome(enum.Enum):
    """Normalized result returned by a configured revocation source."""

    GOOD = "good"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"


@dataclasses.dataclass(frozen=True)
class NipRevocationDecision:
    """Result of observing one source or completing the policy."""

    valid: bool
    error_code: str | None = None
    failed_step: int = 0


class NipRevocationEvaluation:
    """Incremental evaluator shared by the verifier and conformance runners."""

    def __init__(
        self,
        mode: NipRevocationMode,
        ocsp_fail_open: bool,
    ) -> None:
        self._mode = mode
        self._ocsp_fail_open = ocsp_fail_open
        self._consulted_sources: list[NipRevocationSource] = []

    @property
    def consulted_sources(self) -> tuple[NipRevocationSource, ...]:
        return tuple(self._consulted_sources)

    def observe(
        self,
        source: NipRevocationSource,
        outcome: NipRevocationOutcome,
    ) -> NipRevocationDecision | None:
        self._consulted_sources.append(source)
        if outcome == NipRevocationOutcome.GOOD:
            return None
        if outcome == NipRevocationOutcome.REVOKED:
            return NipRevocationDecision(
                False, error_codes.CERT_REVOKED, failed_step=4)
        if (
            source == NipRevocationSource.OCSP
            and self._ocsp_fail_open
        ):
            return None
        return NipRevocationDecision(
            False, error_codes.OCSP_UNAVAILABLE, failed_step=4)

    def complete(self) -> NipRevocationDecision:
        if (
            self._mode == NipRevocationMode.REQUIRED
            and not self._consulted_sources
        ):
            return NipRevocationDecision(
                False, error_codes.OCSP_UNAVAILABLE, failed_step=4)
        return NipRevocationDecision(True)
