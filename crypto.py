"""Pure decoding/encryption helpers for NETCFG and MXCFG files.

These functions have no side effects and no I/O so they are trivial to unit
test. They are intentionally kept free of Telegram-specific concerns.
"""

from __future__ import annotations

import json
from base64 import b64decode

NETCFG_KEY: bytes = b"2yHBg"

MXCFG_KEYS: tuple[bytes, ...] = (
    b"xR9#vL2@mK7!pQ4$nW6^jT8&",
    b"Mx!Cl#2026$Pr0tect^Key&Adv",
    b"MerixtiClumsy2025!@#SecretKey",
)


def xor_decrypt(data: bytes, key: bytes) -> bytes:
    """Apply a repeating-key XOR to ``data``."""
    if not key:
        raise ValueError("key must not be empty")
    klen = len(key)
    return bytes(b ^ key[i % klen] for i, b in enumerate(data))


def fix_encrypted_flag(text: str) -> str:
    """Force ``"encrypted": false`` in the given JSON-ish text."""
    return text.replace('"encrypted": true', '"encrypted": false').replace(
        '"encrypted":true', '"encrypted":false'
    )


def decode_netcfg(data: bytes) -> tuple[bytes | None, str]:
    """Decode a NETCFG file.

    Returns a tuple ``(decoded_bytes, status)`` where ``status`` is one of:
      * ``"decoded"``  — the file was successfully decrypted
      * ``"already"``  — the file was already decrypted
      * ``"too_short"`` — the file is too small to be a valid NETCFG
      * ``"unknown"``  — the file could not be recognised / decrypted
    """
    if len(data) < 2:
        return None, "too_short"

    if data[:2] == b"\x01\x00":
        return data, "already"

    payload = data[2:] if data[:2] == b"\x01\x01" else data

    if not payload:
        return None, "too_short"

    decrypted = xor_decrypt(payload, NETCFG_KEY)

    try:
        text = decrypted.decode("utf-8")
    except UnicodeDecodeError:
        return None, "unknown"

    if text.lstrip().startswith("{"):
        return b"\x01\x00" + decrypted, "decoded"

    return None, "unknown"


def decode_mxcfg_bytes(data: bytes) -> tuple[bytes | None, str]:
    """Decode an MXCFG file.

    Returns a tuple ``(decoded_bytes, status)`` where ``status`` is one of:
      * ``"decoded"``  — the file was successfully decrypted
      * ``"already"``  — the file was already decrypted
      * ``"empty"``    — the payload is empty
      * ``"error"``    — the payload could not be processed at all
      * ``"unknown"``  — no known MXCFG key matched
    """
    if not data:
        return None, "empty"

    try:
        text = data.decode("utf-8", errors="ignore").strip()
    except Exception:
        return None, "error"

    if text.startswith("{"):
        try:
            js = json.loads(text)
            js["encrypted"] = False
            return json.dumps(js, indent=2, ensure_ascii=False).encode("utf-8"), "already"
        except Exception:
            return fix_encrypted_flag(text).encode("utf-8"), "already"

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
        try:
            decrypted = xor_decrypt(raw, key).decode("utf-8", errors="ignore")
        except Exception:
            continue

        if not decrypted.lstrip().startswith("{"):
            continue

        # Require valid JSON so we don't accept garbage from a wrong key that
        # happens to start with "{" by coincidence.
        try:
            js = json.loads(decrypted)
        except Exception:
            continue

        js["encrypted"] = False
        return json.dumps(js, indent=2, ensure_ascii=False).encode("utf-8"), "decoded"

    return None, "unknown"
