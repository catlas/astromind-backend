"""
Фаза 28 (R09): правила за production и безопасният опис на изданието (GET /version).

- is_production(): Render слага RENDER=true; APP_ENV=production го налага и другаде.
- enforce_production_rules(): стартът се отказва при очевидно опасна настройка. Брояч на заявките в паметта и регистърът на
  задачите са на ниво процес, затова повече от един работен процес (WEB_CONCURRENCY) в production не се допуска.
- release_info(): без тайни и без подробности за инфраструктурата: какъв код и какви версии на правилата са пуснати.
"""
import functools
import logging
import os
from typing import Any, Dict, List

PLACEHOLDER_KEYS = {"test-secret", "secret", "changeme", "change-me", "change_me", "dev", "dev-secret", "your-secret-key", "supersecret"}
MIN_SECRET_LENGTH = 32


def is_production(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("RENDER", "")).lower() == "true" or str(env.get("APP_ENV", "")).lower() == "production"


def production_problems(env=None) -> List[str]:
    """Списък на причините, поради които production не бива да тръгне. Празен списък = всичко е наред."""
    env = os.environ if env is None else env
    if not is_production(env):
        return []
    problems: List[str] = []
    key = str(env.get("SECRET_KEY", ""))
    if key.strip().lower() in PLACEHOLDER_KEYS:
        problems.append("SECRET_KEY е примерна (тестова) стойност")
    try:
        workers = int(str(env.get("WEB_CONCURRENCY", "1")).strip() or "1")
    except ValueError:
        workers = 0
    if workers != 1:
        problems.append("WEB_CONCURRENCY трябва да е 1: ограничителят на заявките и регистърът на задачите са на ниво процес")
    if str(env.get("TEXT_CHECK_MODE", "enforce")).strip().lower() != "enforce":
        problems.append("TEXT_CHECK_MODE трябва да е enforce: проверката на текстовете не може да се изключва в production")
    if str(env.get("ENABLE_API_DOCS", "")) == "1":
        problems.append("ENABLE_API_DOCS=1: документацията на API-то трябва да е изключена в production")
    return problems


def production_warnings(env=None) -> List[str]:
    """Неща, които не спират старта, но трябва да се видят в журнала."""
    env = os.environ if env is None else env
    if is_production(env) and len(str(env.get("SECRET_KEY", ""))) < MIN_SECRET_LENGTH:
        return [f"SECRET_KEY е по-къс от {MIN_SECRET_LENGTH} знака: сменете го с дълъг случаен низ"]
    return []


def enforce_production_rules(env=None) -> None:
    problems = production_problems(env)
    if problems:
        raise RuntimeError("Стартът в production е отказан: " + "; ".join(problems))
    for warning in production_warnings(env):
        logging.getLogger("astromind.deployment").warning(warning)


@functools.lru_cache(maxsize=1)
def _ephemeris_info() -> Dict[str, Any]:
    """Файловете с ефемериди не се менят, докато процесът работи: отпечатъкът се смята веднъж."""
    import engine
    low, high = engine.supported_year_range()
    return {"fingerprint": engine.ephemeris_fingerprint(), "years": [low, high] if high >= low else None}


def release_info(env=None) -> Dict[str, Any]:
    """Безопасно описание на изданието: само версии и отпечатъци, нищо от конфигурацията на сървъра."""
    env = os.environ if env is None else env
    import report_snapshot
    commit = str(env.get("RENDER_GIT_COMMIT", "") or env.get("GIT_COMMIT", "")).strip()
    return {
        "commit": commit[:40] if commit else None,
        "versions": report_snapshot.versions(),
        "ephemeris": dict(_ephemeris_info()),
        "production": is_production(env),
    }
