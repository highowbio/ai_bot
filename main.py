"""Telegram bot for decrypting and viewing NETCFG / MXCFG files."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from io import BytesIO
from pathlib import Path

from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

from crypto import decode_mxcfg_bytes, decode_netcfg
from view import pretty_mxcfg_view

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("Ignoring invalid ADMIN_IDS entry: %r", part)
    return ids


BASE_DIR = Path(__file__).parent

ADMIN_IDS: set[int] = _parse_admin_ids(os.environ.get("ADMIN_IDS", ""))
WHITELIST_FILE = Path(os.environ.get("WHITELIST_FILE", BASE_DIR / "whitelist.json"))
PERSISTENCE_FILE = Path(os.environ.get("PERSISTENCE_FILE", BASE_DIR / "bot_persistence.pickle"))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_MB", "20")) * 1024 * 1024


# ---------------------------------------------------------------------------
# Whitelist persistence (file-backed to survive restarts)
# ---------------------------------------------------------------------------


def _load_whitelist() -> set[int]:
    if not WHITELIST_FILE.exists():
        return set()
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except Exception:
        logger.warning("Failed to read whitelist file: %s", WHITELIST_FILE)
        return set()


def _save_whitelist(wl: set[int]) -> None:
    WHITELIST_FILE.write_text(json.dumps(sorted(wl), indent=2), encoding="utf-8")


whitelist: set[int] = _load_whitelist()


def is_allowed(uid: int) -> bool:
    return uid in ADMIN_IDS or uid in whitelist


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ---------------------------------------------------------------------------
# Per-user state (stored in context.user_data, persisted via PicklePersistence)
# ---------------------------------------------------------------------------

_ACTION_KEY = "action"
_MODE_KEY = "mode"


def _get_action(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return (context.user_data or {}).get(_ACTION_KEY)


def _get_mode(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return (context.user_data or {}).get(_MODE_KEY)


def _set_action(context: ContextTypes.DEFAULT_TYPE, action: str | None) -> None:
    if context.user_data is None:
        return
    if action is None:
        context.user_data.pop(_ACTION_KEY, None)
    else:
        context.user_data[_ACTION_KEY] = action


def _set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str | None) -> None:
    if context.user_data is None:
        return
    if mode is None:
        context.user_data.pop(_MODE_KEY, None)
    else:
        context.user_data[_MODE_KEY] = mode


def _reset_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    _set_action(context, None)
    _set_mode(context, None)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔓 Дешифровать", callback_data="action:decrypt"),
                InlineKeyboardButton("👁 Просмотр", callback_data="action:view"),
            ]
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


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 В главное меню", callback_data="back:main")]]
    )


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def _require_msg_user(update: Update) -> tuple[Message, User] | None:
    """Return (message, user) if both are present, else None.

    All our message handlers are registered on filters that always produce
    an effective message + user, but ``telegram.Update`` types both as
    ``Optional`` so we guard once at the top of each handler.
    """
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return None
    return message, user


async def safe_edit(
    query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
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
            fallback_msg = query.message
            if isinstance(fallback_msg, Message):
                await fallback_msg.reply_text(
                    text=text, parse_mode="HTML", reply_markup=reply_markup
                )
        else:
            raise


async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    chat: Chat | None = update.effective_chat
    if chat is None:
        return
    if len(text) <= 3800:
        await context.bot.send_message(
            chat.id, text, parse_mode="HTML", reply_markup=kb_back_main()
        )
        return

    buf = BytesIO(text.encode("utf-8"))
    await context.bot.send_document(
        chat.id,
        document=buf,
        filename="mxcfg_info.txt",
        caption="📄 Файл слишком большой — отправляю документом.",
        reply_markup=kb_back_main(),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    uid = user.id
    if not is_allowed(uid):
        await message.reply_text(
            f"⛔ У вас нет доступа.\n🆔 Ваш ID: <code>{uid}</code>",
            parse_mode="HTML",
        )
        return

    _reset_state(context)
    await message.reply_text(
        "👋 <b>Привет!</b>\n\nВыбери режим работы:",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    uid = user.id
    if not is_allowed(uid):
        await message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    text = (
        "📖 <b>Справка</b>\n\n"
        "/start  — главное меню\n"
        "/help   — эта справка\n"
        "/myid   — показать ваш Telegram ID\n"
        "/cancel — сбросить текущий режим\n"
    )
    if is_admin(uid):
        text += (
            "\n<b>🔧 Команды администратора:</b>\n"
            "/adduser &lt;id&gt;    — добавить пользователя\n"
            "/removeuser &lt;id&gt; — удалить пользователя\n"
            "/users             — список пользователей\n"
        )

    await message.reply_text(text, parse_mode="HTML")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    await message.reply_text(
        f"🆔 Ваш Telegram ID: <code>{user.id}</code>",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    if not is_allowed(user.id):
        return
    _reset_state(context)
    await message.reply_text(
        "↩️ Режим сброшен. Выбери заново:",
        reply_markup=kb_main(),
    )


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    uid = user.id
    if not is_admin(uid):
        await message.reply_text("⛔ Только для администраторов.")
        return

    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            "Использование: /adduser &lt;telegram_id&gt;",
            parse_mode="HTML",
        )
        return

    target = int(args[0])
    if target in ADMIN_IDS:
        await message.reply_text("ℹ️ Этот пользователь уже администратор.")
        return
    if target in whitelist:
        await message.reply_text(
            f"ℹ️ Пользователь <code>{target}</code> уже в списке.",
            parse_mode="HTML",
        )
        return

    whitelist.add(target)
    _save_whitelist(whitelist)
    logger.info("Admin %s added user %s", uid, target)
    await message.reply_text(
        f"✅ Пользователь <code>{target}</code> добавлен.",
        parse_mode="HTML",
    )


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    uid = user.id
    if not is_admin(uid):
        await message.reply_text("⛔ Только для администраторов.")
        return

    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            "Использование: /removeuser &lt;telegram_id&gt;",
            parse_mode="HTML",
        )
        return

    target = int(args[0])
    if target in ADMIN_IDS:
        await message.reply_text("⛔ Нельзя удалить администратора.")
        return
    if target not in whitelist:
        await message.reply_text(
            f"ℹ️ Пользователь <code>{target}</code> не найден.",
            parse_mode="HTML",
        )
        return

    whitelist.discard(target)
    _save_whitelist(whitelist)
    logger.info("Admin %s removed user %s", uid, target)
    await message.reply_text(
        f"✅ Пользователь <code>{target}</code> удалён.",
        parse_mode="HTML",
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    if not is_admin(user.id):
        await message.reply_text("⛔ Только для администраторов.")
        return

    lines = ["<b>👑 Администраторы:</b>"]
    for i, aid in enumerate(sorted(ADMIN_IDS), 1):
        lines.append(f"  {i}. <code>{aid}</code>")

    if whitelist:
        lines += ["", "<b>📋 Пользователи с доступом:</b>"]
        for i, wuid in enumerate(sorted(whitelist), 1):
            lines.append(f"  {i}. <code>{wuid}</code>")
    else:
        lines += ["", "📋 Белый список пуст."]

    await message.reply_text("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# Callbacks and messages
# ---------------------------------------------------------------------------


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
        _reset_state(context)
        await safe_edit(
            query,
            "👋 <b>Привет!</b>\n\nВыбери режим работы:",
            reply_markup=kb_main(),
        )
        return

    if data.startswith("action:"):
        requested_action = data.split(":", 1)[1]
        if requested_action not in ("decrypt", "view"):
            await query.answer()
            return

        await query.answer()
        _set_action(context, requested_action)
        _set_mode(context, None)

        if requested_action == "decrypt":
            await safe_edit(
                query,
                "🔓 <b>Дешифрование</b>\n\nВыбери тип файла:",
                reply_markup=kb_decrypt_type(),
            )
        else:
            await safe_edit(
                query,
                "👁 <b>Просмотр</b>\n\nВыбери тип файла:",
                reply_markup=kb_view_type(),
            )
        return

    if data.startswith("mode:"):
        requested_mode = data.split(":", 1)[1]
        current_action = _get_action(context)

        if not current_action:
            await query.answer()
            await safe_edit(
                query,
                "⚠️ Сессия устарела. Начни заново:",
                reply_markup=kb_main(),
            )
            return

        if requested_mode not in ("netcfg", "mxcfg"):
            await query.answer()
            return

        if requested_mode == "netcfg" and current_action == "view":
            await query.answer("Просмотр доступен только для MXCFG.", show_alert=True)
            return

        await query.answer()
        _set_mode(context, requested_mode)

        labels = {"netcfg": "NETCFG", "mxcfg": "MXCFG"}
        verb = "просмотра" if current_action == "view" else "дешифровки"
        await safe_edit(
            query,
            f"📎 Отправь файл <b>{labels[requested_mode]}</b> для {verb}.",
            reply_markup=kb_back_main(),
        )
        return

    await query.answer()


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx
    uid = user.id

    if not is_allowed(uid):
        await message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    action = _get_action(context)
    mode = _get_mode(context)

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
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ Файл слишком большой. Максимум {MAX_FILE_SIZE // 1024 // 1024} МБ.",
            reply_markup=kb_back_main(),
        )
        return

    tg_file = await doc.get_file()
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    raw = buf.getvalue()
    original_name = doc.file_name or "file.bin"

    if not raw:
        await message.reply_text("❌ Файл пустой.", reply_markup=kb_back_main())
        return

    logger.info(
        "uid=%s action=%s mode=%s file=%s size=%d",
        uid,
        action,
        mode,
        original_name,
        len(raw),
    )

    async def send_file(data: bytes, name: str, caption: str) -> None:
        await message.reply_document(
            document=BytesIO(data),
            filename=name,
            caption=caption,
            reply_markup=kb_back_main(),
        )

    if action == "decrypt" and mode == "netcfg":
        result, status = await asyncio.to_thread(decode_netcfg, raw)

        if status == "too_short":
            await message.reply_text("❌ Файл слишком мал для NETCFG.", reply_markup=kb_back_main())
            return
        if result is None:
            await message.reply_text(
                "❌ Не удалось расшифровать NETCFG.\nУбедись, что это корректный файл.",
                reply_markup=kb_back_main(),
            )
            return

        caption = "ℹ️ Файл уже был расшифрован." if status == "already" else "✅ Файл расшифрован."
        await send_file(result, f"decoded_{original_name}", caption)
        _reset_state(context)
        return

    if action == "decrypt" and mode == "mxcfg":
        result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

        if status in ("empty", "error"):
            await message.reply_text("❌ Файл пустой или повреждён.", reply_markup=kb_back_main())
            return
        if result is None:
            await message.reply_text(
                "❌ Не удалось расшифровать MXCFG.\nНи один из известных ключей не подошёл.",
                reply_markup=kb_back_main(),
            )
            return

        caption = "ℹ️ Файл уже был расшифрован." if status == "already" else "✅ Файл расшифрован."
        await send_file(result, f"decoded_{original_name}", caption)
        _reset_state(context)
        return

    if action == "view" and mode == "mxcfg":
        result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

        if status in ("empty", "error"):
            await message.reply_text("❌ Файл пустой или повреждён.", reply_markup=kb_back_main())
            return
        if result is None:
            await message.reply_text(
                "❌ Не удалось прочитать MXCFG.\nНи один из известных ключей не подошёл.",
                reply_markup=kb_back_main(),
            )
            return

        text = result.decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(text)
            pretty = pretty_mxcfg_view(parsed)
        except Exception:
            escaped = text[:3500].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            pretty = f"<pre>{escaped}</pre>"

        await send_long_text(update, context, pretty)
        _reset_state(context)
        return

    await message.reply_text(
        "⚠️ Неподдерживаемая комбинация режима и типа файла.",
        reply_markup=kb_back_main(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _require_msg_user(update)
    if ctx is None:
        return
    message, user = ctx

    if not is_allowed(user.id):
        return

    action = _get_action(context)
    mode = _get_mode(context)

    if action and mode:
        await message.reply_text(
            "📎 Пожалуйста, отправь файл, а не текст.",
            reply_markup=kb_back_main(),
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:", exc_info=context.error)

    chat: Chat | None = None
    if isinstance(update, Update):
        chat = update.effective_chat
    if chat is None:
        return

    try:
        await context.bot.send_message(
            chat.id,
            "💥 Внутренняя ошибка. Попробуй ещё раз или вернись в главное меню.",
            reply_markup=kb_back_main(),
        )
    except Exception:
        logger.exception("Failed to notify user about error")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
    )
    # httpx emits an info-level log line for every Telegram API call; keep it quiet
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_application(token: str) -> Application:
    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
    app = Application.builder().token(token).persistence(persistence).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)

    return app


def main() -> None:
    _configure_logging()

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set. Create a .env file (see .env.example) or "
            "export BOT_TOKEN before starting the bot."
        )

    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty. Set it in the environment to enable admin commands.")

    app = build_application(token)
    logger.info("Bot started. Admins: %s", sorted(ADMIN_IDS))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
