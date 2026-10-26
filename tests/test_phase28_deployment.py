"""
Фаза 28 (R09): правила за production, безопасният опис на изданието и изходът от всички устройства.

- production не тръгва с примерен SECRET_KEY, с повече от един работен процес, с изключена проверка на текстовете или с включена документация;
- извън production същите настройки са позволени (тестове, локална работа);
- GET /version показва commit и версиите на правилата, но нито тайни, нито адреси, нито имена на настройки;
- /docs, /redoc и /openapi.json не съществуват;
- екраните: подсказката за парола съвпада с правилото на сървъра, а „Изход навсякъде“ не твърди успех при грешка.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import testenv  # noqa: F401  (трябва да е преди main)
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import deployment  # noqa: E402
import main  # noqa: E402
from rate_limit import limiter  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
GOOD = {"RENDER": "true", "SECRET_KEY": "x" * 48}


class ProductionRulesTest(unittest.TestCase):
    def problems(self, **extra):
        return deployment.production_problems({**GOOD, **extra})

    def test_a_clean_production_environment_passes(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems(WEB_CONCURRENCY="1", TEXT_CHECK_MODE="enforce", ENABLE_API_DOCS="0"), [])

    def test_test_and_placeholder_secrets_are_refused(self):
        for key in ("test-secret", "Secret", "changeme", " dev "):
            self.assertTrue(any("SECRET_KEY" in p for p in self.problems(SECRET_KEY=key)), msg=key)

    def test_more_than_one_worker_is_refused(self):
        for value in ("2", "4", "auto", "0"):
            self.assertTrue(any("WEB_CONCURRENCY" in p for p in self.problems(WEB_CONCURRENCY=value)), msg=value)

    def test_the_text_check_cannot_be_switched_off_and_docs_stay_closed(self):
        for mode in ("off", "warn"):
            self.assertTrue(any("TEXT_CHECK_MODE" in p for p in self.problems(TEXT_CHECK_MODE=mode)), msg=mode)
        self.assertTrue(any("ENABLE_API_DOCS" in p for p in self.problems(ENABLE_API_DOCS="1")))

    def test_enforce_raises_and_outside_production_nothing_is_checked(self):
        with self.assertRaises(RuntimeError) as caught:
            deployment.enforce_production_rules({"RENDER": "true", "SECRET_KEY": "test-secret"})
        self.assertIn("SECRET_KEY", str(caught.exception))
        self.assertEqual(deployment.production_problems({"SECRET_KEY": "test-secret", "WEB_CONCURRENCY": "8", "TEXT_CHECK_MODE": "off"}), [])
        self.assertFalse(deployment.is_production({}))
        self.assertTrue(deployment.is_production({"APP_ENV": "production"}))

    def test_a_short_real_secret_only_warns(self):
        env = {"RENDER": "true", "SECRET_KEY": "k" * 20}
        self.assertEqual(deployment.production_problems(env), [])
        self.assertTrue(deployment.production_warnings(env))
        deployment.enforce_production_rules(env)                       # не хвърля

    def test_this_test_process_is_not_production(self):
        self.assertFalse(deployment.is_production())
        self.assertEqual(deployment.production_problems(), [])


class VersionEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        limiter.reset()

    def test_version_lists_the_rule_versions_and_the_ephemeris(self):
        data = self.client.get("/version").json()
        self.assertEqual(set(data["versions"]), {"snapshot", "calendar", "factpack", "orbs", "checker", "prompt"})
        self.assertEqual(len(data["ephemeris"]["fingerprint"]), 16)
        self.assertIn("production", data)

    def test_the_commit_comes_from_the_environment_and_nothing_secret_is_shown(self):
        env = {"RENDER_GIT_COMMIT": "a" * 40, "SECRET_KEY": "very-secret-value", "DATABASE_URL": "postgresql://u:p@h/db", "OPENAI_API_KEY": "sk-x"}
        text = json.dumps(deployment.release_info(env))
        self.assertIn("a" * 40, text)
        for secret in ("very-secret-value", "postgresql", "sk-x", "DATABASE_URL", "OPENAI", auth.SECRET_KEY):
            self.assertNotIn(secret, text)
        self.assertIsNone(deployment.release_info({})["commit"])

    def test_no_api_documentation_is_served(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, msg=path)


class ScreenTextTest(unittest.TestCase):
    def source(self, name):
        return open(os.path.join(SRC, name), encoding="utf-8").read()

    def test_the_password_hint_matches_the_server_rule(self):
        settings = self.source("pages/Settings.jsx")
        self.assertNotIn("Минимум 6", settings)
        self.assertIn("Минимум 10 символа, с поне една буква и една цифра", settings)
        self.assertEqual(auth.PASSWORD_MIN_LENGTH, 10)
        self.assertIn("minLength={10}", self.source("pages/AuthLinkPages.jsx"))

    def test_logout_everywhere_shows_a_failure_and_does_not_claim_success(self):
        settings = self.source("pages/Settings.jsx")
        block = settings[settings.index("api.post('/logout-all')") - 400: settings.index("api.post('/logout-all')") + 700]
        self.assertNotRegex(block, r"catch\s*\{\s*/\*")                       # празно „поглъщане“ на грешката
        self.assertIn("setLogoutAllMsg(apiErrorMessage", block)
        self.assertRegex(block, r"return;\s*\}\s*clearSessionAndRedirect")      # при грешка няма излизане


def run_node(script):
    """Копира money.js и howItWorks.js като .mjs (с поправен импорт) и изпълнява скрипта."""
    work = tempfile.mkdtemp()
    try:
        for name in ("money", "howItWorks"):
            source = open(os.path.join(SRC, "utils", f"{name}.js"), encoding="utf-8").read().replace("'./money'", "'./money.mjs'")
            open(os.path.join(work, f"{name}.mjs"), "w", encoding="utf-8").write(source)
        full = f"import * as M from '{work}/money.mjs'; import * as H from '{work}/howItWorks.mjs';\n{script}"
        out = subprocess.run(["node", "--input-type=module", "-e", full], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return json.loads(out.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("node"), "Node.js не е наличен")
class PricingTextTest(unittest.TestCase):
    def test_the_double_forecast_surcharge_is_for_the_whole_period(self):
        result = run_node("console.log(JSON.stringify({ text: M.partnerSurchargeText(), custom: M.partnerSurchargeText({ forecast_partner_extra: 90 }) }));")
        self.assertEqual(result["text"], "+0,60\u00a0€ за целия период")
        self.assertEqual(result["custom"], "+0,90\u00a0€ за целия период")

    def test_during_the_beta_everything_is_free_and_the_gift_is_a_future_basic_only_credit(self):
        steps = run_node("console.log(JSON.stringify(H.billingSteps({ enforced: false, gift: 500, prices: { forecast_partner_extra: 60 } })));")
        self.assertEqual(len(steps), 3)
        text = " ".join(f"{s['title']} {s['text']}" for s in steps)
        self.assertIn("безплатни", steps[0]["title"])
        self.assertIn("5,00\u00a0€", steps[1]["title"])
        self.assertIn("само за основните анализи", steps[1]["text"])
        self.assertIn("от старта на таксуването", steps[1]["text"])
        self.assertIn("допълнително заплащане (предстои)", steps[2]["text"])
        self.assertNotRegex(text.lower(), r"пробен период|безплатен пробен|trial")

    def test_after_the_start_the_gift_is_still_limited_and_premium_is_paid_from_deposits(self):
        steps = run_node("console.log(JSON.stringify(H.billingSteps({ enforced: true, gift: 500 })));")
        self.assertEqual(len(steps), 3)
        self.assertIn("само за анализи за един човек", steps[0]["text"])
        self.assertIn("само от внесени средства", steps[2]["text"])
        self.assertIn("допълнително заплащане (предстои)", steps[2]["text"])

    def test_the_product_block_is_three_real_steps_and_the_check_scope_is_honest(self):
        result = run_node("console.log(JSON.stringify({ steps: H.PRODUCT_STEPS, scope: H.CHECK_SCOPE }));")
        self.assertEqual(len(result["steps"]), 3)
        self.assertIn("не гарантира", result["scope"])
        self.assertIn("част от твърденията", result["scope"])
        self.assertIn(result["scope"], result["steps"][2]["text"])

    def test_no_page_promises_correct_texts_or_uses_the_old_currency(self):
        for dirpath, _, files in os.walk(SRC):
            for name in files:
                if not name.endswith((".js", ".jsx")) or name == "bulgarianCities.js":
                    continue
                text = open(os.path.join(dirpath, name), encoding="utf-8").read()
                self.assertNotRegex(text, r"\bлв\.?(?![а-я])|\bлева\b", msg=name)
                self.assertNotIn("Изчисленията са точни", text, msg=name)
                self.assertNotIn("<span className=\"text-[#5211d4] block\">Точен.</span>", text, msg=name)
        home = open(os.path.join(SRC, "pages", "Home.jsx"), encoding="utf-8").read()
        for needle in ("HowItWorks", "PRODUCT_STEPS", "billingSteps", 'id="how-it-works"', "partnerSurchargeSoon()", "PRICE_SOON", "PlanLimits"):
            self.assertIn(needle, home)
        for page in ("Balance.jsx", "Settings.jsx", "Legal.jsx"):
            self.assertIn("<HowItWorks", open(os.path.join(SRC, "pages", page), encoding="utf-8").read(), msg=page)

    def test_the_headings_helper_and_the_release_check_refuse_what_they_should(self):
        headers = load_script("check_headers")
        good = {"x-frame-options": "DENY", "content-security-policy": "frame-ancestors 'none'", "referrer-policy": "strict-origin-when-cross-origin",
                "x-content-type-options": "nosniff", "strict-transport-security": "max-age=31536000; includeSubDomains"}
        self.assertEqual(headers.evaluate(good, https=True), [])
        self.assertEqual(len(headers.evaluate({}, https=True)), 5)
        self.assertEqual(len(headers.evaluate({}, https=False)), 4)                             # HSTS само за https
        self.assertTrue(headers.evaluate({**good, "x-frame-options": "SAMEORIGIN"}, https=True))
        release = load_script("release_check")
        clean = ("const LEGAL_DRAFT = false;\nconst PROCESSORS_VERIFIED = true;\nconst COMPANY = {\n  name: 'Фирма ЕООД',\n  eik: '123456789',\n"
                 "  address: 'София',\n  email: 'a@b.bg',\n};\n")
        self.assertEqual(release.blockers(clean), [])
        self.assertTrue(release.blockers(clean.replace("LEGAL_DRAFT = false", "LEGAL_DRAFT = true")))
        self.assertTrue(release.blockers(clean.replace("'123456789'", "'[ЕИК]'")))
        self.assertTrue(release.blockers(clean.replace("PROCESSORS_VERIFIED = true", "PROCESSORS_VERIFIED = false")))
        self.assertTrue(release.blockers(""))
        real = open(os.path.join(SRC, "pages", "Legal.jsx"), encoding="utf-8").read()
        self.assertGreaterEqual(len(release.blockers(real)), 3)                                 # докато собственикът не е попълнил данните

    def test_the_site_is_covered_by_a_stopgap_and_documented(self):
        index = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.assertIn('name="referrer"', index)
        self.assertIn("/frame-guard.js", index)
        self.assertIn("window.top !== window.self", open(os.path.join(ROOT, "public", "frame-guard.js"), encoding="utf-8").read())
        config = open(os.path.join(ROOT, "vite.config.js"), encoding="utf-8").read()
        self.assertIn("__BUILD_COMMIT__", config)
        self.assertNotIn("frame-ancestors", config.split("const csp")[1].split("].join")[0])      # в meta CSP не работи; идва от хостинга




if __name__ == "__main__":
    unittest.main()
