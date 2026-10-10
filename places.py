"""
Места от проверим източник (Фаза 12): GeoNames cities15000 (CC BY 4.0, https://www.geonames.org), вградена в data/places.sqlite.

AI помага само с изписването на името (виж geocode_api.py), а координатите идват оттук. Място, което го няма в базата
(под 15 000 жители), остава „непроверено“ и потребителят го коригира ръчно. Базата се чете само за четене; не се зарежда
в паметта (заявка по индекс), затова не тежи на безплатния план.
"""
import gzip
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

PACKED_PATH = os.getenv("PLACES_DB_GZ", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "places.sqlite.gz"))
ATTRIBUTION = "Данни за местата: GeoNames (geonames.org), лиценз CC BY 4.0."

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_local = threading.local()
_unpack_lock = threading.Lock()


def _unpacked_path() -> Optional[str]:
    """Разпакованата база във временната папка (един път за процеса и версията на файла). None, ако пакетът липсва."""
    if not os.path.exists(PACKED_PATH):
        return None
    stamp = f"{os.path.getsize(PACKED_PATH)}-{int(os.path.getmtime(PACKED_PATH))}"
    target = os.path.join(tempfile.gettempdir(), f"astromind_places_{stamp}.sqlite")
    with _unpack_lock:
        if not os.path.exists(target):
            partial = f"{target}.{os.getpid()}.part"
            with gzip.open(PACKED_PATH, "rb") as source, open(partial, "wb") as out:
                shutil.copyfileobj(source, out)
            os.replace(partial, target)
    return target


def normalize(text: str) -> str:
    """Ключ за търсене: без диакритики, малки букви, интервали вместо препинателни знаци ("Wien", "wien", "Wïen" са едно)."""
    kept: List[str] = []
    for ch in unicodedata.normalize("NFKD", str(text or "")):
        # Диакритиките на латиницата се махат (ö -> o); „й“ е буква на кирилицата (и + знак) и се пази
        if unicodedata.combining(ch) and not (kept and "CYRILLIC" in unicodedata.name(kept[-1], "")):
            continue
        kept.append(ch)
    composed = unicodedata.normalize("NFC", "".join(kept))
    return _NON_WORD.sub(" ", composed.casefold().replace("ß", "ss")).strip()


@dataclass(frozen=True)
class Place:
    id: int
    name: str
    country: str          # ISO 3166-1 alpha-2
    admin1: str
    population: int
    lat: float
    lon: float
    timezone: str

    def public(self) -> dict:
        return {"id": self.id, "city": self.name, "country_code": self.country, "region": self.admin1,
                "population": self.population, "lat": round(self.lat, 4), "lon": round(self.lon, 4),
                "timezone": self.timezone, "source": "geonames"}


def _connection() -> Optional[sqlite3.Connection]:
    conn = getattr(_local, "conn", None)
    if conn is None:
        path = _unpacked_path()
        if path is None:
            return None
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        _local.conn = conn
    return conn


def available() -> bool:
    return _connection() is not None


def search(name: str, country: Optional[str] = None, limit: int = 8) -> List[Place]:
    """Местата с точно това име (на всеки език в базата), най-големите първи. country е ISO код (по желание)."""
    key = normalize(name)
    conn = _connection()
    if not key or conn is None:
        return []
    sql = ("SELECT DISTINCT p.id, p.name, p.country, p.admin1, p.population, p.lat, p.lon, p.timezone "
           "FROM name n JOIN place p ON p.id = n.place_id WHERE n.norm = ?")
    args: list = [key]
    if country:
        sql += " AND p.country = ?"
        args.append(country.upper())
    sql += " ORDER BY p.population DESC LIMIT ?"
    args.append(limit)
    return [Place(*row) for row in conn.execute(sql, args).fetchall()]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Разстояние по голям кръг (хаверсинус)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def info() -> dict:
    conn = _connection()
    return dict(conn.execute("SELECT key, value FROM meta").fetchall()) if conn else {}
