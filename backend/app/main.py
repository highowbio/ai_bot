"""FastAPI entry point for the Mini App backend.

Runs the Telegram bot (polling) alongside the HTTP API in the same process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app import access, codec, view
from app.auth import require_allowed_user
from app.bot import run_bot
from app.config import CORS_ORIGINS, MAX_FILE_SIZE, RUN_BOT

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ai_bot.api")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    bot_task: asyncio.Task | None = None
    if RUN_BOT:
        bot_task = asyncio.create_task(run_bot(), name="telegram-bot")
    try:
        yield
    finally:
        if bot_task:
            bot_task.cancel()
            with contextlib.suppress(BaseException):
                await bot_task


app = FastAPI(title="AI Bot Mini App API", lifespan=lifespan)

# CORS — permissive by default so the Mini App can reach the API from any
# devinapps.com subdomain; tighten via ``CORS_ORIGINS`` or ``FRONTEND_URL``.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/whoami")
async def whoami(user=Depends(require_allowed_user)) -> dict:
    uid = int(user["id"])
    return {
        "id": uid,
        "is_admin": access.is_admin(uid),
        "user": user,
    }


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Файл пустой")
    if len(data) > MAX_FILE_SIZE:
        limit_mb = MAX_FILE_SIZE // 1024 // 1024
        raise HTTPException(413, f"Файл больше {limit_mb} МБ")
    return data


def _file_response(result: bytes, status: str, original_name: str) -> Response:
    filename = f"decoded_{original_name or 'file.bin'}"
    # ASCII-safe Content-Disposition: filename*=UTF-8''... handles Cyrillic.
    from urllib.parse import quote

    safe = quote(filename)
    return Response(
        content=result,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"file.bin\"; filename*=UTF-8''{safe}",
            "X-Decode-Status": status,
            "X-Filename": safe,
        },
    )


@app.post("/api/netcfg/decrypt")
async def api_netcfg_decrypt(
    file: UploadFile = File(...),
    user=Depends(require_allowed_user),
) -> Response:
    data = await _read_upload(file)
    result, status = codec.decode_netcfg(data)
    if status == "too_short":
        raise HTTPException(422, "Файл слишком мал для NETCFG")
    if result is None:
        raise HTTPException(422, "Не удалось расшифровать NETCFG")
    return _file_response(result, status, file.filename or "netcfg.bin")


@app.post("/api/mxcfg/decrypt")
async def api_mxcfg_decrypt(
    file: UploadFile = File(...),
    user=Depends(require_allowed_user),
) -> Response:
    data = await _read_upload(file)
    result, status = codec.decode_mxcfg_bytes(data)
    if status in ("empty", "error"):
        raise HTTPException(422, "Файл пустой или повреждён")
    if result is None:
        raise HTTPException(422, "Не удалось расшифровать MXCFG")
    return _file_response(result, status, file.filename or "mxcfg.bin")


@app.post("/api/mxcfg/view")
async def api_mxcfg_view(
    file: UploadFile = File(...),
    user=Depends(require_allowed_user),
) -> dict:
    data = await _read_upload(file)
    result, status = codec.decode_mxcfg_bytes(data)
    if status in ("empty", "error"):
        raise HTTPException(422, "Файл пустой или повреждён")
    if result is None:
        raise HTTPException(422, "Не удалось прочитать MXCFG")
    try:
        parsed = json.loads(result.decode("utf-8", errors="ignore"))
    except Exception as exc:
        raise HTTPException(422, f"Расшифровано, но это не JSON: {exc}")
    return {
        "status": status,
        "raw": parsed,
        "html": view.pretty_mxcfg_view(parsed),
    }
