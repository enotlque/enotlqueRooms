import asyncpg
import os

DATABASE_URL = os.environ.get('DATABASE_URL')

async def run_migrations():
    """Создаёт индексы для ускорения запросов"""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Индекс для топов по голосовым часам
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_user_profiles_voice_hours 
            ON user_profiles(voice_hours DESC)
        ''')
        print("✅ Индекс idx_user_profiles_voice_hours создан")

        # Индекс для поиска ролей по владельцу
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_roles_id_owner_now 
            ON roles(id_owner_now)
        ''')
        print("✅ Индекс idx_roles_id_owner_now создан")

        # Индекс для поиска браков
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_marriages_user1_user2 
            ON marriages(user1_id, user2_id)
        ''')
        print("✅ Индекс idx_marriages_user1_user2 создан")

        # Индекс для поиска комнат по владельцу
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_room_leadership_leader_id 
            ON room_leadership(leader_id)
        ''')
        print("✅ Индекс idx_room_leadership_leader_id создан")

        # Индекс для поиска пользователей по балансу (для топов)
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_user_profiles_balance 
            ON user_profiles(balance DESC)
        ''')
        print("✅ Индекс idx_user_profiles_balance создан")

        # Индекс для поиска привязок лобби
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_lobby_bindings_user_id 
            ON lobby_bindings(user_id)
        ''')
        print("✅ Индекс idx_lobby_bindings_user_id создан")

    except Exception as e:
        print(f"❌ Ошибка создания индексов: {e}")
    finally:
        await conn.close()
