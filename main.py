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

# Запускаем веб-сервер в отдельном потоке
threading.Thread(target=run_web_server, daemon=True).start()
# === КОНЕЦ БЛОКА ===

# Создаем объект intents и устанавливаем нужные параметры
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.typing = False
intents.presences = False
intents.reactions = True
intents.voice_states = True  # Включаем для голосовых каналов

bot = commands.Bot(command_prefix='!', intents=intents)

# ID вашей категории для комнат
CATEGORY_ID = 1126627249001607179
restricted_role_id = 1295482170374095049

# === ПОДКЛЮЧЕНИЕ К POSTGRESQL ===
DATABASE_URL = os.environ.get('DATABASE_URL')

async def init_db():
    """Создает таблицы в PostgreSQL, если их нет"""
    import socket
    original_getaddrinfo = socket.getaddrinfo
    
    def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    
    socket.getaddrinfo = ipv4_only_getaddrinfo
    
    try:
        conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
        try:
            # === ТАБЛИЦА ДЛЯ КОМНАТ ===
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
            
            # === ТАБЛИЦЫ ДЛЯ ЭКОНОМИКИ ===
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id BIGINT PRIMARY KEY,
                    balance INTEGER DEFAULT 0,
                    last_daily_claimed TEXT,
                    status TEXT DEFAULT 'Статуса нет'
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
            
        finally:
            await conn.close()
    finally:
        socket.getaddrinfo = original_getaddrinfo

async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

class PgWrapper:
    """Обертка для PostgreSQL, имитирующая интерфейс SQLite"""
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

# Глобальный объект для работы с БД
cursor = PgWrapper()
conn = cursor

# === ИМПОРТ КОМАНД ===
# Команды для комнат
from commands_room import setup_room_commands

# Экономические команды
from commands_economy import (
    eco_group,
    me,
    marry,
    role_group,
    slots_group,
    withrole,
)

# === РЕГИСТРАЦИЯ КОМАНД ===
# Регистрируем экономические группы
bot.tree.add_command(eco_group)
bot.tree.add_command(role_group)
bot.tree.add_command(slots_group)

# Регистрируем отдельные экономические команды
bot.tree.add_command(me)
bot.tree.add_command(marry)
bot.tree.add_command(withrole)

# Регистрируем /room команды
setup_room_commands(bot, cursor, CATEGORY_ID, restricted_role_id)

@bot.event
async def on_ready():
    await init_db()
    
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game("/room manage | /eco balance")
    )
    
    print(f'✅ Bot is Up and Ready with PostgreSQL!')
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
