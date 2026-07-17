"""
Учёт активности пользователей: часы в войсе + количество сообщений.

Дизайн (важно при большом сервере, 15k+ участников):
- Никаких запросов в БД на каждое сообщение или голосовое событие.
- Состояние войса живёт в памяти (dict), обновляется точечно по on_voice_state_update.
- Раз в TICK_INTERVAL секунд бот "тикает" только по занятым голосовым каналам
  (не по всем участникам сервера) и начисляет часы в память тем, кто удовлетворяет
  условию честного рейтинга.
- Раз в FLUSH_INTERVAL секунд накопленное в памяти одним батчем (executemany)
  улетает в БД через одно соединение.
- Если флаш не удался - накопленное не теряется, а возвращается обратно в буфер.

Условие начисления часа (честный рейтинг, без накрутки):
- В канале должно быть >= 2 живых (не бот) участников с одновременно:
  включённым микрофоном (не self_mute), включённым звуком (не self_deaf),
  и не заглушённых сервером (не mute/deaf со стороны модерации).
- Только эти "полностью включённые" участники получают начисление за тик.
  Человек, который сидит с выключенным звуком/микрофоном, вообще не получает
  часы, даже если рядом кто-то есть - именно поэтому нельзя "накрутить"
  часы просто сидя молча с выключенным всем.
"""

import discord
from discord.ext import commands, tasks

TICK_INTERVAL = 60      # секунд между тиками учёта войса (1 минута)
FLUSH_INTERVAL = 600    # секунд между флашами в БД (10 минут)

# --- глобальное состояние модуля -------------------------------------------------

_cursor = None            # PgWrapper из main.py (используется только если понадобится где-то ещё)
_get_connection = None    # async фабрика "сырых" asyncpg-соединений (для батч-флаша)

# channel_id -> {user_id: (discord.Member, discord.VoiceState)}
_voice_members: dict[int, dict[int, tuple]] = {}

# накопители, которые ещё не улетели в БД
_pending_hours: dict[int, float] = {}
_pending_messages: dict[int, int] = {}


def set_cursor(cursor):
    global _cursor
    _cursor = cursor


def set_connection_factory(factory):
    """factory - async функция без аргументов, возвращающая asyncpg.Connection
    (в main.py это уже существующая get_db_connection)."""
    global _get_connection
    _get_connection = factory


def _is_eligible(vs: discord.VoiceState) -> bool:
    """Человек реально готов к общению: сам не выключал микрофон/звук,
    и сервер его не заглушил."""
    if vs is None:
        return False
    return not (vs.self_mute or vs.self_deaf or vs.mute or vs.deaf)


def _snapshot_guild_voice_state(bot: commands.Bot):
    """Заполняем состояние войса по факту на момент запуска бота
    (учитывает людей, которые уже сидели в войсе до рестарта)."""
    _voice_members.clear()
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            for member in channel.members:
                if member.bot:
                    continue
                _voice_members.setdefault(channel.id, {})[member.id] = (member, member.voice)


# --- фоновые задачи ----------------------------------------------------------------

@tasks.loop(seconds=TICK_INTERVAL)
async def voice_tick():
    increment = TICK_INTERVAL / 3600  # часы за один тик
    for channel_id, members in list(_voice_members.items()):
        eligible_ids = [uid for uid, (member, vs) in members.items() if _is_eligible(vs)]
        if len(eligible_ids) >= 2:
            for uid in eligible_ids:
                _pending_hours[uid] = _pending_hours.get(uid, 0.0) + increment


@tasks.loop(seconds=FLUSH_INTERVAL)
async def flush_activity():
    if not _pending_hours and not _pending_messages:
        return
    if _get_connection is None:
        return

    hours_snapshot = dict(_pending_hours)
    messages_snapshot = dict(_pending_messages)
    _pending_hours.clear()
    _pending_messages.clear()

    user_ids = set(hours_snapshot) | set(messages_snapshot)
    if not user_ids:
        return

    rows = [
        (hours_snapshot.get(uid, 0.0), messages_snapshot.get(uid, 0), uid)
        for uid in user_ids
    ]

    conn = await _get_connection()
    try:
        await conn.executemany(
            '''
            INSERT INTO user_profiles (user_id, voice_hours, messages_count)
            VALUES ($3, $1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET voice_hours = user_profiles.voice_hours + EXCLUDED.voice_hours,
                messages_count = user_profiles.messages_count + EXCLUDED.messages_count
            ''',
            rows
        )
        print(f"✅ Activity flush: {len(rows)} пользователей обновлено")
    except Exception as e:
        # не теряем данные - возвращаем в буфер для следующей попытки
        for uid, h in hours_snapshot.items():
            _pending_hours[uid] = _pending_hours.get(uid, 0.0) + h
        for uid, m in messages_snapshot.items():
            _pending_messages[uid] = _pending_messages.get(uid, 0) + m
        print(f"❌ Ошибка флаша activity: {e}")
    finally:
        await conn.close()


# --- подключение к боту -------------------------------------------------------------

def setup_activity_tracking(bot: commands.Bot, cursor, db_connection_factory):
    """Вызывается один раз из main.py."""
    set_cursor(cursor)
    set_connection_factory(db_connection_factory)

    async def _on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        # если сменил канал (или вышел) - убрать из старого
        old_channel_id = before.channel.id if before.channel else None
        new_channel_id = after.channel.id if after.channel else None

        if old_channel_id is not None and old_channel_id != new_channel_id:
            ch = _voice_members.get(old_channel_id)
            if ch:
                ch.pop(member.id, None)
                if not ch:
                    del _voice_members[old_channel_id]

        # добавить/обновить в новом (или том же) канале
        if new_channel_id is not None:
            _voice_members.setdefault(new_channel_id, {})[member.id] = (member, after)

    async def _on_message(message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            return
        _pending_messages[message.author.id] = _pending_messages.get(message.author.id, 0) + 1

    async def _on_ready_start_loops():
        _snapshot_guild_voice_state(bot)
        if not voice_tick.is_running():
            voice_tick.start()
        if not flush_activity.is_running():
            flush_activity.start()
        print("✅ Учёт активности (часы в войсе / сообщения) запущен")

    # add_listener, а не @bot.event - чтобы не перебить существующие обработчики
    # (например on_voice_state_update в commands_lobby.py) и спокойно сосуществовать с ними.
    bot.add_listener(_on_voice_state_update, "on_voice_state_update")
    bot.add_listener(_on_message, "on_message")
    bot.add_listener(_on_ready_start_loops, "on_ready")
