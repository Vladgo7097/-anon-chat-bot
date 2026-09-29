#!/usr/bin/env python3
# language: Python 3.11+, file: discord_setup.py, target: Discord REST API v10, runtime: Python stdlib only
# Настраивает community/проект-сервер: роли с битовыми правами, категории, каналы,
# permission-overwrites, порядок веток, приветственный пост в #правила.
# Идемпотентен: повторный запуск правит существующие объекты, а не плодит дубли.
# Запуск: DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=... python3 discord_setup.py [--dry-run]

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"

# ── биты прав Discord ────────────────────────────────────────────────────────
P = {
    "create_invite": 1 << 0, "kick": 1 << 1, "ban": 1 << 2, "admin": 1 << 3,
    "manage_channels": 1 << 4, "manage_guild": 1 << 5, "add_reactions": 1 << 6,
    "audit_log": 1 << 7, "priority_speaker": 1 << 8, "stream": 1 << 9,
    "view_channel": 1 << 10, "send_messages": 1 << 11, "send_tts": 1 << 12,
    "manage_messages": 1 << 13, "embed_links": 1 << 14, "attach_files": 1 << 15,
    "read_history": 1 << 16, "mention_everyone": 1 << 17, "external_emojis": 1 << 18,
    "guild_insights": 1 << 19, "connect": 1 << 20, "speak": 1 << 21,
    "mute_members": 1 << 22, "deafen_members": 1 << 23, "move_members": 1 << 24,
    "vad": 1 << 25, "change_nickname": 1 << 26, "manage_nicknames": 1 << 27,
    "manage_roles": 1 << 28, "manage_webhooks": 1 << 29, "manage_expressions": 1 << 30,
    "app_commands": 1 << 31, "request_to_speak": 1 << 32, "manage_events": 1 << 33,
    "manage_threads": 1 << 34, "create_public_threads": 1 << 35,
    "create_private_threads": 1 << 36, "external_stickers": 1 << 37,
    "send_in_threads": 1 << 38, "activities": 1 << 39, "moderate_members": 1 << 40,
}

CHANNEL_TEXT, CHANNEL_VOICE, CHANNEL_CAT, CHANNEL_ANNOUNCE = 0, 2, 4, 5

MEMBER_BASE = [
    "view_channel", "send_messages", "embed_links", "attach_files", "read_history",
    "add_reactions", "external_emojis", "external_stickers", "connect", "speak",
    "stream", "vad", "change_nickname", "app_commands", "send_in_threads",
    "create_public_threads",
]

MOD_EXTRAS = [
    "kick", "ban", "manage_messages", "moderate_members", "manage_threads",
    "audit_log", "move_members", "mute_members", "deafen_members",
    "manage_nicknames",
]

ADMIN_EXTRAS = MOD_EXTRAS + [
    "manage_channels", "manage_roles", "manage_guild", "manage_webhooks",
    "manage_expressions", "create_invite", "mention_everyone",
]

# порядок — сверху вниз; роль бота поднимается на самый верх отдельно.
# цвета — финальная палитра сервера
ROLE_PLAN = [
    {"name": "Участник",     "color": 0x747D8C, "hoist": False, "perms": MEMBER_BASE},
    {"name": "Активный",     "color": 0x2ED573, "hoist": True,  "perms": MEMBER_BASE},
    {"name": "Разработчик",  "color": 0xA29BFE, "hoist": True,  "perms": MEMBER_BASE + ["manage_webhooks"]},
    {"name": "Модератор",    "color": 0xFFA502, "hoist": True,  "perms": MEMBER_BASE + MOD_EXTRAS},
    {"name": "Админ",        "color": 0xFF6B81, "hoist": True,  "perms": MEMBER_BASE + ADMIN_EXTRAS},
]

# Роли и каналы на сервере переименованы вручную в «красивые» имена.
# План ниже хранит исходные имена, алиасы связывают их с текущим состоянием —
# без них повторный запуск создал бы дубли.
ROLE_ALIAS = {
    "Админ": "✦·АДМИН",
    "Модератор": "✦·МОДЕРАТОР",
    "Разработчик": "✦·РАЗРАБОТЧИК",
    "Активный": "✦·АКТИВНЫЙ",
    "Участник": "✦·УЧАСТНИК",
}

CHANNEL_ALIAS = {
    "правила": "❋правила",
    "анонсы": "❋анонсы",
    "роли": "❋роли",
    "изменения": "❋изменения",
    "общий-чат": "♡общий-чат",
    "флейм": "♡флейм",
    "медия": "♡медия",
    "бот-команды": "♡бот-команды",
    "идеи": "✧идеи",
    "фидбек": "✧фидбек",
    "гайды": "✧гайды",
    "тикеты": "✧тикеты",
    "логи": "☾логи",
    "модерация": "☾модерация",
    "📋 Информация": "✦・ИНФОРМАЦИЯ",
    "🏠 Общение": "✦・ОБЩЕНИЕ",
    "🎯 Проект": "✦・ПРОЕКТ",
    "🎙 Голосовые": "✦・ГОЛОСОВЫЕ",
    "🛡 Модерация": "✦・МОДЕРАЦИЯ",
    "🔊 Общая": "🔊・Общая",
    "🔊 Игры": "🔊・Игры",
    "🔊 Тихая": "🔊・Тихая",
    "🔇 АФК": "💤・АФК",
}


def with_aliases(mapping: dict, aliases: dict) -> dict:
    """Добавляет в индекс ключи из плана, если объект живёт под алиасом."""
    for plan_name, actual in aliases.items():
        if actual in mapping and plan_name not in mapping:
            mapping[plan_name] = mapping[actual]
    return mapping


def role_index(roles: list) -> dict[str, str]:
    """Имя роли из плана → её id, с учётом ручных переименований."""
    return with_aliases({r["name"]: r["id"] for r in roles}, ROLE_ALIAS)

# overwrites для @everyone по каналам: allow/deny — имена битов
NO_TEXT = ["send_messages", "send_in_threads", "create_public_threads", "create_private_threads"]

CHANNEL_PLAN = [
    {"category": "📋 Информация", "position": 0, "channels": [
        {"name": "правила", "type": CHANNEL_TEXT, "position": 0,
         "allow": ["view_channel", "read_history", "add_reactions"], "deny": NO_TEXT},
        {"name": "анонсы", "type": CHANNEL_ANNOUNCE, "position": 1,
         "allow": ["view_channel", "read_history", "add_reactions"], "deny": NO_TEXT},
        {"name": "роли", "type": CHANNEL_TEXT, "position": 2,
         "allow": ["view_channel", "read_history", "add_reactions"], "deny": NO_TEXT},
        {"name": "изменения", "type": CHANNEL_TEXT, "position": 3,
         "allow": ["view_channel", "read_history"], "deny": NO_TEXT},
    ]},
    {"category": "🏠 Общение", "position": 1, "channels": [
        {"name": "общий-чат", "type": CHANNEL_TEXT, "position": 0, "allow": ["view_channel"], "deny": []},
        {"name": "флейм", "type": CHANNEL_TEXT, "position": 1, "allow": ["view_channel"], "deny": []},
        {"name": "медия", "type": CHANNEL_TEXT, "position": 2, "allow": ["view_channel"], "deny": []},
        {"name": "бот-команды", "type": CHANNEL_TEXT, "position": 3, "allow": ["view_channel"], "deny": []},
    ]},
    {"category": "🎯 Проект", "position": 2, "channels": [
        {"name": "идеи", "type": CHANNEL_TEXT, "position": 0, "allow": ["view_channel"], "deny": []},
        {"name": "фидбек", "type": CHANNEL_TEXT, "position": 1, "allow": ["view_channel"], "deny": []},
        {"name": "гайды", "type": CHANNEL_TEXT, "position": 2,
         "allow": ["view_channel", "read_history"], "deny": NO_TEXT},
        {"name": "тикеты", "type": CHANNEL_TEXT, "position": 3, "allow": ["view_channel"], "deny": []},
    ]},
    {"category": "🎙 Голосовые", "position": 3, "channels": [
        {"name": "🔊 Общая", "type": CHANNEL_VOICE, "position": 0, "allow": ["view_channel", "connect", "speak"], "deny": []},
        {"name": "🔊 Игры", "type": CHANNEL_VOICE, "position": 1, "allow": ["view_channel", "connect", "speak"], "deny": []},
        {"name": "🔊 Тихая", "type": CHANNEL_VOICE, "position": 2, "allow": ["view_channel", "connect", "speak"], "deny": []},
        {"name": "🔇 АФК", "type": CHANNEL_VOICE, "position": 3,
         "allow": ["view_channel", "connect"], "deny": ["speak"]},
    ]},
    {"category": "🛡 Модерация", "position": 4, "locked": True, "channels": [
        {"name": "логи", "type": CHANNEL_TEXT, "position": 0, "allow": ["view_channel"], "deny": []},
        {"name": "модерация", "type": CHANNEL_TEXT, "position": 1, "allow": ["view_channel"], "deny": []},
    ]},
]

WELCOME = {
    "title": "Добро пожаловать в Nebula",
    "color": 0x5865F2,
    "description": (
        "Анонимный чат, тёплое комьюнити и проект, который растёт на глазах. "
        "Начни с правил, бери роль в #роли и заходи в #общий-чат."
    ),
    "fields": [
        {"name": "📋 Информация", "value": "правила · анонсы · роли · изменения", "inline": False},
        {"name": "🏠 Общение", "value": "общий-чат · флейм · медия · бот-команды", "inline": False},
        {"name": "🎯 Проект", "value": "идеи · фидбек · гайды · тикеты", "inline": False},
        {"name": "🎙 Голосовые", "value": "Общая · Игры · Тихая · АФК", "inline": False},
    ],
    "footer": {"text": "Правила — в #правила, они же закреплены"},
}

# посты в каналах: pin=True — закрепить сразу после отправки.
# идемпотентность — по заголовку эмбеда в последних 50 сообщениях канала
POST_PLAN = [
    {"channel": "правила", "pin": True, "embed": {
        "title": "Правила Nebula",
        "color": 0xED4245,
        "description": "Короткие правила, чтобы не копать простыню. Незнание не снимает ответственности.",
        "fields": [
            {"name": "1 · Отношение", "value": "Оскорбления, травля, дискриминация и давление на личность запрещены. Спорить можно, унижать — нет.", "inline": False},
            {"name": "2 · Спам и реклама", "value": "Флуд, капс, инвайты, реклама в каналы и в личку участникам — мут или бан. Свои ссылки — только с разрешения модерации.", "inline": False},
            {"name": "3 · Личные данные", "value": "Чужие и свои адреса, телефоны, аккаунты и документы не публикуются. Это бан без обсуждения.", "inline": False},
            {"name": "4 · Контент", "value": "NSFW и шок-контент — только в каналах, где это явно разрешено, и никогда в обсуждениях проекта.", "inline": False},
            {"name": "5 · Обход наказаний", "value": "Новый аккаунт после бана или таймаута удваивает срок. Так же карается мультиаккаунт для обхода лимитов.", "inline": False},
            {"name": "6 · Решения модерации", "value": "Спор с наказанием — через #тикеты, спокойно и по делу. Публичный разбор в канале не работает.", "inline": False},
            {"name": "Структура сервера", "value": "📋 Информация — правила, анонсы, роли, изменения\n🏠 Общение — общий-чат, флейм, медия, бот-команды\n🎯 Проект — идеи, фидбек, гайды, тикеты\n🎙 Голосовые — Общая, Игры, Тихая, АФК\n🛡 Модерация — скрыта от участников", "inline": False},
            {"name": "Куда идти", "value": "Новичку: прочитать это → взять роль в #роли → писать в #общий-чат. Вопрос модерации — #тикеты.", "inline": False},
        ],
        "footer": {"text": "Обновлено при настройке сервера"},
    }},
    {"channel": "общий-чат", "pin": True, "embed": WELCOME},
    {"channel": "роли", "pin": True, "embed": {
        "title": "Роли на сервере",
        "color": 0x3BA55C,
        "description": "Роли определяют, что видно и что можно делать. Выдаёт модератор или бот по запросу.",
        "fields": [
            {"name": "Участник", "value": "Базовая роль, получается при вступлении.", "inline": True},
            {"name": "Активный", "value": "Для тех, кто живёт в чате: выделение в списке участников.", "inline": True},
            {"name": "Разработчик", "value": "Ядро проекта: идеи, код, решения по функционалу.", "inline": True},
            {"name": "Модератор", "value": "Порядок в каналах: таймауты, кики, чистка.", "inline": True},
            {"name": "Админ", "value": "Полный контроль сервера и настройки.", "inline": True},
        ],
        "footer": {"text": "Хочешь роль — напиши в #тикеты"},
    }},
    {"channel": "анонсы", "pin": True, "embed": {
        "title": "Как устроены анонсы",
        "color": 0xFAA61A,
        "description": "Канал только для новостей проекта: релизы, обновления, ивенты. Комментарии и обсуждение — в #общий-чат.",
    }},
    {"channel": "изменения", "pin": True, "embed": {
        "title": "Журнал изменений",
        "color": 0x5865F2,
        "description": "Каждое обновление проекта попадает сюда одной записью: дата, что изменилось, куда смотреть. Подписывайтесь на уведомления канала, чтобы не листать историю.",
    }},
    {"channel": "идеи", "pin": True, "embed": {
        "title": "Как предлагать идеи",
        "color": 0x5865F2,
        "description": "Одна идея — одно сообщение. Формат: что делаем → кому поможет → как выглядит. Идеи с голосами и примерами продвигаются быстрее.",
    }},
    {"channel": "фидбек", "pin": True, "embed": {
        "title": "Фидбек и баги",
        "color": 0xED4245,
        "description": "Баг: шаги → что ожидалось → что получилось → скриншот. Фидбек: что не устроило и как должно быть. Жалобы на участников — не сюда, а в #тикеты.",
    }},
    {"channel": "гайды", "pin": True, "embed": {
        "title": "Гайды",
        "color": 0x3BA55C,
        "description": "Канал только для чтения: инструкции, разборы, шпаргалки. Свои гайды присылай модерации — добавим с указанием автора.",
    }},
    {"channel": "тикеты", "pin": True, "embed": {
        "title": "Как открыть тикет",
        "color": 0xFAA61A,
        "description": "Нужна помощь, жалоба или апелляция: напиши сообщение прямо здесь, модерация ответит в этом же канале. Не публикуй тикет в общих каналах.",
        "fields": [
            {"name": "Что указать", "value": "Суть вопроса · участники · дата · скриншоты, если есть", "inline": False},
            {"name": "Срок ответа", "value": "Обычно в течение дня, апелляции по банам — в приоритете.", "inline": False},
        ],
    }},
    {"channel": "бот-команды", "pin": True, "embed": {
        "title": "Команды бота",
        "color": 0x99AAB5,
        "description": "Канал для служебных команд и ответов бота, чтобы не засорять #общий-чат. Сюда же — слэш-команды, если бот подключён.",
    }},
    {"channel": "модерация", "pin": True, "embed": {
        "title": "Регламент модерации",
        "color": 0xED4245,
        "description": "Порядок работы с нарушениями, чтобы решения были одинаковыми у всех.",
        "fields": [
            {"name": "Шкала", "value": "1-е нарушение — предупреждение в лс · 2-е — таймаут 1ч · 3-е — таймаут 1д · тяжёлые (спам-реклама, личные данные, NSFW) — сразу бан.", "inline": False},
            {"name": "Что фиксировать", "value": "Ссылку на сообщение, причину и срок — в этом канале. Баны без записи не считаются.", "inline": False},
            {"name": "Апелляции", "value": "Идут через #тикеты, на них отвечает не тот, кто наказал.", "inline": False},
        ],
    }},
    {"channel": "логи", "pin": True, "embed": {
        "title": "Логи модерации",
        "color": 0x99AAB5,
        "description": "Сюда попадают записи о наказаниях и служебные события. Канал виден только модераторам и админам.",
    }},
]



def bits(names: list[str]) -> str:
    return str(sum(P[n] for n in names))


def norm_overwrites(items: list | None) -> set[tuple[str, str, str]]:
    """Сравнение по (id, allow, deny) — API добавляет к объекту поле type, оно мешает."""
    return {
        (str(o.get("id")), str(o.get("allow", "0")), str(o.get("deny", "0")))
        for o in items or []
    }


class Api:
    """REST-клиент: ретраит 429 по retry_after, остальные коды отдаёт наверх как значение."""

    def __init__(self, token: str) -> None:
        self.token = token

    def call(self, method: str, path: str, body: object | None = None):
        url = API + path
        net_fail = 0
        for _ in range(10):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Content-Type": "application/json",
                    "User-Agent": "DiscordServerSetup (self-hosted, 1.0)",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return resp.status, json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"raw": raw.decode(errors="replace")[:300]}
                if exc.code == 429:
                    time.sleep(float(payload.get("retry_after", 1.0)) + 0.2)
                    continue
                return exc.code, payload
            except (urllib.error.URLError, OSError) as exc:
                # DNS/сброс соединения — временная сетевая ошибка, ретраим с бэкоффом
                reason = str(getattr(exc, "reason", exc))
                net_fail += 1
                if net_fail >= 8:
                    raise RuntimeError(f"{method} {path}: сеть недоступна: {reason}") from exc
                time.sleep(min(2 ** net_fail, 15))
                continue
        raise RuntimeError(f"{method} {path}: rate-limit retries exhausted")


class Setup:
    def __init__(self, api: Api, guild_id: str, dry_run: bool) -> None:
        self.api = api
        self.guild_id = guild_id
        self.dry_run = dry_run
        self.notes: list[str] = []
        self.actions = 0

    def plan(self, text: str) -> None:
        self.actions += 1
        print(("  ~ " if self.dry_run else "  + ") + text)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print("  ! " + text)

    # ── объекты ─────────────────────────────────────────────────────────────
    def load(self):
        status, guild = self.api.call("GET", f"/guilds/{self.guild_id}")
        if status != 200:
            raise SystemExit(f"нет доступа к серверу {status}: {guild}")
        _, roles = self.api.call("GET", f"/guilds/{self.guild_id}/roles")
        _, channels = self.api.call("GET", f"/guilds/{self.guild_id}/channels")
        _, me = self.api.call("GET", "/users/@me")
        m_status, member = self.api.call("GET", f"/guilds/{self.guild_id}/members/{me['id']}")
        if m_status != 200 or not isinstance(member, dict):
            raise SystemExit(f"бот не состоит в сервере ({m_status}) — сначала открой инвайт-ссылку")
        return guild, roles, channels, member

    def ensure_roles(self, roles: list, everyone_id: str, bot_role_id: str):
        print("\n[роли]")
        existing = with_aliases({r["name"]: r for r in roles}, ROLE_ALIAS)
        created: dict[str, str | None] = {}
        for spec in ROLE_PLAN:
            name = spec["name"]
            desired = {
                "permissions": bits(spec["perms"]),
                "color": spec["color"] or 0,
                "hoist": spec["hoist"],
                "mentionable": name in ("Модератор", "Админ"),
            }
            if name in existing:
                role = existing[name]
                changed = (
                    int(role["permissions"]) != int(desired["permissions"])
                    or int(role.get("color") or 0) != int(desired["color"])
                    or bool(role.get("hoist")) != desired["hoist"]
                )
                if changed:
                    self.plan(f"правлю роль {name}")
                    if not self.dry_run:
                        # имя не отправляем — сохраним ручное переименование
                        status, payload = self.api.call(
                            "PATCH", f"/guilds/{self.guild_id}/roles/{role['id']}", desired
                        )
                        if status != 200:
                            self.note(f"роль {name}: {status} {payload}")
                created[name] = role["id"]
            else:
                self.plan(f"создаю роль {name}")
                created[name] = None
                if not self.dry_run:
                    status, role = self.api.call(
                        "POST", f"/guilds/{self.guild_id}/roles",
                        {"name": name, **desired},
                    )
                    if status != 200:
                        self.note(f"роль {name}: {status} {role}")
                        continue
                    created[name] = role["id"]
                    existing[name] = role

        # позиции считаются от низа: чем больше число — тем выше роль в иерархии.
        # свой блок ставим поверх всех чужих ролей, иначе бот не сможет ими управлять
        mine = {rid for rid in created.values() if rid}
        base = max(
            (r.get("position") or 0 for r in roles
             if r["id"] not in mine and r["id"] != bot_role_id and r["name"] != "@everyone"),
            default=0,
        )
        desired: list[tuple[str, int]] = []
        if bot_role_id != everyone_id:
            desired.append((bot_role_id, base + len(ROLE_PLAN) + 1))
        for i, spec in enumerate(ROLE_PLAN, start=1):
            rid = created.get(spec["name"])
            if rid:
                desired.append((rid, base + i))

        current = {r["id"]: r.get("position") for r in roles}
        for spec in ROLE_PLAN:
            rid = created.get(spec["name"])
            if rid and spec["name"] in existing:
                current[rid] = existing[spec["name"]].get("position")

        stale = [r for r, pos in desired if current.get(r) != pos]
        if not stale:
            print("  - порядок ролей уже верный, пропускаю")
            return created, everyone_id

        payload = [{"id": r, "position": pos} for r, pos in desired]
        self.plan("выстраиваю порядок ролей (снизу: Участник → … → Админ → роль бота)")
        if not self.dry_run:
            status, out = self.api.call("PATCH", f"/guilds/{self.guild_id}/roles", payload)
            if status != 200:
                status, out = self.api.call(
                    "PATCH", f"/guilds/{self.guild_id}/roles", {"positions": payload}
                )
            if status != 200:
                self.note(
                    "порядок ролей не применился — перетащи роль бота на самый верх "
                    "вручную (Настройки сервера → Роли), затем повтори запуск"
                )
                print(f"      {status}: {out}")
        return created, everyone_id

    def ensure_channels(self, guild, everyone_id: str, role_ids: dict, bot_role_id: str):
        print("\n[каналы]")
        _, channels = self.api.call("GET", f"/guilds/{self.guild_id}/channels")
        by_name = with_aliases({c["name"]: c for c in channels}, CHANNEL_ALIAS)
        # без собственного override роль бота гасится deny'ем @everyone
        # (deny перекрывает базовые права) — поэтому боту даём allow везде
        bot_allow = [
            "view_channel", "send_messages", "embed_links", "attach_files",
            "read_history", "add_reactions", "manage_messages", "connect",
        ]
        locked_allow = [role_ids.get("Админ"), role_ids.get("Модератор")]
        locked_allow = [r for r in locked_allow if r]
        cat_ids: dict[str, str] = {}

        for block in CHANNEL_PLAN:
            cat_name = block["category"]
            overwrites = [{
                "id": str(bot_role_id),
                "allow": bits(bot_allow),
                "deny": "0",
            }]
            if block.get("locked"):
                overwrites.append(
                    {"id": str(everyone_id), "allow": "0", "deny": bits(["view_channel"])}
                )
                overwrites.extend(
                    {"id": r, "allow": bits(["view_channel"]), "deny": "0"}
                    for r in locked_allow
                )
            if cat_name in by_name:
                cat = by_name[cat_name]
                cat_ids[cat_name] = cat["id"]
                if overwrites and norm_overwrites(cat.get("permission_overwrites")) != norm_overwrites(overwrites):
                    self.plan(f"правлю права категории {cat_name}")
                    if not self.dry_run:
                        self.api.call(
                            "PATCH", f"/channels/{cat['id']}",
                            {"permission_overwrites": overwrites},
                        )
            else:
                self.plan(f"создаю категорию {cat_name}")
                if self.dry_run:
                    cat_ids[cat_name] = ""
                else:
                    status, cat = self.api.call("POST", f"/guilds/{self.guild_id}/channels", {
                        "name": cat_name, "type": CHANNEL_CAT,
                        "position": block["position"],
                        "permission_overwrites": overwrites,
                    })
                    if status != 201:
                        self.note(f"категория {cat_name}: {status} {cat}")
                        continue
                    cat_ids[cat_name] = cat["id"]
                    by_name[cat_name] = cat

            for spec in block["channels"]:
                name = spec["name"]
                ow_list = [
                    {
                        "id": str(everyone_id),
                        "allow": bits(spec.get("allow", [])),
                        "deny": bits(spec.get("deny", [])),
                    },
                    {
                        "id": str(bot_role_id),
                        "allow": bits(bot_allow),
                        "deny": "0",
                    },
                ]
                target = {
                    "name": name, "type": spec["type"],
                    "parent_id": cat_ids.get(cat_name) or None,
                    "position": spec["position"],
                    "permission_overwrites": ow_list,
                }
                if name in by_name:
                    ch = by_name[name]
                    needs = (
                        ch.get("parent_id") != target["parent_id"]
                        or norm_overwrites(ch.get("permission_overwrites")) != norm_overwrites(ow_list)
                        or ch.get("type") != target["type"]
                    )
                    if needs:
                        self.plan(f"правлю канал #{name}")
                        if not self.dry_run:
                            self.api.call(
                                "PATCH", f"/channels/{ch['id']}",
                                {
                                    "parent_id": target["parent_id"],
                                    "position": target["position"],
                                    "permission_overwrites": ow_list,
                                },
                            )
                else:
                    self.plan(f"создаю #{name} ({'голос' if spec['type'] == CHANNEL_VOICE else 'текст'})")
                    if not self.dry_run:
                        status, ch = self.api.call(
                            "POST", f"/guilds/{self.guild_id}/channels", target
                        )
                        if status != 201:
                            self.note(f"канал #{name}: {status} {ch}")
                            continue
                        by_name[name] = ch
        return by_name

    def pin_message(self, channel_id: str, message_id: str):
        status, payload = self.api.call("POST", f"/channels/{channel_id}/pins/{message_id}")
        if status in (200, 204):
            return status, payload
        return self.api.call("PUT", f"/channels/{channel_id}/pins/{message_id}")

    def post_content(self, channels: dict):
        print("\n[посты]")
        for item in POST_PLAN:
            name = item["channel"]
            ch = channels.get(name)
            if not isinstance(ch, dict) or "id" not in ch:
                self.note(f"канал #{name} не найден — пост пропущен")
                continue
            title = item["embed"].get("title", "")
            _, msgs = self.api.call("GET", f"/channels/{ch['id']}/messages?limit=50")
            msgs = msgs if isinstance(msgs, list) else []
            found = next(
                (m for m in msgs
                 if isinstance(m, dict)
                 and any(e.get("title") == title for e in (m.get("embeds") or []))),
                None,
            )
            if found:
                if item.get("pin") and not found.get("pinned"):
                    self.plan(f"закрепляю «{title}» в #{name}")
                    if not self.dry_run:
                        st, payload = self.pin_message(ch["id"], found["id"])
                        if st not in (200, 204):
                            self.note(f"закреп «{title}»: {st} {payload}")
                else:
                    state = " и закреплён" if found.get("pinned") else ""
                    print(f"  - #{name}: «{title}» уже стоит{state}, пропускаю")
                continue

            self.plan(
                f"пост «{title}» → #{name}" + (" + закреп" if item.get("pin") else "")
            )
            if self.dry_run:
                continue
            st, payload = self.api.call(
                "POST", f"/channels/{ch['id']}/messages",
                {"embeds": [item["embed"]], "allowed_mentions": {"parse": []}},
            )
            if st not in (200, 201) or "id" not in payload:
                self.note(f"пост «{title}» в #{name}: {st} {payload}")
                continue
            if item.get("pin"):
                st, payload = self.pin_message(ch["id"], payload["id"])
                if st not in (200, 204):
                    self.note(f"закреп «{title}»: {st} {payload}")


def main() -> int:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    dry_run = "--dry-run" in sys.argv

    if not token:
        print("нужен DISCORD_BOT_TOKEN (вкладка Bot → Token в Developer Portal)")
        return 1
    if not guild_id:
        print("нужен DISCORD_GUILD_ID (правый клик по серверу → Copy Server ID)")
        return 1
    if not guild_id.isdigit():
        print(f"DISCORD_GUILD_ID должен быть числом, получено: {guild_id!r}")
        return 1

    api = Api(token)
    status, me = api.call("GET", "/users/@me")
    if status != 200:
        print(f"токен отвергнут: {status} {me}")
        return 1
    print(f"авторизован: {me.get('username')}  сервер: {guild_id}")

    setup = Setup(api, guild_id, dry_run)
    guild, roles, _, member = setup.load()
    everyone_id = next(r["id"] for r in roles if r["name"] == "@everyone")
    # своя роль бота определяется по bot_id на объекте роли; индекс [0] в member.roles
    # порядок не гарантирует — он только фолбэк, если бот выдан без managed-роли
    own_role = next((r["id"] for r in roles if r.get("bot_id") == me["id"]), None)
    bot_role_id = own_role or (member.get("roles") or [everyone_id])[0]

    setup.ensure_roles(roles, everyone_id, bot_role_id)
    _, roles, _, _ = setup.load()
    role_ids = role_index(roles)

    channels = setup.ensure_channels(guild, everyone_id, role_ids, bot_role_id)
    setup.post_content(channels)

    mode = "DRY-RUN, ничего не изменено" if dry_run else "готово"
    print(f"\n{mode} · действий: {setup.actions}")
    for n in setup.notes:
        print("  !! " + n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
