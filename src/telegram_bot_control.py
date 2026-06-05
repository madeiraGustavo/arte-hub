"""
Telegram bot для управления кампанией.

Команды:
  /старт   — главное меню
  /стат    — статистика за сегодня
  /аккаунты — список аккаунтов
  /рассылка — запустить дневную рассылку
  /ответы  — проверить ответы сейчас
  /обслуж  — обслуживание (сброс счётчиков + статистика)
  /загрузить — загрузить Excel файл с получателями
  /добавить — добавить аккаунты из текстового файла

Бот работает на сервере и принимает команды только от авторизованного chat_id.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

HELP_TEXT = """
*Gmail Campaign Bot* 🤖

*Команды:*
/стат — статистика за сегодня
/аккаунты — список аккаунтов
/рассылка — запустить рассылку писем
/ответы — проверить ответы и отправить вторые письма
/обслуж — обслуживание и сброс счётчиков

*Загрузка данных:*
/загрузить — отправьте Excel файл с получателями
/добавить — отправьте .txt файл с аккаунтами

/помощь — это сообщение
"""

_running: dict[str, bool] = {}


class TelegramBot:
    def __init__(self, token: str, allowed_chat_id: str, config):
        self.token = token
        self.allowed_chat_id = str(allowed_chat_id)
        self.config = config
        self.api = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    async def send(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{self.api}/sendMessage", json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })

    async def send_to_owner(self, text: str) -> None:
        await self.send(self.allowed_chat_id, text)

    async def get_updates(self) -> list:
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                resp = await client.get(f"{self.api}/getUpdates", params={
                    "offset": self.offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                })
                data = resp.json()
                return data.get("result", [])
        except Exception as exc:
            log.warning("getUpdates error: %s", exc)
            return []

    async def handle_update(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return

        chat_id = str(msg["chat"]["id"])
        if chat_id != self.allowed_chat_id:
            await self.send(chat_id, "⛔ Доступ запрещён.")
            return

        # Handle file uploads
        if "document" in msg:
            await self.handle_document(msg)
            return

        text = msg.get("text", "").strip().lower()

        if text in ("/start", "/старт", "старт"):
            await self.cmd_start()
        elif text in ("/стат", "/stats", "/stat"):
            await self.cmd_stats()
        elif text in ("/аккаунты", "/accounts"):
            await self.cmd_accounts()
        elif text in ("/рассылка", "/blast"):
            await self.cmd_blast()
        elif text in ("/ответы", "/replies", "/check"):
            await self.cmd_replies()
        elif text in ("/обслуж", "/maintenance"):
            await self.cmd_maintenance()
        elif text in ("/загрузить", "/upload"):
            await self.send_to_owner(
                "📎 Отправьте Excel файл (.xlsx) с получателями прямо в этот чат.\n"
                "Формат: колонка A — email, колонка B — тема письма."
            )
        elif text in ("/добавить", "/addaccounts"):
            await self.send_to_owner(
                "📎 Отправьте .txt файл с аккаунтами прямо в этот чат.\n"
                "Формат каждой строки:\n`email:пароль:запасная@почта:2fa_ключ`\n"
                "или без 2FA:\n`email:пароль:запасная@почта`"
            )
        elif text in ("/помощь", "/help"):
            await self.send_to_owner(HELP_TEXT)
        else:
            await self.send_to_owner(
                "Не понял команду. Отправьте /помощь для списка команд."
            )

    async def cmd_start(self) -> None:
        await self.send_to_owner(
            "👋 *Gmail Campaign Bot запущен!*\n\n"
            "Управляйте кампанией прямо из Telegram.\n\n"
            + HELP_TEXT
        )

    async def cmd_stats(self) -> None:
        from .database import get_daily_stats
        stats = get_daily_stats(self.config.paths.db_path)
        text = (
            f"📊 *Статистика за {stats['date']}*\n\n"
            f"✉️ Первых писем отправлено:  *{stats['first_sent']}*\n"
            f"💬 Ответов получено:         *{stats['replied']}*\n"
            f"🔗 Вторых писем отправлено:  *{stats['second_sent']}*\n\n"
            f"✅ Активных аккаунтов:   {stats['active_accounts']}\n"
            f"⚠️ Исчерпанных:          {stats['exhausted_accounts']}\n"
            f"🚫 Заблокированных:      {stats['blocked_accounts']}"
        )
        await self.send_to_owner(text)

    async def cmd_accounts(self) -> None:
        from .database import get_active_accounts
        import sqlite3
        db = self.config.paths.db_path
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT email, status, daily_sent, total_sent FROM accounts ORDER BY status, total_sent DESC LIMIT 30"
        ).fetchall()
        conn.close()

        if not rows:
            await self.send_to_owner("📭 Аккаунтов нет. Добавьте через /добавить")
            return

        status_icons = {"active": "✅", "blocked": "🚫", "exhausted": "⚠️"}
        lines = ["*Аккаунты:*\n"]
        for r in rows:
            icon = status_icons.get(r["status"], "❓")
            lines.append(
                f"{icon} `{r['email']}`\n"
                f"   Сегодня: {r['daily_sent']} | Всего: {r['total_sent']}\n"
            )
        await self.send_to_owner("\n".join(lines))

    async def cmd_blast(self) -> None:
        if _running.get("blast"):
            await self.send_to_owner("⏳ Рассылка уже запущена, подождите.")
            return
        await self.send_to_owner("🚀 Запускаю дневную рассылку...")
        asyncio.create_task(self._run_blast())

    async def _run_blast(self) -> None:
        from .campaign_runner import run_daily_blast
        _running["blast"] = True
        try:
            await run_daily_blast(self.config)
            await self.send_to_owner("✅ Рассылка завершена! Используйте /стат для статистики.")
        except Exception as exc:
            await self.send_to_owner(f"❌ Ошибка рассылки: {exc}")
        finally:
            _running["blast"] = False

    async def cmd_replies(self) -> None:
        if _running.get("replies"):
            await self.send_to_owner("⏳ Проверка ответов уже запущена.")
            return
        await self.send_to_owner("🔍 Проверяю ответы...")
        asyncio.create_task(self._run_replies())

    async def _run_replies(self) -> None:
        from .campaign_runner import run_reply_check
        _running["replies"] = True
        try:
            await run_reply_check(self.config)
            await self.send_to_owner("✅ Проверка ответов завершена! Используйте /стат для статистики.")
        except Exception as exc:
            await self.send_to_owner(f"❌ Ошибка проверки: {exc}")
        finally:
            _running["replies"] = False

    async def cmd_maintenance(self) -> None:
        await self.send_to_owner("⚙️ Запускаю обслуживание...")
        asyncio.create_task(self._run_maintenance())

    async def _run_maintenance(self) -> None:
        from .campaign_runner import run_maintenance
        try:
            await run_maintenance(self.config)
            await self.send_to_owner("✅ Обслуживание завершено.")
        except Exception as exc:
            await self.send_to_owner(f"❌ Ошибка: {exc}")

    async def handle_document(self, msg: dict) -> None:
        doc = msg["document"]
        file_name = doc.get("file_name", "")
        file_id = doc["file_id"]

        if file_name.endswith(".xlsx") or file_name.endswith(".xls"):
            await self.process_excel_upload(file_id, file_name)
        elif file_name.endswith(".txt"):
            await self.process_accounts_upload(file_id, file_name)
        else:
            await self.send_to_owner(
                "⚠️ Неизвестный тип файла.\n"
                "Для получателей: .xlsx\n"
                "Для аккаунтов: .txt"
            )

    async def process_excel_upload(self, file_id: str, file_name: str) -> None:
        await self.send_to_owner(f"📥 Получил файл `{file_name}`, обрабатываю...")
        try:
            path = await self._download_file(file_id, file_name)
            from .excel_reader import read_recipients
            from .language_detector import detect_languages_bulk
            from .database import upsert_recipients
            rows = read_recipients(path)
            rows_with_lang = detect_languages_bulk([dict(r) for r in rows])
            added = upsert_recipients(self.config.paths.db_path, rows_with_lang)
            # Show language breakdown
            langs = {}
            for r in rows_with_lang:
                langs[r["language"]] = langs.get(r["language"], 0) + 1
            lang_text = " | ".join(f"{k.upper()}: {v}" for k, v in langs.items())
            await self.send_to_owner(
                f"✅ Загружено получателей: *{added}* новых из {len(rows)} строк\n"
                f"Языки: {lang_text}\n\n"
                f"Теперь можете запустить /рассылка"
            )
        except Exception as exc:
            await self.send_to_owner(f"❌ Ошибка обработки Excel: {exc}")

    async def process_accounts_upload(self, file_id: str, file_name: str) -> None:
        await self.send_to_owner(f"📥 Получил файл `{file_name}`, обрабатываю...")
        try:
            path = await self._download_file(file_id, file_name)
            from .account_parser import parse_accounts_file
            from .database import upsert_account
            accounts = parse_accounts_file(path)
            for acc in accounts:
                upsert_account(self.config.paths.db_path, acc)
            await self.send_to_owner(
                f"✅ Добавлено/обновлено аккаунтов: *{len(accounts)}*\n"
                f"Используйте /аккаунты для просмотра."
            )
        except Exception as exc:
            await self.send_to_owner(f"❌ Ошибка обработки аккаунтов: {exc}")

    async def _download_file(self, file_id: str, file_name: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.api}/getFile", params={"file_id": file_id})
            file_path = r.json()["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            content = (await client.get(url)).content

        save_dir = Path(self.config.paths.recipients_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        dest = str(save_dir / file_name)
        with open(dest, "wb") as fh:
            fh.write(content)
        return dest

    async def run_polling(self) -> None:
        log.info("Telegram bot started (polling)")
        await self.send_to_owner("🟢 *Бот запущен и готов к работе!*\n\nОтправьте /помощь для списка команд.")
        while True:
            updates = await self.get_updates()
            for update in updates:
                self.offset = update["update_id"] + 1
                try:
                    await self.handle_update(update)
                except Exception as exc:
                    log.error("Error handling update: %s", exc)
            if not updates:
                await asyncio.sleep(0.5)


async def start_bot(config) -> None:
    token = config.telegram.bot_token
    chat_id = config.telegram.chat_id
    if not token or not chat_id:
        raise RuntimeError(
            "Telegram bot_token и chat_id должны быть заполнены в config.json"
        )
    bot = TelegramBot(token, chat_id, config)
    await bot.run_polling()
