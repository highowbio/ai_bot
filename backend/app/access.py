"""Shared whitelist / admin access management."""

from __future__ import annotations

import asyncio
import json
import logging

from app.config import ADMIN_IDS, WHITELIST_FILE

logger = logging.getLogger("ai_bot.access")

_lock = asyncio.Lock()


def _load() -> set[int]:
    if not WHITELIST_FILE.exists():
        return set()
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except Exception:
        logger.warning("Не удалось прочитать %s", WHITELIST_FILE)
        return set()


def _save(wl: set[int]) -> None:
    WHITELIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WHITELIST_FILE.write_text(
        json.dumps(sorted(wl), indent=2), encoding="utf-8"
    )


_whitelist: set[int] = _load()


def is_allowed(uid: int) -> bool:
    return uid in ADMIN_IDS or uid in _whitelist


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def snapshot() -> dict:
    return {
        "admins": sorted(ADMIN_IDS),
        "whitelist": sorted(_whitelist),
    }


async def add_user(uid: int) -> bool:
    async with _lock:
        if uid in ADMIN_IDS or uid in _whitelist:
            return False
        _whitelist.add(uid)
        _save(_whitelist)
    return True


async def remove_user(uid: int) -> bool:
    async with _lock:
        if uid not in _whitelist:
            return False
        _whitelist.discard(uid)
        _save(_whitelist)
    return True
