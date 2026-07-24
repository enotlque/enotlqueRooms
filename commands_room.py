import discord
from discord.ext import commands
from discord import app_commands
from discord import Intents
import re
from discord import ButtonStyle
from discord.ui import Button, View, Select, Modal, TextInput
import asyncio
from datetime import datetime, timedelta
from discord import Embed, Interaction, Member
from discord.ext import commands, tasks
import time
import random


# Создаем объект intents и устанавливаем нужные параметры
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Разрешает просмотр и обработку содержимого сообщений

HEX_COLOR_REGEX = re.compile(r'^#[0-9A-Fa-f]{6}$')

# === КОНСТАНТЫ СИСТЕМЫ КОМНАТ ===
ROOM_DATE_FORMAT = "%d.%m.%Y в %Hч %Mм %Sс"
ROOM_CREATE_COST = 5000
MAX_ROOMS_TOTAL = 15
ROOM_EXTEND_DAY_COST = 50
ROOM_CREATE_DAYS = 30
ROOM_MAX_EXTEND_DAYS = 365
ROOM_AUTO_RENEW_MAX_DAYS = 30


def format_room_timedelta(delta: timedelta) -> str:
    """Форматирует timedelta в вид '5д 03ч 12м 45с' (как в системе ролей/браков)."""
    if delta.total_seconds() <= 0:
        return "0д 00ч 00м 00с"
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}д {hours:02}ч {minutes:02}м {seconds:02}с"


# Проверка на наличие у пользователя роли администратора
def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator


# ============================================
# АВТОПРОДЛЕНИЕ / АВТОУДАЛЕНИЕ КОМНАТ ПО ИСТЕЧЕНИИ СРОКА
# ============================================

_room_task_started = False


def start_room_expiry_task(bot, cursor):
    """Запускает фоновую проверку истёкших комнат. Если у комнаты есть банк —
    комната автоматически продлевается за его счёт (как в системе браков),
    иначе комната (роль + каналы) удаляется. Безопасно вызывать повторно."""
    global _room_task_started
    if _room_task_started:
        return
    _room_task_started = True

    @tasks.loop(hours=1)
    async def check_room_expirations():
        try:
            await cursor.execute('''
                SELECT leader_id, room_name, expiration_date, room_balance, role_id, text_channel_id, voice_channel_id
                FROM room_leadership
            ''')
            rows = cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка при чтении комнат для автопроверки: {e}")
            return

        now = datetime.now()

        for leader_id, room_name, expiration_date, room_balance, role_id, text_channel_id, voice_channel_id in rows:
            if not expiration_date:
                continue

            try:
                expiration = datetime.strptime(expiration_date, ROOM_DATE_FORMAT)
            except (TypeError, ValueError):
                continue

            if expiration > now:
                continue  # Комната ещё активна

            affordable_days = (room_balance or 0) // ROOM_EXTEND_DAY_COST

            if affordable_days >= 1:
                days_to_add = min(affordable_days, ROOM_AUTO_RENEW_MAX_DAYS)
                cost = days_to_add * ROOM_EXTEND_DAY_COST
                new_expiration_str = (expiration + timedelta(days=days_to_add)).strftime(ROOM_DATE_FORMAT)

                try:
                    await cursor.execute(
                        'UPDATE room_leadership SET expiration_date = $1, room_balance = room_balance - $2, extend_date = $3 WHERE leader_id = $4',
                        new_expiration_str, cost, now.strftime(ROOM_DATE_FORMAT), leader_id
                    )
                    print(f"✅ Комната '{room_name}' автопродлена на {days_to_add} дн. (списано {cost} из банка комнаты)")
                except Exception as e:
                    print(f"❌ Ошибка автопродления комнаты '{room_name}': {e}")
                continue

            # Средств в банке не хватает — комната удаляется
            for guild in bot.guilds:
                role = guild.get_role(role_id) if role_id else None
                if role:
                    try:
                        await role.delete(reason="Автоматическое удаление: истёк срок действия комнаты")
                    except Exception as e:
                        print(f"❌ Не удалось удалить роль комнаты '{room_name}': {e}")

                text_channel = guild.get_channel(text_channel_id) if text_channel_id else None
                if text_channel:
                    try:
                        await text_channel.delete(reason="Автоматическое удаление: истёк срок действия комнаты")
                    except Exception:
                        pass

                voice_channel = guild.get_channel(voice_channel_id) if voice_channel_id else None
                if voice_channel:
                    try:
                        await voice_channel.delete(reason="Автоматическое удаление: истёк срок действия комнаты")
                    except Exception:
                        pass

            try:
                await cursor.execute('DELETE FROM room_leadership WHERE leader_id = $1', leader_id)
                print(f"🗑️ Комната '{room_name}' автоматически удалена: истёк срок действия, банк пуст")
            except Exception as e:
                print(f"❌ Ошибка удаления комнаты '{room_name}' из БД: {e}")
                continue

            owner = bot.get_user(leader_id)
            if owner:
                try:
                    await owner.send(embed=discord.Embed(
                        description=(
                            f"Ваша комната **{room_name}** была автоматически удалена: "
                            "истёк срок действия, и в банке комнаты не хватило средств на продление."
                        ),
                        color=0x6e6e6e
                    ))
                except Exception:
                    pass  # Пользователь закрыл личные сообщения — не критично

    @check_room_expirations.before_loop
    async def before_check():
        await bot.wait_until_ready()

    check_room_expirations.start()

def setup_room_commands(bot, cursor, CATEGORY_ID, restricted_role_id):
    room_group = app_commands.Group(name="room", description="Управление комнатами")
    POSITION_UNDER_ROLE_ID = 1295482170374095049

    async def update_manage_message(parent_view):
        """Обновляет основное сообщение управления комнатой (счетчик участников и т.д.)"""
        if parent_view.original_message is None:
            return

        embed = Embed(color=0x6e6e6e)
        embed.set_author(name=f"Управление комнатой - {parent_view.owner.display_name}", icon_url=parent_view.owner.display_avatar.url)
        embed.add_field(name="<:mice:1526013753110433872> Комната", value=parent_view.room_name, inline=True)
        embed.add_field(name="<:people:1526013751457874033> Участников", value=str(parent_view.member_count), inline=True)

        try:
            await parent_view.original_message.edit(embed=embed, view=parent_view)
        except (discord.NotFound, discord.HTTPException):
            # Панель ephemeral — редактирование через webhook-токен доступно ~15 минут
            # после открытия /room manage. Если панель открыта дольше, обновление
            # счётчика молча пропускается: владельцу нужно переоткрыть /room manage.
            pass

    # owner_role_id -> set(member_id) с активным (ещё не принятым/не отклонённым/не истекшим) приглашением
    MAX_PENDING_INVITES = 10
    pending_invites: dict = {}

    @room_group.command(name="create", description=f"Создать собственную комнату ({ROOM_CREATE_COST} монет)")
    @app_commands.describe(
        комната="Название комнаты",
        роль="Название роли",
        цвет="Цвет роли в HEX формате (например, #000000)"
    )
    async def createroom(interaction: discord.Interaction, комната: str, роль: str, цвет: str):
        guild = interaction.guild
        участник = interaction.user
        category = guild.get_channel(CATEGORY_ID)

        await interaction.response.defer(ephemeral=True)

        # Проверка категории
        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                embed=Embed(description="Указанная категория не найдена.", color=0xFF0000),
                ephemeral=True
            )
            return

        # Валидация HEX цвета
        if not HEX_COLOR_REGEX.match(цвет):
            await interaction.followup.send(
                embed=Embed(
                    description="Некорректный формат цвета. Используйте HEX формат, например: #000000",
                    color=0xFF0000
                ),
                ephemeral=True
            )
            return

        # Проверка уникальности имени комнаты
        await cursor.execute('SELECT room_name FROM room_leadership WHERE room_name = $1', комната)
        if cursor.fetchone():
            await interaction.followup.send(
                embed=Embed(
                    description=f"Имя комнаты '{комната}' уже занято. Выберите другое имя.",
                    color=0xFF0000
                ),
                ephemeral=True
            )
            return

        # Проверка существующей комнаты у пользователя (1 владелец - 1 комната)
        await cursor.execute('SELECT room_name FROM room_leadership WHERE leader_id = $1', участник.id)
        if existing_room := cursor.fetchone():
            await interaction.followup.send(
                embed=Embed(
                    description=f"Вы уже владеете комнатой '{existing_room[0]}'! Одному владельцу доступна только одна комната.",
                    color=0xFFA500
                ),
                ephemeral=True
            )
            return

        # Проверка общего лимита комнат
        await cursor.execute('SELECT COUNT(*) FROM room_leadership')
        total_rooms = cursor.fetchone()[0]
        if total_rooms >= MAX_ROOMS_TOTAL:
            await interaction.followup.send(
                embed=Embed(
                    description=f"Достигнут лимит комнат на сервере ({MAX_ROOMS_TOTAL}/{MAX_ROOMS_TOTAL}).",
                    color=0xFF0000
                ),
                ephemeral=True
            )
            return

        # Проверка баланса
        await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', участник.id)
        profile = cursor.fetchone()
        if not profile or profile[0] < ROOM_CREATE_COST:
            await interaction.followup.send(
                embed=Embed(
                    description=f"Недостаточно средств для создания комнаты. Требуется {ROOM_CREATE_COST} монет.",
                    color=0xFF0000
                ),
                ephemeral=True
            )
            return

        # Создание роли
        role_color = int(цвет.lstrip('#'), 16)
        role = await guild.create_role(name=роль, color=discord.Color(role_color))

        # Позиционирование роли
        if reference_role := guild.get_role(POSITION_UNDER_ROLE_ID):
            try:
                await role.edit(position=reference_role.position - 1)
            except discord.Forbidden:
                await interaction.followup.send("Не удалось установить позицию роли!", ephemeral=True)

        # Создание каналов
        text_channel = await guild.create_text_channel(комната, category=category)
        voice_channel = await guild.create_voice_channel(f"◦ {комната}", category=category, user_limit=99)

        # Настройка прав доступа для текстового канала
        text_overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
            role: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                connect=True,
                view_channel=True
            ),
            guild.get_role(restricted_role_id): discord.PermissionOverwrite(
                read_messages=False,
                view_channel=False
            )
        }

        # Настройка прав доступа для голосового канала
        voice_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,  # Разрешаем просмотр
                connect=False       # Запрещаем вход
            ),
            role: discord.PermissionOverwrite(
                view_channel=True,  # Разрешаем просмотр
                connect=True,       # Разрешаем вход
                speak=True         # Разрешаем говорить
            ),
            guild.get_role(restricted_role_id): discord.PermissionOverwrite(
                view_channel=False,
                connect=False
            )
        }

        await text_channel.edit(overwrites=text_overwrites)
        await voice_channel.edit(overwrites=voice_overwrites)

        # Списание стоимости
        await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', ROOM_CREATE_COST, участник.id)

        creation_date = datetime.now()
        expiration_date = creation_date + timedelta(days=ROOM_CREATE_DAYS)
        creation_date_str = creation_date.strftime(ROOM_DATE_FORMAT)
        expiration_date_str = expiration_date.strftime(ROOM_DATE_FORMAT)

        # Сохранение в базе данных
        await cursor.execute('''
            INSERT INTO room_leadership (
                leader_id, 
                room_name, 
                role_id, 
                text_channel_id, 
                voice_channel_id, 
                creation_date,
                expiration_date,
                room_balance
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ''', участник.id, комната, role.id, text_channel.id, voice_channel.id, creation_date_str, expiration_date_str, 0)

        # Выдача роли пользователю
        await участник.add_roles(role)

        success_embed = Embed(
            description=f"Вы успешно создали комнату на `{ROOM_CREATE_DAYS} д`",
            color=0x6e6e6e
        )
        success_embed.set_author(name=участник.display_name, icon_url=участник.display_avatar.url)
        success_embed.add_field(name="Название", value=комната, inline=True)
        success_embed.add_field(name="Роль", value=role.mention, inline=True)
        success_embed.add_field(name="Дата истечения", value=expiration_date_str, inline=True)

        await interaction.followup.send(embed=success_embed, ephemeral=True)

    # Обработчик ошибок для группы комнат
    @room_group.error
    async def room_error_handler(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            error_embed = Embed(
                description="У вас недостаточно прав для использования этой команды!",
                color=0x6e6e6e
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
        else:
            raise error

    @room_group.command(name="list", description="Показать список существующих комнат")
    async def roomlist(interaction: Interaction):
        await list_rooms(interaction, 0, new_message=True)

    async def list_rooms(interaction: Interaction, offset: int, new_message: bool = False):
        await cursor.execute('SELECT room_name, leader_id FROM room_leadership LIMIT 5 OFFSET $1', offset)
        rooms = cursor.fetchall()

        if not rooms:
            embed = Embed(description="<a:writing:1526019043083948232> На данный момент комнат не обнаружено", color=0x000000)
            if new_message:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.edit_original_response(embed=embed, view=None)
            return

        await cursor.execute('SELECT COUNT(*) FROM room_leadership')
        total_rooms = cursor.fetchone()[0]

        total_pages = (total_rooms + 4) // 5  # Чтобы округлить вверх

        embed = Embed(title=f"Список комнат ({total_rooms})", color=0x39393c)
        for index, (room_name, leader_id) in enumerate(rooms, start=1 + offset):
            leader = await interaction.guild.fetch_member(leader_id)
            leader_name = leader.display_name if leader else f"ID: **{leader_id}**"
            embed.add_field(
                name=f"{index}) {room_name}",
                value=f"Владелец: {leader_name}",
                inline=False
            )

        page_number = (offset // 5) + 1
        embed.set_footer(text=f"Страница {page_number} из {total_pages}")

        view = RoomListView(offset, total_rooms, total_pages)
        if new_message:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.edit_original_response(embed=embed, view=view)

    class RoomListView(View):
        def __init__(self, offset: int, total_rooms: int, total_pages: int):
            super().__init__(timeout=None)  # Убрать таймаут
            self.offset = offset
            self.total_rooms = total_rooms
            self.total_pages = total_pages
            self.add_buttons()

        def add_buttons(self):
            page_number = (self.offset // 5) + 1

            # Кнопка "Назад"
            if page_number > 1:
                self.add_item(PreviousButton(self.offset))
            else:
                self.add_item(Button(label="Назад", style=ButtonStyle.secondary, disabled=True))

            # Кнопка "Следующая"
            if page_number < self.total_pages:
                self.add_item(NextButton(self.offset))
            else:
                self.add_item(Button(label="Следующая", style=ButtonStyle.secondary, disabled=True))

    class PreviousButton(Button):
        def __init__(self, offset: int):
            super().__init__(label="Назад", style=ButtonStyle.secondary)
            self.offset = offset

        async def callback(self, interaction: Interaction):
            await interaction.response.defer()  # Отложенный ответ для предотвращения тайм-аутов
            await list_rooms(interaction, self.offset - 5, new_message=False)

    class NextButton(Button):
        def __init__(self, offset: int):
            super().__init__(label="Следующая", style=ButtonStyle.success)  # Зеленая кнопка
            self.offset = offset

        async def callback(self, interaction: Interaction):
            await interaction.response.defer()  # Отложенный ответ для предотвращения тайм-аутов
            await list_rooms(interaction, self.offset + 5, new_message=False)

    @room_group.command(name='info', description='Показать информацию о комнате')
    @app_commands.describe(
        комната="Укажите ID комнаты или ID владельца"
    )
    async def room_info(interaction: discord.Interaction, комната: str):
        try:
            # Пробуем преобразовать введенное значение в число (ID)
            search_id = int(комната)
            
            # Пытаемся найти комнату по ID владельца или ID канала
            await cursor.execute('''
                SELECT room_name, leader_id, role_id, text_channel_id, voice_channel_id, creation_date, expiration_date
                FROM room_leadership 
                WHERE leader_id = $1 OR text_channel_id = $2 OR voice_channel_id = $3
            ''', search_id, search_id, search_id)
        except ValueError:
            # Если не удалось преобразовать в число, ищем по имени комнаты
            await cursor.execute('''
                SELECT room_name, leader_id, role_id, text_channel_id, voice_channel_id, creation_date, expiration_date
                FROM room_leadership 
                WHERE room_name = $1
            ''', комната)

        room = cursor.fetchone()
        
        if not room:
            embed = discord.Embed(
                description="<:xrestik:1526013747112448090> Комната не найдена.",
                color=0x6e6e6e
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        room_name, leader_id, role_id, text_channel_id, voice_channel_id, creation_date, expiration_date = room

        if expiration_date:
            try:
                expiration = datetime.strptime(expiration_date, ROOM_DATE_FORMAT)
                days_left_str = format_room_timedelta(expiration - datetime.now())
            except ValueError:
                days_left_str = "Ошибка в дате"
        else:
            days_left_str = "Не указано"

        # Получение данных о роли
        role = interaction.guild.get_role(role_id)
        role_name = role.mention if role else f"Не найдена"
        role_hex = f"#{role.color.value:06x}" if role else "Не найден"

        # Получение данных о лидере
        leader = interaction.guild.get_member(leader_id)
        leader_name = leader.mention if leader else f"ID: {leader_id}"

        # Подсчет участников с данной ролью
        member_count = sum(1 for member in interaction.guild.members if role and role in member.roles)

        # Получение текстового и голосового каналов
        text_channel = interaction.guild.get_channel(text_channel_id)
        voice_channel = interaction.guild.get_channel(voice_channel_id)

        text_channel_mention = f"<#{text_channel_id}>" if text_channel else f"ID: {text_channel_id}"
        voice_channel_mention = f"<#{voice_channel_id}>" if voice_channel else f"ID: {voice_channel_id}"

        # Создание Embed сообщения
        embed = discord.Embed(
            title=f"Информация о комнате {room_name}",
            color=0x6e6e6e
        )

        # Добавление отдельных полей для каждого параметра
        embed.add_field(name="<:leader:1526013741106331729> Владелец", value=leader_name, inline=True)
        embed.add_field(name="<:calendar:1526013739747508224> Дата создания", value=creation_date or "Не указано", inline=True)
        embed.add_field(name="<:calendar:1526013739747508224> Дата истечения", value=expiration_date or "Не указано", inline=True)
        embed.add_field(name="<:vremya:1337141252151447555> Осталось дней", value=days_left_str, inline=True)
        embed.add_field(name="<:almaz:1526013736781873182> Роль", value=role_name, inline=True)
        embed.add_field(name="<:info:1526013735246893238> HEX-код роли", value=role_hex, inline=True)
        embed.add_field(name="<:mice:1526013753110433872> Войс", value=voice_channel_mention, inline=True)
        embed.add_field(name="<:chat:1526013733833408612> Текстовой", value=text_channel_mention, inline=True)
        embed.add_field(name="<:eshepeople:1526013744314843222> Участников", value=str(member_count), inline=True)

        await interaction.response.send_message(embed=embed)

    @room_group.command(name="delete", description="Удалить комнату указанного пользователя [Только для Администрации]")
    @app_commands.describe(
        участник="Участник, чью комнату необходимо удалить"
    )
    async def roomdelete(interaction: discord.Interaction, участник: discord.User):
        requester = interaction.user
        guild = interaction.guild

        if not is_admin(interaction):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="Вы не являетесь администратором и не можете использовать эту команду.", 
                    color=0x6e6e6e
                ), 
                ephemeral=True
            )
            return

        await cursor.execute('SELECT room_name, text_channel_id, voice_channel_id, role_id FROM room_leadership WHERE leader_id = $1', участник.id)
        result = cursor.fetchone()
        if result is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"У пользователя {участник.mention} нет своей комнаты для удаления.", 
                    color=0x6e6e6e
                ), 
                ephemeral=True
            )
            return

        room_name = result[0]
        text_channel_id = result[1]
        voice_channel_id = result[2]
        role_id = result[3]

        role = guild.get_role(role_id)
        if role:
            try:
                await role.delete()
            except discord.errors.HTTPException as e:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description=f"Ошибка при удалении роли: {str(e)}", 
                        color=0x6e6e6e
                    ), 
                    ephemeral=True
                )

        text_channel = guild.get_channel(text_channel_id)
        if text_channel:
            try:
                await text_channel.delete()
            except discord.errors.HTTPException as e:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description=f"Ошибка при удалении текстового канала: {str(e)}", 
                        color=0x6e6e6e
                    ), 
                    ephemeral=True
                )

        voice_channel = guild.get_channel(voice_channel_id)
        if voice_channel:
            try:
                await voice_channel.delete()
            except discord.errors.HTTPException as e:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description=f"Ошибка при удалении голосового канала: {str(e)}", 
                        color=0x6e6e6e
                    ), 
                    ephemeral=True
                )

        await cursor.execute('DELETE FROM room_leadership WHERE leader_id = $1', участник.id)

        embed = discord.Embed(
            description="Успешное удаление комнаты",
            color=0x6e6e6e
        )

        # AUTHOR
        embed.set_author(name=f"{участник}", icon_url=участник.display_avatar.url)

        # Fields
        embed.add_field(name="Комната", value=room_name, inline=True)
        embed.add_field(name="Пользователь", value=участник.mention, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def build_owner_room_embed(owner, room_name, member_count):
        """Строит embed управления комнатой владельцем: даты, банк, участники."""
        await cursor.execute(
            'SELECT creation_date, expiration_date, room_balance FROM room_leadership WHERE leader_id = $1',
            owner.id
        )
        row = cursor.fetchone()
        creation_date, expiration_date, room_balance = row if row else (None, None, 0)

        if expiration_date:
            try:
                expiration = datetime.strptime(expiration_date, ROOM_DATE_FORMAT)
                days_left_str = format_room_timedelta(expiration - datetime.now())
            except ValueError:
                days_left_str = "Ошибка в дате"
        else:
            days_left_str = "Не указано"

        embed = Embed(color=0x6e6e6e)
        embed.set_author(name=f"Управление комнатой - {owner.display_name}", icon_url=owner.avatar.url if owner.avatar else owner.default_avatar.url)
        embed.add_field(name="<:mice:1526013753110433872> Комната", value=room_name, inline=True)
        embed.add_field(name="<:people:1526013751457874033> Участников", value=str(member_count), inline=True)
        embed.add_field(name="<a:coinonrole:1298391257042784266> Банк комнаты", value=f"{room_balance or 0} монет", inline=True)
        embed.add_field(name="<:data:1337141473162039337> Дата создания", value=creation_date or "Не указано", inline=True)
        embed.add_field(name="<:watchw:1337130049123389500> Дата истечения", value=expiration_date or "Не указано", inline=True)
        embed.add_field(name="<:vremya:1337141252151447555> Осталось дней", value=days_left_str, inline=True)
        embed.set_footer(text=f"Продление: {ROOM_EXTEND_DAY_COST} монет/день")

        return embed

    @room_group.command(name="manage", description="Управление личной комнатой")
    async def introom(interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild

        await cursor.execute('SELECT room_name, role_id, creation_date, voice_channel_id FROM room_leadership WHERE leader_id = $1', user.id)
        result = cursor.fetchone()
        if result is None:
            await interaction.response.send_message(embed=Embed(description="У вас нет своей комнаты для управления.", color=0xFF0000), ephemeral=True)
            return

        room_name, role_id, creation_date, voice_channel_id = result
        role = guild.get_role(role_id)
        voice_channel = guild.get_channel(voice_channel_id) if voice_channel_id else None

        if not role:
            await interaction.response.send_message(embed=Embed(description="Роль вашей комнаты не найдена.", color=0xFFA500), ephemeral=True)
            return

        member_count = sum(1 for member in guild.members if role in member.roles)
        is_channel_open = voice_channel and voice_channel.permissions_for(guild.default_role).connect

        embed = await build_owner_room_embed(user, room_name, member_count)

        view = InitialView(
            owner_role_id=role_id,
            owner=user,
            room_name=room_name,
            member_count=member_count,
            voice_channel=voice_channel,
            is_channel_open=is_channel_open,
            interaction=interaction
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    class InitialView(View):
        def __init__(self, owner_role_id, owner, room_name, member_count, voice_channel, is_channel_open, interaction):
            super().__init__()
            self.owner_role_id = owner_role_id
            self.owner = owner
            self.room_name = room_name
            self.member_count = member_count
            self.voice_channel = voice_channel
            self.is_channel_open = is_channel_open
            self.interaction = interaction
            self.original_message = None
            
            self.add_item(ManageButton(self))
            self.add_item(ExtendRoomButton(self))
            self.add_item(BankDepositButton(self))
            self.add_item(TransferOwnershipButton(self))
            self.add_item(DeleteRoomButton(self))

        async def refresh(self, interaction: Interaction):
            """Перестраивает embed панели владельца после продления/пополнения банка."""
            embed = await build_owner_room_embed(self.owner, self.room_name, self.member_count)
            if interaction.message is not None:
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)

    class ManageButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="Управлять", style=ButtonStyle.secondary, emoji="<:knopka1:1526013750090399925>")
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(
                    embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000),
                    ephemeral=True
                )
                return

            view = ManageRoomView(
                owner_role_id=self.parent_view.owner_role_id,
                owner=self.parent_view.owner,
                room_name=self.parent_view.room_name,
                member_count=self.parent_view.member_count,
                voice_channel=self.parent_view.voice_channel,
                is_channel_open=self.parent_view.is_channel_open,
                interaction=interaction,
                original_view=self.parent_view
            )

            embed = self.parent_view.original_message.embeds[0]
            await interaction.response.edit_message(embed=embed, view=view)

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.parent_view.owner_role_id for role in interaction.user.roles)

    class BackButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="Назад", style=ButtonStyle.secondary, emoji="<:strelka:1526013742394118264>", row=1)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(
                    embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000),
                    ephemeral=True
                )
                return

            new_view = InitialView(
                owner_role_id=self.parent_view.owner_role_id,
                owner=self.parent_view.owner,
                room_name=self.parent_view.room_name,
                member_count=self.parent_view.member_count,
                voice_channel=self.parent_view.voice_channel,
                is_channel_open=self.parent_view.is_channel_open,
                interaction=interaction
            )

            embed = await build_owner_room_embed(self.parent_view.owner, self.parent_view.room_name, self.parent_view.member_count)

            await interaction.response.edit_message(embed=embed, view=new_view)
            new_view.original_message = await interaction.original_response()

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.parent_view.owner_role_id for role in interaction.user.roles)

    # ============================================
    # ПРОДЛЕНИЕ / БАНК / ПЕРЕДАЧА / УДАЛЕНИЕ КОМНАТЫ (ВЛАДЕЛЕЦ)
    # ============================================

    class ExtendRoomButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="Продлить", style=ButtonStyle.secondary, emoji="<:beskone4:1337141486512242868>", row=2)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return
            await interaction.response.send_modal(ExtendRoomModal(self.parent_view.owner.id, refresh_view=self.parent_view))

    class BankDepositButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="В банк", style=ButtonStyle.secondary, emoji="<a:coinonrole:1298391257042784266>", row=2)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return
            await interaction.response.send_modal(RoomBankDepositModal(self.parent_view.owner.id, refresh_view=self.parent_view))

    class TransferOwnershipButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="Передать управление", style=ButtonStyle.secondary, emoji="<:crownaa:1337141290068086825>", row=2)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return

            embed = Embed(
                description=f"Выберите пользователя, которому хотите передать управление комнатой **{self.parent_view.room_name}**.",
                color=0x6e6e6e
            )
            view = TransferSelectView(self.parent_view)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    class DeleteRoomButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="Удалить", style=ButtonStyle.danger, emoji="<:krestic:1337141359286550618>", row=2)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return

            confirm_embed = Embed(
                title="Подтверждение удаления",
                description=(
                    f"Вы действительно хотите удалить комнату **{self.parent_view.room_name}**?\n"
                    "Это действие необратимо — роль и каналы комнаты будут удалены с сервера, средства из банка сгорают."
                ),
                color=0x6e6e6e
            )
            confirm_view = RoomDeleteConfirmView(self.parent_view)
            await interaction.response.edit_message(embed=confirm_embed, view=confirm_view)

    class RoomDeleteConfirmView(View):
        """Второй шаг подтверждения удаления комнаты — Да/Отмена (как в /role manage)."""

        def __init__(self, parent_view):
            super().__init__(timeout=60)
            self.parent_view = parent_view

        async def on_timeout(self):
            pass

        @discord.ui.button(label="Да, удалить", style=ButtonStyle.danger, emoji="<:checkmark:1526013748718993428>")
        async def confirm(self, interaction: Interaction, button: Button):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            guild = interaction.guild
            role = guild.get_role(self.parent_view.owner_role_id)
            room_name = self.parent_view.room_name

            try:
                await cursor.execute('DELETE FROM room_leadership WHERE leader_id = $1', self.parent_view.owner.id)
            except Exception as e:
                await interaction.response.send_message(f"Не удалось удалить комнату из базы данных: {e}", ephemeral=True)
                return

            if role:
                try:
                    await role.delete(reason=f"Комната удалена владельцем ({interaction.user.id}) через панель управления")
                except Exception as e:
                    print(f"❌ Не удалось удалить роль комнаты '{room_name}': {e}")

            if self.parent_view.voice_channel:
                try:
                    await self.parent_view.voice_channel.delete()
                except Exception:
                    pass

            result_embed = Embed(
                description=f"<:galochka:1337141373446651955> Комната **{room_name}** удалена.",
                color=0x6e6e6e
            )
            self.stop()
            await interaction.response.edit_message(embed=result_embed, view=None)

        @discord.ui.button(label="Отмена", style=ButtonStyle.secondary)
        async def cancel(self, interaction: Interaction, button: Button):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            self.stop()
            embed = await build_owner_room_embed(self.parent_view.owner, self.parent_view.room_name, self.parent_view.member_count)
            await interaction.response.edit_message(embed=embed, view=self.parent_view)

    class TransferSelectView(View):
        def __init__(self, parent_view):
            super().__init__(timeout=60)
            self.parent_view = parent_view
            self.add_item(TransferUserSelect(self))

    class TransferUserSelect(discord.ui.UserSelect):
        def __init__(self, select_owner_view: "TransferSelectView"):
            super().__init__(placeholder="Выберите нового владельца", min_values=1, max_values=1)
            self.select_owner_view = select_owner_view

        async def callback(self, interaction: Interaction):
            parent_view = self.select_owner_view.parent_view
            if interaction.user.id != parent_view.owner.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            guild = interaction.guild
            new_owner = self.values[0]
            new_member = new_owner if isinstance(new_owner, discord.Member) else guild.get_member(new_owner.id)

            if new_member is None:
                await interaction.response.edit_message(embed=Embed(description="Пользователь не найден на сервере.", color=0xFF0000), view=None)
                return
            if new_member.bot:
                await interaction.response.edit_message(embed=Embed(description="Нельзя передать управление боту.", color=0xFF0000), view=None)
                return
            if new_member.id == parent_view.owner.id:
                await interaction.response.edit_message(embed=Embed(description="Вы уже владелец этой комнаты.", color=0xFF0000), view=None)
                return

            await cursor.execute('SELECT room_name FROM room_leadership WHERE leader_id = $1', new_member.id)
            if cursor.fetchone():
                await interaction.response.edit_message(embed=Embed(description=f"{new_member.mention} уже владеет другой комнатой.", color=0xFF0000), view=None)
                return

            confirm_embed = Embed(
                description=(
                    f"Вы действительно хотите передать управление комнатой **{parent_view.room_name}** "
                    f"пользователю {new_member.mention}?\nВы потеряете права владельца."
                ),
                color=0x6e6e6e
            )
            confirm_view = TransferConfirmView(parent_view, new_member)
            await interaction.response.edit_message(embed=confirm_embed, view=confirm_view)

    class TransferConfirmView(View):
        def __init__(self, parent_view, new_member):
            super().__init__(timeout=60)
            self.parent_view = parent_view
            self.new_member = new_member

        @discord.ui.button(label="Да, передать", style=ButtonStyle.success, emoji="<:checkmark:1526013748718993428>")
        async def confirm(self, interaction: Interaction, button: Button):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            guild = interaction.guild
            role = guild.get_role(self.parent_view.owner_role_id)

            try:
                await cursor.execute('UPDATE room_leadership SET leader_id = $1 WHERE leader_id = $2', self.new_member.id, self.parent_view.owner.id)
            except Exception as e:
                await interaction.response.send_message(f"Не удалось передать управление: {e}", ephemeral=True)
                return

            if role and role not in self.new_member.roles:
                try:
                    await self.new_member.add_roles(role)
                except Exception:
                    pass

            result_embed = Embed(
                description=f"<:galochka:1337141373446651955> Управление комнатой **{self.parent_view.room_name}** передано {self.new_member.mention}.",
                color=0x6e6e6e
            )
            self.stop()
            await interaction.response.edit_message(embed=result_embed, view=None)

        @discord.ui.button(label="Отмена", style=ButtonStyle.secondary)
        async def cancel(self, interaction: Interaction, button: Button):
            if interaction.user.id != self.parent_view.owner.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return
            self.stop()
            await interaction.response.edit_message(embed=Embed(description="Передача управления отменена.", color=0x6e6e6e), view=None)

    class ExtendRoomModal(Modal):
        """Продление комнаты. Используется и владельцем (панель управления), и участником (/room extend).
        Стоимость списывается с личного баланса того, кто продлевает."""

        def __init__(self, leader_id, refresh_view=None):
            super().__init__(title="Продление комнаты")
            self.leader_id = leader_id
            self.refresh_view = refresh_view  # объект с async def refresh(interaction), либо None
            self.add_item(TextInput(label=f"Количество дней (1 день {ROOM_EXTEND_DAY_COST} монет)", style=discord.TextStyle.short, placeholder="Введите количество дней"))

        async def on_submit(self, interaction: Interaction):
            try:
                days_to_extend = int(self.children[0].value)
                if days_to_extend < 1 or days_to_extend > ROOM_MAX_EXTEND_DAYS:
                    await interaction.response.send_message(f"В модальном окне только цифры от 1 до {ROOM_MAX_EXTEND_DAYS}", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message(f"В модальном окне только цифры от 1 до {ROOM_MAX_EXTEND_DAYS}", ephemeral=True)
                return

            await cursor.execute('SELECT expiration_date, room_name FROM room_leadership WHERE leader_id = $1', self.leader_id)
            row = cursor.fetchone()
            if not row:
                await interaction.response.send_message("Комната не найдена.", ephemeral=True)
                return
            expiration_date, room_name = row

            try:
                current_expiration = datetime.strptime(expiration_date, ROOM_DATE_FORMAT)
            except (TypeError, ValueError):
                current_expiration = datetime.now()

            max_extend_date = datetime.now() + timedelta(days=ROOM_MAX_EXTEND_DAYS)
            new_expiration_date = min(current_expiration + timedelta(days=days_to_extend), max_extend_date)
            actual_days_extended = max((new_expiration_date - current_expiration).days, 0)
            cost = actual_days_extended * ROOM_EXTEND_DAY_COST

            if actual_days_extended <= 0:
                await interaction.response.send_message("Комнату уже нельзя продлить дальше максимального срока.", ephemeral=True)
                return

            await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
            balance_row = cursor.fetchone()
            user_balance = balance_row[0] if balance_row else 0
            if user_balance < cost:
                await interaction.response.send_message(f"Недостаточно монет для продления. Необходимо {cost}, а у вас {user_balance}.", ephemeral=True)
                return

            await cursor.execute(
                'UPDATE room_leadership SET expiration_date = $1, extend_date = $2 WHERE leader_id = $3',
                new_expiration_date.strftime(ROOM_DATE_FORMAT), datetime.now().strftime(ROOM_DATE_FORMAT), self.leader_id
            )
            await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', cost, interaction.user.id)

            await interaction.response.send_message(
                f"Комната {room_name} успешно продлена на {actual_days_extended} дней за {cost} монет!", ephemeral=True
            )

            if self.refresh_view is not None:
                try:
                    await self.refresh_view.refresh(interaction)
                except Exception:
                    pass

    class RoomBankDepositModal(Modal):
        """Внесение монет в банк комнаты (используется владельцем и участниками)."""

        def __init__(self, leader_id, refresh_view=None):
            super().__init__(title="Пополнение банка комнаты")
            self.leader_id = leader_id
            self.refresh_view = refresh_view  # объект с async def refresh(interaction), либо None
            self.add_item(TextInput(label="Сумма пополнения", style=discord.TextStyle.short, placeholder="Введите количество монет"))

        async def on_submit(self, interaction: Interaction):
            try:
                amount = int(self.children[0].value)
                if amount < 1:
                    await interaction.response.send_message("Введите положительное число монет.", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("Введите положительное число монет.", ephemeral=True)
                return

            await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
            balance_row = cursor.fetchone()
            user_balance = balance_row[0] if balance_row else 0
            if user_balance < amount:
                await interaction.response.send_message(f"У вас недостаточно монет. Необходимо {amount}, а у вас {user_balance}.", ephemeral=True)
                return

            await cursor.execute('SELECT room_name FROM room_leadership WHERE leader_id = $1', self.leader_id)
            row = cursor.fetchone()
            if not row:
                await interaction.response.send_message("Комната не найдена.", ephemeral=True)
                return
            room_name = row[0]

            await cursor.execute('UPDATE room_leadership SET room_balance = room_balance + $1 WHERE leader_id = $2', amount, self.leader_id)
            await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', amount, interaction.user.id)

            await interaction.response.send_message(f"Вы внесли {amount} монет в банк комнаты {room_name}.", ephemeral=True)

            if self.refresh_view is not None:
                try:
                    await self.refresh_view.refresh(interaction)
                except Exception:
                    pass

    class ManageRoomView(View):
        def __init__(self, owner_role_id, owner, room_name, member_count, voice_channel, is_channel_open, interaction, original_view):
            super().__init__()
            self.owner_role_id = owner_role_id
            self.owner = owner
            self.room_name = room_name
            self.member_count = member_count
            self.voice_channel = voice_channel
            self.is_channel_open = is_channel_open
            self.interaction = interaction
            self.original_view = original_view
            self.original_message = original_view.original_message

            self.add_item(InviteButton(owner_role_id, self))
            self.add_item(RemoveButton(owner_role_id, self))
            
            if self.voice_channel:
                if self.is_channel_open:
                    self.add_item(CloseChannelButton(owner_role_id, owner, room_name, member_count, voice_channel, original_view.original_message))
                else:
                    self.add_item(OpenChannelButton(owner_role_id, owner, room_name, member_count, voice_channel, original_view.original_message))
            
            self.add_item(MembersListButton(owner_role_id, self))
            self.add_item(BackButton(self))

    class MembersListButton(Button):
        def __init__(self, owner_role_id, parent_view):
            super().__init__(label="Участники", style=ButtonStyle.secondary, emoji="<:eshepeople:1526013744314843222>", row=1)
            self.owner_role_id = owner_role_id
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(
                    embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000),
                    ephemeral=True
                )
                return

            guild = interaction.guild
            role = guild.get_role(self.owner_role_id)
            members = [member for member in guild.members if role in member.roles]
            
            await self.show_members_list(interaction, role, 0, members)

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.owner_role_id for role in interaction.user.roles)

        async def show_members_list(self, interaction: Interaction, role: discord.Role, offset: int, members: list):
            members_per_page = 10
            members_to_display = members[offset:offset + members_per_page]

            embed = Embed(
                title=f"Участники комнаты {self.parent_view.room_name}",
                color=0x6e6e6e
            )

            member_list = []
            for index, member in enumerate(members_to_display, start=offset + 1):
                member_list.append(f"**{index}.** {member.mention}")

            embed.description = "\n".join(member_list)
            total_pages = (len(members) + members_per_page - 1) // members_per_page
            embed.set_footer(text=f"Страница {offset // members_per_page + 1}/{total_pages}")

            view = MembersListView(offset, len(members), interaction, role, members, self.parent_view)
            await interaction.response.edit_message(embed=embed, view=view)

    class MembersListView(View):
        def __init__(self, offset: int, total_items: int, interaction: Interaction, role: discord.Role, members: list, parent_view):
            super().__init__()
            self.offset = offset
            self.total_items = total_items
            self.items_per_page = 10
            self.interaction = interaction
            self.role = role
            self.members = members
            self.parent_view = parent_view
            self.update_buttons()

        def update_buttons(self):
            self.clear_items()
            max_pages = (len(self.members) + self.items_per_page - 1) // self.items_per_page
            current_page = self.offset // self.items_per_page + 1
            
            self.add_button("Назад", self.go_back, ButtonStyle.primary, disabled=(current_page == 1))
            self.add_button("Вернуться", self.return_to_manage, ButtonStyle.secondary)
            self.add_button("Вперед", self.go_forward, ButtonStyle.primary, 
                        disabled=(current_page >= max_pages))

        def add_button(self, label: str, callback, style: ButtonStyle, disabled: bool = False):
            button = Button(label=label, style=style, disabled=disabled)
            button.callback = callback
            self.add_item(button)

        async def show_members_list(self, interaction: Interaction, offset: int):
            members_to_display = self.members[offset:offset + self.items_per_page]
            current_page = (offset // self.items_per_page) + 1
            total_pages = (len(self.members) + self.items_per_page - 1) // self.items_per_page

            embed = Embed(
                title=f"Участники комнаты {self.parent_view.room_name}",
                color=0x6e6e6e
            )

            member_list = []
            for index, member in enumerate(members_to_display, start=offset + 1):
                member_list.append(f"**{index}.** {member.mention}")

            embed.description = "\n".join(member_list)
            embed.set_footer(text=f"Страница {current_page}/{total_pages}")

            self.offset = offset
            self.update_buttons()
            await interaction.response.edit_message(embed=embed, view=self)

        async def go_back(self, interaction: Interaction):
            if interaction.user.id != self.interaction.user.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            new_offset = max(self.offset - self.items_per_page, 0)
            await self.show_members_list(interaction, new_offset)

        async def go_forward(self, interaction: Interaction):
            if interaction.user.id != self.interaction.user.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return
            
            max_offset = ((len(self.members) - 1) // self.items_per_page) * self.items_per_page
            new_offset = min(self.offset + self.items_per_page, max_offset)
            await self.show_members_list(interaction, new_offset)

        async def return_to_manage(self, interaction: Interaction):
            if interaction.user.id != self.interaction.user.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            embed = Embed(color=0x6e6e6e)
            embed.set_author(name=f"Управление комнатой - {self.parent_view.owner.display_name}",
                            icon_url=self.parent_view.owner.avatar.url)
            embed.add_field(name="<:mice:1526013753110433872> Комната", value=self.parent_view.room_name, inline=True)
            embed.add_field(name="<:eshepeople:1526013744314843222> Участников", value=str(self.parent_view.member_count), inline=True)

            await interaction.response.edit_message(embed=embed, view=self.parent_view)

    class InviteButton(Button):
        def __init__(self, owner_role_id, parent_view):
            super().__init__(label="Пригласить", style=ButtonStyle.secondary, emoji="<:checkmark:1526013748718993428>", row=0)
            self.owner_role_id = owner_role_id
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return

            embed = Embed(
                description=(
                    f"Выберите участников, которых хотите пригласить в комнату **{self.parent_view.room_name}**.\n"
                    f"-# Можно выбрать до **10** человек одновременно."
                ),
                color=0x6e6e6e
            )
            embed.set_author(name="Приглашение участников", icon_url=interaction.user.display_avatar.url)

            view = InviteSelectView(self.owner_role_id, self.parent_view, interaction.user)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.owner_role_id for role in interaction.user.roles)

    class RemoveButton(Button):
        def __init__(self, owner_role_id, parent_view):
            super().__init__(label="Исключить", style=ButtonStyle.secondary, emoji="<:xrestik:1526013747112448090>", row=0)
            self.owner_role_id = owner_role_id
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return

            embed = Embed(
                description=(
                    f"Выберите участников, которых хотите исключить из комнаты **{self.parent_view.room_name}**.\n"
                    f"-# Можно выбрать до **10** человек одновременно. Будут исключены только те, кто состоит в комнате."
                ),
                color=0x6e6e6e
            )
            embed.set_author(name="Исключение участников", icon_url=interaction.user.display_avatar.url)

            view = RemoveSelectView(self.owner_role_id, self.parent_view, interaction.user)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.owner_role_id for role in interaction.user.roles)

    class OpenChannelButton(Button):
        def __init__(self, owner_role_id, owner, room_name, member_count, voice_channel, original_message):
            super().__init__(label="Закрыта", style=ButtonStyle.secondary, emoji="<:galo4ka:1526013745254629470>", row=1)
            self.owner_role_id = owner_role_id
            self.owner = owner
            self.room_name = room_name
            self.member_count = member_count
            self.voice_channel = voice_channel
            self.original_message = original_message

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return

            if self.voice_channel:
                await self.voice_channel.set_permissions(interaction.guild.default_role, connect=True)
                
                temp_view = View()
                temp_view.original_message = self.original_message
                
                new_view = ManageRoomView(
                    owner_role_id=self.owner_role_id,
                    owner=self.owner,
                    room_name=self.room_name,
                    member_count=self.member_count,
                    voice_channel=self.voice_channel,
                    is_channel_open=True,
                    interaction=interaction,
                    original_view=temp_view
                )
                
                await interaction.response.edit_message(embed=self.create_embed(), view=new_view)
            else:
                await interaction.response.send_message(embed=Embed(description="Голосовой канал не найден.", color=0xFF0000), ephemeral=True)

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.owner_role_id for role in interaction.user.roles)

        def create_embed(self):
            return Embed(color=0x212121).set_author(
                name=f"Управление комнатой - {self.owner.display_name}",
                icon_url=self.owner.avatar.url
            ).add_field(
                name="<:mice:1526013753110433872> Комната",
                value=self.room_name,
                inline=True
            ).add_field(
                name="<:people:1526013751457874033> Участников",
                value=str(self.member_count),
                inline=True
            )

    class CloseChannelButton(Button):
        def __init__(self, owner_role_id, owner, room_name, member_count, voice_channel, original_message):
            super().__init__(label="Открыта", style=ButtonStyle.secondary, emoji="<:offff:1526013732591894788>", row=1)
            self.owner_role_id = owner_role_id
            self.owner = owner
            self.room_name = room_name
            self.member_count = member_count
            self.voice_channel = voice_channel
            self.original_message = original_message

        async def callback(self, interaction: Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message(embed=Embed(description="Вы не являетесь владельцем этой комнаты.", color=0xFF0000), ephemeral=True)
                return

            if self.voice_channel:
                await self.voice_channel.set_permissions(interaction.guild.default_role, connect=False)
                
                temp_view = View()
                temp_view.original_message = self.original_message
                
                new_view = ManageRoomView(
                    owner_role_id=self.owner_role_id,
                    owner=self.owner,
                    room_name=self.room_name,
                    member_count=self.member_count,
                    voice_channel=self.voice_channel,
                    is_channel_open=False,
                    interaction=interaction,
                    original_view=temp_view
                )
                
                await interaction.response.edit_message(embed=self.create_embed(), view=new_view)
            else:
                await interaction.response.send_message(embed=Embed(description="Голосовой канал не найден.", color=0xFF0000), ephemeral=True)

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.owner_role_id for role in interaction.user.roles)

        def create_embed(self):
            return Embed(color=0x212121).set_author(
                name=f"Управление комнатой - {self.owner.display_name}",
                icon_url=self.owner.avatar.url
            ).add_field(
                name="<:mice:1526013753110433872> Комната",
                value=self.room_name,
                inline=True
            ).add_field(
                name="<:people:1526013751457874033> Участников",
                value=str(self.member_count),
                inline=True
            )

        def create_new_view(self, is_channel_open, interaction):
            return ManageRoomView(self.owner_role_id, self.owner, self.room_name, self.member_count, self.voice_channel, is_channel_open, interaction)

    # === Массовое приглашение через нативный UserSelect (выпадающий список с поиском) ===
    class InviteSelectView(View):
        def __init__(self, owner_role_id, parent_view, owner):
            super().__init__(timeout=60)
            self.owner_role_id = owner_role_id
            self.parent_view = parent_view
            self.owner = owner
            self.add_item(InviteUserSelect(self))

    class InviteUserSelect(discord.ui.UserSelect):
        def __init__(self, select_owner_view: "InviteSelectView"):
            super().__init__(
                placeholder="Выберите участников (до 10)",
                min_values=1,
                max_values=10
            )
            self.select_owner_view = select_owner_view

        async def callback(self, interaction: Interaction):
            guild = interaction.guild
            parent_view = self.select_owner_view.parent_view
            owner = self.select_owner_view.owner
            role = guild.get_role(self.select_owner_view.owner_role_id)

            if not role:
                await interaction.response.edit_message(
                    embed=Embed(description="Роль комнаты не найдена.", color=0xFF0000),
                    view=None
                )
                return

            await interaction.response.defer(ephemeral=True)

            pending_set = pending_invites.setdefault(role.id, set())
            available_slots = MAX_PENDING_INVITES - len(pending_set)

            invited = []
            skipped = []

            for user in self.values:
                member = user if isinstance(user, discord.Member) else guild.get_member(user.id)

                if member is None:
                    skipped.append(f"{user.mention} — не найден на сервере")
                    continue
                if member.bot:
                    skipped.append(f"{member.mention} — бот")
                    continue
                if member.id == owner.id:
                    skipped.append(f"{member.mention} — вы владелец комнаты")
                    continue
                if role in member.roles:
                    skipped.append(f"{member.mention} — уже состоит в комнате")
                    continue
                if member.id in pending_set:
                    skipped.append(f"{member.mention} — уже есть активное приглашение")
                    continue
                if available_slots <= 0:
                    skipped.append(f"{member.mention} — достигнут лимит активных приглашений ({MAX_PENDING_INVITES})")
                    continue

                invite_embed = Embed(
                    title=f"Комната {parent_view.room_name}",
                    description=f"Приглашение пользователя {member.mention} во вступление\n\n-# *20с. на действие*",
                    color=0x6e6e6e
                )
                invite_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)

                invite_view = InviteConfirmView(
                    member=member,
                    owner=owner,
                    role=role,
                    parent_view=parent_view,
                    owner_role_id=self.select_owner_view.owner_role_id,
                    room_name=parent_view.room_name,
                    pending_set=pending_set
                )

                try:
                    msg = await interaction.channel.send(embed=invite_embed, view=invite_view)
                    invite_view.message = msg
                    invited.append(member.mention)
                    pending_set.add(member.id)
                    available_slots -= 1
                except Exception:
                    skipped.append(f"{member.mention} — ошибка отправки приглашения")

            summary = Embed(color=0x6e6e6e)
            summary.set_author(name="Приглашения обработаны", icon_url=owner.display_avatar.url)
            if invited:
                summary.add_field(name="<:checkmark:1526013748718993428> Приглашены", value="\n".join(invited), inline=False)
            if skipped:
                summary.add_field(name="<:xrestik:1526013747112448090> Пропущены", value="\n".join(skipped), inline=False)
            summary.set_footer(text=f"Активных приглашений: {len(pending_set)}/{MAX_PENDING_INVITES}")
            if not invited and not skipped:
                summary.description = "Никто не был выбран."

            await interaction.edit_original_response(embed=summary, view=None)

    # === Массовое исключение через нативный UserSelect (выпадающий список с поиском) ===
    class RemoveSelectView(View):
        def __init__(self, owner_role_id, parent_view, owner):
            super().__init__(timeout=60)
            self.owner_role_id = owner_role_id
            self.parent_view = parent_view
            self.owner = owner
            self.add_item(RemoveUserSelect(self))

    class RemoveUserSelect(discord.ui.UserSelect):
        def __init__(self, select_owner_view: "RemoveSelectView"):
            super().__init__(
                placeholder="Выберите участников (до 10)",
                min_values=1,
                max_values=10
            )
            self.select_owner_view = select_owner_view

        async def callback(self, interaction: Interaction):
            guild = interaction.guild
            parent_view = self.select_owner_view.parent_view
            owner = self.select_owner_view.owner
            role = guild.get_role(self.select_owner_view.owner_role_id)

            if not role:
                await interaction.response.edit_message(
                    embed=Embed(description="Роль комнаты не найдена.", color=0xFF0000),
                    view=None
                )
                return

            await interaction.response.defer(ephemeral=True)

            removed = []
            skipped = []

            for user in self.values:
                member = user if isinstance(user, discord.Member) else guild.get_member(user.id)

                if member is None:
                    skipped.append(f"{user.mention} — не найден на сервере")
                    continue
                if member.id == owner.id:
                    skipped.append(f"{member.mention} — вы владелец комнаты")
                    continue
                if role not in member.roles:
                    skipped.append(f"{member.mention} — не состоит в комнате")
                    continue

                try:
                    await member.remove_roles(role)
                    removed.append(member.mention)
                    parent_view.member_count = max(0, parent_view.member_count - 1)
                except Exception:
                    skipped.append(f"{member.mention} — ошибка исключения")

            if removed:
                await update_manage_message(parent_view)

            summary = Embed(color=0x6e6e6e)
            summary.set_author(name="Исключение обработано", icon_url=owner.display_avatar.url)
            if removed:
                summary.add_field(name="<:checkmark:1526013748718993428> Исключены", value="\n".join(removed), inline=False)
            if skipped:
                summary.add_field(name="<:xrestik:1526013747112448090> Пропущены", value="\n".join(skipped), inline=False)
            if not removed and not skipped:
                summary.description = "Никто не был выбран."

            await interaction.edit_original_response(embed=summary, view=None)

    class InviteConfirmView(View):
        def __init__(self, member, owner, role, parent_view, owner_role_id, room_name, pending_set=None):
            super().__init__(timeout=20)
            self.member = member
            self.owner = owner
            self.role = role
            self.parent_view = parent_view
            self.owner_role_id = owner_role_id
            self.room_name = room_name
            self.message = None
            self.pending_set = pending_set

        async def on_timeout(self):
            # Освобождаем слот активного приглашения
            if self.pending_set is not None:
                self.pending_set.discard(self.member.id)

            # Удаляем сообщение после таймаута
            if self.message:
                try:
                    await self.message.delete()
                except:
                    pass

        @discord.ui.button(label="Да", style=ButtonStyle.success, emoji="<:checkmark:1526013748718993428>")
        async def accept_button(self, interaction: Interaction, button: Button):
            # Только приглашенный пользователь может принять
            if interaction.user.id != self.member.id:
                await interaction.response.send_message(
                    embed=Embed(description="Только приглашенный пользователь может принять приглашение.", color=0xFF0000),
                    ephemeral=True
                )
                return

            # Освобождаем слот активного приглашения
            if self.pending_set is not None:
                self.pending_set.discard(self.member.id)

            # Выдаем роль
            await self.member.add_roles(self.role)
            
            # Обновляем счетчик участников
            self.parent_view.member_count += 1
            await self.update_main_message()
            
            # Отправляем подтверждение
            await interaction.response.send_message(
                embed=Embed(description="", color=0x6e6e6e)
                .set_author(name=f"Вы вступили в комнату - {self.room_name}", icon_url=self.member.avatar.url if self.member.avatar else self.member.default_avatar.url),
                ephemeral=True
            )
            
            # Удаляем сообщение с приглашением
            try:
                await interaction.message.delete()
            except:
                pass

        @discord.ui.button(label="Нет", style=ButtonStyle.danger, emoji="<:xrestik:1526013747112448090>")
        async def decline_button(self, interaction: Interaction, button: Button):
            # Приглашенный пользователь или владелец комнаты могут отклонить
            if interaction.user.id != self.member.id and interaction.user.id != self.owner.id:
                await interaction.response.send_message(
                    embed=Embed(description="У вас нет прав на это действие.", color=0xFF0000),
                    ephemeral=True
                )
                return

            # Останавливаем таймер
            self.stop()

            # Освобождаем слот активного приглашения
            if self.pending_set is not None:
                self.pending_set.discard(self.member.id)

            # Определяем кто отклонил
            if interaction.user.id == self.owner.id:
                decline_embed = Embed(
                    title=f"Комната {self.room_name}",
                    description=f"~~Приглашение пользователя {self.member.mention} во вступление~~\n\n-# *Владелец отменил приглашение*",
                    color=0x6e6e6e
                )
            else:
                decline_embed = Embed(
                    title=f"Комната {self.room_name}",
                    description=f"~~Приглашение пользователя {self.member.mention} во вступление~~\n\n-# *Приглашение отклонено*",
                    color=0x6e6e6e
                )
            
            decline_embed.set_thumbnail(url=self.member.avatar.url if self.member.avatar else self.member.default_avatar.url)
            await interaction.response.edit_message(embed=decline_embed, view=None)
            
            # Удаляем сообщение через 3 секунды
            await asyncio.sleep(3)
            try:
                await interaction.message.delete()
            except:
                pass

        async def update_main_message(self):
            if self.parent_view.original_message is None:
                return

            embed = Embed(color=0x6e6e6e)
            embed.set_author(name=f"Управление комнатой - {self.parent_view.owner.display_name}", icon_url=self.parent_view.owner.avatar.url)
            embed.add_field(name="<:mice:1526013753110433872> Комната", value=self.parent_view.room_name, inline=True)
            embed.add_field(name="<:people:1526013751457874033> Участников", value=str(self.parent_view.member_count), inline=True)

            try:
                await self.parent_view.original_message.edit(embed=embed, view=self.parent_view)
            except (discord.NotFound, discord.HTTPException):
                # См. комментарий в update_manage_message — панель ephemeral,
                # редактирование доступно только ~15 минут после /room manage.
                pass

    # ============================================
    # /room extend — ПАНЕЛЬ УЧАСТНИКА КОМНАТЫ
    # ============================================

    async def build_member_room_embed(leader_id, room_name):
        await cursor.execute(
            'SELECT creation_date, expiration_date, room_balance FROM room_leadership WHERE leader_id = $1',
            leader_id
        )
        row = cursor.fetchone()
        creation_date, expiration_date, room_balance = row if row else (None, None, 0)

        if expiration_date:
            try:
                expiration = datetime.strptime(expiration_date, ROOM_DATE_FORMAT)
                days_left_str = format_room_timedelta(expiration - datetime.now())
            except ValueError:
                days_left_str = "Ошибка в дате"
        else:
            days_left_str = "Не указано"

        embed = Embed(title=f"Комната {room_name}", color=0x6e6e6e)
        embed.add_field(name="<:data:1337141473162039337> Дата создания", value=creation_date or "Не указано", inline=True)
        embed.add_field(name="<:watchw:1337130049123389500> Дата истечения", value=expiration_date or "Не указано", inline=True)
        embed.add_field(name="<:vremya:1337141252151447555> Осталось дней", value=days_left_str, inline=True)
        embed.add_field(name="<a:coinonrole:1298391257042784266> Банк комнаты", value=f"{room_balance or 0} монет", inline=True)
        embed.set_footer(text=f"Продление: {ROOM_EXTEND_DAY_COST} монет/день")
        return embed

    class MemberRoomView(View):
        def __init__(self, leader_id, room_name):
            super().__init__(timeout=120)
            self.leader_id = leader_id
            self.room_name = room_name

            extend_button = Button(label="Продлить", style=ButtonStyle.secondary, emoji="<:beskone4:1337141486512242868>")
            extend_button.callback = self.extend_callback
            self.add_item(extend_button)

            bank_button = Button(label="В банк", style=ButtonStyle.secondary, emoji="<a:coinonrole:1298391257042784266>")
            bank_button.callback = self.bank_callback
            self.add_item(bank_button)

        async def refresh(self, interaction: Interaction):
            embed = await build_member_room_embed(self.leader_id, self.room_name)
            if interaction.message is not None:
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)

        async def extend_callback(self, interaction: Interaction):
            await interaction.response.send_modal(ExtendRoomModal(self.leader_id, refresh_view=self))

        async def bank_callback(self, interaction: Interaction):
            await interaction.response.send_modal(RoomBankDepositModal(self.leader_id, refresh_view=self))

    class RoomMemberSelectView(View):
        """Список комнат, в которых пользователь состоит (когда их 2 и более) — выбор нужной."""

        def __init__(self, rooms: list):
            super().__init__(timeout=60)
            self.add_item(RoomMemberSelect(rooms))

    class RoomMemberSelect(Select):
        def __init__(self, rooms: list):
            options = [
                discord.SelectOption(label=room_name, value=str(leader_id))
                for leader_id, room_name, role_id in rooms[:25]
            ]
            super().__init__(placeholder="Выберите комнату", min_values=1, max_values=1, options=options)
            self.rooms_by_leader = {str(leader_id): room_name for leader_id, room_name, role_id in rooms}

        async def callback(self, interaction: Interaction):
            leader_id = int(self.values[0])
            room_name = self.rooms_by_leader[self.values[0]]

            embed = await build_member_room_embed(leader_id, room_name)
            view = MemberRoomView(leader_id, room_name)
            await interaction.response.edit_message(embed=embed, view=view)

    @room_group.command(name="extend", description="Продлить или пополнить банк комнаты, в которой вы участник")
    async def room_extend(interaction: discord.Interaction):
        user = interaction.user
        user_role_ids = [role.id for role in user.roles]

        if not user_role_ids:
            await interaction.response.send_message(
                embed=Embed(description="У вас нет ни одной комнаты, в которой вы участник.", color=0xFF0000),
                ephemeral=True
            )
            return

        await cursor.execute('SELECT leader_id, room_name, role_id FROM room_leadership WHERE role_id = ANY($1)', user_role_ids)
        rows = cursor.fetchall()

        if not rows:
            await interaction.response.send_message(
                embed=Embed(description="У вас нет ни одной комнаты, в которой вы участник.", color=0xFF0000),
                ephemeral=True
            )
            return

        if len(rows) == 1:
            leader_id, room_name, role_id = rows[0]
            embed = await build_member_room_embed(leader_id, room_name)
            view = MemberRoomView(leader_id, room_name)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        embed = Embed(description="Вы состоите в нескольких комнатах. Выберите нужную:", color=0x6e6e6e)
        view = RoomMemberSelectView(rows)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    bot.tree.add_command(room_group)
