# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""CA key management and certificate issuance for NIP CA Server."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes, serialization

PBKDF2_ITERS = 600_000
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_LEN,
                     salt=salt, iterations=PBKDF2_ITERS)
    return kdf.derive(passphrase.encode())


def _raw_private_bytes(priv: Ed25519PrivateKey) -> bytes:
    """Return the 32-byte seed."""
    return priv.private_bytes(serialization.Encoding.Raw,
                               serialization.PrivateFormat.Raw,
                               serialization.NoEncryption())


def _raw_public_bytes(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def pub_key_string(pub: Ed25519PublicKey) -> str:
    return "ed25519:" + _raw_public_bytes(pub).hex()


def save_key(priv: Ed25519PrivateKey, path: str, passphrase: str) -> None:
    salt = secrets.token_bytes(SALT_LEN)
    nonce = secrets.token_bytes(NONCE_LEN)
    dk = _derive_key(passphrase, salt)
    plaintext = _raw_private_bytes(priv)
    ciphertext = AESGCM(dk).encrypt(nonce, plaintext, None)
    envelope = {
        "version": 1,
        "algorithm": "ed25519",
        "pub_key": pub_key_string(priv.public_key()),
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(envelope, f)
    os.chmod(path, 0o600)


def load_key(path: str, passphrase: str) -> Ed25519PrivateKey:
    with open(path) as f:
        env = json.load(f)
    salt = bytes.fromhex(env["salt"])
    nonce = bytes.fromhex(env["nonce"])
    ct = bytes.fromhex(env["ciphertext"])
    dk = _derive_key(passphrase, salt)
    try:
        seed = AESGCM(dk).decrypt(nonce, ct, None)
    except Exception as exc:
        raise ValueError("Key decryption failed — wrong passphrase?") from exc
    return Ed25519PrivateKey.from_private_bytes(seed)


def _canonical_json(d: dict) -> bytes:
    return json.dumps(
        {k: d[k] for k in sorted(d)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def sign_dict(priv: Ed25519PrivateKey, d: dict) -> str:
    """Sign a dict (canonical JSON, sorted keys) and return 'ed25519:<b64>'."""
    sig = priv.sign(_canonical_json(d))
    return "ed25519:" + base64.b64encode(sig).decode()


def issue_cert(
    priv: Ed25519PrivateKey,
    ca_nid: str,
    subject_nid: str,
    subject_pub_key: str,
    entity_type: str,
    capabilities: list[str],
    scope: dict,
    validity_days: int,
    serial: str,
    metadata: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=validity_days)
    unsigned = {
        "capabilities": capabilities,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_by": ca_nid,
        "nid": subject_nid,
        "pub_key": subject_pub_key,
        "scope": scope,
        "serial": serial,
    }
    signature = sign_dict(priv, unsigned)
    cert = {**unsigned, "signature": signature}
    if metadata:
        cert["metadata"] = metadata
    return cert


def generate_nid(domain: str, entity_type: str) -> str:
    uid = secrets.token_hex(8)
    return f"urn:nps:{entity_type}:{domain}:{uid}"
