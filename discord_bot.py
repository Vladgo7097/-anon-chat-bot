# language: Python, file: discord_bot.py, target: Discord (discord.py 2.7.1), runtime: Python 3.14
"""Шлюз-бот Nebula: слэш-команды, самовыдача ролей, логи модерации, тикеты, антиспам.

Запуск:
    DISCORD_BOT_TOKEN=<token> DISCORD_GUILD_ID=873836896860655656 python3 discord_bot.py
В intent-ах Portal должны быть включены: Server Members, Message Content.
Без них команда /help молча подскажет об этом, а не упадёт.
"""
from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0"))

ROLES_CHANNEL = "❋роли"
LOGS_CHANNEL = "☾логи"
TICKETS_CHANNEL = "✧тикеты"
RULES_CHANNEL = "❋правила"

MOD_ROLE_NAMES = {"✦·МОДЕРАТОР", "✦·АДМИН", "✦·РАЗРАБОТЧИК"}
REACTION_ROLES = {"🟢": "Активный"}          # эмодзи -> имя роли
SPAM_WINDOW = timedelta(seconds=5)
SPAM_LIMIT = 6                                # сообщений в окно
TIMEOUT_ON_REPEAT = timedelta(minutes=5)
STRIKE_TTL = timedelta(minutes=10)            # повторный флуд после паузы — снова предупреждение

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nebula")

# DISCORD_PRIVILEGED_INTENTS=0 — деградированный запуск, если Portal не выдал
# Server Members / Message Content (без них шлюз отвечает 4014 и не пускает).
PRIVILEGED = os.environ.get("DISCORD_PRIVILEGED_INTENTS", "1") == "1"
intents = discord.Intents.default()
intents.members = PRIVILEGED
intents.message_content = PRIVILEGED
intents.reactions = True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

reaction_message_id: int | None = None
spam_clocks: dict[int, deque] = defaultdict(lambda: deque(maxlen=SPAM_LIMIT + 4))
spam_struck: dict[int, datetime] = {}         # id -> момент предупреждения
ticket_openers: dict[int, int] = {}           # thread id -> кто открыл тикет
announced_ready = False


def now() -> datetime:
    return datetime.now(timezone.utc)


async def channel_named(guild: discord.Guild, name: str) -> discord.abc.Messageable | None:
    return discord.utils.get(guild.text_channels, name=name)


async def send_log(guild: discord.Guild, title: str, description: str, color: discord.Color) -> None:
    """Пишет событие в #☾логи; тихо пропускает, если канала ещё нет."""
    channel = await channel_named(guild, LOGS_CHANNEL)
    if channel is None:
        return
    embed = discord.Embed(title=title, description=description, color=color, timestamp=now())
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:      # не роняем обработчик из-за лога
        log.warning("лог не отправлен: %s", exc)


def is_mod(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    return any(role.name in MOD_ROLE_NAMES for role in member.roles)


async def ensure_reaction_roles(guild: discord.Guild) -> int | None:
    """Находит или создаёт пост самовыдачи в #❋роли и ставит эмодзи."""
    channel = await channel_named(guild, ROLES_CHANNEL)
    if channel is None:
        log.warning("канал %s не найден", ROLES_CHANNEL)
        return None

    target: discord.Message | None = None
    async for message in channel.history(limit=50):
        if message.embeds and message.embeds[0].title == "Самовыдача ролей":
            target = message
            break

    if target is None:
        lines = "\n".join(f"{emoji} — {role}" for emoji, role in REACTION_ROLES.items())
        embed = discord.Embed(
            title="Самовыдача ролей",
            description=(
                "Поставь реакцию под этим сообщением, чтобы получить роль.\n"
                "Снимешь реакцию — роль снимется.\n\n"
                f"{lines}\n\n"
                "Остальные роли выдаёт модератор командой `/role`."
            ),
            color=discord.Color.from_rgb(0xA2, 0x9B, 0xFE),
        )
        target = await channel.send(embed=embed)

    for emoji in REACTION_ROLES:
        if not any(r.emoji == emoji and r.me for r in target.reactions):
            await target.add_reaction(emoji)
    return target.id


# ───────────────────────────── команды ─────────────────────────────

@bot.tree.command(name="help", description="Список команд бота")
async def cmd_help(interaction: discord.Interaction) -> None:
    lines = [
        "`/ticket <описание>` — открыть приватный тикет",
        "`/close` — закрыть текущий тикет (только в ветке тикета)",
        "`/rules` — показать правила сервера",
        "",
        "**Модерация:**",
        "`/role <пользователь> <роль>` — выдать роль",
        "`/mute <пользователь> <минуты> [причина]` — таймаут",
        "`/ban <пользователь> [причина]` — бан",
        "`/clear <1-100>` — удалить сообщения",
    ]
    if not bot.intents.message_content:
        lines += ["", "⚠ Message Content Intent выключен в Portal — фильтр флуда работает по таймингу."]
    if not bot.intents.members:
        lines += ["", "⚠ Server Members Intent выключен — события участников не приходят."]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="rules", description="Показать правила сервера")
async def cmd_rules(interaction: discord.Interaction) -> None:
    channel = await channel_named(interaction.guild, RULES_CHANNEL)
    if channel is None:
        await interaction.response.send_message("Канал правил не найден.", ephemeral=True)
        return
    rules = [m async for m in channel.history(limit=5)]
    embed = next((m.embeds[0] for m in rules if m.embeds), None)
    if embed is None:
        await interaction.response.send_message(f"Правила: {channel.jump_url}", ephemeral=False)
        return
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ticket", description="Открыть приватный тикет")
@app_commands.describe(topic="Кратко, в чём вопрос")
async def cmd_ticket(interaction: discord.Interaction, topic: str) -> None:
    # defer сразу: создание ветки + инвайт + пост + лог укладываются в 3 секунды не всегда
    await interaction.response.defer()
    host = await channel_named(interaction.guild, TICKETS_CHANNEL)
    if not isinstance(host, discord.TextChannel):
        await interaction.followup.send("Канал тикетов не найден.", ephemeral=True)
        return
    name = f"тикет-{interaction.user.name}-{topic[:24]}".replace(" ", "-")[:100]
    try:
        thread = await host.create_thread(
            name=name,
            type=discord.ChannelType.private_thread,
            reason=f"тикет от {interaction.user}",
        )
        await thread.add_user(interaction.user)
    except discord.HTTPException as exc:
        log.warning("тикет не создан: %s", exc)
        await interaction.followup.send("Не удалось открыть тикет — попробуй позже.", ephemeral=True)
        return
    ticket_openers[thread.id] = interaction.user.id
    embed = discord.Embed(
        title="Тикет открыт",
        description=(
            f"**Автор:** {interaction.user.mention}\n"
            f"**Вопрос:** {topic[:500]}\n\n"
            "Модераторы подключатся сами. Закрыть: `/close`."
        ),
        color=discord.Color.from_rgb(0x2E, 0xD5, 0x73),
    )
    await thread.send(embed=embed)
    await send_log(
        interaction.guild,
        "Тикет открыт",
        f"{interaction.user} → {thread.jump_url}\n{topic[:200]}",
        discord.Color.from_rgb(0x2E, 0xD5, 0x73),
    )
    await interaction.followup.send(f"Тикет открыт: {thread.jump_url}")


@bot.tree.command(name="close", description="Закрыть текущий тикет")
async def cmd_close(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.Thread):
        await interaction.response.send_message("Это не тикет.", ephemeral=True)
        return
    # владеет веткой бот (создатель), поэтому автора узнаём по словарю,
    # а после рестарта — по имени ветки «тикет-<username>-…»
    is_opener = (
        ticket_openers.get(channel.id) == interaction.user.id
        or channel.name.startswith(f"тикет-{interaction.user.name}-")
    )
    if not is_mod(interaction.user) and not is_opener:
        await interaction.response.send_message("Закрыть может автор или модератор.", ephemeral=True)
        return
    await send_log(
        interaction.guild,
        "Тикет закрыт",
        f"{interaction.user} закрыл {channel.name}",
        discord.Color.from_rgb(0xFA, 0xA6, 0x1A),
    )
    await channel.edit(archived=True, locked=True)
    await interaction.response.send_message("Тикет закрыт.", ephemeral=True)


async def mod_guard(interaction: discord.Interaction) -> bool:
    if is_mod(interaction.user):
        return True
    await interaction.response.send_message("Команда только для модераторов.", ephemeral=True)
    return False


@bot.tree.command(name="role", description="Выдать роль участнику")
@app_commands.describe(user="Кому", role="Какую роль")
async def cmd_role(interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
    if not await mod_guard(interaction):
        return
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message("Эту роль бот не выдаст — она выше его.", ephemeral=True)
        return
    if role in user.roles:
        await user.remove_roles(role, reason=f"снял {interaction.user}")
        await send_log(
            interaction.guild,
            "Роль снята",
            f"{interaction.user} → {user.mention} : **{role.name}**",
            discord.Color.from_rgb(0x74, 0x7D, 0x8C),
        )
        await interaction.response.send_message(f"Роль **{role.name}** снята с {user.mention}.")
        return
    await user.add_roles(role, reason=f"выдал {interaction.user}")
    await send_log(
        interaction.guild,
        "Роль выдана",
        f"{interaction.user} → {user.mention} : **{role.name}**",
        discord.Color.from_rgb(0x58, 0x65, 0xF2),
    )
    await interaction.response.send_message(f"{user.mention} получает **{role.name}**.")


@bot.tree.command(name="mute", description="Таймаут участника")
@app_commands.describe(user="Кому", minutes="Минуты", reason="Причина")
async def cmd_mute(interaction: discord.Interaction, user: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "") -> None:
    if not await mod_guard(interaction):
        return
    if user.top_role >= interaction.guild.me.top_role:
        await interaction.response.send_message("Он выше бота по ролям.", ephemeral=True)
        return
    await user.timeout(timedelta(minutes=minutes), reason=reason or f"мьют от {interaction.user}")
    await send_log(
        interaction.guild,
        "Таймаут",
        f"**{user}** на {minutes} мин. — {reason or 'без причины'}\nМодератор: {interaction.user}",
        discord.Color.from_rgb(0xFA, 0xA6, 0x1A),
    )
    await interaction.response.send_message(f"{user.mention} на {minutes} мин.")


@bot.tree.command(name="ban", description="Забанить участника")
@app_commands.describe(user="Кого", reason="Причина")
async def cmd_ban(interaction: discord.Interaction, user: discord.User, reason: str = "") -> None:
    if not await mod_guard(interaction):
        return
    if isinstance(user, discord.Member) and user.top_role >= interaction.guild.me.top_role:
        await interaction.response.send_message("Он выше бота по ролям.", ephemeral=True)
        return
    await interaction.guild.ban(user, reason=reason or f"бан от {interaction.user}", delete_message_seconds=0)
    await interaction.response.send_message(f"{user} забанен.")
    # лог уйдёт из on_member_ban — там будет и причина из аудита


@bot.tree.command(name="clear", description="Удалить сообщения")
@app_commands.describe(amount="Сколько (1-100)")
async def cmd_clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
    if not await mod_guard(interaction):
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Только в обычных каналах.", ephemeral=True)
        return
    await interaction.response.send_message(f"Удаляю {amount}…", ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await send_log(
        interaction.guild,
        "Очистка",
        f"{interaction.user} удалил {len(deleted)} сообщ. в {interaction.channel.mention}",
        discord.Color.from_rgb(0x74, 0x7D, 0x8C),
    )


# ───────────────────────────── реакционные роли ─────────────────────────────

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if payload.message_id != reaction_message_id or payload.guild_id is None:
        return
    role_name = REACTION_ROLES.get(str(payload.emoji))
    if role_name is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
    if member is None or member.bot:
        return
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None or role in member.roles:
        return
    await member.add_roles(role, reason="самовыдача по реакции")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    if payload.message_id != reaction_message_id or payload.guild_id is None:
        return
    role_name = REACTION_ROLES.get(str(payload.emoji))
    if role_name is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    if member is None:
        return
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None or role not in member.roles:
        return
    await member.remove_roles(role, reason="реакция снята")


# ───────────────────────────── логи модерации ─────────────────────────────

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    reason = "без причины"
    try:
        entry = (await guild.audit_logs(limit=5, action=discord.AuditLogAction.ban)).find(
            lambda e: e.target.id == user.id
        )
        if entry:
            reason = entry.reason or "без причины"
            mod = entry.user
            await send_log(guild, "БАН", f"**{user}**\nМодератор: {mod}\nПричина: {reason}", discord.Color.red())
            return
    except discord.Forbidden:
        pass
    await send_log(guild, "БАН", f"**{user}**\nПричина: {reason}", discord.Color.red())


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    await send_log(guild, "АНБАН", f"**{user}** снят с бана", discord.Color.green())


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    try:
        await member.guild.fetch_ban(member)
        return                                   # это бан — лог уже ушёл из on_member_ban
    except (discord.NotFound, discord.HTTPException):
        pass
    await send_log(
        member.guild,
        "УХОД",
        f"**{member}** ({member.id}) покинул сервер\nУчастников: {member.guild.member_count}",
        discord.Color.from_rgb(0x74, 0x7D, 0x8C),
    )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    was = before.timed_out_until
    now_t = after.timed_out_until
    if now_t and (was is None or now_t != was):
        until = now_t.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
        await send_log(
            after.guild,
            "ТАЙМАУТ",
            f"**{after}** до {until}",
            discord.Color.from_rgb(0xFA, 0xA6, 0x1A),
        )


@bot.event
async def on_message_delete(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return
    text = (message.content or "").strip()[:400] or "*вложение/embed*"
    await send_log(
        message.guild,
        "СООБЩЕНИЕ УДАЛЕНО",
        f"Автор: {message.author} · {message.channel.mention}\n{text}",
        discord.Color.from_rgb(0xED, 0x42, 0x45),
    )


# ───────────────────────────── антиспам ─────────────────────────────

@bot.event
async def on_message(message: discord.Message) -> None:
    if message.guild is None or message.author.bot:
        return
    # модерация сама пишет пачками — фильтр флуда на неё не распространяем
    if isinstance(message.author, discord.Member) and is_mod(message.author):
        await bot.process_commands(message)
        return
    clock = spam_clocks[message.author.id]
    clock.append(message.created_at)
    if len(clock) > SPAM_LIMIT and (clock[-1] - clock[0]) <= SPAM_WINDOW:
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        struck_at = spam_struck.get(message.author.id)
        # предупреждение протухает: флуд с паузой > STRIKE_TTL снова получает шанс
        if struck_at is not None and (message.created_at - struck_at) <= STRIKE_TTL:
            try:
                await message.author.timeout(TIMEOUT_ON_REPEAT, reason="флуд")
            except discord.HTTPException:
                pass
            await send_log(
                message.guild,
                "ФЛУД",
                f"**{message.author}** получил таймаут {int(TIMEOUT_ON_REPEAT.total_seconds() // 60)} мин.",
                discord.Color.red(),
            )
            spam_struck.pop(message.author.id, None)
        else:
            spam_struck[message.author.id] = message.created_at
            try:
                await message.author.send("Не флуди — сообщения удаляются.")
            except discord.HTTPException:
                pass
        return
    await bot.process_commands(message)


# ───────────────────────────── старт ─────────────────────────────

@bot.event
async def on_ready() -> None:
    global reaction_message_id, announced_ready
    if announced_ready:
        return
    announced_ready = True

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        log.error("бот не видит сервер %s", GUILD_ID)
        return

    try:
        bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        log.info("слэш-команды зарегистрированы: %d", len(synced))
    except discord.HTTPException as exc:
        log.error("sync не удался: %s", exc)

    reaction_message_id = await ensure_reaction_roles(guild)
    log.info("пост самовыдачи: %s", reaction_message_id)

    missing = [name for name, ok in (
        ("Server Members", bot.intents.members),
        ("Message Content", bot.intents.message_content),
    ) if not ok]
    await send_log(
        guild,
        "БОТ ЗАПУЩЕН",
        f"Версия discord.py {discord.__version__}\n"
        f"Команды: /help /ticket /close /rules /role /mute /ban /clear\n"
        + (f"⚠ Нет intent: {', '.join(missing)}" if missing else "Все intent на месте."),
        discord.Color.green(),
    )
    log.info("готов: %s · сервер: %s · intent-проблемы: %s", bot.user, guild.name, missing or "нет")


def main() -> int:
    if not TOKEN:
        print("нет DISCORD_BOT_TOKEN", file=sys.stderr)
        return 2
    if not GUILD_ID:
        print("нет DISCORD_GUILD_ID", file=sys.stderr)
        return 2
    bot.run(TOKEN, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
