# anon-chat-bot — карта проекта

Читай этот файл вместо сканирования `.py`-файлов при заходе в проект — экономит токены.
Если факт отсюда разошёлся с кодом (переименование, удалённый модуль) — доверяй коду и поправь этот файл.

## Что это

Telegram-бот анонимного чата (аналог "Random Coffee" / omegle-style): случайный подбор
собеседника, анкета, премиум-подписка за Telegram Stars, реклама, реферальная программа,
жалобы/баны, админ-панель.

## Стек

- Python, **aiogram 3.31** (Router-based handlers, FSM, middleware)
- **SQLAlchemy 2.x async** + **asyncpg** (PostgreSQL) — `models/base.py`: `engine`, `async_session`, `Base`
- **Redis** — очередь поиска, online-статусы, FSM storage, антифлуд (`utils/storage.py`, `services/chat_manager.py`)
- Docker Compose: `db` (postgres:16), `redis`, `bot` — см. `docker-compose.yml`
- Свои SQL-миграции в `migrations/*.sql`, раннер `migrations/runner.py` (без Alembic)

## Точка входа и запуск

`bot.py`:
- `on_startup()` — прогоняет миграции (`migrations.runner.migrate`), создаёт таблицы,
  сидирует продукты (`services.payments.seed_products`), сверяет активные чаты
  (`ChatService.reconcile`), шлёт владельцу уведомление о старте.
- `main()` — собирает `Bot`/`Dispatcher`, регистрирует роутеры из `handlers.load_routers()`,
  вешает middleware (антифлуд, дедупликация апдейтов, альбомы, access-контроль, JSON-логирование),
  запускает фоновый `workers/maintenance.py::run` (нотификации, обслуживание) и polling.
- `on_shutdown()` — закрывает Redis и engine.

Конфиг — `config.py`: токены, лимиты, цены в Stars, читается из `.env` (`python-dotenv`).

## Слои приложения

`handlers/` (aiogram Router, тонкий слой, парсинг апдейта → вызов сервиса → ответ)
→ `services/` (бизнес-логика) → `repositories/` (доступ к БД для User/Chat) → `models/` (ORM).

### handlers/ (роутеры регистрируются в `handlers/__init__.py::load_routers`)
- `start.py` — `/start`, онбординг, правила
- `menu.py` — главное меню, отмена ввода
- `search.py` — поиск собеседника, очередь, таймауты (`search_loop`, `restore_searches`/`shutdown_searches` — persist поиска между рестартами)
- `chat.py` — активный чат: пересылка сообщений, `/next`, `/stop`, разрешение показать контакт
- `profile.py` — анкета (просмотр/редактирование полей, фото, удаление)
- `settings.py` — настройки пользователя (фильтр по полу и т.п.)
- `rating.py` — оценка собеседника после чата
- `report.py` — жалобы на собеседника
- `reveal.py` — платное раскрытие контакта/статуса (Stars)
- `premium.py` — премиум-подписка: витрина, `pre_checkout`/`successful_payment` (Telegram Stars)
- `ads.py` — отслеживание вступления в рекламный канал (`ChatMemberUpdated`)
- `admin.py` — админ-панель: статистика, воронка, экспорт, жалобы, сезонные события
- `stats.py`, `help.py` — вспомогательные

### services/ (по одному файлу на домен, часто класс `*Service` + модульные функции)
- `chat_manager.py` — низкоуровневые Redis-примитивы: online, очередь поиска, антифлуд, партнёр по чату
- `chats.py::ChatService` — создание/завершение чатов, `reconcile()` при старте
- `matching.py::MatchingService` — алгоритм подбора пары
- `relay.py::RelayService` — пересылка сообщений между собеседниками
- `profile.py::ProfileService`, `onboarding.py::OnboardingService`
- `premium.py`, `payments.py::PaymentService` — премиум-статус, Stars-платежи, продукты, `seed_products`
- `reveal.py::RevealService` — платное раскрытие контакта
- `reports.py::ReportService` — жалобы и баны
- `notifications.py::NotificationService` + `enqueue()` — очередь уведомлений/рассылок (обрабатывается воркером)
- `growth.py` — стрики, ачивки, промо-офферы (`StreakService`, `GrowthService`, `SeasonalService`)
- `ads.py` — рекламные кампании, показы, вступления по инвайт-ссылкам
- `admin.py` — `StatsService`, `AdminService`, аудит админ-действий (`audit()`)
- `settings.py::SettingsService`, `backup_alert.py` — уведомление о неудачном бэкапе
- `result.py::ServiceResult` — общий тип результата операций сервисов

### repositories/
- `users.py`, `chats.py` — CRUD/запросы к `User`/`Chat` для сервисов и хендлеров

### models/ (SQLAlchemy ORM, `Base` из `models/base.py`)
- `user.py::User`
- `chat.py`: `Chat`, `ChatMessage`, `ActiveParticipant`, `Rating`, `Block`, `RevealRequest`
- `payment.py`: `Payment`, `PremiumSubscription`, `Product`, `Entitlement`
- `operations.py`: `BotSetting`, `AdminAudit`, `AnalyticsEvent`, `Referral`, `Streak`, `Achievement`,
  `Offer`, `Notification`, `Broadcast`, `AdCampaign`, `AdImpression`, `AdJoin`
- `report.py::Report`, `ban.py::Ban`

### workers/
- `maintenance.py::run(bot)` — фоновый цикл: рассылка уведомлений из очереди, обслуживание (запускается из `bot.py`)

### web/ — сайт и Mini App (отдельный контейнер `web`, FastAPI + uvicorn)
Тот же аккаунт, что в боте: вход через Telegram Login Widget или `initData` Mini App,
подписанная cookie. **Вся логика поиска/чата/профиля/платежей — те же сервисы, что у бота**,
веб только новый способ до них дозвониться. Пара может быть бот↔бот, сайт↔сайт или смешанная.
- `app.py` — сборка приложения, отдача `static/`; `auth.py` — проверка подписи Telegram и cookie
- `ws.py` — один сокет `/ws/session` на пользователя: статус поиска, доставка чата, сигналинг звонков
- `routes_chat.py` / `routes_account.py` / `routes_call.py` — сообщения, разделы аккаунта, звонки
- `static/` — обычные HTML/CSS/JS без сборки (`app.html` — панель и разделы, `call.html` — комната звонка)
- Живые события между двумя сторонами идут через Redis Pub/Sub `chat:{session_id}`
  (публикует `services/relay.py::publish_delivery`)

### Звонки 1-на-1
- `services/calls.py` — жизненный цикл (offer/accept/decline/hangup), состояние в Redis,
  запись факта в таблицу `calls`; там же выдача временных TURN-кредов
- `handlers/call.py` — `/call` и инлайн-кнопки в боте (роутер регистрируется **до** `chat`,
  иначе catch-all хендлер чата перехватит команду)
- Медиа идёт браузер↔браузер через **свой coturn на этом же хосте** (`/etc/turnserver.conf`,
  systemd-сервис `coturn`, сертификаты синхронизируются хуком `/usr/local/bin/coturn-cert-sync`).
  Релей принудительный (`iceTransportPolicy: "relay"`) — иначе собеседники видят IP друг друга.
- Лимитов на длительность и число звонков сознательно нет; потолок — аплинк сервера.
  Ориентир: аудио ≈ 200 кбит/с на звонок через релей, видео ограничено 500 кбит/с.

### utils/
- `middleware.py` — `AntiFloodMiddleware`, `AccessMiddleware`
- `deduplication.py::UpdateDeduplication`, `albums.py::AlbumMiddleware`
- `storage.py::AvailableRedisStorage` — FSM storage с фолбэком
- `telegram.py::RetryTelegramRequests` — ретраи с учётом `retry_after`
- `logging.py` — JSON-логирование, `UpdateLoggingMiddleware`
- `db.py`, `time.py::utcnow`, `healthcheck.py`

### keyboards/
- `inline.py`, `reply.py` — клавиатуры; `texts.py` — тексты кнопок/сообщений

### migrations/
- Пронумерованные `.sql`-файлы (000…012), применяются `runner.py::migrate()` из `on_startup()`.
  Alembic не используется. Новую миграцию — новым файлом с следующим номером.

### tests/
- pytest, по одному файлу на область: онбординг, платежи, поиск, рассылки, приватность,
  callback-роутинг, миграции, стресс и др. Реальный Telegram не дёргается.

## Как гонять тесты быстро

Полный прогон через `docker-compose.test.yml` занимает **~7 минут** — не потому что тестов
много, а потому что стек намеренно зажат по CPU/RAM (см. комментарий в начале файла: этот же
хост уже роняли нагрузочным прогоном). Не пытайся ускорить это поднятием лимитов — гоняй так:

1. **Во время правки — только релевантный файл(ы) теста, локально, без Docker.**
   Почти все тесты используют SQLite-фолбэк (`DatabaseCase` в `test_chat_duration.py`) и
   мокают Redis — реальная БД им не нужна. Это секунды-десятки секунд на файл:
   ```
   python -m pytest tests/test_search_recovery.py -q
   ```
   Чтобы найти нужный файл — грепни импорт изменённого модуля по tests/:
   ```
   grep -rl "handlers.search\|handlers import search" tests/
   ```
   Если правка небольшая и по коду очевидно, какой файл её покрывает (совпадение по имени
   области — поиск/чат/платежи/админка) — не грепай, просто запускай его.

2. **Три файла требуют реальный Postgres и без него сами себя пропускают
   (`unittest.skipUnless(RUN_POSTGRES_TESTS==1, ...)`)** — их локальный `pytest` тихо
   скипнет, это нормально, а не "тесты не запустились":
   - `tests/test_postgres.py` — advisory-локи, конкурентность мэтчинга/жалоб.
   - `tests/test_migrations.py` — реальный прогон `.sql`-миграций.
   - `tests/test_stress.py` — дополнительно требует `RUN_STRESS_TESTS=1`, в обычный прогон
     теста не входит вообще.
   Если правка их не касается (не трогаешь `repositories/chats.py`, `migrations/*.sql`,
   advisory-локи) — их можно не гонять руками, полный прогон всё равно тихо пропустит.

3. **Полный прогон (docker-compose.test.yml) — только перед `git push deploy`**, и только
   если правка задевает миграции, advisory-локи (`repositories/chats.py`, `services/matching.py`,
   `services/admin.py` резолвы жалоб/банов) или другую Postgres-специфику. Для правок, целиком
   покрытых файлами из пункта 1, локального прогона плюс здравого смысла достаточно — не
   обязательно каждый раз поднимать полный стек ради одной правки в `handlers/`.
   Docker Desktop на этой машине не поднят — запускай стек через SSH на сервере в
   одноразовом каталоге и с уникальным `-p` (проектом compose), не трогая `/opt/anon-chat-bot`:
   ```
   ssh root@77.90.6.247 "cd /root/<временный-каталог-с-копией-репо> && \
     docker compose -f docker-compose.test.yml -p <уникальное-имя> \
     up --abort-on-container-exit --exit-code-from tests"
   ```
   После — обязательно `down -v` и удалить временный каталог/архив, чтобы не мусорить на сервере.

## Деплой

**Правка кода сама по себе ничего не меняет в проде.** Бот в контейнере работает с тем кодом,
который был на момент последней сборки образа — правки в рабочей директории туда не попадают,
пока их не закоммитить и не запушить. После любого изменения в `.py`/миграциях/`Dockerfile` и
т.п., которое должно попасть в прод, обязательна вся цепочка:

1. `git add` затронутых файлов (только их, не `-A`/`.`)
2. `git commit`
3. `git push deploy <branch>` — это единственное действие, которое пересобирает образ
   (`docker compose build bot`) и перезапускает контейнер (`docker compose up -d bot`) через
   `post-receive` хук на сервере.

Без пуша контейнер продолжит работать со старым кодом даже после `Edit`/`Write` — это не
опечатка и не баг деплоя, так и задумано (сервер — не рабочая копия, а деплой-таргет).

**Автокоммит и автопуш разрешены заранее — не спрашивать перед каждым разом.** Пользователь
явно попросил это как постоянное правило для этого проекта. После правки кода в рамках
поставленной задачи:
1. прогнать релевантные тесты (см. «Как гонять тесты быстро» выше) и убедиться, что зелено;
2. закоммитить только реально изменённые файлы (не `-A`/`.` — проверить `git status`);
3. запушить на `deploy` — без отдельного запроса разрешения на сам факт коммита/пуша.

Это не отменяет остальные правила безопасности: перед пушем всё равно `git status`, чтение
диффа перед `git add`, никаких `--force`/`--no-verify`/`reset --hard`, и обычная осторожность
с миграциями/схемой БД. Если правка выходит за рамки того, что просил пользователь в этом
диалоге (задели файлы, не относящиеся к задаче), или тесты не зелёные — по-прежнему сначала
сказать, а не пушить молча.

Локальный репозиторий — источник истины. Пуш идёт на `deploy` remote (bare-репо на сервере),
`deploy/post-receive` хук делает `checkout -f` в `/opt/anon-chat-bot` и пересобирает/перезапускает
контейнер `bot` через `docker compose`. **Правки на сервере мимо git теряются при следующем пуше**
(хук сохраняет патч в `.deploy-rescue/`, но не восстанавливает автоматически).

```
git push deploy <branch>
```

Прод-стек — `docker-compose.yml`: `db` (postgres:16-alpine), `redis` (7-alpine, maxmemory 128mb/LRU),
`bot` (собирается из `Dockerfile`, читает `.env`). `POSTGRES_PASSWORD` — только через `.env`, не коммитится.

## Документы в репозитории (справочные, не código)
- `docs/product-spec.md`, `docs/implementation.md` — спецификация/описание реализации
- `AUDIT_MAP.md`, `AUDIT_PASS_REPORT.md`, `DEFECTS.md`, `TECHNICAL_AUDIT_20260907.md` — истории аудитов; читай при работе над конкретным дефектом, не при каждом заходе
- `BACKUPS.md` — бэкап БД (`scripts/backup_database.sh`, `scripts/install_backup_service.sh`)
- `design/` — HTML-мокапы экранов (не используются в рантайме бота)
