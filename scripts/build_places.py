"""
Строи backend/data/places.sqlite от GeoNames cities15000.zip (Фаза 12): места с над 15 000 жители, координати, зона и имена
(латиница и кирилица) за търсене. Не се пуска от приложението; резултатът се комитва.

Източник: https://download.geonames.org/export/dump/cities15000.zip, лиценз CC BY 4.0 (https://www.geonames.org).
Пускане: python scripts/build_places.py <път до cities15000.zip>
"""
import gzip
import hashlib
import io
import os
import re
import sqlite3
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from places import normalize  # noqa: E402

CYRILLIC = re.compile(r"[А-Яа-яЀ-ӿ]")
WANTED = re.compile(r"[A-Za-zА-Яа-яЀ-ӿ]")        # само имена с латиница или кирилица
MAX_NAMES = 60


def main(zip_path: str) -> None:
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "places.sqlite")
    if os.path.exists(out):
        os.remove(out)
    raw = open(zip_path, "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        lines = archive.read("cities15000.txt").decode("utf-8").splitlines()

    db = sqlite3.connect(out)
    db.executescript("""
        CREATE TABLE place (id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, admin1 TEXT,
                            population INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, timezone TEXT);
        CREATE TABLE name (norm TEXT NOT NULL, place_id INTEGER NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    for line in lines:
        f = line.split("\t")
        if len(f) < 19:
            continue
        place_id, name, ascii_name, alternates = int(f[0]), f[1], f[2], f[3]
        lat, lon, country, admin1, population, timezone = float(f[4]), float(f[5]), f[8], f[10], int(f[14] or 0), f[17]
        db.execute("INSERT INTO place VALUES (?,?,?,?,?,?,?,?)", (place_id, name, country, admin1, population, lat, lon, timezone))
        usable = [a for a in alternates.split(",") if a and WANTED.search(a)]
        cyrillic = [a for a in usable if CYRILLIC.search(a)]
        names = [name, ascii_name] + cyrillic + [a for a in usable if not CYRILLIC.search(a)]   # кирилицата първа: най-често се търси
        seen = []
        for candidate in names:
            key = normalize(candidate)
            if key and key not in seen:
                seen.append(key)
            if len(seen) >= MAX_NAMES:
                break
        db.executemany("INSERT INTO name VALUES (?,?)", [(key, place_id) for key in seen])
    db.execute("CREATE INDEX name_norm ON name (norm)")
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("source", "GeoNames cities15000.zip (https://download.geonames.org/export/dump/cities15000.zip)"),
        ("license", "CC BY 4.0, https://www.geonames.org"), ("sha256", digest), ("places", str(len(lines)))])
    db.commit()
    db.execute("VACUUM")
    db.close()
    # В хранилището влиза само пакетираният файл; places.py го разпакова при първо ползване
    with open(out, "rb") as source, gzip.open(out + ".gz", "wb", compresslevel=9) as target:
        target.write(source.read())
    os.remove(out)
    print(f"{len(lines)} места, {os.path.getsize(out + '.gz') / 1e6:.1f} МБ (gz), sha256 {digest[:16]}…")


if __name__ == "__main__":
    main(sys.argv[1])
