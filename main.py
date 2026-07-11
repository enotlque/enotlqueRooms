import discord 
from discord.ext import commands
import sqlite3
from discord import Intents
from config import TOKEN
from commands import setup_commands
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
intents.voice_states = False

bot = commands.Bot(command_prefix='!', intents=intents)

# ID вашей категории
CATEGORY_ID = 1126627249001607179
restricted_role_id = 1129742835487358989

# === ПОДКЛЮЧЕНИЕ К POSTGRESQL ===
DATABASE_URL = os.environ.get('DATABASE_URL')

async def init_db():
    """Создает таблицы в PostgreSQL, если их нет"""
    # Принудительно используем IPv4
    import asyncpg
    import socket
    original_getaddrinfo = socket.getaddrinfo
    
    def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # Переопределяем, чтобы использовать только IPv4
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    
    socket.getaddrinfo = ipv4_only_getaddrinfo
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
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
            print("Таблица room_leadership создана/проверена")
        finally:
            await conn.close()
    finally:
        # Возвращаем оригинальную функцию
        socket.getaddrinfo = original_getaddrinfo

async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL)

# Функции для работы с БД (заменяют sqlite3)
async def execute_query(query, *args):
    conn = await get_db_connection()
    try:
        if query.strip().upper().startswith('SELECT'):
            return await conn.fetch(query, *args)
        else:
            return await conn.execute(query, *args)
    finally:
        await conn.close()

async def fetch_one(query, *args):
    conn = await get_db_connection()
    try:
        return await conn.fetchrow(query, *args)
    finally:
        await conn.close()

async def fetch_all(query, *args):
    conn = await get_db_connection()
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()

class PgWrapper:
    """Обертка для PostgreSQL, имитирующая интерфейс SQLite"""
    def __init__(self):
        self.last_result = None
    
    async def execute(self, query, *args):
        """Выполняет запрос"""
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
        """Возвращает первую запись из последнего результата"""
        if self.last_result and len(self.last_result) > 0:
            return self.last_result[0]
        return None
    
    def fetchall(self):
        """Возвращает все записи из последнего результата"""
        return self.last_result if self.last_result else []

cursor = PgWrapper()
conn = cursor  

@bot.event
async def on_ready():
    await init_db()
    
    await bot.change_presence(
        status=discord.Status.invisible,
    )
    
    print(f'Bot is Up and Ready with PostgreSQL!')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(e)

setup_commands(bot, cursor, CATEGORY_ID, conn, restricted_role_id)

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"Произошла ошибка в событии {event}:")
    import traceback
    traceback.print_exc()

bot.run(TOKEN)
