import io
import os
import random
import discord
from PIL import Image, ImageDraw, ImageFont

# ============================================
# Генератор картинки профиля (используется командой /me из commands_economy.py)
# ============================================

TEMPLATE_PATH = "PlaceholderProfile.png"
FONT_BOLD_PATH = "ProximaNova-Bold.ttf"
FONT_REGULAR_PATH = "ProximaNova-Regular.ttf"

MALE_ROLE_ID = 1126893214536827050
FEMALE_ROLE_ID = 1126893217405739090

# --------------------------------------------------------------------------
# КООРДИНАТЫ РАЗМЕТКИ (подобраны по PlaceholderProfile.png, 1672x941).
# Если версия картинки поменяется/сдвинется — поправь константы ниже.
# Для калибровки есть DEBUG_GRID = True (см. ниже).
# --------------------------------------------------------------------------

DEBUG_GRID = False  # True -> поверх картинки рисуется сетка 50px для калибровки координат

# Аватар (круглая рамка по центру сверху)
AVATAR_CENTER = (825, 375)
AVATAR_RADIUS = 122          # итоговый радиус вставляемого аватара (чуть меньше рамки, чтобы не наезжать на обводку)
AVATAR_SIZE = AVATAR_RADIUS * 2

# Ник под аватаром
USERNAME_CENTER_X = 825
USERNAME_Y = 515
USERNAME_MAX_WIDTH = 430
USERNAME_FONT_SIZE = 34

# "На сервере с ..." — приподнято и мельче, чем ник
JOINED_CENTER_X = 825
JOINED_Y = 695
JOINED_MAX_WIDTH = 460
JOINED_FONT_SIZE = 18

# Левая колонка (Личная роль / Личная комната / Статус брака).
# Значения центрируются по центру прямоугольника (как ник) и стоят ниже,
# ближе к нижнему краю каждого блока.
LEFT_CENTER_X = 340
LEFT_MAX_WIDTH = 380
LEFT_VALUE_FONT_SIZE = 26
LEFT_VALUES_Y = {
    "role": 335,
    "room": 535,
    "marriage": 735,
}

# Правая колонка (Баланс / В войсе / Сообщения / Место в топе).
# Значения стоят на той же строке, что и подпись (на уровне линии под ней),
# прижаты к правому краю блока.
RIGHT_BLOCK_RIGHT_EDGE = 1520
RIGHT_MAX_WIDTH = 200
RIGHT_VALUE_FONT_SIZE = 26
RIGHT_VALUES_Y = {
    "balance": 235,
    "voice": 395,
    "messages": 555,
    "rank": 715,
}

TEXT_COLOR = (255, 255, 255)
MUTED_COLOR = (190, 190, 190)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Шрифт '{path}' не найден рядом с ботом. Положи файл ProximaNova (Bold/Regular) в корень проекта."
        )
    return ImageFont.truetype(path, size)


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Обрезает текст под ширину max_width, добавляя '...' если не помещается."""
    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "..."
    truncated = text
    while truncated and draw.textlength(truncated + ellipsis, font=font) > max_width:
        truncated = truncated[:-1]

    return (truncated.rstrip() + ellipsis) if truncated else ellipsis


def _draw_centered_text(draw, center_x: int, y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    width = draw.textlength(text, font=font)
    draw.text((center_x - width / 2, y), text, font=font, fill=fill)


def _draw_right_aligned_text(draw, right_x: int, y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    width = draw.textlength(text, font=font)
    draw.text((right_x - width, y), text, font=font, fill=fill)


async def _fetch_circular_avatar(member: discord.abc.User, diameter: int) -> Image.Image:
    asset = member.display_avatar.replace(size=256, format="png")
    avatar_bytes = await asset.read()

    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar = avatar.resize((diameter, diameter), Image.LANCZOS)

    mask = Image.new("L", (diameter, diameter), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, diameter, diameter), fill=255)
    avatar.putalpha(mask)

    return avatar


def _draw_debug_grid(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    w, h = image.size
    for x in range(0, w, 50):
        color = (255, 0, 0) if x % 100 == 0 else (0, 255, 0)
        draw.line([(x, 0), (x, h)], fill=color, width=1)
    for y in range(0, h, 50):
        color = (255, 0, 0) if y % 100 == 0 else (0, 255, 255)
        draw.line([(0, y), (w, y)], fill=color, width=1)


async def _get_active_role_names(cursor, member: discord.Member) -> list:
    """Роли пользователя, которые не архивированы (доступны для отображения)."""
    result = await cursor.execute(
        'SELECT role_name, archived FROM roles WHERE id_owner_now = $1',
        member.id
    )
    rows = cursor.fetchall()
    return [name for name, archived in rows if archived != 1]


async def _get_displayed_role(cursor, member: discord.Member, guild: discord.Guild):
    """Роль, выбранная через /role inventory -> «Отобразить».
    Если ничего не выбрано (или выбранная роль больше не активна) — берётся случайная
    из доступных ролей пользователя. Если активных ролей нет вовсе — None."""
    active_role_names = await _get_active_role_names(cursor, member)

    result = await cursor.execute('SELECT displayed_role FROM user_profiles WHERE user_id = $1', member.id)
    row = cursor.fetchone()
    chosen_name = row[0] if row else None

    if not chosen_name or chosen_name not in active_role_names:
        chosen_name = random.choice(active_role_names) if active_role_names else None

    if not chosen_name:
        return None

    return discord.utils.get(guild.roles, name=chosen_name)


async def _get_room_name(cursor, member: discord.Member):
    result = await cursor.execute('SELECT room_name FROM room_leadership WHERE leader_id = $1', member.id)
    row = cursor.fetchone()
    return row[0] if row else None


async def _get_marriage_text(cursor, member: discord.Member, guild: discord.Guild) -> str:
    result = await cursor.execute(
        'SELECT user1_id, user2_id FROM marriages WHERE user1_id = $1 OR user2_id = $1',
        member.id
    )
    row = cursor.fetchone()
    if not row:
        return "Отсутствует"

    partner_id = row[1] if row[0] == member.id else row[0]
    partner = guild.get_member(partner_id)
    if partner is None:
        try:
            partner = await guild.fetch_member(partner_id)
        except discord.NotFound:
            return "Отсутствует"

    owner_role_ids = {r.id for r in member.roles}
    if FEMALE_ROLE_ID in owner_role_ids:
        return f"Замужем за {partner.display_name}"
    elif MALE_ROLE_ID in owner_role_ids:
        return f"Женат на {partner.display_name}"
    return f"В браке с {partner.display_name}"


async def create_profile_image(cursor, member: discord.Member, guild: discord.Guild) -> io.BytesIO:
    # --- статистика из user_profiles ---
    result = await cursor.execute(
        'SELECT balance, voice_hours, messages_count FROM user_profiles WHERE user_id = $1',
        member.id
    )
    row = cursor.fetchone()
    if not row:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', member.id, 0)
        balance, voice_hours, messages_count = 0, 0, 0
    else:
        balance, voice_hours, messages_count = row
        voice_hours = float(voice_hours or 0)
        messages_count = messages_count or 0

    # --- место в топе по часам в войсе ---
    rank_result = await cursor.execute(
        'SELECT COUNT(*) + 1 FROM user_profiles WHERE voice_hours > (SELECT voice_hours FROM user_profiles WHERE user_id = $1)',
        member.id
    )
    rank_row = rank_result and cursor.fetchone()
    rank = rank_row[0] if rank_row else 1

    # --- личная роль, личная комната, брак ---
    displayed_role = await _get_displayed_role(cursor, member, guild)
    room_name = await _get_room_name(cursor, member)
    marriage_text = await _get_marriage_text(cursor, member, guild)

    # --- дата вступления ---
    joined_str = "—"
    if member.joined_at:
        joined_str = member.joined_at.strftime("%d.%m.%Y")

    # --- сборка картинки ---
    base = Image.open(TEMPLATE_PATH).convert("RGBA")

    if DEBUG_GRID:
        _draw_debug_grid(base)

    draw = ImageDraw.Draw(base)

    font_username = _load_font(FONT_BOLD_PATH, USERNAME_FONT_SIZE)
    font_joined = _load_font(FONT_REGULAR_PATH, JOINED_FONT_SIZE)
    font_left_value = _load_font(FONT_REGULAR_PATH, LEFT_VALUE_FONT_SIZE)
    font_right_value = _load_font(FONT_REGULAR_PATH, RIGHT_VALUE_FONT_SIZE)

    # аватар
    avatar = await _fetch_circular_avatar(member, AVATAR_SIZE)
    avatar_pos = (AVATAR_CENTER[0] - AVATAR_RADIUS, AVATAR_CENTER[1] - AVATAR_RADIUS)
    base.paste(avatar, avatar_pos, avatar)

    # ник
    _draw_centered_text(draw, USERNAME_CENTER_X, USERNAME_Y, member.display_name, font_username, USERNAME_MAX_WIDTH)

    # дата на сервере
    _draw_centered_text(draw, JOINED_CENTER_X, JOINED_Y, f"На сервере с {joined_str}г", font_joined, JOINED_MAX_WIDTH, fill=MUTED_COLOR)

    # левая колонка - центрируется в прямоугольнике, ниже подписи
    _draw_centered_text(draw, LEFT_CENTER_X, LEFT_VALUES_Y["role"], displayed_role.name if displayed_role else "Отсутствует", font_left_value, LEFT_MAX_WIDTH)
    _draw_centered_text(draw, LEFT_CENTER_X, LEFT_VALUES_Y["room"], room_name if room_name else "Отсутствует", font_left_value, LEFT_MAX_WIDTH)
    _draw_centered_text(draw, LEFT_CENTER_X, LEFT_VALUES_Y["marriage"], marriage_text, font_left_value, LEFT_MAX_WIDTH)

    # правая колонка - на строке подписи, прижато к правому краю блока
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["balance"], f"{balance}", font_right_value, RIGHT_MAX_WIDTH)
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["voice"], f"{voice_hours:.1f}ч", font_right_value, RIGHT_MAX_WIDTH)
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["messages"], f"{messages_count}", font_right_value, RIGHT_MAX_WIDTH)
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["rank"], f"#{rank}", font_right_value, RIGHT_MAX_WIDTH)

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
