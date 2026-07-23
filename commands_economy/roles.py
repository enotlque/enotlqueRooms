import discord
from discord import app_commands, Interaction, Embed, utils, ButtonStyle
from discord import TextStyle
from discord.ext import tasks
import random
import discord.ui
from discord.ui import View, Button, Modal, TextInput
from discord import ui
from discord.utils import get
from datetime import datetime, timedelta
from functools import wraps
from typing import List, Tuple, Dict, Set
import re
import logging
import traceback
import asyncio
import time
import os
import io
from PIL import Image, ImageDraw, ImageFont
from cache import get_cached, set_cached, delete_cached, balance_cache_key, profile_cache_key, top_cache_key
from commands_profile import create_profile_image, get_active_role_names, get_member_room_options

from . import common
from .common import create_embed, format_timedelta, get_user_balance, subtract_user_balance

import discord
from discord import app_commands, Interaction, Embed, utils, ButtonStyle
from discord import TextStyle
from discord.ext import tasks
import random
import discord.ui
from discord.ui import View, Button, Modal, TextInput
from discord import ui
from discord.utils import get
from datetime import datetime, timedelta
from functools import wraps
from typing import List, Tuple, Dict, Set
import re
import logging
import traceback
import asyncio
import time
import os
import io
from PIL import Image, ImageDraw, ImageFont
from cache import get_cached, set_cached, delete_cached, balance_cache_key, profile_cache_key, top_cache_key
from commands_profile import create_profile_image, get_active_role_names, get_member_room_options

from . import common
from .common import create_embed, format_timedelta, get_user_balance, subtract_user_balance

# ============================================
# АВТОУДАЛЕНИЕ ИСТЁКШИХ РОЛЕЙ
# ============================================

_role_task_started = False

def start_role_expiry_task(bot):
    """Запускает фоновую проверку истёкших кастомных ролей. Безопасно вызывать повторно — стартует только один раз."""
    global _role_task_started
    if _role_task_started:
        return
    _role_task_started = True

    @tasks.loop(hours=1)
    async def check_role_expirations():
        cursor = common.cursor
        try:
            await cursor.execute("SELECT role_name, expiration_date, archived, id_owner_now FROM roles")
            rows = cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка при чтении ролей для автопроверки: {e}")
            return

        now = datetime.now()

        for role_name, expiration_date, archived, id_owner_now in rows:
            # Архивированные роли не истекают — их отсчёт заморожен вручную владельцем
            if archived or not expiration_date or expiration_date == '-':
                continue

            try:
                expiration = datetime.strptime(expiration_date, "%d.%m.%Y в %Hч %Mм %Sс")
            except (TypeError, ValueError):
                continue

            if expiration > now:
                continue  # Роль ещё активна

            # Ищем реальный объект роли на сервере
            discord_role = None
            for guild in bot.guilds:
                discord_role = get(guild.roles, name=role_name)
                if discord_role:
                    break

            if discord_role:
                try:
                    await discord_role.delete(reason="Автоматическое удаление: истёк срок действия роли")
                except Exception as e:
                    print(f"❌ Не удалось удалить роль '{role_name}' на сервере: {e}")

            try:
                await cursor.execute("DELETE FROM roles WHERE role_name = $1", role_name)
                print(f"🗑️ Роль '{role_name}' автоматически удалена: истёк срок действия")
            except Exception as e:
                print(f"❌ Ошибка удаления роли '{role_name}' из БД: {e}")
                continue

            if id_owner_now:
                owner = bot.get_user(id_owner_now)
                if owner:
                    try:
                        await owner.send(embed=discord.Embed(
                            description=(
                                f"Ваша роль **{role_name}** была автоматически удалена: "
                                "истёк срок действия, и она не была продлена или заархивирована вовремя."
                            ),
                            color=0x6e6e6e
                        ))
                    except Exception:
                        pass  # Пользователь закрыл личные сообщения — не критично

    @check_role_expirations.before_loop
    async def before_check():
        await bot.wait_until_ready()

    check_role_expirations.start()

# ============================================
# ОЧИСТКА БД ПРИ РУЧНОМ УДАЛЕНИИ РОЛИ
# ============================================

_role_delete_listener_started = False

def setup_role_delete_listener(bot):
    """Слушает удаление роли на сервере (в т.ч. вручную через настройки Discord)
    и убирает её запись из БД, если это была кастомная роль системы экономики.
    Безопасно вызывать повторно — регистрируется только один раз."""
    global _role_delete_listener_started
    if _role_delete_listener_started:
        return
    _role_delete_listener_started = True

    async def on_guild_role_delete(role: discord.Role):
        cursor = common.cursor
        try:
            result = await cursor.execute("SELECT id_owner_now FROM roles WHERE role_name = $1", role.name)
            row = cursor.fetchone()
        except Exception as e:
            print(f"❌ Ошибка при проверке удалённой роли '{role.name}' в БД: {e}")
            return

        if not row:
            return  # роль не относится к нашей системе кастомных ролей

        id_owner_now = row[0]

        try:
            await cursor.execute("DELETE FROM roles WHERE role_name = $1", role.name)
            print(f"🗑️ Роль '{role.name}' удалена вручную на сервере — запись в БД удалена")
        except Exception as e:
            print(f"❌ Ошибка удаления роли '{role.name}' из БД после ручного удаления: {e}")
            return

        if id_owner_now:
            owner = role.guild.get_member(id_owner_now) or bot.get_user(id_owner_now)
            if owner:
                try:
                    await owner.send(embed=discord.Embed(
                        description=(
                            f"Ваша роль **{role.name}** была удалена вручную на сервере, "
                            "и запись о ней удалена из базы данных."
                        ),
                        color=0x6e6e6e
                    ))
                except Exception:
                    pass  # Пользователь закрыл личные сообщения — не критично

    bot.add_listener(on_guild_role_delete, "on_guild_role_delete")

async def reconcile_deleted_roles(bot):
    """Разовая сверка при старте бота: убирает из БД записи о ролях, которые
    были физически удалены на сервере, пока бот был выключен (в т.ч. заархивированные,
    для которых нет автопроверки истечения). Вызывать один раз в on_ready после init_db()."""
    cursor = common.cursor
    try:
        await cursor.execute("SELECT role_name, id_owner_now FROM roles")
        rows = cursor.fetchall()
    except Exception as e:
        print(f"❌ Ошибка при чтении ролей для сверки при старте: {e}")
        return

    if not rows:
        return

    for role_name, id_owner_now in rows:
        discord_role = None
        target_guild = None
        for guild in bot.guilds:
            discord_role = get(guild.roles, name=role_name)
            if discord_role:
                target_guild = guild
                break

        if discord_role:
            continue  # роль на месте, всё в порядке

        try:
            await cursor.execute("DELETE FROM roles WHERE role_name = $1", role_name)
            print(f"🗑️ Роль '{role_name}' удалена из БД при сверке на старте: физически отсутствует на сервере")
        except Exception as e:
            print(f"❌ Ошибка удаления роли '{role_name}' из БД при сверке на старте: {e}")
            continue

        if id_owner_now:
            owner = None
            for guild in bot.guilds:
                owner = guild.get_member(id_owner_now)
                if owner:
                    break
            owner = owner or bot.get_user(id_owner_now)
            if owner:
                try:
                    await owner.send(embed=discord.Embed(
                        description=(
                            f"Ваша роль **{role_name}** была удалена вручную на сервере, "
                            "и запись о ней удалена из базы данных."
                        ),
                        color=0x6e6e6e
                    ))
                except Exception:
                    pass  # Пользователь закрыл личные сообщения — не критично

# ============================================
# ROLE GROUP
# ============================================

role_group = app_commands.Group(name="role", description="Управление ролями")

async def check_role_exists(guild, role_name):
    cursor = common.cursor
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        await cursor.execute("DELETE FROM roles WHERE role_name = $1", role_name)
        return False
    return True

def role_existence_check(func):
    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        cursor = common.cursor
        guild = interaction.guild
        result = await cursor.execute("SELECT role_name FROM roles")
        roles = cursor.fetchall()
        for (role_name,) in roles:
            await check_role_exists(guild, role_name)
        return await func(interaction, *args, **kwargs)
    return wrapper

POSITION_UNDER_ROLE_ID = 1295482270567632949

@role_group.command(name="create", description="Создать новую роль и выдать её пользователю")
@app_commands.describe(
    название="Название роли",
    цвет="HEX-код роли (6 символов, например, 000FFF)"
)
async def create(interaction: discord.Interaction, название: str, цвет: str):
    cursor = common.cursor
    await interaction.response.defer(ephemeral=True)

    if len(название) > 20:
        error_embed = discord.Embed(
            description="### <:xx:1295095667617960018> Максимальное количество символов в названии: `20`",
            color=discord.Color(int('6e6e6e', 16))
        )
        await interaction.followup.send(embed=error_embed, ephemeral=True)
        return

    result = await cursor.execute("SELECT COUNT(*) FROM roles WHERE id_owner_now = $1", interaction.user.id)
    role_count = cursor.fetchone()[0]
    if role_count >= 2:
        await interaction.followup.send("У вас уже есть максимальное количество ролей (2). Вы не можете создать новую роль.", ephemeral=True)
        return

    if not re.match(r'^[0-9A-Fa-f]{6}$', цвет):
        await interaction.followup.send("Неверный формат HEX-кода. Используйте 6 символов (0-9, A-F).", ephemeral=True)
        return

    result = await cursor.execute("SELECT role_name, hex_code FROM roles WHERE role_name = $1 AND hex_code = $2", название, f"#{цвет}")
    if cursor.fetchone():
        await interaction.followup.send("Роль с таким названием и цветом уже существует. Название и цвет не могут совпадать одновременно.", ephemeral=True)
        return

    result = await cursor.execute("SELECT balance FROM user_profiles WHERE user_id = $1", interaction.user.id)
    user_profile = cursor.fetchone()
    if not user_profile or user_profile[0] < 1250:
        await interaction.followup.send("У вас недостаточно средств для создания роли. Требуется 1250 монет.", ephemeral=True)
        return

    guild = interaction.guild
    
    reference_role = guild.get_role(POSITION_UNDER_ROLE_ID)
    if not reference_role:
        await interaction.followup.send("Не удалось найти роль для позиционирования.", ephemeral=True)
        return
        
    role = await guild.create_role(name=название, color=discord.Color(int(цвет, 16)))
    
    try:
        positions = {r: r.position for r in guild.roles}
        positions[role] = reference_role.position
        await guild.edit_role_positions(positions)
    except discord.Forbidden:
        await interaction.followup.send("У бота недостаточно прав для изменения позиции роли.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка при установке позиции роли: {str(e)}", ephemeral=True)
        return

    await interaction.user.add_roles(role)

    await cursor.execute("UPDATE user_profiles SET balance = balance - 1250 WHERE user_id = $1", interaction.user.id)

    creation_date = datetime.now()
    expiration_date = creation_date + timedelta(days=30)

    creation_date_str = creation_date.strftime("%d.%m.%Y в %Hч %Mм %Sс")
    expiration_date_str = expiration_date.strftime("%d.%m.%Y в %Hч %Mм %Sс")

    await cursor.execute(""" 
        INSERT INTO roles (
            role_name, hex_code, owner_id, id_owner_now, creation_date, expiration_date, archived,
            extend_date, archivation_date, razarchive_date
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """, название, f"#{цвет}", interaction.user.id, interaction.user.id,
        creation_date_str, expiration_date_str, 0, None, None, None)

    embed = discord.Embed(
        description="Вы успешно приобрели роль на `30 д`",
        color=discord.Color(int('6e6e6e', 16))
    )
    
    embed.set_author(
        name=f"Создание роли - {interaction.user.name}",
        icon_url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
    )
    
    embed.add_field(name="<:docs:1295378117875204198> Название", value=название, inline=True)
    embed.add_field(name="<:100:1296116859040698389> Цвет", value=f"#{цвет}", inline=True)
    embed.add_field(name="<:pinkdiamond:1295376800431476780> Роль", value=role.mention, inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)

@role_group.command(name="manage", description="Управление ролями")
@role_existence_check
async def manage(interaction: Interaction):
    cursor = common.cursor
    user_id = interaction.user.id
    
    result = await cursor.execute("SELECT role_name, expiration_date, archived, remaining_time, id_owner_now FROM roles WHERE id_owner_now = $1", user_id)
    user_roles = cursor.fetchall()

    if not user_roles:
        await interaction.response.send_message("У вас нет ролей для управления.", ephemeral=True)
        return

    embed = Embed(color=0x6e6e6e)
    embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

    description = ""
    for index, (role_name, expiration_date, archived, remaining_time, id_owner_now) in enumerate(user_roles, start=1):
        role = get(interaction.guild.roles, name=role_name)
        if role:
            emoji = f"<:ww{index}:{'1337129170022432889' if index == 1 else '1337129132789862501'}>"
            if archived:
                description += f"{emoji} {role.mention} (архивирована) - **осталось:** `{remaining_time}`\n"
            else:
                try:
                    expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
                    remaining_time = expiration - datetime.now()
                    remaining_str = format_timedelta(remaining_time)
                    description += f"{emoji} {role.mention} - **осталось:** `{remaining_str}`\n"
                except Exception as e:
                    description += f"{emoji} {role.mention} - **Ошибка в дате истечения:** {str(e)}\n"

    embed.description = description or "Нет активных ролей."

    view = RoleSelectView(user_roles, interaction.user, interaction.guild)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class RoleSelectView(View):
    def __init__(self, user_roles, user, guild):
        super().__init__()
        self.user_roles = user_roles
        self.user = user
        self.guild = guild

        emoji_ids = {
            1: "<:ww1:1337129170022432889>",
            2: "<:ww2:1337129132789862501>"
        }

        for index, (role_name, _, _, _, _) in enumerate(self.user_roles, start=1):
            if index <= 2:
                button = Button(emoji=emoji_ids[index], style=ButtonStyle.primary)
                button.callback = self.create_callback(role_name)
                self.add_item(button)

    def create_callback(self, role_name):
        async def callback(interaction: Interaction):
            cursor = common.cursor
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            result = await cursor.execute("SELECT * FROM roles WHERE role_name = $1", role_name)
            role_info = cursor.fetchone()
            
            if role_info:
                embed = self.create_role_embed(role_info)
                view = self.create_role_view(role_info, interaction.user.id)
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.response.send_message("Информация о роли не найдена.", ephemeral=True)

        return callback

    @staticmethod
    def create_role_embed(role_info):
        role_name, hex_code, owner_id, id_owner_now, creation_date, expiration_date, archived, extend_date, archivation_date, razarchive_date, numberofday, remaining_time, allcoinsend_on_role = role_info

        embed = Embed(title=role_name, color=int(hex_code[1:], 16))
        embed.add_field(name="<:biletik:1337141227329556692> Цвет", value=hex_code)
        embed.add_field(name="<:crownaa:1337141290068086825> Создатель", value=f"<@{owner_id}>")
        embed.add_field(name="<:gamepads:1337141435811762289> Текущий владелец", value=f"<@{id_owner_now}>")
        embed.add_field(name="<:data:1337141473162039337> Дата создания", value=creation_date)
        embed.add_field(name="Статус", value="Архивирована" if archived else "Активна")
        embed.add_field(name="<:watchw:1337130049123389500> Дата истечения", value=expiration_date if not archived else "Архивирована")
        embed.add_field(name="<:vremya:1337141252151447555> Оставшееся время", value=remaining_time if archived else "Роль активна")
        embed.add_field(name="<:infor:1337141420305416252> Дата продления", value=extend_date if extend_date else "Нет")
        embed.add_field(name="<:pause:1337141200334880838> Дата архивации", value=archivation_date if archivation_date else "Нет")
        embed.add_field(name="<:unpause:1337141212599025684> Дата разархивации", value=razarchive_date if razarchive_date else "Нет")
        embed.add_field(name="<a:coinonrole:1298391257042784266> Потрачено монет на роль", value=str(allcoinsend_on_role))
        embed.set_footer(text="Архивация 250 монет")

        return embed

    def create_role_view(self, role_info, user_id):
        view = View()
        archived = role_info[6]
        expiration_date = role_info[5]
        id_owner_now = role_info[3]

        archive_button = Button(label="Разархивация" if archived else "Архивация", style=ButtonStyle.secondary, emoji="<:listok:1337141447861993653>")
        archive_button.callback = self.archive_callback(role_info, user_id)
        view.add_item(archive_button)

        can_extend = False
        if not archived and expiration_date != '-':
            expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
            remaining_time = expiration - datetime.now()
            can_extend = remaining_time.days < 364

        extend_button = Button(label="Продление", style=ButtonStyle.secondary, emoji="<:beskone4:1337141486512242868>", disabled=archived or not can_extend)
        extend_button.callback = self.extend_callback(role_info[0], user_id)
        view.add_item(extend_button)

        delete_button = Button(label="Удалить", style=ButtonStyle.danger, emoji="<:krestic:1337141359286550618>")
        delete_button.callback = self.delete_callback(role_info, user_id)
        view.add_item(delete_button)

        back_button = Button(label="Назад", style=ButtonStyle.secondary)
        back_button.callback = self.back_callback
        view.add_item(back_button)

        return view

    def archive_callback(self, role_info, user_id):
        async def callback(interaction: Interaction):
            cursor = common.cursor
            if interaction.user.id != user_id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            user_balance = await get_user_balance(cursor, user_id)
            if user_balance < 250:
                await interaction.response.send_message("У вас недостаточно монет для архивации роли.", ephemeral=True)
                return

            role_name, _, _, id_owner_now, _, expiration_date, archived, _, _, _, _, remaining_time, allcoinsend_on_role = role_info

            if archived:
                new_expiration_date = (datetime.now() + timedelta(
                    days=int(remaining_time.split('д')[0]),
                    hours=int(remaining_time.split('д')[1].split('ч')[0]),
                    minutes=int(remaining_time.split('ч')[1].split('м')[0]),
                    seconds=int(remaining_time.split('м')[1].split('с')[0])
                )).strftime("%d.%m.%Y в %Hч %Mм %Sс")
                await cursor.execute("UPDATE roles SET archived = 0, razarchive_date = $1, expiration_date = $2, remaining_time = NULL WHERE role_name = $3", 
                                   datetime.now().strftime("%d.%m.%Y в %Hч %Mм %Sс"), new_expiration_date, role_name)
                role = get(interaction.guild.roles, name=role_name)
                if role:
                    await interaction.user.add_roles(role)
                await interaction.response.send_message(f"Роль {role_name} разархивирована и выдана вам.", ephemeral=True)
            else:
                remaining_time_delta = datetime.strptime(expiration_date, "%d.%m.%Y в %Hч %Mм %Sс") - datetime.now()
                remaining_time = f"{remaining_time_delta.days}д {remaining_time_delta.seconds // 3600}ч {(remaining_time_delta.seconds % 3600) // 60}м {remaining_time_delta.seconds % 60}с"
                await cursor.execute("UPDATE roles SET archived = 1, archivation_date = $1, expiration_date = '-', remaining_time = $2, allcoinsend_on_role = allcoinsend_on_role + 250 WHERE role_name = $3",
                                   datetime.now().strftime("%d.%m.%Y в %Hч %Mм %Sс"), remaining_time, role_name)
                role = get(interaction.guild.roles, name=role_name)
                if role:
                    await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"Роль {role_name} заархивирована и снята. Вычтено 250 монет.", ephemeral=True)
                await subtract_user_balance(cursor, user_id, 250)

            result = await cursor.execute("SELECT * FROM roles WHERE role_name = $1", role_name)
            updated_role_info = cursor.fetchone()

            updated_embed = self.create_role_embed(updated_role_info)
            updated_view = self.create_role_view(updated_role_info, user_id)

            await interaction.followup.edit_message(message_id=interaction.message.id, embed=updated_embed, view=updated_view)

        return callback

    def extend_callback(self, role_name, user_id):
        async def callback(interaction: Interaction):
            await interaction.response.send_modal(ExtendRoleModal(role_name, self, user_id))
        return callback

    def delete_callback(self, role_info, user_id):
        async def callback(interaction: Interaction):
            if interaction.user.id != user_id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            role_name = role_info[0]

            confirm_embed = Embed(
                title="Подтверждение удаления",
                description=(
                    f"Вы действительно хотите удалить роль **{role_name}**?\n"
                    "Это действие необратимо — роль будет удалена с сервера и из базы данных."
                ),
                color=0x6e6e6e
            )
            confirm_view = RoleDeleteConfirmView(role_info, user_id, self)
            await interaction.response.edit_message(embed=confirm_embed, view=confirm_view)

        return callback

    async def back_callback(self, interaction: Interaction):
        cursor = common.cursor
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return
        
        result = await cursor.execute("SELECT role_name, expiration_date, archived, remaining_time, id_owner_now FROM roles WHERE id_owner_now = $1", self.user.id)
        updated_user_roles = cursor.fetchall()

        embed = Embed(color=0x6e6e6e)
        embed.set_author(name=self.user.name, icon_url=self.user.avatar.url if self.user.avatar else None)

        description = ""
        for index, (role_name, expiration_date, archived, remaining_time, id_owner_now) in enumerate(updated_user_roles, start=1):
            role = get(self.guild.roles, name=role_name)
            if role:
                emoji = f"<:pixe{index}:{'1298675729864851520' if index == 1 else '1298675752690122853'}>"
                if archived:
                    description += f"{emoji} {role.mention} (архивирована) - **осталось:** {remaining_time}\n"
                else:
                    try:
                        expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
                        remaining_time = expiration - datetime.now()
                        remaining_str = format_timedelta(remaining_time)
                        description += f"{emoji} {role.mention} - **осталось:** {remaining_str}\n"
                    except Exception as e:
                        description += f"{emoji} {role.mention} - **Ошибка в дате истечения:** {str(e)}\n"

        embed.description = description or "Нет активных ролей."
        
        updated_view = RoleSelectView(updated_user_roles, self.user, self.guild)

        await interaction.response.edit_message(embed=embed, view=updated_view)

class RoleDeleteConfirmView(View):
    """Второй шаг подтверждения удаления роли (как двухфакторка) — Да/Отмена."""

    def __init__(self, role_info, user_id, parent_view: 'RoleSelectView'):
        super().__init__(timeout=60)
        self.role_info = role_info
        self.user_id = user_id
        self.parent_view = parent_view

    async def on_timeout(self):
        # Если пользователь не ответил вовремя — просто оставляем сообщение как есть,
        # кнопки станут неактивными сами по себе (Discord их задизейблит после timeout).
        pass

    @discord.ui.button(label="Да, удалить", style=ButtonStyle.danger, emoji="<:checkmark:1526013748718993428>")
    async def confirm(self, interaction: Interaction, button: Button):
        cursor = common.cursor

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        role_name = self.role_info[0]

        try:
            await cursor.execute("DELETE FROM roles WHERE role_name = $1", role_name)
        except Exception as e:
            await interaction.response.send_message(f"Не удалось удалить роль из базы данных: {e}", ephemeral=True)
            return

        discord_role = get(interaction.guild.roles, name=role_name)
        if discord_role:
            try:
                await discord_role.delete(reason=f"Роль удалена владельцем ({interaction.user.id}) через профиль")
            except Exception as e:
                print(f"❌ Не удалось удалить роль '{role_name}' на сервере: {e}")

        result_embed = Embed(
            description=f"<:galochka:1337141373446651955> Роль **{role_name}** удалена.",
            color=0x6e6e6e
        )
        self.stop()
        await interaction.response.edit_message(embed=result_embed, view=None)

    @discord.ui.button(label="Отмена", style=ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        self.stop()
        embed = self.parent_view.create_role_embed(self.role_info)
        view = self.parent_view.create_role_view(self.role_info, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

class ExtendRoleModal(Modal):
    def __init__(self, role_name, view, user_id):
        super().__init__(title="Продление роли")
        self.role_name = role_name
        self.view = view
        self.user_id = user_id

        self.add_item(TextInput(label="Количество дней (1 день 45 монет)", style=TextStyle.short, placeholder="Введите количество дней"))

    async def on_submit(self, interaction: Interaction):
        cursor = common.cursor
        try:
            days_to_extend = int(self.children[0].value)
            if days_to_extend < 1 or days_to_extend > 365:
                await interaction.response.send_message("В модальном окне только цифры от 1 до 365", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("В модальном окне только цифры от 1 до 365", ephemeral=True)
            return

        result = await cursor.execute("SELECT expiration_date FROM roles WHERE role_name = $1", self.role_name)
        expiration_date = cursor.fetchone()[0]
        current_expiration = datetime.strptime(expiration_date, "%d.%m.%Y в %Hч %Mм %Sс")
        max_extend_date = datetime.now() + timedelta(days=365)
        new_expiration_date = min(current_expiration + timedelta(days=days_to_extend), max_extend_date)

        actual_days_extended = (new_expiration_date - current_expiration).days
        cost_of_extension = actual_days_extended * 45

        user_balance = await get_user_balance(cursor, self.user_id)
        if user_balance < cost_of_extension:
            await interaction.response.send_message(f"Недостаточно монет для продления роли. Необходимо {cost_of_extension} монет, а у вас {user_balance} монет.", ephemeral=True)
            return

        await cursor.execute("UPDATE roles SET expiration_date = $1, extend_date = $2, allcoinsend_on_role = allcoinsend_on_role + $3 WHERE role_name = $4", 
                           new_expiration_date.strftime("%d.%m.%Y в %Hч %Mм %Sс"), datetime.now().strftime("%d.%m.%Y в %Hч %Mм %Sс"), cost_of_extension, self.role_name)
        await subtract_user_balance(cursor, self.user_id, cost_of_extension)
        
        await interaction.response.send_message(f"Роль {self.role_name} успешно продлена на {actual_days_extended} дней за {cost_of_extension} монет!", ephemeral=True)

        result = await cursor.execute("SELECT * FROM roles WHERE role_name = $1", self.role_name)
        updated_role_info = cursor.fetchone()

        updated_embed = self.view.create_role_embed(updated_role_info)
        updated_view = self.view.create_role_view(updated_role_info, self.user_id)

        await interaction.followup.edit_message(message_id=interaction.message.id, embed=updated_embed, view=updated_view)

# Кнопка «Отобразить» перенесена из /role inventory в /me -> «Меню»
# (см. ProfileMenuView / RoleDisplaySelect / RoomDisplaySelect ниже, рядом с /me).


@role_group.command(name="inventory", description="Показать активные роли и дату их истечения")
@role_existence_check
async def inventory(interaction: discord.Interaction, пользователь: discord.User = None):
    cursor = common.cursor
    is_self = пользователь is None
    if пользователь is None:
        пользователь = interaction.user

    result = await cursor.execute(''' 
        SELECT role_name, expiration_date, remaining_time, archived 
        FROM roles 
        WHERE id_owner_now = $1 
    ''', пользователь.id)
    roles = cursor.fetchall()

    if not roles:
        await interaction.response.send_message(f"{пользователь.display_name} не имеет активных ролей в базе данных.", ephemeral=True)
        return

    embed = discord.Embed(color=discord.Color.from_str('#6e6e6e'))
    embed.set_author(name=f"Активные роли - {пользователь.display_name}", icon_url=пользователь.avatar.url)

    role_list = ""
    warning_emoji = "<:warning:1295095037734031472>"
    active_role_names = []  # роли, не архивированные и не истёкшие - доступны для отображения в профиле

    for index, (role_name, expiration_date, remaining_time, archived) in enumerate(roles, start=1):
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if role is None:
            continue

        archived_status = " (архивирована)" if archived == 1 else ""

        if archived == 1:
            remaining_time_str = remaining_time if remaining_time else "Оставшееся время не указано"
            days_left = int(remaining_time_str.split('д')[0]) if remaining_time_str.split('д')[0].isdigit() else 0
        else:
            try:
                expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
            except ValueError as e:
                await interaction.response.send_message(f"Ошибка преобразования даты для роли {role_name}: {e}", ephemeral=True)
                return

            time_difference = expiration - datetime.now()

            if time_difference.total_seconds() <= 0:
                remaining_time_str = "Роль истекла"
                days_left = 0
            else:
                days = time_difference.days
                hours, remainder = divmod(time_difference.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                remaining_time_str = f"{days}д {hours:02}ч {minutes:02}м {seconds:02}с"
                days_left = days

        warning = warning_emoji if days_left < 3 else ""
        role_list += f"**{index})** {role.mention}{archived_status} - **осталось:** `{remaining_time_str}`{warning}\n"

        if archived != 1 and days_left > 0:
            active_role_names.append(role_name)

    embed.description = role_list if role_list else "Нет активных ролей."

    # Выбор отображаемой в профиле роли теперь делается через /me -> «Меню»
    await interaction.response.send_message(embed=embed, ephemeral=True)

@role_group.command(name="info", description="Получить информацию о роли")
@app_commands.describe(роль="Упомяните роль для проверки информации")
@role_existence_check
async def info(interaction: discord.Interaction, роль: discord.Role):
    cursor = common.cursor
    result = await cursor.execute("""
        SELECT role_name, hex_code, owner_id, creation_date, expiration_date, archived, extend_date, archivation_date, razarchive_date, allcoinsend_on_role, remaining_time
        FROM roles
        WHERE role_name = $1
    """, роль.name)
    result_row = cursor.fetchone()

    if result_row:
        role_name, hex_code, owner_id, creation_date, expiration_date, archived, extend_date, archivation_date, razarchive_date, allcoinsend_on_role, remaining_time = result_row
        owner = interaction.guild.get_member(owner_id)

        def format_date(date_str):
            if date_str and date_str != '-':
                return datetime.strptime(date_str, '%d.%m.%Y в %Hч %Mм %Sс').strftime("%d.%m.%Y в %Hч %Mм %Sс")
            return "Не установлена"

        embed = discord.Embed(color=int(hex_code.lstrip("#"), 16))
        embed.set_author(name=f"Информация о роли - {interaction.user.display_name}", icon_url=interaction.user.avatar.url)
        
        embed.add_field(name="<:4elovekww:1337141385530445886> Роль", value=роль.mention, inline=True)
        embed.add_field(name="<:biletik:1337141227329556692> Цвет (HEX)", value=f"*{hex_code}*", inline=True)
        embed.add_field(name="<a:SmokingAstronaut:1298391242706522224> Владелец", value=owner.mention if owner else "Неизвестен", inline=True)
        embed.add_field(name="<:data:1337141473162039337> Создание", value=f"*{format_date(creation_date)}*", inline=True)
        embed.add_field(name="<:beskone4:1337141486512242868> Продление", value=f"*{format_date(extend_date)}*", inline=True)
        embed.add_field(name="<a:coinonrole:1298391257042784266> Потрачено на роль", value=f"*{allcoinsend_on_role}*", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("Эта роль не найдена в базе данных.", ephemeral=True)

@role_group.command(name="give", description="Передать роль другому пользователю")
@role_existence_check
async def give(interaction: discord.Interaction):
    cursor = common.cursor
    sender = interaction.user

    result = await cursor.execute("SELECT role_name FROM roles WHERE id_owner_now = $1", sender.id)
    owned_roles = [row[0] for row in cursor.fetchall()]

    if not owned_roles:
        await interaction.response.send_message("У вас нет ролей для передачи.", ephemeral=True)
        return

    view = RoleGiveView(sender, owned_roles)
    await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

class RoleGiveView(ui.View):
    """Меню передачи роли: выбор роли -> выбор получателя -> сумма -> подтверждение.
    Логика построена по образцу LobbyManageView из commands_lobby.py."""

    def __init__(self, sender: discord.Member, owned_roles: list):
        super().__init__(timeout=120)
        self.sender = sender
        self.owned_roles = owned_roles
        self.selected_role_name: str | None = None
        self.selected_user: discord.Member | None = None
        self.amount = 0
        self.amount_set = False
        self.message = None

        self.role_select = RoleGiveRoleSelect(self, owned_roles)
        self.user_select = RoleGiveUserSelect(self)
        self.amount_button = RoleGiveAmountButton(self)

        self.add_item(self.role_select)
        self.add_item(self.user_select)
        self.add_item(self.amount_button)
        self.update_amount_button_state()

    def update_amount_button_state(self):
        self.amount_button.disabled = not (self.selected_role_name and self.selected_user)

    def build_embed(self):
        embed = discord.Embed(color=0x6e6e6e)
        embed.set_author(name="Передача роли", icon_url=self.sender.display_avatar.url)
        embed.add_field(
            name="<:pinkdiamond:1295376800431476780> Роль",
            value=f"`{self.selected_role_name}`" if self.selected_role_name else "не выбрана",
            inline=True
        )
        embed.add_field(
            name="<:4elovekww:1337141385530445886> Получатель",
            value=self.selected_user.mention if self.selected_user else "не выбран",
            inline=True
        )
        if self.amount_set:
            embed.add_field(
                name="<a:coinonrole:1298391257042784266> Сумма",
                value=f"{self.amount} монет" if self.amount > 0 else "Бесплатно",
                inline=True
            )
            embed.set_footer(text="Нажмите «Подтвердить», чтобы отправить предложение")
        else:
            embed.set_footer(text="Выберите роль и получателя, затем нажмите «Сумма»")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.sender.id:
            await interaction.response.send_message("Это меню не для вас.", ephemeral=True)
            return False
        return True

class RoleGiveRoleSelect(ui.Select):
    def __init__(self, parent_view: "RoleGiveView", owned_roles: list):
        options = [discord.SelectOption(label=name[:100], value=name) for name in owned_roles[:2]]
        super().__init__(placeholder="Выберите роль для передачи", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_role_name = self.values[0]
        self.parent_view.update_amount_button_state()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)

class RoleGiveUserSelect(discord.ui.UserSelect):
    def __init__(self, parent_view: "RoleGiveView"):
        super().__init__(placeholder="Выберите получателя", min_values=1, max_values=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        member = selected if isinstance(selected, discord.Member) else interaction.guild.get_member(selected.id)

        if member is None or member.bot:
            await interaction.response.send_message("Нельзя выбрать бота или пользователя вне сервера.", ephemeral=True)
            return
        if member.id == self.parent_view.sender.id:
            await interaction.response.send_message("Нельзя передать роль самому себе.", ephemeral=True)
            return

        self.parent_view.selected_user = member
        self.parent_view.update_amount_button_state()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)

class RoleGiveAmountButton(ui.Button):
    def __init__(self, parent_view: "RoleGiveView"):
        super().__init__(label="Сумма", style=discord.ButtonStyle.secondary, emoji="<a:coinonrole:1298391257042784266>", disabled=True)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleGiveAmountModal(self.parent_view))

class RoleGiveAmountModal(ui.Modal, title="Сумма за роль"):
    amount = ui.TextInput(label="Сумма монет (0 — бесплатно)", style=discord.TextStyle.short, placeholder="0", required=True, max_length=10)

    def __init__(self, parent_view: "RoleGiveView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.amount.value)
            if value < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Введите неотрицательное целое число.", ephemeral=True)
            return

        view = self.parent_view
        view.amount = value
        view.amount_set = True

        # Кнопка "Сумма" больше не нужна — заменяем её на "Подтвердить"
        view.remove_item(view.amount_button)
        view.confirm_button = RoleGiveConfirmButton(view)
        view.add_item(view.confirm_button)

        await interaction.response.edit_message(embed=view.build_embed(), view=view)

class RoleGiveConfirmButton(ui.Button):
    def __init__(self, parent_view: "RoleGiveView"):
        super().__init__(label="Подтвердить", style=discord.ButtonStyle.success, emoji="<:checkmark:1526013748718993428>")
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        cursor = common.cursor
        view = self.parent_view
        sender = view.sender
        role_name = view.selected_role_name
        получатель = view.selected_user
        сумма = view.amount

        # Финальные проверки прямо перед отправкой публичного предложения
        result = await cursor.execute("SELECT id_owner_now FROM roles WHERE role_name = $1 AND id_owner_now = $2", role_name, sender.id)
        if not cursor.fetchone():
            await interaction.response.edit_message(
                embed=discord.Embed(description="Эта роль вам больше не принадлежит.", color=0x6e6e6e), view=None
            )
            return

        role_obj = discord.utils.get(interaction.guild.roles, name=role_name)
        if role_obj is None:
            await interaction.response.edit_message(
                embed=discord.Embed(description="Роль не найдена на сервере.", color=0x6e6e6e), view=None
            )
            return

        result = await cursor.execute("SELECT COUNT(*) FROM roles WHERE id_owner_now = $1", получатель.id)
        role_count = cursor.fetchone()[0]
        if role_count >= 2:
            await interaction.response.edit_message(
                embed=discord.Embed(description="У получателя уже есть 2 роли. Передача невозможна.", color=0x6e6e6e), view=None
            )
            return

        if сумма > 0:
            result = await cursor.execute("SELECT balance FROM user_profiles WHERE user_id = $1", получатель.id)
            receiver_balance = cursor.fetchone()
            if not receiver_balance or receiver_balance[0] < сумма:
                await interaction.response.edit_message(
                    embed=discord.Embed(description="У получателя недостаточно средств для оплаты роли.", color=0x6e6e6e), view=None
                )
                return

        # Резервируем роль на время ожидания принятия
        await cursor.execute("UPDATE roles SET id_owner_now = -1 WHERE role_name = $1", role_name)
        await sender.remove_roles(role_obj)

        # Закрываем ephemeral-меню отправителя
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"Предложение о передаче роли {role_obj.mention} отправлено {получатель.mention}.",
                color=0x6e6e6e
            ),
            view=None
        )

        # Публичное предложение с кнопкой для получателя (видно всем, живёт максимум 60 секунд)
        offer_embed = discord.Embed(color=discord.Color.from_str('#6e6e6e'), timestamp=discord.utils.utcnow())
        offer_embed.set_author(name=f"Трансфер роли - {sender.name}", icon_url=sender.display_avatar.url)
        offer_embed.add_field(name="Отправитель", value=sender.mention, inline=True)
        offer_embed.add_field(name="Получатель", value=получатель.mention, inline=True)
        offer_embed.add_field(name="Роль", value=role_obj.mention, inline=True)
        offer_embed.add_field(name="Сумма", value=f"{сумма} <a:coinonrole:1298391257042784266>" if сумма > 0 else "Бесплатно", inline=True)
        offer_embed.set_footer(text="У получателя есть 60 секунд, чтобы принять предложение")

        offer_view = RoleGiveAcceptView(sender, получатель, role_obj, role_name, сумма)
        offer_message = await interaction.channel.send(content=получатель.mention, embed=offer_embed, view=offer_view)
        offer_view.message = offer_message

class RoleGiveAcceptView(ui.View):
    """Публичная кнопка «Купить» для получателя. 60 секунд на принятие,
    иначе роль возвращается отправителю, а сообщение стирается."""

    def __init__(self, sender: discord.Member, получатель: discord.Member, role_obj: discord.Role, role_name: str, сумма: int):
        super().__init__(timeout=60)
        self.sender = sender
        self.получатель = получатель
        self.role_obj = role_obj
        self.role_name = role_name
        self.сумма = сумма
        self.message = None
        self.resolved = False

    async def on_timeout(self):
        cursor = common.cursor
        if self.resolved:
            return
        self.resolved = True

        await cursor.execute("UPDATE roles SET id_owner_now = $1 WHERE role_name = $2", self.sender.id, self.role_name)
        try:
            await self.sender.add_roles(self.role_obj)
        except Exception:
            pass

        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass

    @discord.ui.button(label="Купить", style=discord.ButtonStyle.success, emoji="<:checkmark:1526013748718993428>")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor = common.cursor
        if interaction.user.id != self.получатель.id:
            await interaction.response.send_message("Это предложение не для вас.", ephemeral=True)
            return
        if self.resolved:
            await interaction.response.send_message("Это предложение уже обработано.", ephemeral=True)
            return

        if self.сумма > 0:
            result = await cursor.execute("SELECT balance FROM user_profiles WHERE user_id = $1", self.получатель.id)
            receiver_balance = cursor.fetchone()
            if not receiver_balance or receiver_balance[0] < self.сумма:
                await interaction.response.send_message("У вас недостаточно монет для покупки этой роли.", ephemeral=True)
                return

        self.resolved = True
        self.stop()

        await cursor.execute("UPDATE roles SET id_owner_now = $1 WHERE role_name = $2", self.получатель.id, self.role_name)
        await self.получатель.add_roles(self.role_obj)

        if self.сумма > 0:
            await cursor.execute("UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2", self.сумма, self.получатель.id)
            await cursor.execute("UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2", self.сумма, self.sender.id)

        success_embed = discord.Embed(
            description=f"Роль {self.role_obj.mention} успешно передана пользователю {self.получатель.mention}",
            color=discord.Color.from_str('#6e6e6e'),
            timestamp=discord.utils.utcnow()
        )
        success_embed.set_author(name=f"Трансфер роли - {self.sender.name}", icon_url=self.sender.display_avatar.url)
        success_embed.add_field(name="Получатель", value=self.получатель.mention, inline=True)
        success_embed.add_field(name="Роль", value=self.role_obj.mention, inline=True)
        success_embed.add_field(name="Сумма", value=f"{self.сумма} монет" if self.сумма > 0 else "Бесплатно", inline=True)

        await interaction.response.defer()
        await interaction.channel.send(embed=success_embed)

        try:
            await interaction.message.delete()
        except Exception:
            pass

# ============================================
# WITHROLE COMMAND
# ============================================

@app_commands.command(name="withrole", description="Показать список пользователей с указанной ролью")
@app_commands.describe(роль="Роль, для которой нужно показать список пользователей")
async def withrole(interaction: discord.Interaction, роль: discord.Role):
    members_with_role = [member for member in interaction.guild.members if роль in member.roles]
    
    if not members_with_role:
        await interaction.response.send_message(
            embed=discord.Embed(
                description="В данной роли нет пользователей.",
                color=0x6e6e6e
            ),
            ephemeral=True
        )
        return

    embed, view = build_role_members_page(interaction.user, роль, 0, members_with_role)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

def build_role_members_page(owner: discord.Member, роль: discord.Role, offset: int, members: list):
    """Строит embed + view для страницы списка участников роли (используется и при первом ответе, и при edit_message)."""
    members_per_page = 20
    members_to_display = members[offset:offset + members_per_page]

    embed = discord.Embed(
        title=f"Пользователи с ролью {роль.name}",
        color=0x6e6e6e
    )

    member_list = []
    for index, member in enumerate(members_to_display, start=offset + 1):
        member_list.append(f"**{index}.** {member.mention}")

    embed.description = "\n".join(member_list) or "Нет пользователей на этой странице."
    total_pages = (len(members) + members_per_page - 1) // members_per_page
    embed.set_footer(text=f"Страница {offset // members_per_page + 1}/{total_pages}")

    view = RoleMemberView(offset, len(members), owner, роль, members)
    return embed, view

class RoleMemberView(ui.View):
    def __init__(self, offset: int, total_items: int, owner: discord.Member, роль: discord.Role, members: list):
        super().__init__()
        self.offset = offset
        self.total_items = total_items
        self.items_per_page = 20
        self.owner = owner
        self.роль = роль
        self.members = members
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        self.add_button("Назад", self.go_back, discord.ButtonStyle.primary, disabled=(self.offset == 0))
        self.add_button("Информация о роли", self.show_role_info, discord.ButtonStyle.secondary)
        self.add_button("Вперед", self.go_forward, discord.ButtonStyle.primary, 
                       disabled=(self.offset + self.items_per_page >= self.total_items))

    def add_button(self, label: str, callback, style: discord.ButtonStyle, disabled: bool = False):
        button = discord.ui.Button(label=label, style=style, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def go_back(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        new_offset = max(self.offset - self.items_per_page, 0)
        embed, view = build_role_members_page(self.owner, self.роль, new_offset, self.members)
        await interaction.response.edit_message(embed=embed, view=view)

    async def go_forward(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        new_offset = min(self.offset + self.items_per_page, max(self.total_items - self.items_per_page, 0))
        embed, view = build_role_members_page(self.owner, self.роль, new_offset, self.members)
        await interaction.response.edit_message(embed=embed, view=view)

    async def show_role_info(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Информация о роли {self.роль.name}",
            description=( 
                f"**Роль:** {self.роль.mention}\n"
                f"**Носителей:** {len([member for member in interaction.guild.members if self.роль in member.roles])}\n"
                f"**ID роли:** {self.роль.id}\n"
                f"**Цвет роли:** {self.роль.color}"
            ),
            color=0x6e6e6e
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
