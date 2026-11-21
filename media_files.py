from __future__ import annotations
from i18n import tr
import ctypes
import hashlib
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

VIDEO_EXTS = {'.mp4', '.mov', '.webm'}
NUMBERED = re.compile(r'(?:(?:часть|частина|part)[ _-]+)?(\d+)(?:_with_ad)?', re.I)

def video_id(path: Path) -> int:
    match = NUMBERED.fullmatch(path.stem)
    if match:
        return int(match[1])
    return 10**18 + int.from_bytes(hashlib.sha256(path.name.casefold().encode()).digest()[:8], 'big')

def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def commit_new_file(staging: Path, destination: Path) -> None:
    if os.name == 'nt':
        staging.rename(destination)
    else:
        os.link(staging, destination)

def validate_folders(root: Path, config: dict) -> tuple[Path, Path, Path]:
    folders = tuple((root / config.get(key, default)).resolve() for key, default in
                    zip(('videos_folder', 'scheduled_folder', 'posted_folder'), ('ready', 'scheduled', 'posted')))
    for folder in folders:
        if folder == root.resolve() or folder in root.resolve().parents:
            raise ValueError(tr('Выбери отдельные папки для видео, вне корня служебных данных.'))
    for i, left in enumerate(folders):
        for right in folders[i+1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError(tr('Папки ready, scheduled и posted не должны совпадать или содержать друг друга.'))
    return folders

@contextmanager
def stable_file(path: Path, age_seconds: float = 15):
    before = path.stat()
    if before.st_size == 0 or time.time() - before.st_mtime < age_seconds:
        raise OSError(tr('Файл ещё копируется или слишком новый; проверим в следующем цикле.'))
    handle = None
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(path.resolve()), 0x80000000, 1, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(tr('Видео занято другой программой; дождись окончания копирования.'))
    try:
        if age_seconds:
            time.sleep(min(0.5, age_seconds))
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError(tr('Видео изменяется; обработка отложена.'))
        yield
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
