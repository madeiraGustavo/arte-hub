"""
Telegram Bot — полное управление рассыльщиком прямо из Telegram.

Команды:
  /start     — приветствие
  /stats     — статистика за сегодня
  /accounts  — список аккаунтов и статусы
  /send      — запустить рассылку прямо сейчас
  /check     — проверить ответы прямо сейчас
  /logs      — последние 20 строк лога
  /stop      — остановить текущую операцию
  /help      — все команды

Файлы:
  Отправь .xlsx файл боту → он сохранится как recipients.xlsx
  Отправь .txt файл боту  → он сохранится как accounts.txt и загрузится в БД
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Update, Document
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from .config import AppConfig
from .database import Database

logger = logging.getLogger(__name__)

# Текущая задача (чтобы можно было остановить)
_current_task: Optional[asyncio.Task] = None


def _esc(text: str) -> str:
    """Escape MarkdownV2 special chars."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


class MailerBot:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.admin_id = config.telegram.admin_chat_id
        self._app: Optional[Application] = None
        self._send_wave_fn = None    # set externally
        self._reply_check_fn = None  # set externally

    def set_handlers(self, send_wave_fn, reply_check_fn) -> None:
        """Inject send_wave and reply_check coroutine factories."""
        self._send_wave_fn = send_wave_fn
        self._reply_check_fn = reply_check_fn

    def _check_admin(self, update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else 0
        return self.admin_id == 0 or uid == self.admin_id

    # -----------------------------------------------------------------------
    # /start  /help
    # -----------------------------------------------------------------------

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_admin(update):
            return
        text = (
            "🤖 *Gmail Mailer Bot*\n\n"
            "Управляй рассыльщиком прямо отсюда:\n\n"
            "📊 /stats — статистика за сегодня\n"
            "👥 /accounts — список аккаунтов\n"
            "📨 /send — запустить рассылку сейчас\n"
            "🔍 /check — проверить ответы сейчас\n"
            "📋 /logs — последние строки лога\n"
            "⏹ /stop — остановить операцию\n\n"
            "📎 Отправь `.xlsx` — новый список получателей\n"
            "📎 Отправь `.txt` — новые аккаунты"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self.cmd_start(update, ctx)

    # -----------------------------------------------------------------------
    # /stats
    # -----------------------------------------------------------------------

    async def cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_admin(update):
            return
        stats = await self.db.get_daily_stats()

        # Count accounts by status
        async with self.db._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM accounts GROUP BY status"
        ) as cur:
            rows = await cur.fetchall()
        acct_lines = "\n".join(
            f"  • {r['status']}: {r['cnt']}" for r in rows
        ) or "  • нет аккаунтов"

        text = (
            f"📊 *Статистика за {stats['date']}*\n\n"
            f"📨 Первых писем отправлено: *{stats['first_sent']}*\n"
            f"💬 Ответов получено: *{stats['replies']}*\n"
            f"✅ Вторых писем отправлено: *{stats['second_sent']}*\n\n"
            f"*Аккаунты:*\n{acct_lines}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # -----------------------------------------------------------------------
    # /accounts
    # -----------------------------------------------------------------------

    async def cmd_accounts(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_admin(update):
            return
        async with self.db._conn.execute(
            "SELECT email, status, daily_sent, total_sent, app_password FROM accounts ORDER BY status, email"
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            await update.message.reply_text("Аккаунтов нет.")
            return

        status_emoji = {
            "active": "🟢", "blocked": "🔴", "exhausted": "🟡", "error": "⚠️"
        }
        lines = []
        for r in rows:
            emoji = status_emoji.get(r["status"], "⚪")
            app_pwd = "✓ app" if r["app_password"] else "✗ app"
            lines.append(
                f"{emoji} `{r['email']}`\n"
                f"   {r['status']} | отправлено: {r['daily_sent']}/день, {r['total_sent']}/всего | {app_pwd}"
            )

        text = "👥 *Аккаунты:*\n\n" + "\n\n".join(lines)
        # Split if too long
        if len(text) > 4000:
            text = text[:4000] + "\n…"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # -----------------------------------------------------------------------
    # /send
    # -----------------------------------------------------------------------

    async def cmd_send(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        global _current_task
        if not self._check_admin(update):
            return
        if _current_task and not _current_task.done():
            await update.message.reply_text("⚠️ Уже выполняется операция. Дождись завершения или /stop")
            return
        if not self._send_wave_fn:
            await update.message.reply_text("❌ send_wave не подключён")
            return

        await update.message.reply_text("📨 Запускаю рассылку…")

        async def _run():
            try:
                result = await self._send_wave_fn()
                await update.message.reply_text(
                    f"✅ Рассылка завершена\\!\n"
                    f"Отправлено: *{result.get('first_sent', 0)}*\n"
                    f"Ошибок: *{result.get('errors', 0)}*",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except asyncio.CancelledError:
                await update.message.reply_text("⏹ Рассылка остановлена")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}")

        _current_task = asyncio.create_task(_run())

    # -----------------------------------------------------------------------
    # /check
    # -----------------------------------------------------------------------

    async def cmd_check(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        global _current_task
        if not self._check_admin(update):
            return
        if _current_task and not _current_task.done():
            await update.message.reply_text("⚠️ Уже выполняется операция. /stop чтобы остановить")
            return
        if not self._reply_check_fn:
            await update.message.reply_text("❌ reply_checker не подключён")
            return

        await update.message.reply_text("🔍 Проверяю ответы…")

        async def _run():
            try:
                result = await self._reply_check_fn()
                await update.message.reply_text(
                    f"✅ Проверка завершена\\!\n"
                    f"Ответов найдено: *{result.get('replies_found', 0)}*\n"
                    f"Вторых писем отправлено: *{result.get('second_sent', 0)}*",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except asyncio.CancelledError:
                await update.message.reply_text("⏹ Проверка остановлена")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}")

        _current_task = asyncio.create_task(_run())

    # -----------------------------------------------------------------------
    # /stop
    # -----------------------------------------------------------------------

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        global _current_task
        if not self._check_admin(update):
            return
        if _current_task and not _current_task.done():
            _current_task.cancel()
            await update.message.reply_text("⏹ Операция остановлена")
        else:
            await update.message.reply_text("Нет активных операций")

    # -----------------------------------------------------------------------
    # /logs
    # -----------------------------------------------------------------------

    async def cmd_logs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_admin(update):
            return
        log_file = Path(self.config.paths.logs_dir) / "mailer.log"
        if not log_file.exists():
            await update.message.reply_text("Лог файл не найден")
            return

        # Read last 30 lines
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        last_lines = lines[-30:] if len(lines) > 30 else lines

        # Filter to show only WARNING+ and keep INFO brief
        output = []
        for line in last_lines:
            line = line.rstrip()
            if not line:
                continue
            # Shorten line
            if len(line) > 120:
                line = line[:120] + "…"
            output.append(line)

        text = "```\n" + "\n".join(output[-25:]) + "\n```"
        if len(text) > 4000:
            text = "```\n" + "\n".join(output[-10:]) + "\n```"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # -----------------------------------------------------------------------
    # File handler — .xlsx и .txt
    # -----------------------------------------------------------------------

    async def handle_file(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_admin(update):
            return
        doc: Document = update.message.document
        if not doc:
            return

        filename = doc.file_name or ""
        file = await ctx.bot.get_file(doc.file_id)

        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            dest = Path(self.config.paths.recipients_excel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            await file.download_to_drive(str(dest))
            await update.message.reply_text(
                f"✅ Файл получателей сохранён: `{dest.name}`\n"
                "Запусти /send чтобы начать рассылку",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif filename.endswith(".txt"):
            dest = Path(self.config.paths.accounts_file)
            dest.parent.mkdir(parents=True, exist_ok=True)
            await file.download_to_drive(str(dest))

            # Import accounts into DB
            from .account_manager import AccountManager
            mgr = AccountManager(self.db, self.config.paths.accounts_file)
            count = await mgr.sync_accounts_from_file()
            await update.message.reply_text(
                f"✅ Аккаунты загружены: *{count}* шт\n"
                "Проверь /accounts",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                "Поддерживаемые форматы:\n"
                "• `.xlsx` — список получателей\n"
                "• `.txt` — список аккаунтов",
                parse_mode=ParseMode.MARKDOWN,
            )

    # -----------------------------------------------------------------------
    # Start / Stop bot
    # -----------------------------------------------------------------------

    def build_app(self) -> Application:
        app = Application.builder().token(self.config.telegram.bot_token).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("stats", self.cmd_stats))
        app.add_handler(CommandHandler("accounts", self.cmd_accounts))
        app.add_handler(CommandHandler("send", self.cmd_send))
        app.add_handler(CommandHandler("check", self.cmd_check))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(CommandHandler("logs", self.cmd_logs))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_file))

        self._app = app
        return app

    async def send_message(self, text: str) -> None:
        """Send a message to admin (used by notifier)."""
        if not self._app or not self.admin_id:
            return
        try:
            await self._app.bot.send_message(
                chat_id=self.admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning("Bot send_message failed: %s", exc)
