from redis_client import redis_client
import json

CACHE_TTL = {
    'profile': 300,
    'top': 3600,
    'balance': 60,
    'room': 600,
}


async def get_cached(key: str):
    if redis_client is None:
        return None
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    except:
        return None


async def set_cached(key: str, value, ttl: int = 300):
    if redis_client is None:
        return
    try:
        await redis_client.setex(key, ttl, json.dumps(value))
    except:
        pass


async def delete_cached(key: str):
    if redis_client is None:
        return
    try:
        await redis_client.delete(key)
    except:
        pass


async def delete_pattern(pattern: str):
    if redis_client is None:
        return
    try:
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
    except:
        pass


def profile_cache_key(user_id: int):
    return f"profile:{user_id}"


def balance_cache_key(user_id: int):
    return f"balance:{user_id}"


def top_cache_key(top_type: str):
    return f"top:{top_type}"
