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
# TOP GROUP - Топы сервера
# ============================================

top_group = app_commands.Group(name="top", description="Топы сервера")

def _top_rank_prefix(index: int) -> str:
    if index == 1:
        return "<:w1:1337129208819875912>"
    elif index == 2:
        return "<:w2:1337129278818750497>"
    elif index == 3:
        return "<:w3:1337129254755762237>"
    return f"**{index})**"

TOP_PAGE_SIZE = 10
TOP_FETCH_LIMIT = 100  # сколько строк максимум тянем из БД для постраничного топа

def _build_top_embed(title: str, icon_url, entries: list, offset: int, page_size: int):
    embed = Embed(color=0x6e6e6e)
    embed.set_author(name=title, icon_url=icon_url)
    page_entries = entries[offset:offset + page_size]
    embed.add_field(name="", value="\n".join(page_entries) or "Нет данных.", inline=False)
    total_pages = max((len(entries) + page_size - 1) // page_size, 1)
    embed.set_footer(text=f"Страница {offset // page_size + 1}/{total_pages}")
    return embed

class TopPaginatorView(ui.View):
    """Постраничный просмотр топов с кнопками Назад / Удалить / Вперёд.
    Сообщение остаётся видимым всем, но удалить его может только тот, кто вызвал команду."""

    def __init__(self, title: str, icon_url, entries: list, owner_id: int, offset: int = 0, page_size: int = TOP_PAGE_SIZE):
        super().__init__(timeout=180)
        self.title = title
        self.icon_url = icon_url
        self.entries = entries
        self.owner_id = owner_id
        self.offset = offset
        self.page_size = page_size
        self.message = None
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()

        back_button = ui.Button(label="Назад", style=discord.ButtonStyle.secondary, disabled=(self.offset == 0))
        back_button.callback = self.go_back
        self.add_item(back_button)

        delete_button = ui.Button(label="Удалить", style=discord.ButtonStyle.danger)
        delete_button.callback = self.delete_message
        self.add_item(delete_button)

        forward_button = ui.Button(label="Вперёд", style=discord.ButtonStyle.secondary, 
                                    disabled=(self.offset + self.page_size >= len(self.entries)))
        forward_button.callback = self.go_forward
        self.add_item(forward_button)

    def render_embed(self):
        return _build_top_embed(self.title, self.icon_url, self.entries, self.offset, self.page_size)

    async def go_back(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Листать этот топ может только тот, кто его вызвал.", ephemeral=True)
            return
        self.offset = max(self.offset - self.page_size, 0)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def go_forward(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Листать этот топ может только тот, кто его вызвал.", ephemeral=True)
            return
        self.offset = min(self.offset + self.page_size, max(len(self.entries) - self.page_size, 0))
        self.update_buttons()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def delete_message(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Удалить это сообщение может только тот, кто вызвал команду.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except discord.NotFound:
            pass
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass

@top_group.command(name="coin", description="Показать топ пользователей по монетам")
async def top_coin(interaction: discord.Interaction):
    cursor = common.cursor
    result = await cursor.execute(f'SELECT user_id, balance FROM user_profiles ORDER BY balance DESC LIMIT {TOP_FETCH_LIMIT}')
    top_users = cursor.fetchall()

    if not top_users:
        await interaction.response.send_message(embed=Embed(description="Нет данных.", color=0x6e6e6e))
        return

    user_entries = []
    index = 0
    for user_id, balance_amount in top_users:
        user = interaction.guild.get_member(user_id)
        if not user:
            continue
        index += 1
        prefix = _top_rank_prefix(index)
        user_entries.append(f"{prefix} {user.mention} - **{balance_amount}** <:wwaluta:1337129761956167751>")

    if not user_entries:
        await interaction.response.send_message(embed=Embed(description="Нет данных.", color=0x6e6e6e))
        return

    icon_url = interaction.user.avatar.url if interaction.user.avatar else None
    view = TopPaginatorView("Топ пользователей по монетам", icon_url, user_entries, interaction.user.id)
    await interaction.response.send_message(embed=view.render_embed(), view=view)
    view.message = await interaction.original_response()

@top_group.command(name="role", description="Показать топ ролей по потраченным на них монетам")
async def top_role(interaction: discord.Interaction):
    cursor = common.cursor
    result = await cursor.execute(f'SELECT role_name, allcoinsend_on_role FROM roles ORDER BY allcoinsend_on_role DESC LIMIT {TOP_FETCH_LIMIT}')
    top_roles = cursor.fetchall()

    if not top_roles:
        await interaction.response.send_message(embed=Embed(description="Нет данных.", color=0x6e6e6e))
        return

    role_entries = []
    for index, (role_name, allcoinsend_on_role) in enumerate(top_roles, start=1):
        role = get(interaction.guild.roles, name=role_name)
        role_display = role.mention if role else f"**{role_name}**"
        prefix = _top_rank_prefix(index)
        role_entries.append(f"{prefix} {role_display} - **{allcoinsend_on_role}** <a:coinonrole:1298391257042784266>")

    icon_url = interaction.user.avatar.url if interaction.user.avatar else None
    view = TopPaginatorView("Топ ролей по потраченным монетам", icon_url, role_entries, interaction.user.id)
    await interaction.response.send_message(embed=view.render_embed(), view=view)
    view.message = await interaction.original_response()

@top_group.command(name="hours", description="Показать топ пользователей по часам в войсе")
async def top_hours(interaction: discord.Interaction):
    cursor = common.cursor
    
    # ПРОВЕРЯЕМ КЕШ
    cached = await get_cached(top_cache_key("hours"))
    if cached:
        icon_url = interaction.user.avatar.url if interaction.user.avatar else None
        view = TopPaginatorView("Топ пользователей по часам в войсе", icon_url, cached, interaction.user.id)
        await interaction.response.send_message(embed=view.render_embed(), view=view)
        view.message = await interaction.original_response()
        return
    
    result = await cursor.execute(f'SELECT user_id, voice_hours FROM user_profiles ORDER BY voice_hours DESC LIMIT {TOP_FETCH_LIMIT}')
    top_users = cursor.fetchall()

    if not top_users:
        await interaction.response.send_message(embed=Embed(description="Нет данных.", color=0x6e6e6e))
        return

    user_entries = []
    index = 0
    for user_id, hours in top_users:
        user = interaction.guild.get_member(user_id)
        if not user or not hours:
            continue
        index += 1
        prefix = _top_rank_prefix(index)
        user_entries.append(f"{prefix} {user.mention} - **{float(hours):.1f}ч**")

    if not user_entries:
        await interaction.response.send_message(embed=Embed(description="Нет данных.", color=0x6e6e6e))
        return

    # СОХРАНЯЕМ В КЕШ
    await set_cached(top_cache_key("hours"), user_entries, 3600)

    icon_url = interaction.user.avatar.url if interaction.user.avatar else None
    view = TopPaginatorView("Топ пользователей по часам в войсе", icon_url, user_entries, interaction.user.id)
    await interaction.response.send_message(embed=view.render_embed(), view=view)
    view.message = await interaction.original_response()

# ============================================
# PROFILE COMMAND - /me
# ============================================
