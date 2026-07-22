import io
import os

import discord
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# ============================================
# Генератор картинки профиля
# ============================================

TEMPLATE_PATH = "PlaceholderProfile2.png"
FONT_BOLD_PATH = "ProximaNova-Bold.ttf"
FONT_REGULAR_PATH = "ProximaNova-Regular.ttf"

# --------------------------------------------------------------------------
# КООРДИНАТЫ ДЛЯ РАЗМЕРА 1200x640 (откалибровано по скриншоту)
# --------------------------------------------------------------------------

DEBUG_GRID = False

# Аватар
AVATAR_CENTER = (600, 178)
AVATAR_RADIUS = 71
AVATAR_SIZE = AVATAR_RADIUS * 2
AVATAR_RING_WIDTH = 1  # толщина 1px
AVATAR_SUPERSAMPLE = 4

# Статусные цвета Discord (оригинальные)
STATUS_COLORS = {
    discord.Status.online: (57, 191, 79),      # #3FBF4F - зеленый
    discord.Status.idle: (250, 166, 26),       # #FAA61A - оранжевый
    discord.Status.dnd: (237, 66, 69),         # #ED4245 - красный
    discord.Status.offline: (116, 127, 141),   # #747F8D - серый
    discord.Status.invisible: (116, 127, 141), # #747F8D - серый
}

# Цвет по умолчанию (если статус не определён)
DEFAULT_RING_COLOR = (116, 127, 141)

# Ник под аватаром
USERNAME_CENTER_X = 600
USERNAME_CENTER_Y = 285
USERNAME_MAX_WIDTH = 240
USERNAME_FONT_SIZE = 32

# "На сервере с ..." - цвет #b6b6b6, размер 12pt
JOINED_CENTER_X = 600
JOINED_CENTER_Y = 369
JOINED_MAX_WIDTH = 250
JOINED_FONT_SIZE = 12
JOINED_COLOR = (182, 182, 182)

# ЛЕВАЯ КОЛОНКА
LEFT_VALUE_FONT_SIZE = 22
LEFT_COLUMN_CENTER_X = 271

# Брачный профиль
LEFT_X = 160
LEFT_MAX_WIDTH = 235
MARRIAGE_CENTER_Y = 155

# "вместе N дней"
MARRIAGE_DAYS_CENTER_X = LEFT_COLUMN_CENTER_X
MARRIAGE_DAYS_CENTER_Y = 194
MARRIAGE_DAYS_MAX_WIDTH = 110
MARRIAGE_DAYS_FONT_SIZE = 14

# Личная роль
ROLE_VALUE_CENTER_X = 305
ROLE_VALUE_CENTER_Y = 311
ROLE_VALUE_MAX_WIDTH = 170

# Личная комната
ROOM_VALUE_CENTER_X = LEFT_COLUMN_CENTER_X
ROOM_VALUE_CENTER_Y = 466
ROOM_VALUE_MAX_WIDTH = 250

# ПРАВАЯ КОЛОНКА - подняты на 1 пиксель
RIGHT_BLOCK_RIGHT_EDGE = 1040
RIGHT_MAX_WIDTH = 170
RIGHT_VALUE_FONT_SIZE = 22

RIGHT_VALUES_CENTER_Y = {
    "balance": 127,   # было 128, подняли на 1
    "voice": 193,     # было 194, подняли на 1
    "messages": 260,  # было 261, подняли на 1
    "rank": 323,      # было 324, подняли на 1
}

# Цвет текста
TEXT_COLOR = (255, 255, 255)

_BASIC_LAYOUT = getattr(ImageFont, "Layout", None)
_BASIC_LAYOUT = _BASIC_LAYOUT.BASIC if _BASIC_LAYOUT else getattr(ImageFont, "LAYOUT_BASIC", 0)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Шрифт '{path}' не найден. Положи файл '{path}' в корень проекта."
        )
    return ImageFont.truetype(path, size, layout_engine=_BASIC_LAYOUT)


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "..."
    truncated = text
    while truncated and draw.textlength(truncated + ellipsis, font=font) > max_width:
        truncated = truncated[:-1]

    return (truncated.rstrip() + ellipsis) if truncated else ellipsis


def _draw_centered_text(draw, center_x: int, center_y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    draw.text((center_x, center_y), text, font=font, fill=fill, anchor="mm")


def _draw_left_aligned_text(draw, x: int, center_y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    draw.text((x, center_y), text, font=font, fill=fill, anchor="lm")


def _draw_right_aligned_text(draw, right_x: int, center_y: int, text: str, font, max_width: int, fill=TEXT_COLOR) -> None:
    text = _truncate_to_width(draw, text, font, max_width)
    draw.text((right_x, center_y), text, font=font, fill=fill, anchor="rm")


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


def _get_status_color(member: discord.Member) -> tuple:
    """Возвращает цвет статуса пользователя в формате RGB"""
    try:
        status = member.status
        return STATUS_COLORS.get(status, DEFAULT_RING_COLOR)
    except Exception:
        return DEFAULT_RING_COLOR


def _draw_avatar_ring(base: Image.Image, center: tuple, radius: int, width: int, color: tuple) -> None:
    """Рисует кольцо вокруг аватара с заданным цветом"""
    ss = AVATAR_SUPERSAMPLE
    box_size = radius * 2 + width * 2

    ring_big = Image.new("RGBA", (box_size * ss, box_size * ss), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring_big)
    c = box_size * ss / 2
    r = radius * ss
    
    # Рисуем кольцо
    ring_draw.ellipse(
        (c - r, c - r, c - r + 2 * r, c - r + 2 * r),
        outline=(*color, 255),
        width=width * ss,
    )
    ring = ring_big.resize((box_size, box_size), Image.LANCZOS)

    paste_pos = (int(center[0] - box_size / 2), int(center[1] - box_size / 2))
    base.alpha_composite(ring, paste_pos)


def _draw_debug_grid(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    w, h = image.size
    
    for y in range(0, h, 50):
        color = (255, 0, 0) if y % 100 == 0 else (0, 255, 255)
        draw.line([(0, y), (w, y)], fill=color, width=1)
        if y % 100 == 0:
            draw.text((5, y + 2), f"y={y}", fill=(255, 255, 0), font=ImageFont.load_default())
    
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

    if not active_role_names:
        return None

    result = await cursor.execute('SELECT displayed_role FROM user_profiles WHERE user_id = $1', member.id)
    row = cursor.fetchone()
    chosen_name = row[0] if row else None

    if chosen_name and chosen_name in active_role_names:
        role = discord.utils.get(guild.roles, name=chosen_name)
        if role:
            return role

    role = discord.utils.get(guild.roles, name=active_role_names[0])
    return role


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

    # Пытаемся получить выбранную комнату, если колонка существует
    try:
        result = await cursor.execute('SELECT displayed_room FROM user_profiles WHERE user_id = $1', member.id)
        row = cursor.fetchone()
        chosen_name = row[0] if row else None

        if chosen_name and chosen_name in room_names:
            return chosen_name
    except Exception:
        pass

    def _parse_date(value):
        try:
            if not value:
                return datetime.max
            return datetime.fromisoformat(value)
        except (ValueError, TypeError, AttributeError):
            try:
                return datetime.strptime(value, '%d.%m.%Y')
            except (ValueError, TypeError):
                return datetime.max

    rooms_sorted = sorted(rooms, key=lambda item: _parse_date(item[1]))
    return rooms_sorted[0][0]


def _shorten_name(name: str, max_len: int = 12) -> str:
    """Сокращает имя до указанной длины, если оно слишком длинное"""
    if len(name) <= max_len:
        return name
    return name[:max_len] + "…"


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

    name1 = _shorten_name(member.display_name, 12)
    name2 = _shorten_name(partner.display_name, 12)
    
    # Используем & вместо сердечка
    couple_line = f"{name1} & {name2}"
    
    MAX_VISIBLE_LEN = 27
    if len(couple_line) > MAX_VISIBLE_LEN:
        excess = len(couple_line) - MAX_VISIBLE_LEN
        cut1 = min(len(name1) - 1, excess // 2 + (excess % 2))
        cut2 = min(len(name2) - 1, excess // 2)
        name1 = name1[:len(name1) - cut1] + "…" if cut1 > 0 else name1
        name2 = name2[:len(name2) - cut2] + "…" if cut2 > 0 else name2
        couple_line = f"{name1} & {name2}"

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
    font_joined = _load_font(FONT_REGULAR_PATH, JOINED_FONT_SIZE)
    font_left_value = _load_font(FONT_BOLD_PATH, LEFT_VALUE_FONT_SIZE)
    font_marriage_days = _load_font(FONT_BOLD_PATH, MARRIAGE_DAYS_FONT_SIZE)
    font_right_value = _load_font(FONT_BOLD_PATH, RIGHT_VALUE_FONT_SIZE)

    # Аватар
    avatar = await _fetch_circular_avatar(member, AVATAR_SIZE)
    avatar_pos = (AVATAR_CENTER[0] - AVATAR_RADIUS, AVATAR_CENTER[1] - AVATAR_RADIUS)
    base.alpha_composite(avatar, avatar_pos)
    
    # Рисуем обводку цветом статуса пользователя (толщина 1px)
    status_color = _get_status_color(member)
    _draw_avatar_ring(base, AVATAR_CENTER, AVATAR_RADIUS, AVATAR_RING_WIDTH, status_color)

    # Ник
    _draw_centered_text(draw, USERNAME_CENTER_X, USERNAME_CENTER_Y, member.display_name, font_username, USERNAME_MAX_WIDTH)

    # Дата на сервере
    _draw_centered_text(draw, JOINED_CENTER_X, JOINED_CENTER_Y, f"На сервере с {joined_str}г", font_joined, JOINED_MAX_WIDTH, fill=JOINED_COLOR)

    # ===== ЛЕВАЯ КОЛОНКА =====
    # Брачный профиль
    if couple_line:
        text_width = draw.textlength(couple_line, font=font_left_value)
        
        if text_width <= LEFT_MAX_WIDTH:
            # Если текст короткий - центрируем по центру прямоугольника
            _draw_centered_text(draw, LEFT_COLUMN_CENTER_X, MARRIAGE_CENTER_Y, couple_line, font_left_value, LEFT_MAX_WIDTH)
        else:
            # Если текст длинный - выравниваем по левому краю под буквой "Б"
            truncated = _truncate_to_width(draw, couple_line, font_left_value, LEFT_MAX_WIDTH)
            draw.text((LEFT_X, MARRIAGE_CENTER_Y), truncated, font=font_left_value, fill=TEXT_COLOR, anchor="lm")
        
        if days_line:
            _draw_centered_text(draw, MARRIAGE_DAYS_CENTER_X, MARRIAGE_DAYS_CENTER_Y, days_line, font_marriage_days, MARRIAGE_DAYS_MAX_WIDTH)
    else:
        # "Отсутствует" - центрируем по центру прямоугольника
        _draw_centered_text(draw, LEFT_COLUMN_CENTER_X, MARRIAGE_CENTER_Y, "Отсутствует", font_left_value, LEFT_MAX_WIDTH)

    # Личная роль
    _draw_centered_text(draw, ROLE_VALUE_CENTER_X, ROLE_VALUE_CENTER_Y, displayed_role.name if displayed_role else "Отсутствует", font_left_value, ROLE_VALUE_MAX_WIDTH)

    # Личная комната
    _draw_centered_text(draw, ROOM_VALUE_CENTER_X, ROOM_VALUE_CENTER_Y, room_name if room_name else "Отсутствует", font_left_value, ROOM_VALUE_MAX_WIDTH)

    # ===== ПРАВАЯ КОЛОНКА =====
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_CENTER_Y["balance"], f"{balance}", font_right_value, RIGHT_MAX_WIDTH)
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_CENTER_Y["voice"], f"{int(voice_hours)}ч", font_right_value, RIGHT_MAX_WIDTH)
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_CENTER_Y["messages"], f"{messages_count}", font_right_value, RIGHT_MAX_WIDTH)
    _draw_right_aligned_text(draw, RIGHT_BLOCK_RIGHT_EDGE, RIGHT_VALUES_CENTER_Y["rank"], f"#{rank}", font_right_value, RIGHT_MAX_WIDTH)

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
