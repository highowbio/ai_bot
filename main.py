import asyncio
import logging
import json
from io import BytesIO
from base64 import b64decode
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8593158641:AAGhsyruVYDBgntjdL-CIlNkwD92Wt9hkVg"

ADMIN_IDS: set[int] = {6903588929, 6734219400}

WHITELIST_FILE = Path(__file__).parent / "whitelist.json"

NETCFG_KEY = b"2yHBg"

MXCFG_KEYS = [
    b"xR9#vL2@mK7!pQ4$nW6^jT8&",
    b"Mx!Cl#2026$Pr0tect^Key&Adv",
    b"MerixtiClumsy2025!@#SecretKey",
]

MAX_FILE_SIZE = 20 * 1024 * 1024

user_action: dict[int, str] = {}
user_mode:   dict[int, str] = {}


def _load_whitelist() -> set[int]:
    if not WHITELIST_FILE.exists():
        return set()
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except Exception:
        logger.warning("Не удалось прочитать whitelist.json")
        return set()


def _save_whitelist(wl: set[int]) -> None:
    WHITELIST_FILE.write_text(json.dumps(sorted(wl), indent=2), encoding="utf-8")


whitelist: set[int] = _load_whitelist()


def is_allowed(uid: int) -> bool:
    return uid in ADMIN_IDS or uid in whitelist


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def reset_state(uid: int) -> None:
    user_action.pop(uid, None)
    user_mode.pop(uid, None)


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔓 Дешифровать", callback_data="action:decrypt"),
            InlineKeyboardButton("👁 Просмотр",    callback_data="action:view"),
        ]
    ])


def kb_decrypt_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 NETCFG", callback_data="mode:netcfg"),
            InlineKeyboardButton("📄 MXCFG",  callback_data="mode:mxcfg"),
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="back:main")],
    ])


def kb_view_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 MXCFG", callback_data="mode:mxcfg")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back:main")],
    ])


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 В главное меню", callback_data="back:main")]
    ])


async def safe_edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest as e:
        err = str(e).lower()
        if any(s in err for s in (
            "there is no text",
            "message can't be edited",
            "message is not modified",
            "message to edit not found",
        )):
            await query.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            raise


async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    chat_id = update.effective_chat.id
    if len(text) <= 3800:
        await context.bot.send_message(
            chat_id, text,
            parse_mode="HTML",
            reply_markup=kb_back_main(),
        )
        return

    buf = BytesIO(text.encode("utf-8"))
    await context.bot.send_document(
        chat_id,
        document=buf,
        filename="mxcfg_info.txt",
        caption="📄 Файл слишком большой — отправляю документом.",
        reply_markup=kb_back_main(),
    )


def xor_decrypt(data: bytes, key: bytes) -> bytes:
    klen = len(key)
    return bytes(b ^ key[i % klen] for i, b in enumerate(data))


def fix_encrypted_flag(text: str) -> str:
    return text.replace('"encrypted": true', '"encrypted": false').replace(
        '"encrypted":true', '"encrypted":false'
    )


def decode_netcfg(data: bytes) -> tuple[bytes | None, str]:
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
            decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode(
                "utf-8", errors="ignore"
            )
            if not decrypted.lstrip().startswith("{"):
                continue
            try:
                js = json.loads(decrypted)
                js["encrypted"] = False
                decrypted = json.dumps(js, indent=2, ensure_ascii=False)
            except Exception:
                decrypted = fix_encrypted_flag(decrypted)
            return decrypted.encode("utf-8"), "decoded"
        except Exception:
            continue

    return None, "unknown"


def render_scalar(value) -> str:
    if isinstance(value, bool):
        return "✅ включено" if value else "❌ выключено"
    if value is None:
        return "null"
    if isinstance(value, str):
        safe = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'"{safe}"'
    return str(value)


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

    lines: list[str] = ["<b>📂 Содержимое MXCFG</b>", ""]

    def add(label: str, value) -> None:
        lines.append(f"<b>{label}:</b> {render_scalar(value)}")

    for key in ("author", "description", "encrypted"):
        if key in parsed:
            add(top_labels[key], parsed[key])

    after_key = next((k for k in ("AfterDur", "afterDur") if k in parsed), None)
    if after_key:
        add(top_labels["AfterDur"], f"{parsed[after_key]} мс")

    if "scriptMode" in parsed:
        add(top_labels["scriptMode"], parsed["scriptMode"])

    if "data" in parsed and isinstance(parsed["data"], dict):
        lines += ["", f"<b>{top_labels['data']}</b>"]
        for k, v in parsed["data"].items():
            lines.append(f"  • {data_labels.get(k, k)}: {render_scalar(v)}")

    if "steps" in parsed and isinstance(parsed["steps"], list):
        lines += ["", f"<b>{top_labels['steps']}</b>"]
        for i, step in enumerate(parsed["steps"], 1):
            if not isinstance(step, dict):
                lines.append(f"  • Шаг {i}: {render_scalar(step)}")
                continue
            lines.append(f"  <b>Шаг {i}:</b>")
            for k, v in step.items():
                lines.append(f"    — {step_labels.get(k, k)}: {render_scalar(v)}")

    if "onStop" in parsed:
        lines.append("")
        add(top_labels["onStop"], parsed["onStop"])

    known = {"author", "description", "encrypted", "AfterDur", "afterDur",
             "scriptMode", "data", "steps", "onStop"}
    unknown = [k for k in parsed if k not in known]
    if unknown:
        lines += ["", "<b>📌 Дополнительные поля</b>"]
        for k in unknown:
            lines.append(f"  • {k}: {render_scalar(parsed[k])}")

    return "\n".join(lines).strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if not is_allowed(uid):
        await update.message.reply_text(
            f"⛔ У вас нет доступа.\n🆔 Ваш ID: <code>{uid}</code>",
            parse_mode="HTML",
        )
        return

    reset_state(uid)
    await update.message.reply_text(
        "👋 <b>Привет!</b>\n\nВыбери режим работы:",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if not is_allowed(uid):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    text = (
        "📖 <b>Справка</b>\n\n"
        "/start — главное меню\n"
        "/help  — эта справка\n"
        "/myid  — показать ваш Telegram ID\n"
    )
    if is_admin(uid):
        text += (
            "\n<b>🔧 Команды администратора:</b>\n"
            "/adduser &lt;id&gt;    — добавить пользователя\n"
            "/removeuser &lt;id&gt; — удалить пользователя\n"
            "/users             — список пользователей\n"
        )

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    await update.message.reply_text(
        f"🆔 Ваш Telegram ID: <code>{user.id}</code>",
        parse_mode="HTML",
    )


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "Использование: /adduser &lt;telegram_id&gt;",
            parse_mode="HTML",
        )
        return

    target = int(context.args[0])
    if target in ADMIN_IDS:
        await update.message.reply_text("ℹ️ Этот пользователь уже администратор.")
        return
    if target in whitelist:
        await update.message.reply_text(
            f"ℹ️ Пользователь <code>{target}</code> уже в списке.",
            parse_mode="HTML",
        )
        return

    whitelist.add(target)
    _save_whitelist(whitelist)
    logger.info("Admin %s added user %s", uid, target)
    await update.message.reply_text(
        f"✅ Пользователь <code>{target}</code> добавлен.",
        parse_mode="HTML",
    )


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "Использование: /removeuser &lt;telegram_id&gt;",
            parse_mode="HTML",
        )
        return

    target = int(context.args[0])
    if target in ADMIN_IDS:
        await update.message.reply_text("⛔ Нельзя удалить администратора.")
        return
    if target not in whitelist:
        await update.message.reply_text(
            f"ℹ️ Пользователь <code>{target}</code> не найден.",
            parse_mode="HTML",
        )
        return

    whitelist.discard(target)
    reset_state(target)
    _save_whitelist(whitelist)
    logger.info("Admin %s removed user %s", uid, target)
    await update.message.reply_text(
        f"✅ Пользователь <code>{target}</code> удалён.",
        parse_mode="HTML",
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Только для администраторов.")
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

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return

    uid  = query.from_user.id
    data = query.data or ""

    if not is_allowed(uid):
        await query.answer("⛔ У вас нет доступа.", show_alert=True)
        return

    if data == "back:main":
        await query.answer()
        reset_state(uid)
        await safe_edit(
            query,
            "👋 <b>Привет!</b>\n\nВыбери режим работы:",
            reply_markup=kb_main(),
        )
        return

    if data.startswith("action:"):
        action = data.split(":", 1)[1]
        if action not in ("decrypt", "view"):
            await query.answer()
            return

        await query.answer()
        user_action[uid] = action
        user_mode.pop(uid, None)

        if action == "decrypt":
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
        mode   = data.split(":", 1)[1]
        action = user_action.get(uid)

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
            await query.answer("Просмотр доступен только для MXCFG.", show_alert=True)
            return

        await query.answer()
        user_mode[uid] = mode

        labels = {"netcfg": "NETCFG", "mxcfg": "MXCFG"}
        verb   = "просмотра" if action == "view" else "дешифровки"
        await safe_edit(
            query,
            f"📎 Отправь файл <b>{labels[mode]}</b> для {verb}.",
            reply_markup=kb_back_main(),
        )
        return

    await query.answer()


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id

    if not is_allowed(uid):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    action = user_action.get(uid)
    mode   = user_mode.get(uid)

    if not action:
        await update.message.reply_text(
            "⚠️ Сначала нажми /start и выбери режим.",
            reply_markup=kb_main(),
        )
        return

    if not mode:
        await update.message.reply_text(
            "⚠️ Сначала выбери тип файла.",
            reply_markup=kb_back_main(),
        )
        return

    doc = update.message.document
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(
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
        await update.message.reply_text("❌ Файл пустой.", reply_markup=kb_back_main())
        return

    logger.info("uid=%s action=%s mode=%s file=%s size=%d", uid, action, mode, original_name, len(raw))

    async def send_file(data: bytes, name: str, caption: str) -> None:
        await update.message.reply_document(
            document=BytesIO(data),
            filename=name,
            caption=caption,
            reply_markup=kb_back_main(),
        )

    try:
        if action == "decrypt" and mode == "netcfg":
            result, status = await asyncio.to_thread(decode_netcfg, raw)

            if status == "too_short":
                await update.message.reply_text(
                    "❌ Файл слишком мал для NETCFG.", reply_markup=kb_back_main()
                )
                return
            if result is None:
                await update.message.reply_text(
                    "❌ Не удалось расшифровать NETCFG.\nУбедись, что это корректный файл.",
                    reply_markup=kb_back_main(),
                )
                return

            caption = "ℹ️ Файл уже был расшифрован." if status == "already" else "✅ Файл расшифрован."
            await send_file(result, f"decoded_{original_name}", caption)
            reset_state(uid)
            return

        if action == "decrypt" and mode == "mxcfg":
            result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

            if status in ("empty", "error"):
                await update.message.reply_text(
                    "❌ Файл пустой или повреждён.", reply_markup=kb_back_main()
                )
                return
            if result is None:
                await update.message.reply_text(
                    "❌ Не удалось расшифровать MXCFG.\nНи один из известных ключей не подошёл.",
                    reply_markup=kb_back_main(),
                )
                return

            caption = "ℹ️ Файл уже был расшифрован." if status == "already" else "✅ Файл расшифрован."
            await send_file(result, f"decoded_{original_name}", caption)
            reset_state(uid)
            return

        if action == "view" and mode == "mxcfg":
            result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

            if status in ("empty", "error"):
                await update.message.reply_text(
                    "❌ Файл пустой или повреждён.", reply_markup=kb_back_main()
                )
                return
            if result is None:
                await update.message.reply_text(
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
            reset_state(uid)
            return

        await update.message.reply_text(
            "⚠️ Неподдерживаемая комбинация режима и типа файла.",
            reply_markup=kb_back_main(),
        )

    except Exception as e:
        logger.exception("Ошибка обработки файла uid=%s", uid)
        await update.message.reply_text(
            f"💥 Внутренняя ошибка: {e}",
            reply_markup=kb_back_main(),
        )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    uid = user.id

    if not is_allowed(uid):
        return

    action = user_action.get(uid)
    mode   = user_mode.get(uid)

    if action and mode:
        await update.message.reply_text(
            "📎 Пожалуйста, отправь файл, а не текст.",
            reply_markup=kb_back_main(),
        )
    elif action:
        await update.message.reply_text(
            "⚠️ Выбери тип файла с помощью кнопок выше.",
            reply_markup=kb_back_main(),
        )
    else:
        await update.message.reply_text(
            "👋 Нажми /start чтобы начать.",
            reply_markup=kb_main(),
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необработанное исключение:", exc_info=context.error)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("myid",       cmd_myid))
    app.add_handler(CommandHandler("adduser",    cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users",      cmd_users))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)

    logger.info("✅ Бот запущен. Админы: %s", ADMIN_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
