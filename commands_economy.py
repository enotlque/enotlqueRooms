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

# ============================================
# ГЛОБАЛЬНЫЙ CURSOR (передается из main.py)
# ============================================
cursor = None

def set_cursor(c):
    global cursor
    cursor = c
# ============================================

# ============================================
# HELPER FUNCTIONS
# ============================================

def create_embed(description: str, color: str = "#00FF00", footer=None, title=None, author_name=None, author_icon_url=None):
    embed = discord.Embed(description=description, color=int(color.lstrip("#"), 16))
    if footer:
        embed.set_footer(text=footer)
    if title:
        embed.title = title
    if author_name and author_icon_url:
        embed.set_author(name=author_name, icon_url=author_icon_url)
    return embed

def format_timedelta(td):
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}д {hours:02d}ч {minutes:02d}м {seconds:02d}с"

async def get_user_balance(cursor, user_id):
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', user_id)
    row = cursor.fetchone()
    return row[0] if row else 0

async def subtract_user_balance(cursor, user_id, amount):
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', user_id)
    row = cursor.fetchone()
    if row:
        current_balance = row[0]
        if current_balance >= amount:
            new_balance = current_balance - amount
            await cursor.execute('UPDATE user_profiles SET balance = $1 WHERE user_id = $2', new_balance, user_id)
            return True
    return False

# ============================================
# ECO GROUP - Экономические команды
# ============================================

eco_group = app_commands.Group(name="eco", description="Экономические команды")

@eco_group.command(name="balance", description="Проверить баланс")
@app_commands.describe(пользователь="Проверить баланс пользователя")
async def balance(interaction: discord.Interaction, пользователь: discord.Member = None):
    пользователь = пользователь or interaction.user
    global cursor
    
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    row = cursor.fetchone()
    
    if row:
        balance_amount = row[0]
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)', 
                           пользователь.id, 0, None)
        balance_amount = 0
    
    await interaction.response.send_message(embed=create_embed(
        description="",
        color="#696969",
        author_name=f"Баланс - {пользователь.display_name}",
        author_icon_url=пользователь.avatar.url
    ).add_field(name="Монет", value=f"```\n{balance_amount}\n```", inline=False), ephemeral=True)

@eco_group.command(name="daily", description="Получить ежедневный бонус (каждые 24 часа)")
async def daily(interaction: discord.Interaction):
    BONUS_AMOUNT = 75
    COOLDOWN_HOURS = 24
    global cursor

    result = await cursor.execute('SELECT last_daily_claimed, balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    row = cursor.fetchone()

    current_time = datetime.now()

    if row:
        last_daily_claimed, balance_amount = row
        if last_daily_claimed:
            last_daily_claimed = datetime.strptime(last_daily_claimed, "%Y-%m-%d %H:%M:%S")
            if current_time < last_daily_claimed + timedelta(hours=COOLDOWN_HOURS):
                next_claim_time = last_daily_claimed + timedelta(hours=COOLDOWN_HOURS)
                discord_time = f"<t:{int(next_claim_time.timestamp())}:R>"
                await interaction.response.send_message(embed=create_embed(
                    description=f"<:xx:1295095667617960018> Вы уже забрали бонус. В следующий раз можете его получить {discord_time}.",
                    color="#6e6e6e",
                    author_name=f"Бонус - {interaction.user.display_name}",
                    author_icon_url=interaction.user.avatar.url
                ), ephemeral=True)  
                return

        new_balance = balance_amount + BONUS_AMOUNT
        await cursor.execute('UPDATE user_profiles SET balance = $1, last_daily_claimed = $2 WHERE user_id = $3', 
                           new_balance, current_time.strftime("%Y-%m-%d %H:%M:%S"), interaction.user.id)
        await interaction.response.send_message(embed=create_embed(
            description=f"Вы забрали: {BONUS_AMOUNT} <a:coinonrole:1298391257042784266>",
            color="#6e6e6e",
            author_name=f"Бонус - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url,
            footer="Возвращайтесь через 24 часа"
        ), ephemeral=True)  
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)', 
                           interaction.user.id, BONUS_AMOUNT, current_time.strftime("%Y-%m-%d %H:%M:%S"))
        await interaction.response.send_message(embed=create_embed(
            description=f"Вы забрали: {BONUS_AMOUNT} <a:coinonrole:1298391257042784266>",
            color="#6e6e6e",
            author_name=f"Бонус - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url,
            footer="Возвращайтесь через 24 часа"
        ), ephemeral=True)  

@eco_group.command(name="work", description="Поработать и получить монеты (каждые 12 часов)")
async def work(interaction: discord.Interaction):
    MIN_AMOUNT = 45
    MAX_AMOUNT = 75
    COOLDOWN_HOURS = 12
    global cursor

    result = await cursor.execute('SELECT last_work_claimed, balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    row = cursor.fetchone()

    current_time = datetime.now()
    earned = random.randint(MIN_AMOUNT, MAX_AMOUNT)

    if row:
        last_work_claimed, balance_amount = row
        if last_work_claimed:
            last_work_claimed = datetime.strptime(last_work_claimed, "%Y-%m-%d %H:%M:%S")
            if current_time < last_work_claimed + timedelta(hours=COOLDOWN_HOURS):
                next_claim_time = last_work_claimed + timedelta(hours=COOLDOWN_HOURS)
                discord_time = f"<t:{int(next_claim_time.timestamp())}:R>"
                await interaction.response.send_message(embed=create_embed(
                    description=f"<:xx:1295095667617960018> Вы уже работали. В следующий раз сможете поработать {discord_time}.",
                    color="#6e6e6e",
                    author_name=f"Работа - {interaction.user.display_name}",
                    author_icon_url=interaction.user.avatar.url
                ), ephemeral=True)  
                return

        new_balance = (balance_amount or 0) + earned
        await cursor.execute('UPDATE user_profiles SET balance = $1, last_work_claimed = $2 WHERE user_id = $3', 
                           new_balance, current_time.strftime("%Y-%m-%d %H:%M:%S"), interaction.user.id)
        await interaction.response.send_message(embed=create_embed(
            description=f"Вы заработали: {earned} <a:coinonrole:1298391257042784266>",
            color="#6e6e6e",
            author_name=f"Работа - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url,
            footer="Возвращайтесь через 12 часов"
        ), ephemeral=True)  
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance, last_work_claimed) VALUES ($1, $2, $3)', 
                           interaction.user.id, earned, current_time.strftime("%Y-%m-%d %H:%M:%S"))
        await interaction.response.send_message(embed=create_embed(
            description=f"Вы заработали: {earned} <a:coinonrole:1298391257042784266>",
            color="#6e6e6e",
            author_name=f"Работа - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url,
            footer="Возвращайтесь через 12 часов"
        ), ephemeral=True)  

@eco_group.command(name="purse", description="Администраторская команда для выдачи средств пользователю")
@app_commands.describe(
    пользователь="Участник, которому вы хотите выдать средства",
    сумма="Сумма выдачи (от 1 до 100,000)"
)
async def purse(interaction: discord.Interaction, пользователь: discord.Member, сумма: int):
    global cursor
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(embed=create_embed(
            description="У вас недостаточно прав для выполнения этой команды.",
            color="#696969"
        ), ephemeral=True)
        return

    if сумма < 1 or сумма > 100000:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма должна быть в пределах от 1 до 100,000 монет.",
            color="#696969"
        ), ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    recipient_balance = cursor.fetchone()

    if recipient_balance:
        await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', сумма, пользователь.id)
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', пользователь.id, сумма)

    await interaction.response.send_message(embed=create_embed(
        description=f"Вы успешно выдали {сумма} монет {пользователь.mention}.",
        color="#696969",
        author_name=f"Выдача монет - {interaction.user.display_name}",
        author_icon_url=interaction.user.avatar.url
    ), ephemeral=True)

@eco_group.command(name="take", description="Администраторская команда для изъятия средств у пользователя")
@app_commands.describe(
    пользователь="Участник, у которого вы хотите изъять средства",
    сумма="Сумма изъятия (от 1 до 100,000)"
)
async def take(interaction: discord.Interaction, пользователь: discord.Member, сумма: int):
    global cursor
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(embed=create_embed(
            description="У вас недостаточно прав для выполнения этой команды.",
            color="#696969"
        ), ephemeral=True)
        return

    if сумма < 1 or сумма > 100000:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма должна быть в пределах от 1 до 100,000 монет.",
            color="#696969"
        ), ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    user_balance = cursor.fetchone()

    if not user_balance:
        await interaction.response.send_message(embed=create_embed(
            description="У пользователя нет профиля в системе.",
            color="#696969"
        ), ephemeral=True)
        return

    if user_balance[0] < сумма:
        await interaction.response.send_message(embed=create_embed(
            description=f"У пользователя недостаточно средств. Текущий баланс: {user_balance[0]} монет.",
            color="#696969"
        ), ephemeral=True)
        return

    await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', сумма, пользователь.id)

    await interaction.response.send_message(embed=create_embed(
        description=f"Вы успешно изъяли {сумма} монет у {пользователь.mention}.",
        color="#696969",
        author_name=f"Изъятие монет - {interaction.user.display_name}",
        author_icon_url=interaction.user.avatar.url
    ), ephemeral=True)

@eco_group.command(name="transfer", description="Перевести средства другому пользователю")
@app_commands.describe(
    пользователь="Участник, которому вы хотите перевести средства",
    сумма="Сумма перевода (минимум 30 монет)"
)
async def transfer(interaction: discord.Interaction, пользователь: discord.Member, сумма: int):
    global cursor
    if сумма < 30:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма перевода должна быть не менее 30 монет.",
            color="#696969"
        ), ephemeral=True)
        return

    if пользователь.id == interaction.user.id:
        await interaction.response.send_message(embed=create_embed(
            description="Невозможно перевести монеты самому себе.",
            color="#696969"
        ), ephemeral=True)
        return

    if сумма <= 0:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма перевода должна быть больше 0.",
            color="#696969"
        ), ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    sender_balance = cursor.fetchone()
    
    if sender_balance and sender_balance[0] >= сумма:
        commission = int(сумма * 0.1)
        total_amount = сумма - commission

        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
        recipient_balance = cursor.fetchone()

        await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', сумма, interaction.user.id)
        if recipient_balance:
            await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', total_amount, пользователь.id)
        else:
            await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', пользователь.id, total_amount)

        await interaction.response.send_message(embed=create_embed(
            description=f"Вы успешно перевели {сумма} монет {пользователь.mention}.",
            color="#696969",
            author_name=f"Перевод монет - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url
        ).set_footer(text="Комиссия 10%"), ephemeral=True)
    else:
        await interaction.response.send_message(embed=create_embed(
            description="Недостаточно средств для перевода.",
            color="#696969"
        ), ephemeral=True)

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
    global cursor
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
    global cursor
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

# ============================================
# PROFILE COMMAND - /me
# ============================================

async def create_profile_embed(cursor, user, guild):
    result = await cursor.execute('SELECT balance, status, god_kissed FROM user_profiles WHERE user_id = $1', user.id)
    row = cursor.fetchone()

    if not row:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', user.id, 0)
        balance_amount, status, god_kissed = 0, "Статуса нет", "—"
    else:
        balance_amount, status, god_kissed = row

    embed = discord.Embed(color=0x6e6e6e, title="", description="")
    embed.add_field(name="Статус", value=f"```\n{status}\n```", inline=False)
    embed.add_field(name="Баланс", value=f"```{balance_amount}```", inline=True)

    days_on_server = "—"
    if isinstance(user, discord.Member) and user.joined_at:
        now = datetime.now(user.joined_at.tzinfo)
        days_on_server = str((now - user.joined_at).days)
    embed.add_field(name="Дней на сервере", value=f"```{days_on_server}```", inline=True)
    embed.add_field(name="Комментарий админа", value=f"```{god_kissed or '—'}```", inline=True)

    result = await cursor.execute('SELECT user1_id, user2_id FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
    marriage_data = cursor.fetchone()
    if marriage_data:
        partner_id = marriage_data[0] if marriage_data[1] == user.id else marriage_data[1]
        partner = await guild.fetch_member(partner_id)
        embed.add_field(name="Возлюбленные", value=f"```{partner.display_name}```", inline=False)

    embed.set_author(name=f"Профиль - {user.display_name}", icon_url=user.avatar.url)
    return embed

class StatusModal(ui.Modal, title="Обновить статус"):
    status_input = ui.TextInput(label="Введите новый статус", min_length=2, max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        global cursor
        await cursor.execute('UPDATE user_profiles SET status = $1 WHERE user_id = $2', self.status_input.value, interaction.user.id)

        await interaction.response.send_message(embed=discord.Embed(
            description=f"Статус обновлен на: ```{self.status_input.value}```",
            color=0x6e6e6e
        ), ephemeral=True)

        profile_embed = await create_profile_embed(cursor, interaction.user, interaction.guild)
        await interaction.followup.edit_message(interaction.message.id, embed=profile_embed)

@app_commands.command(name="me", description="Показать профиль пользователя")
@app_commands.describe(пользователь="Участник, чей профиль вы хотите просмотреть")
async def me(interaction: discord.Interaction, пользователь: discord.Member = None):
    global cursor
    пользователь = пользователь or interaction.user
    embed = await create_profile_embed(cursor, пользователь, interaction.guild)

    view = ui.View()

    button_change_status = ui.Button(
        label="Изменить статус", 
        style=discord.ButtonStyle.secondary, 
        emoji="<:whitepen:1337134443902537769>",
        disabled=пользователь != interaction.user
    )

    async def change_status_callback(i: discord.Interaction):
        if i.user == пользователь:
            await i.response.send_modal(StatusModal())
            updated_embed = await create_profile_embed(cursor, пользователь, i.guild)
            await interaction.edit_original_response(embed=updated_embed, view=view)

    button_change_status.callback = change_status_callback
    view.add_item(button_change_status)

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
            global cursor
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
        global cursor
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
        await i.response.edit_message(embed=marriage_embed, view=marriage_view)

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
            global cursor
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
                amount = ui.TextInput(label="Сумма (мин. 90 монет)", min_length=2, max_length=10, required=True)

                async def on_submit(self, modal_interaction: discord.Interaction):
                    global cursor
                    try:
                        amount = int(self.amount.value)
                        if amount < 90:
                            await modal_interaction.response.send_message("Минимальная сумма пополнения — 90 монет.", ephemeral=True)
                            return

                        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', user.id)
                        user_balance_row = cursor.fetchone()
                        user_balance = user_balance_row[0] if user_balance_row else 0

                        if user_balance < amount:
                            await modal_interaction.response.send_message(
                                embed=discord.Embed(
                                    color=0x6e6e6e,
                                    description="Недостаточно средств."
                                ),
                                ephemeral=True
                            )
                            return

                        await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', amount, user.id)
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
            global cursor
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
            updated_embed = await create_profile_embed(cursor, user, i.guild)
            await i.response.edit_message(embed=updated_embed, view=view)

        button_back.callback = back_callback
        marriage_view.add_item(button_back)
        
        return marriage_view

    button_marriage.callback = marriage_callback
    view.add_item(button_marriage)

    await interaction.response.send_message(embed=embed, view=view)

# ============================================
# MARRY COMMAND
# ============================================

@app_commands.command(name="marry", description="Сделать предложение пользователю")
async def marry(interaction: discord.Interaction, пользователь: discord.Member):
    global cursor
    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger(__name__)
    
    MALE_ROLE_ID = 1126893214536827050
    FEMALE_ROLE_ID = 1126893217405739090
    MARRIAGE_COST = 2500
    MARRIAGE_CATEGORY_ID = 1132300392215097365
    
    # Хранилище активных предложений (user_id -> сумма)
    active_proposals = getattr(marry, 'active_proposals', {})
    marry.active_proposals = active_proposals
    
    async def check_basic_conditions():
        if пользователь.id == interaction.user.id:
            await interaction.response.send_message("Вы не можете сделать предложение самому себе!", ephemeral=True)
            return False
            
        sender_roles = [role.id for role in interaction.user.roles]
        target_roles = [role.id for role in пользователь.roles]
        
        sender_gender_count = sum(1 for role_id in sender_roles if role_id in (MALE_ROLE_ID, FEMALE_ROLE_ID))
        target_gender_count = sum(1 for role_id in target_roles if role_id in (MALE_ROLE_ID, FEMALE_ROLE_ID))
        
        if sender_gender_count != 1 or target_gender_count != 1:
            await interaction.response.send_message("У каждого участника должна быть ровно одна гендерная роль!", ephemeral=True)
            return False
            
        sender_is_male = MALE_ROLE_ID in sender_roles
        target_is_male = MALE_ROLE_ID in target_roles
        if sender_is_male == target_is_male:
            await interaction.response.send_message("Ай-ай, нарушаем закон РФ)", ephemeral=True)
            return False
            
        return True

    async def check_balance_and_profiles():
        # Проверяем активные предложения
        if interaction.user.id in active_proposals:
            await interaction.response.send_message(
                "У вас уже есть активное предложение! Дождитесь его завершения.",
                ephemeral=True
            )
            return False
        
        # Проверяем и создаём профиль отправителя если его нет
        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
        sender_row = cursor.fetchone()
        
        if not sender_row:
            await cursor.execute(
                'INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)',
                interaction.user.id, 0, None
            )
            await interaction.response.send_message(
                f"У вас недостаточно монет! Необходимо {MARRIAGE_COST} монет. У вас 0 монет.",
                ephemeral=True
            )
            return False
        
        if sender_row[0] < MARRIAGE_COST:
            await interaction.response.send_message(
                f"У вас недостаточно монет! Необходимо {MARRIAGE_COST} монет. У вас {sender_row[0]} монет.",
                ephemeral=True
            )
            return False
        
        # Проверяем и создаём профиль получателя если его нет
        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
        receiver_row = cursor.fetchone()
        
        if not receiver_row:
            await cursor.execute(
                'INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)',
                пользователь.id, 0, None
            )
            logger.info(f"Создан профиль для пользователя {пользователь.id} при отправке предложения")
            
        return True

    async def check_marriage_status():
        try:
            await cursor.execute(
                'SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1 OR user1_id = $2 OR user2_id = $2',
                interaction.user.id, пользователь.id
            )
            if cursor.fetchone():
                await interaction.response.send_message("Один из пользователей уже состоит в браке!", ephemeral=True)
                return False
            return True
        except Exception as e:
            logger.error(f"Error checking marriage status: {str(e)}\n{traceback.format_exc()}")
            await interaction.response.send_message("Произошла ошибка при проверке статуса брака.", ephemeral=True)
            return False

    class MarriageView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.message = None
            self.is_processed = False  # Флаг, чтобы избежать двойной обработки

        async def on_timeout(self):
            if self.is_processed:
                return
            self.is_processed = True
            
            sender_avatar = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
            sender_name = interaction.user.name
            
            embed = discord.Embed(
                description="Ваше предложение руки и сердца истекло.",
                color=discord.Color.from_rgb(110, 110, 110)
            )
            embed.set_author(
                name=f"Предложение руки и сердца - {sender_name}",
                icon_url=sender_avatar
            )
            
            # Возвращаем монеты отправителю
            try:
                # Проверяем, не был ли уже создан брак
                await cursor.execute(
                    'SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1',
                    interaction.user.id
                )
                if not cursor.fetchone():
                    # Возвращаем монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    logger.info(f"Возвращены монеты пользователю {interaction.user.id} при таймауте")
                
                # Удаляем из активных предложений
                if interaction.user.id in active_proposals:
                    del active_proposals[interaction.user.id]
            except Exception as e:
                logger.error(f"Error returning coins on timeout: {e}")
            
            await self.message.edit(embed=embed, view=None)
            self.stop()

        @discord.ui.button(label="Принять", style=discord.ButtonStyle.green)
        async def accept(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            global cursor
            if self.is_processed:
                await button_interaction.response.send_message("Это предложение уже обработано!", ephemeral=True)
                return
                
            if button_interaction.user.id != пользователь.id:
                await button_interaction.response.send_message("Это не ваше предложение!", ephemeral=True)
                return

            # Блокируем кнопки
            self.is_processed = True
            for child in self.children:
                child.disabled = True
            await self.message.edit(view=self)

            try:
                # === КРИТИЧЕСКАЯ ПРОВЕРКА: баланс отправителя ===
                result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
                sender_row = cursor.fetchone()
                
                # Проверяем, что у отправителя всё ещё есть достаточно монет
                # (с учётом того, что мы уже зарезервировали MARRIAGE_COST)
                if not sender_row or sender_row[0] < 0:
                    # Если баланс отрицательный или отсутствует - что-то пошло не так
                    await button_interaction.response.send_message(
                        "Ошибка: баланс отправителя повреждён. Обратитесь к администратору.",
                        ephemeral=True
                    )
                    # Возвращаем зарезервированные монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return
                
                # Проверяем, не потратил ли отправитель зарезервированные монеты
                # Если баланс меньше 0 - значит он потратил больше, чем у него было
                if sender_row[0] < 0:
                    await button_interaction.response.send_message(
                        f"У отправителя недостаточно монет! Баланс: {sender_row[0]} монет. Необходимо: {MARRIAGE_COST} монет.",
                        ephemeral=True
                    )
                    # Возвращаем зарезервированные монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return

                # Проверяем и создаём профиль получателя
                result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
                receiver_row = cursor.fetchone()
                
                if not receiver_row:
                    await cursor.execute(
                        'INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)',
                        пользователь.id, 0, None
                    )
                    logger.info(f"Создан профиль для пользователя {пользователь.id} при принятии предложения")

                # Проверяем брак повторно
                await cursor.execute(
                    'SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1 OR user1_id = $2 OR user2_id = $2',
                    interaction.user.id, пользователь.id
                )
                if cursor.fetchone():
                    await button_interaction.response.send_message(
                        "Кто-то из пользователей уже успел вступить в брак!", 
                        ephemeral=True
                    )
                    # Возвращаем зарезервированные монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return

                # === СОЗДАЁМ БРАК ===
                category = discord.utils.get(interaction.guild.categories, id=MARRIAGE_CATEGORY_ID)
                if category is None:
                    logger.error(f"Marriage category {MARRIAGE_CATEGORY_ID} not found")
                    await button_interaction.response.send_message(
                        "Категория для создания голосового канала не найдена.", 
                        ephemeral=True
                    )
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return

                voice_channel = await category.create_voice_channel(
                    name=f"☯ {interaction.user.display_name} & {пользователь.display_name}"
                )

                await voice_channel.set_permissions(interaction.guild.default_role, connect=False)
                await voice_channel.set_permissions(interaction.user, connect=True, speak=True)
                await voice_channel.set_permissions(пользователь, connect=True, speak=True)

                created_at = datetime.now().isoformat()
                expires_at = (datetime.now() + timedelta(days=30)).isoformat()

                await cursor.execute('''
                    INSERT INTO marriages (user1_id, user2_id, marriage_balance, created_at, renewed_at, expires_at, voice_marry_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''', interaction.user.id, пользователь.id, 0, created_at, created_at, expires_at, voice_channel.id)

                # Монеты уже были списаны при отправке предложения, 
                # поэтому НЕ списываем их повторно!

                # Удаляем из активных предложений
                if interaction.user.id in active_proposals:
                    del active_proposals[interaction.user.id]

                success_embed = discord.Embed(
                    title="Брак успешно зарегистрирован!",
                    description=f"{interaction.user.mention} и {пользователь.mention} теперь **Возлюбленные**. Им предоставляется комната {voice_channel.mention}",
                    color=discord.Color.from_rgb(110, 110, 110)
                )
                await self.message.edit(embed=success_embed, view=None)
                
                await button_interaction.response.send_message(
                    "✅ Брак успешно заключён!", 
                    ephemeral=True
                )
                self.stop()

            except Exception as e:
                logger.error(f"Error in marriage creation: {str(e)}\n{traceback.format_exc()}")
                
                # Откат при ошибке
                try:
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                except Exception as rollback_error:
                    logger.error(f"Error rolling back: {rollback_error}")
                
                await button_interaction.response.send_message(
                    "Произошла ошибка при создании брака. Пожалуйста, попробуйте еще раз.",
                    ephemeral=True
                )
                self.stop()

        @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
        async def decline(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            if self.is_processed:
                await button_interaction.response.send_message("Это предложение уже обработано!", ephemeral=True)
                return
                
            if button_interaction.user.id not in [пользователь.id, interaction.user.id]:
                await button_interaction.response.send_message("Это не ваше предложение!", ephemeral=True)
                return

            self.is_processed = True
            for child in self.children:
                child.disabled = True
            await self.message.edit(view=self)

            # Возвращаем зарезервированные монеты
            try:
                await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                    MARRIAGE_COST, interaction.user.id)
                if interaction.user.id in active_proposals:
                    del active_proposals[interaction.user.id]
            except Exception as e:
                logger.error(f"Error returning coins on decline: {e}")

            if button_interaction.user.id == interaction.user.id:
                decline_embed = discord.Embed(
                    description=f"💔 {interaction.user.mention} отклонил собственное предложение руки и сердца {пользователь.mention}.",
                    color=discord.Color.from_rgb(110, 110, 110)
                )
            else:
                decline_embed = discord.Embed(
                    description=f"💔 {пользователь.mention} отклонил(а) предложение руки и сердца {interaction.user.mention}.",
                    color=discord.Color.from_rgb(110, 110, 110)
                )

            await self.message.edit(embed=decline_embed, view=None)
            self.stop()

    try:
        if not await check_basic_conditions():
            return
            
        if not await check_balance_and_profiles():
            return
            
        if not await check_marriage_status():
            return

        # === РЕЗЕРВИРУЕМ МОНЕТЫ ===
        # Списываем их сразу, чтобы предотвратить трату
        await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', 
                            MARRIAGE_COST, interaction.user.id)
        
        # Добавляем в список активных предложений
        active_proposals[interaction.user.id] = MARRIAGE_COST

        embed = discord.Embed(
            description=f"Сделал предложение {пользователь.mention}\nДля принятия нажмите на кнопку ниже.",
            color=discord.Color.from_rgb(110, 110, 110)
        )
        embed.set_author(
            name=f"Предложение руки и сердца - {interaction.user.name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
        )
        embed.set_footer(text="У вас есть 60 секунд на принятие решения")

        view = MarriageView()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    except Exception as e:
        logger.error(f"Error in main relation flow: {str(e)}\n{traceback.format_exc()}")
        
        # Откат при любой ошибке
        try:
            await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                MARRIAGE_COST, interaction.user.id)
            if interaction.user.id in active_proposals:
                del active_proposals[interaction.user.id]
        except Exception as rollback_error:
            logger.error(f"Error rolling back in main flow: {rollback_error}")
        
        await interaction.response.send_message("Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", ephemeral=True)

# ============================================
# АВТОПРОДЛЕНИЕ / АВТОРАСТОРЖЕНИЕ БРАКОВ
# ============================================

MARRIAGE_RENEWAL_DAY_COST = 90
MARRIAGE_AUTO_RENEW_MAX_DAYS = 30

_marriage_task_started = False

def start_marriage_expiry_task(bot):
    global _marriage_task_started
    if _marriage_task_started:
        return
    _marriage_task_started = True

    @tasks.loop(hours=1)
    async def check_marriage_expirations():
        global cursor
        try:
            await cursor.execute(
                'SELECT user1_id, user2_id, marriage_balance, expires_at, voice_marry_id FROM marriages'
            )
            rows = cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка при чтении браков для автопроверки: {e}")
            return

        now = datetime.now()

        for row in rows:
            user1_id, user2_id, marriage_balance, expires_at_str, voice_channel_id = row

            try:
                expires_at = datetime.fromisoformat(expires_at_str)
            except (TypeError, ValueError):
                continue

            if expires_at > now:
                continue

            affordable_days = (marriage_balance or 0) // MARRIAGE_RENEWAL_DAY_COST

            if affordable_days >= 1:
                days_to_add = min(affordable_days, MARRIAGE_AUTO_RENEW_MAX_DAYS)
                cost = days_to_add * MARRIAGE_RENEWAL_DAY_COST
                new_expires_at = (expires_at + timedelta(days=days_to_add)).isoformat()

                try:
                    await cursor.execute(
                        'UPDATE marriages SET expires_at = $1, marriage_balance = marriage_balance - $2, renewed_at = $3 '
                        'WHERE user1_id = $4 AND user2_id = $5',
                        new_expires_at, cost, now.isoformat(), user1_id, user2_id
                    )
                    print(f"✅ Брак {user1_id}-{user2_id} автопродлён на {days_to_add} дн. (списано {cost} с общего баланса)")
                except Exception as e:
                    print(f"❌ Ошибка автопродления брака {user1_id}-{user2_id}: {e}")
                continue

            if voice_channel_id:
                channel = bot.get_channel(voice_channel_id)
                if channel:
                    try:
                        await channel.delete(reason="Автоматическое расторжение брака: закончился общий баланс")
                    except Exception as e:
                        print(f"❌ Не удалось удалить голосовой канал брака {user1_id}-{user2_id}: {e}")

            try:
                await cursor.execute('DELETE FROM marriages WHERE user1_id = $1 AND user2_id = $2', user1_id, user2_id)
                print(f"💔 Брак {user1_id}-{user2_id} автоматически расторгнут: не хватило средств на продление")
            except Exception as e:
                print(f"❌ Ошибка автоматического расторжения брака {user1_id}-{user2_id}: {e}")
                continue

            divorce_embed = discord.Embed(
                description="Ваш брак был автоматически расторгнут: на общем балансе не хватило средств для продления.",
                color=0x6e6e6e
            )
            for uid in (user1_id, user2_id):
                user_obj = bot.get_user(uid)
                if user_obj:
                    try:
                        await user_obj.send(embed=divorce_embed)
                    except Exception:
                        pass

    @check_marriage_expirations.before_loop
    async def before_check():
        await bot.wait_until_ready()

    check_marriage_expirations.start()

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
        global cursor
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
        global cursor
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
    global cursor
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
# SLOTS GROUP
# ============================================

active_players: Set[int] = set()

SLOT_SYMBOLS = {
    "<:orangediamond:1295376833688113232>": (2, 150, 30),   # вес, x3 в ряд, x2 в ряд
    "<:slotiseven:1337178032430911488>": (6, 55, 16),
    "<:cherry128x:1337421942529065082>": (15, 22, 0),        # 2 в ряд теперь проигрыш
    "<:lemon128x:1337421957431300146>": (25, 9, 0),          # 2 в ряд теперь проигрыш
    "<:strawberry128x:1337421500898082817>": (52, 3, 0)      # 2 в ряд — проигрыш (как раньше)
}

LEFT_ARROW = "<:rightarrow:1337396550204129330>"
RIGHT_ARROW = "<:leftarrow:1337396538619592744>"

slots_group = app_commands.Group(name="slots", description="Команды для игры в слоты")

async def generate_slot_display(symbols_matrix: List[List[str]]) -> str:
    display_lines = []
    padding = " ㅤㅤ "
    
    for i, row in enumerate(symbols_matrix):
        if i == 1:
            line = f"{LEFT_ARROW}ㅤ**|** {' **:** '.join(row)} **|**ㅤ{RIGHT_ARROW}"
        else:
            line = f"{padding}**|** {' **|** '.join(row)} **|**{padding}"
        display_lines.append(line)
    
    return "\n \n".join(display_lines)

async def animate_slots(interaction: discord.Interaction, embed: discord.Embed) -> List[str]:
    symbols, weights = zip(*[(sym, data[0]) for sym, data in SLOT_SYMBOLS.items()])
    
    sequence = []
    initial_rows = [random.choices(symbols, weights=weights, k=3) for _ in range(3)]
    sequence.extend(initial_rows)
    
    spin_rows = 3
    for _ in range(spin_rows):
        sequence.append(random.choices(symbols, weights=weights, k=3))
    
    final_middle_line = random.choices(symbols, weights=weights, k=3)
    
    post_final_rows = [random.choices(symbols, weights=weights, k=3) for _ in range(2)]
    sequence.extend(post_final_rows)
    
    frames = []
    for i in range(len(sequence) - 2):
        frame = sequence[i:i+3]
        frames.append(frame)
    
    for frame_idx, frame in enumerate(frames):
        embed.description = await generate_slot_display(frame)
        await interaction.edit_original_response(embed=embed)
        
        if frame_idx < len(frames) - 6:
            await asyncio.sleep(0.2)
        elif frame_idx < len(frames) - 3:
            await asyncio.sleep(0.4)
        else:
            await asyncio.sleep(0.7)
    
    return frames[-1][1]

async def calculate_winnings(final_slots: List[str], bet: int) -> Tuple[int, str, int]:
    symbol_counts = {}
    for symbol in final_slots:
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

    max_count = max(symbol_counts.values())
    winning_symbol = next((s for s, cnt in symbol_counts.items() if cnt == max_count), None)
    
    if max_count >= 2:
        multiplier = SLOT_SYMBOLS[winning_symbol][1] if max_count == 3 else SLOT_SYMBOLS[winning_symbol][2]
        
        if multiplier == 0:
            return 0, winning_symbol, max_count
        
        if multiplier == 1:
            return bet, winning_symbol, max_count
        
        winnings = int(bet * multiplier)
        return winnings, winning_symbol, max_count
    
    return 0, None, 0

async def update_balance(cursor, user_id: int, amount: int):
    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', amount, user_id)

async def play_slot_machine(interaction: discord.Interaction, bet: int):
    global cursor
    try:
        embed = discord.Embed(color=int("6e6e6e", 16))
        embed.set_author(
            name=f"Слоты - {interaction.user.name}",
            icon_url=interaction.user.avatar.url
        )
        
        await interaction.response.send_message(embed=embed)
        final_slots = await animate_slots(interaction, embed)
        
        winnings, winning_symbol, symbol_count = await calculate_winnings(final_slots, bet)
        
        if winnings > 0:
            if winnings == bet:
                win_message = (
                    f"<:infor:1337141420305416252> Возврат ставки: **{winnings}** <:wwaluta:1337129761956167751>\n"
                    f"Комбинация: {winning_symbol} x{symbol_count}"
                )
            else:
                win_message = (
                    f"<:galochka:1337141373446651955> Выигрыш: **{winnings}** <:wwaluta:1337129761956167751>\n"
                    f"Комбинация: {winning_symbol} x{symbol_count}"
                )
            embed.add_field(name="Результат", value=win_message)
            
            await update_balance(cursor, interaction.user.id, winnings)
        else:
            embed.add_field(
                name="Результат",
                value="<:krestic:1337141359286550618> Вы проиграли ставку"
            )
        
        await interaction.edit_original_response(embed=embed)
    except Exception as e:
        print(f"Ошибка в слотах для пользователя {interaction.user.id}: {e}")
    finally:
        active_players.discard(interaction.user.id)

@slots_group.command(name="info", description="Показать информацию о игре в слоты")
async def slots_info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Руководство по игре в слоты",
        color=int("6e6e6e", 16)
    )

    info = (
        "<:infor:1337141420305416252> **Основная информация**\n"
        "<:smalldotwhite:1337130077808230508> Минимальная ставка: **50** <:wwaluta:1337129761956167751>\n"
        "<:smalldotwhite:1337130077808230508> Максимальная ставка: **5000** <:wwaluta:1337129761956167751>\n"
        "<:smalldotwhite:1337130077808230508> Выигрыш рассчитывается по средней линии\n\n"
        "<:smska:1337141319394529280> **Символы и множители**\n"
        f"> Алмаз <:orangediamond:1295376833688113232>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х150 от ставки (джекпот)\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х30 от ставки\n\n"
        f"> Семёрка <:slotiseven:1337178032430911488>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х55 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х16 от ставки\n\n"
        f"> Вишня <:cherry128x:1337421942529065082>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х22 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: проигрыш\n\n"
        f"> Лимон <:lemon128x:1337421957431300146>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х9 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: проигрыш\n\n"
        f"> Клубника <:strawberry128x:1337421500898082817>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х3 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: проигрыш\n\n"
    )

    embed.description = info
    await interaction.response.send_message(embed=embed, ephemeral=True)

@slots_group.command(name="bet", description="Сыграть в слоты")
@app_commands.describe(ставка="Сумма ставки (от 50 до 5000 монет)")
async def slots_bet(interaction: discord.Interaction, ставка: int):
    global cursor
    if interaction.user.id in active_players:
        error_embed = discord.Embed(
            description="<:krestic:1337141359286550618> Вы уже играете! Дождитесь окончания текущей игры.",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    if ставка < 50 or ставка > 5000:
        error_embed = discord.Embed(
            description="Ставка должна быть от 50 до 5000 монет!",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    balance_row = cursor.fetchone()
    
    if not balance_row or balance_row[0] < ставка:
        error_embed = discord.Embed(
            description="У вас недостаточно монет!",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    active_players.add(interaction.user.id)

    await update_balance(cursor, interaction.user.id, -ставка)
    
    await play_slot_machine(interaction, ставка)

# ============================================
# ROLE GROUP
# ============================================

role_group = app_commands.Group(name="role", description="Управление ролями")

async def check_role_exists(guild, role_name):
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        await cursor.execute("DELETE FROM roles WHERE role_name = $1", role_name)
        return False
    return True

def role_existence_check(func):
    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
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
    global cursor
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
    global cursor
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
            global cursor
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

        back_button = Button(label="Назад", style=ButtonStyle.secondary)
        back_button.callback = self.back_callback
        view.add_item(back_button)

        return view

    def archive_callback(self, role_info, user_id):
        async def callback(interaction: Interaction):
            global cursor
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

    async def back_callback(self, interaction: Interaction):
        global cursor
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

class ExtendRoleModal(Modal):
    def __init__(self, role_name, view, user_id):
        super().__init__(title="Продление роли")
        self.role_name = role_name
        self.view = view
        self.user_id = user_id

        self.add_item(TextInput(label="Количество дней (1 день 45 монет)", style=TextStyle.short, placeholder="Введите количество дней"))

    async def on_submit(self, interaction: Interaction):
        global cursor
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

@role_group.command(name="inventory", description="Показать активные роли и дату их истечения")
@role_existence_check
async def inventory(interaction: discord.Interaction, пользователь: discord.User = None):
    global cursor
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

    embed.description = role_list if role_list else "Нет активных ролей."
    await interaction.response.send_message(embed=embed, ephemeral=True)

@role_group.command(name="info", description="Получить информацию о роли")
@app_commands.describe(роль="Упомяните роль для проверки информации")
@role_existence_check
async def info(interaction: discord.Interaction, роль: discord.Role):
    global cursor
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
    global cursor
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
        global cursor
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
        global cursor
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
        global cursor
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

# ============================================
# ДУЭЛЬ В КОСТИ (/duel)
# ============================================

active_games: Dict[int, bool] = {}

DUEL_RAKE_PERCENT = 0.10  # 10% комиссия с банка при победе — защита от абуза переливом баланса


class DiceButton(Button):
    def __init__(self, ставка: int, author_id: int, author_name: str, target_user: discord.Member = None):
        super().__init__(label="Принять вызов", style=discord.ButtonStyle.primary)
        self.ставка = ставка
        self.author_id = author_id
        self.author_name = author_name
        self.target_user = target_user
        self.game_completed = False  # Флаг завершения игры
        self.lock = asyncio.Lock()   # Асинхронная блокировка для предотвращения гонок

    async def callback(self, interaction: discord.Interaction):
        global cursor

        if time.time() > self.view.end_time:
            await self.view.on_timeout()
            return

        async with self.lock:
            if self.game_completed:
                await interaction.response.send_message("Игра уже завершена.", ephemeral=True)
                return

            await interaction.response.defer()

            if interaction.user.id == self.author_id:
                await interaction.followup.send("<:hausted:1303112530402480128> Вы не можете принять свой собственный вызов!", ephemeral=True)
                return

            if self.target_user and interaction.user.id != self.target_user.id:
                await interaction.followup.send("<:nerd:1295095424855834635> Этот вызов предназначен для другого игрока!", ephemeral=True)
                return

            result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
            row = cursor.fetchone()
            challenger_balance = row[0] if row else None

            if challenger_balance is None or challenger_balance < self.ставка:
                await interaction.followup.send("У вас недостаточно монет для принятия вызова!", ephemeral=True)
                return

            await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2',
                                  self.ставка, interaction.user.id)

            # СОЗДАЕМ КАРТИНКУ ДУЭЛИ
            try:
                duel_image = self.create_duel_image(self.author_name, interaction.user.name)
                duel_file = discord.File(duel_image, filename="duel.png")
            except Exception as e:
                print(f"Ошибка создания картинки дуэли: {e}")
                duel_file = None

            author_roll1 = random.randint(1, 20)
            author_roll2 = random.randint(1, 20)
            author_total = author_roll1 + author_roll2

            challenger_roll1 = random.randint(1, 20)
            challenger_roll2 = random.randint(1, 20)
            challenger_total = challenger_roll1 + challenger_roll2

            result_embed = discord.Embed(color=int("6e6e6e", 16))
            result_embed.set_author(name=f"Брошен вызов в кости - {self.author_name}",
                                     icon_url=interaction.guild.get_member(self.author_id).avatar.url)

            # ДОБАВЛЯЕМ КАРТИНКУ В EMBED
            if duel_file:
                result_embed.set_image(url="attachment://duel.png")

            if author_total > challenger_total:
                winner_id = self.author_id
                winner_name = self.author_name
            elif challenger_total > author_total:
                winner_id = interaction.user.id
                winner_name = interaction.user.name
            else:
                # НИЧЬЯ — ставки возвращаются обоим
                await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                                      self.ставка, self.author_id)
                await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                                      self.ставка, interaction.user.id)

                result_note = discord.Embed(color=int("6e6e6e", 16))
                result_note.description = "<:shoked:1295095176548847717> Ничья! Ставки возвращены обоим игрокам."
                result_note.set_image(url="https://i.postimg.cc/jdv5cp6v/1111-1.png")

                view = View()
                button = DiceButton(self.ставка, self.author_id, self.author_name, self.target_user)
                button.disabled = True
                view.add_item(button)

                if duel_file:
                    await interaction.message.edit(embeds=[result_embed, result_note], view=view, attachments=[duel_file])
                else:
                    await interaction.message.edit(embeds=[result_embed, result_note], view=view)

                self.game_completed = True
                self.view.stop()
                return

            # Победителю зачисляется банк за вычетом комиссии (защита от абуза переливом баланса)
            pot = self.ставка * 2
            rake = int(pot * DUEL_RAKE_PERCENT)
            payout = pot - rake
            net_win = payout - self.ставка  # чистая прибыль победителя сверх возврата своей ставки

            await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                                  payout, winner_id)

            result_note = discord.Embed(color=int("6e6e6e", 16))
            result_note.description = (f"<:winner:1299059106060959766> Победитель: **{winner_name}**\n"
                                        f"<a:coinonrole:1298391257042784266> Чистый выигрыш: **{net_win}**")
            result_note.set_footer(text=f"комиссия: {rake}")
            result_note.set_image(url="https://i.postimg.cc/jdv5cp6v/1111-1.png")

            view = View()
            button = DiceButton(self.ставка, self.author_id, self.author_name, self.target_user)
            button.disabled = True
            view.add_item(button)

            if duel_file:
                await interaction.message.edit(embeds=[result_embed, result_note], view=view, attachments=[duel_file])
            else:
                await interaction.message.edit(embeds=[result_embed, result_note], view=view)

            self.game_completed = True
            self.view.stop()

    def create_duel_image(self, author_name, opponent_name):
        # Открываем заготовленное изображение
        image_path = "duel-1.png"

        if not os.path.exists(image_path):
            raise FileNotFoundError("Файл изображения не найден по указанному пути.")

        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)

        # Задаем параметры шрифта
        font_path = "Vito Wide Bold.ttf"
        font_size = 45

        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, font_size)
        else:
            raise FileNotFoundError("Файл шрифта не найден по указанному пути.")

        text_author = f"{author_name.lower()}"
        text_opponent = f"{opponent_name.lower()}"

        author_bbox = draw.textbbox((0, 0), text_author, font=font)
        opponent_bbox = draw.textbbox((0, 0), text_opponent, font=font)

        center_x = image.width // 2
        center_y = image.height // 2

        author_x = center_x - 650 - (author_bbox[2] - author_bbox[0]) // 2
        author_y = center_y - 300 - (author_bbox[3] - author_bbox[1]) // 2

        opponent_x = center_x + 650 - (opponent_bbox[2] - opponent_bbox[0]) // 2
        opponent_y = center_y - 300 - (opponent_bbox[3] - opponent_bbox[1]) // 2

        draw.text((author_x, author_y), text_author, font=font, fill=(255, 255, 255))
        draw.text((opponent_x, opponent_y), text_opponent, font=font, fill=(255, 255, 255))

        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr


class DiceView(View):
    def __init__(self, ставка: int, author_id: int, author_name: str, target_user: discord.Member = None):
        super().__init__(timeout=60)  # 60 секунд таймаут
        self.ставка = ставка
        self.author_id = author_id
        self.author_name = author_name
        self.target_user = target_user
        self.message = None
        self.end_time = time.time() + 60  # Фиксированное время истечения вызова
        self.add_item(DiceButton(ставка, author_id, author_name, target_user))

    async def on_timeout(self):
        global cursor

        if not self.message:
            return

        # Ставка возвращается автору, если никто не принял вызов вовремя
        await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                              self.ставка, self.author_id)

        view = View()
        button = DiceButton(self.ставка, self.author_id, self.author_name, self.target_user)
        button.disabled = True
        view.add_item(button)

        timeout_embed = discord.Embed(color=int("6e6e6e", 16))
        timeout_embed.set_author(name=f"Вызов в кости отменен - {self.author_name}",
                                  icon_url=self.message.guild.get_member(self.author_id).avatar.url)
        timeout_embed.add_field(name="Сумма вызова",
                                 value=f"**{self.ставка}** <a:coinonrole:1298391257042784266>",
                                 inline=False)

        await self.message.edit(embed=timeout_embed, view=view)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if time.time() > self.end_time:
            await self.on_timeout()
            return False
        return True


@app_commands.command(name="duel", description="Бросить вызов на игру в кости на определенную ставку")
@app_commands.describe(
    ставка="Сумма вызова (от 30 до 2500 монет)",
    противник="Игрок, которому бросаете вызов (необязательно)"
)
async def duel(interaction: discord.Interaction, ставка: int, противник: discord.Member = None):
    global cursor

    if interaction.user.id in active_games:
        await interaction.response.send_message("У вас уже есть активный вызов! Дождитесь его завершения.", ephemeral=True)
        return

    if противник and противник.id == interaction.user.id:
        await interaction.response.send_message("<:roblox:1295095451254657066> Вы не можете бросить вызов самому себе!", ephemeral=True)
        return

    if ставка < 30 or ставка > 2500:
        await interaction.response.send_message("Ставка должна быть от 30 до 2500 монет!", ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    row = cursor.fetchone()
    balance_amount = row[0] if row else None

    if balance_amount is None or balance_amount < ставка:
        await interaction.response.send_message("У вас недостаточно монет для такой ставки!", ephemeral=True)
        return

    if противник:
        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', противник.id)
        opponent_row = cursor.fetchone()
        opponent_balance = opponent_row[0] if opponent_row else None

        if opponent_balance is None or opponent_balance < ставка:
            error_embed = discord.Embed(
                description=f"У {противник.mention} недостаточно монет для принятия вызова!",
                color=int("6e6e6e", 16)
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

    await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2',
                          ставка, interaction.user.id)

    embed = discord.Embed(color=int("6e6e6e", 16))
    embed.set_author(name=f"Брошен вызов в кости - {interaction.user.name}",
                      icon_url=interaction.user.avatar.url)

    if противник:
        embed.description = f"{interaction.user.mention} бросил вызов {противник.mention} на **{ставка}** <a:coinonrole:1298391257042784266>"
    else:
        embed.description = f"{interaction.user.mention} бросил вызов на **{ставка}** <a:coinonrole:1298391257042784266>"

    view = DiceView(ставка, interaction.user.id, interaction.user.name, противник)
    active_games[interaction.user.id] = True

    await interaction.response.send_message(embed=embed, view=view)
    view.message = await interaction.original_response()

    await view.wait()
    if interaction.user.id in active_games:
        del active_games[interaction.user.id]
