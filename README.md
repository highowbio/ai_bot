# AI Bot · декодер конфигов (Telegram Mini App)

Telegram Mini App для дешифровки и просмотра конфигурационных файлов
**NETCFG** и **MXCFG**.

```
Пользователь в Telegram
        │
        ▼
   /start → кнопка «🚀 Открыть приложение»
        │
        ▼
┌───────────────────┐        ┌──────────────────────┐
│  Frontend (HTML)  │ ─────▶ │   Backend (FastAPI)  │
│ Mini App интерфейс│        │ • /api/netcfg/decrypt│
│ Telegram WebApp   │ ◀───── │ • /api/mxcfg/decrypt │
│ SDK + fetch       │        │ • /api/mxcfg/view    │
└───────────────────┘        │ • PTB polling (бот)  │
                             └──────────────────────┘
```

## Структура репозитория

```
backend/            FastAPI + Telegram-бот (один процесс)
  app/
    main.py         FastAPI-приложение и lifespan, запускающий бота
    bot.py          PTB-бот: /start с WebApp-кнопкой и админ-команды
    auth.py         Валидация initData (HMAC-SHA256 по токену бота)
    codec.py        Чистая логика декодирования NETCFG / MXCFG
    view.py         HTML-рендер разобранного MXCFG
    access.py       Белый список пользователей / админов
    config.py       Чтение настроек из env (runtime.env / .env)
  pyproject.toml    FastAPI + python-telegram-bot 21 + python-dotenv
frontend/           Статический Mini App
  index.html        UI + подключение telegram-web-app.js
  app.js            Логика загрузки файлов и вызовов API
  styles.css        Оформление с Telegram theme-параметрами
```

## Переменные окружения (бэкенд)

| Имя              | Назначение                                        |
| ---------------- | ------------------------------------------------- |
| `BOT_TOKEN`      | Токен бота от @BotFather **(обязателен)**         |
| `BOT_ADMIN_IDS`  | ID админов через запятую                          |
| `FRONTEND_URL`   | URL Mini App (для CORS)                           |
| `BOT_WEBAPP_URL` | URL Mini App, подставляется в `/start` кнопку     |
| `CORS_ORIGINS`   | Дополнительные origin-ы через запятую             |
| `DATA_DIR`       | Куда складывать `whitelist.json` (по умолч. `/data`) |
| `RUN_BOT`        | `0`, чтобы отключить polling (только API)          |
| `INIT_DATA_TTL`  | Сколько секунд живёт initData (по умолч. 86400)    |
| `MAX_FILE_SIZE`  | Лимит upload в байтах (по умолч. 20 МБ)            |

Локальная разработка: положите переменные в `backend/runtime.env` или
`backend/.env` — их подтянет `python-dotenv` при старте (оба в
`.gitignore`).

## Локальный запуск

```bash
cd backend
pip install -e .

# создайте backend/runtime.env с BOT_TOKEN, затем:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Frontend — просто откройте `frontend/index.html`, предварительно заменив
`__BACKEND_URL__` на адрес бэкенда.

## Настройка в @BotFather

1. `/mybots` → выберите бота → **Bot Settings** → **Menu Button** →
   **Configure Menu Button**.
2. Введите URL вашего фронта (например,
   `https://frontend-nmzphmhl.devinapps.com`).
3. Задайте название кнопки (например, `🚀 Открыть`).

Теперь в клиенте Telegram рядом с полем ввода появится кнопка, которая
открывает Mini App.

## Команды бота

Публичные:

* `/start` — открыть Mini App
* `/myid` — показать свой Telegram ID

Админы (добавляются через `BOT_ADMIN_IDS`):

* `/adduser <id>` — добавить пользователя
* `/removeuser <id>` — убрать пользователя
* `/users` — список пользователей и админов

## Безопасность

* Бэкенд проверяет `initData` Mini App по HMAC-SHA256 с секретом `WebAppData`
  и токеном бота; без валидной подписи все API-ручки отвечают `401`.
* Пользователь не в белом списке получает `403`.
* `BOT_TOKEN` никогда не коммитится — только в `runtime.env` на сервере
  либо как переменная окружения.
