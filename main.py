import discord
from discord.ext import commands
from discord import Intents
from config import TOKEN
import asyncio
import os
import threading
import asyncpg
from asyncpg import Pool
from rate_limiter import safe_discord_call, rate_limiter
from redis_client import init_redis

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
db_pool: Pool = None

# === ВЕБ-СЕРВЕР ДЛЯ RENDER ===
from flask import Flask

app = Flask(__name__)

@app.route('/')
def health():
    return "I'm alive!", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()
# === КОНЕЦ БЛОКА ===

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.typing = False
intents.presences = True
intents.reactions = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

CATEGORY_ID = 1126627249001607179
restricted_role_id = 1295482170374095049

DATABASE_URL = os.environ.get('DATABASE_URL')
REDIS_URL = os.environ.get('REDIS_URL')


# === ПУЛ СОЕДИНЕНИЙ POSTGRESQL ===
async def init_db_pool():
    global db_pool
    import socket
    original_getaddrinfo = socket.getaddrinfo
    
    def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    
    socket.getaddrinfo = ipv4_only_getaddrinfo
    
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=3,
            max_size=20,
            max_inactive_connection_lifetime=300,
            statement_cache_size=0
        )
        print("✅ Пул соединений PostgreSQL создан")
        
        async with db_pool.acquire() as conn:
            # Таблица для комнат
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS room_leadership (
                    leader_id BIGINT PRIMARY KEY,
                    room_name TEXT UNIQUE,
                    role_id BIGINT,
                    text_channel_id BIGINT,
                    voice_channel_id BIGINT,
                    creation_date TEXT,
                    expiration_date TEXT,
                    room_balance INTEGER DEFAULT 0,
                    extend_date TEXT,
                    text_channel_last_rename TIMESTAMP,
                    voice_channel_last_rename TIMESTAMP
                )
            ''')
            print("✅ Таблица room_leadership создана/проверена")
            
            # Таблицы для экономики
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id BIGINT PRIMARY KEY,
                    balance INTEGER DEFAULT 0,
                    last_daily_claimed TEXT,
                    last_work_claimed TEXT,
                    god_kissed TEXT DEFAULT '—',
                    voice_hours NUMERIC(10,2) DEFAULT 0,
                    displayed_role TEXT,
                    displayed_room TEXT,
                    messages_count INTEGER DEFAULT 0
                )
            ''')
            print("✅ Таблица user_profiles создана/проверена")
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS marriages (
                    id SERIAL PRIMARY KEY,
                    user1_id BIGINT,
                    user2_id BIGINT,
                    marriage_balance INTEGER DEFAULT 0,
                    created_at TEXT,
                    renewed_at TEXT,
                    expires_at TEXT,
                    voice_marry_id BIGINT
                )
            ''')
            print("✅ Таблица marriages создана/проверена")
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS roles (
                    role_name TEXT PRIMARY KEY,
                    hex_code TEXT,
                    owner_id BIGINT,
                    id_owner_now BIGINT,
                    creation_date TEXT,
                    expiration_date TEXT,
                    archived INTEGER DEFAULT 0,
                    extend_date TEXT,
                    archivation_date TEXT,
                    razarchive_date TEXT,
                    numberofday TEXT,
                    remaining_time TEXT,
                    allcoinsend_on_role INTEGER DEFAULT 0
                )
            ''')
            print("✅ Таблица roles создана/проверена")

            # Таблица привязок для умного лобби
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS lobby_bindings (
                    user_id BIGINT PRIMARY KEY,
                    voice_channel_id BIGINT,
                    bound_by BIGINT,
                    bound_at TEXT
                )
            ''')
            print("✅ Таблица lobby_bindings создана/проверена")
            
    finally:
        socket.getaddrinfo = original_getaddrinfo


async def get_db_connection():
    """Возвращает соединение из пула"""
    return await db_pool.acquire()


async def release_db_connection(conn):
    """Возвращает соединение обратно в пул"""
    await db_pool.release(conn)

# === ОБЁРТКА ДЛЯ БАЗЫ ДАННЫХ ===
#
# ИСПРАВЛЕНО (главное бутылочное горлышко бота):
# Старая PgWrapper лениво брала ОДНО соединение (self._conn) и держала его
# вечно — весь бот, независимо от размера пула (min_size=3, max_size=20),
# реально ходил в базу через один и тот же физический коннект. Все команды
# экономики/ролей/браков/комнат/активности стояли в очередь друг за другом
# на этом единственном соединении, даже если пул мог отдать 20 параллельных.
#
# Плюс last_result хранился в self, то есть был ОБЩИЙ на все одновременно
# выполняющиеся команды: если два человека одновременно жали кнопки,
# execute() одной команды мог перезаписать last_result до того, как другая
# успевала прочитать его через fetchone()/fetchall() — гонка данных.
#
# Теперь: каждый execute() берёт своё соединение из пула на время ОДНОГО
# запроса и сразу возвращает обратно (async with pool.acquire()), а
# last_result хранится в contextvars.ContextVar, который изолирован для
# каждой asyncio.Task (discord.py выполняет каждый interaction/callback в
# своей Task) — гонка исключена. Внешний API (execute -> fetchone/fetchall)
# не поменялся, поэтому ни один из command-файлов (eco/roles/marriage/
# rooms/profile/top/...) правок не требует.
import contextvars

_last_result_var: contextvars.ContextVar = contextvars.ContextVar('pg_last_result', default=None)


class PgWrapper:
    def __init__(self, pool_getter):
        self._pool_getter = pool_getter

    async def execute(self, query, *args):
        pool = self._pool_getter()
        if pool is None:
            raise RuntimeError("db_pool ещё не инициализирован (execute вызван до on_ready/init_db_pool)")

        async with pool.acquire() as conn:
            if query.strip().upper().startswith('SELECT'):
                result = await conn.fetch(query, *args)
            else:
                result = await conn.execute(query, *args)

        _last_result_var.set(result)
        return result

    def fetchone(self):
        result = _last_result_var.get()
        if result and len(result) > 0:
            return result[0]
        return None

    def fetchall(self):
        result = _last_result_var.get()
        return result if result else []


def _get_pool():
    return db_pool


cursor = PgWrapper(_get_pool)
conn = cursor

# === ИМПОРТ МОДУЛЕЙ ===
from commands_room import setup_room_commands, start_room_expiry_task
from commands_staff import setup_staff_commands
from commands_lobby import setup_lobby_commands
from commands_activity import setup_activity_tracking
# commands_economy теперь пакет (папка commands_economy/ с __init__.py),
# разбитый на common/eco/top/profile/marriage/roles/slots/duel.py —
# импорты и вызовы ниже не меняются, __init__.py реэкспортирует всё то же самое.
import commands_economy
commands_economy.set_cursor(cursor)

commands_economy.set_slots_connection_factory(get_db_connection, release_db_connection)

from commands_economy import (
    eco_group,
    me,
    marry,
    role_group,
    slots_group,
    top_group,
    withrole,
    duel,
    start_marriage_expiry_task,
    start_role_expiry_task,
    setup_role_delete_listener,
    reconcile_deleted_roles,
)

# === РЕГИСТРАЦИЯ КОМАНД ===
bot.tree.add_command(eco_group)
bot.tree.add_command(role_group)
bot.tree.add_command(slots_group)
bot.tree.add_command(top_group)
bot.tree.add_command(me)
bot.tree.add_command(marry)
bot.tree.add_command(withrole)
bot.tree.add_command(duel)

setup_room_commands(bot, cursor, CATEGORY_ID, restricted_role_id)
setup_staff_commands(bot, cursor)
setup_lobby_commands(bot, cursor)
setup_activity_tracking(bot, cursor, get_db_connection, release_db_connection)
setup_role_delete_listener(bot)


# === ON_READY ===
@bot.event
async def on_ready():
    # Инициализация БД и RediS
    await init_db_pool()
    await init_redis()

    from migrations import run_migrations
    await run_migrations()
    
    await reconcile_deleted_roles(bot)
    
    await bot.change_presence(
        status=discord.Status.online
    )
    
    print(f'✅ Bot is Up and Ready with PostgreSQL!')

    # Запуск фоновых задач
    start_marriage_expiry_task(bot)
    print('✅ Задача автопроверки браков запущена')
    
    start_role_expiry_task(bot)
    print('✅ Задача автопроверки ролей запущена')

    start_room_expiry_task(bot, cursor)
    print('✅ Задача автопроверки комнат запущена')

    await asyncio.sleep(5)
    
    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} command(s)')
        for cmd in synced:
            print(f'  - /{cmd.name}')
    except discord.HTTPException as e:
        if e.status == 429:
            print(f"⚠️ Rate limit при синхронизации, ждём {e.retry_after}с...")
            await asyncio.sleep(e.retry_after + 1)
            try:
                synced = await bot.tree.sync()
                print(f'✅ Synced {len(synced)} command(s) после ожидания')
            except Exception as retry_error:
                print(f"❌ Ошибка повторной синхронизации: {retry_error}")
        else:
            print(f"❌ Error syncing: {e}")
    except Exception as e:
        print(f"❌ Error syncing: {e}")


@bot.event
async def on_error(event, *args, **kwargs):
    print(f"❌ Ошибка в событии {event}:")
    import traceback
    traceback.print_exc()


# === ЗАПУСК БОТА ===
if __name__ == "__main__":
    bot.run(TOKEN)
