# ai_bot

Telegram-бот для **дешифровки** и **просмотра** файлов формата NETCFG и MXCFG.

## Возможности

- 🔓 Дешифровка NETCFG (`0x01 0x01` + XOR по ключу) и MXCFG (base64 + XOR с подбором ключа).
- 👁 Просмотр содержимого MXCFG в красивом HTML-виде (автор, описание, шаги, сетевые настройки).
- 👑 Белый список пользователей с командами для админов (`/adduser`, `/removeuser`, `/users`).
- 💾 Сохранение состояния юзеров между рестартами (`PicklePersistence`).

## Требования

- Python 3.11+
- Аккаунт бота в Telegram (токен от [@BotFather](https://t.me/BotFather))

## Быстрый старт

```bash
git clone https://github.com/highowbio/ai_bot.git
cd ai_bot

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# открой .env и заполни BOT_TOKEN и ADMIN_IDS

python main.py
```

## Конфигурация

Все настройки читаются из переменных окружения (см. `.env.example`):

| Переменная         | Обязательная | Описание                                                      |
|--------------------|--------------|---------------------------------------------------------------|
| `BOT_TOKEN`        | да           | Токен бота от @BotFather.                                     |
| `ADMIN_IDS`        | рекомендуется| ID админов через запятую, например `111,222`.                 |
| `WHITELIST_FILE`   | нет          | Путь к JSON с whitelist-ом. По умолчанию `./whitelist.json`.  |
| `PERSISTENCE_FILE` | нет          | Путь к файлу состояния PTB. По умолчанию `./bot_persistence.pickle`. |
| `MAX_FILE_SIZE_MB` | нет          | Максимальный размер файла в МБ. По умолчанию `20`.            |
| `LOG_LEVEL`        | нет          | `DEBUG` / `INFO` / `WARNING` / `ERROR`. По умолчанию `INFO`.  |

## Команды

Для всех пользователей с доступом:

- `/start` — главное меню
- `/help` — справка
- `/myid` — показать свой Telegram ID
- `/cancel` — сбросить текущий режим

Для админов:

- `/adduser <id>` — добавить пользователя в whitelist
- `/removeuser <id>` — удалить пользователя из whitelist
- `/users` — список админов и пользователей

## Запуск в Docker

```bash
cp .env.example .env
# заполни .env

docker compose up -d --build
docker compose logs -f bot
```

Данные (whitelist, состояние PTB) хранятся в именованном volume `bot_data`.

## Разработка

```bash
pip install -r requirements-dev.txt

ruff check .
ruff format --check .
mypy
pytest
```

CI (см. `.github/workflows/ci.yml`) прогоняет линт, типы и тесты на каждом push/PR.

## Структура проекта

```
.
├── main.py            # Точка входа, хендлеры Telegram
├── crypto.py          # Чистые функции дешифровки (NETCFG, MXCFG)
├── view.py            # Рендер MXCFG в HTML
├── tests/             # Pytest-тесты для crypto.py и view.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml     # Конфиг ruff / mypy / pytest
└── .env.example
```

## Безопасность

- `BOT_TOKEN` **никогда** не должен попадать в git. `.env` добавлен в `.gitignore`.
- Если токен был случайно закоммичен — отзови его через `/revoke` у @BotFather.
- Файл `whitelist.json` тоже не коммитится: он содержит персональные ID пользователей.
