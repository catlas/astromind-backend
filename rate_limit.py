"""
Прост ограничител на заявките (sliding window) в паметта на процеса.

Render пуска бекенда като една инстанция, затова брояч в паметта е достатъчен.
При рестарт броячите се нулират, което е приемливо за защита от злоупотреба.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> int:
        """
        Отбелязва заявка за key. Връща 0, ако е позволена, иначе броя
        секунди, след които ще бъде позволена отново.
        """
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(hits[0] + window_seconds - now) + 1)
            hits.append(now)
            return 0

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # Render стои зад proxy и подава реалния адрес в X-Forwarded-For
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(key: str, limit: int, window_seconds: int, message: str):
    retry_after = limiter.hit(key, limit, window_seconds)
    if retry_after:
        minutes = (retry_after + 59) // 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{message} Опитайте отново след около {minutes} мин.",
            headers={"Retry-After": str(retry_after)},
        )
