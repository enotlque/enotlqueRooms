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


class RoleDisplaySelect(discord.ui.Select):
    """Селект для выбора роли, которая будет отображаться в визуальном профиле (/me)."""

    def __init__(self, role_options: list):
        options = [discord.SelectOption(label=name[:100], value=name) for name in role_options[:25]]
        super().__init__(placeholder="Роль для отображения в профиле", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        cursor = common.cursor
        chosen_role_name = self.values[0]

        await cursor.execute(
            'UPDATE user_profiles SET displayed_role = $1 WHERE user_id = $2',
            chosen_role_name, interaction.user.id
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"Роль **{chosen_role_name}** теперь отображается в вашем профиле (`/me`).",
                color=discord.Color.from_str('#6e6e6e')
            ),
            ephemeral=True
        )


class RoomDisplaySelect(discord.ui.Select):
    """Селект для выбора комнаты, которая будет отображаться в визуальном профиле (/me)."""

    def __init__(self, room_options: list):
        options = [discord.SelectOption(label=name[:100], value=name) for name in room_options[:25]]
        super().__init__(placeholder="Комната для отображения в профиле", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        cursor = common.cursor
        chosen_room_name = self.values[0]

        await cursor.execute(
            'UPDATE user_profiles SET displayed_room = $1 WHERE user_id = $2',
            chosen_room_name, interaction.user.id
        )

        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"Комната **{chosen_room_name}** теперь отображается в вашем профиле (`/me`).",
                color=discord.Color.from_str('#6e6e6e')
            ),
            ephemeral=True
        )


class ProfileMenuView(discord.ui.View):
    """Меню /me -> «Меню»: селект роли добавляется только если есть из чего выбирать
    (2+ активные роли), аналогично для комнаты (2+ комнаты по роли участника)."""

    def __init__(self, role_options: list, room_options: list):
        super().__init__(timeout=60)
        if len(role_options) >= 2:
            self.add_item(RoleDisplaySelect(role_options))
        if len(room_options) >= 2:
            self.add_item(RoomDisplaySelect(room_options))


@app_commands.command(name="me", description="Показать профиль пользователя")
@app_commands.describe(пользователь="Участник, чей профиль вы хотите просмотреть")
async def me(interaction: discord.Interaction, пользователь: discord.Member = None):
    cursor = common.cursor
    пользователь = пользователь or interaction.user

    await interaction.response.defer()

    image_buffer = await create_profile_image(cursor, пользователь, interaction.guild)
    profile_file = discord.File(image_buffer, filename="profile.png")

    view = ui.View()

    # Проверка брака
    result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1', пользователь.id)
    has_marriage = cursor.fetchone() is not None

    button_marriage = ui.Button(
        label="Брачный профиль",
        style=discord.ButtonStyle.gray,
        emoji="<a:pinkpixelheart:1298391338223403008>",
        disabled=not has_marriage or пользователь != interaction.user
    )

    class ExtendMarriageModal(ui.Modal, title="Продление брака"):
        days = ui.TextInput(
            label="Количество дней (1 день - 90 монет)",
            style=discord.TextStyle.short,
            placeholder="Введите количество дней от 1 до 365"
        )

        async def on_submit(self, modal_interaction: discord.Interaction):
            cursor = common.cursor
            try:
                days_to_extend = int(self.days.value)
                if days_to_extend < 1 or days_to_extend > 365:
                    await modal_interaction.response.send_message(
                        embed=discord.Embed(
                            color=0x6e6e6e,
                            description="Количество дней должно быть от 1 до 365."
                        ),
                        ephemeral=True
                    )
                    return
            except ValueError:
                await modal_interaction.response.send_message("Пожалуйста, введите число от 1 до 365", ephemeral=True)
                return

            result = await cursor.execute('SELECT expires_at, marriage_balance FROM marriages WHERE user1_id = $1 OR user2_id = $2', 
                         пользователь.id, пользователь.id)
            marriage_data = cursor.fetchone()
            
            if not marriage_data:
                await modal_interaction.response.send_message("Ошибка: брак не найден", ephemeral=True)
                return

            current_expiration = datetime.fromisoformat(marriage_data[0])
            marriage_balance = marriage_data[1]
            
            max_extend_date = datetime.now() + timedelta(days=365)
            new_expiration_date = min(current_expiration + timedelta(days=days_to_extend), max_extend_date)
            
            actual_days_extended = (new_expiration_date - current_expiration).days
            cost = actual_days_extended * 90

            if marriage_balance < cost:
                await modal_interaction.response.send_message(
                    embed=discord.Embed(
                        color=0x6e6e6e,
                        description=f"Недостаточно средств. Необходимо: {cost} монет, доступно: {marriage_balance} монет"
                    ),
                    ephemeral=True
                )
                return

            await cursor.execute(
                'UPDATE marriages SET expires_at = $1, marriage_balance = marriage_balance - $2 WHERE user1_id = $3 OR user2_id = $4',
                new_expiration_date.isoformat(), cost, пользователь.id, пользователь.id
            )

            updated_marriage_embed = await create_marriage_embed(cursor, modal_interaction, пользователь)
            await modal_interaction.response.edit_message(embed=updated_marriage_embed, view=create_marriage_view(cursor, пользователь))

    async def marriage_callback(i: discord.Interaction):
        cursor = common.cursor
        if i.user != пользователь:
            await i.response.send_message(
                embed=discord.Embed(
                    description="Вы не имеете право взаимодействовать с данным сообщением",
                    color=0x6e6e6e
                ),
                ephemeral=True
            )
            return
        result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1', пользователь.id)
        marriage_data = cursor.fetchone()
        
        if not marriage_data:
            await i.response.send_message(f"{пользователь.name} не состоит в браке!", ephemeral=True)
            return

        marriage_embed = await create_marriage_embed(cursor, i, пользователь)
        marriage_view = await create_marriage_view(cursor, пользователь, i)
        await i.response.edit_message(embed=marriage_embed, view=marriage_view, attachments=[])

    def format_duration(duration):
        years = duration.days // 365
        days = duration.days % 365
        hours = duration.seconds // 3600
        minutes = (duration.seconds % 3600) // 60
        seconds = duration.seconds % 60

        parts = []
        if years > 0:
            parts.append(f"{years}г")
        if days > 0:
            parts.append(f"{days}д")
        if hours > 0:
            parts.append(f"{hours}ч")
        if minutes > 0:
            parts.append(f"{minutes}м")
        if seconds > 0:
            parts.append(f"{seconds}с")
            
        if not parts:
            return "0с"
            
        return " ".join(parts)

    async def create_marriage_embed(cursor, interaction, user):
        result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
        marriage_data = cursor.fetchone()
        
        partner_id = marriage_data[1] if marriage_data[1] != user.id else marriage_data[2]
        partner = await interaction.client.fetch_user(partner_id)
        created_at = datetime.fromisoformat(marriage_data[4])
        expires_at = datetime.fromisoformat(marriage_data[6])
        
        now = datetime.now()
        marriage_duration = now - created_at

        embed = discord.Embed(color=0x6e6e6e)
        embed.set_author(name=f"Любовный профиль - @{user.display_name}", icon_url=user.avatar.url)
        embed.add_field(name="Возлюбленные", value=f"```ㅤ{user.display_name}ㅤ ㅤ❤ㅤ ㅤ{partner.display_name}ㅤ```", inline=False)
        embed.add_field(name="Баланс", value=f"```{marriage_data[3]}```", inline=True)
        embed.add_field(name="Регистрация", value=f"```{created_at.strftime('%d-%m-%Y')}```", inline=True)
        embed.add_field(name="Действителен до", value=f"```{expires_at.strftime('%d-%m-%Y')}```", inline=True)
        embed.add_field(name="Времени вместе", value=f"```{format_duration(marriage_duration)}```", inline=False)
        
        return embed

    async def create_marriage_view(cursor, user, interaction):
        marriage_view = ui.View()
        
        button_add_balance = ui.Button(label="Пополнить баланс", style=discord.ButtonStyle.green, emoji="<a:co11nn:1301173474751938641>")
        async def add_balance_callback(i: discord.Interaction):
            cursor = common.cursor
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            class AddBalanceModal(ui.Modal, title="Пополнить баланс"):
                amount = ui.TextInput(label="Сумма (мин. 1 монета)", min_length=1, max_length=10, required=True)

                async def on_submit(self, modal_interaction: discord.Interaction):
                    cursor = common.cursor
                    try:
                        amount = int(self.amount.value)
                        if amount < 1:
                            await modal_interaction.response.send_message("Минимальная сумма пополнения — 1 монета.", ephemeral=True)
                            return

                        await cursor.execute(
                            'UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2 AND balance >= $1 RETURNING balance',
                            amount, user.id
                        )
                        if cursor.fetchone() is None:
                            await modal_interaction.response.send_message(
                                embed=discord.Embed(
                                    color=0x6e6e6e,
                                    description="Недостаточно средств."
                                ),
                                ephemeral=True
                            )
                            return

                        await cursor.execute('UPDATE marriages SET marriage_balance = marriage_balance + $1 WHERE user1_id = $2 OR user2_id = $3', amount, user.id, user.id)

                        updated_marriage_embed = await create_marriage_embed(cursor, modal_interaction, user)
                        updated_view = await create_marriage_view(cursor, user, modal_interaction)
                        await modal_interaction.response.edit_message(embed=updated_marriage_embed, view=updated_view)
                        await modal_interaction.followup.send(
                            embed=discord.Embed(
                                color=0x6e6e6e,
                                description=f"Брачный баланс пополнен на {amount} монет."
                            ),
                            ephemeral=True
                        )

                    except ValueError:
                        await modal_interaction.response.send_message("Пожалуйста, введите корректное число.", ephemeral=True)

            await i.response.send_modal(AddBalanceModal())

        button_add_balance.callback = add_balance_callback
        marriage_view.add_item(button_add_balance)

        result = await cursor.execute('SELECT expires_at FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
        marriage_data = cursor.fetchone()
        if marriage_data:
            expires_at = datetime.fromisoformat(marriage_data[0])
            now = datetime.now()
            days_until_max = (now + timedelta(days=365) - expires_at).days
        else:
            days_until_max = 0
        
        button_extend = ui.Button(
            label="Продление", 
            style=discord.ButtonStyle.primary,
            disabled=days_until_max <= 0,
            emoji="<:heart:1295095623690878987>"
        )
        
        async def extend_callback(i: discord.Interaction):
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            await i.response.send_modal(ExtendMarriageModal())

        button_extend.callback = extend_callback
        marriage_view.add_item(button_extend)

        button_divorce = ui.Button(
            label="Развестись",
            style=discord.ButtonStyle.danger,
            emoji="<:xx:1295095667617960018>"
        )

        async def divorce_callback(i: discord.Interaction):
            cursor = common.cursor
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            confirmation_embed = discord.Embed(
                color=0x6e6e6e,
                description="Вы уверены, что хотите развестись? Нажмите **__Подтвердить__**."
            )
            confirmation_embed.set_author(
                name=f"Решил развестись - {i.user.display_name}",
                icon_url=i.user.avatar.url
            )
            confirmation_embed.set_footer(text="Это сообщение будет доступно 25 секунд.")

            confirm_view = ui.View(timeout=25)
            confirm_button = ui.Button(
                label="Подтвердить",
                style=discord.ButtonStyle.danger
            )

            async def confirm_callback(confirm_i: discord.Interaction):
                if confirm_i.user != i.user:
                    await confirm_i.response.send_message(
                        embed=discord.Embed(
                            color=0x6e6e6e,
                            description="Только инициатор развода может подтвердить это действие."
                        ),
                        ephemeral=True
                    )
                    return

                result = await cursor.execute('SELECT voice_marry_id, user1_id, user2_id FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
                marriage_data = cursor.fetchone()

                if marriage_data:
                    voice_channel_id = marriage_data[0]
                    
                    if voice_channel_id:
                        try:
                            voice_channel = await i.guild.fetch_channel(voice_channel_id)
                            await voice_channel.delete()
                        except discord.NotFound:
                            pass
                        except Exception as e:
                            print(f"Error deleting voice channel: {e}")

                    await cursor.execute('DELETE FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)

                    final_embed = discord.Embed(
                        color=0x6e6e6e,
                        description="Ваш брак был успешно расторгнут."
                    )
                    final_embed.set_author(
                        name=f"Решил развестись - {confirm_i.user.display_name}",
                        icon_url=confirm_i.user.avatar.url
                    )
                    final_embed.set_footer(text=":(")
                    await confirm_i.response.edit_message(embed=final_embed, view=None)
                else:
                    error_embed = discord.Embed(
                        color=0x6e6e6e,
                        description="Ошибка: брак не найден."
                    )
                    await confirm_i.response.edit_message(embed=error_embed, view=None)

            confirm_button.callback = confirm_callback
            confirm_view.add_item(confirm_button)

            async def on_timeout():
                timeout_embed = discord.Embed(
                    color=0x6e6e6e,
                    description="Время расторжения брака истекло, повторите действие."
                )
                timeout_embed.set_author(
                    name=f"Решил развестись - {i.user.display_name}",
                    icon_url=i.user.avatar.url
                )
                timeout_embed.set_footer(text="Это сообщение было доступно 25 секунд.")
                await i.message.edit(embed=timeout_embed, view=None)

            confirm_view.on_timeout = on_timeout
            await i.response.edit_message(embed=confirmation_embed, view=confirm_view)

        button_divorce.callback = divorce_callback
        marriage_view.add_item(button_divorce)

        button_back = ui.Button(label="Назад", style=discord.ButtonStyle.gray)
        async def back_callback(i: discord.Interaction):
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            updated_buffer = await create_profile_image(cursor, user, i.guild)
            updated_file = discord.File(updated_buffer, filename="profile.png")
            await i.response.edit_message(embed=None, attachments=[updated_file], view=view)

        button_back.callback = back_callback
        marriage_view.add_item(button_back)
        
        return marriage_view

    button_marriage.callback = marriage_callback
    view.add_item(button_marriage)

    # Кнопка «Меню»: выбор отображаемой в профиле роли и/или комнаты.
    # Недоступна для чужого профиля, а также если выбирать не из чего —
    # то есть у пользователя не больше одной роли и не больше одной комнаты (см. ТЗ п.2).
    active_role_names = await get_active_role_names(cursor, пользователь)
    room_options = await get_member_room_options(cursor, пользователь)
    room_names = [name for name, _ in room_options]

    can_choose_role = len(active_role_names) >= 2
    can_choose_room = len(room_names) >= 2

    button_menu = ui.Button(
        label="Меню",
        style=discord.ButtonStyle.gray,
        emoji="<:infor:1337141420305416252>",
        disabled=(пользователь != interaction.user) or not (can_choose_role or can_choose_room)
    )

    async def menu_callback(i: discord.Interaction):
        if i.user != пользователь:
            await i.response.send_message(
                embed=discord.Embed(
                    description="Меню отображения профиля доступно только его владельцу.",
                    color=0x6e6e6e
                ),
                ephemeral=True
            )
            return

        current_role_names = await get_active_role_names(cursor, пользователь)
        current_room_options = await get_member_room_options(cursor, пользователь)
        current_room_names = [name for name, _ in current_room_options]

        menu_view = ProfileMenuView(current_role_names, current_room_names)
        await i.response.send_message(
            "Выберите, что отображать в профиле (`/me`):",
            view=menu_view,
            ephemeral=True
        )

    button_menu.callback = menu_callback
    view.add_item(button_menu)

    await interaction.followup.send(file=profile_file, view=view)
