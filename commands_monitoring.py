import io
import os

import discord
from discord import app_commands, Interaction
from datetime import datetime
import asyncio
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# ================== НАСТРОЙКИ (легко менять) ==================
UPDATE_INTERVAL = 60 * 15      # секунд (для теста). Боевой режим: 15 * 60
MAX_EVENTS = 7
MAX_EXPIRING = 7
MAX_STORED_EVENTS = 30       # сколько событий максимум хранить в БД (старые удаляются)

COUNTER_EMOJIS = {
    "marriages": "<:monheart:1553055709401321652>",
    "roles": "<a:monorb:1553057449190236300>",
    "rooms": "<:monroom:1553055708075786330>",
}

# Цвет эмбеда (как в остальном боте)
EMBED_COLOR = 0x6e6e6e

# Формат дат ролей/комнат
ROLE_ROOM_DATE_FORMAT = "%d.%m.%Y в %Hч %Mм %Sс"

# ================== КАРТИНКА СО СЧЁТЧИКАМИ (monitoring.png) ==================
# Шаблон лежит в той же папке, что и PlaceholderProfile2.png (корень проекта)
MONITORING_TEMPLATE_PATH = "monitoring.png"
FONT_BOLD_PATH = "ProximaNova-Bold.ttf"  # тот же шрифт, что и в профиле

# Координаты значений (откалиброваны по monitoring.png, точка — левый край
# числа, по вертикали — центр строки, anchor="lm")
COUNTER_VALUE_FONT_SIZE = 60
COUNTER_VALUE_COLOR = (255, 255, 255)
COUNTER_VALUE_POSITIONS = {
    "marriages": (900, 428),   # строка "Брачных рум:"
    "rooms": (830, 585),       # строка "Личных рум:"
    "roles": (935, 723),       # строка "Личных ролей:"
}
# ==============================================================

cursor = None
bot_instance = None
_update_task = None
_is_running = False


def set_cursor(c):
    global cursor
    cursor = c


def set_bot(b):
    global bot_instance
    bot_instance = b


# ================== ЛОГИРОВАНИЕ СОБЫТИЙ ==================

async def log_event(
    event_type: str,
    user_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    item_name: Optional[str] = None,
    item_id: Optional[int] = None,          # role_id или voice_channel_id
    amount: Optional[int] = None,
):
    """Пишет событие в economy_events. Вызывать после успешного действия.
    После записи удаляет самые старые, чтобы в таблице было не больше MAX_STORED_EVENTS.
    """
    if cursor is None:
        return
    try:
        await cursor.execute(
            """
            INSERT INTO economy_events
                (event_type, user_id, target_user_id, item_name, item_id, amount, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            event_type,
            user_id,
            target_user_id,
            item_name,
            item_id,
            amount,
            datetime.utcnow(),
        )
        # Чистим хвост: оставляем только последние MAX_STORED_EVENTS записей
        await cursor.execute(
            """
            DELETE FROM economy_events
            WHERE id NOT IN (
                SELECT id FROM economy_events
                ORDER BY created_at DESC
                LIMIT $1
            )
            """,
            MAX_STORED_EVENTS,
        )
    except Exception as e:
        print(f"[monitoring] Ошибка логирования события {event_type}: {e}")


# ================== ПОСТРОЕНИЕ ЭМБЕДА ==================

def _ts(dt: datetime) -> str:
    """Discord relative timestamp"""
    return f"<t:{int(dt.timestamp())}:R>"


def _parse_role_room_date(s: str):
    if not s or s == "-":
        return None
    try:
        return datetime.strptime(s, ROLE_ROOM_DATE_FORMAT)
    except Exception:
        return None


def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ================== ГЕНЕРАЦИЯ КАРТИНКИ ==================

_BASIC_LAYOUT = getattr(ImageFont, "Layout", None)
_BASIC_LAYOUT = _BASIC_LAYOUT.BASIC if _BASIC_LAYOUT else getattr(ImageFont, "LAYOUT_BASIC", 0)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Шрифт '{path}' не найден. Положи файл '{path}' в корень проекта."
        )
    return ImageFont.truetype(path, size, layout_engine=_BASIC_LAYOUT)


def create_monitoring_image(marriages_count: int, rooms_count: int, roles_count: int) -> io.BytesIO:
    """Рисует счётчики поверх monitoring.png и возвращает PNG в буфере."""
    if not os.path.exists(MONITORING_TEMPLATE_PATH):
        raise FileNotFoundError(
            f"Шаблон '{MONITORING_TEMPLATE_PATH}' не найден. "
            f"Положи файл '{MONITORING_TEMPLATE_PATH}' в корень проекта."
        )

    base = Image.open(MONITORING_TEMPLATE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(base)
    font = _load_font(FONT_BOLD_PATH, COUNTER_VALUE_FONT_SIZE)

    values = {
        "marriages": marriages_count,
        "rooms": rooms_count,
        "roles": roles_count,
    }
    for key, (x, y) in COUNTER_VALUE_POSITIONS.items():
        draw.text((x, y), str(values.get(key, 0)), font=font, fill=COUNTER_VALUE_COLOR, anchor="lm")

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ================== ПОСТРОЕНИЕ CV2-БЛОКА ==================

async def build_monitoring_view():
    """Возвращает (LayoutView, discord.File) — картинка со счётчиками сверху,
    текстовые блоки (события/истечения) снизу, всё внутри одного Container."""
    now = datetime.now()

    # ----- Счётчики -----
    await cursor.execute("SELECT COUNT(*) FROM marriages")
    marriages_count = (cursor.fetchone() or [0])[0] or 0

    await cursor.execute("SELECT COUNT(*) FROM roles WHERE archived = 0")
    roles_count = (cursor.fetchone() or [0])[0] or 0

    await cursor.execute("SELECT COUNT(*) FROM room_leadership")
    rooms_count = (cursor.fetchone() or [0])[0] or 0

    # (счётчики теперь рисуются прямо на monitoring.png, см. create_monitoring_image)

    # ----- Недавние события -----
    # Берём с запасом, чтобы после сворачивания передач одной роли хватило на MAX_EVENTS
    await cursor.execute(
        """
        SELECT event_type, user_id, target_user_id, item_name, item_id, amount, created_at
        FROM economy_events
        ORDER BY created_at DESC
        LIMIT $1
        """,
        MAX_EVENTS * 5,
    )
    rows = cursor.fetchall() or []

    # Сворачиваем role_transfer одной и той же роли: оставляем только последнюю
    # (rows уже отсортированы от новых к старым → первое вхождение = актуальное)
    seen_role_transfers = set()
    filtered_rows = []
    for row in rows:
        event_type, user_id, target_user_id, item_name, item_id, amount, created_at = row
        if event_type == "role_transfer":
            key = item_id if item_id is not None else item_name
            if key in seen_role_transfers:
                continue  # более старая передача этой же роли — пропускаем
            seen_role_transfers.add(key)
        filtered_rows.append(row)
        if len(filtered_rows) >= MAX_EVENTS:
            break

    events_lines = []
    for event_type, user_id, target_user_id, item_name, item_id, amount, created_at in filtered_rows:
        ts = _ts(created_at) if isinstance(created_at, datetime) else ""

        u = f"<@{user_id}>" if user_id else "?"
        t = f"<@{target_user_id}>" if target_user_id else "?"

        if event_type == "role_create":
            role_m = f"<@&{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• {u} создал роль {role_m} — {ts}"
        elif event_type == "role_transfer":
            role_m = f"<@&{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• {u} передал роль {role_m} → {t} — {ts}"
        elif event_type == "role_extend":
            role_m = f"<@&{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• {u} продлил роль {role_m} — {ts}"
        elif event_type == "role_expire":
            role_m = f"<@&{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• Роль {role_m} у {u} истекла — {ts}"
        elif event_type == "room_create":
            ch = f"<#{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• {u} создал комнату {ch} — {ts}"
        elif event_type == "room_extend":
            ch = f"<#{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• {u} продлил комнату {ch} — {ts}"
        elif event_type == "room_expire":
            ch = f"<#{item_id}>" if item_id else f"**{item_name or '?'}**"
            line = f"• Комната {ch} у {u} истекла — {ts}"
        elif event_type == "marriage":
            line = f"• {u} и {t} заключили брак — {ts}"
        elif event_type == "marriage_renew":
            line = f"• {u} и {t} продлили брак — {ts}"
        elif event_type == "marriage_expire":
            line = f"• Брак {u} и {t} истёк — {ts}"
        else:
            line = f"• {event_type} — {ts}"

        events_lines.append(line)

    events_block = "\n".join(events_lines) if events_lines else "*Пока тихо*"

    # ----- Скоро истекает -----
    expiring = []

    # Роли
    await cursor.execute(
        "SELECT role_name, id_owner_now, expiration_date FROM roles WHERE archived = 0 AND expiration_date IS NOT NULL AND expiration_date != '-'"
    )
    for row in (cursor.fetchall() or []):
        role_name, owner_id, exp_str = row
        exp = _parse_role_room_date(exp_str)
        if exp and exp > now:
            role_id = None
            if bot_instance:
                for g in bot_instance.guilds:
                    r = discord.utils.get(g.roles, name=role_name)
                    if r:
                        role_id = r.id
                        break
            role_m = f"<@&{role_id}>" if role_id else f"**{role_name}**"
            expiring.append((exp, f"• Роль {role_m} у <@{owner_id}> — {_ts(exp)}"))

    # Комнаты
    await cursor.execute(
        "SELECT leader_id, room_name, expiration_date, voice_channel_id FROM room_leadership WHERE expiration_date IS NOT NULL"
    )
    for row in (cursor.fetchall() or []):
        leader_id, room_name, exp_str, voice_id = row
        exp = _parse_role_room_date(exp_str)
        if exp and exp > now:
            ch = f"<#{voice_id}>" if voice_id else f"**{room_name}**"
            expiring.append((exp, f"• Комната {ch} у <@{leader_id}> — {_ts(exp)}"))

    # Браки
    await cursor.execute(
        "SELECT user1_id, user2_id, expires_at FROM marriages WHERE expires_at IS NOT NULL"
    )
    for row in (cursor.fetchall() or []):
        u1, u2, exp_str = row
        exp = _parse_iso(exp_str)
        if exp and exp > now:
            expiring.append((exp, f"• Брак <@{u1}> и <@{u2}> — {_ts(exp)}"))

    expiring.sort(key=lambda x: x[0])
    expiring_lines = [line for _, line in expiring[:MAX_EXPIRING]]
    expiring_block = "\n".join(expiring_lines) if expiring_lines else "*Ближайших истечений нет*"

    # ----- Картинка со счётчиками (marriages/rooms/roles на monitoring.png) -----
    image_buffer = create_monitoring_image(marriages_count, rooms_count, roles_count)
    image_file = discord.File(image_buffer, filename="monitoring.png")

    # ----- Текстовые блоки под картинкой -----
    events_text = f"**Недавние события**\n{events_block}"
    expiring_text = f"**Скоро истекает**\n{expiring_block}"
    footer_text = f"-# Обновлено • интервал {UPDATE_INTERVAL} сек • {_ts(datetime.utcnow())}"

    container = discord.ui.Container(accent_color=discord.Colour(EMBED_COLOR))
    container.add_item(
        discord.ui.MediaGallery(discord.MediaGalleryItem(media="attachment://monitoring.png"))
    )
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(events_text))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(expiring_text))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(footer_text))

    view = discord.ui.LayoutView()
    view.add_item(container)

    return view, image_file


# ================== ФОНОВАЯ ЗАДАЧА ==================

async def _update_loop():
    global _is_running
    while _is_running:
        try:
            await cursor.execute(
                "SELECT channel_id, message_id FROM server_life_config WHERE enabled = TRUE LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                await asyncio.sleep(UPDATE_INTERVAL)
                continue

            channel_id, message_id = row
            channel = bot_instance.get_channel(channel_id) if bot_instance else None
            if not channel:
                await asyncio.sleep(UPDATE_INTERVAL)
                continue

            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.HTTPException):
                # сообщение удалили вручную — выключаем
                await cursor.execute(
                    "UPDATE server_life_config SET enabled = FALSE, message_id = NULL"
                )
                _is_running = False
                break

            view, image_file = await build_monitoring_view()
            await message.edit(view=view, attachments=[image_file])

        except Exception as e:
            print(f"[monitoring] Ошибка обновления: {e}")

        await asyncio.sleep(UPDATE_INTERVAL)


def start_monitoring_task():
    global _update_task, _is_running
    if _is_running:
        return
    _is_running = True
    _update_task = asyncio.create_task(_update_loop())
    print("✅ Задача «Жизнь сервера» запущена")


def stop_monitoring_task():
    global _update_task, _is_running
    _is_running = False
    if _update_task and not _update_task.done():
        _update_task.cancel()
    _update_task = None
    print("⏹ Задача «Жизнь сервера» остановлена")


# ================== КОМАНДЫ ==================

monitoring_group = app_commands.Group(
    name="monitoring", description="Управление каналом «Жизнь сервера»"
)


@monitoring_group.command(name="on", description="Включить канал «Жизнь сервера»")
@app_commands.describe(канал="Текстовый канал, в котором будет жить сообщение")
async def monitoring_on(interaction: Interaction, канал: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)

    # Проверяем, не включено ли уже
    await cursor.execute(
        "SELECT enabled, message_id FROM server_life_config LIMIT 1"
    )
    row = cursor.fetchone()
    if row and row[0]:
        await interaction.followup.send(
            "Система уже включена. Сначала выключи через `/monitoring off`.",
            ephemeral=True,
        )
        return

    # Создаём сообщение
    view, image_file = await build_monitoring_view()
    msg = await канал.send(view=view, files=[image_file])
    try:
        await msg.pin(reason="Жизнь сервера")
    except Exception:
        pass  # нет прав на закрепление — не критично

    # Сохраняем в БД
    await cursor.execute("DELETE FROM server_life_config")
    await cursor.execute(
        """
        INSERT INTO server_life_config (enabled, channel_id, message_id)
        VALUES (TRUE, $1, $2)
        """,
        канал.id,
        msg.id,
    )

    start_monitoring_task()
    await interaction.followup.send(
        f"Готово! Сообщение создано в {канал.mention} и будет обновляться каждые {UPDATE_INTERVAL} сек.",
        ephemeral=True,
    )


@monitoring_group.command(name="off", description="Выключить канал «Жизнь сервера»")
async def monitoring_off(interaction: Interaction):
    await interaction.response.defer(ephemeral=True)

    await cursor.execute(
        "SELECT channel_id, message_id FROM server_life_config WHERE enabled = TRUE LIMIT 1"
    )
    row = cursor.fetchone()

    stop_monitoring_task()

    if row:
        channel_id, message_id = row
        channel = bot_instance.get_channel(channel_id) if bot_instance else None
        if channel and message_id:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.delete()
            except Exception:
                pass

    await cursor.execute("DELETE FROM server_life_config")
    await interaction.followup.send(
        "Система «Жизнь сервера» полностью выключена. Сообщение удалено.",
        ephemeral=True,
    )


def setup_monitoring(bot, db_cursor):
    """Вызывать из main.py (регистрация команды)."""
    set_bot(bot)
    set_cursor(db_cursor)
    bot.tree.add_command(monitoring_group)


async def resume_monitoring_if_needed():
    """Вызывать из on_ready — возобновляет задачу, если мониторинг был включён."""
    try:
        await asyncio.sleep(2)
        await cursor.execute(
            "SELECT enabled FROM server_life_config WHERE enabled = TRUE LIMIT 1"
        )
        if cursor.fetchone():
            start_monitoring_task()
            print("✅ Мониторинг «Жизнь сервера» возобновлён после рестарта")
    except Exception as e:
        print(f"[monitoring] Не удалось возобновить задачу: {e}")
