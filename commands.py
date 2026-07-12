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
from discord.ext import commands
import time
import random


# Создаем объект intents и устанавливаем нужные параметры
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Разрешает просмотр и обработку содержимого сообщений

HEX_COLOR_REGEX = re.compile(r'^#[0-9A-Fa-f]{6}$')

# Проверка на наличие у пользователя роли администратора
def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator

def setup_commands(bot, cursor, CATEGORY_ID, conn, restricted_role_id):
    room_group = app_commands.Group(name="room", description="Управление комнатами")
    POSITION_UNDER_ROLE_ID = 1295482170374095049

    async def update_manage_message(parent_view):
        """Обновляет основное сообщение управления комнатой (счетчик участников и т.д.)"""
        if parent_view.original_message is None:
            return

        embed = Embed(color=0x6e6e6e)
        embed.set_author(name=f"Управление комнатой - {parent_view.owner.display_name}", icon_url=parent_view.owner.display_avatar.url)
        embed.add_field(name="<:voice:1337103709150248992> Комната", value=parent_view.room_name, inline=True)
        embed.add_field(name="<:people:1337103698568020091> Участников", value=str(parent_view.member_count), inline=True)

        await parent_view.original_message.edit(embed=embed, view=parent_view)

    # owner_role_id -> set(member_id) с активным (ещё не принятым/не отклонённым/не истекшим) приглашением
    MAX_PENDING_INVITES = 10
    pending_invites: dict = {}

    @room_group.command(name="create", description="Создание приватной комнаты [Только для Администрации]")
    @app_commands.describe(
        участник="Участник, которому будет принадлежать комната",
        комната="Название комнаты",
        роль="Название роли",
        цвет="Цвет роли в HEX формате (например, #000000)"
    )
    @app_commands.check(is_admin)
    async def createroom(interaction: discord.Interaction, участник: discord.User, комната: str, роль: str, цвет: str):
        guild = interaction.guild
        category = guild.get_channel(CATEGORY_ID)

        # Проверка категории
        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                embed=Embed(description="Указанная категория не найдена.", color=0xFF0000),
                ephemeral=True
            )
            return

        # Валидация HEX цвета
        if not HEX_COLOR_REGEX.match(цвет):
            await interaction.response.send_message(
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
            await interaction.response.send_message(
                embed=Embed(
                    description=f"Имя комнаты '{комната}' уже занято. Выберите другое имя.",
                    color=0xFF0000
                ),
                ephemeral=True
            )
            return

        # Проверка существующей комнаты у пользователя
        await cursor.execute('SELECT room_name FROM room_leadership WHERE leader_id = $1', участник.id)
        if existing_room := cursor.fetchone():
            await interaction.response.send_message(
                embed=Embed(
                    description=f"{участник.mention} уже владеет комнатой '{existing_room[0]}'!",
                    color=0xFFA500
                ),
                ephemeral=True
            )
            return

        # Создание первоначального embed
        success_embed = Embed(
            description="Ожидайте создание комнаты",
            color=0x6e6e6e
        )
        success_embed.set_author(name=участник.display_name, icon_url=участник.display_avatar.url)
        success_embed.add_field(name="Название", value=комната, inline=True)
        success_embed.add_field(name="Роль", value=роль, inline=True)
        success_embed.add_field(name="Дата создания", value=datetime.now().strftime('%d.%m.%Y'), inline=True)
        
        await interaction.response.send_message(embed=success_embed, ephemeral=True)

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

        # Сохранение в базе данных
        await cursor.execute('''
    INSERT INTO room_leadership (
        leader_id, 
        room_name, 
        role_id, 
        text_channel_id, 
        voice_channel_id, 
        creation_date
    ) VALUES ($1, $2, $3, $4, $5, $6)
''', участник.id, комната, role.id, text_channel.id, voice_channel.id, datetime.now().strftime('%d.%m.%Y'))

        # Выдача роли пользователю
        await участник.add_roles(role)
        await interaction.followup.send("Комната успешно создана!", ephemeral=True)

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
        # Обновленный запрос для получения даты создания
        await cursor.execute('SELECT room_name, leader_id, creation_date FROM room_leadership LIMIT 5 OFFSET $1', offset)
        rooms = cursor.fetchall()

        if not rooms:
            embed = Embed(description="<a:print:1337103792491200553> На данный момент комнат не обнаружено", color=0x000000)
            if new_message:
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.edit_original_response(embed=embed, view=None)
            return

        await cursor.execute('SELECT COUNT(*) FROM room_leadership')
        total_rooms = cursor.fetchone()[0]

        total_pages = (total_rooms + 4) // 5  # Чтобы округлить вверх

        embed = Embed(title=f"Список комнат ({total_rooms})", color=0x39393c)
        for index, (room_name, leader_id, creation_date) in enumerate(rooms, start=1 + offset):
            leader = await interaction.guild.fetch_member(leader_id)
            leader_name = leader.display_name if leader else f"ID: **{leader_id}**"
            # Форматирование имени комнаты с датой справа
            formatted_room_name = f"{room_name} `[{creation_date}]`"
            embed.add_field(
                name=f"{index}) {formatted_room_name}",
                value=f"Владелец: {leader_name}",
                inline=False
            )

        page_number = (offset // 5) + 1
        embed.set_footer(text=f"Страница {page_number} из {total_pages}")

        view = RoomListView(offset, total_rooms, total_pages)
        if new_message:
            await interaction.response.send_message(embed=embed, view=view)
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
                SELECT room_name, leader_id, role_id, text_channel_id, voice_channel_id, creation_date 
                FROM room_leadership 
                WHERE leader_id = $1 OR text_channel_id = $2 OR voice_channel_id = $3
            ''', search_id, search_id, search_id)
        except ValueError:
            # Если не удалось преобразовать в число, ищем по имени комнаты
            await cursor.execute('''
                SELECT room_name, leader_id, role_id, text_channel_id, voice_channel_id, creation_date 
                FROM room_leadership 
                WHERE room_name = $1
            ''', комната)

        room = cursor.fetchone()
        
        if not room:
            embed = discord.Embed(
                description="<:xxx:1299081147917008938> Комната не найдена.",
                color=0x6e6e6e
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        room_name, leader_id, role_id, text_channel_id, voice_channel_id, creation_date = room

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
        embed.add_field(name="<:13371memberwhite:1337148842755493958> Владелец", value=leader_name, inline=True)
        embed.add_field(name="<:datasozdaniya:1337149528356159498> Дата создания", value=creation_date, inline=True)
        embed.add_field(name="<a:diamond:1302038845491118204> Роль", value=role_name, inline=True)
        embed.add_field(name="<:10447information:1337148819879628850> HEX-код роли", value=role_hex, inline=True)
        embed.add_field(name="<:voice:1337103709150248992> Войс", value=voice_channel_mention, inline=True)
        embed.add_field(name="<:textss:1337149867365105688> Текстовой", value=text_channel_mention, inline=True)
        embed.add_field(name="<:ludi:1337149186856194112> Участников", value=str(member_count), inline=True)

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

    # @room_group.command(name="search", description="Поиск владельца комнаты")
    # @app_commands.describe(
    #     параметр="ID голосовой/текстовой комнаты, либо название комнаты"
    # )
    # async def wholeader(interaction: discord.Interaction, параметр: str):
    #     try:
    #         guild = interaction.guild
    #         user = interaction.user
    #         room_name = None
    #         channel_id = None

    #         # Проверка на пустой параметр
    #         if not параметр:
    #             error_embed = discord.Embed(
    #                 description="<:xxx:1299081147917008938> Пожалуйста, укажите ID комнаты или её название.",
    #                 color=0x6e6e6e
    #             )
    #             await interaction.response.send_message(embed=error_embed, ephemeral=True)
    #             return

    #         # Попытка преобразовать параметр в ID канала
    #         try:
    #             channel_id = int(параметр)
    #         except ValueError:
    #             channel_id = None

    #         # Выполнение SQL-запроса в зависимости от типа параметра
    #         if channel_id is not None:
    #             cursor.execute('''
    #                 SELECT leader_id, room_name 
    #                 FROM room_leadership 
    #                 WHERE text_channel_id = ? OR voice_channel_id = ?
    #             ''', (channel_id, channel_id))
    #         else:
    #             cursor.execute('''
    #                 SELECT leader_id, room_name 
    #                 FROM room_leadership 
    #                 WHERE room_name = ?
    #             ''', (параметр,))

    #         result = cursor.fetchone()

    #         # Если комната не найдена
    #         if result is None:
    #             error_embed = discord.Embed(
    #                 description="<:xxx:1299081147917008938> Комната не найдена. Проверьте правильность введенных данных.",
    #                 color=0x6e6e6e
    #             )
    #             await interaction.response.send_message(embed=error_embed, ephemeral=True)
    #             return

    #         # Данные из результата запроса
    #         leader_id, room_name = result
    #         leader = guild.get_member(leader_id)

    #         # Формируем embed сообщение
    #         embed = discord.Embed(color=0x6e6e6e)
            
    #         # Проверка наличия аватара у пользователя
    #         avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
            
    #         # Устанавливаем author с именем и иконкой вызывающего пользователя
    #         embed.set_author(name=f"Поиск комнаты - {user.name}", icon_url=avatar_url)
            
    #         # Добавляем поля с информацией
    #         embed.add_field(
    #             name="Идентификатор", 
    #             value=f"`{параметр}`", 
    #             inline=True
    #         )
            
    #         if leader:
    #             embed.add_field(
    #                 name="Владелец", 
    #                 value=leader.mention, 
    #                 inline=True
    #             )
    #         else:
    #             embed.add_field(
    #                 name="Владелец", 
    #                 value=f"Пользователь с ID `{leader_id}`", 
    #                 inline=True
    #             )
                
    #         embed.add_field(
    #             name="Комната", 
    #             value=f"**{room_name}**", 
    #             inline=True
    #         )
            
    #         # Отправляем embed
    #         await interaction.response.send_message(embed=embed)

    #     except Exception as e:
    #         # Обработка непредвиденных ошибок
    #         error_embed = discord.Embed(
    #             description="<:xxx:1299081147917008938> Произошла ошибка при выполнении команды. Попробуйте позже.",
    #             color=0x6e6e6e
    #         )
    #         await interaction.response.send_message(embed=error_embed, ephemeral=True)
    #         print(f"Error in wholeader command: {str(e)}")  # Логирование ошибки

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

        embed = Embed(color=0x6e6e6e)
        embed.set_author(name=f"Управление комнатой - {user.display_name}", icon_url=user.avatar.url)
        embed.add_field(name="<:voice:1337103709150248992> Комната", value=room_name, inline=True)
        embed.add_field(name="<:people:1337103698568020091> Участников", value=str(member_count), inline=True)
        embed.set_footer(text=f"Дата создания: {creation_date}")

        view = InitialView(
            owner_role_id=role_id,
            owner=user,
            room_name=room_name,
            member_count=member_count,
            voice_channel=voice_channel,
            is_channel_open=is_channel_open,
            interaction=interaction
        )

        await interaction.response.send_message(embed=embed, view=view)
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

    class ManageButton(Button):
        def __init__(self, parent_view):
            super().__init__(label="Управлять", style=ButtonStyle.secondary, emoji="<:customprof:1337103673649664072>")
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
            super().__init__(label="Назад", style=ButtonStyle.secondary, emoji="<:61991right:1337148887299002371>")
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

            embed = Embed(color=0x6e6e6e)
            embed.set_author(name=f"Управление комнатой - {self.parent_view.owner.display_name}",
                            icon_url=self.parent_view.owner.avatar.url)
            embed.add_field(name="<:voice:1337103709150248992> Комната", value=self.parent_view.room_name, inline=True)
            embed.add_field(name="<:people:1337103698568020091> Участников", value=str(self.parent_view.member_count), inline=True)

            await interaction.response.edit_message(embed=embed, view=new_view)
            new_view.original_message = await interaction.original_response()

        def is_owner(self, interaction: Interaction):
            return any(role.id == self.parent_view.owner_role_id for role in interaction.user.roles)

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
            super().__init__(label="Участники", style=ButtonStyle.secondary, emoji="<:91221members:1337148934992429137>")
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
            embed.add_field(name="<:voice:1337103709150248992> Комната", value=self.parent_view.room_name, inline=True)
            embed.add_field(name="<:91221members:1337148934992429137> Участников", value=str(self.parent_view.member_count), inline=True)

            await interaction.response.edit_message(embed=embed, view=self.parent_view)

    class InviteButton(Button):
        def __init__(self, owner_role_id, parent_view):
            super().__init__(label="Пригласить", style=ButtonStyle.secondary, emoji="<:checkmark:1299081136013709352>")
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
            super().__init__(label="Исключить", style=ButtonStyle.secondary, emoji="<:xxx:1299081147917008938>")
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
            super().__init__(label="Закрыта", style=ButtonStyle.secondary, emoji="<:turnon:1337103564715200572>")
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
                name="<:voice:1337103709150248992> Комната",
                value=self.room_name,
                inline=True
            ).add_field(
                name="<:people:1337103698568020091> Участников",
                value=str(self.member_count),
                inline=True
            )

    class CloseChannelButton(Button):
        def __init__(self, owner_role_id, owner, room_name, member_count, voice_channel, original_message):
            super().__init__(label="Открыта", style=ButtonStyle.secondary, emoji="<:turnoff:1337103551255543839>")
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
                name="<:voice:1337103709150248992> Комната",
                value=self.room_name,
                inline=True
            ).add_field(
                name="<:people:1337103698568020091> Участников",
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
                summary.add_field(name="<:checkmark:1299081136013709352> Приглашены", value="\n".join(invited), inline=False)
            if skipped:
                summary.add_field(name="<:xxx:1299081147917008938> Пропущены", value="\n".join(skipped), inline=False)
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
                summary.add_field(name="<:checkmark:1299081136013709352> Исключены", value="\n".join(removed), inline=False)
            if skipped:
                summary.add_field(name="<:xxx:1299081147917008938> Пропущены", value="\n".join(skipped), inline=False)
            if not removed and not skipped:
                summary.description = "Никто не был выбран."

            await interaction.edit_original_response(embed=summary, view=None)

    # === НАЧАЛО ДОБАВЛЕНИЯ НОВОГО КЛАССА ===
    # === НАЧАЛО ДОБАВЛЕНИЯ НОВОГО КЛАССА ===
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

        @discord.ui.button(label="Да", style=ButtonStyle.success, emoji="<:checkmark:1299081136013709352>")
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

        @discord.ui.button(label="Нет", style=ButtonStyle.danger, emoji="<:xxx:1299081147917008938>")
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
            embed.add_field(name="<:voice:1337103709150248992> Комната", value=self.parent_view.room_name, inline=True)
            embed.add_field(name="<:people:1337103698568020091> Участников", value=str(self.parent_view.member_count), inline=True)

            await self.parent_view.original_message.edit(embed=embed, view=self.parent_view)

    bot.tree.add_command(room_group)
