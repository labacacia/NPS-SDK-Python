# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
OID constants for NPS X.509 certificates per NPS-RFC-0002 §4.

The 1.3.6.1.4.1.65715 arc is the LabAcacia IANA-assigned Private Enterprise
Number (PEN 65715, NPS-CR-0004, 2026-05-08).
"""

from cryptography.x509 import ObjectIdentifier


class NpsX509Oids:
    """OID constants — namespace class for clarity at call sites."""

    # ── LabAcacia IANA-assigned PEN arc (PEN 65715, NPS-CR-0004, 2026-05-08) ──
    LAB_ACACIA_PEN_ARC = "1.3.6.1.4.1.65715"
    EKU_ARC            = LAB_ACACIA_PEN_ARC + ".1"
    EXTENSION_ARC      = LAB_ACACIA_PEN_ARC + ".2"

    # ── EKUs (NPS-RFC-0002 §4.1) ─────────────────────────────────────────────
    EKU_AGENT_IDENTITY        = ObjectIdentifier(EKU_ARC + ".1")
    EKU_NODE_IDENTITY         = ObjectIdentifier(EKU_ARC + ".2")
    EKU_CA_INTERMEDIATE_AGENT = ObjectIdentifier(EKU_ARC + ".3")

    # ── Custom extensions ────────────────────────────────────────────────────
    NID_ASSURANCE_LEVEL = ObjectIdentifier(EXTENSION_ARC + ".1")
    # NIP v0.12 §7.5 — CA attestation of the node roles / capabilities an identity may
    # claim. Both carry DER `SEQUENCE OF UTF8String`.
    ID_NPS_NODE_ROLES   = ObjectIdentifier(EXTENSION_ARC + ".2")
    ID_NPS_CAPABILITIES = ObjectIdentifier(EXTENSION_ARC + ".3")

    # ── Ed25519 algorithm OID per RFC 8410 ───────────────────────────────────
    ED25519 = ObjectIdentifier("1.3.101.112")
