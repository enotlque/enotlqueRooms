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
    """Атомарно списывает amount с баланса user_id, если средств достаточно.

    Раньше это делалось в 2 шага (SELECT баланса -> проверка в Python ->
    UPDATE), между которыми был зазор: два одновременных вызова для одного
    и того же пользователя могли оба пройти проверку по устаревшему
    значению баланса и оба списать деньги, уводя баланс в минус. Теперь
    проверка "хватает ли денег" встроена прямо в WHERE самого UPDATE —
    списание либо происходит целиком атомарно на стороне БД, либо не
    происходит вовсе, гонка невозможна в принципе.
    """
    await cursor.execute(
        'UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2 AND balance >= $1 RETURNING balance',
        amount, user_id
    )
    return cursor.fetchone() is not None
