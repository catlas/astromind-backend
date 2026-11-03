"""Контакт с нас (03.11.2026): форма с име, имейл и текст; писмото отива до собственика, а отговорът (Reply-To) е към подателя."""
import html
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import mailer
from auth import normalize_email, validate_email
from rate_limit import client_ip, enforce

router = APIRouter()

CONTACT_TO = os.getenv("CONTACT_EMAIL", "evgeniatanasov@gmail.com")


class ContactIn(BaseModel):
    name: str = Field(..., max_length=100)
    email: str = Field(..., max_length=254)
    message: str = Field(..., max_length=3000)
    website: str = Field(default="", max_length=200)          # скрито поле за ботове: човекът го оставя празно


@router.post("/contact")
async def contact(data: ContactIn, request: Request):
    if data.website.strip():
        return {"message": "Благодарим! Съобщението е изпратено."}             # бот: тихо не правим нищо
    enforce(f"contact-ip:{client_ip(request)}", 5, 3600, "Изпратихте няколко съобщения. Опитайте по-късно.")
    name, message, email = data.name.strip(), data.message.strip(), normalize_email(data.email)
    if not name:
        raise HTTPException(status_code=400, detail="Въведете име")
    if validate_email(email):
        raise HTTPException(status_code=400, detail="Въведете валиден имейл, на който да ви отговорим")
    if len(message) < 10:
        raise HTTPException(status_code=400, detail="Напишете поне няколко думи")
    enforce(f"contact-mail:{email}", 3, 3600, "Изпратихте няколко съобщения. Опитайте по-късно.")
    if not mailer.is_configured():
        raise HTTPException(status_code=503, detail="Изпращането на съобщения още не е активирано. Опитайте по-късно.")
    subject = f"Контакт от AstroMind: {name[:60]}".replace("\n", " ").replace("\r", " ")
    text = f"От: {name} <{email}>\n\n{message}"
    body = html.escape(message).replace("\n", "<br>")
    page = f"<p><b>От:</b> {html.escape(name)} &lt;{html.escape(email)}&gt;</p><p>{body}</p>"
    if not await mailer.send_email(CONTACT_TO, subject, text, page, reply_to=email):
        raise HTTPException(status_code=502, detail="Не успяхме да изпратим съобщението. Опитайте отново след малко.")
    return {"message": "Благодарим! Съобщението е изпратено."}
