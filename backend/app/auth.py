"""Telegram WebApp ``initData`` validation.

See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from app import access
from app.config import BOT_TOKEN, INIT_DATA_TTL


def _compute_hash(bot_token: str, data_check_string: str) -> str:
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    return hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()


def validate_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    if not init_data:
        raise ValueError("empty init_data")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("missing hash")
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(pairs.items())
    )
    computed = _compute_hash(bot_token, data_check_string)
    if not hmac.compare_digest(computed, received_hash):
        raise ValueError("bad hash")

    auth_date_raw = pairs.get("auth_date", "0")
    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        raise ValueError("bad auth_date")
    if INIT_DATA_TTL > 0 and auth_date:
        if time.time() - auth_date > INIT_DATA_TTL:
            raise ValueError("init_data expired")

    user_raw = pairs.get("user", "")
    if not user_raw:
        raise ValueError("missing user")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        raise ValueError("bad user json")
    if "id" not in user:
        raise ValueError("user has no id")
    return user


async def require_allowed_user(
    x_telegram_init_data: str = Header(
        default="", alias="X-Telegram-Init-Data"
    ),
) -> dict[str, Any]:
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN not configured")
    try:
        user = validate_init_data(x_telegram_init_data, BOT_TOKEN)
    except ValueError as exc:
        raise HTTPException(401, f"Invalid Telegram auth: {exc}") from None

    try:
        uid = int(user["id"])
    except (TypeError, ValueError):
        raise HTTPException(401, "Invalid user id")

    if not access.is_allowed(uid):
        raise HTTPException(403, "Доступ запрещён — свяжитесь с администратором.")

    return user
