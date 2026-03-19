#!/usr/bin/env python3
"""
Crypto utilities — encrypt/decrypt REE receipts using the wallet private key.

Flow:
  - Derive a 32-byte AES key from the wallet private key via SHA-256
  - Encrypt receipt JSON with AES-256-GCM (authenticated encryption)
  - Upload the encrypted blob to IPFS
  - Only the private key holder can decrypt and verify

pip install pycryptodome
"""

import json
import hashlib
import base64
import os

try:
    from Crypto.Cipher import AES
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


DECRYPT_MESSAGE = "REEcord:v1:decrypt"


def _derive_key(private_key: str) -> bytes:
    """
    Derive a 32-byte AES key by signing a known message with the wallet key.
    Compatible with MetaMask personal_sign in the browser.
    """
    from eth_account.messages import encode_defunct
    from eth_account import Account
    msg    = encode_defunct(text=DECRYPT_MESSAGE)
    signed = Account.sign_message(msg, private_key=private_key)
    return hashlib.sha256(signed.signature).digest()


def encrypt_receipt(receipt: dict, private_key: str) -> dict:
    """
    Encrypt receipt JSON with AES-256-GCM.
    Returns a dict with: ciphertext, nonce, tag (all base64 encoded).
    Raises ImportError if pycryptodome is not installed.
    """
    if not _CRYPTO_AVAILABLE:
        raise ImportError("Run: pip install pycryptodome")

    key   = _derive_key(private_key)
    nonce = os.urandom(16)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)

    return {
        "encrypted": True,
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "nonce":      base64.b64encode(nonce).decode(),
        "tag":        base64.b64encode(tag).decode(),
    }


def decrypt_receipt(encrypted_blob: dict, private_key: str) -> dict:
    """
    Decrypt an encrypted receipt blob.
    Returns the original receipt dict.
    """
    if not _CRYPTO_AVAILABLE:
        raise ImportError("Run: pip install pycryptodome")

    if not encrypted_blob.get("encrypted"):
        return encrypted_blob  # already plaintext

    key        = _derive_key(private_key)
    nonce      = base64.b64decode(encrypted_blob["nonce"])
    ciphertext = base64.b64decode(encrypted_blob["ciphertext"])
    tag        = base64.b64decode(encrypted_blob["tag"])

    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return json.loads(plaintext.decode("utf-8"))
