from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
from uuid import uuid4

import requests

from i18n import tr


MUSIC_REGIONS = ('eu-west-1', 'us-west-2', 'ap-southeast-1')
_CONTAINERS = 'https://api-v2.acrcloud.com/api/fs-containers'
_TIMEOUT = (6, 30)
_POLL_SECONDS = 3
_RECOGNITION_TIMEOUT = 180
_AUDIO_LIMIT = 500_000_000
_WIN_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_INCOMPLETE = 'ACRCloud вернул неполный или неизвестный результат.'
_MEDIA_ERROR = 'Не удалось прочитать аудиодорожку видео.'


class MusicCheckError(RuntimeError):
    pass


class MusicCheckCancelled(Exception):
    pass


@dataclass(frozen=True)
class MusicMatch:
    title: str
    artist: str
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True)
class MusicResult:
    matches: tuple[MusicMatch, ...] = ()
    no_audio: bool = False
    cleanup_failed: bool = False


def validate_music_credentials(token: str, region: str, container_id: str) -> None:
    if (not re.fullmatch(r'[!-~]+', token)
            or region not in MUSIC_REGIONS
            or not re.fullmatch(r'[1-9][0-9]{0,19}', container_id)):
        raise MusicCheckError(tr('Укажи токен, регион и числовой ID контейнера ACRCloud.'))


def _check_stop(stop_event: Event) -> None:
    if stop_event.is_set():
        raise MusicCheckCancelled()


def _run_media(command: list[str], stop_event: Event, timeout: int) -> bytes:
    _check_stop(stop_event)
    deadline = monotonic() + timeout
    try:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, creationflags=_WIN_FLAGS) as process:
            try:
                while True:
                    _check_stop(stop_event)
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise MusicCheckError(tr(_MEDIA_ERROR))
                    try:
                        output, _ = process.communicate(timeout=min(.25, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            _check_stop(stop_event)
            if process.returncode != 0:
                raise MusicCheckError(tr(_MEDIA_ERROR))
            return output
    except OSError:
        raise MusicCheckError(tr(_MEDIA_ERROR)) from None


def _audio_stream(ffprobe: str, source: Path, stop_event: Event) -> int | None:
    output = _run_media(
        [ffprobe, '-v', 'error', '-protocol_whitelist', 'file', '-show_entries',
         'stream=index,codec_type:stream_disposition=default', '-of', 'json', str(source)],
        stop_event, 30,
    )
    try:
        streams = json.loads(output)['streams']
        if not any(stream['codec_type'] == 'video' for stream in streams):
            raise ValueError()
        audio = [stream for stream in streams if stream['codec_type'] == 'audio']
        if not audio:
            return None
        stream = next((stream for stream in audio if stream.get('disposition', {}).get('default') == 1), audio[0])
        index = stream['index']
        if type(index) is not int or index < 0:
            raise ValueError()
        return index
    except (KeyError, TypeError, ValueError, AttributeError):
        raise MusicCheckError(tr(_MEDIA_ERROR)) from None


def _milliseconds(value, scale: int = 1) -> int | None:
    try:
        number = float(value) * scale
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 1_000_000_000_000:
        return None
    return round(number)


def _display_text(value) -> str:
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _matches(row: dict) -> tuple[MusicMatch, ...]:
    results = row.get('results')
    entries = results.get('music') if isinstance(results, dict) else None
    if not isinstance(entries, list):
        raise MusicCheckError(tr(_INCOMPLETE))
    matches = []
    for entry in entries:
        track = entry.get('result') if isinstance(entry, dict) else None
        title = _display_text(track.get('title')) if isinstance(track, dict) else ''
        if not title:
            raise MusicCheckError(tr(_INCOMPLETE))
        artists = track.get('artists')
        names = []
        if isinstance(artists, list):
            names = [_display_text(artist.get('name')) for artist in artists if isinstance(artist, dict)]
        artist = ', '.join(name for name in names if name)
        offset = _milliseconds(entry.get('offset'), 1000)
        begin = _milliseconds(track.get('sample_begin_time_offset_ms'))
        end = _milliseconds(track.get('sample_end_time_offset_ms'))
        duration = _milliseconds(entry.get('played_duration'), 1000)
        start_ms = offset + (begin or 0) if offset is not None else None
        end_ms = None
        if offset is not None:
            if end is not None and end > (begin or 0):
                end_ms = offset + end
            elif duration:
                end_ms = start_ms + duration
        matches.append(MusicMatch(title, artist, start_ms, end_ms))
    return tuple(sorted(set(matches), key=lambda match: (match.start_ms is None, match.start_ms or 0,
                                                         match.title, match.artist, match.end_ms or 0)))


class MusicScanner:
    def __init__(self, token: str, region: str, container_id: str, ffmpeg: str, ffprobe: str):
        validate_music_credentials(token, region, container_id)
        self.region = region
        self.container_id = container_id
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self._headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
        self._files_url = f'https://api-{region}.acrcloud.com/api/fs-containers/{container_id}/files'
        self._verified = False

    def _request(self, method: str, url: str, *, timeout: tuple[int, int] = _TIMEOUT, **kwargs):
        try:
            with requests.request(method, url, headers=self._headers, timeout=timeout,
                                  allow_redirects=False, **kwargs) as response:
                if method == 'DELETE' and response.status_code in (204, 404):
                    return None
                if not 200 <= response.status_code < 300:
                    raise MusicCheckError(tr('ACRCloud: ошибка HTTP {0}.').format(response.status_code))
                try:
                    body = response.json()
                except ValueError:
                    raise MusicCheckError(tr(_INCOMPLETE)) from None
        except requests.RequestException:
            raise MusicCheckError(tr('ACRCloud: ошибка подключения.')) from None
        if not isinstance(body, dict) or 'data' not in body:
            raise MusicCheckError(tr(_INCOMPLETE))
        return body['data']

    def _verify_container(self) -> None:
        data = self._request('GET', f'{_CONTAINERS}/{self.container_id}')
        if not isinstance(data, dict) or str(data.get('id')) != self.container_id:
            raise MusicCheckError(tr(_INCOMPLETE))
        policy = data.get('policy')
        buckets = data.get('buckets')
        if (data.get('region', self.region) != self.region
                or not isinstance(policy, dict)
                or policy.get('type') != 'traverse'
                or str(policy.get('interval')) != '0'
                or str(data.get('engine')) != '1'
                or not isinstance(buckets, list)
                or not any(isinstance(bucket, dict) and bucket.get('name') == 'ACRCloud Music'
                           for bucket in buckets)):
            raise MusicCheckError(tr('ACRCloud: настрой контейнер для полного сканирования каталога ACRCloud Music.'))
        self._verified = True

    def _wait_for_result(self, url: str, remote_id: str, stop_event: Event) -> MusicResult:
        deadline = monotonic() + _RECOGNITION_TIMEOUT
        while monotonic() < deadline:
            _check_stop(stop_event)
            data = self._request('GET', url)
            _check_stop(stop_event)
            if isinstance(data, list) and len(data) == 1:
                data = data[0]
            if not isinstance(data, dict) or data.get('id') != remote_id:
                raise MusicCheckError(tr(_INCOMPLETE))
            state = str(data.get('state'))
            if state == '1':
                return MusicResult(matches=_matches(data))
            if state == '-1':
                return MusicResult()
            if state in ('-2', '-3'):
                raise MusicCheckError(tr('ACRCloud не смог распознать аудио.'))
            if state != '0':
                raise MusicCheckError(tr(_INCOMPLETE))
            if stop_event.wait(_POLL_SECONDS):
                raise MusicCheckCancelled()
        raise MusicCheckError(tr('ACRCloud не завершил проверку вовремя.'))

    def scan(self, path: Path, stop_event: Event) -> MusicResult:
        source = path.resolve()
        stream_index = _audio_stream(self.ffprobe, source, stop_event)
        if stream_index is None:
            return MusicResult(no_audio=True)
        if not self._verified:
            self._verify_container()
        with TemporaryDirectory(prefix='jetvideo-music-') as folder:
            audio = Path(folder) / 'audio.m4a'
            _run_media(
                [self.ffmpeg, '-v', 'error', '-xerror', '-nostdin', '-protocol_whitelist', 'file',
                 '-i', str(source), '-map', f'0:{stream_index}', '-map_metadata', '-1',
                 '-map_metadata:s:a', '-1', '-map_chapters', '-1',
                 '-af', 'aresample=async=1:first_pts=0', '-ac', '1', '-c:a', 'aac', '-b:a', '64k', str(audio)],
                stop_event, 180,
            )
            try:
                size = audio.stat().st_size
                if not size:
                    raise MusicCheckError(tr(_MEDIA_ERROR))
                if size >= _AUDIO_LIMIT:
                    raise MusicCheckError(tr('Размер аудио превышает лимит ACRCloud.'))
                _check_stop(stop_event)
                anonymous_name = f'{uuid4().hex}.m4a'
                with audio.open('rb') as payload:
                    data = self._request('POST', self._files_url, data={'data_type': 'audio'},
                                         files={'file': (anonymous_name, payload, 'audio/mp4')})
            except OSError:
                raise MusicCheckError(tr(_MEDIA_ERROR)) from None
        remote_id = data.get('id') if isinstance(data, dict) else None
        if not isinstance(remote_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', remote_id):
            raise MusicCheckError(tr(_INCOMPLETE))
        url = f'{self._files_url}/{remote_id}'
        cleanup_failed = False
        try:
            result = self._wait_for_result(url, remote_id, stop_event)
        finally:
            try:
                self._request('DELETE', url, timeout=(4, 6))
            except MusicCheckError:
                cleanup_failed = True
        _check_stop(stop_event)
        return replace(result, cleanup_failed=cleanup_failed)
