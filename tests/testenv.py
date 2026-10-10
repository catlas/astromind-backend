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
