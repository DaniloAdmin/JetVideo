from bisect import bisect_right
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from app_info import APP_NAME
from editor_ui import ACCENT_LIGHT, BG, BORDER, FG, FONT, MUTED, PANEL, PANEL2, resolve_video_tools
from i18n import file_types, language, tr
from subtitles import CaptionPainter, Cue, FONTS, SubtitleStyle, VAD_MODEL_NAME, cue_at, export_subtitled, frame_at, read_srt, transcribe, word_starts
from ui_icons import icon
from video_ad_inserter_core import VIDEO_TYPES, parse_time, probe_media
from video_preview import VideoPreview


def _time_label(ms):
    seconds, fraction = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{fraction:03d}'


def _whisper_tools():
    folder = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    folder = folder / 'tools' / 'whisper'
    binary = folder / ('whisper-cli.exe' if sys.platform == 'win32' else 'whisper-cli')
    model = folder / 'ggml-base.bin'
    return (binary, model) if binary.is_file() and model.is_file() else None


class SubtitleEditor:
    def __init__(self, editor):
        self.editor = editor
        width = min(1060, editor.root.winfo_screenwidth() - 48)
        self.top = editor._dialog(tr('Субтитры'), width, 780)
        self.top.protocol('WM_DELETE_WINDOW', self.close)
        self.top.bind('<Escape>', lambda _event: self.close())
        self.top.minsize(640, 490)
        self.source = self.info = self.image = self.ffmpeg = self.result = None
        self.result_step = 0
        self.cues, self.starts = [], []
        self.jobs = queue.SimpleQueue()
        self.job_cancel = None
        self.preview_request = None
        self.preview_running = False
        self.preview_ticket = 0
        self.playback = None
        self.video_box = None
        self.frame_cache = self.painter_settings = self.caption_cache = None
        self.selected_color, self.selected_accent = '#ffffff', '#ffe15c'
        self.font = tk.StringVar(self.top, 'Noto Sans')
        self.size = tk.StringVar(self.top, '5')
        self.width = tk.StringVar(self.top, '84')
        self.outline = tk.StringVar(self.top, '3')
        self.x = tk.StringVar(self.top, '50')
        self.y = tk.StringVar(self.top, '72')
        self.keyframes = tk.StringVar(self.top, editor.cut_duration.get())
        self.language = tk.StringVar(self.top, 'Українська' if language() == 'uk' else 'Русский')
        self.speech_only = tk.BooleanVar(self.top, True)
        self.highlight = tk.BooleanVar(self.top, False)
        self.background = tk.BooleanVar(self.top, False)
        self.local_position = tk.BooleanVar(self.top, False)
        self.time = tk.DoubleVar(self.top, 0)
        self.status = tk.StringVar(self.top, tr('Выбери видео и загрузи SRT или распознай речь.'))
        self.file_label = tk.StringVar(self.top, '')
        self._build()
        self.top.bind('<Destroy>', self._destroyed, add='+')
        self.top.after(100, self._poll)

    def show(self):
        self.editor._show_dialog(self.top)
        self.top.grab_set()
        if self.source is None and len(self.editor.video_files) == 1 and self.job_cancel is None:
            self._load_video(Path(self.editor.video_files[0]))

    def close(self):
        self.pause_playback()
        if self.job_cancel is None:
            if self.top.grab_current() is self.top:
                self.top.grab_release()
            self.top.withdraw()
        else:
            self.job_cancel.set()

    def _destroyed(self, event):
        if event.widget is self.top and self.playback is not None:
            self.playback.close()

    def _build(self):
        top = self.top
        editor = self.editor
        header = tk.Frame(top, bg=BG)
        header.pack(fill='x', pady=(0, 8))
        files = tk.Frame(header, bg=BG)
        files.grid(row=0, column=0, sticky='w')
        speech = tk.Frame(header, bg=BG)
        speech.grid(row=0, column=1, sticky='w', padx=(12, 0))
        self.open_button = editor._btn(files, tr('Выбрать видео'), self.select_video, glyph='video', compact=True)
        self.open_button.pack(side='left')
        self.import_button = editor._btn(files, tr('Загрузить SRT'), self.import_srt, glyph='captions', compact=True)
        self.import_button.pack(side='left', padx=(6, 0))
        ttk.Combobox(speech, textvariable=self.language, values=('Русский', 'Українська'),
                     state='readonly', width=10, style='Ad.TCombobox').pack(side='left', padx=(0, 4))
        self.transcribe_button = editor._btn(speech, tr('Распознать речь'), self.auto_captions, compact=True)
        self.transcribe_button.pack(side='left')
        self.speech_only_button = tk.Checkbutton(speech, text=tr('Только речь'), variable=self.speech_only,
                                                 bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
                                                 selectcolor=PANEL2)
        self.speech_only_button.pack(side='left', padx=(8, 0))

        def reflow_header(event):
            stacked = event.width < files.winfo_reqwidth() + speech.winfo_reqwidth() + 12
            if int(speech.grid_info()['row']) != int(stacked):
                speech.grid_configure(row=int(stacked), column=int(not stacked),
                                      padx=0 if stacked else (12, 0), pady=(5, 0) if stacked else 0)

        header.bind('<Configure>', reflow_header)
        tk.Label(top, textvariable=self.file_label, bg=BG, fg=MUTED, anchor='w').pack(fill='x')

        content = tk.PanedWindow(top, orient='horizontal', bg=BG, sashwidth=6, bd=0)
        content.pack(fill='both', expand=True, pady=6)
        video = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        rail = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        content.add(video, minsize=250, stretch='always')
        content.add(rail, minsize=330, width=370, stretch='never')
        sidebar = ttk.Notebook(rail, style='Ad.TNotebook')
        sidebar.pack(fill='both', expand=True, padx=8, pady=8)
        phrases = tk.Frame(sidebar, bg=PANEL, padx=6, pady=7)
        controls = tk.Frame(sidebar, bg=PANEL, padx=6, pady=7)
        sidebar.icons = (icon(sidebar, 'captions', ACCENT_LIGHT, 16), icon(sidebar, 'edit', ACCENT_LIGHT, 16))
        sidebar.add(phrases, text=tr('Фразы'), image=sidebar.icons[0], compound='left')
        sidebar.add(controls, text=tr('Оформление'), image=sidebar.icons[1], compound='left')
        self.canvas = tk.Canvas(video, bg='#050811', width=1, height=1, bd=0, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _event: self.redraw())
        self.canvas.bind('<Button-1>', self.move_caption)
        self.canvas.bind('<B1-Motion>', self.move_caption)
        self.frame_item = self.canvas.create_image(0, 0, anchor='nw')
        self.caption_item = self.canvas.create_image(0, 0, anchor='nw', state='hidden')
        playback_controls = tk.Frame(video, bg=PANEL)
        playback_controls.pack(side='bottom', fill='x', before=self.canvas, padx=8, pady=4)
        self.play_button = editor._btn(playback_controls, tr('Воспроизвести'), self.toggle_playback, glyph='play', compact=True)
        self.pause_icon = icon(self.play_button, 'pause', FG, 16)
        self.play_button.pack(side='left', padx=(0, 6))
        self.seek = tk.Scale(playback_controls, from_=0, to=0, resolution=.001, orient='horizontal',
                             variable=self.time, command=self._slider_changed, bg=PANEL, fg=FG,
                             troughcolor=PANEL2, highlightthickness=0)
        self.seek.pack(side='left', fill='x', expand=True)
        for event in ('<ButtonPress-1>', '<Left>', '<Right>', '<Up>', '<Down>', '<Home>', '<End>'):
            self.seek.bind(event, lambda _event: self.pause_playback(), add='+')

        font_row = tk.Frame(controls, bg=PANEL)
        font_row.pack(fill='x')
        editor._label(font_row, tr('Шрифт')).pack(side='left')
        ttk.Combobox(font_row, textvariable=self.font, values=tuple(FONTS), state='readonly',
                     width=17, style='Ad.TCombobox').pack(side='right')
        for fields in (
            ((tr('Размер, %'), self.size, 1, 12), (tr('Ширина, %'), self.width, 25, 100)),
            (('X, %', self.x, 0, 100), ('Y, %', self.y, 0, 100), (tr('Обводка'), self.outline, 0, 10)),
        ):
            row = tk.Frame(controls, bg=PANEL)
            row.pack(fill='x', pady=(6, 0))
            for label, variable, minimum, maximum in fields:
                editor._label(row, label, 9).pack(side='left', padx=(0, 3))
                ttk.Spinbox(row, textvariable=variable, from_=minimum, to=maximum, width=4,
                            style='Ad.TSpinbox').pack(side='left', padx=(0, 6))
        row = tk.Frame(controls, bg=PANEL)
        row.pack(fill='x', pady=(6, 2))
        self.text_color = editor._btn(row, tr('Цвет текста'), lambda: self.choose_color(False), compact=True)
        self.text_color.pack(side='left')
        self.accent_color = editor._btn(row, tr('Цвет подсветки'), lambda: self.choose_color(True), compact=True)
        self.accent_color.pack(side='left', padx=5)
        for label, setting in (
            (tr('Тёмная плашка'), self.background),
            (tr('Подсвечивать активное слово'), self.highlight),
            (tr('Перемещать только выбранную фразу'), self.local_position),
        ):
            tk.Checkbutton(controls, text=label, variable=setting, bg=PANEL, fg=FG,
                           activebackground=PANEL, activeforeground=FG, selectcolor=PANEL2).pack(anchor='w')
        tree_frame = tk.Frame(phrases, bg=PANEL2)
        tree_frame.pack(fill='both', expand=True, pady=(5, 0))
        self.listing = ttk.Treeview(tree_frame, columns=('time', 'text'), show='headings', height=4,
                                    style='Ad.Treeview', selectmode='browse')
        self.listing.heading('time', text=tr('Время'))
        self.listing.heading('text', text=tr('Фраза'))
        self.listing.column('time', width=83, stretch=False)
        self.listing.column('text', width=205)
        self.listing.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient='vertical', command=self.listing.yview, style='Ad.Vertical.TScrollbar')
        scrollbar.pack(side='right', fill='y')
        self.listing.configure(yscrollcommand=scrollbar.set)
        self.listing.bind('<<TreeviewSelect>>', self.select_cue)
        self.timing = editor._label(phrases, '', 9, MUTED, wraplength=305, justify='left')
        self.timing.pack(anchor='w', pady=(6, 0))
        self.text = tk.Text(phrases, height=2, bg=PANEL2, fg=FG, insertbackground=FG, wrap='word',
                            font=(FONT, 10), bd=0, padx=4, pady=3)
        self.text.pack(fill='x', pady=4)
        edit_row = tk.Frame(phrases, bg=PANEL)
        edit_row.pack(fill='x')
        editor._btn(edit_row, tr('Сохранить правку'), self.apply_edit, glyph='check', compact=True).pack(side='left')
        editor._btn(edit_row, tr('Сохранить SRT'), self.save_srt, compact=True).pack(side='right')

        footer = tk.Frame(top, bg=BG)
        footer.pack(side='bottom', fill='x', before=content)
        actions = tk.Frame(footer, bg=BG)
        actions.pack(fill='x', pady=(4, 0))
        self.export_button = editor._btn(actions, tr('Экспортировать MP4'), self.export, 'primary', glyph='upload', compact=True)
        self.export_button.pack(side='left')
        self.cut_button = editor._btn(actions, tr('Передать в нарезку'), self.send_to_cut, glyph='scissors', compact=True)
        self.cut_button.pack(side='left', padx=6)
        self.cancel_button = editor._btn(actions, tr('Отмена'), self.close, compact=True)
        self.cancel_button.pack(side='right')

        details = tk.Frame(footer, bg=BG)
        details.pack(fill='x', pady=(5, 0))
        ttk.Entry(details, textvariable=self.keyframes, width=8, style='Ad.TEntry').pack(side='right')
        editor._label(details, tr('Ключевые кадры через'), 9, MUTED, bg=BG).pack(side='right', padx=(10, 6))
        status = editor._label(details, '', 9, MUTED, bg=BG, textvariable=self.status,
                               wraplength=320, anchor='w', justify='left')
        status.pack(side='left', fill='x', expand=True)
        status.bind('<Configure>', lambda event: status.configure(wraplength=max(1, event.width)))
        for variable in (self.language, self.font, self.size, self.width, self.x, self.y,
                         self.outline, self.highlight, self.background):
            variable.trace_add('write', lambda *_: self.redraw())
        self._set_buttons()

    def _set_buttons(self):
        busy = self.job_cancel is not None
        for button in (self.open_button, self.import_button, self.transcribe_button, self.export_button,
                       self.speech_only_button):
            button.configure(state='disabled' if busy else 'normal')
        self.cut_button.configure(state='normal' if self.result and not busy else 'disabled')
        self.cancel_button.configure(text=tr('Отмена') if busy else tr('Закрыть'))
        self.play_button.configure(state='normal' if self.source and not busy else 'disabled',
                                   text=tr('Пауза') if self.playback is not None else tr('Воспроизвести'),
                                   image=self.pause_icon if self.playback is not None else self.play_button.icon)

    def _start(self, name, function):
        if self.job_cancel is not None:
            return
        self.pause_playback()
        self.job_cancel = threading.Event()
        self.editor.busy = True
        self.editor.cancel_event = self.job_cancel
        self.editor._update_badges()
        self._set_buttons()
        cancel = self.job_cancel

        def run():
            notify = lambda message: self.jobs.put(('progress', message))
            try:
                result = function(cancel, notify)
                self.jobs.put((name, result) if not cancel.is_set() else ('cancelled', None))
            except Exception as exc:
                self.jobs.put(('error', str(exc)))

        threading.Thread(target=run, daemon=True, name='jetvideo-subtitles').start()

    def _poll(self):
        while not self.jobs.empty():
            kind, data = self.jobs.get()
            if kind == 'progress':
                self.status.set(data)
                continue
            if kind == 'frame':
                ticket, image = data
                self.preview_running = False
                if image is not None and ticket == self.preview_ticket:
                    self.image = image
                    self.redraw()
                if self.preview_request is not None:
                    self._read_preview()
                continue
            self.job_cancel = None
            self.editor.busy = False
            self.editor._update_badges()
            if kind == 'open':
                self.source, self.ffmpeg, self.info, self.image = data
                self.frame_size = self.image.size
                self.result = None
                self.seek.configure(to=self.info.duration)
                self.seek.set(0)
                self.file_label.set(self.source.name)
                self.status.set(tr('Выбери видео и загрузи SRT или распознай речь.'))
                self.redraw()
            elif kind == 'cues':
                if data:
                    self._load_cues(data)
            elif kind == 'export':
                if data:
                    self.result, self.result_step = data
                    self.status.set(tr('Готово: ') + self.result.name)
            elif kind == 'error':
                self.status.set(tr('Ошибка обработки.'))
                messagebox.showerror(APP_NAME, data, parent=self.top)
            else:
                self.status.set(tr('Отменено.'))
            self._set_buttons()
        self.top.after(100, self._poll)

    def select_video(self):
        self.pause_playback()
        selected = filedialog.askopenfilename(parent=self.top, filetypes=file_types(VIDEO_TYPES))
        if selected:
            self._load_video(Path(selected))

    def _load_video(self, source):
        tools = resolve_video_tools(self.editor.ffmpeg_path.get())
        if not tools:
            messagebox.showerror(APP_NAME, tr('Пара FFmpeg и FFprobe не найдена.'), parent=self.top)
            return
        self.preview_ticket += 1
        self.preview_request = None
        self.status.set(tr('Читаю длительность: ') + source.name)

        def load(_cancel, _notify):
            ffmpeg, ffprobe = tools
            return source, ffmpeg, probe_media(ffprobe, str(source)), frame_at(ffmpeg, source, 0)

        self._start('open', load)

    def import_srt(self):
        self.pause_playback()
        name = filedialog.askopenfilename(parent=self.top, filetypes=((tr('Субтитры'), '*.srt'),))
        if name:
            self.status.set(tr('Загрузка субтитров…'))
            self._start('cues', lambda _cancel, _notify: read_srt(Path(name)))

    def auto_captions(self):
        if self.source is None:
            self.status.set(tr('Сначала выбери видео.'))
            return
        tools = _whisper_tools()
        if tools is None:
            messagebox.showerror(APP_NAME, tr('Для распознавания добавь Whisper и модель ggml-base.bin в tools/whisper.'), parent=self.top)
            return
        vad_model = tools[1].with_name(VAD_MODEL_NAME) if self.speech_only.get() else None
        if vad_model is not None and not vad_model.is_file():
            messagebox.showerror(APP_NAME, tr('Для режима «Только речь» добавь модель {0} в tools/whisper.').format(VAD_MODEL_NAME), parent=self.top)
            return
        language = 'uk' if self.language.get() == 'Українська' else 'ru'
        self._start('cues', lambda cancel, notify: transcribe(self.ffmpeg, self.source, *tools, language, cancel, notify,
                                                             vad_model=vad_model))

    def _load_cues(self, cues):
        self.cues = sorted(cues, key=lambda cue: cue.start)
        self.starts = [cue.start for cue in self.cues]
        self.listing.delete(*self.listing.get_children())
        for index, cue in enumerate(self.cues):
            self.listing.insert('', 'end', iid=str(index), values=(_time_label(cue.start), ' '.join(cue.text.split())))
        self.listing.selection_set('0')
        self.listing.focus('0')
        self.status.set(tr('Загружено фраз: {0}').format(len(self.cues)))
        self.select_cue()

    def _selected(self):
        selection = self.listing.selection()
        return int(selection[0]) if selection else None

    def select_cue(self, _event=None):
        index = self._selected()
        if index is None:
            return
        self.pause_playback()
        cue = self.cues[index]
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', cue.text)
        self.time.set(cue.start / 1000)
        self.request_frame()

    def apply_edit(self):
        index = self._selected()
        if index is None:
            return
        text = self.text.get('1.0', 'end-1c').strip()
        if not text:
            self.status.set(tr('Введи текст фразы.'))
            return
        cue = self.cues[index]
        starts = cue.word_starts if len(cue.text.split()) == len(text.split()) else ()
        self.cues[index] = replace(cue, text=text, word_starts=starts)
        self.listing.set(str(index), 'text', ' '.join(text.split()))
        self.redraw()

    def save_srt(self):
        self.pause_playback()
        if not self.cues:
            self.status.set(tr('Сначала добавь субтитры.'))
            return
        path = filedialog.asksaveasfilename(parent=self.top, defaultextension='.srt',
                                            filetypes=((tr('Субтитры'), '*.srt'),))
        if path:
            try:
                document = '\n\n'.join(f'{index}\n{_time_label(cue.start)} --> {_time_label(cue.end)}\n{cue.text}'
                                        for index, cue in enumerate(self.cues, start=1))
                Path(path).write_text(document + '\n', encoding='utf-8-sig')
                self.status.set(tr('Готово: ') + Path(path).name)
            except OSError as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=self.top)

    def request_frame(self, _value=None):
        self.redraw()
        if not self.source:
            return
        seconds = min(self.time.get(), max(0, self.info.duration - .07))
        self.preview_ticket += 1
        self.preview_request = (self.preview_ticket, self.ffmpeg, self.source, seconds)
        if not self.preview_running:
            self._read_preview()

    def _slider_changed(self, _value):
        if self.playback is None:
            self.request_frame()

    def toggle_playback(self):
        if self.playback is not None:
            self.pause_playback()
            return
        if self.source is None or self.job_cancel is not None:
            return
        start = self.time.get()
        if start >= self.info.duration - .04:
            start = 0
            self.time.set(0)
        self.preview_ticket += 1
        self.preview_request = None
        self.playback = VideoPreview(self.ffmpeg, self.source, start, self.info.duration, self.frame_size,
                                     float(Fraction(self.info.fps)), self.info.has_audio)
        self._set_buttons()
        self._playback_tick(self.playback)

    def pause_playback(self):
        if self.playback is not None:
            self.playback.close()
            self.playback = None
            self._set_buttons()

    def _playback_tick(self, playback):
        if self.playback is not playback:
            return
        if playback.error:
            self.pause_playback()
            self.status.set(playback.error)
            return
        position, frame = playback.poll()
        self.time.set(position)
        if frame is not None:
            self.image = frame
        self.redraw()
        if position >= playback.duration:
            self.pause_playback()
        else:
            self.top.after(20, self._playback_tick, playback)

    def _read_preview(self):
        ticket, ffmpeg, source, seconds = self.preview_request
        self.preview_request = None
        self.preview_running = True

        def read():
            try:
                image = frame_at(ffmpeg, source, seconds)
            except Exception:
                image = None
            self.jobs.put(('frame', (ticket, image)))

        threading.Thread(target=read, daemon=True, name='jetvideo-caption-preview').start()

    def _style(self):
        values = [float(variable.get().replace(',', '.')) for variable in (self.size, self.width, self.x, self.y)]
        size, width, x, y = values
        outline = int(self.outline.get())
        if not (1 <= size <= 12 and 25 <= width <= 100 and 0 <= x <= 100 and 0 <= y <= 100 and 0 <= outline <= 10):
            raise ValueError(tr('Проверь размер, ширину, координаты и обводку субтитров.'))
        return SubtitleStyle(self.font.get(), size, width, x, y, self.selected_color, self.selected_accent,
                             self.highlight.get(), self.background.get(), outline)

    def choose_color(self, accent):
        hex_color = colorchooser.askcolor(self.selected_accent if accent else self.selected_color, parent=self.top)[1]
        if hex_color:
            if accent:
                self.selected_accent = hex_color
            else:
                self.selected_color = hex_color
            self.redraw()

    def redraw(self):
        if self.image is None or self.canvas.winfo_width() < 2 or self.canvas.winfo_height() < 2:
            return
        width, height = self.image.size
        scale = min(self.canvas.winfo_width() / width, self.canvas.winfo_height() / height)
        w, h = max(1, round(width * scale)), max(1, round(height * scale))
        x, y = (self.canvas.winfo_width() - w) // 2, (self.canvas.winfo_height() - h) // 2
        self.video_box = x, y, w, h
        frame_key = self.image, w, h
        if self.frame_cache is None or self.frame_cache[0] is not self.image or self.frame_cache[1:] != (w, h):
            frame = self.image.resize((w, h), Image.Resampling.BILINEAR)
            self.preview_photo = ImageTk.PhotoImage(frame, master=self.top)
            self.canvas.itemconfigure(self.frame_item, image=self.preview_photo)
            self.frame_cache = frame_key
        self.canvas.coords(self.frame_item, x, y)
        time_ms = round(self.time.get() * 1000)
        index = cue_at(self.cues, self.starts, time_ms)
        if index is None and self.cues:
            self.timing.configure(text='')
            self.canvas.itemconfigure(self.caption_item, state='hidden')
            return
        example = 'Її ґаджет знімає відео.' if self.language.get() == 'Українська' else 'Ёлку уже нарядили.'
        cue = self.cues[index] if index is not None else Cue(0, 4000, example)
        try:
            style = self._style()
        except ValueError:
            self.canvas.itemconfigure(self.caption_item, state='hidden')
            return
        active = bisect_right(word_starts(cue), time_ms) - 1 if style.highlight else None
        painter_key = style, self.frame_size
        if self.painter_settings != painter_key:
            self.painter = CaptionPainter(style, self.frame_size)
            self.painter_settings = painter_key
        caption_key = painter_key, cue, active, w, h
        if self.caption_cache != caption_key:
            layout = self.painter.layout(cue)
            picture = self.painter.paint(layout, active)
            sx, sy = w / self.frame_size[0], h / self.frame_size[1]
            picture = picture.resize((max(1, round(picture.width * sx)), max(1, round(picture.height * sy))),
                                     Image.Resampling.LANCZOS)
            self.caption_photo = ImageTk.PhotoImage(picture, master=self.top)
            self.caption_offset = round(layout[0][0] * sx), round(layout[0][1] * sy)
            self.canvas.itemconfigure(self.caption_item, image=self.caption_photo)
            self.caption_cache = caption_key
        self.canvas.coords(self.caption_item, x + self.caption_offset[0], y + self.caption_offset[1])
        self.canvas.itemconfigure(self.caption_item, state='normal')
        self.timing.configure(text=(tr('Подсветка слов приблизительная.') if style.highlight and not cue.word_starts else ''))

    def move_caption(self, event):
        if not self.video_box:
            return
        x, y, w, h = self.video_box
        position = (round(100 * max(0, min(w, event.x - x)) / w, 1),
                    round(100 * max(0, min(h, event.y - y)) / h, 1))
        if self.local_position.get():
            index = cue_at(self.cues, self.starts, round(self.time.get() * 1000))
            if index is not None:
                self.cues[index] = replace(self.cues[index], position=position)
                self.redraw()
        else:
            self.x.set(f'{position[0]:g}')
            self.y.set(f'{position[1]:g}')

    def export(self):
        self.pause_playback()
        if not self.source or not self.cues:
            self.status.set(tr('Выбери видео и добавь субтитры.'))
            return
        try:
            style = self._style()
            step = parse_time(self.keyframes.get()) if self.keyframes.get().strip() else 0
        except ValueError as exc:
            self.status.set(str(exc))
            return
        path = filedialog.asksaveasfilename(parent=self.top, initialdir=str(self.source.parent),
                                            initialfile=self.source.stem + '_subtitles.mp4',
                                            defaultextension='.mp4', filetypes=(('MP4', '*.mp4'),))
        if path:
            cues = tuple(self.cues)
            def render(cancel, notify):
                result = export_subtitled(
                    self.ffmpeg, self.source, Path(path), self.info, self.frame_size, cues, style, step, cancel, notify)
                return (result, step) if result else None

            self._start('export', render)

    def send_to_cut(self):
        if self.result and self.result.is_file() and self.job_cancel is None:
            self.editor.video_files = [str(self.result)]
            self.editor.refresh_video_list()
            self.editor.export_mode.set('cut')
            if self.result_step:
                self.editor.cut_duration.set(f'{self.result_step:g}')
                if self.editor.cut_banners.get() == 'Без баннеров':
                    self.editor.cut_fast.set(True)
            self.editor._on_change()
            self.close()
