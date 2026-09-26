import discord
from discord import app_commands, Interaction, File
from datetime import datetime
import asyncio
from typing import Optional
import io
import os
import json
from PIL import Image, ImageDraw, ImageFont

# ================== НАСТРОЙКИ (легко менять) ==================
UPDATE_INTERVAL = 60 * 15      # секунд (для теста). Боевой режим: 15 * 60
MAX_EVENTS = 7
MAX_EXPIRING = 7
MAX_STORED_EVENTS = 30       # сколько событий максимум хранить в БД (старые удаляются)

# Путь к баннеру (PNG с прозрачностью, круг выходит за края)
# Ищем в нескольких местах, чтобы не зависеть от cwd
_BANNER_CANDIDATES = [
    "monitoring.png",
    "monitoring_banner.png",
    os.path.join(os.path.dirname(__file__), "monitoring.png"),
    os.path.join(os.path.dirname(__file__), "monitoring_banner.png"),
    "monitoring (1).png",
    os.path.join("attachments", "monitoring.png"),
    os.path.join("attachments", "monitoring (1).png"),
]
BANNER_PATH = next((p for p in _BANNER_CANDIDATES if os.path.exists(p)), "monitoring.png")

# Шрифты (те же, что в профиле)
FONT_BOLD_PATH = "ProximaNova-Bold.ttf"
FONT_REGULAR_PATH = "ProximaNova-Regular.ttf"

# Координаты чисел на баннере (2555x1041)
# Подобраны под текущий макет: сразу после двоеточий
NUM_POSITIONS = {
    "marriages": (780, 310),   # Брачных рум:
    "rooms":     (780, 470),   # Личных рум:
    "roles":     (780, 630),   # Личных ролей:
}
NUM_FONT_SIZE = 56
NUM_COLOR = (255, 255, 255)

# Discord Components V2
IS_COMPONENTS_V2 = 1 << 15  # 32768

# ==============================================================

cursor = None
bot_instance = None
_update_task = None
_is_running = False

_BASIC_LAYOUT = getattr(ImageFont, "Layout", None)
_BASIC_LAYOUT = _BASIC_LAYOUT.BASIC if _BASIC_LAYOUT else getattr(ImageFont, "LAYOUT_BASIC", 0)


def set_cursor(c):
    global cursor
    cursor = c


def set_bot(b):
    global bot_instance
    bot_instance = b


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        # fallback
        return ImageFont.load_default()
    return ImageFont.truetype(path, size, layout_engine=_BASIC_LAYOUT)


# ================== ЛОГИРОВАНИЕ СОБЫТИЙ ==================

async def log_event(
    event_type: str,
    user_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    item_name: Optional[str] = None,
    item_id: Optional[int] = None,
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


# ================== ГЕНЕРАЦИЯ БАННЕРА С ЧИСЛАМИ ==================

async def _get_counts() -> dict:
    """Возвращает актуальные счётчики."""
    await cursor.execute("SELECT COUNT(*) FROM marriages")
    marriages_count = (cursor.fetchone() or [0])[0] or 0

    await cursor.execute("SELECT COUNT(*) FROM roles WHERE archived = 0")
    roles_count = (cursor.fetchone() or [0])[0] or 0

    await cursor.execute("SELECT COUNT(*) FROM room_leadership")
    rooms_count = (cursor.fetchone() or [0])[0] or 0

    return {
        "marriages": marriages_count,
        "rooms": rooms_count,
        "roles": roles_count,
    }


def _render_banner(counts: dict) -> io.BytesIO:
    """Рисует числа на баннере и возвращает PNG с прозрачностью."""
    if not os.path.exists(BANNER_PATH):
        raise FileNotFoundError(
            f"Баннер не найден: {BANNER_PATH}. "
            f"Положи файл monitoring_banner.png рядом с ботом."
        )

    base = Image.open(BANNER_PATH).convert("RGBA")
    draw = ImageDraw.Draw(base)

    try:
        font = _load_font(FONT_BOLD_PATH, NUM_FONT_SIZE)
    except Exception:
        font = ImageFont.load_default()

    for key, pos in NUM_POSITIONS.items():
        value = str(counts.get(key, 0))
        # Рисуем с лёгкой тенью для читаемости
        shadow_offset = 2
        draw.text(
            (pos[0] + shadow_offset, pos[1] + shadow_offset),
            value,
            font=font,
            fill=(0, 0, 0, 160),
            anchor="lm",
        )
        draw.text(
            pos,
            value,
            font=font,
            fill=NUM_COLOR,
            anchor="lm",
        )

    buf = io.BytesIO()
    base.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ================== ПОСТРОЕНИЕ ТЕКСТА ЖУРНАЛА ==================

def _ts(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


def _parse_role_room_date(s: str):
    if not s or s == "-":
        return None
    try:
        return datetime.strptime(s, "%d.%m.%Y в %Hч %Mм %Sс")
    except Exception:
        return None


def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


async def _build_journal_text() -> str:
    """Собирает текст «Недавние события» + «Скоро истекает»."""
    now = datetime.now()

    # ----- Недавние события -----
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

    seen_role_transfers = set()
    filtered_rows = []
    for row in rows:
        event_type, user_id, target_user_id, item_name, item_id, amount, created_at = row
        if event_type == "role_transfer":
            key = item_id if item_id is not None else item_name
            if key in seen_role_transfers:
                continue
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

    await cursor.execute(
        "SELECT leader_id, room_name, expiration_date, voice_channel_id FROM room_leadership WHERE expiration_date IS NOT NULL"
    )
    for row in (cursor.fetchall() or []):
        leader_id, room_name, exp_str, voice_id = row
        exp = _parse_role_room_date(exp_str)
        if exp and exp > now:
            ch = f"<#{voice_id}>" if voice_id else f"**{room_name}**"
            expiring.append((exp, f"• Комната {ch} у <@{leader_id}> — {_ts(exp)}"))

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

    text = (
        f"**Недавние события**\n{events_block}\n\n"
        f"**Скоро истекает**\n{expiring_block}\n\n"
        f"-# Обновлено • интервал {UPDATE_INTERVAL} сек"
    )
    return text


# ================== ПОСТРОЕНИЕ COMPONENTS V2 ==================

async def build_container_payload() -> tuple[list, File]:
    """
    Возвращает (components, file) для отправки/редактирования.
    Картинка идёт первой (Media Gallery), потом текст журнала.
    accent_color не указываем → левая полоска сливается с фоном.
    """
    try:
        counts = await _get_counts()
        banner_buf = _render_banner(counts)
        journal = await _build_journal_text()

        file = File(banner_buf, filename="banner.png")

        # Container (type 17) без accent_color — полоска не видна / цвет фона
        components = [
            {
                "type": 17,  # Container
                "components": [
                    {
                        "type": 12,  # Media Gallery
                        "items": [
                            {
                                "media": {"url": "attachment://banner.png"}
                            }
                        ]
                    },
                    {
                        "type": 10,  # Text Display
                        "content": journal
                    }
                ]
            }
        ]
        return components, file
    except Exception as e:
        print(f"[monitoring] Ошибка build_container_payload: {e}")
        import traceback
        traceback.print_exc()
        raise


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
                await cursor.execute(
                    "UPDATE server_life_config SET enabled = FALSE, message_id = NULL"
                )
                _is_running = False
                break

            components, file = await build_container_payload()
            try:
                await message.edit(
                    attachments=[file],
                    components=components,
                    flags=IS_COMPONENTS_V2,
                )
            except TypeError:
                # Библиотека не поддерживает components/flags в edit
                await message.edit(attachments=[file])
                print("[monitoring] edit() не принял components — обновил только картинку")

        except Exception as e:
            print(f"[monitoring] Ошибка обновления: {e}")
            import traceback
            traceback.print_exc()

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

    try:
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

        components, file = await build_container_payload()

        # ===== Отправка Components V2 =====
        # Пробуем стандартный способ. Если библиотека ругается на
        # keyword 'components' — используем прямой HTTP-запрос.
        msg = None
        try:
            msg = await канал.send(
                files=[file],
                components=components,
                flags=IS_COMPONENTS_V2,
            )
        except TypeError as te:
            print(f"[monitoring] send() не принял components/flags: {te}")
            print("[monitoring] Переходим на прямой HTTP-запрос...")

            # Прямой запрос к API Discord
            route = discord.http.Route(
                "POST", "/channels/{channel_id}/messages", channel_id=канал.id
            )

            # Готовим multipart form с JSON + файлом
            form = []
            payload = {
                "flags": IS_COMPONENTS_V2,
                "components": components,
            }
            form.append({"name": "payload_json", "value": json.dumps(payload)})

            # Файл
            file.fp.seek(0)
            form.append({
                "name": "files[0]",
                "value": file.fp,
                "filename": file.filename,
                "content_type": "image/png",
            })

            data = await bot_instance.http.request(route, form=form)
            msg = канал._state.create_message(channel=канал, data=data)

        if msg is None:
            raise RuntimeError("Не удалось отправить сообщение")

        try:
            await msg.pin(reason="Жизнь сервера")
        except Exception:
            pass

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
    except Exception as e:
        print(f"[monitoring] Ошибка в /monitoring on: {e}")
        import traceback
        traceback.print_exc()
        try:
            await interaction.followup.send(
                f"❌ Ошибка при создании:\n```{type(e).__name__}: {e}```",
                ephemeral=True,
            )
        except Exception:
            pass


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
