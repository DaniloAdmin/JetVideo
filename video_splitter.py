from __future__ import annotations

from i18n import tr
import csv
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from string import Formatter
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import uuid

from media_files import commit_new_file
from video_ad_inserter_core import (
    PAD_TO_EVEN, MediaInfo, parse_time, prepare_insertions, probe_media, run_capture, source_input_args,
)

MODES = ('none', 'overlay', 'freeze')
_RESERVED = re.compile(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)')


@dataclass(frozen=True)
class Segment:
    source: str
    start: float
    duration: float
    part: int
    filename: str


def output_name(template: str, part: int, source: str) -> str:
    template = template.strip()
    if not template:
        raise ValueError(tr('Укажи название, например «часть {part}».'))
    fields = list(Formatter().parse(template))
    has_part = False
    for _, field, spec, conversion in fields:
        if field is None:
            continue
        if field not in ('part', 'source') or conversion:
            raise ValueError(tr('В названии доступны только {part} и {source}.'))
        if spec and (field != 'part' or not re.fullmatch(r'0?[1-9][0-9]?d', spec)):
            raise ValueError(tr('Для номера допустим формат вроде {part:03d}.'))
        has_part |= field == 'part'
    if not has_part:
        if template.lower().endswith('.mp4'):
            template = template[:-4]
        template += ' {part}'
    name = template.format(part=part, source=Path(source).stem)
    if name.lower().endswith('.mp4'):
        name = name[:-4]
    if (not name or len(name) > 180 or name[-1] in ' .'
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or _RESERVED.match(name)):
        raise ValueError(tr('Недопустимое имя файла Windows: убери пути, служебные символы и зарезервированные имена.'))
    return name + '.mp4'


def validate_options(opt: dict) -> tuple[float, int]:
    seconds = parse_time(str(opt.get('clip_duration', '')))
    if seconds < .1:
        raise ValueError(tr('Длина части должна быть не меньше 0.1 секунды.'))
    part = int(str(opt.get('start_part', 1)))
    if part < 1:
        raise ValueError(tr('Начальный номер должен быть больше нуля.'))
    output_name(opt.get('name_template', tr('часть {part}')), part, tr('Видео.mp4'))
    if opt.get('split_banner_mode', 'none') not in MODES:
        raise ValueError(tr('Неизвестный режим баннеров.'))
    return seconds, part


def segments_for(source: str, info: MediaInfo, seconds: float, start_part: int,
                 template: str, keep_tail: bool = True) -> list[Segment]:
    if not math.isfinite(info.duration) or info.duration <= 0:
        raise ValueError(tr('Некорректная длительность исходного видео.'))
    frame = 1 / float(Fraction(info.fps))
    if seconds < frame:
        raise ValueError(tr('Длина части меньше одного кадра исходного видео.'))
    result = []
    count = math.ceil((info.duration - 1e-7) / seconds)
    for i in range(count):
        start = i * seconds
        duration = min(seconds, info.duration - start)
        if duration < frame / 2 or (not keep_tail and duration + 1e-7 < seconds):
            continue
        part = start_part + len(result)
        result.append(Segment(source, start, duration, part, output_name(template, part, source)))
    return result


def available_path(folder: Path, name: str) -> Path:
    target = folder / name
    counter = 2
    while target.exists():
        target = folder / f'{Path(name).stem}_{counter}.mp4'
        counter += 1
    return target


def _banners(opt: dict, cancel: threading.Event) -> list[dict]:
    mode = opt.get('split_banner_mode', 'none')
    if mode == 'none':
        return []
    entries = prepare_insertions(opt.get('insertions', []))
    metadata = {}
    for entry in entries:
        if cancel.is_set():
            raise RuntimeError(tr('Нарезка отменена.'))
        path = entry['banner']
        if path not in metadata:
            if Path(path).suffix.lower() in ('.png', '.jpg', '.jpeg'):
                cp = run_capture([opt['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', path])
                if cp.returncode:
                    raise ValueError(tr('Не удалось прочитать изображение баннера.'))
                stream = json.loads(cp.stdout)['streams'][0]
                metadata[path] = MediaInfo(3, int(stream['width']), int(stream['height']), '30/1', False)
            else:
                metadata[path] = probe_media(opt['ffprobe'], path)
        info = metadata[path]
        entry.update(kind=mode, duration=float(entry.get('duration') or info.duration),
                     audio=info.has_audio, codec=info.codec)
        background = entry.get('mode', 'Общие настройки')
        if background == 'Общие настройки':
            background = opt['banner_mode']
        if background == 'auto':
            background = 'Прозрачный WebM (alpha)' if Path(path).suffix.lower() == '.webm' else 'Без удаления фона'
        entry['mode'] = background
    return entries


def banners_for_segment(entries: list[dict], info: MediaInfo, mode: str) -> tuple[list[dict], list[dict]]:
    frame = 1 / float(Fraction(info.fps))
    selected, skipped = [], []
    for entry in entries:
        t = entry['time']
        fits = 0 <= t < info.duration if mode == 'overlay' else frame <= t < info.duration - frame
        (selected if fits else skipped).append(dict(entry))
    return selected, skipped


def _write_plain(owner, args, target, duration, index, total):
    args += ['-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', '-loglevel', 'error', '-f', 'mp4', target]
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as errors:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, text=True,
                              encoding='utf-8', errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as proc:
            owner._monitor_cancel(proc)
            for line in proc.stdout:
                if line.startswith('out_time_ms='):
                    try:
                        pct = max(0, min(99, int(int(line.split('=')[1]) / 1e6 / duration * 100)))
                        owner.events.put(('status', tr('Нарезка {0}/{1}: {2}%').format(index, total, pct)))
                        owner.events.put(('progress', (index - 1) * 100 + pct))
                    except ValueError:
                        pass
            code = proc.wait()
        errors.seek(0)
        detail = errors.read()[-2500:]
    if code or not Path(target).is_file() or Path(target).stat().st_size == 0:
        raise RuntimeError(detail or tr('FFmpeg завершился с кодом {0}.').format(code))


def _render_plain(owner, opt, job, target, index, total, copy_full, cancel):
    ffmpeg = [opt['ffmpeg'], '-hide_banner', '-nostdin', '-y']
    streams = ['-map', '0:v:0', '-map', '0:a:0?']
    if copy_full:
        try:
            _write_plain(owner, ffmpeg + ['-xerror', '-i', job.source] + streams + ['-c', 'copy'],
                         target, job.duration, index, total)
        except RuntimeError:
            if cancel.is_set():
                raise
            owner.events.put(('log', tr('Копирование потоков не удалось. Сохраняю с перекодированием.')))
        else:
            owner.events.put(('log', tr('Фрагмент сохранён без перекодирования.')))
            return

    args = ffmpeg + source_input_args(job.source, opt) + streams
    args += ['-t', f'{job.duration:.9f}', '-vf', f'setpts=PTS-STARTPTS,{PAD_TO_EVEN},format=yuv420p',
             '-af', 'asetpts=PTS-STARTPTS', '-c:v', 'libx264', '-preset', opt['x264_preset'],
             '-crf', str(opt['crf']), '-c:a', 'aac', '-b:a', '192k']
    _write_plain(owner, args, target, job.duration, index, total)


def _copy_at_keyframes(owner, opt, source, info, seconds, staging, index, total):
    args = [opt['ffmpeg'], '-hide_banner', '-nostdin', '-loglevel', 'error', '-xerror',
            '-i', str(Path(source).resolve()), '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy',
            '-progress', 'pipe:1', '-nostats', '-f', 'segment', '-reference_stream', 'v:0',
            '-segment_time', f'{seconds:.9f}', '-reset_timestamps', '1',
            '-segment_list', 'parts.csv', '-segment_list_type', 'csv', '-segment_format', 'mp4',
            '-segment_format_options', 'movflags=+faststart', '%09d.mp4']
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as errors:
        with subprocess.Popen(args, cwd=staging, stdout=subprocess.PIPE, stderr=errors, text=True,
                              encoding='utf-8', errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as proc:
            owner._monitor_cancel(proc)
            for line in proc.stdout:
                if line.startswith('out_time_us='):
                    try:
                        elapsed = int(line.partition('=')[2]) / 1e6
                    except ValueError:
                        continue
                    percent = max(0, min(99, int(elapsed / info.duration * 100)))
                    owner.events.put(('status', tr('Быстрая нарезка {0}/{1}: {2}%').format(index, total, percent)))
                    owner.events.put(('progress', (index - 1) * 100 + percent))
            code = proc.wait()
        errors.seek(0)
        detail = errors.read()[-2500:]
    return code, detail


def _closed_segments(staging: Path) -> list[tuple[Path, float]]:
    manifest = staging / 'parts.csv'
    if not manifest.is_file():
        return []
    segments = []
    with manifest.open(encoding='utf-8', newline='') as document:
        for row in csv.reader(document):
            if len(row) != 3 or not re.fullmatch(r'[0-9]{9,}\.mp4', row[0]):
                continue
            try:
                duration = float(row[2]) - float(row[1])
            except ValueError:
                continue
            if math.isfinite(duration) and duration > 0:
                segments.append((staging / row[0], duration))
    return segments


def _run_fast_split(owner, opt, seconds, number, output, cancel):
    successful = failed = 0
    template = opt.get('name_template', tr('часть {part}'))
    total = len(opt['videos'])
    owner.events.put(('progress_max', max(1, total) * 100))
    for index, source in enumerate(opt['videos'], start=1):
        if cancel.is_set():
            break
        owner.events.put(('status', tr('Читаю длительность: ') + Path(source).name))
        try:
            info = probe_media(opt['ffprobe'], source)
            if not math.isfinite(info.duration) or info.duration <= 0:
                raise ValueError(tr('Некорректная длительность исходного видео.'))
            frame = 1 / float(Fraction(info.fps))
            if seconds < frame:
                raise ValueError(tr('Длина части меньше одного кадра исходного видео.'))
            if cancel.is_set():
                break
            owner.events.put(('log', tr('{0}: нарезка по ключевым кадрам без перекодирования.').format(Path(source).name)))
            with tempfile.TemporaryDirectory(prefix='.jojo-segments-', dir=output) as directory:
                staging = Path(directory)
                code, detail = _copy_at_keyframes(owner, opt, source, info, seconds, staging, index, total)
                clips = _closed_segments(staging)
                if not clips and code == 0:
                    raise RuntimeError(tr('FFmpeg не создал ни одного фрагмента.'))
                if clips and code == 0 and not opt.get('keep_tail', True) and clips[-1][1] + frame / 2 < seconds:
                    clips.pop()
                for clip, _ in clips:
                    try:
                        target = available_path(output, output_name(template, number, source))
                        commit_new_file(clip, target)
                    except (OSError, ValueError) as exc:
                        failed += 1
                        owner.events.put(('log', tr('ОШИБКА записи части {0}: {1}').format(number, exc)))
                    else:
                        successful += 1
                        owner.events.put(('rendered', str(target)))
                        owner.events.put(('log', tr('Готово: ') + target.name))
                    number += 1
                if code and not cancel.is_set():
                    raise RuntimeError(detail or tr('FFmpeg завершился с кодом {0}.').format(code))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            if not cancel.is_set():
                failed += 1
                owner.events.put(('log', tr('ОШИБКА исходника {0}: {1}').format(Path(source).name, exc)))
        if cancel.is_set():
            break
        owner.events.put(('progress', index * 100))
    if cancel.is_set():
        owner.events.put(('cancelled', successful, failed))
    elif successful or failed:
        owner.events.put(('done', successful, failed, str(output)))
    else:
        raise ValueError(tr('Нет фрагментов для нарезки. Проверь исходники, длину части и сохранение короткого остатка.'))


def run_split(owner, opt: dict):
    successful = failed = 0
    cancel = getattr(owner, 'cancel_event', threading.Event())
    try:
        seconds, number = validate_options(opt)
        name_template = opt.get('name_template', tr('часть {part}'))
        output = Path(opt['output'])
        output.mkdir(parents=True, exist_ok=True)
        mode = opt.get('split_banner_mode', 'none')
        if opt.get('split_fast') and mode == 'none':
            _run_fast_split(owner, opt, seconds, number, output, cancel)
            return
        entries = _banners(opt, cancel)
        jobs = []
        for source in opt['videos']:
            if cancel.is_set():
                owner.events.put(('cancelled', successful, failed)); return
            owner.events.put(('status', tr('Читаю длительность: ') + Path(source).name))
            try:
                info = probe_media(opt['ffprobe'], source)
                planned = segments_for(source, info, seconds, number,
                                       name_template, opt.get('keep_tail', True))
                jobs.extend((job, info) for job in planned)
                number += len(planned)
                owner.events.put(('log', tr('{0}: частей {1}, длина исходника {2:.3f} с.').format(Path(source).name, len(planned), info.duration)))
            except Exception as exc:
                failed += 1
                owner.events.put(('log', tr('ОШИБКА исходника {0}: {1}').format(Path(source).name, exc)))
        if not jobs:
            raise ValueError(tr('Нет фрагментов для нарезки. Проверь исходники, длину части и сохранение короткого остатка.'))
        owner.events.put(('progress_max', len(jobs) * 100))
        for index, (job, source_info) in enumerate(jobs, start=1):
            if cancel.is_set():
                owner.events.put(('cancelled', successful, failed)); return
            target = available_path(output, job.filename)
            temp = output / f'.jojo-cut-{uuid.uuid4().hex}.mp4.tmp'
            try:
                info = replace(source_info, duration=job.duration)
                options = {**opt, 'x264_preset': 'veryfast',
                           'source_segment': {'start': job.start, 'duration': job.duration}}
                selected, skipped = banners_for_segment(entries, info, mode)
                for entry in skipped:
                    owner.events.put(('log', tr('{0}: баннер на {1:g} с пропущен — вне фрагмента длиной {2:g} с.').format(target.name, entry['time'], job.duration)))
                owner.events.put(('log', tr('[{0}/{1}] {2}: {3:g}–{4:g} с исходника; баннеров {5}.').format(index, len(jobs), target.name, job.start, job.start + job.duration, len(selected))))
                if not selected:
                    copy_full = source_info.can_remux_mp4 and job.start == 0 and job.duration == source_info.duration
                    _render_plain(owner, options, job, str(temp), index, len(jobs), copy_full, cancel)
                elif mode == 'overlay':
                    from overlay_render import render_overlay
                    render_overlay(owner, options, job.source, str(temp), info, selected, index, len(jobs))
                else:
                    owner.make_one(options, job.source, str(temp), info, selected, index, len(jobs))
                if cancel.is_set():
                    owner.events.put(('cancelled', successful, failed)); return
                commit_new_file(temp, target)
                successful += 1
                owner.events.put(('rendered', str(target)))
                owner.events.put(('log', tr('Готово: ') + target.name))
            except Exception as exc:
                if cancel.is_set():
                    owner.events.put(('cancelled', successful, failed)); return
                failed += 1
                owner.events.put(('log', tr('ОШИБКА {0}: {1}').format(target.name, exc)))
            finally:
                temp.unlink(missing_ok=True)
            owner.events.put(('progress', index * 100))
        owner.events.put(('done', successful, failed, str(output)))
    except Exception as exc:
        owner.events.put(('cancelled', successful, failed) if cancel.is_set() else ('fatal', str(exc)))
