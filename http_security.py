"""
Защита на HTTP слоя (Фаза 14): заглавия за сигурност на всеки отговор и таван на размера на заявката.

- API-то връща само JSON и файлове за изтегляне, затова CSP е най-строгата (default-src 'none'; frame-ancestors 'none'):
  нищо от отговорите му не може да се зареди като страница или да се вгради в друга.
- HSTS само при https (Render слага X-Forwarded-Proto), без да пречи на локалната разработка.
- Размерът на тялото на заявката е ограничен; импортът на стари данни от браузъра има по-висок таван.
Чист ASGI, без зависимости: работи и за заявки без Content-Length (chunked).
"""
import os
from typing import Iterable

MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(1_000_000)))
MAX_IMPORT_BYTES = int(os.getenv("MAX_IMPORT_BODY_BYTES", str(8_000_000)))
IMPORT_PATHS = ("/profiles/import", "/reports/import")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-resource-policy", b"same-site"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class RequestTooLarge(Exception):
    pass


def limit_for(path: str) -> int:
    return MAX_IMPORT_BYTES if path in IMPORT_PATHS else MAX_BODY_BYTES


def _present(headers: Iterable, name: bytes) -> bool:
    return any(key.lower() == name for key, _ in headers)


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict((k.lower(), v) for k, v in scope.get("headers", []))
        limit = limit_for(scope.get("path", ""))
        secure = scope.get("scheme") == "https" or headers.get(b"x-forwarded-proto", b"").split(b",")[0].strip() == b"https"

        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(send, secure)
            return

        received = 0
        started = False
        too_large = False            # FastAPI превръща всяка грешка при четене на тялото в 400: отговорът се подменя на 413

        async def limited_receive():
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    raise RequestTooLarge()
            return message

        async def secured_send(message):
            nonlocal started
            if too_large:
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await self._reject(send, secure)
                return                                  # нищо друго от приложението не стига до клиента
            if message["type"] == "http.response.start":
                started = True
                out = list(message.get("headers", []))
                for name, value in SECURITY_HEADERS:
                    if not _present(out, name):
                        out.append((name, value))
                if secure and not _present(out, HSTS[0]):
                    out.append(HSTS)
                if not _present(out, b"cache-control"):
                    out.append((b"cache-control", b"no-store"))
                message = {**message, "headers": out}
            await send(message)

        try:
            await self.app(scope, limited_receive, secured_send)
        except RequestTooLarge:
            if not started:
                started = True
                await self._reject(send, secure)

    @staticmethod
    async def _reject(send, secure: bool) -> None:
        body = '{"detail":"Заявката е твърде голяма."}'.encode("utf-8")
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                   *SECURITY_HEADERS, (b"cache-control", b"no-store")]
        if secure:
            headers.append(HSTS)
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})
