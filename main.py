import asyncio
import logging
import os
import json
from io import BytesIO
from base64 import b64decode
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─── Логирование ──────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Конфиг ───────────────────────────────────────────────────────────────────

BOT_TOKEN = ("8593158641:AAGhsyruVYDBgntjdL-CIlNkwD92Wt9hkVg")

# Администраторы бота (через запятую в env: ADMIN_IDS=123456,789012)
_raw_admins = "6903588929" "6734219400"
ADMIN_IDS: set[int] = {
    int(x.strip()) for x in _raw_admins.split(",") if x.strip().isdigit()
}

# Файл белого списка (рядом со скриптом)
WHITELIST_FILE = Path(__file__).parent / "whitelist.json"

NETCFG_KEY = b"2yHBg"

MXCFG_KEYS = [
    b"xR9#vL2@mK7!pQ4$nW6^jT8&",
    b"Mx!Cl#2026$Pr0tect^Key&Adv",
    b"MerixtiClumsy2025!@#SecretKey",
]

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ

# ─── Состояние пользователей ──────────────────────────────────────────────────

user_action: dict[int, str] = {}
user_mode:   dict[int, str] = {}

# ─── Белый список ─────────────────────────────────────────────────────────────

def _load_whitelist() -> set[int]:
    """Загружает белый список из файла."""
    if not WHITELIST_FILE.exists():
        return set()
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        return set(int(x) for x in data)
    except Exception:
        logger.warning("Не удалось прочитать whitelist.json, используется пустой список.")
        return set()


def _save_whitelist(wl: set[int]) -> None:
    """Сохраняет белый список в файл."""
    WHITELIST_FILE.write_text(
        json.dumps(sorted(wl), indent=2),
        encoding="utf-8",
    )


# Загружаем при старте
whitelist: set[int] = _load_whitelist()


def is_allowed(uid: int) -> bool:
    """True если пользователь имеет доступ к боту."""
    return uid in ADMIN_IDS or uid in whitelist


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ─── Клавиатуры ───────────────────────────────────────────────────────────────

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

# ─── Хелпер: безопасное редактирование сообщения ──────────────────────────────

async def safe_edit(query, text: str, reply_markup=None) -> None:
    """
    Редактирует сообщение. Если невозможно (нет текста, не изменилось) —
    отправляет новое сообщение.
    """
    kwargs = dict(text=text, parse_mode="HTML", reply_markup=reply_markup)
    try:
        await query.edit_message_text(**kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if any(s in err for s in (
            "there is no text",
            "message can't be edited",
            "message is not modified",
            "message to edit not found",
        )):
            await query.message.reply_text(**kwargs)
        else:
            raise

# ─── Хелпер: отправить длинный текст ──────────────────────────────────────────

async def send_long_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Отправляет текст: если > 3800 символов — документом."""
    chat_id = update.effective_chat.id

    if len(text) <= 3800:
        await context.bot.send_message(
            chat_id, text,
            parse_mode="HTML",
            reply_markup=kb_back_main(),
        )
        return

    doc = BytesIO(text.encode("utf-8"))
    doc.name = "mxcfg_info.txt"
    await context.bot.send_document(
        chat_id, doc,
        caption="📄 Файл слишком большой — отправляю документом.",
        reply_markup=kb_back_main(),
    )

# ─── Крипто-утилиты ───────────────────────────────────────────────────────────

def xor_decrypt(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


def fix_encrypted_flag(text: str) -> str:
    text = text.replace('"encrypted": true',  '"encrypted": false')
    text = text.replace('"encrypted":true',   '"encrypted":false')
    return text


def decode_netcfg(data: bytes) -> tuple[bytes | None, str]:
    if data.startswith(b"\x01\x00"):
        return data, "already"

    if len(data) < 2:
        return None, "too_short"

    if data[:2] == b"\x01\x01":
        data = data[2:]

    if not data:
        return None, "too_short"

    decrypted = xor_decrypt(data, NETCFG_KEY)

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

    # Уже JSON
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
            decrypted = bytes(
                x ^ key[i % len(key)] for i, x in enumerate(raw)
            ).decode("utf-8", errors="ignore")

            if decrypted.lstrip().startswith("{"):
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

# ─── Рендер MXCFG ─────────────────────────────────────────────────────────────

def render_scalar(value) -> str:
    if isinstance(value, bool):
        return "✅ включено" if value else "❌ выключено"
    if value is None:
        return "null"
    if isinstance(value, str):
        # Экранируем HTML-символы в строках
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
        add(top_labels["AfterDur"], f'{parsed[after_key]} мс')

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
        lines += [""]
        add(top_labels["onStop"], parsed["onStop"])

    known = {"author", "description", "encrypted", "AfterDur", "afterDur",
             "scriptMode", "data", "steps", "onStop"}
    unknown = [k for k in parsed if k not in known]
    if unknown:
        lines += ["", "<b>📌 Дополнительные поля</b>"]
        for k in unknown:
            lines.append(f"  • {k}: {render_scalar(parsed[k])}")

    return "\n".join(lines).strip()

# ─── Хендлеры команд ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    if not is_allowed(uid):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    user_action.pop(uid, None)
    user_mode.pop(uid, None)

    await update.message.reply_text(
        "👋 <b>Привет!</b>\n\nВыбери режим работы:",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

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
    uid = update.effective_user.id
    await update.message.reply_text(f"🆔 Ваш Telegram ID: <code>{uid}</code>", parse_mode="HTML")


# ─── Команды администратора ───────────────────────────────────────────────────

async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Использование: /adduser <telegram_id>")
        return

    target = int(context.args[0])
    if target in whitelist:
        await update.message.reply_text(f"ℹ️ Пользователь <code>{target}</code> уже в списке.", parse_mode="HTML")
        return

    whitelist.add(target)
    _save_whitelist(whitelist)
    logger.info("Admin %s added user %s", uid, target)
    await update.message.reply_text(f"✅ Пользователь <code>{target}</code> добавлен.", parse_mode="HTML")


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Использование: /removeuser <telegram_id>")
        return

    target = int(context.args[0])
    if target not in whitelist:
        await update.message.reply_text(f"ℹ️ Пользователь <code>{target}</code> не найден.", parse_mode="HTML")
        return

    whitelist.discard(target)
    user_action.pop(target, None)
    user_mode.pop(target, None)
    _save_whitelist(whitelist)
    logger.info("Admin %s removed user %s", uid, target)
    await update.message.reply_text(f"✅ Пользователь <code>{target}</code> удалён.", parse_mode="HTML")


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("⛔ Только для администраторов.")
        return

    if not whitelist:
        await update.message.reply_text("📋 Белый список пуст.")
        return

    lines = ["<b>📋 Пользователи с доступом:</b>"]
    for i, wuid in enumerate(sorted(whitelist), 1):
        lines.append(f"  {i}. <code>{wuid}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ─── Callback хендлер ─────────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    uid  = query.from_user.id
    data = query.data

    # Проверка доступа
    if not is_allowed(uid):
        await query.answer("⛔ У вас нет доступа.", show_alert=True)
        return

    # ── Назад в главное меню
    if data == "back:main":
        user_action.pop(uid, None)
        user_mode.pop(uid, None)
        await safe_edit(
            query,
            "👋 <b>Привет!</b>\n\nВыбери режим работы:",
            reply_markup=kb_main(),
        )
        return

    # ── Выбор действия
    if data.startswith("action:"):
        action = data.split(":", 1)[1]
        if action not in ("decrypt", "view"):
            return

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

    # ── Выбор типа файла
    if data.startswith("mode:"):
        mode   = data.split(":", 1)[1]
        action = user_action.get(uid)

        # Нет выбранного действия — сбрасываем
        if not action:
            await safe_edit(
                query,
                "⚠️ Сессия устарела. Начни заново:",
                reply_markup=kb_main(),
            )
            return

        if mode not in ("netcfg", "mxcfg"):
            return

        if mode == "netcfg" and action == "view":
            await query.answer("Просмотр доступен только для MXCFG.", show_alert=True)
            return

        user_mode[uid] = mode

        labels = {"netcfg": "NETCFG", "mxcfg": "MXCFG"}
        verb   = "просмотра" if action == "view" else "дешифровки"
        await safe_edit(
            query,
            f"📎 Отправь файл <b>{labels[mode]}</b> для {verb}.",
            reply_markup=kb_back_main(),
        )
        return

# ─── Хендлер документов ───────────────────────────────────────────────────────

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid    = update.effective_user.id

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

    # Проверка размера (до скачивания)
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

    # Проверка пустого файла
    if not raw:
        await update.message.reply_text(
            "❌ Файл пустой.",
            reply_markup=kb_back_main(),
        )
        return

    logger.info("uid=%s action=%s mode=%s file=%s size=%d", uid, action, mode, original_name, len(raw))

    try:
        # ── Дешифровать NETCFG
        if action == "decrypt" and mode == "netcfg":
            result, status = await asyncio.to_thread(decode_netcfg, raw)

            if status == "already":
                await update.message.reply_text(
                    "ℹ️ Файл уже расшифрован.",
                    reply_markup=kb_back_main(),
                )
                return
            if status == "too_short":
                await update.message.reply_text(
                    "❌ Файл слишком мал для NETCFG.",
                    reply_markup=kb_back_main(),
                )
                return
            if result is None:
                await update.message.reply_text(
                    "❌ Не удалось расшифровать NETCFG.\n"
                    "Убедись, что это корректный файл.",
                    reply_markup=kb_back_main(),
                )
                return

            out = BytesIO(result)
            out.name = f"decoded_{original_name}"
            await update.message.reply_document(
                out,
                caption="✅ Файл расшифрован.",
                reply_markup=kb_back_main(),
            )
            # Сбрасываем состояние после успеха
            user_mode.pop(uid, None)
            return

        # ── Дешифровать MXCFG
        if action == "decrypt" and mode == "mxcfg":
            result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

            if status == "already":
                await update.message.reply_text(
                    "ℹ️ Файл уже расшифрован.",
                    reply_markup=kb_back_main(),
                )
                return
            if status == "empty":
                await update.message.reply_text(
                    "❌ Файл пустой.",
                    reply_markup=kb_back_main(),
                )
                return
            if result is None:
                await update.message.reply_text(
                    "❌ Не удалось расшифровать MXCFG.\n"
                    "Проверь формат файла — ни один из известных ключей не подошёл.",
                    reply_markup=kb_back_main(),
                )
                return

            out = BytesIO(result)
            out.name = f"decoded_{original_name}"
            await update.message.reply_document(
                out,
                caption="✅ Файл расшифрован.",
                reply_markup=kb_back_main(),
            )
            user_mode.pop(uid, None)
            return

        # ── Просмотр MXCFG
        if action == "view" and mode == "mxcfg":
            result, status = await asyncio.to_thread(decode_mxcfg_bytes, raw)

            if status == "empty":
                await update.message.reply_text(
                    "❌ Файл пустой.",
                    reply_markup=kb_back_main(),
                )
                return
            if result is None:
                await update.message.reply_text(
                    "❌ Не удалось прочитать MXCFG.",
                    reply_markup=kb_back_main(),
                )
                return

            text = result.decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(text)
                pretty = pretty_mxcfg_view(parsed)
            except Exception:
                # Если не JSON — показываем raw в блоке кода
                escaped = text[:3500].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                pretty = f"<pre>{escaped}</pre>"

            await send_long_text(update, context, pretty)
            user_mode.pop(uid, None)
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


# ─── Хендлер текстовых сообщений ──────────────────────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    if not is_allowed(uid):
        return  # Молча игнорируем

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
        )
    else:
        await update.message.reply_text(
            "👋 Нажми /start чтобы начать.",
            reply_markup=kb_main(),
        )


# ─── Обработчик ошибок ────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необработанное исключение:", exc_info=context.error)

# ─── Запуск ───────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан.\n"
            "Задай переменную окружения: export BOT_TOKEN=your_token"
        )
    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS не заданы! Никто не сможет управлять доступом. "
            "Задай: export ADMIN_IDS=ваш_telegram_id"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Команды
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("myid",       cmd_myid))
    app.add_handler(CommandHandler("adduser",    cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users",      cmd_users))

    # Callback кнопки
    app.add_handler(CallbackQueryHandler(on_callback))

    # Файлы
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))

    # Текст (должен быть последним)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Глобальный обработчик ошибок
    app.add_error_handler(error_handler)

    logger.info("✅ Бот запущен. Админы: %s", ADMIN_IDS or "не заданы")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
