import redis.asyncio as redis
import os

redis_client: redis.Redis = None
REDIS_URL = os.environ.get('REDIS_URL')


async def init_redis():
    global redis_client
    try:
        if REDIS_URL:
            redis_client = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                max_connections=20,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            await redis_client.ping()
            print("✅ Redis подключён")
        else:
            print("⚠️ REDIS_URL не найден, кеш отключён")
            redis_client = None
    except Exception as e:
        print(f"⚠️ Redis не подключён: {e}")
        redis_client = None
