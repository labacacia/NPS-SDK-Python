# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NIP — server-side Certificate Authority (CA) service library.

Port of the .NET reference ``NPS.NIP.Ca`` namespace (NPS-3 §6–8, NPS-CR-0003
group/session lineage, NPS-CR-0005 Registration-Authority enrollment tiers,
NPS-RFC-0002 X.509 dual-trust).

This is the *server* half of NIP identity issuance — the client half lives in
:mod:`nps_sdk.nip.ca_client`. The two share the same wire contract (field
names, NID format, error codes, HTTP statuses) so a Python CA and a .NET
client (or vice versa) interoperate.
"""

from nps_sdk.nip.ca.options import EnrollmentTier, NipCaOptions
from nps_sdk.nip.ca.store import (
    INipCaStore,
    InMemoryNipCaStore,
    NipCaCertRecord,
)
from nps_sdk.nip.ca.errors import NipCaException, ca_error_codes
from nps_sdk.nip.ca.lineage import IdentLineage, IdentLineageRole
from nps_sdk.nip.ca.service import (
    NipCaService,
    NipVerifyResult,
)
from nps_sdk.nip.ca.ra import (
    AllowlistPolicy,
    BootstrapTokenInfo,
    BootstrapTokenPolicy,
    IBootstrapTokenStore,
    IEnrollmentPolicy,
    InMemoryBootstrapTokenStore,
    InMemoryPendingStore,
    IPendingStore,
    NipRaPendingException,
    PendingQueuePolicy,
    PendingRegistration,
    PendingStatus,
    create_enrollment_policy,
)
from nps_sdk.nip.ca.group_jws import FlattenedJws, NipGroupJws
from nps_sdk.nip.ca.router import NipCaRouterApp
from nps_sdk.nip.ca.sql_store import (
    INipCaDbExecutor,
    SqlNipCaStore,
    SqliteNipCaDbExecutor,
    SqliteNipCaStore,
)

__all__ = [
    "EnrollmentTier",
    "NipCaOptions",
    "INipCaStore",
    "InMemoryNipCaStore",
    "NipCaCertRecord",
    "NipCaException",
    "ca_error_codes",
    "IdentLineage",
    "IdentLineageRole",
    "NipCaService",
    "NipVerifyResult",
    "IEnrollmentPolicy",
    "AllowlistPolicy",
    "BootstrapTokenPolicy",
    "IBootstrapTokenStore",
    "InMemoryBootstrapTokenStore",
    "BootstrapTokenInfo",
    "PendingQueuePolicy",
    "IPendingStore",
    "InMemoryPendingStore",
    "PendingRegistration",
    "PendingStatus",
    "NipRaPendingException",
    "create_enrollment_policy",
    "FlattenedJws",
    "NipGroupJws",
    "NipCaRouterApp",
    # SQL-backed CA store ([I] band)
    "INipCaDbExecutor",
    "SqlNipCaStore",
    "SqliteNipCaDbExecutor",
    "SqliteNipCaStore",
]
