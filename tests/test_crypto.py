from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from src.crypto import decrypt_json, encrypt_json

VECTOR = Path(__file__).parent / "vectors" / "crypto_vector.json"
PASS = "correct horse battery staple"


def test_round_trip():
    payload = {"jobs": [{"title": "SRE", "company": "Acme"}], "n": 1, "unicode": "Bengaluru · ₹"}
    blob = encrypt_json(payload, PASS, iterations=1000)
    assert set(blob) == {"v", "salt", "iv", "iters", "ct"}
    assert decrypt_json(blob, PASS) == payload


def test_random_salt_and_iv_each_time():
    a, b = encrypt_json({"x": 1}, PASS, iterations=1000), encrypt_json({"x": 1}, PASS, iterations=1000)
    assert a["salt"] != b["salt"] and a["iv"] != b["iv"] and a["ct"] != b["ct"]
    assert len(base64.b64decode(a["salt"])) == 16 and len(base64.b64decode(a["iv"])) == 12


def test_wrong_password_fails():
    blob = encrypt_json({"x": 1}, PASS, iterations=1000)
    with pytest.raises(Exception):
        decrypt_json(blob, "wrong password here")


def test_short_password_rejected():
    with pytest.raises(ValueError):
        encrypt_json({}, "short")


def test_default_iterations_and_shared_vector():
    """The JS test (tests/js/crypto.test.mjs) decrypts this same vector with WebCrypto."""
    vec = json.loads(VECTOR.read_text())
    assert vec["blob"]["iters"] == 600_000
    assert decrypt_json(vec["blob"], vec["password"]) == vec["plaintext"]
    # Deterministic salt/iv reproduce the exact ciphertext.
    again = encrypt_json(vec["plaintext"], vec["password"], salt=base64.b64decode(vec["blob"]["salt"]),
                         iv=base64.b64decode(vec["blob"]["iv"]), version=1)
    assert again == vec["blob"]


def test_v2_is_gzipped_and_round_trips():
    payload = {"jobs": [{"title": "DevOps Engineer", "desc": "kubernetes " * 200}]}
    v1 = encrypt_json(payload, "a long enough passphrase", version=1)
    v2 = encrypt_json(payload, "a long enough passphrase")
    assert v2["v"] == 2 and len(v2["ct"]) < len(v1["ct"]) / 5
    assert decrypt_json(v2, "a long enough passphrase") == payload
