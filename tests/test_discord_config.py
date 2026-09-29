"""Консистентность Discord-конфигурации: бот, setup-план и живые имена сервера.

discord_bot.py импортируется, только если discord.py поставлен в окружении —
в docker-прогонах тестов его нет, файл молча скипается.
"""
import os
import unittest

os.environ.setdefault("DISCORD_GUILD_ID", "873836896860655656")

import discord_setup

try:
    import discord_bot
except ImportError:                                   # discord.py нет в этом окружении
    discord_bot = None

VIEW = 1 << 10


class ReactionRoles(unittest.TestCase):
    """Маппинг реакций: имя роли обязано совпадать с серверным, эмодзи — с тем,
    что приходит из gateway (⚔ и ⚔️ — это один и тот же ключ)."""

    @unittest.skipIf(discord_bot is None, "discord.py не установлен")
    def test_variation_selector_is_normalized(self):
        self.assertEqual(discord_bot.emoji_key("⚔️"), discord_bot.emoji_key("⚔"))
        self.assertEqual(discord_bot.role_for_emoji("⚔️"), "Клановые Битвы")
        self.assertEqual(discord_bot.role_for_emoji("⚔"), "Клановые Битвы")
        self.assertEqual(discord_bot.role_for_emoji("🟢"), "✦·АКТИВНЫЙ")
        self.assertIsNone(discord_bot.role_for_emoji("❌"))

    @unittest.skipIf(discord_bot is None, "discord.py не установлен")
    def test_keys_have_no_variation_selector(self):
        for emoji in discord_bot.REACTION_ROLES:
            self.assertNotIn("\ufe0f", emoji, f"{emoji!r} не переживёт сравнение с payload")

    @unittest.skipIf(discord_bot is None, "discord.py не установлен")
    def test_every_mapped_role_is_produced_by_plan(self):
        planned = {spec["name"] for spec in discord_setup.ROLE_PLAN}
        planned |= set(discord_setup.ROLE_ALIAS)
        planned |= set(discord_setup.ROLE_ALIAS.values())
        for emoji, role in discord_bot.REACTION_ROLES.items():
            self.assertIn(role, planned, f"роль {role} под {emoji} не создаётся планом")

    def test_notification_roles_are_mentionable(self):
        for spec in discord_setup.ROLE_PLAN:
            if spec["name"] in ("Анонсы", "Клановые Битвы"):
                self.assertTrue(spec["mentionable"], f"{spec['name']} должна упоминаться")

    @unittest.skipIf(discord_bot is None, "discord.py не установлен")
    def test_post_title_is_the_one_bot_looks_for(self):
        """Пост самовыдачи ищется по заголовку — другой заголовок породил бы дубль."""
        self.assertEqual(discord_bot.ROLES_POST_TITLE, "✦・Самовыдача ролей")


class ChannelNames(unittest.TestCase):
    """Бот ищет каналы по имени: константы обязаны биться с алиасами плана,
    иначе /rules, /ticket и логи молча уйдут в пустоту после переименования."""

    @unittest.skipIf(discord_bot is None, "discord.py не установлен")
    def test_constants_match_setup_aliases(self):
        for actual, plan in (
            (discord_bot.ROLES_CHANNEL, "роли"),
            (discord_bot.LOGS_CHANNEL, "логи"),
            (discord_bot.TICKETS_CHANNEL, "тикеты"),
            (discord_bot.RULES_CHANNEL, "правила"),
        ):
            self.assertEqual(discord_setup.CHANNEL_ALIAS[plan], actual,
                             f"константа {actual} не совпала с планом «{plan}»")

    def test_alias_values_are_unique(self):
        values = list(discord_setup.CHANNEL_ALIAS.values())
        self.assertEqual(len(values), len(set(values)), "два плановых имени смотрят в один канал")


class FakeApi:
    def __init__(self, channels):
        self.channels = channels
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return 200, self.channels
        return 200, {}


class LockedModerationChannels(unittest.TestCase):
    """Регресс на дыру: @everyone не должен снова получить view в логах/регламенте,
    а модерация — потерять его: allow ролей обязан лежать и в канале, не только
    в категории (канал с собственными overwrites категорию не наследует)."""

    EVERYONE = "1"
    BOT = "2"
    ADMIN, MODERATOR, DEVELOPER = "11", "12", "13"

    def _run(self):
        channels = [
            {"id": "100", "name": discord_setup.CHANNEL_ALIAS["🛡 Модерация"],
             "type": discord_setup.CHANNEL_CAT, "permission_overwrites": [
                 {"id": self.BOT, "allow": "1", "deny": "0"},
                 {"id": self.EVERYONE, "allow": "0", "deny": str(VIEW)},
             ]},
            {"id": "200", "name": discord_setup.CHANNEL_ALIAS["логи"], "type": 0,
             "parent_id": "100", "permission_overwrites": [
                 {"id": self.EVERYONE, "allow": "0", "deny": str(VIEW)},
                 {"id": self.BOT, "allow": "1", "deny": "0"},
             ]},
            {"id": "201", "name": discord_setup.CHANNEL_ALIAS["модерация"], "type": 0,
             "parent_id": "100", "permission_overwrites": [
                 {"id": self.EVERYONE, "allow": "0", "deny": str(VIEW)},
                 {"id": self.BOT, "allow": "1", "deny": "0"},
             ]},
        ]
        api = FakeApi(channels)
        setup = discord_setup.Setup(api, "1", dry_run=False)
        setup.ensure_channels(None, self.EVERYONE, {
            "Админ": self.ADMIN, "Модератор": self.MODERATOR, "Разработчик": self.DEVELOPER,
        }, self.BOT)
        return api

    def _channel_patches(self, api):
        return [(path, body) for method, path, body in api.calls
                if method == "PATCH" and path.startswith("/channels/")]

    def test_plan_denies_everyone_view_on_locked_channels(self):
        locked = next(block for block in discord_setup.CHANNEL_PLAN if block.get("locked"))
        self.assertTrue(locked["channels"], "locked-категория осталась без каналов")
        for spec in locked["channels"]:
            self.assertIn("view_channel", spec.get("deny", []))
            self.assertNotIn("view_channel", spec.get("allow", []),
                             f"{spec['name']} снова открыт @everyone")

    def test_channels_get_role_allows_not_only_category(self):
        api = self._run()
        patches = dict(self._channel_patches(api))
        # категория + оба канала (в фейке у каналов не хранится allow ролей)
        self.assertEqual(len(patches), 3, f"ожидалось 3 патча, есть: {list(patches)}")

        _, own = next((p, b) for p, b in patches.items() if p.endswith("200"))
        overwrites = {o["id"]: o for o in own["permission_overwrites"]}
        view = str(VIEW)
        self.assertEqual(overwrites[self.EVERYONE], {"id": self.EVERYONE, "allow": "0", "deny": view})
        for role_id in (self.ADMIN, self.MODERATOR, self.DEVELOPER):
            self.assertIn(role_id, overwrites, "роль потеряла доступ к логам модерации")
            self.assertEqual(overwrites[role_id]["allow"], view)
            self.assertEqual(overwrites[role_id]["deny"], "0")
        self.assertIn(self.BOT, overwrites, "бот перестал видеть канал логов")


if __name__ == "__main__":
    unittest.main()
