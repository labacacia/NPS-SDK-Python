# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Issues NPS X.509 NID certificates per NPS-RFC-0002 §4.

Two factory functions:
- `issue_leaf` — leaf cert with critical NPS EKU + SAN URI = NID + assurance-level extension.
- `issue_root` — self-signed root for testing / private-CA use.

Both sign with native pyca/cryptography Ed25519.
"""

from __future__ import annotations

import datetime
import enum

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.x509.oids import NpsX509Oids


class LeafRole(enum.Enum):
    """Role embedded in the leaf cert's ExtendedKeyUsage extension."""

    AGENT = NpsX509Oids.EKU_AGENT_IDENTITY
    NODE  = NpsX509Oids.EKU_NODE_IDENTITY


class NipX509Builder:
    """Static factory; namespace class for parity with other SDKs."""

    @staticmethod
    def issue_leaf(
        subject_nid:        str,
        subject_pub_key:    ed25519.Ed25519PublicKey,
        ca_priv_key:        ed25519.Ed25519PrivateKey,
        issuer_nid:         str,
        role:               LeafRole,
        assurance_level:    AssuranceLevel,
        not_before:         datetime.datetime,
        not_after:          datetime.datetime,
        serial_number:      int,
    ) -> x509.Certificate:
        """Issue an NPS leaf cert (RFC-0002 §4.1)."""
        builder = (
            x509.CertificateBuilder()
            .subject_name(_name(subject_nid))
            .issuer_name(_name(issuer_nid))
            .public_key(subject_pub_key)
            .serial_number(serial_number)
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False,
                    key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([role.value]),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.UniformResourceIdentifier(subject_nid)]),
                critical=False,
            )
            .add_extension(
                # ASN.1 ENUMERATED value: tag=0x0A, len=0x01, content=<rank>.
                # Hand-rolled DER — pyca/cryptography exposes UnrecognizedExtension for
                # arbitrary extension contents.
                x509.UnrecognizedExtension(
                    NpsX509Oids.NID_ASSURANCE_LEVEL,
                    bytes([0x0A, 0x01, assurance_level.rank]),
                ),
                critical=False,
            )
        )
        # Ed25519 in cryptography signs with algorithm=None (no separate digest).
        return builder.sign(private_key=ca_priv_key, algorithm=None)

    @staticmethod
    def issue_root(
        ca_nid:         str,
        ca_priv_key:    ed25519.Ed25519PrivateKey,
        not_before:     datetime.datetime,
        not_after:      datetime.datetime,
        serial_number:  int,
    ) -> x509.Certificate:
        """Issue a self-signed CA root cert."""
        ca_pub = ca_priv_key.public_key()
        builder = (
            x509.CertificateBuilder()
            .subject_name(_name(ca_nid))
            .issuer_name(_name(ca_nid))
            .public_key(ca_pub)
            .serial_number(serial_number)
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False,
                    key_cert_sign=True, crl_sign=True,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
        )
        return builder.sign(private_key=ca_priv_key, algorithm=None)


def _name(nid: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nid)])
