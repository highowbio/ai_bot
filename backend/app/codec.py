"""Pure decoding logic for NETCFG / MXCFG configuration files.

Copied from the legacy main.py with stricter UTF-8 handling.
"""

from __future__ import annotations

import json
from base64 import b64decode
from typing import Tuple

NETCFG_KEY = b"2yHBg"

MXCFG_KEYS: list[bytes] = [
    b"xR9#vL2@mK7!pQ4$nW6^jT8&",
    b"Mx!Cl#2026$Pr0tect^Key&Adv",
    b"MerixtiClumsy2025!@#SecretKey",
]


def xor_decrypt(data: bytes, key: bytes) -> bytes:
    klen = len(key)
    return bytes(b ^ key[i % klen] for i, b in enumerate(data))


def _normalize_encrypted_flag(payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    if not text.lstrip().startswith("{"):
        return payload
    try:
        js = json.loads(text)
        if isinstance(js, dict):
            js["encrypted"] = False
            return json.dumps(js, indent=2, ensure_ascii=False).encode("utf-8")
    except Exception:
        pass
    return (
        text.replace('"encrypted": true', '"encrypted": false')
        .replace('"encrypted":true', '"encrypted":false')
        .encode("utf-8")
    )


def decode_netcfg(data: bytes) -> Tuple[bytes | None, str]:
    if len(data) < 2:
        return None, "too_short"

    if data[:2] == b"\x01\x00":
        normalized = _normalize_encrypted_flag(data[2:])
        return b"\x01\x00" + normalized, "already"

    payload = data[2:] if data[:2] == b"\x01\x01" else data
    if not payload:
        return None, "too_short"

    decrypted = xor_decrypt(payload, NETCFG_KEY)
    try:
        text = decrypted.decode("utf-8")
    except UnicodeDecodeError:
        return None, "unknown"
    if not text.lstrip().startswith("{"):
        return None, "unknown"

    return b"\x01\x00" + _normalize_encrypted_flag(decrypted), "decoded"


def decode_mxcfg_bytes(data: bytes) -> Tuple[bytes | None, str]:
    if not data:
        return None, "empty"

    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="ignore").strip()

    if text.startswith("{"):
        try:
            js = json.loads(text)
            if isinstance(js, dict):
                js["encrypted"] = False
                return (
                    json.dumps(js, indent=2, ensure_ascii=False).encode("utf-8"),
                    "already",
                )
        except Exception:
            pass
        return _normalize_encrypted_flag(text.encode("utf-8")), "already"

    if "MXCFG" not in text:
        return None, "unknown"

    try:
        encoded = text.split(":", 1)[1].strip()
        raw = b64decode(encoded)
    except Exception:
        return None, "unknown"

    if not raw:
        return None, "unknown"

    for key in MXCFG_KEYS:
        decrypted_bytes = xor_decrypt(raw, key)
        try:
            decrypted = decrypted_bytes.decode("utf-8")  # strict
        except UnicodeDecodeError:
            continue
        if not decrypted.lstrip().startswith("{"):
            continue
        return _normalize_encrypted_flag(decrypted.encode("utf-8")), "decoded"

    return None, "unknown"
