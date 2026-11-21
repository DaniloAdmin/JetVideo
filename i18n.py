from functools import lru_cache
import json
import locale
from pathlib import Path
import sys

LANGUAGES = {'ru': 'Русский', 'en': 'English', 'uk': 'Українська'}
_language = 'ru'


def system_language():
    if sys.platform == 'win32':
        import ctypes
        name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), '')
    else:
        name = locale.getlocale()[0] or ''
    code = name.lower().partition('_')[0]
    return code if code in LANGUAGES else 'en'


def language():
    return _language


def set_language(code):
    global _language
    if code not in LANGUAGES:
        raise ValueError(f'Unsupported locale: {code}')
    _language = code


@lru_cache(maxsize=2)
def catalog(code):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    path = base / 'locales' / f'{code}.json'
    return json.loads(path.read_text(encoding='utf-8'))


def tr(message):
    return message if _language == 'ru' else catalog(_language).get(message, message)


def file_types(types):
    return [(tr(label), pattern) for label, pattern in types]


def localized_combobox(parent, variable, values, **kwargs):
    from tkinter import ttk

    values = tuple(values)
    labels = tuple(tr(item) for item in values)
    box = ttk.Combobox(parent, values=labels, state='readonly', **kwargs)

    def sync(*_args):
        value = variable.get()
        if value in values:
            box.current(values.index(value))
        else:
            box.set(value)

    def select(_event):
        index = box.current()
        if index != -1:
            variable.set(values[index])

    sync()
    observer = variable.trace_add('write', sync)
    box.bind('<<ComboboxSelected>>', select)
    box.bind('<Destroy>', lambda _event: variable.trace_remove('write', observer))
    return box
