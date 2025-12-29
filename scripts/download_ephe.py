"""
Автоматичен изтегляч на Swiss Ephemeris файлове
Изтегля необходимите .se1 файлове от https://www.astro.com/ftp/swisseph/ephe/
"""

import os
import requests
from pathlib import Path

# URL база за ефемеридите
EPHE_BASE_URL = "https://www.astro.com/ftp/swisseph/ephe"

# Необходими файлове: име_файл -> описание
REQUIRED_FILES = {
    "sepl_18.se1": "Планети (1800-2399)",
    "semo_18.se1": "Луна",
    "seas_18.se1": "Астероиди"
}


def download_file(url: str, filepath: Path, chunk_size: int = 8192) -> bool:
    """
    Изтегля файл от URL и показва прогрес.
    
    Args:
        url: URL адрес на файла
        filepath: Път към целевия файл
        chunk_size: Размер на chunk-а за изтегляне
        
    Returns:
        True ако изтеглянето е успешно, False иначе
    """
    try:
        print(f"Изтегляне на {filepath.name}...")
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\rПрогрес: {percent:.1f}% ({downloaded}/{total_size} bytes)", end='', flush=True)
        
        print(f"\n✓ Успешно изтеглен {filepath.name}")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Грешка при изтегляне на {filepath.name}: {e}")
        return False
    except Exception as e:
        print(f"\n✗ Неочаквана грешка при изтегляне на {filepath.name}: {e}")
        if filepath.exists():
            filepath.unlink()  # Изтриване на частично изтегления файл
        return False


def ensure_ephe_directory(base_dir: Path) -> Path:
    """
    Създава директорията ephe ако не съществува.
    
    Args:
        base_dir: Базова директория на проекта
        
    Returns:
        Път към ephe директорията
    """
    ephe_dir = base_dir / "ephe"
    ephe_dir.mkdir(exist_ok=True)
    return ephe_dir


def download_ephemeris_files(base_dir: Path = None) -> bool:
    """
    Основна функция за изтегляне на всички необходими ефемеридни файлове.
    
    Args:
        base_dir: Базова директория (ако не е предоставена, използва текущата директория на скрипта)
        
    Returns:
        True ако всички файлове са изтеглени успешно, False иначе
    """
    if base_dir is None:
        # Намиране на backend директорията (родител на scripts)
        script_dir = Path(__file__).parent
        base_dir = script_dir.parent
    
    ephe_dir = ensure_ephe_directory(base_dir)
    
    print("=" * 60)
    print("Автоматично изтегляне на Swiss Ephemeris файлове")
    print("=" * 60)
    
    all_success = True
    
    for filename, description in REQUIRED_FILES.items():
        filepath = ephe_dir / filename
        
        # Проверка дали файлът вече съществува
        if filepath.exists():
            file_size = filepath.stat().st_size
            print(f"✓ {filename} вече съществува ({file_size:,} bytes) - пропускане")
            continue
        
        # Изтегляне на файла
        url = f"{EPHE_BASE_URL}/{filename}"
        success = download_file(url, filepath)
        
        if not success:
            all_success = False
    
    print("=" * 60)
    if all_success:
        print("✓ Всички файлове са успешно изтеглени или вече съществуват!")
    else:
        print("⚠ Някои файлове не бяха изтеглени успешно. Моля опитайте отново.")
    print("=" * 60)
    
    return all_success


if __name__ == "__main__":
    # При стартиране директно, изтегля всички файлове
    download_ephemeris_files()




