"""
Обща тестова среда: временна SQLite база и фиксирани лимити.
Импортира се преди main във всеки тестов модул (базата се създава веднъж на процес).
"""
import os
import sys
import tempfile

if "ASTRO_TEST_DB_DIR" not in os.environ:
    os.environ["ASTRO_TEST_DB_DIR"] = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{os.environ['ASTRO_TEST_DB_DIR']}/test.db"
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "test-dummy")  # AI моделът не се вика в тестовете
os.environ["AI_RATE_LIMIT_PER_HOUR"] = "2"
os.environ["LOGIN_RATE_LIMIT_PER_15_MIN"] = "3"
os.environ["REGISTER_RATE_LIMIT_PER_HOUR"] = "1000"
os.environ["GEOCODE_RATE_LIMIT_PER_HOUR"] = "4"
for key in ("RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_SECURITY",
            "SMTP_VERIFY_TLS", "SMTP_TIMEOUT", "MAIL_FROM", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
    os.environ.pop(key, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Общ преглед с четирите задължителни части (проверката за пълнота на отчета): за тестове с подменен AI
OVERVIEW_OK = ("<p><b>Накратко</b>: смесен период.</p><p><b>Най-силни моменти</b>: няколко дати.</p>"
               "<p><b>Моменти за повече внимание</b>: две.</p><p><b>Съгласуван план</b>: по дати.</p>")

PASSWORD = "Zvezdi2026x"
CHART = {"date": "1990-05-15", "time": "14:30", "lat": 42.6977, "lon": 23.3219}


def register_and_login(client, email, password=PASSWORD, name="Тест"):
    r = client.post("/register", json={"email": email, "password": password, "full_name": name, "accept_terms": True})
    assert r.status_code == 200, r.text
    r = client.post("/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def give_deposit(email, cents):
    """Слага внесени средства на баланса (премиум услугите се плащат само с тях), през регистъра."""
    import billing
    from database import SessionLocal, User
    db = SessionLocal()
    try:
        uid = db.query(User.id).filter(User.email == email).scalar()
        billing.apply_transaction(db, uid, "admin", paid=cents, description="тест: внесени средства")
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Анализ през задачите (Фаза 11), с отговори в старата форма на /interpret и /interpret-stream
# ---------------------------------------------------------------------------
import json  # noqa: E402

_STATUS_BY_CODE = {"invalid_input": 400, "text_check": 502, "forecast_failed": 502, "no_events": 400,
                   "timeout": 504, "interrupted": 500, "internal": 500}


class LegacyResponse:
    """Отговор в старата форма: status_code, json() и text (при стрийм: редове "data: {...}")."""

    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body


def sse(events):
    return "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events)


def analyze(client, headers, body, stream=False, key=None, wait=120):
    """
    Пуска анализ като задача, изчаква я и връща отговор в старата форма.
    stream=False: като старото POST /interpret (HTTP статус, картите, interpretation, report_id, charged_cents, баланс).
    stream=True: като старото POST /interpret-stream (200 и събития, а грешките са събития "error").
    """
    headers = dict(headers or {})
    if key:
        headers["Idempotency-Key"] = key
    r = client.post(f"/jobs?wait={wait}", json=body, headers=headers)
    if r.status_code not in (200, 202):
        data = r.json()
        detail = data.get("detail")
        if stream and r.status_code not in (401, 403, 422):
            event = {"type": "error", "message": detail if isinstance(detail, str) else str(detail)}
            if r.status_code == 402:
                event["code"] = 402
            return LegacyResponse(200, text=sse([event]))
        return LegacyResponse(r.status_code, data)
    data = r.json()
    if data.get("crisis"):
        if stream:
            return LegacyResponse(200, text=sse([{"type": "crisis", "html": data["html"]}]))
        return LegacyResponse(200, {"interpretation": data["html"], "crisis": True, "charged_cents": 0,
                                    "report_id": None, "natal_chart": None})
    job = data["job"]
    assert job["status"] in ("succeeded", "failed", "cancelled"), job
    events = job.get("events", [])
    visible = [e for e in events if e.get("type") != "stage"]
    if job["status"] != "succeeded":
        error = job.get("error") or {"code": "internal", "message": "задачата не завърши"}
        if stream:
            event = {"type": "error", "message": error["message"]}
            if error["code"] in ("text_check", "forecast_failed"):
                event["code"] = 502
            return LegacyResponse(200, text=sse([e for e in visible if e.get("type") != "error"] + [event]))
        return LegacyResponse(_STATUS_BY_CODE.get(error["code"], 500), {"detail": error["message"]})
    if stream:
        return LegacyResponse(200, text=sse(visible))
    start = next((e for e in events if e.get("type") == "start"), {})
    complete = next(e for e in events if e.get("type") == "complete")
    text = next((e["interpretation"] for e in events if e.get("type") == "text"), None)
    if text is None:
        text = client.get(f"/reports/{job['report_id']}", headers=headers).json()["content"]
    result = {k: start.get(k) for k in ("natal_chart", "transit_chart", "partner_chart", "natal_aspects",
                                         "partner_natal_aspects")}
    result.update({"interpretation": text, "report_id": complete["report_id"], "charged_cents": complete["charged_cents"],
                   "balance_cents": complete.get("balance_cents"), "paid_cents": complete.get("paid_cents"),
                   "gift_cents": complete.get("gift_cents"), "crisis": False, "job_id": job["id"]})
    return LegacyResponse(200, result)


def pair_reply(user_prompt):
    """Подменен отговор на AI за анализ „за дата“ (card_engine.py): таг за всяка карта от подканата и затварящите секции."""
    import re
    ids = re.findall(r"^([ABSH]\d+): ", user_prompt, re.M)
    parts = [f"[[{i}]]\nТова изисква търпение и ясни граници в разговорите." for i in ids]
    parts += ["[[SUMMARY]]\nДенят е напрегнат и за двамата, затова е разумно да се говори бавно и да се изчаква, ако нещо тежи.",
              "[[ADVICE]]\n- Говорете спокойно и изчакайте, ако усетите напрежение.\n- Не бързайте с големи решения през този ден.",
              "[[ANSWER]]\nНай-добре е да говорите спокойно и бавно: първо изслушайте другия докрай, после кажете какво ви тежи, без да търсите виновен. Изберете един важен въпрос вместо много теми и оставете място за паузи. Така разговорът няма да се превърне в спор.",
              "[[CONCLUSION]]\nДенят е подходящ за внимателен разговор и неподходящ за прибързани решения."]
    return "\n\n".join(parts)



PERIOD_MARKERS = ("MODE: MONTHLY FORECAST BY FACT CARDS", "MODE: MONTH ADVICE FROM VERIFIED CARDS", "MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS",
                  "You write ONE short interpretation for ONE pre-written fact card")


def is_period_prompt(system_prompt):
    return any(marker in system_prompt for marker in PERIOD_MARKERS)


def period_reply(system_prompt, user_prompt):
    """Подменен отговор на AI за прогноза за период по карти (period_cards.py): таг за всяка карта, съвет за месеца и затварящите секции."""
    import re
    card = "Това изисква търпение и ясни граници в разговорите."
    if "MODE: MONTHLY FORECAST BY FACT CARDS" in system_prompt:
        ids = re.findall(r"^(M\d+E\d+): ", user_prompt, re.M)
        parts = [f"[[{i}]]\n{card}" for i in ids]
        parts.append("[[MONTH]]\nТози месец е добре да подбирате внимателно думите и да не бързате с големи решения.")
        return "\n\n".join(parts)
    if "MODE: MONTH ADVICE FROM VERIFIED CARDS" in system_prompt:
        return "[[MONTH]]\nТози месец е добре да подбирате внимателно думите и да не бързате с големи решения."
    if "MODE: PERIOD OVERVIEW FROM VERIFIED BLOCKS" in system_prompt:
        parts = ["[[SUMMARY]]\nПериодът е променлив и изисква търпение, а месеците следват един друг със сходни теми.",
                 "[[ADVICE]]\n- Говорете спокойно и изчакайте, ако усетите напрежение.\n- Не бързайте с големи решения през периода."]
        if "[[ANSWER]]" in system_prompt:
            parts.append("[[ANSWER]]\nНай-добре е да говорите спокойно и бавно: първо изслушайте другия докрай, после кажете какво ви тежи, без да търсите "
                         "виновен. Изберете един важен въпрос вместо много теми и оставете място за паузи. Така разговорът няма да се превърне в спор.")
        parts.append("[[CONCLUSION]]\nПериодът е подходящ за внимателни разговори и неподходящ за прибързани решения.")
        return "\n\n".join(parts)
    return card


async def period_fake(system_prompt, user_prompt, max_tokens=None, **kwargs):
    """Подмяна на AIInterpreter._call_api за прогноза за период: patch.object(main.ai_interpreter, "_call_api", period_fake)."""
    return period_reply(system_prompt, user_prompt)
