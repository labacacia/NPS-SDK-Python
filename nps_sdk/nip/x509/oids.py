# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
OID constants for NPS X.509 certificates per NPS-RFC-0002 §4.

The 1.3.6.1.4.1.99999 arc is provisional pending IANA Private Enterprise
Number assignment (RFC-0002 §10 OQ-2). All implementations MUST update
these constants when the official PEN is granted.
"""

from cryptography.x509 import ObjectIdentifier


class NpsX509Oids:
    """OID constants — namespace class for clarity at call sites."""

    # ── Provisional LabAcacia PEN arc ────────────────────────────────────────
    LAB_ACACIA_PEN_ARC = "1.3.6.1.4.1.99999"
    EKU_ARC            = LAB_ACACIA_PEN_ARC + ".1"
    EXTENSION_ARC      = LAB_ACACIA_PEN_ARC + ".2"

    # ── EKUs (NPS-RFC-0002 §4.1) ─────────────────────────────────────────────
    EKU_AGENT_IDENTITY        = ObjectIdentifier(EKU_ARC + ".1")
    EKU_NODE_IDENTITY         = ObjectIdentifier(EKU_ARC + ".2")
    EKU_CA_INTERMEDIATE_AGENT = ObjectIdentifier(EKU_ARC + ".3")

    # ── Custom extensions ────────────────────────────────────────────────────
    NID_ASSURANCE_LEVEL = ObjectIdentifier(EXTENSION_ARC + ".1")

    # ── Ed25519 algorithm OID per RFC 8410 ───────────────────────────────────
    ED25519 = ObjectIdentifier("1.3.101.112")
