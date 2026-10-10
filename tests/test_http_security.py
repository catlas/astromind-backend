"""
Тестове за Фаза 14: заглавия за сигурност, таван на заявката и изключена документация на API-то.

Пускане: python -m unittest discover -s tests -p "test_http_security.py"
"""
import json
import unittest

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import http_security  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402
from testenv import CHART, register_and_login  # noqa: E402

EXPECTED = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}


class HeadersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def check(self, response):
        for name, value in EXPECTED.items():
            self.assertEqual(response.headers.get(name), value, name)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_ordinary_responses(self):
        self.check(self.client.get("/health"))
        self.check(self.client.get("/me"))                                    # 401
        self.check(self.client.get("/no-such-page"))                          # 404
        self.check(self.client.post("/login", json={"email": "x", "password": "y"}))

    def test_downloads_and_cors_preflight_have_them_too(self):
        headers = register_and_login(self.client, "sec-headers@test.bg")
        from unittest import mock
        with mock.patch.object(main.ai_interpreter, "interpret_chart", mock.AsyncMock(return_value="<p>т</p>")):
            rid = self.client.post("/jobs?wait=60", json=CHART, headers=headers).json()["job"]["report_id"]
        export = self.client.get(f"/reports/{rid}/export?format=md", headers=headers)
        self.check(export)
        preflight = self.client.options("/jobs", headers={"Origin": "http://localhost:5173",
                                                          "Access-Control-Request-Method": "POST"})
        self.assertEqual(preflight.headers.get("x-content-type-options"), "nosniff")

    def test_hsts_only_over_https(self):
        self.assertNotIn("strict-transport-security", self.client.get("/health").headers)
        secure = self.client.get("/health", headers={"X-Forwarded-Proto": "https"})
        self.assertIn("max-age=31536000", secure.headers["strict-transport-security"])

    def test_api_documentation_is_off(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)


class BodyLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_a_large_body_is_refused_before_it_is_read(self):
        big = json.dumps({"email": "a@b.bg", "password": "x" * (http_security.MAX_BODY_BYTES + 10)})
        r = self.client.post("/login", content=big, headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        self.assertIn("твърде голяма", r.json()["detail"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_a_chunked_body_without_a_length_is_cut_off(self):
        def chunks():
            for _ in range(12):
                yield b"x" * 100_000
        r = self.client.post("/login", content=chunks(), headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_a_normal_body_passes(self):
        r = self.client.post("/login", json={"email": "nobody@test.bg", "password": "Zvezdi2026x"})
        self.assertEqual(r.status_code, 401)

    def test_the_import_endpoints_have_a_higher_ceiling(self):
        headers = register_and_login(self.client, "sec-import@test.bg")
        self.assertEqual(http_security.limit_for("/reports/import"), http_security.MAX_IMPORT_BYTES)
        self.assertGreater(http_security.MAX_IMPORT_BYTES, http_security.MAX_BODY_BYTES)
        report = {"content": "в" * 150_000, "type": "general", "label": "Стар"}
        body = {"reports": [dict(report) for _ in range(10)]}                # ~3 МБ на байтове (кирилица): над общия таван
        r = self.client.post("/reports/import", json=body, headers=headers)
        self.assertEqual(r.status_code, 200, r.text[:200])
        huge = json.dumps({"reports": [{"content": "x" * 1000}], "pad": "y" * (http_security.MAX_IMPORT_BYTES + 10)})
        r = self.client.post("/reports/import", content=huge, headers={**headers, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)


if __name__ == "__main__":
    unittest.main()
