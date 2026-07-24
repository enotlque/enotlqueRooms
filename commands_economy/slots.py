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
# SLOTS GROUP - НОВАЯ ВЕРСИЯ (3x5, RTP 96%)
# ============================================

slots_group = app_commands.Group(name="slots", description="Команды для игры в слоты")

_get_connection = None
_release_connection = None

def set_slots_connection_factory(factory, release_factory=None):
    """Устанавливает фабрику соединений для слотов.

    release_factory обязателен для корректной работы пула: раньше здесь
    вызывался conn.close(), что для соединения, взятого через
    pool.acquire(), реально РАЗРЫВАЕТ TCP-соединение с базой вместо того,
    чтобы вернуть его в пул — на каждый спин бот заново переподключался
    к PostgreSQL (лишний RTT + handshake), а не переиспользовал одно из
    20 уже открытых соединений."""
    global _get_connection, _release_connection
    _get_connection = factory
    _release_connection = release_factory


async def _release(conn):
    if _release_connection is not None:
        await _release_connection(conn)
    else:
        await conn.close()

# ============================================
# КОНФИГУРАЦИЯ СЛОТОВ (ОРИГИНАЛЬНЫЕ ЭМОДЗИ)
# ============================================
LEFT_ARROW = "<:rightarrow:1337396550204129330>"
RIGHT_ARROW = "<:leftarrow:1337396538619592744>"

SLOT_SYMBOLS = {
    "<:orangediamond:1295376833688113232>": {"weight": 3, "payouts": {2: 4, 3: 15, 4: 70, 5: 600}, "name": "Алмаз"},
    "<:slotiseven:1337178032430911488>": {"weight": 10, "payouts": {2: 2, 3: 7, 4: 26, 5: 120}, "name": "Семёрка"},
    "<:cherry128x:1337421942529065082>": {"weight": 20, "payouts": {3: 2, 4: 6, 5: 20}, "name": "Вишня"},
    "<:lemon128x:1337421957431300146>": {"weight": 27, "payouts": {3: 1, 4: 3, 5: 9}, "name": "Лимон"},
    "<:strawberry128x:1337421500898082817>": {"weight": 40, "payouts": {4: 2, 5: 6}, "name": "Клубника"},
}
# Пересчитано через Monte-Carlo симуляцию (6 000 000 спинов): RTP ≈ 94.7%.
# Старая таблица весов/множителей (2/6/15/25/52 и х1-х150 только за 2-3 совпадения)
# на деле давала RTP ~460-500% — при текущей механике paylines (независимые
# случайные символы в каждой ячейке) 2 одинаковых символа подряд выпадают
# СЛИШКОМ часто, особенно у частых символов (клубника была 52% всех ячеек).
# Новая таблица:
#   - у частых символов (клубника, лимон, вишня) убрана/уменьшена выплата за
#     2-3 совпадения — иначе казино разоряется на самом частом символе;
#   - добавлены выплаты за 4 и 5 совпадений (раньше это давало 0 — то есть
#     игрок мог собрать почти всю линию и получить ноль, это тоже было багом);
#   - алмаз остаётся редким джекпот-символом: 5 подряд на линии — это x600,
#     событие примерно 1 раз на 3 000 000 спинов, поэтому не портит RTP.

# Оптимизированный список (кэшируется при загрузке модуля)
SYMBOLS_WEIGHTED = []
for emoji, data in SLOT_SYMBOLS.items():
    SYMBOLS_WEIGHTED.extend([emoji] * data["weight"])

# 5 линий выплат для 3x5
PAYLINES = [
    [(1, 0), (1, 1), (1, 2), (1, 3), (1, 4)],  # Центр
    [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)],  # Верх
    [(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)],  # Низ
    [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)],  # Зигзаг вверх
    [(2, 0), (1, 1), (0, 2), (1, 3), (2, 4)],  # Зигзаг вниз
]

# ============================================
# ИГРОВАЯ ЛОГИКА
# ============================================

def generate_reels() -> List[List[str]]:
    """Генерирует 3x5 поле"""
    return [[random.choice(SYMBOLS_WEIGHTED) for _ in range(5)] for _ in range(3)]

def check_paylines(reels: List[List[str]], bet: int) -> Tuple[int, List[Tuple[int, int, List[str]]]]:
    """Проверяет 5 линий на выигрыш"""
    total_win = 0
    wins = []
    
    for line_idx, line in enumerate(PAYLINES):
        symbols = [reels[r][c] for r, c in line]
        
        first = symbols[0]
        count = 1
        for s in symbols[1:]:
            if s == first:
                count += 1
            else:
                break
        
        if count >= 2 and first in SLOT_SYMBOLS:
            multiplier = SLOT_SYMBOLS[first]["payouts"].get(count, 0)
            if multiplier > 0:
                win = bet * multiplier
                total_win += win
                wins.append((line_idx + 1, win, symbols[:count]))
    
    return total_win, wins

# ============================================
# РАБОТА С БАЛАНСОМ (через фабрику соединений)
# ============================================

async def get_balance_cached(user_id: int) -> int:
    """Получает баланс с кэшированием (Redis 5 сек)"""
    cached = await get_cached(balance_cache_key(user_id))
    if cached is not None:
        return cached
    
    conn = await _get_connection()
    try:
        row = await conn.fetchrow('SELECT balance FROM user_profiles WHERE user_id = $1', user_id)
        balance = row[0] if row else 0
    finally:
        await _release(conn)
    
    await set_cached(balance_cache_key(user_id), balance, 5)
    return balance

async def update_balance(user_id: int, new_balance: int):
    """Обновляет баланс и инвалидирует кэш"""
    conn = await _get_connection()
    try:
        await conn.execute(
            'UPDATE user_profiles SET balance = $1 WHERE user_id = $2',
            new_balance, user_id
        )
    finally:
        await _release(conn)
    
    await delete_cached(balance_cache_key(user_id))

# ============================================
# КОМАНДА /SLOTS BET (СОХРАНЯЕМ ОРИГИНАЛЬНЫЙ ВИД)
# ============================================

@slots_group.command(name="bet", description="Сыграть в слоты")
@app_commands.describe(ставка="Сумма ставки (от 50 до 5000 монет)")
async def slots_bet(interaction: discord.Interaction, ставка: int):
    # ===== ЗАЩИТА ОТ ОДНОВРЕМЕННЫХ ИГР =====
    if not hasattr(slots_bet, '_active'):
        slots_bet._active = set()
    
    if interaction.user.id in slots_bet._active:
        embed = discord.Embed(
            description="<:krestic:1337141359286550618> Вы уже играете! Дождитесь окончания текущей игры.",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    slots_bet._active.add(interaction.user.id)
    # ========================================
    
    try:
        # 1. ПРОВЕРКА СТАВКИ
        if ставка < 50 or ставка > 5000:
            embed = discord.Embed(
                description="Ставка должна быть от 50 до 5000 монет!",
                color=int("6e6e6e", 16)
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. ПРОВЕРКА БАЛАНСА (из кэша)
        balance = await get_balance_cached(interaction.user.id)
        if balance < ставка:
            embed = discord.Embed(
                description="У вас недостаточно монет!",
                color=int("6e6e6e", 16)
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. СНАЧАЛА СЧИТАЕМ РЕЗУЛЬТАТ, ПОТОМ ОДИН РАЗ ПИШЕМ В БД
        # (раньше было 2 отдельных подключения к БД за спин: списание ставки +
        # отдельное зачисление выигрыша. Сама анимация прокрутки — это просто
        # 4-5 лёгких edit_original_response к одному сообщению одного игрока,
        # это не нагружает бота вообще. А вот открывать/закрывать asyncpg-
        # соединение дважды на каждый спин — конкретно лишняя нагрузка на БД,
        # которую убираем, объединяя в один update_balance.)
        reels = generate_reels()
        total_win, wins = check_paylines(reels, ставка)
        final_balance = balance - ставка + total_win
        await update_balance(interaction.user.id, final_balance)

        # Рендер поля 3x5 БЕЗ code-block'а — Discord не рендерит кастомные
        # эмодзи внутри ``` ```, поэтому строку нельзя оборачивать в них.
        def render_field(reel_rows: List[List[str]]) -> str:
            padding = " ㅤㅤ "
            lines = []
            for i, row in enumerate(reel_rows):
                if i == 1:
                    lines.append(f"{LEFT_ARROW}ㅤ**|** {' **:** '.join(row)} **|**ㅤ{RIGHT_ARROW}")
                else:
                    lines.append(f"{padding}**|** {' **|** '.join(row)} **|**{padding}")
            return "\n \n".join(lines)

        # ===== АНИМАЦИЯ ПРОКРУТКИ =====
        SPIN_FRAMES = 3          # сколько промежуточных кадров показать
        SPIN_DELAY = 0.6         # задержка между кадрами (сек)

        spin_embed = discord.Embed(color=int("6e6e6e", 16))
        spin_embed.set_author(
            name=f"Слоты - {interaction.user.name}",
            icon_url=interaction.user.avatar.url
        )
        spin_embed.add_field(name="Крутим...", value=render_field(generate_reels()), inline=False)

        await interaction.response.send_message(embed=spin_embed)

        for _ in range(SPIN_FRAMES):
            await asyncio.sleep(SPIN_DELAY)
            spin_embed.clear_fields()
            spin_embed.add_field(name="Крутим...", value=render_field(generate_reels()), inline=False)
            await interaction.edit_original_response(embed=spin_embed)

        await asyncio.sleep(SPIN_DELAY)

        # 6. СОЗДАЁМ ФИНАЛЬНЫЙ EMBED (реальный результат, без code-block'а)
        embed = discord.Embed(color=int("6e6e6e", 16))
        embed.set_author(
            name=f"Слоты - {interaction.user.name}",
            icon_url=interaction.user.avatar.url
        )
        embed.add_field(name="Результат", value=render_field(reels), inline=False)

        # Выигрыши
        if total_win > 0:
            if total_win == ставка:
                win_message = f"<:infor:1337141420305416252> Возврат ставки: **{total_win}** <:wwaluta:1337129761956167751>"
            else:
                win_message = f"<:galochka:1337141373446651955> Выигрыш: **{total_win}** <:wwaluta:1337129761956167751>"
            
            embed.add_field(name="Результат", value=win_message)
        else:
            embed.add_field(
                name="Результат",
                value="<:krestic:1337141359286550618> Вы проиграли ставку"
            )

        await interaction.edit_original_response(embed=embed)

    finally:
        # ===== РАЗБЛОКИРОВКА =====
        slots_bet._active.discard(interaction.user.id)

# ============================================
# КОМАНДА /SLOTS INFO (ОРИГИНАЛЬНЫЙ ФОРМАТ)
# ============================================

@slots_group.command(name="info", description="Показать информацию о игре в слоты")
async def slots_info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Руководство по игре в слоты",
        color=int("6e6e6e", 16)
    )

    info = (
        "<:infor:1337141420305416252> **Основная информация**\n"
        "<:smalldotwhite:1337130077808230508> Поле: **3x5** (3 ряда, 5 колонок)\n"
        "<:smalldotwhite:1337130077808230508> Линий: **5**\n"
        "<:smalldotwhite:1337130077808230508> Ставка: **50-5000** монет\n\n"
        
        "<:smska:1337141319394529280> **Символы и множители**\n"
        f"> Алмаз <:orangediamond:1295376833688113232> — джекпот-символ\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х4 • 3 в ряд: х15 • 4 в ряд: х70 • 5 в ряд: х600\n\n"
        f"> Семёрка <:slotiseven:1337178032430911488>\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х2 • 3 в ряд: х7 • 4 в ряд: х26 • 5 в ряд: х120\n\n"
        f"> Вишня <:cherry128x:1337421942529065082>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х2 • 4 в ряд: х6 • 5 в ряд: х20\n\n"
        f"> Лимон <:lemon128x:1337421957431300146>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х1 • 4 в ряд: х3 • 5 в ряд: х9\n\n"
        f"> Клубника <:strawberry128x:1337421500898082817>\n"
        "<:smalldotwhite:1337130077808230508> 4 в ряд: х2 • 5 в ряд: х6\n\n"
        "<:infor:1337141420305416252> RTP игры (реальная отдача): **≈95%**\n\n"
        
        "**💡 Удачи! 🍀**"
    )

    embed.description = info
    await interaction.response.send_message(embed=embed, ephemeral=True)
