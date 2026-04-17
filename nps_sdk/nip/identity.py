# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NipIdentity — Ed25519 keypair management for NPS NIP agents.

Handles:
- Key generation + AES-256-GCM encrypted file persistence (NPS-3 §10.1).
- Key loading from an encrypted key file.
- Canonical JSON signing / verification of NIP frames.

Key file format (binary):
  [12-byte nonce][N-byte ciphertext][16-byte tag]
  ciphertext = AES-256-GCM(key=PBKDF2-SHA256(passphrase, nonce, 600_000 iters), plaintext=raw_private_key_32_bytes)
  (AESGCM.encrypt returns the ciphertext with the 16-byte authentication tag appended.)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes, serialization


_NONCE_SIZE   = 12
_TAG_SIZE     = 16
_PBKDF2_ITERS = 600_000
_RAW_KEY_SIZE = 32  # Ed25519 raw private key length


class NipIdentity:
    """
    NIP Ed25519 identity for an Agent or CA.

    Load from an encrypted key file::

        identity = NipIdentity()
        identity.load("ca.key", "my-passphrase")

    Or generate a fresh keypair::

        identity = NipIdentity.generate("ca.key", "my-passphrase")

    Sign a NIP frame (canonical JSON, excluding the 'signature' field)::

        sig = identity.sign(ident_frame.unsigned_dict())
        # sig is an ed25519:<base64url> string

    Verify a signature::

        ok = NipIdentity.verify_signature(pub_key_str, payload_dict, sig_str)
    """

    def __init__(self) -> None:
        self._private_key: Ed25519PrivateKey | None = None
        self._public_key:  Ed25519PublicKey  | None = None

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._private_key is not None

    @property
    def public_key(self) -> Ed25519PublicKey:
        if self._public_key is None:
            raise RuntimeError("Identity not loaded. Call load() or generate() first.")
        return self._public_key

    @property
    def pub_key_string(self) -> str:
        """
        Return the public key in ``ed25519:<base64url(DER)>`` format,
        as used in NIP IdentFrame.pub_key (NPS-3 §5.1).
        """
        der = self.public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return f"ed25519:{base64.urlsafe_b64encode(der).decode('ascii')}"

    # ── Key generation ────────────────────────────────────────────────────────

    @classmethod
    def generate(cls, key_file_path: str, passphrase: str) -> "NipIdentity":
        """
        Generate a new Ed25519 keypair, save it encrypted to *key_file_path*,
        and return a loaded NipIdentity.
        """
        identity = cls()
        private_key = Ed25519PrivateKey.generate()
        identity._save_encrypted(private_key, key_file_path, passphrase)
        identity.load(key_file_path, passphrase)
        return identity

    # ── Key loading ───────────────────────────────────────────────────────────

    def load(self, key_file_path: str, passphrase: str) -> None:
        """
        Load and decrypt the keypair from *key_file_path*.

        Raises:
            FileNotFoundError: if the key file does not exist.
            ValueError: if the passphrase is wrong or the file is corrupt.
        """
        file_bytes = _read_file(key_file_path)

        # File format: [nonce(12)] [ciphertext + tag(N+16)]
        # AESGCM.encrypt appends the 16-byte tag to the ciphertext output.
        if len(file_bytes) < _NONCE_SIZE + _TAG_SIZE + _RAW_KEY_SIZE:
            raise ValueError("Key file is too short or corrupt.")

        nonce      = file_bytes[:_NONCE_SIZE]
        ct_and_tag = file_bytes[_NONCE_SIZE:]
        aes_key    = _derive_key(passphrase, nonce)

        try:
            aesgcm    = AESGCM(aes_key)
            plaintext = aesgcm.decrypt(nonce, ct_and_tag, None)
        except Exception as exc:
            raise ValueError("Failed to decrypt key file. Wrong passphrase or corrupt file.") from exc

        private_key = Ed25519PrivateKey.from_private_bytes(plaintext[:_RAW_KEY_SIZE])

        # Scrub plaintext
        for i in range(len(plaintext)):
            plaintext = plaintext  # bytes are immutable; just let GC clean up
        del plaintext
        del aes_key

        self._private_key = private_key
        self._public_key  = private_key.public_key()

    # ── Signing ───────────────────────────────────────────────────────────────

    def sign(self, payload: dict[str, Any]) -> str:
        """
        Sign a canonical JSON representation of *payload* with the loaded private key.

        The payload dict is serialised with sorted keys and no extra whitespace
        (deterministic JSON, compatible with the .NET reference implementation).

        Returns:
            A signature string in ``ed25519:<base64url>`` format.
        """
        if self._private_key is None:
            raise RuntimeError("Identity not loaded.")

        canonical = _canonical_json(payload)
        raw_sig   = self._private_key.sign(canonical.encode("utf-8"))
        return f"ed25519:{base64.urlsafe_b64encode(raw_sig).decode('ascii')}"

    # ── Verification ──────────────────────────────────────────────────────────

    @staticmethod
    def verify_signature(
        pub_key_str: str,
        payload: dict[str, Any],
        signature_str: str,
    ) -> bool:
        """
        Verify that *signature_str* (``ed25519:<base64url>``) is a valid
        Ed25519 signature over the canonical JSON of *payload* by the key
        encoded in *pub_key_str* (``ed25519:<base64url(DER)>``).

        Returns:
            True if valid, False if invalid (never raises on bad sig).
        """
        try:
            pub_key = NipIdentity._parse_pub_key(pub_key_str)
            raw_sig = _decode_sig(signature_str)
            canonical = _canonical_json(payload)
            pub_key.verify(raw_sig, canonical.encode("utf-8"))
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_pub_key(pub_key_str: str) -> Ed25519PublicKey:
        if not pub_key_str.startswith("ed25519:"):
            raise ValueError(f"Unsupported public key format: {pub_key_str!r}")
        der = base64.urlsafe_b64decode(pub_key_str[len("ed25519:"):] + "==")
        return Ed25519PublicKey.from_public_bytes(
            # DER SubjectPublicKeyInfo for Ed25519 has a 12-byte header before the 32-byte key
            der[-32:] if len(der) > 32 else der
        )

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _save_encrypted(
        private_key: Ed25519PrivateKey,
        file_path: str,
        passphrase: str,
    ) -> None:
        raw    = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        nonce      = os.urandom(_NONCE_SIZE)
        aes_key    = _derive_key(passphrase, nonce)
        aesgcm     = AESGCM(aes_key)
        # AESGCM.encrypt returns ciphertext + 16-byte authentication tag
        ct_and_tag = aesgcm.encrypt(nonce, raw, None)

        # File format: [nonce(12)] [ciphertext + tag(N+16)]
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(nonce + ct_and_tag)

        # Scrub
        del aes_key
        del raw


# ── Helpers ───────────────────────────────────────────────────────────────────

def _derive_key(passphrase: str, nonce: bytes) -> bytes:
    """Derive a 256-bit AES key from *passphrase* using PBKDF2-SHA256."""
    salt = nonce[:16]
    kdf  = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _canonical_json(payload: dict[str, Any]) -> str:
    """
    Serialise *payload* to canonical JSON (sorted keys, no whitespace).
    Compatible with the .NET reference implementation's JCS-style hashing.
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _decode_sig(sig_str: str) -> bytes:
    if not sig_str.startswith("ed25519:"):
        raise ValueError(f"Unsupported signature format: {sig_str!r}")
    return base64.urlsafe_b64decode(sig_str[len("ed25519:"):] + "==")


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
