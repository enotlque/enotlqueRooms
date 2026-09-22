import discord
import asyncio
from contextlib import asynccontextmanager
from discord import app_commands, Interaction, Embed, ButtonStyle
from discord.ui import View, Button, Select, Modal, TextInput
from discord.ext import tasks
from datetime import datetime, timedelta
from collections import namedtuple
from rate_limiter import safe_discord_call

# ============================================
# СИСТЕМА ПРИВАТНЫХ ВРЕМЕННЫХ КОМНАТ (join-to-create)
# ============================================
CREATE_CHANNEL_NAME = "┗➕ ◦ Создать"
SETTINGS_CHANNEL_NAME = "┍⚙️・настройка"
TRIGGER_CHANNEL_LIMIT = 2       # лимит у самого канала-триггера "Создать"
DEFAULT_ROOM_LIMIT = 0          # 0 = без ограничений — лимит личной комнаты по умолчанию
RENAME_COOLDOWN_MINUTES = 10
DATE_FORMAT = "%d.%m.%Y %H:%M:%S"

# --- Кэш конфига системы temp_rooms по guild_id (экономит запросы к БД) ---
_config_cache = {}

_locks = {}


class _LockEntry:
    __slots__ = ("lock", "refcount")

    def __init__(self):
        self.lock = asyncio.Lock()
        self.refcount = 0


@asynccontextmanager
async def _locked(key):
    # Получение/создание записи и инкремент refcount — синхронный код без
    # await между ними, поэтому в рамках одного event loop это атомарно и
    # не может гонки с параллельной задачей на этом же ключе.
    entry = _locks.get(key)
    if entry is None:
        entry = _LockEntry()
        _locks[key] = entry
    entry.refcount += 1

    try:
        async with entry.lock:
            yield
    finally:
        entry.refcount -= 1
        if entry.refcount <= 0 and _locks.get(key) is entry:
            del _locks[key]

ConfigRow = namedtuple('ConfigRow', 'guild_id category_id create_channel_id settings_channel_id panel_message_id')
RoomRow = namedtuple('RoomRow', 'voice_channel_id guild_id owner_id user_limit is_locked is_hidden created_at last_rename')


def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


PANEL_EMBED_DESCRIPTION = (
    "**Управление приватной комнатой**\n\n"
    "Жми следующие кнопки, чтобы настроить свою комнату\n\n"
    "<:lockroom:1530362658262351872> — Закрыть комнату\n"
    "<:unlockroom:1530362729863315516> — Открыть комнату\n"
    "<:skrit:1530363423026581524> — Скрыть комнату\n"
    "<:otkrit:1530363462302175272> — Показать комнату\n"
    "<:roomlimit:1530363527930314892> — Установить лимит\n\n"
    "<:vidatdostup:1530363632272281690> — Выдать доступ\n"
    "<:zabratdostup:1530363599317504071> — Забрать доступ\n"
    "<:kickroom:1530362848776294622> — Выгнать из комнаты\n\n"
    "<:changename:1530363133938368662> — Сменить название\n"
    "<:peredat:1530362967239950397> — Передать владельца\n\n"
    "-# Использовать их можно только когда у тебя есть приватный канал"
)


def build_panel_embed() -> Embed:
    return Embed(description=PANEL_EMBED_DESCRIPTION, color=0x6e6e6e)


def no_room_embed() -> Embed:
    return Embed(
        description="У вас нет активной приватной комнаты. Зайдите в канал создания, чтобы получить свою.",
        color=0xFF0000
    )


def error_embed(text: str) -> Embed:
    return Embed(description=text, color=0xFF0000)


def ok_embed(text: str) -> Embed:
    return Embed(description=text, color=0x6e6e6e)


# ============================================
# ФОНОВАЯ ОЧИСТКА ОПУСТЕВШИХ КОМНАТ (страховка на случай простоя бота)
# ============================================

_temp_room_cleanup_started = False


def start_temp_room_cleanup_task(bot, cursor):
    """Раз в 10 минут проверяет активные временные комнаты и удаляет те,
    что физически пусты (или уже удалены вручную) — на случай, если
    on_voice_state_update не отработал из-за простоя бота."""
    global _temp_room_cleanup_started
    if _temp_room_cleanup_started:
        return
    _temp_room_cleanup_started = True

    @tasks.loop(minutes=10)
    async def cleanup_empty_rooms():
        try:
            await cursor.execute('SELECT voice_channel_id, guild_id FROM temp_rooms')
            rows = cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка при чтении временных комнат для очистки: {e}")
            return

        for voice_channel_id, guild_id in rows:
            guild = bot.get_guild(guild_id)
            channel = guild.get_channel(voice_channel_id) if guild else None

            if channel is None:
                try:
                    await cursor.execute('DELETE FROM temp_rooms WHERE voice_channel_id = $1', voice_channel_id)
                except Exception as e:
                    print(f"❌ Ошибка удаления записи временной комнаты {voice_channel_id}: {e}")
                continue

            if not any(not m.bot for m in channel.members):
                try:
                    await safe_discord_call(lambda c=channel: c.delete(reason="Комната опустела (фоновая проверка)"))
                except Exception:
                    pass
                try:
                    await cursor.execute('DELETE FROM temp_rooms WHERE voice_channel_id = $1', voice_channel_id)
                except Exception as e:
                    print(f"❌ Ошибка удаления записи временной комнаты {voice_channel_id}: {e}")

    @cleanup_empty_rooms.before_loop
    async def before_cleanup():
        await bot.wait_until_ready()

    cleanup_empty_rooms.start()


def setup_temp_room_commands(bot, cursor):

    # ============================================
    # ДОСТУП К БД
    # ============================================

    async def get_config(guild_id):
        if guild_id in _config_cache:
            return _config_cache[guild_id]

        await cursor.execute(
            'SELECT guild_id, category_id, create_channel_id, settings_channel_id, panel_message_id '
            'FROM temp_rooms_config WHERE guild_id = $1',
            guild_id
        )
        row = cursor.fetchone()
        config = ConfigRow(*row) if row else None
        _config_cache[guild_id] = config
        return config

    def invalidate_config(guild_id):
        _config_cache.pop(guild_id, None)

    async def get_room_by_owner(guild_id, owner_id):
        await cursor.execute(
            'SELECT voice_channel_id, guild_id, owner_id, user_limit, is_locked, is_hidden, created_at, last_rename '
            'FROM temp_rooms WHERE guild_id = $1 AND owner_id = $2',
            guild_id, owner_id
        )
        row = cursor.fetchone()
        return RoomRow(*row) if row else None

    async def get_room_by_channel(channel_id):
        await cursor.execute(
            'SELECT voice_channel_id, guild_id, owner_id, user_limit, is_locked, is_hidden, created_at, last_rename '
            'FROM temp_rooms WHERE voice_channel_id = $1',
            channel_id
        )
        row = cursor.fetchone()
        return RoomRow(*row) if row else None

    async def delete_room_row(channel_id):
        await cursor.execute('DELETE FROM temp_rooms WHERE voice_channel_id = $1', channel_id)

    async def resolve_room_channel(guild, room: RoomRow):
        channel = guild.get_channel(room.voice_channel_id)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            await delete_room_row(room.voice_channel_id)
            return None
        return channel

    # ============================================
    # МОДАЛКИ (лимит / переименование)
    # ============================================

    class SetLimitModal(Modal, title="Лимит участников"):
        def __init__(self, channel_id):
            super().__init__()
            self.channel_id = channel_id
            self.limit_input = TextInput(
                label="Лимит (0-99, 0 = без ограничений)",
                placeholder="Например: 4",
                max_length=2,
                required=True
            )
            self.add_item(self.limit_input)

        async def on_submit(self, interaction: Interaction):
            raw = self.limit_input.value.strip()
            if not raw.isdigit() or not (0 <= int(raw) <= 99):
                await interaction.response.send_message(embed=error_embed("Введите целое число от 0 до 99."), ephemeral=True)
                return

            limit = int(raw)
            channel = interaction.guild.get_channel(self.channel_id)
            if channel is None:
                await interaction.response.send_message(embed=no_room_embed(), ephemeral=True)
                return

            if limit == 1 and len([m for m in channel.members if not m.bot]) <= 1:
                await interaction.response.send_message(
                    embed=error_embed("Нельзя поставить лимит 1, пока в комнате кроме вас никого нет."),
                    ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)
            await safe_discord_call(lambda: channel.edit(user_limit=limit, reason="Изменение лимита участников владельцем комнаты"))
            await cursor.execute('UPDATE temp_rooms SET user_limit = $1 WHERE voice_channel_id = $2', limit, self.channel_id)

            limit_text = "без ограничений" if limit == 0 else str(limit)
            await interaction.followup.send(embed=ok_embed(f"Лимит участников установлен: **{limit_text}**."), ephemeral=True)

    class RenameRoomModal(Modal, title="Название комнаты"):
        def __init__(self, channel_id):
            super().__init__()
            self.channel_id = channel_id
            self.name_input = TextInput(
                label="Новое название",
                placeholder="Например: Тихий уголок",
                max_length=95,
                required=True
            )
            self.add_item(self.name_input)

        async def on_submit(self, interaction: Interaction):
            channel = interaction.guild.get_channel(self.channel_id)
            if channel is None:
                await interaction.response.send_message(embed=no_room_embed(), ephemeral=True)
                return

            new_name = self.name_input.value.strip()
            if not new_name:
                await interaction.response.send_message(embed=error_embed("Название не может быть пустым."), ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            try:
                await safe_discord_call(lambda: channel.edit(name=new_name, reason="Смена названия комнаты владельцем"))
            except discord.HTTPException as e:
                if e.status == 429:
                    await interaction.followup.send(
                        embed=error_embed("Discord временно ограничивает смену названия канала. Попробуйте чуть позже."),
                        ephemeral=True
                    )
                    return
                raise

            await cursor.execute(
                'UPDATE temp_rooms SET last_rename = $1 WHERE voice_channel_id = $2',
                datetime.now().strftime(DATE_FORMAT), self.channel_id
            )
            await interaction.followup.send(embed=ok_embed(f"Комната переименована в **{new_name}**."), ephemeral=True)

    # ============================================
    # ВЫБОР ЛЮБОГО ПОЛЬЗОВАТЕЛЯ СЕРВЕРА (доступ выдать/забрать)
    # ============================================

    class AccessUserSelectView(View):
        def __init__(self, channel_id, owner_id, mode):
            super().__init__(timeout=60)
            self.channel_id = channel_id
            self.owner_id = owner_id
            self.mode = mode  # 'grant' | 'revoke'
            self.add_item(AccessUserSelect(self))

    class AccessUserSelect(discord.ui.UserSelect):
        def __init__(self, parent_view: "AccessUserSelectView"):
            super().__init__(placeholder="Выберите участников (до 10)", min_values=1, max_values=10)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            guild = interaction.guild
            channel = guild.get_channel(self.parent_view.channel_id)
            if channel is None:
                await interaction.response.edit_message(embed=no_room_embed(), view=None)
                return

            await interaction.response.defer(ephemeral=True)

            processed, skipped = [], []
            for user in self.values:
                member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
                if member is None:
                    skipped.append(f"{user.mention} — не найден на сервере")
                    continue
                if member.id == self.parent_view.owner_id:
                    skipped.append(f"{member.mention} — вы владелец комнаты")
                    continue

                try:
                    if self.parent_view.mode == 'grant':
                        await safe_discord_call(lambda m=member: channel.set_permissions(
                            m, connect=True, view_channel=True, reason="Доступ выдан владельцем комнаты"
                        ))
                    else:
                        await safe_discord_call(lambda m=member: channel.set_permissions(
                            m, connect=False, reason="Доступ забран владельцем комнаты"
                        ))
                        if member.voice and member.voice.channel and member.voice.channel.id == channel.id:
                            await safe_discord_call(lambda m=member: m.move_to(None, reason="Доступ к комнате отозван"))
                    processed.append(member.mention)
                except Exception:
                    skipped.append(f"{member.mention} — ошибка")

            verb = "Выдан доступ" if self.parent_view.mode == 'grant' else "Забран доступ"
            summary = Embed(color=0x6e6e6e)
            if processed:
                summary.add_field(name=f"✅ {verb}", value="\n".join(processed), inline=False)
            if skipped:
                summary.add_field(name="🚫 Пропущены", value="\n".join(skipped), inline=False)
            if not processed and not skipped:
                summary.description = "Никто не был выбран."

            await interaction.edit_original_response(embed=summary, view=None)

    # ============================================
    # ВЫБОР ИЗ ТЕХ, КТО СЕЙЧАС В КАНАЛЕ (кик / мут / размут / передача)
    # ============================================

    class MemberActionSelectView(View):
        def __init__(self, channel_id, owner_id, action, members):
            super().__init__(timeout=60)
            self.channel_id = channel_id
            self.owner_id = owner_id
            self.action = action  # 'kick' | 'transfer'
            self.add_item(MemberActionSelect(self, members))

    class MemberActionSelect(Select):
        PLACEHOLDERS = {
            'kick': "Кого выгнать из комнаты",
            'transfer': "Кому передать управление",
        }

        def __init__(self, parent_view: "MemberActionSelectView", members: list):
            options = [
                discord.SelectOption(label=member.display_name[:100], value=str(member.id))
                for member in members[:25]
            ]
            max_values = 1 if parent_view.action == 'transfer' else len(options)
            super().__init__(
                placeholder=self.PLACEHOLDERS[parent_view.action],
                min_values=1,
                max_values=max_values,
                options=options
            )
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            guild = interaction.guild
            channel = guild.get_channel(self.parent_view.channel_id)
            if channel is None:
                await interaction.response.edit_message(embed=no_room_embed(), view=None)
                return

            await interaction.response.defer(ephemeral=True)
            action = self.parent_view.action

            if action == 'transfer':
                new_owner = guild.get_member(int(self.values[0]))
                still_in_channel = (
                    new_owner is not None and new_owner.voice is not None
                    and new_owner.voice.channel is not None and new_owner.voice.channel.id == channel.id
                )
                if not still_in_channel:
                    await interaction.edit_original_response(
                        embed=error_embed("Этот участник уже не в комнате."), view=None
                    )
                    return

                await cursor.execute('UPDATE temp_rooms SET owner_id = $1 WHERE voice_channel_id = $2', new_owner.id, channel.id)
                await interaction.edit_original_response(
                    embed=ok_embed(f"Управление комнатой **{channel.name}** передано {new_owner.mention}."),
                    view=None
                )
                return

            processed, skipped = [], []
            for value in self.values:
                member = guild.get_member(int(value))
                if member is None:
                    skipped.append(f"<@{value}> — не найден на сервере")
                    continue

                in_channel = bool(member.voice and member.voice.channel and member.voice.channel.id == channel.id)

                try:
                    if action == 'kick':
                        if not in_channel:
                            skipped.append(f"{member.mention} — уже не в канале")
                            continue
                        await safe_discord_call(lambda m=member: m.move_to(None, reason="Выгнан владельцем из приватной комнаты"))
                    processed.append(member.mention)
                except Exception:
                    skipped.append(f"{member.mention} — ошибка")

            verbs = {'kick': "Выгнаны"}
            summary = Embed(color=0x6e6e6e)
            if processed:
                summary.add_field(name=f"✅ {verbs[action]}", value="\n".join(processed), inline=False)
            if skipped:
                summary.add_field(name="🚫 Пропущены", value="\n".join(skipped), inline=False)
            if not processed and not skipped:
                summary.description = "Никто не был выбран."

            await interaction.edit_original_response(embed=summary, view=None)

    # ============================================
    # PERSISTENT-ПАНЕЛЬ УПРАВЛЕНИЯ (одно сообщение на весь сервер)
    # ============================================

    class TempRoomPanelView(View):
        def __init__(self):
            super().__init__(timeout=None)

        async def _get_owner_room(self, interaction: Interaction):
            """Возвращает (room, channel) владельца или отвечает ошибкой и возвращает (None, None)."""
            room = await get_room_by_owner(interaction.guild.id, interaction.user.id)
            if room is None:
                await interaction.response.send_message(embed=no_room_embed(), ephemeral=True)
                return None, None

            channel = await resolve_room_channel(interaction.guild, room)
            if channel is None:
                await interaction.response.send_message(embed=no_room_embed(), ephemeral=True)
                return None, None

            return room, channel

        # --- Ряд 1: состояние комнаты (закрыть/открыть, скрыть/показать, лимит) ---

        @discord.ui.button(emoji="<:lockroom:1530362658262351872>", style=ButtonStyle.secondary, custom_id="temprooms:lock", row=0)
        async def btn_lock(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            others = [m for m in channel.members if not m.bot and m.id != room.owner_id]
            if not others:
                await interaction.response.send_message(
                    embed=error_embed("Нельзя закрыть комнату, пока в ней кроме вас никого нет."),
                    ephemeral=True
                )
                return
            # Сразу подтверждаем interaction (лимит Discord — 3с), иначе при
            # медленном set_permissions получаем «приложение не ответило вовремя».
            await interaction.response.defer(ephemeral=True)
            await safe_discord_call(lambda: channel.set_permissions(
                interaction.guild.default_role, connect=False, reason="Комната закрыта владельцем"
            ))
            await cursor.execute('UPDATE temp_rooms SET is_locked = TRUE WHERE voice_channel_id = $1', channel.id)
            await interaction.followup.send(embed=ok_embed(f"Комната **{channel.name}** закрыта — заходить могут только те, кому выдан доступ."), ephemeral=True)

        @discord.ui.button(emoji="<:unlockroom:1530362729863315516>", style=ButtonStyle.secondary, custom_id="temprooms:unlock", row=0)
        async def btn_unlock(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            await interaction.response.defer(ephemeral=True)
            await safe_discord_call(lambda: channel.set_permissions(
                interaction.guild.default_role, connect=True, reason="Комната открыта владельцем"
            ))
            await cursor.execute('UPDATE temp_rooms SET is_locked = FALSE WHERE voice_channel_id = $1', channel.id)
            await interaction.followup.send(embed=ok_embed(f"Комната **{channel.name}** открыта."), ephemeral=True)

        @discord.ui.button(emoji="<:skrit:1530363423026581524>", style=ButtonStyle.secondary, custom_id="temprooms:hide", row=0)
        async def btn_hide(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            others = [m for m in channel.members if not m.bot and m.id != room.owner_id]
            if not others:
                await interaction.response.send_message(
                    embed=error_embed("Нельзя скрыть комнату, пока в ней кроме вас никого нет."),
                    ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            await safe_discord_call(lambda: channel.set_permissions(
                interaction.guild.default_role, view_channel=False, reason="Комната скрыта владельцем"
            ))
            await cursor.execute('UPDATE temp_rooms SET is_hidden = TRUE WHERE voice_channel_id = $1', channel.id)
            await interaction.followup.send(embed=ok_embed(f"Комната **{channel.name}** скрыта из списка каналов."), ephemeral=True)

        @discord.ui.button(emoji="<:otkrit:1530363462302175272>", style=ButtonStyle.secondary, custom_id="temprooms:show", row=0)
        async def btn_show(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            await interaction.response.defer(ephemeral=True)
            await safe_discord_call(lambda: channel.set_permissions(
                interaction.guild.default_role, view_channel=True, reason="Комната показана владельцем"
            ))
            await cursor.execute('UPDATE temp_rooms SET is_hidden = FALSE WHERE voice_channel_id = $1', channel.id)
            await interaction.followup.send(embed=ok_embed(f"Комната **{channel.name}** снова видна всем."), ephemeral=True)

        @discord.ui.button(emoji="<:roomlimit:1530363527930314892>", style=ButtonStyle.secondary, custom_id="temprooms:limit", row=0)
        async def btn_limit(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            await interaction.response.send_modal(SetLimitModal(channel.id))

        # --- Ряд 2: участники и владение (доступ, выгнать, название, передача) ---

        @discord.ui.button(emoji="<:vidatdostup:1530363632272281690>", style=ButtonStyle.secondary, custom_id="temprooms:grant", row=1)
        async def btn_grant(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            embed = ok_embed(f"Выберите участников, которым нужно выдать доступ к комнате **{channel.name}**.")
            view = AccessUserSelectView(channel.id, room.owner_id, 'grant')
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        @discord.ui.button(emoji="<:zabratdostup:1530363599317504071>", style=ButtonStyle.secondary, custom_id="temprooms:revoke", row=1)
        async def btn_revoke(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            embed = ok_embed(f"Выберите участников, у которых нужно забрать доступ к комнате **{channel.name}**.")
            view = AccessUserSelectView(channel.id, room.owner_id, 'revoke')
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        @discord.ui.button(emoji="<:kickroom:1530362848776294622>", style=ButtonStyle.secondary, custom_id="temprooms:kick", row=1)
        async def btn_kick(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            members = [m for m in channel.members if not m.bot and m.id != room.owner_id]
            if not members:
                await interaction.response.send_message(embed=error_embed("В комнате сейчас нет других участников."), ephemeral=True)
                return
            embed = ok_embed(f"Выберите, кого выгнать из комнаты **{channel.name}**.")
            view = MemberActionSelectView(channel.id, room.owner_id, 'kick', members)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        # --- (продолжение ряда 2: название и владение) ---

        @discord.ui.button(emoji="<:changename:1530363133938368662>", style=ButtonStyle.secondary, custom_id="temprooms:rename", row=1)
        async def btn_rename(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return

            if room.last_rename:
                try:
                    last = datetime.strptime(room.last_rename, DATE_FORMAT)
                    remaining = timedelta(minutes=RENAME_COOLDOWN_MINUTES) - (datetime.now() - last)
                    if remaining.total_seconds() > 0:
                        minutes_left = int(remaining.total_seconds() // 60) + 1
                        await interaction.response.send_message(
                            embed=error_embed(f"Название можно менять раз в {RENAME_COOLDOWN_MINUTES} минут. Подождите ещё ~{minutes_left} мин."),
                            ephemeral=True
                        )
                        return
                except ValueError:
                    pass

            await interaction.response.send_modal(RenameRoomModal(channel.id))

        @discord.ui.button(emoji="<:peredat:1530362967239950397>", style=ButtonStyle.secondary, custom_id="temprooms:transfer", row=1)
        async def btn_transfer(self, interaction: Interaction, button: Button):
            room, channel = await self._get_owner_room(interaction)
            if not room:
                return
            members = [m for m in channel.members if not m.bot and m.id != room.owner_id]
            if not members:
                await interaction.response.send_message(embed=error_embed("В комнате нет других участников, чтобы передать управление."), ephemeral=True)
                return
            embed = ok_embed(f"Кому передать управление комнатой **{channel.name}**?\n-# Выбрать можно только того, кто сейчас в канале.")
            view = MemberActionSelectView(channel.id, room.owner_id, 'transfer', members)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # Регистрируем persistent-view сразу — переживает рестарт бота благодаря
    # timeout=None + фиксированным custom_id у каждой кнопки.
    bot.add_view(TempRoomPanelView())

    # ============================================
    # СОЗДАНИЕ КОМНАТЫ ПРИ ВХОДЕ В ТРИГГЕР-КАНАЛ
    # ============================================

    async def handle_room_creation(member, guild, config: ConfigRow):
        async with _locked(('member', member.id)):
            # Если у пользователя уже есть активная комната — просто возвращаем его туда
            existing = await get_room_by_owner(guild.id, member.id)
            if existing:
                existing_channel = await resolve_room_channel(guild, existing)
                if existing_channel:
                    try:
                        await safe_discord_call(lambda: member.move_to(existing_channel, reason="У пользователя уже есть активная комната"))
                    except Exception:
                        pass
                    return

            category = guild.get_channel(config.category_id)
            if category is None or not isinstance(category, discord.CategoryChannel):
                return

            # Название без эмодзи/символов: "Комната <ник игрока>"
            room_name = f"Комната {member.display_name}"[:100]

            try:
                new_channel = await category.create_voice_channel(
                    name=room_name,
                    user_limit=DEFAULT_ROOM_LIMIT,
                    overwrites=dict(category.overwrites),
                    reason=f"Временная комната для {member}"
                )
            except discord.Forbidden:
                return
            except discord.HTTPException as e:
                print(f"❌ Ошибка создания временной комнаты для {member}: {e}")
                return

            # Пишем комнату в БД ДО перемещения участника: если пользователь
            # моментально отключится (обрыв связи), событие выхода уже найдёт
            # запись комнаты в temp_rooms и корректно её подчистит — вместо
            # того, чтобы канал остался физически "осиротевшим" без записи.
            try:
                await cursor.execute('''
                    INSERT INTO temp_rooms (voice_channel_id, guild_id, owner_id, user_limit, is_locked, is_hidden, created_at, last_rename)
                    VALUES ($1, $2, $3, $4, FALSE, FALSE, $5, NULL)
                ''', new_channel.id, guild.id, member.id, DEFAULT_ROOM_LIMIT, datetime.now().strftime(DATE_FORMAT))
            except Exception as e:
                print(f"❌ Ошибка записи временной комнаты в БД: {e}")

            try:
                await safe_discord_call(lambda: member.move_to(new_channel, reason="Перемещение в новую временную комнату"))
            except Exception:
                pass

    async def on_voice_state_update(member, before, after):
        guild = member.guild

        try:
            # === Вход в триггер-канал "Создать" ===
            if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
                config = await get_config(guild.id)
                if config and after.channel.id == config.create_channel_id:
                    await handle_room_creation(member, guild, config)

            # === Выход из комнаты — проверяем опустела ли она ===
            if before.channel is not None and (after.channel is None or after.channel.id != before.channel.id):
                config = await get_config(guild.id)
                # Технические каналы системы ("Создать" / "настройка") никогда
                # не хранятся в temp_rooms, но эта проверка — дополнительная
                # страховка: их нельзя удалить в этой ветке ни при каких условиях.
                is_system_channel = bool(config) and before.channel.id in (
                    config.create_channel_id, config.settings_channel_id
                )
                if not is_system_channel:
                    async with _locked(('channel', before.channel.id)):
                        room = await get_room_by_channel(before.channel.id)
                        if room:
                            remaining = [m for m in before.channel.members if not m.bot]
                            if not remaining:
                                try:
                                    await safe_discord_call(lambda c=before.channel: c.delete(reason="Комната опустела"))
                                except Exception:
                                    pass
                                await delete_room_row(before.channel.id)
        except Exception as e:
            print(f"❌ Ошибка обработки on_voice_state_update для временных комнат: {e}")

    bot.add_listener(on_voice_state_update, 'on_voice_state_update')

    # ============================================
    # /vremcomnata — НАСТРОЙКА СИСТЕМЫ (АДМИНИСТРАЦИЯ)
    # ============================================

    async def create_system_in_category(interaction: Interaction, category: discord.CategoryChannel):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        # Если система уже была настроена — убираем старые технические каналы
        old_config = await get_config(guild.id)
        if old_config:
            for old_id in (old_config.create_channel_id, old_config.settings_channel_id):
                old_channel = guild.get_channel(old_id) if old_id else None
                if old_channel:
                    try:
                        await safe_discord_call(lambda c=old_channel: c.delete(reason="Переустановка системы временных комнат"))
                    except Exception:
                        pass

        # Явно копируем overwrites категории — иначе новые каналы не наследуют
        # ограничения доступа категории (Discord API не синхронизирует их
        # автоматически при создании через API).
        category_overwrites = dict(category.overwrites)

        try:
            create_channel = await category.create_voice_channel(
                name=CREATE_CHANNEL_NAME,
                user_limit=TRIGGER_CHANNEL_LIMIT,
                overwrites=category_overwrites,
                reason="Настройка системы временных комнат"
            )
            settings_channel = await category.create_text_channel(
                name=SETTINGS_CHANNEL_NAME,
                overwrites=category_overwrites,
                reason="Настройка системы временных комнат"
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                embed=error_embed("У бота не хватает прав создавать каналы в этой категории."),
                view=None
            )
            return

        panel_message = await settings_channel.send(embed=build_panel_embed(), view=TempRoomPanelView())

        await cursor.execute('''
            INSERT INTO temp_rooms_config (guild_id, category_id, create_channel_id, settings_channel_id, panel_message_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id) DO UPDATE SET
                category_id = EXCLUDED.category_id,
                create_channel_id = EXCLUDED.create_channel_id,
                settings_channel_id = EXCLUDED.settings_channel_id,
                panel_message_id = EXCLUDED.panel_message_id
        ''', guild.id, category.id, create_channel.id, settings_channel.id, panel_message.id)
        invalidate_config(guild.id)

        result_embed = ok_embed(
            f"Система приватных временных комнат настроена в категории **{category.name}**.\n\n"
            f"Заходи в {create_channel.mention}, чтобы создать свою комнату.\n"
            f"Управлять ей можно через панель в {settings_channel.mention}."
        )
        await interaction.edit_original_response(embed=result_embed, view=None)

    class CategorySelectView(View):
        def __init__(self):
            super().__init__(timeout=120)
            self.add_item(CategoryChannelSelect())

    class CategoryChannelSelect(discord.ui.ChannelSelect):
        def __init__(self):
            super().__init__(
                placeholder="Выберите категорию",
                channel_types=[discord.ChannelType.category],
                min_values=1,
                max_values=1
            )

        async def callback(self, interaction: Interaction):
            guild = interaction.guild
            category = guild.get_channel(self.values[0].id)
            if category is None or not isinstance(category, discord.CategoryChannel):
                await interaction.response.edit_message(embed=error_embed("Не удалось найти выбранную категорию."), view=None)
                return
            await create_system_in_category(interaction, category)

    async def delete_system(guild: discord.Guild) -> bool:
        """Полностью удаляет систему временных комнат: все активные личные
        комнаты, канал-триггер, канал настройки и запись конфига."""
        config = await get_config(guild.id)
        if not config:
            return False

        await cursor.execute('SELECT voice_channel_id FROM temp_rooms WHERE guild_id = $1', guild.id)
        room_rows = cursor.fetchall()
        for (voice_channel_id,) in room_rows:
            room_channel = guild.get_channel(voice_channel_id)
            if room_channel:
                try:
                    await safe_discord_call(lambda c=room_channel: c.delete(reason="Удаление системы временных комнат"))
                except Exception:
                    pass
        await cursor.execute('DELETE FROM temp_rooms WHERE guild_id = $1', guild.id)

        for channel_id in (config.create_channel_id, config.settings_channel_id):
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel:
                try:
                    await safe_discord_call(lambda c=channel: c.delete(reason="Удаление системы временных комнат"))
                except Exception:
                    pass

        await cursor.execute('DELETE FROM temp_rooms_config WHERE guild_id = $1', guild.id)
        invalidate_config(guild.id)
        return True

    class MainMenuView(View):
        def __init__(self):
            super().__init__(timeout=120)

        @discord.ui.button(label="Создать", style=ButtonStyle.success, custom_id="temprooms:menu_create")
        async def btn_create(self, interaction: Interaction, button: Button):
            config = await get_config(interaction.guild.id)
            if config:
                await interaction.response.send_message(
                    embed=error_embed("Система уже настроена на этом сервере. Используйте «Пересоздать», чтобы перенастроить её на другую категорию."),
                    ephemeral=True
                )
                return
            embed = ok_embed("Выберите категорию, в которой будет создана система приватных временных комнат.")
            await interaction.response.send_message(embed=embed, view=CategorySelectView(), ephemeral=True)

        @discord.ui.button(label="Удалить", style=ButtonStyle.danger, custom_id="temprooms:menu_delete")
        async def btn_delete(self, interaction: Interaction, button: Button):
            config = await get_config(interaction.guild.id)
            if not config:
                await interaction.response.send_message(
                    embed=error_embed("Система временных комнат ещё не настроена на этом сервере."),
                    ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            await delete_system(interaction.guild)
            await interaction.edit_original_response(
                embed=ok_embed("Система приватных временных комнат удалена: технические каналы и все активные личные комнаты закрыты."),
                view=None
            )

        @discord.ui.button(label="Пересоздать", style=ButtonStyle.primary, custom_id="temprooms:menu_recreate")
        async def btn_recreate(self, interaction: Interaction, button: Button):
            embed = ok_embed("Выберите категорию, в которой будет пересоздана система приватных временных комнат.")
            await interaction.response.send_message(embed=embed, view=CategorySelectView(), ephemeral=True)

    @app_commands.command(name="vremcomnata", description="Настроить систему приватных временных комнат [Только для Администрации]")
    @app_commands.guild_only()
    @app_commands.check(is_admin)
    async def vremcomnata(interaction: Interaction):
        embed = ok_embed("Выберите действие для системы приватных временных комнат.")
        await interaction.response.send_message(embed=embed, view=MainMenuView(), ephemeral=True)

    @vremcomnata.error
    async def vremcomnata_error_handler(interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                embed=error_embed("У вас недостаточно прав для использования этой команды!"),
                ephemeral=True
            )
        else:
            raise error

    bot.tree.add_command(vremcomnata)
