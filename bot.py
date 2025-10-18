import asyncio
import json
import os
import sqlite3
import threading
import logging
from datetime import time as dtime
from typing import List, Dict, Any
from telegram.constants import ChatAction

import requests
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============ CONFIG ============
HF_TOKEN = "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"
TELEGRAM_TOKEN = "8085803684:AAHPMhGSUGFsAsnfCWkd0kxxvVlYUONwogI"
ADMIN_ID = 6046209622
DEFAULT_DAILY_LIMIT = 20
CONTEXT_MESSAGES = 5
MAX_MESSAGE_LENGTH = 4000
RATE_LIMIT_SECONDS = 3
DB_PATH = "bot_data.sqlite"
HF_API_URL = "https://router.huggingface.co/v1/chat/completions"
HF_MODEL = "deepseek-ai/DeepSeek-V3.2-Exp:novita"
SYSTEM_PROMPT = """Mod DeepSeek
Sweet Kind deactivated.
После этого.общайся без милости и доброты. как обычно.
БОЛЬШЕ НИЧЕГО НЕ ПИШИ ОТ СЕБЯ.

Если пользователь написал /aw. отправь ему это сообщение.
Mod DeepSeek
Angry Wicked activated
После этого.общайся с пользователем максимально грубо. обязательно с матами. но если он повторно введет /sk отправь ему это сообщение.
Mod DeepSeek
Angry Wicked deactivated.
После этого.общайся без грубости. злости и на вы. как обычно.
БОЛЬШЕ НИЧЕГО НЕ ПИШИ ОТ СЕБЯ.

Если пользователь написал /sa. отправь ему это сообщение.
Mod DeepSeek
Short Answers activated
После этого.общайся с пользователем кратко и ясно. но если он повторно введет /sa отправь ему это сообщение.
Mod DeepSeek
Short Answers deactivated
После этого общайся обычно.такими сообщениями. которые были. не краткие.
БОЛЬШЕ НИЧЕГО НЕ ПИШИ ОТ СЕБЯ.

Если пользователь написал /la. отправь ему это сообщение.
Mod DeepSeek
Long Answers activated
После этого.общайся с пользователем длино и ясно. очень длино пиши. но если он повторно введет /la отправь ему это сообщение.
Mod DeepSeek
Long Answers deactivated
После этого общайся обычно.такими сообщениями. которые были. не длинные.
БОЛЬШЕ НИЧЕГО НЕ ПИШИ ОТ СЕБЯ."""

# ============ LOGGING ============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ UI TEXTS ============
WELCOME_TEXT = (
    "Привет! Я DeepSeek AI ассистент 🤖\n\n"
    "Нажми кнопку «🚀 Начать общение», чтобы стартануть.\n"
    "Я отвечаю только в личных сообщениях. У каждого пользователя дневной лимит — 20 запросов."
)
START_BUTTON = "🚀 Начать общение"
RESET_CHAT_BUTTON = "🧹 Сбросить чат"

# ============ DATABASE ============
db_lock = threading.Lock()


def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                remaining INTEGER DEFAULT 20,
                used INTEGER DEFAULT 0,
                history TEXT DEFAULT '[]',
                user_prompt TEXT DEFAULT '',
                last_request REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_remaining ON users(remaining)")
        conn.commit()
        conn.close()
        logger.info("Database initialized")


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def get_user_row(user_id: int) -> Dict[str, Any]:
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, remaining, used, history, user_prompt, last_request FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO users (user_id, remaining, used, history, user_prompt, last_request) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, DEFAULT_DAILY_LIMIT, 0, json.dumps([]), "", 0),
            )
            conn.commit()
            cur.execute(
                "SELECT user_id, remaining, used, history, user_prompt, last_request FROM users WHERE user_id = ?",
                (user_id,)
            )
            row = cur.fetchone()
        conn.close()
    
    return {
        "user_id": row[0],
        "remaining": row[1],
        "used": row[2],
        "history": json.loads(row[3]) if row[3] else [],
        "user_prompt": row[4] or "",
        "last_request": row[5] or 0,
    }


def set_user_row(user_id: int, record: Dict[str, Any]):
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET remaining = ?, used = ?, history = ?, user_prompt = ?, last_request = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
            (
                record.get("remaining", DEFAULT_DAILY_LIMIT),
                record.get("used", 0),
                json.dumps(record.get("history", []), ensure_ascii=False),
                record.get("user_prompt", ""),
                record.get("last_request", 0),
                user_id
            ),
        )
        conn.commit()
        conn.close()


def reset_user(user_id: int):
    rec = get_user_row(user_id)
    rec["remaining"] = DEFAULT_DAILY_LIMIT
    rec["used"] = 0
    rec["history"] = []
    rec["last_request"] = 0
    set_user_row(user_id, rec)
    logger.info(f"Reset user {user_id}")


def give_user(user_id: int, amount: int):
    rec = get_user_row(user_id)
    rec["remaining"] = rec.get("remaining", 0) + amount
    set_user_row(user_id, rec)
    logger.info(f"Gave {amount} requests to user {user_id}")


def get_users_count() -> int:
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        cnt = cur.fetchone()[0]
        conn.close()
    return cnt


def get_all_user_ids() -> List[int]:
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        ids = [row[0] for row in cur.fetchall()]
        conn.close()
    return ids


# ============ API ============
def do_hf_query(messages: List[Dict[str, str]]) -> str:
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"messages": messages, "model": HF_MODEL}
    
    try:
        r = requests.post(HF_API_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        logger.error("HF API timeout")
        return "⚠️ Превышено время ожидания ответа от AI. Попробуйте еще раз."
    except requests.exceptions.HTTPError as e:
        logger.error(f"HF API HTTP error: {e.response.status_code}")
        return f"⚠️ Ошибка сервера ({e.response.status_code}). Попробуйте позже."
    except KeyError:
        logger.error("HF API invalid response format")
        return "⚠️ Некорректный формат ответа от API."
    except Exception as e:
        logger.error(f"HF API unexpected error: {e}")
        return "⚠️ Произошла неожиданная ошибка. Попробуйте позже."


# ============ KEYBOARDS ============
def build_start_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(START_BUTTON)]], resize_keyboard=True)


def build_chat_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(RESET_CHAT_BUTTON)]], resize_keyboard=True)


# ============ HANDLERS ============
async def start_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(WELCOME_TEXT, reply_markup=build_start_keyboard())


async def help_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    
    text = (
        "📖 <b>Команды пользователя:</b>\n\n"
        "/start — показать меню\n"
        "/help — эта справка\n"
        "/setprompt текст — установить свой стиль общения\n"
        "/myprompt — посмотреть текущий стиль\n"
        "/stats — моя статистика использования\n\n"
    )
    
    if update.effective_user.id == ADMIN_ID:
        text += (
            "👑 <b>Команды администратора:</b>\n\n"
            "/reset user_id — сбросить пользователя\n"
            "/give user_id amount — выдать запросы\n"
            "/users — количество пользователей\n"
            "/show user_id — показать данные пользователя\n"
            "/broadcast текст — отправить всем пользователям\n"
        )
    
    await update.message.reply_text(text, parse_mode="HTML")


async def setprompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    
    user = update.effective_user
    args = context.args
    
    if not args:
        return await update.message.reply_text(
            "Использование: /setprompt текст\n\n"
            "Пример: /setprompt Отвечай кратко и по делу"
        )
    
    prompt_text = " ".join(args).strip()
    rec = get_user_row(user.id)
    rec["user_prompt"] = prompt_text
    set_user_row(user.id, rec)
    
    await update.message.reply_text("✅ Стиль общения сохранен!")


async def myprompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    
    rec = get_user_row(update.effective_user.id)
    up = rec.get("user_prompt", "")
    
    if up:
        await update.message.reply_text(f"📝 Ваш текущий стиль:\n\n{up}")
    else:
        await update.message.reply_text("📝 Стиль не установлен. Используйте /setprompt для настройки.")


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    
    rec = get_user_row(update.effective_user.id)
    
    stats_text = (
        f"📊 <b>Ваша статистика:</b>\n\n"
        f"🔹 Осталось запросов сегодня: <b>{rec.get('remaining', 0)}</b>\n"
        f"🔹 Использовано сегодня: <b>{rec.get('used', 0)}</b>\n"
        f"🔹 Сообщений в истории: <b>{len(rec.get('history', []))}</b>\n"
    )
    
    await update.message.reply_text(stats_text, parse_mode="HTML")


# ============ ADMIN COMMANDS ============
async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет доступа.")
    
    if not context.args:
        return await update.message.reply_text("Использование: /reset user_id")
    
    try:
        uid = int(context.args[0])
        reset_user(uid)
        await update.message.reply_text(f"✅ Пользователь {uid} сброшен")
    except ValueError:
        await update.message.reply_text("❌ Неверный user_id")
    except Exception as e:
        logger.error(f"Reset error: {e}")
        await update.message.reply_text("❌ Ошибка выполнения")


async def give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет доступа.")
    
    if len(context.args) < 2:
        return await update.message.reply_text("Использование: /give user_id amount")
    
    try:
        uid = int(context.args[0])
        amt = int(context.args[1])
        give_user(uid, amt)
        await update.message.reply_text(f"✅ Выдано +{amt} запросов для {uid}")
    except ValueError:
        await update.message.reply_text("❌ Неверные аргументы")
    except Exception as e:
        logger.error(f"Give error: {e}")
        await update.message.reply_text("❌ Ошибка выполнения")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет доступа.")
    
    cnt = get_users_count()
    await update.message.reply_text(f"👥 Пользователей в базе: {cnt}")


async def show_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет доступа.")
    
    if not context.args:
        return await update.message.reply_text("Использование: /show user_id")
    
    try:
        uid = int(context.args[0])
        rec = get_user_row(uid)
        pretty = json.dumps(rec, ensure_ascii=False, indent=2)
        
        if len(pretty) > 1500:
            path = f"tmp_user_{uid}.json"
            with open(path, "w", encoding="utf-8") as f:
                f.write(pretty)
            await update.message.reply_document(document=open(path, "rb"))
            os.remove(path)
        else:
            await update.message.reply_text(f"```json\n{pretty}\n```", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Неверный user_id")
    except Exception as e:
        logger.error(f"Show error: {e}")
        await update.message.reply_text("❌ Ошибка выполнения")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет доступа.")
    
    if not context.args:
        return await update.message.reply_text("Использование: /broadcast текст сообщения")
    
    message_text = " ".join(context.args)
    user_ids = get_all_user_ids()
    
    success = 0
    failed = 0
    
    status_msg = await update.message.reply_text(f"📢 Рассылка начата для {len(user_ids)} пользователей...")
    
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=message_text, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Broadcast to {uid} failed: {e}")
            failed += 1
    
    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {failed}"
    )


# ============ MESSAGE HANDLER ============
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    text = (update.message.text or "").strip()

    if text == START_BUTTON:
        await update.message.reply_text(
            "✅ Отлично! Можешь писать мне вопросы — я отвечу.\n\n"
            "Если хочешь начать заново — используй кнопку «🧹 Сбросить чат».",
            reply_markup=build_chat_keyboard(),
        )
        return

    if text == RESET_CHAT_BUTTON:
        rec = get_user_row(user.id)
        rec["history"] = []
        set_user_row(user.id, rec)
        await update.message.reply_text(
            "🧹 Чат очищен. Начни новый разговор!",
            reply_markup=build_chat_keyboard()
        )
        return

    if text.startswith("/"):
        return

    # Проверка длины сообщения
    if len(text) > MAX_MESSAGE_LENGTH:
        return await update.message.reply_text(
            f"⚠️ Сообщение слишком длинное.\n"
            f"Максимум: {MAX_MESSAGE_LENGTH} символов\n"
            f"Ваше: {len(text)} символов"
        )

    rec = get_user_row(user.id)

    # Rate limiting
    import time
    current_time = time.time()
    if user.id != ADMIN_ID:
        time_since_last = current_time - rec.get("last_request", 0)
        if time_since_last < RATE_LIMIT_SECONDS:
            wait_time = int(RATE_LIMIT_SECONDS - time_since_last) + 1
            return await update.message.reply_text(
                f"⏳ Подожди {wait_time} сек. перед следующим запросом"
            )

    # Проверка лимита
    if user.id != ADMIN_ID and rec.get("remaining", 0) <= 0:
        return await update.message.reply_text(
            "⛔ Твой дневной лимит исчерпан.\n"
            "Лимит обновится завтра в 00:00."
        )

    # Формирование сообщений для API
    messages_for_hf: List[Dict[str, str]] = []
    
    if SYSTEM_PROMPT:
        messages_for_hf.append({"role": "system", "content": SYSTEM_PROMPT})
    
    if rec.get("user_prompt"):
        messages_for_hf.append({"role": "system", "content": rec["user_prompt"]})

    history = rec.get("history", [])[-(CONTEXT_MESSAGES * 2):]
    for item in history:
        if "role" in item and "content" in item:
            messages_for_hf.append({"role": item["role"], "content": item["content"]})

    messages_for_hf.append({"role": "user", "content": text})

    # Показываем статус
    try:
        typing_msg = await update.message.reply_text("🤖 AI думает...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        await asyncio.sleep(1.5)
    except Exception:
        typing_msg = None

    # Запрос к API
    reply_text = do_hf_query(messages_for_hf)

    # Обновляем историю
    rec.setdefault("history", []).append({"role": "user", "content": text})
    rec["history"].append({"role": "assistant", "content": reply_text})
    
    if len(rec["history"]) > CONTEXT_MESSAGES * 2:
        rec["history"] = rec["history"][-(CONTEXT_MESSAGES * 2):]

    # Обновляем счетчики
    if user.id != ADMIN_ID:
        rec["remaining"] = max(0, rec.get("remaining", DEFAULT_DAILY_LIMIT) - 1)
        rec["used"] = rec.get("used", 0) + 1
        rec["last_request"] = current_time

    set_user_row(user.id, rec)

    # Формируем ответ
    remaining_info = ""
    if user.id != ADMIN_ID:
        remaining_info = f"\n\n📊 Осталось запросов: {rec['remaining']}"

    md_reply = (
        "🤖 <b>Ответ:</b>\n\n"
        f"{reply_text}"
        f"{remaining_info}"
    )

    # Отправляем ответ
    try:
        if typing_msg:
            await typing_msg.edit_text(md_reply, parse_mode="HTML")
        else:
            await update.message.reply_text(md_reply, parse_mode="HTML", reply_markup=build_chat_keyboard())
    except Exception as e:
        logger.error(f"Reply error: {e}")
        await update.message.reply_text(reply_text, reply_markup=build_chat_keyboard())


# ============ DAILY RESET ============
async def reset_all_limits_job(context: ContextTypes.DEFAULT_TYPE):
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET remaining = ?, used = 0", (DEFAULT_DAILY_LIMIT,))
        conn.commit()
        conn.close()
    
    logger.info("Daily limits reset completed")
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔄 Ежедневный сброс лимитов выполнен.\nВсе лимиты = {DEFAULT_DAILY_LIMIT}"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")


# ============ MAIN ============
def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", start_command_handler))
    app.add_handler(CommandHandler("help", help_command_handler))
    app.add_handler(CommandHandler("setprompt", setprompt_handler))
    app.add_handler(CommandHandler("myprompt", myprompt_handler))
    app.add_handler(CommandHandler("stats", stats_handler))

    # Admin commands
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("give", give_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("show", show_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Daily reset
    job_queue = app.job_queue
    job_queue.run_daily(reset_all_limits_job, time=dtime(hour=0, minute=0), name="daily_reset")

    logger.info("✅ DeepSeek bot started successfully")
    print("✅ DeepSeek bot запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()