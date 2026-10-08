"""
Изпращане на имейли (потвърждение на имейл, нулиране на парола).

Доставчикът се избира от environment:
- RESEND_API_KEY (+ MAIL_FROM) → Resend API, по HTTPS
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD (+ MAIL_FROM) → SMTP
    порт 465 → защитена връзка още от началото (SSL/TLS);
    всеки друг порт (26, 587) → STARTTLS. SMTP_SECURITY=ssl|starttls|none го задава изрично.
    Сертификатът на сървъра се проверява; SMTP_VERIFY_TLS=0 изключва проверката.
    SMTP_TIMEOUT (секунди, по подразбиране 15) ограничава чакането.
Ако са зададени и двата, Resend има предимство.
Без настройки имейлът не се изпраща. Линкът се отпечатва в лога само при
локална SQLite база, за да не изтичат токени в продукционните логове.

ВАЖНО: Render Free блокира изходящите портове 25, 465 и 587. На безплатен план
ползвайте друг порт на пощенския сървър (напр. 26) или платен план.
Паролата и адресите на получателите никога не се записват в лога.
"""
import asyncio
import contextlib
import os
import smtplib
import socket
import ssl
import threading
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Optional

import httpx

from database import SQLALCHEMY_DATABASE_URL

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://astromind-web.onrender.com").rstrip("/")

DEFAULT_SMTP_PORT = 587
DEFAULT_TIMEOUT_SECONDS = 15.0

# Редове с \r\n и тяло, кодирано в base64/quoted-printable (policy.SMTP само по себе си
# оставя кирилицата като суров 8-битов текст, затова cte_type се задава изрично)
MAIL_POLICY = policy.SMTP.clone(cte_type="7bit")


def frontend_link(path: str) -> str:
    return f"{FRONTEND_URL}/#{path}"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

def _smtp_host() -> str:
    return _env("SMTP_HOST")


def _smtp_port() -> int:
    raw = _env("SMTP_PORT")
    try:
        return int(raw) if raw else DEFAULT_SMTP_PORT
    except ValueError:
        return DEFAULT_SMTP_PORT


def _smtp_timeout() -> float:
    try:
        value = float(_env("SMTP_TIMEOUT") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _smtp_security(port: int) -> str:
    """ssl (защитена връзка от началото), starttls или none. По подразбиране: ssl за порт 465."""
    mode = _env("SMTP_SECURITY").lower()
    if mode in ("ssl", "starttls", "none"):
        return mode
    return "ssl" if port == 465 else "starttls"


def _verify_tls() -> bool:
    return _env("SMTP_VERIFY_TLS") != "0"


def _config_problem() -> Optional[str]:
    """Какво липсва, за да работи SMTP. None означава, че всичко необходимо е зададено."""
    if not _smtp_host():
        return "не е зададен SMTP_HOST"
    if _env("SMTP_USER") and not os.getenv("SMTP_PASSWORD"):
        return "SMTP_USER е зададен, но липсва SMTP_PASSWORD"
    return None


def is_configured() -> bool:
    return bool(_env("RESEND_API_KEY")) or _config_problem() is None


def _smtp_from() -> str:
    configured = _env("MAIL_FROM")
    if configured:
        return configured
    user = _env("SMTP_USER")
    return formataddr(("AstroMind", user)) if "@" in user else user


def _from_domain() -> Optional[str]:
    address = parseaddr(_smtp_from())[1]
    return address.rpartition("@")[2] if "@" in address else None


def _smtp_label() -> str:
    port = _smtp_port()
    return f"{_smtp_host()}:{port} ({_smtp_security(port)})"


def _tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not _verify_tls():
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


# ---------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------

def _build_message(to: str, subject: str, text: str, html: str) -> EmailMessage:
    # Чисто 7-битово писмо: минава през всеки сървър, без да разчита на 8BITMIME
    msg = EmailMessage(policy=MAIL_POLICY)
    msg["From"] = _smtp_from()
    msg["To"] = to
    msg["Subject"] = subject
    # Date и Message-ID с домейна на подателя: без тях писмата по-лесно стигат до спам
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=_from_domain())
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


@contextlib.contextmanager
def _smtp_session():
    """
    Отворена и защитена SMTP връзка (още без вход). Данните за вход никога не се
    изпращат по незащитена връзка: ако STARTTLS липсва, връзката се прекъсва.
    """
    port = _smtp_port()
    security = _smtp_security(port)
    timeout = _smtp_timeout()
    context = _tls_context()
    # Името в EHLO е домейнът на подателя; иначе smtplib търси името на сървъра през DNS
    local_hostname = _from_domain()

    if security == "ssl":
        smtp = smtplib.SMTP_SSL(_smtp_host(), port, local_hostname=local_hostname, timeout=timeout, context=context)
    else:
        smtp = smtplib.SMTP(_smtp_host(), port, local_hostname=local_hostname, timeout=timeout)
    try:
        if security == "starttls":
            smtp.ehlo()
            if not smtp.has_extn("starttls"):
                raise smtplib.SMTPNotSupportedError("сървърът не предлага STARTTLS")
            smtp.starttls(context=context)
        yield smtp
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            pass
        finally:
            smtp.close()


def _authenticate(smtp, user: str, password: str):
    """
    Вход с един опит. smtplib.login() при грешна парола опитва всички начини един
    след друг (PLAIN, после LOGIN), а хостингът брои всеки опит като неуспешен вход.
    """
    smtp.ehlo_or_helo_if_needed()
    if not smtp.has_extn("auth"):
        raise smtplib.SMTPNotSupportedError("сървърът не предлага удостоверяване (AUTH)")
    advertised = smtp.esmtp_features["auth"].upper().split()
    smtp.user, smtp.password = user, password
    if "PLAIN" in advertised:
        smtp.auth("PLAIN", smtp.auth_plain, initial_response_ok=True)
    elif "LOGIN" in advertised:
        smtp.auth("LOGIN", smtp.auth_login)
    else:
        smtp.login(user, password)  # напр. сървър само с CRAM-MD5


def _send_smtp(to: str, subject: str, text: str, html: str):
    msg = _build_message(to, subject, text, html)
    with _smtp_session() as smtp:
        user = _env("SMTP_USER")
        if user:
            _authenticate(smtp, user, os.getenv("SMTP_PASSWORD") or "")
        smtp.send_message(msg)


def probe_smtp() -> str:
    """
    Проверка без вход и без писмо: връзка, TLS сертификат и начини за удостоверяване.
    Връща списъка с поддържани начини за удостоверяване или хвърля грешка.
    """
    with _smtp_session() as smtp:
        smtp.ehlo()
        return (smtp.esmtp_features.get("auth") or "").strip() or "няма"


def _failure_hint(exc: Exception) -> str:
    """Най-честата причина за грешката, за да се чете лесно логът."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        hint = "сертификатът на сървъра не съвпада със SMTP_HOST (SMTP_VERIFY_TLS=0 изключва проверката, но е по-малко сигурно)"
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        hint = "сървърът отхвърли SMTP_USER или SMTP_PASSWORD"
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        hint = "подателят е отказан: MAIL_FROM трябва да е адресът на самата пощенска кутия"
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        hint = "получателят е отказан от сървъра"
    elif isinstance(exc, smtplib.SMTPNotSupportedError):
        hint = "сървърът не предлага нужното разширение (STARTTLS или AUTH): проверете порта и SMTP_SECURITY"
    elif isinstance(exc, TimeoutError):
        hint = ("няма отговор от сървъра. Render Free блокира изходящите портове 25, 465 и 587: "
                "ползвайте друг порт (напр. 26) или платен план")
    elif isinstance(exc, ConnectionRefusedError):
        hint = "връзката е отказана: проверете SMTP_HOST и SMTP_PORT"
    elif isinstance(exc, socket.gaierror):
        hint = "адресът на сървъра не е намерен: проверете SMTP_HOST"
    elif isinstance(exc, ssl.SSLError):
        hint = "грешка при TLS: порт 465 е за ssl, а портове 26 и 587 - за starttls (SMTP_SECURITY)"
    elif isinstance(exc, (smtplib.SMTPServerDisconnected, ConnectionError)):
        hint = "сървърът прекъсна връзката: проверете порта и режима на защита (SMTP_SECURITY)"
    else:
        return ""
    return f" - {hint}"


def _safe_error_text(exc: Exception) -> str:
    """Текст на грешката без адресите на получателите и без знаци от паролата."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = ", ".join(str(item[0]) for item in exc.recipients.values())
        return f"получателят е отказан (код {codes})"
    if isinstance(exc, UnicodeError):
        # Текстът на тази грешка съдържа знака, който не може да се кодира, т.е. част от паролата
        return "потребителското име или паролата съдържат знаци извън ASCII, които SMTP не поддържа"
    return str(exc)


# ---------------------------------------------------------------------------
# Изпращане
# ---------------------------------------------------------------------------

async def send_email(to: str, subject: str, text: str, html: str) -> bool:
    provider = "Resend" if _env("RESEND_API_KEY") else None
    try:
        if provider:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                    json={"from": os.getenv("MAIL_FROM", "AstroMind <onboarding@resend.dev>"),
                          "to": [to], "subject": subject, "text": text, "html": html},
                )
                r.raise_for_status()
            return True
        if _config_problem() is None:
            provider = f"SMTP {_smtp_label()}"
            await asyncio.to_thread(_send_smtp, to, subject, text, html)
            print(f"✉️  Изпратен имейл „{subject}“ през {provider}")
            return True
    except Exception as exc:
        print(f"❌ Имейлът не беше изпратен („{subject}“) през {provider}: "
              f"{type(exc).__name__}: {_safe_error_text(exc)}{_failure_hint(exc)}")
        return False

    problem = _config_problem() if (_smtp_host() or _env("SMTP_USER")) else None
    reason = f"SMTP е настроен непълно ({problem})" if problem else "имейл доставчик не е настроен"
    print(f"✉️  {reason}; пропускам „{subject}“")
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        print(f"   (локално) {text}")
    return False


def _startup_check():
    label = _smtp_label()
    try:
        auth = probe_smtp()
        problem = _config_problem()
        note = f" ВНИМАНИЕ: {problem}." if problem else ""
        print(f"✅ Имейл: {label} е достъпен, сертификатът е приет, удостоверяване: {auth}. "
              f"Подател: {_smtp_from() or 'не е зададен'}.{note}")
    except Exception as exc:
        print(f"❌ Имейл: {label} не е достъпен: {type(exc).__name__}: {exc}{_failure_hint(exc)}")


def log_status_in_background():
    """
    При старт записва в лога кой имейл доставчик е настроен и дали пощенският сървър е
    достъпен (без вход и без изпращане на писмо). Работи във фонова нишка и не забавя старта.
    """
    if _env("RESEND_API_KEY"):
        print("✉️  Имейл: Resend (по HTTPS)")
    elif _smtp_host():
        threading.Thread(target=_startup_check, name="mail-check", daemon=True).start()
    else:
        print("✉️  Имейл: не е настроен (писма за потвърждение и нова парола не се изпращат)")


# ---------------------------------------------------------------------------
# Писма
# ---------------------------------------------------------------------------

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
