#!/usr/bin/env python3

from __future__ import annotations

from app_info import APP_NAME
from i18n import file_types, localized_combobox, tr
import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from media_files import commit_new_file

PAD_TO_EVEN = 'pad=ceil(iw/2)*2:ceil(ih/2)*2'
VIDEO_TYPES = [("Видео", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("Все файлы", "*.*")]
BANNER_TYPES = [("Баннеры", "*.webm *.mp4 *.mov *.mkv *.png *.jpg *.jpeg"), ("Все файлы", "*.*")]


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: str
    has_audio: bool
    codec: str = ''


def run_capture(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=60,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def find_ffprobe(ffmpeg_path: str) -> str | None:
    p = Path(ffmpeg_path)
    candidate = p.with_name("ffprobe.exe" if p.suffix.lower() == ".exe" else "ffprobe")
    if candidate.exists():
        return str(candidate)
    return shutil.which("ffprobe")


def probe_media(ffprobe: str, path: str) -> MediaInfo:
    cp = run_capture([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or tr('FFprobe не смог прочитать файл.'))
    data = json.loads(cp.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError(tr('В выбранном файле не найден видеопоток.'))
    duration_values = []
    if data.get("format", {}).get("duration"):
        duration_values.append(float(data["format"]["duration"]))
    if v.get("duration"):
        duration_values.append(float(v["duration"]))
    if not duration_values:
        raise RuntimeError(tr('Не удалось определить длительность файла.'))
    duration = max(duration_values)
    fps_raw = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    try:
        rate = Fraction(fps_raw)
        if rate <= 0 or float(rate) > 120:
            rate = Fraction(30, 1)
        fps = f"{rate.numerator}/{rate.denominator}"
    except Exception:
        fps = "30/1"
    return MediaInfo(duration, int(v.get("width", 0)), int(v.get("height", 0)), fps,
                     any(s.get("codec_type") == "audio" for s in streams), v.get('codec_name', ''))


def parse_time(value: str) -> float:
    value = value.strip().replace(",", ".")
    if not value:
        raise ValueError(tr('Укажи время вставки, например 00:00:20 или 20.'))
    parts = value.split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        raise ValueError(tr('Неверный формат времени. Используй секунды, ММ:СС или ЧЧ:ММ:СС.'))
    if len(nums) == 1:
        result = nums[0]
    elif len(nums) == 2:
        result = nums[0] * 60 + nums[1]
    elif len(nums) == 3:
        result = nums[0] * 3600 + nums[1] * 60 + nums[2]
    else:
        raise ValueError(tr('Неверный формат времени. Используй секунды, ММ:СС или ЧЧ:ММ:СС.'))
    if not math.isfinite(result) or result < 0:
        raise ValueError(tr('Время должно быть неотрицательным конечным числом.'))
    return result



def prepare_insertions(items: list[dict]) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise ValueError(tr('Добавь хотя бы одну рекламную вставку.'))
    if len(items) > 40:
        raise ValueError(tr('Для одного ролика допускается не более 40 вставок.'))
    result = []
    for n, entry in enumerate(items, start=1):
        if not isinstance(entry, dict):
            raise ValueError(tr('Вставка {0}: неправильный формат.').format(n))
        path = str(entry.get("banner", "")).strip()
        if not path or not Path(path).is_file():
            raise ValueError(tr('Вставка {0}: рекламный файл не найден: {1}').format(n, path))
        timestamp = parse_time(str(entry.get("time", entry.get("seconds", ""))))
        item = dict(entry)
        item.update(time=timestamp, banner=path, ordinal=n)
        if item.get('kind', 'freeze') not in ('freeze', 'overlay'):
            raise ValueError(tr('Неизвестный режим баннера.'))
        for key, default in [('x', 0), ('y', 0), ('width', 100), ('height', 100), ('opacity', 1)]:
            value = float(item.get(key, default))
            limit = 1 if key == 'opacity' else 100
            if not math.isfinite(value) or not 0 <= value <= limit or (key in ('width', 'height') and value == 0):
                raise ValueError(tr('Вставка {0}: некорректное значение {1}.').format(n, key))
            item[key] = value
        if item['x'] + item['width'] > 100 or item['y'] + item['height'] > 100:
            raise ValueError(tr('Вставка {0}: баннер выходит за пределы кадра.').format(n))
        if item.get('duration') not in (None, ''):
            item['duration'] = parse_time(str(item['duration']))
            if item['duration'] <= 0:
                raise ValueError(tr('Длительность баннера должна быть больше нуля.'))
        result.append(item)
    result.sort(key=lambda item: item["time"])
    return result


def validate_source_timeline(entries: list[dict], info: MediaInfo) -> None:
    frame = 1.0 / float(Fraction(info.fps))
    for entry in entries:
        t = entry["time"]
        if t < frame or t >= info.duration - frame:
            raise ValueError(
                tr('Вставка в {0:.2f} c не помещается в видео длиной {1:.2f} с. Разрешён диапазон: {2:.2f}–{3:.2f} с.').format(t, info.duration, frame, info.duration - frame)
            )
    last = None
    for entry in entries:
        if entry.get('kind') == 'overlay':
            if entry['time'] < 0 or entry['time'] >= info.duration:
                raise ValueError(tr('Начало баннера вне исходного видео.'))
            continue
        t = entry["time"]
        if last is not None and 0 < t - last < frame - 1e-6:
            raise ValueError(
                tr('Вставки в {0:.3f} и {1:.3f} слишком близко для частоты {2}. Поставь их в одно время или разнеси минимум на один кадр.').format(last, t, info.fps)
            )
        last = t


def safe_stem(path: str) -> str:
    return Path(path).stem


def source_input_args(source: str, opt: dict) -> list[str]:
    segment = opt.get('source_segment')
    if segment is None:
        return ['-i', source]
    return ['-ss', f"{segment['start']:.9f}", '-t', f"{segment['duration']:.9f}", '-i', source]


class VideoAdInserter:
    def __init__(self, root: tk.Tk):
        self.root = root
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title(APP_NAME)
            self.root.geometry("900x760")
            self.root.minsize(780, 650)

        self.video_files: list[str] = []
        self.banner_file = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.insert_at = tk.StringVar(value="00:00:20")
        self.banner_mode = tk.StringVar(value="auto")
        self.fit_mode = tk.StringVar(value="Вписать баннер (без искажения)")
        self.key_color = tk.StringVar(value="0x0000FF")
        self.key_similarity = tk.StringVar(value="0.25")
        self.key_blend = tk.StringVar(value="0.08")
        self.crf = tk.StringVar(value="18")
        self.include_banner_audio = tk.BooleanVar(value=True)
        self.ffmpeg_path = tk.StringVar(value=shutil.which("ffmpeg") or "")
        self.status = tk.StringVar(value=tr('Добавь исходные видео и рекламный баннер.'))
        self.events: queue.Queue = queue.Queue()
        self.busy = False

        self._build_ui()
        self.root.after(100, self._process_events)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text=APP_NAME, font=("Segoe UI", 19, "bold"))
        title.pack(anchor="w")
        ttk.Label(outer, text=tr('Вставляет анимированный баннер поверх замороженного кадра, затем продолжает исходное видео.')).pack(anchor="w", pady=(2, 12))

        files = ttk.LabelFrame(outer, text=tr('1. Файлы'), padding=10)
        files.pack(fill="x", pady=5)
        row = ttk.Frame(files); row.pack(fill="x")
        ttk.Button(row, text=tr('Выбрать видео (можно несколько)'), command=self.choose_videos).pack(side="left")
        ttk.Button(row, text=tr('Удалить выбранные'), command=self.remove_selected).pack(side="left", padx=8)
        self.video_list = tk.Listbox(files, height=4, selectmode="extended", activestyle="dotbox")
        self.video_list.pack(fill="x", pady=(8, 4))
        self.video_count = ttk.Label(files, text=tr('Видео не выбраны'))
        self.video_count.pack(anchor="w")

        banner_row = ttk.Frame(files); banner_row.pack(fill="x", pady=(10, 4))
        ttk.Label(banner_row, text=tr('Рекламный баннер:')).pack(side="left")
        ttk.Entry(banner_row, textvariable=self.banner_file).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(banner_row, text=tr('Обзор…'), command=self.choose_banner).pack(side="left")
        out_row = ttk.Frame(files); out_row.pack(fill="x", pady=4)
        ttk.Label(out_row, text=tr('Папка результата:')).pack(side="left")
        ttk.Entry(out_row, textvariable=self.output_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(out_row, text=tr('Обзор…'), command=self.choose_output).pack(side="left")

        settings = ttk.LabelFrame(outer, text=tr('2. Настройки вставки'), padding=10)
        settings.pack(fill="x", pady=5)
        r1 = ttk.Frame(settings); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text=tr('Вставить после:')).pack(side="left")
        ttk.Entry(r1, textvariable=self.insert_at, width=16).pack(side="left", padx=8)
        ttk.Label(r1, text=tr('(секунды, ММ:СС или ЧЧ:ММ:СС)')).pack(side="left")

        r2 = ttk.Frame(settings); r2.pack(fill="x", pady=3)
        ttk.Label(r2, text=tr('Тип баннера:')).pack(side="left")
        self.mode_combo = localized_combobox(r2, self.banner_mode,
            ["auto", "Прозрачный WebM (alpha)", "Синий фон MP4 (удалить фон)", "Без удаления фона"], width=33)
        self.mode_combo.pack(side="left", padx=8)
        ttk.Label(r2, text=tr('auto = прозрачность WebM / иначе без хромакея')).pack(side="left")

        r3 = ttk.Frame(settings); r3.pack(fill="x", pady=3)
        ttk.Label(r3, text=tr('Размещение:')).pack(side="left")
        fit_combo = localized_combobox(r3, self.fit_mode,
            ["Вписать баннер (без искажения)", "Заполнить кадр с обрезкой", "Растянуть на весь кадр"], width=33)
        fit_combo.pack(side="left", padx=8)
        ttk.Label(r3, text=tr('Для большинства рекламных файлов подойдёт первый вариант.')).pack(side="left")

        r4 = ttk.Frame(settings); r4.pack(fill="x", pady=3)
        ttk.Label(r4, text=tr('Цвет фона для хромакея:')).pack(side="left")
        ttk.Entry(r4, textvariable=self.key_color, width=12).pack(side="left", padx=8)
        ttk.Label(r4, text=tr('Похожесть:')).pack(side="left")
        ttk.Entry(r4, textvariable=self.key_similarity, width=7).pack(side="left", padx=5)
        ttk.Label(r4, text=tr('Смешивание:')).pack(side="left")
        ttk.Entry(r4, textvariable=self.key_blend, width=7).pack(side="left", padx=5)
        ttk.Label(r4, text=tr('(для синего фона; начни с 0.25 и 0.08)')).pack(side="left")

        r5 = ttk.Frame(settings); r5.pack(fill="x", pady=3)
        ttk.Label(r5, text=tr('Качество H.264 (CRF):')).pack(side="left")
        ttk.Entry(r5, textvariable=self.crf, width=6).pack(side="left", padx=8)
        ttk.Label(r5, text=tr('18 — высокое качество; 20–23 — меньше размер')).pack(side="left")

        r6 = ttk.Frame(settings); r6.pack(fill="x", pady=3)
        ttk.Checkbutton(r6, text=tr('Добавлять звук рекламного файла (если он есть)'),
                        variable=self.include_banner_audio).pack(side="left")

        ff = ttk.LabelFrame(outer, text="3. FFmpeg", padding=10)
        ff.pack(fill="x", pady=5)
        ffrow = ttk.Frame(ff); ffrow.pack(fill="x")
        ttk.Label(ffrow, text=tr('Путь к ffmpeg.exe (если не найден автоматически):')).pack(side="left")
        ttk.Entry(ffrow, textvariable=self.ffmpeg_path).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(ffrow, text=tr('Обзор…'), command=self.choose_ffmpeg).pack(side="left")

        actions = ttk.Frame(outer); actions.pack(fill="x", pady=(10, 5))
        self.start_btn = ttk.Button(actions, text=tr('СОЗДАТЬ ВИДЕО'), command=self.start_processing)
        self.start_btn.pack(side="left")
        ttk.Button(actions, text=tr('Открыть папку результата'), command=self.open_output).pack(side="left", padx=8)
        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=10)

        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(2, 4))
        log_frame = ttk.LabelFrame(outer, text=tr('Журнал'), padding=6)
        log_frame.pack(fill="both", expand=True, pady=4)
        self.log = tk.Text(log_frame, height=9, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

        footer = ttk.Label(outer, text=tr('На время рекламы исходный звук ставится на паузу; звук баннера воспроизводится, если он есть. Для синего фона MP4 используй режим хромакея.'), foreground="#555")
        footer.pack(anchor="w", pady=(5, 0))

    def log_message(self, message: str):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose_videos(self):
        paths = filedialog.askopenfilenames(title=tr('Выбери исходные видео'), filetypes=file_types(VIDEO_TYPES))
        if paths:
            existing = set(self.video_files)
            self.video_files.extend(p for p in paths if p not in existing)
            self.refresh_video_list()
            if not self.output_dir.get():
                self.output_dir.set(str(Path(self.video_files[0]).parent / "output_with_ads"))

    def refresh_video_list(self):
        self.video_list.delete(0, "end")
        for p in self.video_files:
            self.video_list.insert("end", p)
        self.video_count.configure(text=tr('Выбрано видео: {0}').format(len(self.video_files)))

    def remove_selected(self):
        indexes = list(self.video_list.curselection())
        for i in reversed(indexes):
            del self.video_files[i]
        self.refresh_video_list()

    def choose_banner(self):
        p = filedialog.askopenfilename(title=tr('Выбери рекламный баннер'), filetypes=file_types(BANNER_TYPES))
        if p:
            self.banner_file.set(p)
            if Path(p).suffix.lower() == ".webm":
                self.banner_mode.set("Прозрачный WebM (alpha)")
            else:
                self.banner_mode.set("Синий фон MP4 (удалить фон)")

    def choose_output(self):
        p = filedialog.askdirectory(title=tr('Папка для готовых видео'))
        if p:
            self.output_dir.set(p)

    def choose_ffmpeg(self):
        p = filedialog.askopenfilename(title=tr('Выбери ffmpeg.exe'), filetypes=[("FFmpeg", "ffmpeg.exe"), (tr('Все файлы'), "*.*")])
        if p:
            self.ffmpeg_path.set(p)

    def open_output(self):
        p = self.output_dir.get().strip()
        if not p:
            messagebox.showinfo(APP_NAME, tr('Сначала выбери папку результата.'))
            return
        try:
            folder = Path(p).resolve()
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, tr('Не удалось открыть папку экспорта:\n{0}').format(exc), parent=self.root)

    def start_processing(self):
        if self.busy:
            return
        if not self.video_files:
            messagebox.showwarning(APP_NAME, tr('Сначала выбери хотя бы одно исходное видео.'))
            return
        modern_events = getattr(self, "insertions", None)
        if modern_events is not None:
            raw_insertions = [dict(item) for item in modern_events]
        else:
            raw_insertions = [{"banner": self.banner_file.get(), "time": self.insert_at.get()}]
        try:
            entries = prepare_insertions(raw_insertions)
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        output = self.output_dir.get().strip()
        if not output:
            messagebox.showwarning(APP_NAME, tr('Выбери папку для результата.'))
            return
        ffmpeg = self.ffmpeg_path.get().strip() or shutil.which("ffmpeg") or ""
        if not ffmpeg or not Path(ffmpeg).exists():
            messagebox.showerror(APP_NAME, tr('Не найден ffmpeg. Установи FFmpeg или укажи путь к ffmpeg.exe.'))
            return
        ffprobe = find_ffprobe(ffmpeg)
        if not ffprobe:
            messagebox.showerror(APP_NAME, tr('Не найден ffprobe.exe рядом с ffmpeg.exe.'))
            return
        try:
            crf = int(self.crf.get())
            if not 15 <= crf <= 28:
                raise ValueError(tr('CRF должен быть в диапазоне 15–28.'))
            sim = float(self.key_similarity.get())
            blend = float(self.key_blend.get())
            if not (0 <= sim <= 1 and 0 <= blend <= 1):
                raise ValueError(tr('Похожесть и смешивание должны быть в диапазоне 0–1.'))
            color = self.key_color.get().strip()
            if not color.startswith("0x") or len(color) != 8 or any(c not in '0123456789abcdefABCDEF' for c in color[2:]):
                raise ValueError(tr('Цвет хромакея укажи в формате 0xRRGGBB, например 0x0000FF.'))
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        options = {
            "videos": list(self.video_files), "insertions": entries, "output": output,
            "banner_mode": self.banner_mode.get(), "fit_mode": self.fit_mode.get(),
            "key_color": color, "key_similarity": sim, "key_blend": blend, "crf": crf,
            "include_banner_audio": self.include_banner_audio.get(),
            "ffmpeg": ffmpeg, "ffprobe": ffprobe,
        }
        self.busy = True
        self.cancel_event = threading.Event()
        self.rendered_files = []
        self.start_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=len(self.video_files) * 100)
        self.log_message(tr('— Начало обработки: {0} вставок на видео —').format(len(entries)))
        threading.Thread(target=self.worker, args=(options,), daemon=True).start()

    def unique_output_path(self, folder: str, source: str) -> str:
        base = f"{safe_stem(source)}_with_ad"
        p = Path(folder) / f"{base}.mp4"
        n = 2
        while p.exists():
            p = Path(folder) / f"{base}_{n}.mp4"
            n += 1
        return str(p)

    def worker(self, opt: dict):
        successful = 0
        failed = 0
        cancel = getattr(self, 'cancel_event', threading.Event())
        try:
            output = Path(opt["output"])
            output.mkdir(parents=True, exist_ok=True)
            entries = prepare_insertions(opt.get("insertions") or [{
                "banner": opt.get("banner", ""), "time": opt.get("insert_seconds", "")
            }])
            kinds = {e.get('kind', 'freeze') for e in entries}
            if len(kinds) > 1:
                raise ValueError(tr('Используй один режим для всего шаблона: наложение или стоп-кадр.'))
            metadata = {}
            for entry in entries:
                path = entry["banner"]
                if path not in metadata:
                    if Path(path).suffix.lower() in ('.png', '.jpg', '.jpeg'):
                        cp = run_capture([opt['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', path])
                        stream = json.loads(cp.stdout)['streams'][0]
                        info = MediaInfo(float(entry.get('duration') or 3), int(stream['width']), int(stream['height']), '30/1', False)
                    else:
                        info = probe_media(opt["ffprobe"], path)
                    if info.duration <= 0.05:
                        raise RuntimeError(tr('Некорректная длительность рекламы: {0}').format(path))
                    metadata[path] = info
                mode = entry.get("mode", "Общие настройки")
                if mode == "Общие настройки":
                    mode = opt["banner_mode"]
                if mode == "auto":
                    mode = "Прозрачный WebM (alpha)" if Path(path).suffix.lower() == ".webm" else "Без удаления фона"
                entry["duration"] = float(entry.get('duration') or metadata[path].duration)
                entry["audio"] = metadata[path].has_audio
                entry['codec'] = metadata[path].codec
                entry["mode"] = mode
            for index, source in enumerate(opt["videos"], start=1):
                if cancel.is_set():
                    self.events.put(('cancelled', successful, failed))
                    return
                self.events.put(("log", tr('[{0}/{1}] Проверка: {2}').format(index, len(opt['videos']), source)))
                temp = output / f'.jojo-render-{uuid.uuid4().hex}.mp4.tmp'
                try:
                    info = probe_media(opt["ffprobe"], source)
                    if kinds == {'overlay'}:
                        if any(e['time'] >= info.duration for e in entries):
                            raise ValueError(tr('Время появления баннера вне длительности видео.'))
                    else:
                        validate_source_timeline(entries, info)
                    target = self.unique_output_path(opt["output"], source)
                    if kinds == {'overlay'}:
                        from overlay_render import render_overlay
                        render_overlay(self, opt, source, str(temp), info, entries, index, len(opt['videos']))
                    else:
                        self.make_one(opt, source, str(temp), info, entries, index, len(opt["videos"]))
                    if cancel.is_set():
                        self.events.put(('cancelled', successful, failed))
                        return
                    commit_new_file(temp, Path(target))
                    successful += 1
                    self.events.put(("rendered", target))
                    self.events.put(("log", tr('Готово: {0} ({1} вставок)').format(target, len(entries))))
                except Exception as e:
                    if cancel.is_set():
                        self.events.put(('cancelled', successful, failed))
                        return
                    failed += 1
                    self.events.put(("log", tr('ОШИБКА для {0}: {1}').format(source, e)))
                finally:
                    temp.unlink(missing_ok=True)
                self.events.put(("progress", index * 100))
            self.events.put(("done", successful, failed, opt["output"]))
        except Exception as e:
            self.events.put(("fatal", str(e)))

    def make_one(self, opt: dict, source: str, target: str, info: MediaInfo,
                 entries: list[dict], index: int, total: int):
        ffmpeg = opt["ffmpeg"]
        fps = info.fps
        w, h = info.width, info.height
        n = len(entries)
        audio_needed = info.has_audio or (
            opt.get("include_banner_audio", True) and any(e["audio"] for e in entries)
        )
        with tempfile.TemporaryDirectory(prefix="video_ad_inserter_") as temp_dir:
            freezes = []
            for k, entry in enumerate(entries):
                if getattr(self, 'cancel_event', threading.Event()).is_set():
                    raise RuntimeError(tr('Обработка отменена.'))
                still = str(Path(temp_dir) / f"freeze_{k:02d}.png")
                seek = entry['time'] + opt.get('source_segment', {}).get('start', 0)
                extract = [ffmpeg, "-hide_banner", '-nostdin', "-loglevel", "error", "-y",
                           "-ss", f"{seek:.9f}", "-i", source, "-frames:v", "1", "-update", "1", still]
                cp = run_capture(extract)
                if cp.returncode != 0 or not Path(still).exists():
                    raise RuntimeError(tr('Не удалось извлечь стоп-кадр {0}: ').format(k + 1) +
                                       (cp.stderr.strip()[-1200:] or tr('неизвестная ошибка')))
                freezes.append(still)

            args = [ffmpeg, "-hide_banner", '-nostdin', "-y"] + source_input_args(source, opt)
            for entry in entries:
                if Path(entry['banner']).suffix.lower() in ('.png', '.jpg', '.jpeg'):
                    args += ['-loop', '1']
                else:
                    args += ['-stream_loop', '-1']
                if entry['mode'] == 'Прозрачный WebM (alpha)' and entry.get('codec') in ('vp8', 'vp9'):
                    args += ['-c:v', 'libvpx-vp9' if entry['codec'] == 'vp9' else 'libvpx']
                args += ["-i", entry["banner"]]
            for entry, still in zip(entries, freezes):
                args += ["-loop", "1", "-framerate", fps, "-t", f"{entry['duration']:.6f}", "-i", still]

            if opt["fit_mode"] == "Заполнить кадр с обрезкой":
                scale_filter = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
            elif opt["fit_mode"] == "Растянуть на весь кадр":
                scale_filter = f"scale={w}:{h}"
            else:
                scale_filter = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0")

            filters = []
            video_parts = []
            audio_parts = []
            audio_format = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"

            def append_original_segment(start: float, stop: float):
                length = stop - start
                if length < 1e-7:
                    return
                j = len(video_parts)
                vtag, atag = f"vsrc{j}", f"asrc{j}"
                filters.append(
                    f"[0:v:0]fps={fps},trim=start={start:.6f}:duration={length:.6f},"
                    f"setpts=PTS-STARTPTS,setsar=1,format=yuv420p[{vtag}]"
                )
                video_parts.append(f"[{vtag}]")
                if audio_needed:
                    if info.has_audio:
                        filters.append(
                            f"[0:a:0]atrim=start={start:.6f}:duration={length:.6f},"
                            f"asetpts=PTS-STARTPTS,{audio_format},apad=pad_dur={length:.6f},"
                            f"atrim=duration={length:.6f}[{atag}]"
                        )
                    else:
                        filters.append(
                            f"anullsrc=r=48000:cl=stereo,atrim=duration={length:.6f},"
                            f"asetpts=PTS-STARTPTS[{atag}]"
                        )
                    audio_parts.append(f"[{atag}]")

            previous_time = 0.0
            for k, entry in enumerate(entries):
                t = entry["time"]
                append_original_segment(previous_time, t)
                previous_time = t
                duration = entry["duration"]
                banner_input = k + 1
                freeze_input = k + 1 + n
                banner_filters = "format=rgba"
                if entry["mode"] == "Синий фон MP4 (удалить фон)":
                    banner_filters += (f",colorkey={opt['key_color']}:"
                                       f"{opt['key_similarity']:.3f}:{opt['key_blend']:.3f}")
                bw = max(2, int(w * entry.get('width', 100) / 100) // 2 * 2)
                bh = max(2, int(h * entry.get('height', 100) / 100) // 2 * 2)
                layout = (f'scale={bw}:{bh}:force_original_aspect_ratio=decrease,'
                          f'pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=black@0')
                effective_scale = layout if any(entry.get(k, d) != d for k, d in [('width', 100), ('height', 100)]) else scale_filter
                banner_filters += f",{effective_scale},format=rgba,colorchannelmixer=aa={entry.get('opacity', 1)},setpts=PTS-STARTPTS"
                filters += [
                    f"[{banner_input}:v:0]{banner_filters}[ban{k}]",
                    f"[{freeze_input}:v:0]fps={fps},trim=duration={duration:.6f},"
                    f"setpts=PTS-STARTPTS,scale={w}:{h},setsar=1,format=rgba[freeze{k}]",
                    f"[freeze{k}][ban{k}]overlay={w*entry.get('x',0)/100:.2f}:{h*entry.get('y',0)/100:.2f}:shortest=1:format=auto,"
                    f"trim=duration={duration:.6f},setpts=PTS-STARTPTS,setsar=1,"
                    f"format=yuv420p[vad{k}]",
                ]
                video_parts.append(f"[vad{k}]")
                if audio_needed:
                    a_tag = f"aad{k}"
                    if entry["audio"] and opt.get("include_banner_audio", True):
                        filters.append(
                            f"[{banner_input}:a:0]atrim=start=0:duration={duration:.6f},"
                            f"asetpts=PTS-STARTPTS,{audio_format},apad=pad_dur={duration:.6f},"
                            f"atrim=duration={duration:.6f}[{a_tag}]"
                        )
                    else:
                        filters.append(
                            f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f},"
                            f"asetpts=PTS-STARTPTS[{a_tag}]"
                        )
                    audio_parts.append(f"[{a_tag}]")
            append_original_segment(previous_time, info.duration)
            filters.append("".join(video_parts) + f"concat=n={len(video_parts)}:v=1:a=0,{PAD_TO_EVEN}[vout]")
            if audio_needed:
                filters.append("".join(audio_parts) + f"concat=n={len(audio_parts)}:v=0:a=1[aout]")

            args += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
            if audio_needed:
                args += ["-map", "[aout]"]
            args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(opt["crf"]),
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            if audio_needed:
                args += ["-c:a", "aac", "-b:a", "192k"]
            args += ["-progress", "pipe:1", "-nostats", "-loglevel", "error", '-f', 'mp4', target]
            with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as error_log, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=error_log,
                                  text=True, encoding="utf-8", errors="replace", bufsize=1,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as proc:
                self._monitor_cancel(proc)
                out_total = info.duration + sum(e["duration"] for e in entries)
                assert proc.stdout is not None
                for line in proc.stdout:
                    if line.startswith("out_time_ms="):
                        try:
                            out_sec = int(line.strip().split("=", 1)[1]) / 1_000_000
                            pct = max(0, min(99, int(out_sec / out_total * 100)))
                            self.events.put(("status", tr('Обработка {0}/{1}: {2} — {3}%').format(index, total, Path(source).name, pct)))
                            self.events.put(('progress', (index-1)*100 + pct))
                        except Exception:
                            pass
                return_code = proc.wait()
                error_log.seek(0)
                stderr = error_log.read()
            if return_code != 0:
                try:
                    Path(target).unlink(missing_ok=True)
                except Exception:
                    pass
                raise RuntimeError(stderr.strip()[-2500:] or tr('FFmpeg завершился с кодом {0}.').format(return_code))
            if not Path(target).exists() or Path(target).stat().st_size < 1024:
                raise RuntimeError(tr('FFmpeg не создал корректный выходной файл.'))

    def cancel_processing(self):
        if self.busy:
            self.cancel_event.set()
            self.status.set(tr('Отмена кодирования…'))

    def _monitor_cancel(self, proc):
        cancel = getattr(self, 'cancel_event', None)
        if cancel is None:
            return

        def monitor():
            while proc.poll() is None:
                if cancel.wait(0.1):
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
        threading.Thread(target=monitor, daemon=True).start()

    def _process_events(self):
        try:
            while True:
                item = self.events.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log_message(item[1])
                elif kind == "rendered":
                    self.rendered_files.append(item[1])
                    callback = getattr(self, "on_rendered", None)
                    if callback is not None:
                        callback(item[1])
                elif kind == "progress":
                    self.progress.configure(value=item[1])
                elif kind == 'progress_max':
                    self.progress.configure(maximum=item[1], value=0)
                elif kind == "status":
                    self.status.set(item[1])
                elif kind == "done":
                    _, successful, failed, output = item
                    self.busy = False
                    self.start_btn.configure(state="normal")
                    self.progress.configure(value=self.progress["maximum"])
                    self.status.set(tr('Завершено. Успешно: {0}; с ошибками: {1}.').format(successful, failed))
                    self.log_message(tr('— Завершено. Успешно: {0}; с ошибками: {1}. —').format(successful, failed))
                    messagebox.showinfo(APP_NAME, tr('Обработка завершена.\nУспешно: {0}\nС ошибками: {1}\n\nПапка: {2}').format(successful, failed, output))
                elif kind == "fatal":
                    self.busy = False
                    self.start_btn.configure(state="normal")
                    self.status.set(tr('Ошибка обработки.'))
                    self.log_message(tr('КРИТИЧЕСКАЯ ОШИБКА: ') + item[1])
                    messagebox.showerror(APP_NAME, item[1])
                elif kind == 'cancelled':
                    self.busy = False
                    self.start_btn.configure(state='normal')
                    self.status.set(tr('Отменено. Готовых файлов: {0}. Исходники сохранены.').format(item[1]))
                    self.log_message(self.status.get())
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    VideoAdInserter(root)
    root.mainloop()


if __name__ == "__main__":
    main()
