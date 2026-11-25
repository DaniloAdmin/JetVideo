from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import ctypes
import os
import queue
import subprocess
import tempfile
import threading
import time

from PIL import Image

from i18n import tr


_RATE = 48000
_CHANNELS = 2
_BYTES_PER_SAMPLE = 2 * _CHANNELS
_CHUNK_BYTES = _RATE * _BYTES_PER_SAMPLE // 20
_DONE = 1
_TIME_MS, _TIME_SAMPLES, _TIME_BYTES = 1, 2, 4
_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class _WaveFormat(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('format', ctypes.c_uint16), ('channels', ctypes.c_uint16), ('rate', ctypes.c_uint32),
        ('byte_rate', ctypes.c_uint32), ('alignment', ctypes.c_uint16), ('bits', ctypes.c_uint16),
        ('extra', ctypes.c_uint16),
    ]


class _WaveHeader(ctypes.Structure):
    _fields_ = [
        ('data', ctypes.c_void_p), ('length', ctypes.c_uint32), ('recorded', ctypes.c_uint32),
        ('user', ctypes.c_size_t), ('flags', ctypes.c_uint32), ('loops', ctypes.c_uint32),
        ('next', ctypes.c_void_p), ('reserved', ctypes.c_size_t),
    ]


class _WaveTime(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint32), ('value', ctypes.c_uint32), ('reserved', ctypes.c_uint32)]


class _WaveOutput:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError(tr('Звуковой предпросмотр доступен в Windows.'))
        self.device = ctypes.c_void_p()
        wave = ctypes.WinDLL('winmm')
        self.api = {}
        handle = ctypes.c_void_p
        headers = [handle, ctypes.POINTER(_WaveHeader), ctypes.c_uint32]
        for name, arguments in (
            ('Open', [ctypes.POINTER(handle), ctypes.c_uint32, ctypes.POINTER(_WaveFormat),
                      ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint32]),
            ('Pause', [handle]), ('Restart', [handle]), ('Reset', [handle]), ('Close', [handle]),
            ('PrepareHeader', headers), ('UnprepareHeader', headers), ('Write', headers),
            ('GetPosition', [handle, ctypes.POINTER(_WaveTime), ctypes.c_uint32]),
        ):
            function = getattr(wave, 'waveOut' + name)
            function.argtypes = arguments
            function.restype = ctypes.c_uint32
            self.api[name] = function
        self.buffers = deque()

    def _call(self, name, *args):
        result = self.api[name](*args)
        if result:
            raise RuntimeError(tr('Не удалось воспроизвести звук (код {0}).').format(result))

    def __enter__(self):
        pcm = _WaveFormat(1, _CHANNELS, _RATE, _RATE * _BYTES_PER_SAMPLE, _BYTES_PER_SAMPLE, 16, 0)
        self._call('Open', ctypes.byref(self.device), 0xFFFFFFFF, ctypes.byref(pcm), 0, 0, 0)
        try:
            self._call('Pause', self.device)
        except RuntimeError:
            self.api['Close'](self.device)
            raise
        return self

    def __exit__(self, *_args):
        self.api['Reset'](self.device)
        try:
            self.release_finished()
        finally:
            self.api['Close'](self.device)

    def release_finished(self):
        while self.buffers and self.buffers[0][0].flags & _DONE:
            header, _data = self.buffers.popleft()
            self._call('UnprepareHeader', self.device, ctypes.byref(header), ctypes.sizeof(header))

    def enqueue(self, data):
        buffer = ctypes.create_string_buffer(data, len(data))
        header = _WaveHeader(ctypes.addressof(buffer), len(data))
        self._call('PrepareHeader', self.device, ctypes.byref(header), ctypes.sizeof(header))
        try:
            self._call('Write', self.device, ctypes.byref(header), ctypes.sizeof(header))
        except RuntimeError:
            self.api['UnprepareHeader'](self.device, ctypes.byref(header), ctypes.sizeof(header))
            raise
        self.buffers.append((header, buffer))

    def start(self):
        self._call('Restart', self.device)

    def elapsed(self):
        position = _WaveTime(_TIME_SAMPLES)
        self._call('GetPosition', self.device, ctypes.byref(position), ctypes.sizeof(position))
        divisors = {_TIME_MS: 1000, _TIME_SAMPLES: _RATE, _TIME_BYTES: _RATE * _BYTES_PER_SAMPLE}
        if position.type not in divisors:
            raise RuntimeError(tr('Не удалось синхронизировать предпросмотр со звуком.'))
        return position.value / divisors[position.type]


class VideoPreview:
    def __init__(self, ffmpeg, source, start, duration, frame_size, fps, has_audio):
        self.start = start
        self.duration = duration
        self.fps = min(25, fps)
        scale = min(1, 720 / max(frame_size))
        self.size = tuple(max(1, round(value * scale)) for value in frame_size)
        self.input_args = [ffmpeg, '-nostdin', '-v', 'error', '-ss', f'{start:.6f}', '-threads', '2', '-i', str(source)]
        self.frames = queue.Queue(maxsize=6)
        self.next_frame = None
        self.has_audio = has_audio
        self.audio_elapsed = 0.0
        self.wall_start = None
        self.frame_available = threading.Event()
        self.cancel = threading.Event()
        self.processes = set()
        self.lock = threading.Lock()
        self.error = None
        self._launch(self._video)
        if has_audio:
            self._launch(self._audio)

    def _launch(self, work):
        def guard():
            try:
                work()
            except (OSError, RuntimeError, ValueError) as exc:
                if not self.cancel.is_set():
                    self.error = str(exc)
                    self.close()

        threading.Thread(target=guard, daemon=True, name='jetvideo-preview').start()

    @contextmanager
    def _decoder(self, output_args):
        with tempfile.TemporaryFile() as errors:
            with subprocess.Popen(self.input_args + output_args, stdout=subprocess.PIPE,
                                  stderr=errors, creationflags=_FLAGS) as process:
                with self.lock:
                    self.processes.add(process)
                    if self.cancel.is_set():
                        process.kill()
                try:
                    yield process.stdout
                    if not self.cancel.is_set() and process.wait():
                        errors.seek(0)
                        detail = errors.read().decode('utf-8', 'replace').strip()
                        raise RuntimeError(detail or tr('Не удалось воспроизвести видео.'))
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    with self.lock:
                        self.processes.discard(process)

    def _video(self):
        width, height = self.size
        size = width * height * 3
        frames = 0
        with self._decoder(['-map', '0:v:0', '-an', '-vf',
                            f'fps={self.fps:g}:start_time=0,scale={width}:{height}:flags=fast_bilinear',
                            '-c:v', 'rawvideo', '-threads:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']) as pipe:
            while not self.cancel.is_set():
                data = pipe.read(size)
                if len(data) < size:
                    break
                while not self.cancel.is_set():
                    try:
                        self.frames.put((frames / self.fps, data), timeout=.08)
                        break
                    except queue.Full:
                        continue
                frames += 1
                self.frame_available.set()
        if not frames and not self.cancel.is_set():
            raise RuntimeError(tr('Не удалось воспроизвести видео.'))

    def _audio(self):
        with _WaveOutput() as output, self._decoder([
                '-map', '0:a:0', '-vn', '-af', f'aresample={_RATE}:async=1:first_pts=0',
                '-ac', str(_CHANNELS), '-ar', str(_RATE), '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1',
        ]) as pipe:
            exhausted = False

            def fill():
                nonlocal exhausted
                while not exhausted and not self.cancel.is_set() and len(output.buffers) < 4:
                    data = pipe.read(_CHUNK_BYTES)
                    exhausted = not data
                    if data:
                        output.enqueue(data)

            fill()
            while not self.frame_available.wait(.02):
                if self.cancel.is_set():
                    return
            if self.cancel.is_set():
                return
            output.start()
            while not self.cancel.is_set():
                self.audio_elapsed = output.elapsed()
                output.release_finished()
                if exhausted and not output.buffers:
                    self.wall_start = time.monotonic() - self.audio_elapsed
                    return
                fill()
                self.cancel.wait(.008)

    def poll(self):
        if not self.has_audio and self.wall_start is None and self.frame_available.is_set():
            self.wall_start = time.monotonic()
        elapsed = time.monotonic() - self.wall_start if self.wall_start is not None else self.audio_elapsed
        position = min(self.duration, self.start + elapsed)
        frame = None
        while True:
            if self.next_frame is None:
                try:
                    self.next_frame = self.frames.get_nowait()
                except queue.Empty:
                    break
            timestamp, data = self.next_frame
            if timestamp > elapsed:
                break
            frame = data
            self.next_frame = None
        return position, Image.frombytes('RGB', self.size, frame) if frame else None

    def close(self):
        self.cancel.set()
        with self.lock:
            for process in self.processes:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
