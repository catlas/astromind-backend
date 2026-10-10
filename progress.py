"""
Връзка между генерацията и задачата (Фаза 11): етапът, в който е анализът.

Задачата (jobs.py) закача функция за времето на единичен анализ, а AI слоят съобщава "checking", когато почне проверката
на готовия текст. Без закачена функция (прогнози, тестове, други заявки) викането не прави нищо. Ползва се ContextVar,
за да не се добавя параметър към всеки метод на AI слоя.
"""
from contextvars import ContextVar, Token
from typing import Callable, Optional

_callback: ContextVar[Optional[Callable[[str], None]]] = ContextVar("job_stage_callback", default=None)


def bind(callback: Callable[[str], None]) -> Token:
    return _callback.set(callback)


def unbind(token: Token) -> None:
    _callback.reset(token)


def stage(name: str) -> None:
    """Съобщава етапа на текущата задача (ако има такава). Грешка във функцията никога не спира анализа."""
    callback = _callback.get()
    if callback is None:
        return
    try:
        callback(name)
    except Exception as exc:  # телеметрията за етапа не е причина да се провали анализът
        print(f"⚠️ Етапът на задачата не се записа ({type(exc).__name__})")
