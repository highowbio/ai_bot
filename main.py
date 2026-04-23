"""Telegram bot for decoding NETCFG / MXCFG configuration files."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ai_bot")


def _parse_ids(value: str) -> set[int]:
    out: set[int] = set()
    for chunk in value.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk and chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

ADMIN_IDS: set[int] = _parse_ids(
    os.environ.get("BOT_ADMIN_IDS", "6903588929,6734219400")
)

WHITELIST_FILE = Path(
    os.environ.get("WHITELIST_FILE") or (Path(__file__).parent / "whitelist.json")
)

NETCFG_KEY = b"2yHBg"

MXCFG_KEYS: list[bytes] = [
    b"xR9#vL2@mK7!pQ4$nW6^jT8&",
    b"Mx!Cl#2026$Pr0tect^Key&Adv",
    b"MerixtiClumsy2025!@#SecretKey",
]

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MiB
MAX_TEXT_BLOCK = 3800             # Telegram message safety cap

# Per-user state keys inside context.user_data
STATE_ACTION = "action"
STATE_MODE = "mode"

# --------------------------------------------------------------------------- #
# Whitelist persistence
# --------------------------------------------------------------------------- #

_whitelist_lock = asyncio.Lock()


def _load_whitelist() -> set[int]:
    if not WHITELIST_FILE.exists():
        return set()
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except Exception:
        logger.warning("Не удалось прочитать %s", WHITELIST_FILE)
        return set()


def _save_whitelist(wl: set[int]) -> None:
    WHITELIST_FILE.write_text(
        json.dumps(sorted(wl), indent=2), encoding="utf-8"
    )


whitelist: set[int] = _load_whitelist()


def is_allowed(uid: int) -> bool:
    return uid in ADMIN_IDS or uid in whitelist


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# --------------------------------------------------------------------------- #
# Per-user state helpers
# --------------------------------------------------------------------------- #


def get_action(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return (context.user_data or {}).get(STATE_ACTION)


def get_mode(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return (context.user_data or {}).get(STATE_MODE)


def set_action(context: ContextTypes.DEFAULT_TYPE, action: str | None) -> None:
    if context.user_data is None:
        return
    if action is None:
        context.user_data.pop(STATE_ACTION, None)
    else:
        context.user_data[STATE_ACTION] = action


def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str | None) -> None:
    if context.user_data is None:
        return
    if mode is None:
        context.user_data.pop(STATE_MODE, None)
    else:
        context.user_data[STATE_MODE] = mode


def reset_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    set_action(context, None)
    set_mode(context, None)


# --------------------------------------------------------------------------- #
# Keyboards / UI text
# --------------------------------------------------------------------------- #

DIVIDER = "━━━━━━━━━━━━━━━━━━"

MAIN_TITLE = (
    "👋 <b>AI Bot · декодер конфигов</b>\n"
    f"<i>{DIVIDER}</i>\n"
    "Что я умею:\n"
    "🔓 Дешифровать <b>NETCFG</b> и <b>MXCFG</b>\n"
    "👁 Красиво показать содержимое <b>MXCFG</b>\n\n"
    "Выбери режим работы ниже 👇"
)

ACCESS_DENIED_TEMPLATE = (
    "⛔ <b>Нет доступа</b>\n"
    f"<i>{DIVIDER}</i>\n"
    "Этот бот закрытый. Попроси администратора добавить тебя.\n\n"
    "🆔 Твой Telegram ID: <code>{uid}</code>"
)


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔓 Дешифровать", callback_data="action:decrypt"),
                InlineKeyboardButton("👁 Просмотр", callback_data="action:view"),
            ],
            [InlineKeyboardButton("📖 Справка", callback_data="nav:help")],
        ]
    )


def kb_decrypt_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📄 NETCFG", callback_data="mode:netcfg"),
                InlineKeyboardButton("📄 MXCFG", callback_data="mode:mxcfg"),
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="back:main")],
        ]
    )


def kb_view_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 MXCFG", callback_data="mode:mxcfg")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back:main")],
        ]
    )


def kb_awaiting_file() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отменить", callback_data="back:main")]]
    )


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 В главное меню", callback_data="back:main")]]
    )


def esc(value: Any) -> str:
    """HTML-escape user-controlled or JSON values for HTML parse mode."""
    return html.escape(str(value), quote=False)


# --------------------------------------------------------------------------- #
# Message helpers
# --------------------------------------------------------------------------- #


async def safe_edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    except BadRequest as e:
        err = str(e).lower()
        if any(
            s in err
            for s in (
                "there is no text",
                "message can't be edited",
                "message is not modified",
                "message to edit not found",
            )
        ):
            await query.message.reply_text(
                text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        else:
            raise


async def send_long_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    chat_id = update.effective_chat.id
    if len(text) <= MAX_TEXT_BLOCK:
        await context.bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back_main(),
        )
        return

    buf = BytesIO(text.encode("utf-8"))
    await context.bot.send_document(
        chat_id,
        document=buf,
        filename="mxcfg_info.txt",
        caption="📄 Результат слишком большой — отправляю документом.",
        reply_markup=kb_back_main(),
    )


# --------------------------------------------------------------------------- #
# Decoding
# --------------------------------------------------------------------------- #


def xor_decrypt(data: bytes, key: bytes) -> bytes:
    klen = len(key)
    return bytes(b ^ key[i % klen] for i, b in enumerate(data))


def _normalize_encrypted_flag(payload: bytes) -> bytes:
    """If payload is JSON with `encrypted: true`, flip it to false."""
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


def decode_netcfg(data: bytes) -> tuple[bytes | None, str]:
    if len(data) < 2:
        return None, "too_short"

    if data[:2] == b"\x01\x00":
        # Already decoded — normalize the `encrypted` flag anyway.
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


def decode_mxcfg_bytes(data: bytes) -> tuple[bytes | None, str]:
    if not data:
        return None, "empty"

    # Try to read as text first.
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


# --------------------------------------------------------------------------- #
# Pretty view for MXCFG
# --------------------------------------------------------------------------- #


def render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "✅ включено" if value else "❌ выключено"
    if value is None:
        return "—"
    if isinstance(value, str):
        return f'"{esc(value)}"'
    if isinstance(value, (int, float)):
        return esc(value)
    return esc(value)


def pretty_mxcfg_view(parsed: dict) -> str:
    top_labels = {
        "author":      "👤 Автор",
        "description": "📝 Описание",
        "encrypted":   "🔐 Шифрование",
        "AfterDur":    "⏱ После таймера",
        "scriptMode":  "⚙️ Режим скрипта",
        "data":        "🌐 Имитация сети",
        "steps":       "📋 Шаги",
        "onStop":      "🛑 При остановке",
    }
    data_labels = {
        "ZaderPC": "Задержка пакетов клиента",
        "DeletPC": "Удаление пакетов клиента",
        "ZaderPS": "Задержка пакетов сервера",
        "DeletPS": "Удаление пакетов сервера",
        "Auto":    "Автоотключение",
    }
    step_labels = {
        "d":       "Задержка",
        "dp":      "Удаление пакетов",
        "sd":      "Задержка серверных пакетов",
        "sdp":     "Удаление серверных пакетов",
        "dur":     "Длительность",
        "drainC":  "Пакеты клиента",
        "drainCD": "Задержка пакетов клиента",
        "drainS":  "Пакеты сервера",
        "drainSD": "Задержка пакетов сервера",
        "szM":     "Режим размера",
        "szMin":   "Мин. размер",
        "szMax":   "Макс. размер",
    }

    lines: list[str] = [
        "📂 <b>Содержимое MXCFG</b>",
        f"<i>{DIVIDER}</i>",
    ]

    def add(label: str, value: Any) -> None:
        lines.append(f"<b>{label}:</b> {render_scalar(value)}")

    for key in ("author", "description", "encrypted"):
        if key in parsed:
            add(top_labels[key], parsed[key])

    after_key = next((k for k in ("AfterDur", "afterDur") if k in parsed), None)
    if after_key is not None:
        lines.append(
            f"<b>{top_labels['AfterDur']}:</b> {esc(parsed[after_key])} мс"
        )

    if "scriptMode" in parsed:
        add(top_labels["scriptMode"], parsed["scriptMode"])

    if isinstance(parsed.get("data"), dict):
        lines += ["", f"<b>{top_labels['data']}</b>"]
        for k, v in parsed["data"].items():
            lines.append(f"  • {esc(data_labels.get(k, k))}: {render_scalar(v)}")

    if isinstance(parsed.get("steps"), list):
        total = len(parsed["steps"])
        lines += ["", f"<b>{top_labels['steps']}</b> <i>(всего: {total})</i>"]
        for i, step in enumerate(parsed["steps"], 1):
            if not isinstance(step, dict):
                lines.append(f"  • Шаг {i}: {render_scalar(step)}")
                continue
            lines.append(f"  <b>Шаг {i}:</b>")
            for k, v in step.items():
                lines.append(
                    f"    — {esc(step_labels.get(k, k))}: {render_scalar(v)}"
                )

    if "onStop" in parsed:
        lines.append("")
        add(top_labels["onStop"], parsed["onStop"])

    known = {
        "author", "description", "encrypted", "AfterDur", "afterDur",
        "scriptMode", "data", "steps", "onStop",
    }
    unknown = [k for k in parsed if k not in known]
    if unknown:
        lines += ["", "<b>📌 Дополнительные поля</b>"]
        for k in unknown:
            lines.append(f"  • {esc(k)}: {render_scalar(parsed[k])}")

    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id
    if not is_allowed(uid):
        await message.reply_text(
            ACCESS_DENIED_TEMPLATE.format(uid=uid),
            parse_mode=ParseMode.HTML,
        )
        return

    reset_state(context)
    await message.reply_text(
        MAIN_TITLE,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(),
    )


def _build_help_text(uid: int) -> str:
    text = (
        "📖 <b>Справка</b>\n"
        f"<i>{DIVIDER}</i>\n"
        "<b>Режимы:</b>\n"
        "🔓 <b>Дешифровать</b> — NETCFG или MXCFG → декодированный файл\n"
        "👁 <b>Просмотр</b> — MXCFG → красиво отформатированное содержимое\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/help — эта справка\n"
        "/myid — показать ваш Telegram ID\n"
        "/cancel — отменить текущее действие\n"
    )
    if is_admin(uid):
        text += (
            "\n<b>🔧 Команды администратора</b>\n"
            "/adduser <code>&lt;id&gt;</code> — добавить пользователя\n"
            "/removeuser <code>&lt;id&gt;</code> — удалить пользователя\n"
            "/users — список пользователей\n"
        )
    return text


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id
    if not is_allowed(uid):
        await message.reply_text(
            ACCESS_DENIED_TEMPLATE.format(uid=uid),
            parse_mode=ParseMode.HTML,
        )
        return
    await message.reply_text(
        _build_help_text(uid),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_back_main(),
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    username = f"@{user.username}" if user.username else "—"
    await message.reply_text(
        "🆔 <b>Ваш профиль</b>\n"
        f"<i>{DIVIDER}</i>\n"
        f"<b>ID:</b> <code>{user.id}</code>\n"
        f"<b>Имя:</b> {esc(user.full_name)}\n"
        f"<b>Username:</b> {esc(username)}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not is_allowed(user.id):
        return
    reset_state(context)
    await message.reply_text(
        "✅ Действие отменено.",
        reply_markup=kb_main(),
    )


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id
    if not is_admin(uid):
        await message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await message.reply_text(
            "Использование: <code>/adduser &lt;telegram_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target = int(context.args[0])
    if target in ADMIN_IDS:
        await message.reply_text("ℹ️ Этот пользователь уже администратор.")
        return

    async with _whitelist_lock:
        if target in whitelist:
            await message.reply_text(
                f"ℹ️ Пользователь <code>{target}</code> уже в списке.",
                parse_mode=ParseMode.HTML,
            )
            return
        whitelist.add(target)
        _save_whitelist(whitelist)

    logger.info("Admin %s added user %s", uid, target)
    await message.reply_text(
        f"✅ Пользователь <code>{target}</code> добавлен.\n"
        f"Всего в белом списке: <b>{len(whitelist)}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id
    if not is_admin(uid):
        await message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await message.reply_text(
            "Использование: <code>/removeuser &lt;telegram_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target = int(context.args[0])
    if target in ADMIN_IDS:
        await message.reply_text("⛔ Нельзя удалить администратора.")
        return

    async with _whitelist_lock:
        if target not in whitelist:
            await message.reply_text(
                f"ℹ️ Пользователь <code>{target}</code> не найден.",
                parse_mode=ParseMode.HTML,
            )
            return
        whitelist.discard(target)
        _save_whitelist(whitelist)

    # Drop any state the removed user had
    try:
        app = context.application
        ud = app.user_data.get(target)
        if ud is not None:
            ud.pop(STATE_ACTION, None)
            ud.pop(STATE_MODE, None)
    except Exception:
        pass

    logger.info("Admin %s removed user %s", uid, target)
    await message.reply_text(
        f"✅ Пользователь <code>{target}</code> удалён.\n"
        f"Осталось в белом списке: <b>{len(whitelist)}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id
    if not is_admin(uid):
        await message.reply_text("⛔ Только для администраторов.")
        return

    lines: list[str] = [
        "<b>👑 Администраторы</b>",
        f"<i>всего: {len(ADMIN_IDS)}</i>",
    ]
    for i, aid in enumerate(sorted(ADMIN_IDS), 1):
        lines.append(f"  {i}. <code>{aid}</code>")

    lines.append("")
    if whitelist:
        lines += [
            "<b>📋 Пользователи с доступом</b>",
            f"<i>всего: {len(whitelist)}</i>",
        ]
        for i, wuid in enumerate(sorted(whitelist), 1):
            lines.append(f"  {i}. <code>{wuid}</code>")
    else:
        lines.append("📋 Белый список пуст.")

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# Callback handler
# --------------------------------------------------------------------------- #


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return

    uid = query.from_user.id
    data = query.data or ""

    if not is_allowed(uid):
        await query.answer("⛔ У вас нет доступа.", show_alert=True)
        return

    if data == "back:main":
        await query.answer()
        reset_state(context)
        await safe_edit(query, MAIN_TITLE, reply_markup=kb_main())
        return

    if data == "nav:help":
        await query.answer()
        await safe_edit(query, _build_help_text(uid), reply_markup=kb_back_main())
        return

    if data.startswith("action:"):
        action = data.split(":", 1)[1]
        if action not in ("decrypt", "view"):
            await query.answer()
            return

        await query.answer()
        set_action(context, action)
        set_mode(context, None)

        if action == "decrypt":
            await safe_edit(
                query,
                "🔓 <b>Дешифрование</b>\n"
                f"<i>{DIVIDER}</i>\n"
                "Выбери тип файла:",
                reply_markup=kb_decrypt_type(),
            )
        else:
            await safe_edit(
                query,
                "👁 <b>Просмотр</b>\n"
                f"<i>{DIVIDER}</i>\n"
                "Выбери тип файла:",
                reply_markup=kb_view_type(),
            )
        return

    if data.startswith("mode:"):
        mode = data.split(":", 1)[1]
        action = get_action(context)

        if not action:
            await query.answer()
            await safe_edit(
                query,
                "⚠️ Сессия устарела. Начни заново:",
                reply_markup=kb_main(),
            )
            return

        if mode not in ("netcfg", "mxcfg"):
            await query.answer()
            return

        if mode == "netcfg" and action == "view":
            await query.answer(
                "Просмотр доступен только для MXCFG.", show_alert=True
            )
            return

        await query.answer()
        set_mode(context, mode)

        labels = {"netcfg": "NETCFG", "mxcfg": "MXCFG"}
        verb = "просмотра" if action == "view" else "дешифровки"
        await safe_edit(
            query,
            f"📎 <b>Ожидаю файл</b>\n"
            f"<i>{DIVIDER}</i>\n"
            f"Отправь файл <b>{labels[mode]}</b> для {verb}.\n"
            f"Максимальный размер — {MAX_FILE_SIZE // 1024 // 1024} МБ.",
            reply_markup=kb_awaiting_file(),
        )
        return

    await query.answer()


# --------------------------------------------------------------------------- #
# Document handler
# --------------------------------------------------------------------------- #


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id

    if not is_allowed(uid):
        await message.reply_text(
            ACCESS_DENIED_TEMPLATE.format(uid=uid),
            parse_mode=ParseMode.HTML,
        )
        return

    action = get_action(context)
    mode = get_mode(context)

    if not action:
        await message.reply_text(
            "⚠️ Сначала нажми /start и выбери режим.",
            reply_markup=kb_main(),
        )
        return

    if not mode:
        await message.reply_text(
            "⚠️ Сначала выбери тип файла.",
            reply_markup=kb_back_main(),
        )
        return

    doc = message.document
    if doc is None:
        await message.reply_text(
            "❌ Не удалось прочитать документ.",
            reply_markup=kb_back_main(),
        )
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ Файл слишком большой. Максимум {MAX_FILE_SIZE // 1024 // 1024} МБ.",
            reply_markup=kb_back_main(),
        )
        return

    # Visible progress indicator while we download + decode.
    try:
        await context.bot.send_chat_action(
            message.chat_id, ChatAction.UPLOAD_DOCUMENT
        )
    except Exception:
        pass

    tg_file = await doc.get_file()
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    raw = buf.getvalue()
    original_name = doc.file_name or "file.bin"

    if not raw:
        await message.reply_text(
            "❌ Файл пустой.", reply_markup=kb_back_main()
        )
        return

    logger.info(
        "uid=%s action=%s mode=%s file=%s size=%d",
        uid, action, mode, original_name, len(raw),
    )

    async def send_file(data: bytes, name: str, caption: str) -> None:
        await message.reply_document(
            document=BytesIO(data),
            filename=name,
            caption=caption,
            reply_markup=kb_back_main(),
        )

    try:
        if action == "decrypt" and mode == "netcfg":
            result, status = await asyncio.to_thread(decode_netcfg, raw)

            if status == "too_short":
                await message.reply_text(
                    "❌ Файл слишком мал для NETCFG.",
                    reply_markup=kb_back_main(),
                )
                return
            if result is None:
                await message.reply_text(
                    "❌ Не удалось расшифровать NETCFG.\n"
                    "Убедись, что это корректный файл.",
                    reply_markup=kb_back_main(),
                )
                return

            caption = (
                "ℹ️ Файл уже был расшифрован — флаг <code>encrypted</code> нормализован."
                if status == "already"
                else "✅ Файл расшифрован."
            )
            # reply_document caption doesn't use HTML by default in our code,
            # so strip tags for "already" case:
            caption_plain = caption.replace("<code>", "").replace("</code>", "")
            await send_file(result, f"decoded_{original_name}", caption_plain)
            reset_state(context)
            return

        if action == "decrypt" and mode == "mxcfg":
            result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

            if status in ("empty", "error"):
                await message.reply_text(
                    "❌ Файл пустой или повреждён.",
                    reply_markup=kb_back_main(),
                )
                return
            if result is None:
                await message.reply_text(
                    "❌ Не удалось расшифровать MXCFG.\n"
                    "Ни один из известных ключей не подошёл.",
                    reply_markup=kb_back_main(),
                )
                return

            caption = (
                "ℹ️ Файл уже был расшифрован."
                if status == "already"
                else "✅ Файл расшифрован."
            )
            await send_file(result, f"decoded_{original_name}", caption)
            reset_state(context)
            return

        if action == "view" and mode == "mxcfg":
            result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

            if status in ("empty", "error"):
                await message.reply_text(
                    "❌ Файл пустой или повреждён.",
                    reply_markup=kb_back_main(),
                )
                return
            if result is None:
                await message.reply_text(
                    "❌ Не удалось прочитать MXCFG.\n"
                    "Ни один из известных ключей не подошёл.",
                    reply_markup=kb_back_main(),
                )
                return

            text = result.decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(text)
                pretty = pretty_mxcfg_view(parsed)
            except Exception:
                escaped = esc(text[:MAX_TEXT_BLOCK - 200])
                pretty = f"<pre>{escaped}</pre>"

            await send_long_text(update, context, pretty)
            reset_state(context)
            return

        await message.reply_text(
            "⚠️ Неподдерживаемая комбинация режима и типа файла.",
            reply_markup=kb_back_main(),
        )

    except Exception as e:
        logger.exception("Ошибка обработки файла uid=%s", uid)
        await message.reply_text(
            f"💥 Внутренняя ошибка: <code>{esc(e)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back_main(),
        )


# --------------------------------------------------------------------------- #
# Text / error handlers
# --------------------------------------------------------------------------- #


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    uid = user.id

    if not is_allowed(uid):
        return

    action = get_action(context)
    mode = get_mode(context)

    if action and mode:
        await message.reply_text(
            "📎 Пожалуйста, отправь файл, а не текст.",
            reply_markup=kb_awaiting_file(),
        )
    elif action:
        await message.reply_text(
            "⚠️ Выбери тип файла с помощью кнопок выше.",
            reply_markup=kb_back_main(),
        )
    else:
        await message.reply_text(
            "👋 Нажми /start чтобы начать.",
            reply_markup=kb_main(),
        )


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error("Необработанное исключение:", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #


PUBLIC_COMMANDS = [
    BotCommand("start", "Главное меню"),
    BotCommand("help", "Справка"),
    BotCommand("myid", "Показать мой Telegram ID"),
    BotCommand("cancel", "Отменить текущее действие"),
]

ADMIN_EXTRA_COMMANDS = [
    BotCommand("adduser", "Добавить пользователя"),
    BotCommand("removeuser", "Удалить пользователя"),
    BotCommand("users", "Список пользователей"),
]


async def post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(PUBLIC_COMMANDS)
    except Exception as e:
        logger.warning("Не удалось задать глобальные команды: %s", e)

    for admin_id in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_EXTRA_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as e:
            logger.warning(
                "Не удалось задать admin-команды для %s: %s", admin_id, e
            )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ Переменная окружения BOT_TOKEN не задана.\n"
            "   Получите токен у @BotFather и запустите:\n"
            "       BOT_TOKEN=... python main.py"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("myid",       cmd_myid))
    app.add_handler(CommandHandler("cancel",     cmd_cancel))
    app.add_handler(CommandHandler("adduser",    cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users",      cmd_users))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)

    logger.info("✅ Бот запущен. Админы: %s", sorted(ADMIN_IDS))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
