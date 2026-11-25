from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
import html
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

from i18n import tr
from media_files import commit_new_file
from video_ad_inserter_core import PAD_TO_EVEN


FONTS = {'Noto Sans': 'NotoSans', 'Inter': 'Inter', 'Montserrat': 'Montserrat',
         'Roboto Condensed': 'RobotoCondensed'}
VAD_MODEL_NAME = 'ggml-silero-v5.1.2.bin'
_STAMP = r'\d+:\d{2}:\d{2}[,.]\d{3}'
_WIN_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
_SOUND_MARKER = re.compile(r'[♪♫♬♩][^♪♫♬♩]*[♪♫♬♩]|[\[(]([^\])\n]+)[\])]')
_SOUND_LABELS = {
    'музыка', 'фоновая музыка', 'музика', 'фонова музика', 'music', 'background music',
    'смех', 'сміх', 'laughter', 'laughs',
    'вздох', 'вздыхает', 'зітхання', 'sigh', 'sighs',
    'дыхание', 'дихання', 'breathing',
    'шум', 'шум на фоне', 'noise', 'background noise',
    'аплодисменты', 'оплески', 'applause',
    'тишина', 'тиша', 'silence',
}
_SOUND_WORD = re.compile(r'([аоеэуиім])(?:[-–—]?\1)*|[аоуэе]+х+|х+м+|(?:х[аеи][-–—]?)+')


@dataclass(frozen=True)
class Cue:
    start: int
    end: int
    text: str
    word_starts: tuple[int, ...] = ()
    position: tuple[float, float] | None = None


@dataclass(frozen=True)
class SubtitleStyle:
    font: str = 'Noto Sans'
    size: float = 5
    width: float = 84
    x: float = 50
    y: float = 72
    color: str = '#ffffff'
    accent: str = '#ffe15c'
    highlight: bool = False
    background: bool = False
    outline: int = 3


def _stamp_ms(value):
    hours, minutes, seconds, milliseconds = map(int, re.split(r'[:,.]', value))
    if minutes >= 60 or seconds >= 60:
        raise ValueError(value)
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + milliseconds


def read_srt(path: Path) -> list[Cue]:
    data = path.read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        document = data.decode('utf-16')
    else:
        try:
            document = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            document = data.decode('cp1251')
    cues = []
    for block in re.split(r'\n(?:[ \t]*\n)+', document.replace('\r\n', '\n').replace('\r', '\n').strip()):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines.pop(0)
        match = re.match(rf'^\s*({_STAMP})\s*-->\s*({_STAMP})(?:\s|$)', lines[0]) if lines else None
        if not match:
            raise ValueError(tr('В файле SRT неверный формат таймкодов.'))
        try:
            start, end = map(_stamp_ms, match.groups())
        except ValueError as exc:
            raise ValueError(tr('В файле SRT неверный формат таймкодов.')) from exc
        text = html.unescape(re.sub(r'</?(?:b|i|u|font)(?:\s[^>]*)?>', '', '\n'.join(lines[1:]), flags=re.I)).strip()
        if end <= start or not text:
            raise ValueError(tr('В SRT есть пустая фраза или неправильная длительность.'))
        cues.append(Cue(start, end, text))
    return sorted(cues, key=lambda cue: cue.start)


def cue_at(cues, starts, time_ms):
    index = bisect_right(starts, time_ms) - 1
    return index if index >= 0 and time_ms < cues[index].end else None


def word_starts(cue: Cue):
    if cue.word_starts:
        return cue.word_starts
    count = len(cue.text.split())
    return tuple(cue.start + (cue.end - cue.start) * index // count for index in range(count))


class CaptionPainter:
    def __init__(self, style, frame_size):
        self.style = style
        self.width, self.height = frame_size
        self.limit = max(24, round(self.width * style.width / 100))
        self.stroke = max(0, round(style.outline * self.height / 1080))
        self.scale = max(9, round(self.height * style.size / 100))
        self.font = self._font(self.scale)

    def _font(self, size):
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        family = FONTS[self.style.font]
        font = ImageFont.truetype(str(root / 'third_party' / 'fonts' / family / (family + '.ttf')), size)
        font.set_variation_by_name('Bold')
        return font

    def layout(self, cue):
        words = cue.text.split()
        font = self.font
        pad = max(self.stroke + 2, self.scale // 4)
        available = max(1, self.limit - pad * 2)
        line_space = max(2, self.scale // 7)
        while True:
            rows, row = [], []
            for index, word in enumerate(words):
                if row and font.getlength(' '.join(words[item] for item in (*row, index))) > available:
                    rows.append(row)
                    row = []
                row.append(index)
            if row:
                rows.append(row)
            widths = [font.getlength(' '.join(words[index] for index in row)) for row in rows]
            ascent, descent = font.getmetrics()
            height = (ascent + descent + line_space) * len(rows) - line_space + 2 * pad
            if (max(widths) <= available and height <= self.height) or font.size <= 9:
                break
            font = self._font(max(9, font.size * 9 // 10))
        width = min(self.width, round(max(widths)) + 2 * pad)
        height = min(self.height, height)
        px, py = cue.position or (self.style.x, self.style.y)
        left = max(0, min(self.width - width, round(self.width * px / 100 - width / 2)))
        top = max(0, min(self.height - height, round(self.height * py / 100 - height / 2)))
        return (left, top, left + width, top + height), (words, rows, widths, font, pad, ascent, descent, line_space)

    def paint(self, layout, active):
        (left, top, right, bottom), (words, rows, widths, font, pad, ascent, descent, line_space) = layout
        picture = Image.new('RGBA', (right - left, bottom - top))
        draw = ImageDraw.Draw(picture)
        if self.style.background:
            draw.rounded_rectangle((0, 0, picture.width - 1, picture.height - 1), radius=pad, fill=(5, 7, 15, 185))
        baseline = pad + ascent
        for row, width in zip(rows, widths):
            text = ' '.join(words[index] for index in row)
            x = (picture.width - width) / 2
            draw.text((x, baseline), text, font=font, anchor='ls', fill=self.style.color,
                      stroke_width=self.stroke, stroke_fill='#000000')
            if active in row:
                previous = ' '.join(words[index] for index in row[:row.index(active)])
                offset = font.getlength(previous + ' ') if previous else 0
                draw.text((x + offset, baseline), words[active], font=font,
                          anchor='ls', fill=self.style.accent, stroke_width=self.stroke, stroke_fill='#000000')
            baseline += ascent + descent + line_space
        return picture


def frame_at(ffmpeg, source, seconds):
    result = subprocess.run([ffmpeg, '-v', 'error', '-ss', f'{seconds:.3f}', '-i', str(source),
                             '-map', '0:v:0', '-frames:v', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1'],
                            capture_output=True, timeout=20, creationflags=_WIN_FLAGS)
    if result.returncode or not result.stdout:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip() or tr('Не удалось прочитать кадр.'))
    with Image.open(BytesIO(result.stdout)) as picture:
        return picture.convert('RGB')


def _wait_process(args, cwd, cancel, progress=None):
    log_path = cwd / 'process.log'
    with log_path.open('wb') as log:
        with subprocess.Popen(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                              creationflags=_WIN_FLAGS) as process:
            while process.poll() is None:
                if cancel.wait(.1):
                    process.kill()
                    return False
                if progress:
                    progress(log_path)
            if process.returncode:
                with log_path.open('rb') as reader:
                    reader.seek(max(0, log_path.stat().st_size - 2200))
                    detail = reader.read().decode('utf-8', 'replace').strip()
                raise RuntimeError(detail or str(process.returncode))
    return not cancel.is_set()


def _prepare_overlay(directory, cues, style, frame_size, duration, cancel, notify):
    painter = CaptionPainter(style, frame_size)
    spans = []
    for index, cue in enumerate(cues):
        end = min(cue.end, cues[index + 1].start if index + 1 < len(cues) else duration, duration)
        if end > cue.start and cue.text.strip():
            spans.append((cue, end, painter.layout(cue)))
    if not spans:
        raise ValueError(tr('На хронометраже видео нет ни одной фразы.'))
    bounds = [layout[0] for _, _, layout in spans]
    origin = min(box[0] for box in bounds) // 2 * 2, min(box[1] for box in bounds) // 2 * 2
    size = (max(box[2] for box in bounds) - origin[0], max(box[3] for box in bounds) - origin[1])
    Image.new('RGBA', size).save(directory / 'blank.png')

    cursor = 0
    with (directory / 'overlay.ffconcat').open('w', encoding='ascii', newline='\n') as manifest:
        manifest.write('ffconcat version 1.0\n')

        def append(filename, length):
            manifest.write(f'file {filename}\noption framerate 1000\nduration {length / 1000:.3f}\n')

        for index, (cue, end, layout) in enumerate(spans):
            if cancel.is_set():
                return None
            notify(tr('Подготовка субтитров {0}/{1}').format(index + 1, len(spans)))
            if cue.start > cursor:
                append('blank.png', cue.start - cursor)
            starts = word_starts(cue) if style.highlight else ()
            marks = sorted({cue.start, end, *(t for t in starts if cue.start < t < end)})
            for number, (start, stop) in enumerate(zip(marks, marks[1:])):
                if cancel.is_set():
                    return None
                active = bisect_right(starts, start) - 1 if starts else None
                caption = painter.paint(layout, active)
                overlay = Image.new('RGBA', size)
                overlay.paste(caption, (layout[0][0] - origin[0], layout[0][1] - origin[1]))
                name = f'f{index}-{number}.png'
                overlay.save(directory / name, compress_level=1)
                append(name, stop - start)
            cursor = end
        append('blank.png', max(1, duration - cursor))
    return origin


def export_subtitled(ffmpeg, source, target, info, frame_size, cues, style, keyframe_step, cancel, notify):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise ValueError(tr('Выбери новое имя для видео с субтитрами.'))
    duration = round(info.duration * 1000)
    with tempfile.TemporaryDirectory(prefix='.jetvideo-subtitles-', dir=target.parent) as folder:
        directory = Path(folder)
        origin = _prepare_overlay(directory, cues, style, frame_size, duration, cancel, notify)
        if origin is None:
            return None
        x, y = origin
        filters = (f'[0:v:0]setpts=PTS-STARTPTS,{PAD_TO_EVEN}[video];'
                   f'[video][1:v:0]overlay={x}:{y}:eof_action=pass:repeatlast=0[v]')
        args = [ffmpeg, '-v', 'error', '-nostdin', '-i', str(source), '-f', 'concat', '-safe', '0',
                '-i', 'overlay.ffconcat', '-filter_complex_threads', '1', '-filter_complex', filters,
                '-map', '[v]', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-af', 'asetpts=PTS-STARTPTS']
        if keyframe_step:
            args += ['-force_key_frames', f'expr:gte(t,n_forced*{keyframe_step:g})']
        args += ['-t', f'{info.duration:.6f}', '-progress', 'pipe:1', '-nostats', '-movflags', '+faststart', 'video.mp4']

        def progress(log_path):
            with log_path.open('rb') as reader:
                reader.seek(max(0, log_path.stat().st_size - 900))
                times = re.findall(rb'out_time_us=(\d+)', reader.read(900))
            if times:
                notify(tr('Экспорт субтитров: {0}%').format(min(99, int(times[-1]) // (duration * 10))))

        notify(tr('Экспорт субтитров: {0}%').format(0))
        if not _wait_process(args, directory, cancel, progress):
            return None
        staged = directory / 'video.mp4'
        if not staged.is_file() or staged.stat().st_size == 0:
            raise RuntimeError(tr('FFmpeg не создал видео с субтитрами.'))
        commit_new_file(staged, target)
    return target


def _whisper_cues(document):
    cues = []
    for segment in document.get('transcription', ()):
        text = segment['text'].strip()
        start, end = segment['offsets']['from'], segment['offsets']['to']
        if not text or end <= start:
            continue
        fragments, positions, times = [], [], []
        length = 0
        for token in segment.get('tokens', ()):
            fragment = token['text']
            if fragment.startswith('[_') and fragment.endswith(']'):
                continue
            if not fragment:
                continue
            fragments.append(fragment)
            positions.append(length)
            length += len(fragment)
            dtw = token.get('t_dtw', -1)
            times.append(round(dtw * 10) if dtw >= 0 else token.get('offsets', {}).get('from'))
        words = re.finditer(r'\S+', ''.join(fragments))
        timed = tuple(times[bisect_right(positions, match.start()) - 1] for match in words)
        if (''.join(fragments).split() != text.split() or any(t is None or not start <= t < end for t in timed)
                or any(b <= a for a, b in zip(timed, timed[1:]))):
            timed = ()
        cues.append(Cue(start, end, text, timed))
    return cues


def _speech_cues(cues):
    def strip_marker(match):
        label = match[1]
        return ' ' if label is None or ' '.join(label.casefold().split()) in _SOUND_LABELS else match[0]

    result = []
    for cue in cues:
        text = _SOUND_MARKER.sub(strip_marker, cue.text).strip(' \t\n♪♫♬♩')
        words = re.findall(r'[^\W_]+(?:[-–—][^\W_]+)*', text.casefold())
        if any(not _SOUND_WORD.fullmatch(word) for word in words):
            result.append(cue if text == cue.text else replace(cue, text=' '.join(text.split()), word_starts=()))
    return result


def transcribe(ffmpeg, source, whisper, model, language, cancel, notify, *, vad_model=None):
    if language not in ('ru', 'uk'):
        raise ValueError(language)
    with tempfile.TemporaryDirectory(prefix='jetvideo-whisper-') as folder:
        directory = Path(folder)
        notify(tr('Подготовка звука…'))
        args = [ffmpeg, '-v', 'error', '-nostdin', '-i', str(Path(source).resolve()), '-map', '0:a:0',
                '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', 'speech.wav']
        if not _wait_process(args, directory, cancel):
            return None
        notify(tr('Распознавание речи на компьютере…'))
        args = [str(whisper), '-m', str(model), '-f', 'speech.wav', '-l', language,
                '-of', 'captions', '-ml', '54', '-sow', '-ng']
        if vad_model is None:
            args += ['-ojf', '-dtw', 'base']
        else:
            args += ['-oj', '--vad', '--vad-model', str(vad_model), '--vad-threshold', '0.55',
                     '--vad-min-speech-duration-ms', '180', '--vad-speech-pad-ms', '100', '--suppress-nst']
        if not _wait_process(args, directory, cancel):
            return None
        cues = _whisper_cues(json.loads((directory / 'captions.json').read_text(encoding='utf-8-sig')))
        if vad_model is not None:
            cues = _speech_cues(cues)
        if not cues:
            raise ValueError(tr('Речь для субтитров не найдена.'))
        return cues
