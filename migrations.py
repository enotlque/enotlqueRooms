import asyncpg
import os

DATABASE_URL = os.environ.get('DATABASE_URL')

async def run_migrations(pool=None):
    """Создаёт индексы для ускорения запросов.

    pool - опциональный уже созданный asyncpg.Pool (main.py передаёт
    db_pool). Если передан, используется соединение из пула вместо
    отдельного asyncpg.connect(): это переиспользует уже проверенную
    сетевую конфигурацию (main.py форсирует IPv4 при создании пула —
    отдельный connect() в обход этого патча мог зависать/падать на
    хостингах без IPv6 до базы) и не тратит лишнее соединение на старте.
    """
    if pool is not None:
        async with pool.acquire() as conn:
            await _create_indexes(conn)
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await _create_indexes(conn)
    finally:
        await conn.close()


async def _create_indexes(conn):
    try:
        # Колонка для роли, выбранной для отображения в /me (может отсутствовать на старых БД)
        await conn.execute('''
            ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS displayed_role TEXT
        ''')
        print("✅ Колонка user_profiles.displayed_role создана/проверена")

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

        # Индекс для поиска комнат по role_id (используется при сверке
        # удалённых на сервере ролей комнат — раньше был full scan)
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_room_leadership_role_id 
            ON room_leadership(role_id)
        ''')
        print("✅ Индекс idx_room_leadership_role_id создан")

    except Exception as e:
        print(f"❌ Ошибка создания индексов: {e}")
