import io
import os

import discord
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# ============================================
# Генератор картинки профиля
# ============================================

TEMPLATE_PATH = "PlaceholderProfile1.png"
FONT_BOLD_PATH = "ProximaNova-Bold.ttf"

# --------------------------------------------------------------------------
# КООРДИНАТЫ ДЛЯ РАЗМЕРА 1200x640 (откалибровано по скриншоту)
# --------------------------------------------------------------------------

DEBUG_GRID = True  # Пока оставлю для финальной калибровки

# Аватар
AVATAR_CENTER = (600, 160)  # чуть выше
AVATAR_RADIUS = 70
AVATAR_SIZE = AVATAR_RADIUS * 2
AVATAR_RING_WIDTH = 2
AVATAR_RING_COLOR = (255, 255, 255, 200)
AVATAR_SUPERSAMPLE = 4

# Ник под аватаром
USERNAME_CENTER_X = 600
USERNAME_Y = 275
USERNAME_MAX_WIDTH = 350
USERNAME_FONT_SIZE = 32

# "На сервере с ..." 
JOINED_CENTER_X = 600
JOINED_Y = 500
JOINED_MAX_WIDTH = 350
JOINED_FONT_SIZE = 16

# ЛЕВАЯ КОЛОНКА (Брачный профиль / Личная роль / Личная комната)
LEFT_X = 140
LEFT_MAX_WIDTH = 250
LEFT_VALUE_FONT_SIZE = 22

# Значения левой колонки (то что должно отображаться)
LEFT_VALUES_Y = {
    "marriage": 185,    # "enotlque ♥ ..." или "Отсутствует"
    "role": 310,        # "gotwelvevmbgfldsfcvsq"
    "room": 435,        # "testtesttesttesttesttest..."
}

# Блок брака — дополнительная строка "вместе N дней"
MARRIAGE_CENTER_X = LEFT_X + LEFT_MAX_WIDTH // 2
MARRIAGE_DAYS_Y_OFFSET = 30  # смещение под "вместе 2 дней"

# ПРАВАЯ КОЛОНКА (Баланс / В войсе / Сообщения / Место в топе)
RIGHT_BLOCK_RIGHT_EDGE = 1060
RIGHT_MAX_WIDTH = 200
RIGHT_VALUE_FONT_SIZE = 22

# Значения правой колонки
RIGHT_VALUES_Y = {
    "balance": 185,     # "1520" (пример)
    "voice": 310,       # "144" (часы)
    "messages": 435,    # "15" (сообщения)
    "rank": 560,        # "#1"
}

# Цвет текста
TEXT_COLOR = (255, 255, 255)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Шрифт '{path}' не найден. Положи файл ProximaNova-Bold.ttf в корень проекта."
        )
    return ImageFont.truetype(path, size)


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "..."
    truncated = text
    while truncated and draw.textlength(truncated + ellipsis, font=font) > max_width:
        truncated = truncated[:-1]

    return (truncated.rstrip() + ellipsis) if truncated else ellipsis


def _measure(draw, text: str, font) -> tuple:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[0]


def _draw_centered_text(draw, center_x: int, y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    width, left_bearing = _measure(draw, text, font)
    draw.text((center_x - width / 2 - left_bearing, y), text, font=font, fill=fill)


def _draw_left_aligned_text(draw, x: int, y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_right_aligned_text(draw, right_x: int, y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    width, left_bearing = _measure(draw, text, font)
    draw.text((right_x - width - left_bearing, y), text, font=font, fill=fill)


async def _fetch_circular_avatar(member: discord.abc.User, diameter: int) -> Image.Image:
    asset = member.display_avatar.replace(size=256, format="png")
    avatar_bytes = await asset.read()

    big_diameter = diameter * AVATAR_SUPERSAMPLE

    avatar_big = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar_big = avatar_big.resize((big_diameter, big_diameter), Image.LANCZOS)

    mask_big = Image.new("L", (big_diameter, big_diameter), 0)
    mask_draw = ImageDraw.Draw(mask_big)
    mask_draw.ellipse((0, 0, big_diameter, big_diameter), fill=255)
    avatar_big.putalpha(mask_big)

    avatar = avatar_big.resize((diameter, diameter), Image.LANCZOS)
    return avatar


def _draw_avatar_ring(base: Image.Image, center: tuple, radius: int, width: int, color: tuple) -> None:
    ss = AVATAR_SUPERSAMPLE
    box_size = radius * 2 + width * 2

    ring_big = Image.new("RGBA", (box_size * ss, box_size * ss), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring_big)
    c = box_size * ss / 2
    r = radius * ss
    ring_draw.ellipse(
        (c - r, c - r, c - r + 2 * r, c - r + 2 * r),
        outline=color,
        width=width * ss,
    )
    ring = ring_big.resize((box_size, box_size), Image.LANCZOS)

    paste_pos = (int(center[0] - box_size / 2), int(center[1] - box_size / 2))
    base.alpha_composite(ring, paste_pos)


def _draw_debug_grid(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    w, h = image.size
    
    # Горизонтальные линии
    for y in range(0, h, 50):
        color = (255, 0, 0) if y % 100 == 0 else (0, 255, 255)
        draw.line([(0, y), (w, y)], fill=color, width=1)
        if y % 100 == 0:
            draw.text((5, y + 2), f"y={y}", fill=(255, 255, 0), font=ImageFont.load_default())
    
    # Вертикальные линии
    for x in range(0, w, 50):
        color = (255, 0, 0) if x % 100 == 0 else (0, 255, 0)
        draw.line([(x, 0), (x, h)], fill=color, width=1)
        if x % 100 == 0:
            draw.text((x + 2, 5), f"x={x}", fill=(255, 255, 0), font=ImageFont.load_default())


async def get_active_role_names(cursor, member: discord.Member) -> list:
    result = await cursor.execute(
        'SELECT role_name, archived FROM roles WHERE id_owner_now = $1',
        member.id
    )
    rows = cursor.fetchall()
    return [name for name, archived in rows if archived != 1]


async def _get_displayed_role(cursor, member: discord.Member, guild: discord.Guild):
    active_role_names = await get_active_role_names(cursor, member)

    result = await cursor.execute('SELECT displayed_role FROM user_profiles WHERE user_id = $1', member.id)
    row = cursor.fetchone()
    chosen_name = row[0] if row else None

    if not chosen_name or chosen_name not in active_role_names:
        import random
        chosen_name = random.choice(active_role_names) if active_role_names else None

    if not chosen_name:
        return None

    return discord.utils.get(guild.roles, name=chosen_name)


async def get_member_room_options(cursor, member: discord.Member) -> list:
    result = await cursor.execute('SELECT room_name, role_id, creation_date FROM room_leadership')
    rows = cursor.fetchall()

    member_role_ids = {r.id for r in member.roles}
    return [(room_name, creation_date) for room_name, role_id, creation_date in rows if role_id in member_role_ids]


async def _get_room_name(cursor, member: discord.Member):
    rooms = await get_member_room_options(cursor, member)
    if not rooms:
        return None

    room_names = [name for name, _ in rooms]
    if len(rooms) == 1:
        return room_names[0]

    result = await cursor.execute('SELECT displayed_room FROM user_profiles WHERE user_id = $1', member.id)
    row = cursor.fetchone()
    chosen_name = row[0] if row else None

    if chosen_name and chosen_name in room_names:
        return chosen_name

    def _parse_date(value):
        try:
            return datetime.strptime(value, '%d.%m.%Y')
        except (ValueError, TypeError):
            return datetime.max

    rooms_sorted = sorted(rooms, key=lambda item: _parse_date(item[1]))
    return rooms_sorted[0][0]


async def _get_marriage_display(cursor, member: discord.Member, guild: discord.Guild):
    result = await cursor.execute(
        'SELECT user1_id, user2_id, created_at FROM marriages WHERE user1_id = $1 OR user2_id = $1',
        member.id
    )
    row = cursor.fetchone()
    if not row:
        return None, None

    user1_id, user2_id, created_at = row
    partner_id = user2_id if user1_id == member.id else user1_id

    partner = guild.get_member(partner_id)
    if partner is None:
        try:
            partner = await guild.fetch_member(partner_id)
        except discord.NotFound:
            return None, None

    couple_line = f"{member.display_name} \u2665 {partner.display_name}"

    days_line = None
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at)
            days_together = max((datetime.now() - created_dt).days, 0)
            days_line = f"вместе {days_together} дней"
        except (ValueError, TypeError):
            days_line = None

    return couple_line, days_line


async def create_profile_image(cursor, member: discord.Member, guild: discord.Guild) -> io.BytesIO:
    # Статистика
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
        voice_hours = round(float(voice_hours or 0), 2)
        messages_count = messages_count or 0

    # Ранг
    rank_result = await cursor.execute(
        'SELECT COUNT(*) + 1 FROM user_profiles WHERE voice_hours > (SELECT voice_hours FROM user_profiles WHERE user_id = $1)',
        member.id
    )
    rank_row = rank_result and cursor.fetchone()
    rank = rank_row[0] if rank_row else 1

    # Роль, комната, брак
    displayed_role = await _get_displayed_role(cursor, member, guild)
    room_name = await _get_room_name(cursor, member)
    couple_line, days_line = await _get_marriage_display(cursor, member, guild)

    # Дата вступления
    joined_str = "—"
    if member.joined_at:
        joined_str = member.joined_at.strftime("%d.%m.%Y")

    # Сборка картинки
    base = Image.open(TEMPLATE_PATH).convert("RGBA")
    
    if base.size != (1200, 640):
        base = base.resize((1200, 640), Image.LANCZOS)

    if DEBUG_GRID:
        _draw_debug_grid(base)

    draw = ImageDraw.Draw(base)

    # Шрифты
    font_username = _load_font(FONT_BOLD_PATH, USERNAME_FONT_SIZE)
    font_joined = _load_font(FONT_BOLD_PATH, JOINED_FONT_SIZE)
    font_left_value = _load_font(FONT_BOLD_PATH, LEFT_VALUE_FONT_SIZE)
    font_right_value = _load_font(FONT_BOLD_PATH, RIGHT_VALUE_FONT_SIZE)

    # Аватар
    avatar = await _fetch_circular_avatar(member, AVATAR_SIZE)
    avatar_pos = (AVATAR_CENTER[0] - AVATAR_RADIUS, AVATAR_CENTER[1] - AVATAR_RADIUS)
    base.alpha_composite(avatar, avatar_pos)
    _draw_avatar_ring(base, AVATAR_CENTER, AVATAR_RADIUS, AVATAR_RING_WIDTH, AVATAR_RING_COLOR)

    # Ник
    _draw_centered_text(draw, USERNAME_CENTER_X, USERNAME_Y, member.display_name, font_username, USERNAME_MAX_WIDTH)

    # Дата на сервере
    _draw_centered_text(draw, JOINED_CENTER_X, JOINED_Y, f"На сервере с {joined_str}г", font_joined, JOINED_MAX_WIDTH)

    # ===== ЛЕВАЯ КОЛОНКА =====
    # Брачный профиль (центрируется)
    if couple_line:
        _draw_centered_text(draw, MARRIAGE_CENTER_X, LEFT_VALUES_Y["marriage"], couple_line, font_left_value, LEFT_MAX_WIDTH)
        if days_line:
            _draw_centered_text(draw, MARRIAGE_CENTER_X, LEFT_VALUES_Y["marriage"] + MARRIAGE_DAYS_Y_OFFSET, days_line, font_left_value, LEFT_MAX_WIDTH)
    else:
        _draw_centered_text(draw, MARRIAGE_CENTER_X, LEFT_VALUES_Y["marriage"], "Отсутствует", font_left_value, LEFT_MAX_WIDTH)

    # Личная роль (по левому краю)
    _draw_left_aligned_text(draw, LEFT_X, LEFT_VALUES_Y["role"], displayed_role.name if displayed_role else "Отсутствует", font_left_value, LEFT_MAX_WIDTH)

    # Личная комната (по левому краю)
    _draw_left_aligned_text(draw, LEFT_X, LEFT_VALUES_Y["room"], room_name if room_name else "Отсутствует", font_left_value, LEFT_MAX_WIDTH)

    # ===== ПРАВАЯ КОЛОНКА =====
    # Баланс
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["balance"], f"{balance}", font_right_value, RIGHT_MAX_WIDTH)
    
    # В войсе
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["voice"], f"{int(voice_hours)}ч", font_right_value, RIGHT_MAX_WIDTH)
    
    # Сообщения
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["messages"], f"{messages_count}", font_right_value, RIGHT_MAX_WIDTH)
    
    # Место в топе
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_Y["rank"], f"#{rank}", font_right_value, RIGHT_MAX_WIDTH)

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
