"""Independent crypto consumption of frozen bytes, NOT a projection admission SDK.

No generic JCS serializer is claimed here: Python's json.dumps(sort_keys=True)
does not supply ECMAScript number rendering or UTF-16 property ordering.
"""
import base64
import hashlib
import json
from .conformance_fixtures import conformance_fixture

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


CORPUS = json.loads(conformance_fixture("alpha20", "context_crypto_vectors.json").read_text(encoding="utf-8"))


def decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@pytest.mark.parametrize("vector", CORPUS["vectors"], ids=lambda v: v["id"])
def test_frozen_context_cryptography(vector):
    public_key = load_der_public_key(bytes.fromhex(CORPUS["test_key"]["public_spki_der_hex"]))
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(CORPUS["test_key"]["seed_hex"]))
    request_bytes = vector["request_jcs"].encode("utf-8")
    assert json.loads(request_bytes) == vector["request"]
    digest = "sha256:" + hashlib.sha256(b"NPS-INVOKE-REQUEST/1\0" + request_bytes).hexdigest()
    assert digest == vector["request_digest"] == vector["payload"]["request_digest"]
    payload_bytes = vector["payload_jcs"].encode("utf-8")
    assert json.loads(payload_bytes) == vector["payload"]
    signature_input = b"NPS-CONTEXT-PROJECTION/1\0" + payload_bytes
    assert signature_input.hex() == vector["signature_input_hex"]
    signature = decode(vector["signature"])
    assert len(signature) == 64
    assert private_key.sign(signature_input) == signature
    public_key.verify(signature, signature_input)
    assert decode(vector["carrier_base64url"]).decode("utf-8") == vector["envelope_json"]
    assert json.loads(vector["envelope_json"]) == {"payload": vector["payload"], "signature": vector["signature"]}
    for wrong_input in (payload_bytes, b"NPS-CONTEXT-PROJECTION/1" + payload_bytes,
                        b"NPS-INVOKE-REQUEST/1\0" + payload_bytes, signature_input + b" "):
        with pytest.raises(InvalidSignature):
            public_key.verify(signature, wrong_input)
    with pytest.raises(InvalidSignature):
        public_key.verify(bytes([signature[0] ^ 1]) + signature[1:], signature_input)
