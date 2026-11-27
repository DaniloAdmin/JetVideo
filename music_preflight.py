from collections import OrderedDict
from collections.abc import Callable, Mapping
from hashlib import sha256
from pathlib import Path
from threading import Event

from app_info import resolve_video_tools
from i18n import tr
from media_files import VIDEO_EXTS, fingerprint, stable_file
from music_check import MusicCheckCancelled, MusicCheckError, MusicResult, MusicScanner


class MusicPreflightBlocked(RuntimeError):
    pass


_results: OrderedDict[tuple[str, str, bytes, str], MusicResult] = OrderedDict()


def _time_label(ms: int) -> str:
    minutes, seconds = divmod(ms // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{ms % 1000:03d}'


def preflight_music(ready: Path, credentials: Mapping, stop: Event, log: Callable[[str], None]) -> dict[Path, str]:
    if stop.is_set():
        raise MusicCheckCancelled()
    try:
        files = sorted((path for path in ready.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTS),
                       key=lambda path: path.name.casefold())
    except OSError as exc:
        raise MusicPreflightBlocked(tr('Не удалось прочитать папку ready для проверки музыки.')) from exc
    if not files:
        log(tr('В ready нет видео для проверки музыки.'))
        return {}
    tools = resolve_video_tools()
    if tools is None:
        raise MusicPreflightBlocked(tr('Пара FFmpeg и FFprobe не найдена.'))
    token = (credentials.get('ACRCLOUD_TOKEN') or '').strip()
    region = (credentials.get('ACRCLOUD_REGION') or '').strip()
    container = (credentials.get('ACRCLOUD_CONTAINER_ID') or '').strip()
    account = sha256(token.encode('utf-8')).digest()
    try:
        scanner = MusicScanner(token, region, container, *tools)
    except MusicCheckError as exc:
        raise MusicPreflightBlocked(str(exc)) from exc
    clear = {}
    with_matches = errors = 0
    for index, path in enumerate(files, 1):
        if stop.is_set():
            raise MusicCheckCancelled()
        log(tr('Проверка музыки {0}/{1} · {2}').format(index, len(files), path.name))
        try:
            with stable_file(path, 0):
                before = path.stat()
                digest = fingerprint(path)
                key = (region, container, account, digest)
                result = _results.get(key)
                if result is None:
                    result = scanner.scan(path, stop)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise MusicCheckError(tr('Видео изменилось во время проверки музыки.'))
                _results[key] = result
                _results.move_to_end(key)
                if len(_results) > 256:
                    _results.popitem(last=False)
        except (MusicCheckError, OSError) as exc:
            if stop.is_set():
                raise MusicCheckCancelled() from exc
            detail = str(exc) if isinstance(exc, MusicCheckError) else tr('Видео занято или недоступно.')
            log(tr('⚠ {0} · Проверка не выполнена · {1}').format(path.name, detail))
            errors += 1
            continue
        if stop.is_set():
            raise MusicCheckCancelled()
        if result.cleanup_failed:
            log(tr('⚠ {0} · Не удалось удалить временное аудио из ACRCloud.').format(path.name))
        if result.matches:
            with_matches += 1
            for match in result.matches:
                track = ' — '.join(part for part in (match.artist, match.title) if part)
                timing = tr('таймкод недоступен')
                if match.start_ms is not None:
                    timing = _time_label(match.start_ms)
                    if match.end_ms is not None:
                        timing += '–' + _time_label(match.end_ms)
                log(tr('⚠ {0} · Найдена музыка · {1} · {2}').format(path.name, timing, track))
        else:
            clear[path] = digest
            status = tr('Нет аудиодорожки') if result.no_audio else tr('Совпадений нет')
            log(f'✓ {path.name} · {status}')
    if with_matches or errors:
        raise MusicPreflightBlocked(
            tr('Проверено {0} видео. С совпадениями: {1}. Проверка не выполнена: {2}. Подробности в журнале.').format(
                len(files), with_matches, errors))
    return clear
