"""
Общие вещи для пакета commands_economy:
- глобальный cursor (БД), устанавливается один раз из main.py через set_cursor()
- мелкие хелперы, которыми пользуются несколько подмодулей (eco, roles и т.д.)

ВАЖНО: остальные файлы пакета НЕ делают `from .common import cursor` напрямую,
потому что это привяжет их к значению cursor на момент импорта (когда оно ещё None).
Вместо этого они делают `from . import common` и внутри функций читают
`cursor = common.cursor` — так они всегда видят актуальное значение,
установленное через set_cursor().
"""

import discord

# ============================================
# ГЛОБАЛЬНЫЙ CURSOR (передается из main.py)
# ============================================
cursor = None


def set_cursor(c):
    global cursor
    cursor = c

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
