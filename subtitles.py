from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import html
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import wave

from PIL import Image, ImageDraw, ImageFont

from caption_layout import keep_with_next, wrap_words
from i18n import tr
from media_files import commit_new_file
from video_ad_inserter_core import PAD_TO_EVEN


FONTS = {'Noto Sans': 'NotoSans', 'Inter': 'Inter', 'Montserrat': 'Montserrat',
         'Roboto Condensed': 'RobotoCondensed'}
VAD_MODEL_NAME = 'ggml-silero-v5.1.2.bin'
SMALL_MODEL_NAME = 'ggml-small.bin'
LARGE_MODEL_NAME = 'ggml-large-v3.bin'
_CAPTION_HOLD_MS = 800
_TRANSCRIPTION_CHUNK_MS = 480_000
_TRANSCRIPTION_OVERLAP_MS = 4_000
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
_VAD_SPAN = re.compile(
    rb'vad_segment_info: orig_start: (\d+\.\d+), orig_end: (\d+\.\d+), '
    rb'vad_start: (\d+\.\d+), vad_end: (\d+\.\d+)'
)


@dataclass(frozen=True)
class Cue:
    start: int
    end: int
    text: str
    word_starts: tuple[int, ...] = ()
    position: tuple[float, float] | None = None


@dataclass(frozen=True)
class _SpeechSpan:
    start: int
    end: int
    compressed_start: int
    compressed_end: int


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


def hold_captions(cues, duration_ms=None):
    result = []
    for index, cue in enumerate(cues):
        end = cue.end + _CAPTION_HOLD_MS
        if duration_ms is not None:
            end = min(end, duration_ms)
        if index + 1 < len(cues):
            end = min(end, cues[index + 1].start)
        if end > cue.end:
            cue = replace(cue, end=end, word_starts=word_starts(cue))
        result.append(cue)
    return result


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
        font = self.font
        preferred_rows = max(2, sum(bool(line.strip()) for line in cue.text.splitlines()))
        preferred_min_size = max(9, round(self.scale * .72))
        while True:
            pad = max(self.stroke + 2, font.size // 4)
            line_space = max(2, font.size // 7)
            available = max(1, self.limit - pad * 2)
            words, rows = wrap_words(cue.text, available, font.getlength)
            widths = [font.getlength(' '.join(words[index] for index in row)) for row in rows]
            ascent, descent = font.getmetrics()
            height = (ascent + descent + line_space) * max(1, len(rows)) - line_space + 2 * pad
            fits = max(widths, default=0) <= available and height <= self.height
            if fits and (len(rows) <= preferred_rows or font.size <= preferred_min_size):
                break
            if font.size == 1:
                break
            next_size = max(1, font.size * 9 // 10)
            font = self._font(max(preferred_min_size, next_size) if fits else next_size)
        width = min(self.width, round(max(widths, default=0)) + 2 * pad)
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


def _sentence_end(word):
    word = word.rstrip('»”"’)]')
    if word.endswith(('?', '!', '…', '...')):
        return True
    if not word.endswith('.'):
        return False
    return word in ('Я.', 'я.') or not re.fullmatch(r'(?:[^\W\d_]\.)+', word)


def _phrase_cues(words):
    cursor = 0
    while cursor < len(words):
        first = cursor
        length = 0
        soft_boundary = first
        sentence_end = False
        while cursor < len(words):
            word, _start, end = words[cursor]
            if cursor > first and (length + len(word) + 1 > 72 or end - words[first][1] > 6000):
                break
            length += len(word) + 1
            cursor += 1
            sentence_end = _sentence_end(word)
            if sentence_end:
                break
            if length >= 30 and word.endswith((',', ';', ':')):
                soft_boundary = cursor

        if cursor < len(words) and not sentence_end:
            if soft_boundary > first:
                cursor = soft_boundary
            while cursor > first + 1 and keep_with_next(words[cursor - 1][0]):
                cursor -= 1

        block = words[first:cursor]
        start, end = block[0][1], block[-1][2]
        timings = tuple(word[1] for word in block)
        if any(a >= b for a, b in zip(timings, timings[1:])) or timings[-1] >= end:
            timings = ()
        yield Cue(start, max(start + 1, end), ' '.join(word[0] for word in block), timings)


def regroup_captions(cues):
    result = []
    words = []
    previous_end = None
    for cue in cues:
        if previous_end is not None and not 0 <= cue.start - previous_end <= 350:
            result.extend(_phrase_cues(words))
            words.clear()
        starts = (cue.start, *word_starts(cue)[1:])
        ends = (*starts[1:], cue.end)
        words.extend(zip(cue.text.split(), starts, ends))
        previous_end = cue.end
    result.extend(_phrase_cues(words))
    return result


def _extract_transcription_audio(ffmpeg, source, directory, cancel, start_ms, end_ms):
    args = [ffmpeg, '-v', 'error', '-nostdin', '-y']
    if start_ms:
        args += ['-ss', f'{start_ms / 1000:.3f}']
    args += ['-i', str(source), '-map', '0:a:0', '-vn', '-af', 'aresample=async=1:first_pts=0',
             '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le']
    if end_ms is not None:
        args += ['-t', f'{(end_ms - start_ms) / 1000:.3f}']
    if not _wait_process(args + ['speech.wav'], directory, cancel):
        return None
    with wave.open(str(directory / 'speech.wav'), 'rb') as audio:
        return round(audio.getnframes() * 1000 / audio.getframerate())


def _speech_spans(log_path):
    spans = []
    with log_path.open('rb') as log:
        for line in log:
            match = _VAD_SPAN.search(line)
            if match:
                start, end, compressed_start, compressed_end = (round(float(value) * 1000) for value in match.groups())
                if start < end and compressed_start < compressed_end:
                    spans.append(_SpeechSpan(start, end, compressed_start, compressed_end))
    return spans


def _token_timed_words(segment, expected_words):
    positions, tokens = [], []
    length = 0
    for token in segment.get('tokens', ()):
        text = token['text']
        if text and not (text.startswith('[_') and text.endswith(']')):
            positions.append(length)
            tokens.append(token)
            length += len(text)
    joined = ''.join(token['text'] for token in tokens)
    if joined.split() != expected_words:
        return []
    result = []
    for word in re.finditer(r'\S+', joined):
        first = bisect_right(positions, word.start()) - 1
        last = bisect_left(positions, word.end())
        timed_tokens = [token for token in tokens[first:last] if 'offsets' in token]
        lexical = [token for token in timed_tokens if any(char.isalnum() for char in token['text'])]
        timed = [token['offsets'] for token in lexical or timed_tokens]
        if not timed or not 0 <= timed[0]['from'] <= timed[-1]['to']:
            return []
        result.append((word.group(), timed[0]['from'], timed[-1]['to']))
    return result


def _word_in_speech_span(word, begin, end, spans, compressed_starts):
    midpoint = (begin + end) // 2
    index = max(0, bisect_right(compressed_starts, midpoint) - 1)
    span = spans[index]
    if index + 1 < len(spans):
        following = spans[index + 1]
        if midpoint - span.compressed_end > following.compressed_start - midpoint:
            span = following
    width = span.end - span.start
    compressed_width = span.compressed_end - span.compressed_start
    start = max(span.start, min(span.end - 1, span.start + (begin - span.compressed_start) * width // compressed_width))
    stop = min(span.end, max(start + 120, span.start + (end - span.compressed_start) * width // compressed_width))
    return Cue(min(start, max(span.start, stop - 120)), stop, word)


def _approximate_vad_words(cue, spans):
    overlapping = [(max(cue.start, span.start), min(cue.end, span.end)) for span in spans
                   if span.start < cue.end and span.end > cue.start]
    if not overlapping:
        nearest = min(spans, key=lambda span: max(span.start - cue.end, cue.start - span.end))
        overlapping = [(nearest.start, nearest.end)]
    active, elapsed = [], 0
    for start, end in overlapping:
        active.append(_SpeechSpan(start, end, elapsed, elapsed + end - start))
        elapsed += end - start
    words = cue.text.split()
    starts = [span.compressed_start for span in active]
    return [_word_in_speech_span(word, index * elapsed // len(words), (index + 1) * elapsed // len(words), active, starts)
            for index, word in enumerate(words)]


def _vad_cues(document, log_path):
    spans = _speech_spans(log_path)
    if not spans:
        return _speech_cues(_whisper_cues(document))
    starts = [span.compressed_start for span in spans]
    cues = []
    for segment in document.get('transcription', ()):
        start, end = segment['offsets']['from'], segment['offsets']['to']
        if end <= start:
            continue
        clean = _speech_cues([Cue(start, end, segment['text'].strip())])
        if not clean:
            continue
        words = _token_timed_words(segment, clean[0].text.split())
        if words:
            cues.extend(_word_in_speech_span(word, begin, end, spans, starts) for word, begin, end in words)
        else:
            cues.extend(_approximate_vad_words(clean[0], spans))
    return cues


def _recognize_audio(directory, whisper, model, language, cancel, vad_model, progress):
    result = directory / 'captions.json'
    result.unlink(missing_ok=True)
    model_name = Path(model).name
    args = [str(whisper), '-m', str(model), '-f', 'speech.wav', '-l', language,
            '-of', 'captions', '-ojf', '-ml', '54', '-sow', '-ng', '-pp']
    if vad_model is None:
        preset = {SMALL_MODEL_NAME: 'small', LARGE_MODEL_NAME: 'large.v3'}.get(model_name, 'base')
        args += ['-dtw', preset]
    else:
        args += ['--vad', '--vad-model', str(vad_model), '--suppress-nst']
        if model_name == LARGE_MODEL_NAME:
            args += ['--vad-threshold', '0.42', '--vad-min-speech-duration-ms', '125',
                     '--vad-min-silence-duration-ms', '180', '--vad-speech-pad-ms', '160']
        else:
            args += ['--vad-threshold', '0.55', '--vad-min-speech-duration-ms', '180', '--vad-speech-pad-ms', '100']
    if not _wait_process(args, directory, cancel, progress):
        return None
    document = json.loads(result.read_text(encoding='utf-8-sig'))
    cues = _whisper_cues(document) if vad_model is None else _vad_cues(document, directory / 'process.log')
    detected = document.get('result', {}).get('language') if language == 'auto' else language
    return cues, detected


def _chunk_words(cues, offset, length):
    for cue in cues:
        start, end = max(0, cue.start), min(length, cue.end)
        if not cue.text.strip() or start >= end:
            continue
        timings = cue.word_starts if all(start <= time < end for time in cue.word_starts) else ()
        cue = replace(cue, start=start, end=end, word_starts=timings)
        starts = (start, *word_starts(cue)[1:])
        ends = (*starts[1:], end)
        yield from ((word, offset + begin, offset + stop)
                    for word, begin, stop in zip(cue.text.split(), starts, ends))


def _word_midpoint(word):
    return (word[1] + word[2]) // 2


def _overlap_anchor(left, right, boundary):
    start = bisect_left(left, boundary - _TRANSCRIPTION_OVERLAP_MS, key=_word_midpoint)
    end = bisect_right(right, boundary + _TRANSCRIPTION_OVERLAP_MS, key=_word_midpoint)
    left_text = [re.sub(r'[^\w]', '', word[0].casefold()).replace('ё', 'е') for word in left[start:]]
    right_text = [re.sub(r'[^\w]', '', word[0].casefold()).replace('ё', 'е') for word in right[:end]]
    candidates = []
    for block in SequenceMatcher(None, left_text, right_text, autojunk=False).get_matching_blocks():
        for index in range(block.size):
            left_index, right_index = block.a + index + start, block.b + index
            left_time = _word_midpoint(left[left_index])
            right_time = _word_midpoint(right[right_index])
            word = right_text[right_index]
            if not word or abs(left_time - right_time) > 1_250:
                continue
            if block.size == 1 and (len(word) < 4 or left_text.count(word) != 1 or right_text.count(word) != 1):
                continue
            proximity = abs((left_time + right_time) // 2 - boundary)
            candidates.append((proximity, -block.size, abs(left_time - right_time), left_index, right_index))
    if candidates:
        return min(candidates)[-2:]
    return None


def _stitch_transcription(left, right, boundary):
    if left and right and left[-1][2] > right[0][1]:
        anchor = _overlap_anchor(left, right, boundary)
        if anchor is None:
            left_limit = max(boundary, right[0][1])
            right_limit = min(boundary, left[-1][2])
            anchor = (bisect_left(left, left_limit, key=_word_midpoint),
                      bisect_left(right, right_limit, key=_word_midpoint))
        left_index, right_index = anchor
        del left[left_index:]
        right = right[right_index:]
    left.extend(right)


def _joined_phrases(words):
    run = []
    previous = None
    for text, start, end in words:
        if previous is not None:
            old_text, old_start, old_end = previous
            start = max(old_start + 1, start)
            run.append((old_text, old_start, min(old_end, start)))
            if start - old_end > 350:
                yield from _phrase_cues(run)
                run.clear()
        previous = (text, start, max(start + 1, end))
    if previous is not None:
        run.append(previous)
        yield from _phrase_cues(run)


def _transcription_progress(notify, index, count, core_start, core_length, total):
    last = -1

    def progress(log_path):
        nonlocal last
        with log_path.open('rb') as reader:
            reader.seek(max(0, log_path.stat().st_size - 2048))
            matches = re.findall(rb'progress\s*=\s*(\d+)\s*%', reader.read(2048))
        if matches:
            local_percent = min(100, int(matches[-1]))
            percent = min(99, (core_start * 100 + core_length * local_percent) // total) if total else min(99, local_percent)
            if percent > last:
                last = percent
                notify(tr('Распознавание речи: {0}% ({1}/{2})').format(percent, index, count))

    return progress


def transcribe(ffmpeg, source, whisper, model, language, cancel, notify, *, vad_model=None, duration=None, on_language=None):
    if language not in ('auto', 'ru', 'uk'):
        raise ValueError(language)
    source = Path(source).resolve()
    total = None if duration is None else max(1, round(duration * 1000))
    starts = range(0, total or 1, _TRANSCRIPTION_CHUNK_MS)
    words = []
    with tempfile.TemporaryDirectory(prefix='jetvideo-whisper-') as folder:
        directory = Path(folder)
        for index, core_start in enumerate(starts, start=1):
            if cancel.is_set():
                return None
            core_end = min(total, core_start + _TRANSCRIPTION_CHUNK_MS) if total else None
            start = max(0, core_start - _TRANSCRIPTION_OVERLAP_MS)
            end = min(total, core_end + _TRANSCRIPTION_OVERLAP_MS) if total else None
            notify(tr('Подготовка звука ({0}/{1})…').format(index, len(starts)))
            length = _extract_transcription_audio(ffmpeg, source, directory, cancel, start, end)
            if length is None:
                return None
            if length:
                notify(tr('Распознавание речи ({0}/{1})…').format(index, len(starts)))
                progress = _transcription_progress(notify, index, len(starts), core_start, (core_end or 0) - core_start, total)
                result = _recognize_audio(directory, whisper, model, language, cancel, vad_model, progress)
                if result is None or cancel.is_set():
                    return None
                cues, detected = result
                chunk = list(_chunk_words(cues, start, length))
                if chunk and on_language is not None and detected in ('ru', 'uk'):
                    on_language(detected)
                    on_language = None
                _stitch_transcription(words, chunk, core_start)
            percent = core_end * 100 // total if total else 100
            notify(tr('Распознавание речи: {0}% ({1}/{2})').format(percent, index, len(starts)))
    if cancel.is_set():
        return None
    if not words:
        raise ValueError(tr('Речь для субтитров не найдена.'))
    return hold_captions(list(_joined_phrases(words)), total if total is not None else length)
