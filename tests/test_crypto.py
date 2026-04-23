"""Tests for the pure decoding helpers in crypto.py."""

from __future__ import annotations

import json
from base64 import b64encode

import pytest

from crypto import (
    MXCFG_KEYS,
    NETCFG_KEY,
    decode_mxcfg_bytes,
    decode_netcfg,
    fix_encrypted_flag,
    xor_decrypt,
)

# ---------------------------------------------------------------------------
# xor_decrypt
# ---------------------------------------------------------------------------


def test_xor_decrypt_is_involutive():
    data = b"hello world"
    key = b"secret"
    assert xor_decrypt(xor_decrypt(data, key), key) == data


def test_xor_decrypt_empty_data():
    assert xor_decrypt(b"", b"key") == b""


def test_xor_decrypt_empty_key_raises():
    with pytest.raises(ValueError):
        xor_decrypt(b"abc", b"")


def test_xor_decrypt_single_byte_key():
    # XOR with a single-byte key should flip the same bit pattern everywhere.
    assert xor_decrypt(b"\x00\x01\x02", b"\xff") == b"\xff\xfe\xfd"


# ---------------------------------------------------------------------------
# fix_encrypted_flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('{"encrypted": true}', '{"encrypted": false}'),
        ('{"encrypted":true}', '{"encrypted":false}'),
        ('{"encrypted": false}', '{"encrypted": false}'),
        ('{"other": true}', '{"other": true}'),
    ],
)
def test_fix_encrypted_flag(source: str, expected: str):
    assert fix_encrypted_flag(source) == expected


# ---------------------------------------------------------------------------
# decode_netcfg
# ---------------------------------------------------------------------------


def _build_encrypted_netcfg(plaintext: bytes) -> bytes:
    return b"\x01\x01" + xor_decrypt(plaintext, NETCFG_KEY)


def test_decode_netcfg_decodes_encrypted_payload():
    plaintext = b'{"foo": "bar"}'
    encrypted = _build_encrypted_netcfg(plaintext)

    result, status = decode_netcfg(encrypted)

    assert status == "decoded"
    assert result is not None
    assert result.startswith(b"\x01\x00")
    assert result[2:] == plaintext


def test_decode_netcfg_already_decoded_is_passthrough():
    data = b'\x01\x00{"hello": 1}'
    result, status = decode_netcfg(data)
    assert status == "already"
    assert result == data


def test_decode_netcfg_too_short():
    assert decode_netcfg(b"") == (None, "too_short")
    assert decode_netcfg(b"\x01") == (None, "too_short")
    assert decode_netcfg(b"\x01\x01") == (None, "too_short")


def test_decode_netcfg_without_prefix_also_tries_xor():
    plaintext = b'{"a": 1}'
    encrypted_no_prefix = xor_decrypt(plaintext, NETCFG_KEY)
    result, status = decode_netcfg(encrypted_no_prefix)
    assert status == "decoded"
    assert result == b"\x01\x00" + plaintext


def test_decode_netcfg_garbage_returns_unknown():
    result, status = decode_netcfg(b"\x01\x01garbage-not-json")
    assert result is None
    assert status == "unknown"


# ---------------------------------------------------------------------------
# decode_mxcfg_bytes
# ---------------------------------------------------------------------------


def _build_mxcfg_blob(plain_json: str, key: bytes) -> bytes:
    encrypted = xor_decrypt(plain_json.encode("utf-8"), key)
    encoded = b64encode(encrypted).decode("ascii")
    return f"MXCFG:{encoded}".encode()


def test_decode_mxcfg_empty():
    assert decode_mxcfg_bytes(b"") == (None, "empty")


def test_decode_mxcfg_already_plain_json_forces_flag_false():
    data = json.dumps({"author": "me", "encrypted": True}).encode("utf-8")
    result, status = decode_mxcfg_bytes(data)
    assert status == "already"
    assert result is not None
    parsed = json.loads(result)
    assert parsed["encrypted"] is False
    assert parsed["author"] == "me"


def test_decode_mxcfg_already_plain_non_strict_json_still_fixed():
    data = b'{"encrypted":true, oops-not-json}'
    result, status = decode_mxcfg_bytes(data)
    assert status == "already"
    assert result is not None
    assert b'"encrypted":false' in result


def test_decode_mxcfg_without_marker_returns_unknown():
    assert decode_mxcfg_bytes(b"just some random text") == (None, "unknown")


@pytest.mark.parametrize("key", MXCFG_KEYS)
def test_decode_mxcfg_with_each_known_key(key: bytes):
    blob = _build_mxcfg_blob(json.dumps({"author": "auth", "encrypted": True}), key)

    result, status = decode_mxcfg_bytes(blob)

    assert status == "decoded"
    assert result is not None
    parsed = json.loads(result)
    assert parsed["encrypted"] is False
    assert parsed["author"] == "auth"


def test_decode_mxcfg_with_unknown_key_returns_unknown():
    wrong_key = b"definitely-not-a-known-key"
    blob = _build_mxcfg_blob(json.dumps({"author": "x", "encrypted": True}), wrong_key)
    result, status = decode_mxcfg_bytes(blob)
    assert result is None
    assert status == "unknown"


def test_decode_mxcfg_bad_base64_returns_unknown():
    assert decode_mxcfg_bytes(b"MXCFG:!!!not-base64!!!") == (None, "unknown")
