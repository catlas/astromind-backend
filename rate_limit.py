"""
Прост ограничител на заявките (sliding window) в паметта на процеса.

Render пуска бекенда като една инстанция, затова брояч в паметта е достатъчен.
При рестарт броячите се нулират, което е приемливо за защита от злоупотреба.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int, token: Optional[str] = None) -> int:
        """
        Отбелязва заявка за key. Връща 0, ако е позволена, иначе броя
        секунди, след които ще бъде позволена отново.
        token (по избор) свързва заявката с резервация: release(key, token) освобождава точно нея.
        """
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0][0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(hits[0][0] + window_seconds - now) + 1)
            hits.append((now, token))
            return 0

    def release(self, key: str, token: Optional[str]) -> bool:
        """Освобождава точно заявката с този token (идемпотентно). Връща True, ако е имало какво да се освободи."""
        if not token:
            return False
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return False
            for index, (_, owner) in enumerate(hits):
                if owner == token:
                    del hits[index]
                    return True
            return False

    def peek(self, key: str, limit: int, window_seconds: int) -> tuple:
        """
        Състоянието на ограничението, без да отбелязва заявка: (колко заявки остават, след колко секунди ще е позволена нова).
        Втората стойност е 0, докато има оставащи заявки.
        """
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return limit, 0
            while hits and hits[0][0] <= now - window_seconds:
                hits.popleft()
            remaining = max(0, limit - len(hits))
            if remaining > 0:
                return remaining, 0
            return 0, max(1, int(hits[0][0] + window_seconds - now) + 1)

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


def enforce(key: str, limit: int, window_seconds: int, message: str, token: Optional[str] = None):
    retry_after = limiter.hit(key, limit, window_seconds, token)
    if retry_after:
        minutes = (retry_after + 59) // 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{message} Опитайте отново след около {minutes} мин.",
            headers={"Retry-After": str(retry_after)},
        )
