"""
Изпращане на имейли (потвърждение на имейл, нулиране на парола).

Доставчикът се избира от environment:
- RESEND_API_KEY (+ MAIL_FROM) → Resend API
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD (+ MAIL_FROM) → SMTP със STARTTLS
Без настройки имейлът не се изпраща. Линкът се отпечатва в лога само при
локална SQLite база, за да не изтичат токени в продукционните логове.
"""
import asyncio
import os
import smtplib
from email.message import EmailMessage

import httpx

from database import SQLALCHEMY_DATABASE_URL

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://astromind-web.onrender.com").rstrip("/")


def frontend_link(path: str) -> str:
    return f"{FRONTEND_URL}/#{path}"


def is_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY") or os.getenv("SMTP_HOST"))


def _send_smtp(to: str, subject: str, text: str, html: str):
    msg = EmailMessage()
    msg["From"] = os.getenv("MAIL_FROM", os.getenv("SMTP_USER", ""))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=20) as smtp:
        smtp.starttls()
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


async def send_email(to: str, subject: str, text: str, html: str) -> bool:
    try:
        if os.getenv("RESEND_API_KEY"):
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                    json={"from": os.getenv("MAIL_FROM", "AstroMind <onboarding@resend.dev>"),
                          "to": [to], "subject": subject, "text": text, "html": html},
                )
                r.raise_for_status()
            return True
        if os.getenv("SMTP_HOST"):
            await asyncio.to_thread(_send_smtp, to, subject, text, html)
            return True
    except Exception as exc:
        print(f"❌ Имейлът не беше изпратен ({subject}): {type(exc).__name__}: {exc}")
        return False

    print(f"✉️  Имейл доставчик не е настроен; пропускам „{subject}“")
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        print(f"   (локално) {text}")
    return False


def _layout(title: str, body: str, link: str, button: str) -> str:
    return f"""<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;color:#1f1733">
<h2 style="color:#5211d4">{title}</h2><p>{body}</p>
<p><a href="{link}" style="background:#5211d4;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block">{button}</a></p>
<p style="font-size:12px;color:#777">Ако бутонът не работи, отворете този адрес:<br>{link}</p>
<p style="font-size:12px;color:#777">Ако не сте поискали това, игнорирайте писмото.</p></div>"""


async def send_verification_email(to: str, token: str) -> bool:
    link = frontend_link(f"/verify-email?token={token}")
    text = f"Потвърдете имейла си за AstroMind: {link}"
    html = _layout("Потвърдете имейла си", "Остава една стъпка, за да активирате акаунта си в AstroMind.", link, "Потвърди имейла")
    return await send_email(to, "Потвърдете имейла си в AstroMind", text, html)


async def send_password_reset_email(to: str, token: str) -> bool:
    link = frontend_link(f"/reset-password?token={token}")
    text = f"Нулиране на паролата за AstroMind (валидно 1 час): {link}"
    html = _layout("Нулиране на паролата", "Получихме заявка за нова парола. Линкът е валиден 1 час.", link, "Задай нова парола")
    return await send_email(to, "Нулиране на паролата в AstroMind", text, html)
