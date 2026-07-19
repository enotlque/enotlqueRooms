import discord
from discord.ext import commands
from discord import Intents
from config import TOKEN
import asyncio
import os
import threading
import asyncpg

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
intents.presences = False
intents.reactions = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

CATEGORY_ID = 1126627249001607179
restricted_role_id = 1295482170374095049

DATABASE_URL = os.environ.get('DATABASE_URL')

async def init_db():
    import socket
    original_getaddrinfo = socket.getaddrinfo
    
    def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    
    socket.getaddrinfo = ipv4_only_getaddrinfo
    
    try:
        conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
        try:
            # Таблица для комнат
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS room_leadership (
                    leader_id BIGINT PRIMARY KEY,
                    room_name TEXT UNIQUE,
                    role_id BIGINT,
                    text_channel_id BIGINT,
                    voice_channel_id BIGINT,
                    creation_date TEXT,
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
            await conn.close()
    finally:
        socket.getaddrinfo = original_getaddrinfo

async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

class PgWrapper:
    def __init__(self):
        self.last_result = None
    
    async def execute(self, query, *args):
        conn = await get_db_connection()
        try:
            if query.strip().upper().startswith('SELECT'):
                result = await conn.fetch(query, *args)
                self.last_result = result
                return result
            else:
                result = await conn.execute(query, *args)
                self.last_result = result
                return result
        finally:
            await conn.close()
    
    def fetchone(self):
        if self.last_result and len(self.last_result) > 0:
            return self.last_result[0]
        return None
    
    def fetchall(self):
        return self.last_result if self.last_result else []

cursor = PgWrapper()
conn = cursor

# === ИМПОРТ И ПЕРЕДАЧА CURSOR В ЭКОНОМИЧЕСКИЙ МОДУЛЬ ===
from commands_room import setup_room_commands
from commands_staff import setup_staff_commands
from commands_lobby import setup_lobby_commands
from commands_activity import setup_activity_tracking
import commands_economy
commands_economy.set_cursor(cursor)

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

# === РЕГИСТРАЦИЯ ===
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
setup_activity_tracking(bot, cursor, get_db_connection)
setup_role_delete_listener(bot)

@bot.event
async def on_ready():
    await init_db()
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

    await asyncio.sleep(5)  # Ждём 5 секунд перед синхронизацией
    
    try:
        synced = await bot.tree.sync()
        print(f'✅ Synced {len(synced)} command(s)')
        for cmd in synced:
            print(f'  - /{cmd.name}')
    except Exception as e:
        print(f"❌ Error syncing: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"❌ Ошибка в событии {event}:")
    import traceback
    traceback.print_exc()

bot.run(TOKEN)
