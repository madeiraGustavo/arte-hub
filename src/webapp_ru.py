"""
Telegram Mini App — русский интерфейс.
Открывается внутри Telegram в полный экран.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import asyncio

_config = None
_db_path = "data/campaign.db"


def _get_config():
    global _config, _db_path
    if _config is None:
        from .config import Config
        _config = Config.load("config.json")
        _db_path = _config.paths.db_path
    return _config


@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_config()
    yield


app = FastAPI(lifespan=lifespan)

_running: dict[str, bool] = {}


@app.get("/api/стат")
async def api_stats():
    from .database import get_daily_stats
    return get_daily_stats(_db_path)


@app.get("/api/аккаунты")
async def api_accounts():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT email, status, daily_sent, total_sent, last_sent_date, "
        "date_added, first_login_completed FROM accounts ORDER BY status, total_sent DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/получатели")
async def api_recipients():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    r = conn.execute("""
        SELECT
            SUM(CASE WHEN status='pending'     THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status='first_sent'  THEN 1 ELSE 0 END) as first_sent,
            SUM(CASE WHEN status='replied'     THEN 1 ELSE 0 END) as replied,
            SUM(CASE WHEN status='second_sent' THEN 1 ELSE 0 END) as second_sent,
            SUM(CASE WHEN status='bad'         THEN 1 ELSE 0 END) as bad,
            COUNT(*) as total
        FROM recipients
    """).fetchone()
    conn.close()
    return dict(r)


@app.get("/api/логи")
async def api_logs(limit: int = 80, уровень: str = ""):
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    if уровень:
        rows = conn.execute(
            "SELECT * FROM logs WHERE level=? ORDER BY id DESC LIMIT ?",
            (уровень.upper(), limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/действие/{действие}")
async def api_action(действие: str):
    if действие not in ("рассылка", "ответы", "обслуживание"):
        return JSONResponse({"ошибка": "неизвестное действие"}, status_code=400)
    if _running.get(действие):
        return JSONResponse({"статус": "уже_запущено"})
    cfg = _get_config()
    from .campaign_runner import run_daily_blast, run_reply_check, run_maintenance

    async def run():
        _running[действие] = True
        try:
            if действие == "рассылка":
                await run_daily_blast(cfg)
            elif действие == "ответы":
                await run_reply_check(cfg)
            elif действие == "обслуживание":
                await run_maintenance(cfg)
        finally:
            _running[действие] = False

    asyncio.create_task(run())
    return JSONResponse({"статус": "запущено"})


@app.get("/api/статус")
async def api_running():
    return _running


@app.get("/api/конфиг")
async def api_config_get():
    p = Path("config.json")
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/конфиг")
async def api_config_save(request: Request):
    body = await request.json()
    allowed = {"proxy", "telegram", "campaign", "paths", "headless", "missing_2fa_strategy"}
    filtered = {k: v for k, v in body.items() if k in allowed}
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=2, ensure_ascii=False)
    global _config
    _config = None
    _get_config()
    return {"статус": "сохранено"}


@app.get("/api/шаблоны")
async def api_templates():
    result = {}
    for lang, name in [("en", "Английский"), ("nl", "Нидерландский"), ("fr", "Французский"), ("de", "Немецкий")]:
        for stage, sname in [("first", "первое"), ("second", "второе")]:
            key = f"{lang}_{stage}"
            path = Path("templates") / f"{key}.txt"
            result[key] = path.read_text(encoding="utf-8") if path.exists() else ""
    return result


@app.post("/api/шаблоны/{ключ}")
async def api_template_save(ключ: str, request: Request):
    allowed = {f"{l}_{s}" for l in ("en","nl","fr","de") for s in ("first","second")}
    if ключ not in allowed:
        return JSONResponse({"ошибка": "неверный ключ"}, status_code=400)
    body = await request.json()
    text = body.get("текст", "")
    path = Path("templates") / f"{ключ}.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"статус": "сохранено"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(WEBAPP_HTML)


WEBAPP_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Gmail Рассылка</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --surface2: #22263a;
    --border: #2e3250; --text: #e2e8f0; --muted: #8892a4;
    --blue: #4f8ef7; --green: #22c55e; --yellow: #f59e0b;
    --red: #ef4444; --orange: #f97316; --purple: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, 'Segoe UI', sans-serif; font-size: 14px; min-height: 100vh; }

  /* ── Нижняя навигация ── */
  .nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--surface); border-top: 1px solid var(--border); display: flex; z-index: 100; }
  .nav-btn { flex: 1; padding: 10px 4px 8px; border: none; background: none; color: var(--muted); font-size: 10px; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 3px; }
  .nav-btn svg { width: 22px; height: 22px; }
  .nav-btn.active { color: var(--blue); }
  .page { display: none; padding: 14px 14px 80px; }
  .page.active { display: block; }

  /* ── Карточки ── */
  .cards { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 14px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 8px; text-align: center; }
  .card .лейбл { color: var(--muted); font-size: 9px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 6px; }
  .card .значение { font-size: 24px; font-weight: 700; }
  .c-blue .значение { color: var(--blue); }
  .c-green .значение { color: var(--green); }
  .c-yellow .значение { color: var(--yellow); }
  .c-red .значение { color: var(--red); }
  .c-orange .значение { color: var(--orange); }
  .c-purple .значение { color: var(--purple); }

  /* ── Кнопки действий ── */
  .actions { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
  .btn-action { padding: 14px; border-radius: 10px; border: none; font-size: 15px; font-weight: 600; cursor: pointer; width: 100%; transition: opacity .15s; }
  .btn-action:active { opacity: .7; }
  .btn-action:disabled { opacity: .4; }
  .btn-рассылка { background: var(--blue); color: #fff; }
  .btn-ответы { background: var(--green); color: #fff; }
  .btn-обслуж { background: var(--yellow); color: #000; }

  /* ── Получатели ── */
  .прогресс-бар { display: flex; height: 14px; border-radius: 6px; overflow: hidden; gap: 2px; margin: 10px 0; }
  .сегмент { height: 100%; transition: width .4s; }
  .s-pending { background: var(--muted); }
  .s-first_sent { background: var(--blue); }
  .s-replied { background: var(--purple); }
  .s-second_sent { background: var(--green); }
  .s-bad { background: var(--red); }
  .легенда { display: flex; flex-wrap: wrap; gap: 8px; }
  .л-item { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--muted); }
  .л-dot { width: 8px; height: 8px; border-radius: 50%; }

  /* ── Таблица аккаунтов ── */
  .акк-список { display: flex; flex-direction: column; gap: 8px; }
  .акк-карта { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
  .акк-email { font-family: monospace; font-size: 12px; margin-bottom: 4px; word-break: break-all; }
  .акк-инфо { display: flex; gap: 8px; font-size: 11px; color: var(--muted); flex-wrap: wrap; }
  .бейдж { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 600; }
  .б-active { background: rgba(34,197,94,.15); color: var(--green); }
  .б-blocked { background: rgba(239,68,68,.15); color: var(--red); }
  .б-exhausted { background: rgba(245,158,11,.15); color: var(--yellow); }

  /* ── Логи ── */
  .лог-список { display: flex; flex-direction: column; gap: 6px; }
  .лог-строка { background: var(--surface); border-radius: 6px; padding: 8px 10px; font-size: 11px; }
  .лог-время { color: var(--muted); margin-bottom: 2px; }
  .лог-msg { word-break: break-word; }
  .б-INFO { background: rgba(79,142,247,.15); color: var(--blue); }
  .б-WARNING { background: rgba(245,158,11,.15); color: var(--yellow); }
  .б-ERROR { background: rgba(239,68,68,.15); color: var(--red); }

  /* ── Настройки ── */
  .форм-блок { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 12px; }
  .форм-блок h3 { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 12px; }
  .форм-группа { margin-bottom: 10px; }
  .форм-группа label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .форм-группа input, .форм-группа select {
    width: 100%; background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); padding: 9px 10px; border-radius: 7px; font-size: 13px; outline: none;
  }
  .форм-группа input:focus { border-color: var(--blue); }
  .btn-сохранить { background: var(--blue); color: #fff; padding: 13px; border: none; border-radius: 8px; width: 100%; font-size: 14px; font-weight: 600; cursor: pointer; margin-top: 4px; }

  /* ── Шаблоны ── */
  .яз-кнопки { display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
  .яз-кнопка { padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border); background: none; color: var(--muted); cursor: pointer; font-size: 12px; }
  .яз-кнопка.active { background: var(--blue); color: #fff; border-color: var(--blue); }
  .этап-кнопки { display: flex; gap: 6px; margin-bottom: 10px; }
  .этап-кнопка { flex: 1; padding: 8px; border-radius: 7px; border: 1px solid var(--border); background: none; color: var(--muted); cursor: pointer; font-size: 12px; text-align: center; }
  .этап-кнопка.active { background: var(--surface2); color: var(--text); }
  textarea.редактор {
    width: 100%; min-height: 220px; background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); padding: 12px; border-radius: 8px; font-family: monospace;
    font-size: 12px; resize: vertical; outline: none; line-height: 1.5;
  }
  .подсказка { font-size: 10px; color: var(--muted); margin-bottom: 8px; line-height: 1.4; }
  .подсказка code { background: var(--surface2); padding: 1px 5px; border-radius: 3px; color: var(--blue); }

  /* ── Уведомление ── */
  #уведомление { position: fixed; top: 16px; left: 50%; transform: translateX(-50%); background: var(--surface); border: 1px solid var(--border); padding: 10px 18px; border-radius: 8px; font-size: 13px; display: none; z-index: 999; white-space: nowrap; box-shadow: 0 4px 20px rgba(0,0,0,.5); }
  #уведомление.показать { display: block; }

  .секция { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
  .секция h2 { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 10px; }
</style>
</head>
<body>

<!-- ══ СТРАНИЦА: ГЛАВНАЯ ══ -->
<div class="page active" id="стр-главная">

  <div class="cards">
    <div class="card c-green">
      <div class="лейбл">Активных</div>
      <div class="значение" id="с-активных">—</div>
    </div>
    <div class="card c-blue">
      <div class="лейбл">Отправлено</div>
      <div class="значение" id="с-отправлено">—</div>
    </div>
    <div class="card c-purple">
      <div class="лейбл">Ответов</div>
      <div class="значение" id="с-ответов">—</div>
    </div>
    <div class="card c-orange">
      <div class="лейбл">2-х писем</div>
      <div class="значение" id="с-вторых">—</div>
    </div>
    <div class="card c-red">
      <div class="лейбл">Блок.</div>
      <div class="значение" id="с-блок">—</div>
    </div>
    <div class="card c-yellow">
      <div class="лейбл">Исчерп.</div>
      <div class="значение" id="с-исчерп">—</div>
    </div>
  </div>

  <div class="actions">
    <button class="btn-action btn-рассылка" onclick="запустить('рассылка', this)">▶ Запустить рассылку</button>
    <button class="btn-action btn-ответы" onclick="запустить('ответы', this)">🔍 Проверить ответы</button>
    <button class="btn-action btn-обслуж" onclick="запустить('обслуживание', this)">⚙ Обслуживание</button>
  </div>

  <div class="секция">
    <h2>Получатели</h2>
    <div class="прогресс-бар" id="прогресс">
      <div class="сегмент s-pending"     id="п-ожидает"    style="width:0%"></div>
      <div class="сегмент s-first_sent"  id="п-отправлено" style="width:0%"></div>
      <div class="сегмент s-replied"     id="п-ответили"   style="width:0%"></div>
      <div class="сегмент s-second_sent" id="п-второе"     style="width:0%"></div>
      <div class="сегмент s-bad"         id="п-битые"      style="width:0%"></div>
    </div>
    <div class="легенда">
      <div class="л-item"><div class="л-dot" style="background:var(--muted)"></div> Ожидают <b id="ч-ожидает">0</b></div>
      <div class="л-item"><div class="л-dot" style="background:var(--blue)"></div> Отправлено <b id="ч-отправлено">0</b></div>
      <div class="л-item"><div class="л-dot" style="background:var(--purple)"></div> Ответили <b id="ч-ответили">0</b></div>
      <div class="л-item"><div class="л-dot" style="background:var(--green)"></div> Готово <b id="ч-второе">0</b></div>
      <div class="л-item"><div class="л-dot" style="background:var(--red)"></div> Битые <b id="ч-битые">0</b></div>
      <div class="л-item" style="margin-left:auto;">Всего: <b id="ч-всего">0</b></div>
    </div>
  </div>
</div>

<!-- ══ СТРАНИЦА: АККАУНТЫ ══ -->
<div class="page" id="стр-аккаунты">
  <div class="секция">
    <h2>Аккаунты <span id="кол-акк" style="float:right;color:var(--text)"></span></h2>
    <div class="акк-список" id="список-акк"></div>
  </div>
</div>

<!-- ══ СТРАНИЦА: ЛОГИ ══ -->
<div class="page" id="стр-логи">
  <div class="секция">
    <h2>Живые логи</h2>
    <div class="лог-список" id="список-логов"></div>
  </div>
</div>

<!-- ══ СТРАНИЦА: ШАБЛОНЫ ══ -->
<div class="page" id="стр-шаблоны">
  <div class="форм-блок">
    <h3>Тексты писем</h3>
    <p class="подсказка">
      Плейсхолдеры: <code>{{recipient_email}}</code> <code>{{subject}}</code> <code>{{unique_link}}</code><br>
      Первая строка: <code>SUBJECT: тема письма</code>
    </p>
    <div class="яз-кнопки">
      <button class="яз-кнопка active" onclick="смЯзык('en',this)">🇬🇧 Английский</button>
      <button class="яз-кнопка" onclick="смЯзык('nl',this)">🇳🇱 Нидерланд.</button>
      <button class="яз-кнопка" onclick="смЯзык('fr',this)">🇫🇷 Французский</button>
      <button class="яз-кнопка" onclick="смЯзык('de',this)">🇩🇪 Немецкий</button>
    </div>
    <div class="этап-кнопки">
      <button class="этап-кнопка active" onclick="смЭтап('first',this)">1-е письмо</button>
      <button class="этап-кнопка" onclick="смЭтап('second',this)">2-е письмо</button>
    </div>
    <textarea class="редактор" id="редактор-текст" placeholder="Загружаю..."></textarea>
    <button class="btn-сохранить" onclick="сохранитьШаблон()">💾 Сохранить шаблон</button>
  </div>
</div>

<!-- ══ СТРАНИЦА: НАСТРОЙКИ ══ -->
<div class="page" id="стр-настройки">
  <div class="форм-блок">
    <h3>🌐 Прокси</h3>
    <div class="форм-группа">
      <label>Адрес прокси</label>
      <input id="н-прокси" type="text" placeholder="http://1.2.3.4:8888">
    </div>
    <div class="форм-группа">
      <label>Логин (если есть)</label>
      <input id="н-прокси-юз" type="text" placeholder="username">
    </div>
    <div class="форм-группа">
      <label>Пароль (если есть)</label>
      <input id="н-прокси-пас" type="password" placeholder="••••••••">
    </div>
  </div>

  <div class="форм-блок">
    <h3>📢 Telegram — уведомления</h3>
    <div class="форм-группа">
      <label>Токен бота</label>
      <input id="н-тг-токен" type="password" placeholder="123456:ABCdef…">
    </div>
    <div class="форм-группа">
      <label>Ваш Chat ID</label>
      <input id="н-тг-чат" type="text" placeholder="123456789">
    </div>
  </div>

  <div class="форм-блок">
    <h3>🔗 Бот генерации ссылок</h3>
    <div class="форм-группа">
      <label>Токен бота</label>
      <input id="н-линк-токен" type="password" placeholder="123456:ABCdef…">
    </div>
    <div class="форм-группа">
      <label>API URL</label>
      <input id="н-линк-урл" type="text" placeholder="https://your-api.com/generate">
    </div>
  </div>

  <div class="форм-блок">
    <h3>⚡ Лимиты отправки</h3>
    <div class="форм-группа">
      <label>Писем в день с аккаунта</label>
      <input id="н-день" type="number" min="1" max="500">
    </div>
    <div class="форм-группа">
      <label>Всего писем с аккаунта</label>
      <input id="н-всего" type="number" min="1">
    </div>
    <div class="форм-группа">
      <label>Задержка мин. (секунды)</label>
      <input id="н-зад-мин" type="number" step="0.5" min="1">
    </div>
    <div class="форм-группа">
      <label>Задержка макс. (секунды)</label>
      <input id="н-зад-макс" type="number" step="0.5" min="1">
    </div>
    <div class="форм-группа">
      <label>Параллельных браузеров</label>
      <input id="н-парал" type="number" min="1" max="5">
    </div>
  </div>

  <button class="btn-сохранить" onclick="сохранитьНастройки()">💾 Сохранить все настройки</button>
</div>

<!-- ══ НИЖНЯЯ НАВИГАЦИЯ ══ -->
<nav class="nav">
  <button class="nav-btn active" onclick="переключить('главная',this)">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>
    Главная
  </button>
  <button class="nav-btn" onclick="переключить('аккаунты',this)">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>
    Аккаунты
  </button>
  <button class="nav-btn" onclick="переключить('логи',this)">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 14H7v-2h5v2zm5-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>
    Логи
  </button>
  <button class="nav-btn" onclick="переключить('шаблоны',this)">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V8l8 5 8-5v10zm-8-7L4 6h16l-8 5z"/></svg>
    Шаблоны
  </button>
  <button class="nav-btn" onclick="переключить('настройки',this)">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
    Настройки
  </button>
</nav>

<div id="уведомление"></div>

<script>
// Telegram WebApp init
const tg = window.Telegram?.WebApp;
if (tg) { tg.expand(); tg.setHeaderColor('#0f1117'); tg.setBackgroundColor('#0f1117'); }

let текЯз = 'en', текЭтап = 'first', данныеШаблонов = {};

// ── Навигация ──────────────────────────────────────────
function переключить(стр, кнопка) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('стр-' + стр).classList.add('active');
  кнопка.classList.add('active');
  if (стр === 'аккаунты') загрузитьАккаунты();
  if (стр === 'логи') загрузитьЛоги();
  if (стр === 'шаблоны') загрузитьШаблоны();
  if (стр === 'настройки') загрузитьНастройки();
}

// ── Главная ────────────────────────────────────────────
async function загрузитьСтат() {
  const d = await апи('/api/стат');
  document.getElementById('с-активных').textContent   = d.active_accounts;
  document.getElementById('с-отправлено').textContent = d.first_sent;
  document.getElementById('с-ответов').textContent    = d.replied;
  document.getElementById('с-вторых').textContent     = d.second_sent;
  document.getElementById('с-блок').textContent       = d.blocked_accounts;
  document.getElementById('с-исчерп').textContent     = d.exhausted_accounts;
}

async function загрузитьПолучателей() {
  const d = await апи('/api/получатели');
  const total = d.total || 1;
  const items = [
    ['ожидает', d.pending], ['отправлено', d.first_sent],
    ['ответили', d.replied], ['второе', d.second_sent], ['битые', d.bad]
  ];
  for (const [key, val] of items) {
    const pct = ((val||0)/total*100).toFixed(1);
    document.getElementById('п-'+key).style.width = pct+'%';
    document.getElementById('ч-'+key).textContent = val||0;
  }
  document.getElementById('ч-всего').textContent = d.total||0;
}

async function запустить(действие, кнопка) {
  const метки = { рассылка:'Запускаю...', ответы:'Проверяю...', обслуживание:'Обслуживаю...' };
  const оригинал = кнопка.textContent;
  кнопка.disabled = true;
  кнопка.textContent = метки[действие];
  const r = await апи('/api/действие/'+действие, 'POST');
  if (r.статус === 'уже_запущено') {
    уведомить('⏳ Уже запущено');
  } else {
    уведомить('✅ Задача запущена!');
  }
  кнопка.textContent = оригинал;
  кнопка.disabled = false;
}

// ── Аккаунты ──────────────────────────────────────────
async function загрузитьАккаунты() {
  const rows = await апи('/api/аккаунты');
  document.getElementById('кол-акк').textContent = rows.length + ' шт';
  const статусы = { active:'Активен', blocked:'Заблок.', exhausted:'Исчерпан' };
  const классы = { active:'б-active', blocked:'б-blocked', exhausted:'б-exhausted' };
  document.getElementById('список-акк').innerHTML = rows.map(r => `
    <div class="акк-карта">
      <div class="акк-email">${r.email}</div>
      <div class="акк-инфо">
        <span class="бейдж ${классы[r.status]||''}">${статусы[r.status]||r.status}</span>
        <span>Сегодня: ${r.daily_sent}</span>
        <span>Всего: ${r.total_sent}</span>
        <span>${r.first_login_completed ? '✅ Вошёл' : '⏳ Не вошёл'}</span>
      </div>
    </div>
  `).join('') || '<div style="color:var(--muted);text-align:center;padding:20px">Аккаунтов нет</div>';
}

// ── Логи ──────────────────────────────────────────────
async function загрузитьЛоги() {
  const rows = await апи('/api/логи?limit=50');
  const уровниЦвет = { INFO:'б-INFO', WARNING:'б-WARNING', ERROR:'б-ERROR' };
  document.getElementById('список-логов').innerHTML = rows.map(r => `
    <div class="лог-строка">
      <div class="лог-время">
        <span class="бейдж ${уровниЦвет[r.level]||''}">${r.level}</span>
        ${(r.ts||'').replace('T',' ').slice(0,19)}
        ${r.account ? '· '+r.account.split('@')[0] : ''}
      </div>
      <div class="лог-msg">${экрHtml(r.message||'')}</div>
    </div>
  `).join('') || '<div style="color:var(--muted);text-align:center;padding:20px">Логов нет</div>';
}

// ── Шаблоны ───────────────────────────────────────────
async function загрузитьШаблоны() {
  данныеШаблонов = await апи('/api/шаблоны');
  отобразитьШаблон();
}

function отобразитьШаблон() {
  const ключ = текЯз + '_' + текЭтап;
  document.getElementById('редактор-текст').value = данныеШаблонов[ключ] || '';
}

function смЯзык(яз, кнопка) {
  текЯз = яз;
  document.querySelectorAll('.яз-кнопка').forEach(b => b.classList.remove('active'));
  кнопка.classList.add('active');
  отобразитьШаблон();
}

function смЭтап(этап, кнопка) {
  текЭтап = этап;
  document.querySelectorAll('.этап-кнопка').forEach(b => b.classList.remove('active'));
  кнопка.classList.add('active');
  отобразитьШаблон();
}

async function сохранитьШаблон() {
  const ключ = текЯз + '_' + текЭтап;
  const текст = document.getElementById('редактор-текст').value;
  данныеШаблонов[ключ] = текст;
  await апи('/api/шаблоны/'+ключ, 'POST', {текст});
  уведомить('✅ Шаблон сохранён!');
}

// ── Настройки ─────────────────────────────────────────
async function загрузитьНастройки() {
  const d = await апи('/api/конфиг');
  const p = d.proxy||{}, t = d.telegram||{}, c = d.campaign||{};
  document.getElementById('н-прокси').value     = p.server||'';
  document.getElementById('н-прокси-юз').value  = p.username||'';
  document.getElementById('н-прокси-пас').value = p.password||'';
  document.getElementById('н-тг-токен').value   = t.bot_token||'';
  document.getElementById('н-тг-чат').value     = t.chat_id||'';
  document.getElementById('н-линк-токен').value = t.link_bot_token||'';
  document.getElementById('н-линк-урл').value   = t.link_bot_api_url||'';
  document.getElementById('н-день').value       = c.daily_limit||100;
  document.getElementById('н-всего').value      = c.total_limit||300;
  document.getElementById('н-зад-мин').value    = c.min_send_delay||2;
  document.getElementById('н-зад-макс').value   = c.max_send_delay||5;
  document.getElementById('н-парал').value      = c.max_concurrent_senders||2;
}

async function сохранитьНастройки() {
  await апи('/api/конфиг', 'POST', {
    proxy: {
      server:   document.getElementById('н-прокси').value,
      username: document.getElementById('н-прокси-юз').value,
      password: document.getElementById('н-прокси-пас').value,
    },
    telegram: {
      bot_token:        document.getElementById('н-тг-токен').value,
      chat_id:          document.getElementById('н-тг-чат').value,
      link_bot_token:   document.getElementById('н-линк-токен').value,
      link_bot_api_url: document.getElementById('н-линк-урл').value,
    },
    campaign: {
      daily_limit:            +document.getElementById('н-день').value,
      total_limit:            +document.getElementById('н-всего').value,
      min_send_delay:         +document.getElementById('н-зад-мин').value,
      max_send_delay:         +document.getElementById('н-зад-макс').value,
      max_concurrent_senders: +document.getElementById('н-парал').value,
    },
    headless: true, missing_2fa_strategy: 'skip',
  });
  уведомить('✅ Настройки сохранены!');
}

// ── Утилиты ───────────────────────────────────────────
async function апи(url, method='GET', body=null) {
  const opts = { method };
  if (body) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  return r.json();
}

function уведомить(текст) {
  const el = document.getElementById('уведомление');
  el.textContent = текст;
  el.classList.add('показать');
  setTimeout(() => el.classList.remove('показать'), 2500);
}

function экрHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Автообновление ─────────────────────────────────────
function обновить() { загрузитьСтат(); загрузитьПолучателей(); }
обновить();
setInterval(обновить, 30000);
</script>
</body>
</html>
"""
