import discord 
from discord.ext import commands
import sqlite3
from discord import Intents
from config import TOKEN  # Убедитесь, что ваш токен импортируется правильно
from commands import setup_commands  # Импортируем функцию для настройки команд
import asyncio
import os

from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def health():
    return "I'm alive!", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# Запускаем веб-сервер в отдельном потоке
threading.Thread(target=run_web_server, daemon=True).start()

# Создаем объект intents и устанавливаем нужные параметры
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Разрешает просмотр и обработку содержимого сообщений
intents.messages = True  # Разрешает получение сообщений в общих чатах
intents.guild_messages = True  # Разрешает получение сообщений на серверах
intents.typing = False
intents.presences = False
intents.reactions = True
intents.voice_states = False

bot = commands.Bot(command_prefix='!', intents=intents)

# ID вашей категории
CATEGORY_ID = 1126627249001607179  # ID Категории, в которую создаются комнаты
restricted_role_id = 1129742835487358989 # ID BANNED роли

# Подключение к базе данных SQLite
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

# Создание таблицы для лидерства в комнатах с новым столбцом creation_date
cursor.execute('''
    CREATE TABLE IF NOT EXISTS room_leadership (
        leader_id INTEGER PRIMARY KEY,
        room_name TEXT UNIQUE,
        role_id INTEGER,
        text_channel_id INTEGER,
        voice_channel_id INTEGER,
        creation_date TEXT,
        text_channel_last_rename TIMESTAMP,
        voice_channel_last_rename TIMESTAMP
    )
''')
conn.commit()

@bot.event
async def on_ready():
    print(f'Bot is Up and Ready!')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(e)

setup_commands(bot, cursor, CATEGORY_ID, conn, restricted_role_id)

@bot.event
async def on_error(event, *args, **kwargs):
    # Обработка ошибок, если возникает исключение
    print(f"Произошла ошибка в событии {event}:")
    import traceback
    traceback.print_exc()

bot.run(TOKEN)