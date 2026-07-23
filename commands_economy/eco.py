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
# ECO GROUP - Экономические команды
# ============================================

eco_group = app_commands.Group(name="eco", description="Экономические команды")

@eco_group.command(name="balance", description="Проверить баланс")
@app_commands.describe(пользователь="Проверить баланс пользователя")
async def balance(interaction: discord.Interaction, пользователь: discord.Member = None):
    пользователь = пользователь or interaction.user
    cursor = common.cursor
    
    # ПРОВЕРЯЕМ КЕШ
    cached = await get_cached(balance_cache_key(пользователь.id))
    if cached is not None:
        await interaction.response.send_message(embed=create_embed(
            description="",
            color="#696969",
            author_name=f"Баланс - {пользователь.display_name}",
            author_icon_url=пользователь.avatar.url
        ).add_field(name="Монет", value=f"```\n{cached}\n```", inline=False), ephemeral=True)
        return
    
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    row = cursor.fetchone()
    
    if row:
        balance_amount = row[0]
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)', 
                           пользователь.id, 0, None)
        balance_amount = 0
    
    # СОХРАНЯЕМ В КЕШ
    await set_cached(balance_cache_key(пользователь.id), balance_amount, 60)
    
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
    cursor = common.cursor

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
        
        await set_cached(balance_cache_key(interaction.user.id), new_balance, 60)
        await set_cached(profile_cache_key(interaction.user.id), None, 1)  # Инвалидация профиля
        
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
        
        await set_cached(balance_cache_key(interaction.user.id), BONUS_AMOUNT, 60)
        await set_cached(profile_cache_key(interaction.user.id), None, 1)  # Инвалидация профиля
        
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
    cursor = common.cursor

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
        
        await set_cached(balance_cache_key(interaction.user.id), new_balance, 60)
        await set_cached(profile_cache_key(interaction.user.id), None, 1)  # Инвалидация профиля
        
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
        
        await set_cached(balance_cache_key(interaction.user.id), earned, 60)
        await set_cached(profile_cache_key(interaction.user.id), None, 1)  # Инвалидация профиля
        
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
    cursor = common.cursor
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
        new_balance = recipient_balance[0] + сумма
        await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', сумма, пользователь.id)
    else:
        new_balance = сумма
        await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', пользователь.id, сумма)

    from cache import set_cached, delete_cached, balance_cache_key, profile_cache_key
    await set_cached(balance_cache_key(пользователь.id), new_balance, 60)
    await delete_cached(profile_cache_key(пользователь.id))

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
    cursor = common.cursor
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

    new_balance = user_balance[0] - сумма
    await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', сумма, пользователь.id)

    from cache import set_cached, delete_cached, balance_cache_key, profile_cache_key
    await set_cached(balance_cache_key(пользователь.id), new_balance, 60)
    await delete_cached(profile_cache_key(пользователь.id))

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
    cursor = common.cursor
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

        # ⬇️⬇️⬇️ ЭТИ СТРОЧКИ ДОБАВИТЬ ⬇️⬇️⬇️
        from cache import set_cached, delete_cached, balance_cache_key, profile_cache_key
        await set_cached(balance_cache_key(interaction.user.id), sender_balance[0] - сумма, 60)
        await set_cached(balance_cache_key(пользователь.id), (recipient_balance[0] if recipient_balance else 0) + total_amount, 60)
        await delete_cached(profile_cache_key(interaction.user.id))
        await delete_cached(profile_cache_key(пользователь.id))

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

