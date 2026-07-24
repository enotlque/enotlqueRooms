import asyncio
import time
from discord import HTTPException

class RateLimiter:
    def __init__(self, max_requests=5, per_seconds=1):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.tokens = max_requests
        self.lock = asyncio.Lock()
        self.last_refill = time.time()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.max_requests,
                self.tokens + elapsed * (self.max_requests / self.per_seconds)
            )
            self.last_refill = now
            
            if self.tokens < 1:
                wait = (1 - self.tokens) * (self.per_seconds / self.max_requests)
                await asyncio.sleep(wait)
                self.tokens = 1
            
            self.tokens -= 1

# Глобальный экземпляр
rate_limiter = RateLimiter(max_requests=10, per_seconds=1)

async def safe_discord_call(coro_func, retries=3):
    """coro_func — вызываемый без аргументов объект, возвращающий НОВУЮ корутину
    при каждом вызове, например: lambda: channel.send("привет").
    Раньше сюда передавали уже созданную корутину напрямую (safe_discord_call(x.send(...)))
    — это работало только на первой попытке: при 429 и повторном заходе в цикл
    `await coro` падал с RuntimeError, так как одну и ту же корутину нельзя
    авызвать дважды. Функция сейчас нигде в проекте не вызывается, поэтому
    баг был "спящим", но чинить его нужно было именно так, а не патчем поверх
    старого API."""
    for attempt in range(retries):
        await rate_limiter.acquire()
        try:
            return await coro_func()
        except HTTPException as e:
            if e.status == 429 and attempt < retries - 1:
                wait = e.retry_after or 5
                await asyncio.sleep(wait * (attempt + 1))
            else:
                raise
    return None
