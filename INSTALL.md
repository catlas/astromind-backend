# Инструкции за инсталация

## Инсталация на зависимости

### Стъпка 1: Активиране на виртуалната среда

```bash
cd backend
source venv/bin/activate
```

### Стъпка 2: Инсталация на основните пакети

```bash
pip install fastapi uvicorn openai python-dotenv requests pydantic
```

### Стъпка 3: Инсталация на pyswisseph

`pyswisseph` може да има проблеми с компилацията на macOS, особено с Python 3.13. Ето няколко алтернативни метода:

#### Метод 1: Използване на Conda (Препоръчително)

```bash
# Ако използвате conda
conda install -c conda-forge pyswisseph
```

#### Метод 2: Използване на по-стара версия на Python

Ако имате проблеми с Python 3.13, опитайте с Python 3.11:

```bash
# Създаване на ново venv с Python 3.11
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Метод 3: Инсталация след обновяване на Xcode Command Line Tools

```bash
# Обновяване на Xcode Command Line Tools
sudo xcode-select --install

# След това опитайте отново
pip install pyswisseph
```

#### Метод 4: Инсталация с конкретни компилаторни флагове

```bash
# За Apple Silicon (M1/M2/M3)
arch -arm64 pip install pyswisseph

# Или за Intel
arch -x86_64 pip install pyswisseph
```

### Стъпка 4: Изтегляне на ефемеридите

След като `pyswisseph` е инсталиран успешно:

```bash
python scripts/download_ephe.py
```

Това ще изтегли необходимите файлове:
- `sepl_18.se1` (Планети 1800-2399)
- `semo_18.se1` (Луна)
- `seas_18.se1` (Астероиди)

в директорията `backend/ephe/`.

## Тестване на инсталацията

След като всички зависимости са инсталирани:

```bash
python engine.py
```

Това ще стартира тестово изчисление на натална карта.

## Проблеми с инсталацията?

Ако продължавате да имате проблеми с `pyswisseph`:

1. Проверете версията на Python: `python --version`
2. Проверете версията на Xcode Command Line Tools: `xcode-select -p`
3. Опитайте да инсталирате от източника: https://github.com/pyswisseph/pyswisseph
4. Проверете съвместимостта: https://pypi.org/project/pyswisseph/




