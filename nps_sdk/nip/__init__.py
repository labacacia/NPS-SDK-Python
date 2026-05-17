# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS NIP — Neural Identity Protocol frames and identity management."""

from nps_sdk.nip.frames import IdentFrame, IdentMetadata, RevokeFrame, TrustFrame
from nps_sdk.nip.identity import NipIdentity
from nps_sdk.nip.reputation import (
    IncidentType,
    InclusionProof,
    ObservationWindow,
    ReputationLogClient,
    ReputationLogEntry,
    ReputationLogException,
    Severity,
    SignedTreeHead,
    sign_entry,
    verify_entry,
)

__all__ = [
    "IdentFrame",
    "IdentMetadata",
    "RevokeFrame",
    "TrustFrame",
    "NipIdentity",
    "IncidentType",
    "InclusionProof",
    "ObservationWindow",
    "ReputationLogClient",
    "ReputationLogEntry",
    "ReputationLogException",
    "Severity",
    "SignedTreeHead",
    "sign_entry",
    "verify_entry",
]
