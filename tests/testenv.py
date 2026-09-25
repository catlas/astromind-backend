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
for key in ("RESEND_API_KEY", "SMTP_HOST", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
    os.environ.pop(key, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSWORD = "Zvezdi2026x"
CHART = {"date": "1990-05-15", "time": "14:30", "lat": 42.6977, "lon": 23.3219}


def register_and_login(client, email, password=PASSWORD, name="Тест"):
    r = client.post("/register", json={"email": email, "password": password, "full_name": name})
    assert r.status_code == 200, r.text
    r = client.post("/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
