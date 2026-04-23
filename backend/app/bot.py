"""Minimal Telegram bot: entry point for the Mini App + admin commands."""

from __future__ import annotations

import asyncio
import logging

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from app import access
from app.config import ADMIN_IDS, BOT_TOKEN, BOT_WEBAPP_URL

logger = logging.getLogger("ai_bot.bot")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    if not access.is_allowed(user.id):
        await message.reply_text(
            "⛔ <b>Нет доступа</b>\n"
            f"🆔 Твой ID: <code>{user.id}</code>\n\n"
            "Попроси администратора добавить тебя.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not BOT_WEBAPP_URL:
        await message.reply_text(
            "⚠️ WebApp URL не настроен. Администратору: задайте "
            "<code>BOT_WEBAPP_URL</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🚀 Открыть приложение",
                    web_app=WebAppInfo(url=BOT_WEBAPP_URL),
                )
            ]
        ]
    )
    await message.reply_text(
        "👋 <b>AI Bot · декодер конфигов</b>\n\n"
        "Нажми кнопку ниже, чтобы открыть Mini App. Там ты сможешь "
        "расшифровать или просмотреть <b>NETCFG</b>/<b>MXCFG</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    m = update.effective_message
    if not u or not m:
        return
    username = f"@{u.username}" if u.username else "—"
    await m.reply_text(
        "🆔 <b>Ваш профиль</b>\n"
        f"<b>ID:</b> <code>{u.id}</code>\n"
        f"<b>Имя:</b> {u.full_name}\n"
        f"<b>Username:</b> {username}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    m = update.effective_message
    if not u or not m:
        return
    if not access.is_admin(u.id):
        await m.reply_text("⛔ Только для администраторов.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await m.reply_text(
            "Использование: <code>/adduser &lt;id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = int(context.args[0])
    added = await access.add_user(target)
    if added:
        await m.reply_text(
            f"✅ Пользователь <code>{target}</code> добавлен.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await m.reply_text(
            f"ℹ️ <code>{target}</code> уже в списке или админ.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_removeuser(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    u = update.effective_user
    m = update.effective_message
    if not u or not m:
        return
    if not access.is_admin(u.id):
        await m.reply_text("⛔ Только для администраторов.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await m.reply_text(
            "Использование: <code>/removeuser &lt;id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = int(context.args[0])
    if target in ADMIN_IDS:
        await m.reply_text("⛔ Нельзя удалить администратора.")
        return
    removed = await access.remove_user(target)
    if removed:
        await m.reply_text(
            f"✅ Пользователь <code>{target}</code> удалён.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await m.reply_text(
            f"ℹ️ <code>{target}</code> не найден.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    m = update.effective_message
    if not u or not m:
        return
    if not access.is_admin(u.id):
        await m.reply_text("⛔ Только для администраторов.")
        return
    snap = access.snapshot()
    lines: list[str] = [
        f"<b>👑 Администраторы</b> <i>({len(snap['admins'])})</i>",
    ]
    for aid in snap["admins"]:
        lines.append(f"  • <code>{aid}</code>")

    if snap["whitelist"]:
        lines.append("")
        lines.append(
            f"<b>📋 Пользователи с доступом</b> <i>({len(snap['whitelist'])})</i>"
        )
        for wid in snap["whitelist"]:
            lines.append(f"  • <code>{wid}</code>")
    else:
        lines.append("")
        lines.append("📋 Белый список пуст.")

    await m.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


PUBLIC_COMMANDS = [
    BotCommand("start", "Открыть Mini App"),
    BotCommand("myid", "Показать мой Telegram ID"),
]

ADMIN_EXTRA_COMMANDS = [
    BotCommand("adduser", "Добавить пользователя"),
    BotCommand("removeuser", "Удалить пользователя"),
    BotCommand("users", "Список пользователей"),
]


async def _set_commands(application: Application) -> None:
    try:
        await application.bot.set_my_commands(PUBLIC_COMMANDS)
    except Exception as exc:
        logger.warning("Не удалось задать глобальные команды: %s", exc)
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_EXTRA_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as exc:
            logger.warning(
                "Не удалось задать admin-команды для %s: %s", admin_id, exc
            )


async def run_bot() -> None:
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN пуст — бот не будет запущен.")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("myid", cmd_myid))
    application.add_handler(CommandHandler("adduser", cmd_adduser))
    application.add_handler(CommandHandler("removeuser", cmd_removeuser))
    application.add_handler(CommandHandler("users", cmd_users))

    await application.initialize()
    await _set_commands(application)
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("🤖 Бот запущен. Админы: %s", sorted(ADMIN_IDS))

    try:
        # Keep running until the task is cancelled.
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await application.updater.stop()
        except Exception:
            pass
        try:
            await application.stop()
        except Exception:
            pass
        try:
            await application.shutdown()
        except Exception:
            pass
