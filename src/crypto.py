"""Dashboard encryption, decryptable by browser WebCrypto.

Format: {"v":2, "salt":b64, "iv":b64, "iters":600000, "ct":b64}. v2 = the JSON is gzipped before encryption
(~6x smaller download); v1 = plain JSON (still decrypted by both sides).
Key: PBKDF2-HMAC-SHA256(passphrase, salt 16 B, 600,000 iterations) -> AES-256-GCM, IV 12 B, ct includes the tag.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VERSION = 2
ITERATIONS = 600_000
MIN_PASSPHRASE_LEN = 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def derive_key(passphrase: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_json(payload: Any, passphrase: str, *, salt: bytes | None = None, iv: bytes | None = None,
                 iterations: int = ITERATIONS, version: int = VERSION) -> dict[str, Any]:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise ValueError(f"DASHBOARD_PASSWORD must be at least {MIN_PASSPHRASE_LEN} characters")
    salt = salt or os.urandom(16)
    iv = iv or os.urandom(12)
    key = derive_key(passphrase, salt, iterations)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if version == 2:
        plaintext = gzip.compress(plaintext, compresslevel=9, mtime=0)
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    return {"v": version, "salt": _b64(salt), "iv": _b64(iv), "iters": iterations, "ct": _b64(ct)}


def decrypt_json(blob: dict[str, Any], passphrase: str) -> Any:
    if blob.get("v") not in (1, 2):
        raise ValueError(f"unsupported version {blob.get('v')}")
    salt, iv, ct = (base64.b64decode(blob[k]) for k in ("salt", "iv", "ct"))
    key = derive_key(passphrase, salt, int(blob["iters"]))
    plaintext = AESGCM(key).decrypt(iv, ct, None)
    return json.loads(gzip.decompress(plaintext) if blob["v"] == 2 else plaintext)
