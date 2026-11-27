from __future__ import annotations

from collections import Counter
import json
import os
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from app_info import APP_NAME, PRODUCT_NAME, portable_home, resolve_video_tools
from guide_ui import bind_tooltip
from i18n import file_types, localized_combobox, tr
from ui_icons import icon

from video_ad_inserter_core import (
    VideoAdInserter as OriginalInserter, VIDEO_TYPES, BANNER_TYPES,
    parse_time, prepare_insertions,
)

PREFERENCES_DIRECTORY = 'JetVideo Modern Editor'
BG = '#090E1A'
PANEL = '#151E30'
PANEL2 = '#1B2840'
BORDER = '#2B3850'
FG = '#F4F6FF'
MUTED = '#98A8C1'
ACCENT = '#8B80FA'
ACCENT_LIGHT = '#a39bff'
CYAN = '#4BD8D0'
GREEN = '#54D9A7'
RED = '#FB8393'
FONT = 'Segoe UI'
MONO = 'Consolas'
ON_ACCENT = '#101226'


def preference_path() -> Path:
    if portable := portable_home():
        return portable / 'editor-preferences.json'
    if os.name == 'nt':
        home = os.environ.get('APPDATA') or os.environ.get('LOCALAPPDATA')
        base = Path(home) if home else Path.home()
        return base / PREFERENCES_DIRECTORY / 'preferences.json'
    return Path.home() / '.config' / PREFERENCES_DIRECTORY / 'preferences.json'


def load_preferences(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_preferences(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix('.tmp')
    staging.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    staging.replace(path)


CUT_BANNER_MODES = {
    'Без баннеров': 'none',
    'Наложение баннеров': 'overlay',
    'Стоп-кадр с баннерами': 'freeze',
}


class ModernVideoAdInserter(OriginalInserter):
    def __init__(self, root: tk.Tk):
        self.pref_file = preference_path()
        self.loaded_prefs = load_preferences(self.pref_file)
        self._initializing = True
        self.insertions: list[dict] = []
        self.cut_duration = tk.StringVar(master=root, value='00:02:00')
        self.cut_name = tk.StringVar(master=root, value=tr('часть {part}'))
        self.cut_start = tk.StringVar(master=root, value='1')
        self.cut_tail = tk.BooleanVar(master=root, value=True)
        self.cut_fast = tk.BooleanVar(master=root, value=False)
        self.cut_banners = tk.StringVar(master=root, value='Без баннеров')
        self.export_mode = tk.StringVar(master=root, value='cut')
        self.settings_window = None
        self.subtitle_editor = None
        self.tool_checks = queue.Queue()
        super().__init__(root)
        root.configure(bg=BG)
        self._restore_prefs()
        self._initializing = False
        for var in (self.banner_file, self.output_dir, self.insert_at, self.banner_mode,
                    self.fit_mode, self.key_color, self.key_similarity,
                    self.key_blend, self.crf, self.include_banner_audio, self.ffmpeg_path,
                    self.cut_duration, self.cut_name, self.cut_start, self.cut_tail,
                    self.cut_fast, self.cut_banners, self.export_mode):
            var.trace_add('write', self._on_change)
        self._update_mode()

    def _build_ui(self):
        self._setup_style()
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill='both', expand=True)

        header = tk.Frame(shell, bg=BG)
        header.pack(fill='x', padx=22, pady=(10, 8))
        self._label(header, tr('Подготовка видео'), 18, bold=True, bg=BG).pack(side='left')
        self._menu_button(header, tr('Настройки'), (
            (tr('Настройки монтажа'), self.show_settings),
            (tr('Журнал обработки'), self.show_log),
        ), glyph='settings').pack(side='right')
        self.subtitle_btn = self._btn(header, tr('Субтитры'), self.show_subtitles, glyph='captions')
        self.subtitle_btn.pack(side='right', padx=(0, 8))

        body = tk.Frame(shell, bg=BG)
        body.pack(fill='both', expand=True, padx=22)
        content = self._scroll_page(body)
        work = tk.Frame(content, bg=BG)
        work.pack(fill='x', pady=(0, 10))
        self.source_card = self._build_sources(work)
        self.options_card = self._build_export_mode(work)
        self.source_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        self.options_card.grid(row=0, column=1, sticky='nsew')
        work.columnconfigure(0, weight=4, uniform='work')
        work.columnconfigure(1, weight=6, uniform='work')
        stacked = False

        def reflow(event):
            nonlocal stacked
            compact = event.width < 800
            if compact == stacked:
                return
            stacked = compact
            self.source_card.grid_configure(padx=0 if compact else (0, 10), pady=(0, 10) if compact else 0)
            self.options_card.grid_configure(row=int(compact), column=int(not compact))
            work.columnconfigure(0, weight=1 if compact else 4, uniform='' if compact else 'work')
            work.columnconfigure(1, weight=0 if compact else 6, uniform='' if compact else 'work')

        work.bind('<Configure>', reflow)
        self._build_inserts(content)
        self._build_export_footer(shell, body)

        self.log_window = self._dialog(tr('Журнал обработки'), 760, 520)
        self._build_logs(self.log_window)
        self._update_mode()

    def _dialog(self, title, width, height):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.transient(self.root.winfo_toplevel())
        top.title(title)
        top.configure(bg=BG, padx=14, pady=14)
        top.minsize(480, 320)
        top.initial_size = (width, min(height, top.winfo_screenheight() - 120))
        top.geometry(f'{top.initial_size[0]}x{top.initial_size[1]}')
        top.protocol('WM_DELETE_WINDOW', top.withdraw)
        top.bind('<Escape>', lambda _event: top.withdraw())
        return top

    def _show_dialog(self, top):
        self.root.update_idletasks()
        width, height = (top.winfo_width(), top.winfo_height()) if top.winfo_width() > 1 else top.initial_size
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        top.geometry(f'{width}x{height}+{x}+{y}')
        top.deiconify()
        top.lift()
        top.focus_set()

    def show_log(self):
        self._show_dialog(self.log_window)

    def show_subtitles(self):
        if self.busy:
            return
        if self.subtitle_editor is None:
            from subtitle_ui import SubtitleEditor
            self.subtitle_editor = SubtitleEditor(self)
        self.subtitle_editor.show()

    def show_settings(self):
        if self.settings_window is None:
            self.settings_window = self._dialog(tr('Настройки монтажа'), 720, 690)
            self._build_settings(self.settings_window)
        self._update_tools()
        self._show_dialog(self.settings_window)

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Ad.Horizontal.TProgressbar', troughcolor=PANEL2,
                        background=ACCENT, bordercolor=PANEL2, lightcolor=ACCENT,
                        darkcolor=ACCENT, thickness=5)
        style.configure('Ad.TCombobox', padding=7, font=(FONT, 10), foreground=FG,
                        fieldbackground=PANEL2, background=PANEL2,
                        arrowcolor=FG, bordercolor=BORDER)
        style.map('Ad.TCombobox', fieldbackground=[('readonly', PANEL2)],
                  foreground=[('readonly', FG)], background=[('readonly', PANEL2)])
        for name in ('Ad.TSpinbox', 'Ad.TEntry'):
            style.configure(name, fieldbackground=PANEL2, background=PANEL2, foreground=FG,
                            insertcolor=FG, bordercolor=BORDER, arrowcolor=MUTED, padding=4)
        style.configure('Ad.TNotebook', background=PANEL, borderwidth=0, tabmargins=0)
        style.configure('Ad.TNotebook.Tab', background=PANEL2, foreground=MUTED, borderwidth=0,
                        padding=(10, 7), font=(FONT, 10, 'bold'))
        style.map('Ad.TNotebook.Tab', background=[('selected', BORDER), ('active', BORDER)],
                  foreground=[('selected', FG), ('active', FG)])
        style.layout('Ad.Vertical.TScrollbar', [
            ('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
                ('Vertical.Scrollbar.thumb', {'expand': True, 'sticky': 'ns'}),
            ]}),
        ])
        style.configure('Ad.Vertical.TScrollbar', troughcolor=BG, background=BORDER,
                        bordercolor=BG, lightcolor=BORDER, darkcolor=BORDER, gripcount=0, width=8)
        self.root.option_add('*TCombobox*Listbox.background', PANEL2)
        self.root.option_add('*TCombobox*Listbox.foreground', FG)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        style.configure('Ad.Treeview', background=PANEL2, fieldbackground=PANEL2, foreground=FG,
                        rowheight=30, font=(FONT, 10), borderwidth=0)
        style.configure('Ad.Treeview.Heading', background=PANEL, foreground=MUTED,
                        font=(FONT, 10, 'bold'), relief='flat')
        style.map('Ad.Treeview', background=[('selected', ACCENT)], foreground=[('selected', ON_ACCENT)])

    def _label(self, parent, text, size=11, color=FG, bold=False, **kw):
        bg = kw.pop('bg', PANEL)
        return tk.Label(parent, text=text, bg=bg, fg=color,
                        font=(FONT, size, 'bold' if bold else 'normal'), **kw)

    def _btn(self, parent, text, command, kind='normal', *, glyph=None, compact=False):
        bg = ACCENT if kind == 'primary' else PANEL2
        fg = ON_ACCENT if kind == 'primary' else FG
        button = tk.Button(parent, text=text, command=command,
                           bg=bg, fg=fg, activebackground=ACCENT_LIGHT if kind == 'primary' else BORDER,
                           activeforeground=fg, disabledforeground='#37445F' if kind == 'primary' else MUTED,
                           bd=0, relief='flat', highlightthickness=1, highlightbackground=bg,
                           highlightcolor=ACCENT_LIGHT, cursor='hand2',
                           font=(FONT, 10, 'bold' if kind == 'primary' else 'normal'),
                           padx=8 if compact else 12, pady=4 if compact else 7)
        if glyph:
            button.icon = icon(button, glyph, fg, 16)
            button.configure(image=button.icon, compound='left')
        return button

    def _menu_button(self, parent, hint, entries, *, glyph='more'):
        button = tk.Menubutton(parent, bg=PANEL2, fg=FG, activebackground=BORDER, activeforeground=FG,
                                borderwidth=0, highlightthickness=1, highlightbackground=PANEL2,
                                highlightcolor=ACCENT_LIGHT, relief='flat', width=34, height=34,
                                cursor='hand2', takefocus=True)
        button.icon = icon(button, glyph, FG, 16)
        button.configure(image=button.icon)
        menu = tk.Menu(button, tearoff=False, bg=PANEL2, fg=FG,
                       activebackground=ACCENT, activeforeground=ON_ACCENT, font=(FONT, 10))
        button.configure(menu=menu)
        for label, command in entries:
            menu.add_command(label=label, command=command)
        bind_tooltip(button, hint)
        return button

    def _entry(self, parent, var, width=20, **kw):
        return tk.Entry(parent, textvariable=var, width=width, bg=PANEL2, fg=FG,
                        insertbackground=FG, highlightthickness=1, highlightcolor=ACCENT,
                        highlightbackground=BORDER, relief='flat', bd=0, font=(FONT, 10),
                        **kw)

    def _card(self, parent, title=None, subtitle=None, *, glyph=None):
        frame = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        if title:
            hdr = tk.Frame(frame, bg=PANEL)
            hdr.pack(fill='x', padx=18, pady=(11, 6 if subtitle else 8))
            heading = self._label(hdr, title, 12, bold=True)
            if glyph:
                heading.icon = icon(heading, glyph, ACCENT_LIGHT, 19)
                heading.configure(image=heading.icon, compound='left', padx=1)
            heading.pack(anchor='w')
            if subtitle:
                self._label(hdr, subtitle, 9, MUTED, wraplength=560, justify='left').pack(anchor='w', pady=(5, 0))
        return frame

    def _section(self, parent, title, subtitle=None):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill='x', padx=19, pady=(10, 6))
        self._label(row, title, 10, bold=True).pack(anchor='w')
        if subtitle:
            self._label(row, subtitle, 9, MUTED, justify='left').pack(anchor='w', pady=(3, 0))
        return row

    def _path_row(self, parent, title, variable, choose, button_title=None):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill='x', padx=19, pady=7)
        self._label(row, title, 9, MUTED).pack(anchor='w', pady=(0, 6))
        line = tk.Frame(row, bg=PANEL)
        line.pack(fill='x')
        ent = self._entry(line, variable, width=12)
        ent.pack(side='left', fill='x', expand=True, ipady=9)
        self._btn(line, button_title or tr('Обзор…'), choose).pack(side='left', padx=(8, 0))
        return ent

    def _form_row(self, parent, title, var, description='', width=16):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill='x', padx=19, pady=8)
        self._label(row, title, 10, bold=True).pack(anchor='w')
        input_row = tk.Frame(row, bg=PANEL)
        input_row.pack(fill='x', pady=(5, 0))
        ent = self._entry(input_row, var, width=width)
        ent.pack(side='left', ipady=8)
        if description:
            self._label(input_row, description, 9, MUTED, wraplength=420,
                        justify='left').pack(side='left', padx=12)
        return ent

    @staticmethod
    def _scroll_range(scrollbar, first, last):
        if float(first) == 0 and float(last) == 1:
            scrollbar.pack_forget()
        else:
            scrollbar.pack(side='right', fill='y')
        scrollbar.set(first, last)

    def _scroll_page(self, parent):
        canvas = tk.Canvas(parent, bg=BG, width=1, height=1, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview,
                               style='Ad.Vertical.TScrollbar')
        canvas.configure(yscrollcommand=lambda first, last: self._scroll_range(scroll, first, last))
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        content = tk.Frame(canvas, bg=BG)
        window_id = canvas.create_window((0, 0), window=content, anchor='nw')
        content.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window_id, width=e.width))
        host = canvas.winfo_toplevel()

        def scroll_wheel(event):
            if isinstance(event.widget, (tk.Listbox, tk.Text, ttk.Treeview, ttk.Combobox)):
                return
            widget = event.widget
            while widget is not None:
                if widget == canvas:
                    canvas.yview_scroll(-int(event.delta / 120), 'units')
                    return 'break'
                widget = getattr(widget, 'master', None)

        binding = host.bind('<MouseWheel>', scroll_wheel, add='+')
        canvas.bind('<Destroy>', lambda _event: host.unbind('<MouseWheel>', binding), add='+')
        return content

    def _build_sources(self, page):
        uploads = self._card(page, tr('Исходные видео'), glyph='video')
        buttons = tk.Frame(uploads, bg=PANEL)
        buttons.pack(fill='x', padx=17, pady=(0, 8))
        self._btn(buttons, tr('+ Добавить видео').lstrip('+ '), self.choose_videos,
                  glyph='plus').pack(side='left')
        self._menu_button(buttons, tr('Действия со списком'), (
            (tr('Из публикаций'), self.select_from_ready),
            (tr('Убрать выбранные'), self.remove_selected),
            (tr('Очистить список'), self.clear_videos),
        )).pack(side='right')
        wrapper = tk.Frame(uploads, bg=PANEL2)
        wrapper.pack(fill='both', expand=True, padx=17)
        self.video_list = tk.Listbox(wrapper, height=5, width=1, bg=PANEL2, fg=FG,
                                     selectbackground=ACCENT, selectforeground=ON_ACCENT,
                                     selectmode='extended', exportselection=False, relief='flat',
                                     highlightthickness=1, highlightbackground=PANEL2,
                                     highlightcolor=ACCENT_LIGHT, font=(FONT, 10))
        self.video_list.pack(side='left', fill='both', expand=True, padx=(6, 3), pady=4)
        scrollbar = ttk.Scrollbar(wrapper, command=self.video_list.yview, style='Ad.Vertical.TScrollbar')
        scrollbar.pack(side='right', fill='y')
        self.video_list.configure(yscrollcommand=lambda first, last: self._scroll_range(scrollbar, first, last))
        self.empty_videos = self._label(wrapper, tr('Видео не выбраны'), 10, MUTED, bg=PANEL2)
        self.empty_videos.place(relx=.5, rely=.5, anchor='center')
        self.video_count = self._label(uploads, tr('Выбрано видео: {0}').format(0), 9, MUTED)
        self.video_count.pack(anchor='w', padx=17, pady=(7, 12))
        return uploads

    def _build_export_mode(self, page):
        card = self._card(page, tr('Что сделать с видео'), glyph='edit')
        modes = tk.Frame(card, bg=PANEL)
        modes.pack(fill='x', padx=17, pady=(0, 7))
        self.mode_buttons = []
        for value, label, glyph in (('cut', tr('Нарезать на части'), 'scissors'),
                                    ('whole', tr('Целое видео с баннерами'), 'image')):
            button = tk.Radiobutton(modes, text=label, value=value, variable=self.export_mode,
                                    indicatoron=False, bg=PANEL2, fg=FG, selectcolor=BORDER,
                                    activebackground=BORDER, activeforeground=FG, font=(FONT, 9, 'bold'),
                                    highlightthickness=1, highlightbackground=BORDER,
                                    highlightcolor=ACCENT_LIGHT, relief='flat', overrelief='flat',
                                    borderwidth=0, padx=8, pady=6, cursor='hand2')
            button.icon = icon(button, glyph, ACCENT_LIGHT, 16)
            button.configure(image=button.icon, compound='left')
            button.pack(side='left', padx=(0, 6))
            self.mode_buttons.append(button)
        self.cut_fields = tk.Frame(card, bg=PANEL)
        fields = tk.Frame(self.cut_fields, bg=PANEL)
        fields.pack(fill='x')
        for col, (label, variable, width) in enumerate((
            (tr('Длина части'), self.cut_duration, 9),
            (tr('Шаблон имени'), self.cut_name, 17),
            (tr('Начальный номер'), self.cut_start, 9),
        )):
            field = tk.Frame(fields, bg=PANEL)
            field.grid(row=0, column=col, sticky='ew', padx=(0, 12))
            self._label(field, label, 9, MUTED).pack(anchor='w')
            self._entry(field, variable, width=width).pack(fill='x', pady=3, ipady=5)
        fields.columnconfigure(1, weight=1)
        self.cut_name_preview = self._label(self.cut_fields, '', 9, MUTED, wraplength=380, justify='left')
        self.cut_name_preview.pack(anchor='w', pady=(3, 3))
        self.cut_fields.bind('<Configure>', lambda event: self.cut_name_preview.configure(wraplength=max(1, event.width)))
        tk.Checkbutton(self.cut_fields, text=tr('Сохранять последний короткий фрагмент'), variable=self.cut_tail,
                       bg=PANEL, fg=FG, selectcolor=PANEL2, activebackground=PANEL,
                       activeforeground=FG, font=(FONT, 9)).pack(anchor='w')
        self.cut_fast_toggle = tk.Checkbutton(
            self.cut_fields, text=tr('Быстро, по ключевым кадрам (длина приблизительная)'), variable=self.cut_fast,
            bg=PANEL, fg=FG, selectcolor=PANEL2, activebackground=PANEL, activeforeground=FG, font=(FONT, 9),
        )
        for var in (self.cut_name, self.cut_start):
            var.trace_add('write', lambda *_: self._update_cut_preview())
        self._update_cut_preview()
        return card

    def _build_export_footer(self, shell, body):
        footer = tk.Frame(shell, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        footer.pack(side='bottom', fill='x', before=body, padx=22, pady=(8, 12))
        output = tk.Frame(footer, bg=PANEL)
        output.pack(fill='x', padx=15, pady=(9, 6))
        self._label(output, tr('Папка результата'), 9, MUTED).pack(side='left', padx=(0, 8))
        self._entry(output, self.output_dir, width=8).pack(side='left', fill='x', expand=True, ipady=5)
        self._btn(output, tr('Выбрать…'), self.choose_output, glyph='folder',
                  compact=True).pack(side='left', padx=(6, 0))
        actions = tk.Frame(footer, bg=PANEL)
        actions.pack(fill='x', padx=15, pady=(0, 7))
        self.start_btn = self._btn(actions, tr('Начать нарезку'), self.start_processing, 'primary', glyph='scissors')
        self.whole_icon = icon(self.start_btn, 'video', ON_ACCENT, 16)
        self.start_btn.pack(side='left')
        self.cancel_btn = self._btn(actions, tr('Отмена'), self.cancel_processing)
        self._btn(actions, tr('Открыть папку'), self.open_output, glyph='folder').pack(side='right')
        self.send_btn = self._btn(actions, tr('Передать в публикации'), self.send_to_publisher, glyph='arrow_right')
        self.send_btn.pack(side='right', padx=8)
        self.progress = ttk.Progressbar(footer, mode='determinate', maximum=100,
                                        style='Ad.Horizontal.TProgressbar')
        self.status.set(tr('Добавь исходное видео, чтобы начать.'))
        self.footer_status = tk.Label(footer, textvariable=self.status, bg=PANEL, fg=MUTED,
                                      font=(FONT, 9), anchor='w', wraplength=800, justify='left')
        self.footer_status.pack(fill='x', padx=15, pady=(0, 7))

    @staticmethod
    def _display_time(value):
        try:
            seconds = parse_time(str(value))
        except ValueError:
            return str(value)
        total = int(seconds)
        tail = f'.{int(round((seconds-total)*1000)):03d}' if seconds != total else ''
        return f'{total//3600:02d}:{(total//60)%60:02d}:{total%60:02d}{tail}'

    def _update_cut_preview(self):
        if not hasattr(self, 'cut_name_preview'):
            return
        from video_splitter import output_name
        try:
            number = int(self.cut_start.get())
            if number < 1:
                raise ValueError(tr('Номер должен быть больше нуля.'))
            source = self.video_files[0] if self.video_files else tr('Видео.mp4')
            names = [output_name(self.cut_name.get(), number+i, source) for i in range(3)]
            self.cut_name_preview.configure(text=tr('Например: ') + '  ·  '.join(names), fg=MUTED)
        except (ValueError, OverflowError) as exc:
            self.cut_name_preview.configure(text=str(exc), fg=RED)

    def refresh_video_list(self):
        self.video_list.delete(0, 'end')
        sources = list(map(Path, self.video_files))
        names = Counter(source.name.casefold() for source in sources)
        for source in sources:
            name = source.name
            if names[name.casefold()] > 1:
                name = f'{name}   ·   {source.parent}'
            self.video_list.insert('end', name)
        self.video_count.configure(text=tr('Выбрано видео: {0}').format(len(self.video_files)))
        if self.video_files:
            self.empty_videos.place_forget()
        else:
            self.empty_videos.place(relx=.5, rely=.5, anchor='center')
        self._update_cut_preview()
        if not self.busy:
            self.status.set(tr('Можно выбрать режим и запустить обработку.') if self.video_files
                            else tr('Добавь исходное видео, чтобы начать.'))

    def _update_mode(self, *_args):
        mode = self.export_mode.get()
        cutting = mode == 'cut'
        for button in self.mode_buttons:
            selected = button.cget('value') == mode
            button.configure(highlightbackground=ACCENT if selected else BORDER, fg=FG if selected else MUTED)
        self.cut_fields.pack_forget()
        self.cut_banner_controls.pack_forget()
        if cutting:
            self.cut_fields.pack(fill='x', padx=18, pady=(0, 6))
            self.cut_banner_controls.pack(fill='x', padx=18, pady=(0, 10))
        if cutting and self.cut_banners.get() == 'Без баннеров':
            self.cut_fast_toggle.pack(anchor='w')
        else:
            self.cut_fast_toggle.pack_forget()
        if not cutting or self.cut_banners.get() != 'Без баннеров':
            self.banner_card.pack(fill='x', pady=(0, 9))
        else:
            self.banner_card.pack_forget()
        self.banner_tip.configure(text=(
            tr('Время считается от начала каждой части. Стоп-кадр увеличивает длину результата.')
            if cutting else tr('Время считается от начала исходного видео. Стоп-кадр увеличивает его длину.')
        ))
        self.insertion_tree.heading('time', text=tr('От начала части') if cutting else tr('От начала видео'))
        self.start_btn.configure(text=tr('Начать нарезку') if cutting else tr('Создать видео'),
                                 image=self.start_btn.icon if cutting else self.whole_icon)
        self._refresh_insertions()
        self._update_badges()

    def start_processing(self):
        if self.busy:
            return
        from video_splitter import run_split, validate_options
        cutting = self.export_mode.get() == 'cut'
        mode = CUT_BANNER_MODES[self.cut_banners.get()] if cutting else None
        fast_split = mode == 'none' and self.cut_fast.get()
        try:
            if not self.video_files:
                raise ValueError(tr('Добавь хотя бы одно исходное видео.'))
            output = self.output_dir.get().strip()
            if not output:
                raise ValueError(tr('Выбери папку результата.'))
            tools = resolve_video_tools(self.ffmpeg_path.get())
            if tools is None:
                self.show_settings()
                raise ValueError(tr('FFmpeg и FFprobe не найдены. Укажи путь к паре из одной сборки в настройках.'))
            ffmpeg, ffprobe = tools
            crf = 18 if fast_split else int(self.crf.get())
            if not 15 <= crf <= 28:
                raise ValueError(tr('Качество H.264 (CRF) должно быть от 15 до 28.'))

            entries = []
            color, similarity, blend = '0x0000FF', .25, .08
            if mode != 'none':
                if not self.insertions:
                    raise ValueError(tr('Добавь хотя бы один баннер в блоке «Баннеры».'))
                entries = prepare_insertions(self.insertions)
                if not cutting and len({entry.get('kind', 'freeze') for entry in entries}) > 1:
                    raise ValueError(tr('Для целого видео выбери один режим для всех баннеров: наложение или стоп-кадр.'))
                background_modes = {entry.get('mode', 'Общие настройки') for entry in entries}
                if 'Общие настройки' in background_modes:
                    background_modes.add(self.banner_mode.get())
                if 'Синий фон MP4 (удалить фон)' in background_modes:
                    color = self.key_color.get().strip()
                    similarity, blend = float(self.key_similarity.get()), float(self.key_blend.get())
                    if not (0 <= similarity <= 1 and 0 <= blend <= 1):
                        raise ValueError(tr('Параметры хромакея должны быть от 0 до 1.'))
                    if not re.fullmatch(r'0x[0-9a-fA-F]{6}', color):
                        raise ValueError(tr('Цвет хромакея укажи в формате 0xRRGGBB.'))

            options = dict(videos=list(self.video_files), insertions=entries, output=output,
                           ffmpeg=ffmpeg, ffprobe=ffprobe, crf=crf, key_color=color,
                           key_similarity=similarity, key_blend=blend,
                           banner_mode=self.banner_mode.get(), fit_mode=self.fit_mode.get(),
                           include_banner_audio=self.include_banner_audio.get())
            if cutting:
                options.update(clip_duration=self.cut_duration.get(), name_template=self.cut_name.get(),
                               start_part=self.cut_start.get(), keep_tail=self.cut_tail.get(),
                               split_banner_mode=mode, split_fast=fast_split)
                seconds, _ = validate_options(options)
                if any(entry['time'] >= seconds for entry in entries):
                    raise ValueError(tr('Время появления баннера должно быть меньше длины части.'))
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            messagebox.showwarning(tr('Монтаж и нарезка'), str(exc), parent=self.root)
            return

        self.busy = True
        self.cancel_event = threading.Event()
        self.rendered_files = []
        self.progress.configure(value=0, maximum=100 if cutting else len(self.video_files) * 100)
        self.status.set(tr('Подготовка нарезки…') if cutting else tr('Подготовка баннеров…'))
        self.log_message(tr('Нарезка по {0:g} с. {1}.').format(seconds, tr(self.cut_banners.get())) if cutting
                         else tr('Обработка целых видео. Баннеров на видео: {0}.').format(len(entries)))
        self._update_badges()
        target, args = (run_split, (self, options)) if cutting else (self.worker, (options,))
        self.processing_thread = threading.Thread(target=target, args=args, daemon=True, name='jojo-video')
        self.processing_thread.start()

    def _build_inserts(self, page):
        self.banner_card = self._card(page, tr('Баннеры'), glyph='image')
        self.cut_banner_controls = tk.Frame(self.options_card, bg=PANEL)
        self._label(self.cut_banner_controls, tr('Баннеры'), 9, MUTED).pack(side='left', padx=(0, 9))
        self.cut_banner_menu = localized_combobox(self.cut_banner_controls, self.cut_banners,
                                                   CUT_BANNER_MODES, width=25, style='Ad.TCombobox')
        self.cut_banner_menu.pack(side='left')
        self.banner_details = tk.Frame(self.banner_card, bg=PANEL)
        self.banner_details.pack(fill='x')
        self.banner_tip = self._label(self.banner_details, '', 9, MUTED)
        self.banner_tip.pack(anchor='w', padx=18, pady=(0, 6))
        row = tk.Frame(self.banner_details, bg=PANEL)
        row.pack(fill='x', padx=18, pady=(0, 7))
        self._btn(row, tr('Добавить баннер'), self.add_insertion, glyph='plus').pack(side='left')
        self._btn(row, tr('Изменить'), self.edit_insertion, glyph='edit').pack(side='left', padx=6)
        self._menu_button(row, tr('Действия с баннерами'), (
            (tr('Дублировать'), self.duplicate_insertion),
            (tr('Убрать'), self.remove_insertion),
            (tr('Сохранить шаблон'), self.export_template),
            (tr('Загрузить шаблон'), self.import_template),
        )).pack(side='right')
        table_wrap = tk.Frame(self.banner_details, bg=PANEL2)
        table_wrap.pack(fill='x', padx=18, pady=(0, 12))
        self.insertion_tree = ttk.Treeview(table_wrap, columns=('time', 'file', 'mode'),
                                           show='headings', selectmode='browse', height=3,
                                           style='Ad.Treeview')
        for key, title, width, stretch in (
            ('time', tr('От начала видео'), 145, False),
            ('file', tr('Баннер'), 280, True),
            ('mode', tr('Режим и фон'), 245, True),
        ):
            self.insertion_tree.heading(key, text=title)
            self.insertion_tree.column(key, width=width, minwidth=60, stretch=stretch)
        self.insertion_tree.pack(side='left', fill='x', expand=True)
        bar = ttk.Scrollbar(table_wrap, orient='vertical', command=self.insertion_tree.yview,
                            style='Ad.Vertical.TScrollbar')
        bar.pack(side='right', fill='y')
        self.insertion_tree.configure(yscrollcommand=bar.set)
        self.insertion_tree.bind('<Double-1>', lambda _event: self.edit_insertion())
        for var in (self.export_mode, self.cut_banners):
            var.trace_add('write', self._update_mode)

    def _refresh_insertions(self):
        self.insertions.sort(key=lambda entry: parse_time(str(entry['time'])))
        selected = self.insertion_tree.selection()
        self.insertion_tree.delete(*self.insertion_tree.get_children())
        cutting = self.export_mode.get() == 'cut'
        for index, entry in enumerate(self.insertions):
            kind = CUT_BANNER_MODES[self.cut_banners.get()] if cutting else entry.get('kind', 'freeze')
            mode = 'Наложение' if kind == 'overlay' else 'Стоп-кадр'
            background = entry.get('mode', 'Общие настройки')
            self.insertion_tree.insert('', 'end', iid=str(index), values=(
                self._display_time(entry['time']), Path(entry['banner']).name, f'{tr(mode)} · {tr(background)}'))
        if selected and self.insertion_tree.exists(selected[0]):
            self.insertion_tree.selection_set(selected[0])

    def _selected_insertion_index(self):
        ids = self.insertion_tree.selection()
        if not ids:
            messagebox.showinfo(APP_NAME, tr('Выбери вставку в таблице.'))
            return None
        return int(ids[0])

    def _edit_insertion_dialog(self, index=None):
        if self.busy:
            messagebox.showwarning(APP_NAME, tr('Дождись окончания текущей обработки.'))
            return
        current = self.insertions[index] if index is not None else None
        cutting = self.export_mode.get() == 'cut'
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.title(tr('Редактировать вставку') if current else tr('Добавить баннер'))
        top.configure(bg=BG)
        self.root.update_idletasks()
        width, height = 700, min(760, top.winfo_screenheight() - 120)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = max(20, self.root.winfo_rooty())
        top.geometry(f'{width}x{height}+{x}+{y}')
        top.minsize(540, 430)
        top.transient(self.root.winfo_toplevel())
        time_var = tk.StringVar(value=str(current['time']) if current else '00:00:20')
        file_var = tk.StringVar(value=current['banner'] if current else (self.insertions[-1]['banner'] if self.insertions else self.banner_file.get()))
        mode_var = tk.StringVar(value=current.get('mode', 'Общие настройки') if current else 'Общие настройки')
        body = tk.Frame(top, bg=BG)
        body.pack(fill='both', expand=True, padx=12, pady=(12, 0))
        box = self._card(self._scroll_page(body))
        box.pack(fill='x')
        timer_label = tr('От начала каждой части') if cutting else tr('От начала видео')
        self._label(box, timer_label, 10, MUTED).pack(anchor='w', padx=19, pady=(14, 0))
        self._entry(box, time_var, width=22).pack(anchor='w', padx=19, pady=(4, 11), ipady=8)
        self._label(box, tr('Рекламный файл'), 9, MUTED).pack(anchor='w', padx=19)
        file_line = tk.Frame(box, bg=PANEL)
        file_line.pack(fill='x', padx=19, pady=(4, 10))
        self._entry(file_line, file_var).pack(side='left', fill='x', expand=True, ipady=8)
        def browse():
            file = filedialog.askopenfilename(parent=top, title=tr('Выбери рекламный ролик'), filetypes=file_types(BANNER_TYPES))
            if file:
                file_var.set(file)
        self._btn(file_line, tr('Обзор…'), browse).pack(side='left', padx=(8, 0))
        self._label(box, tr('Удаление фона для этой вставки'), 9, MUTED).pack(anchor='w', padx=19)
        localized_combobox(box, mode_var, ['Общие настройки', 'auto', 'Прозрачный WebM (alpha)',
                           'Синий фон MP4 (удалить фон)', 'Без удаления фона'],
                           style='Ad.TCombobox').pack(fill='x', padx=19, pady=(5, 12))
        advanced = tk.Frame(box, bg=PANEL)
        advanced.pack(fill='x', padx=19)
        kind_var = tk.StringVar(value='Наложение' if (current or {}).get('kind', 'overlay') == 'overlay' else 'Стоп-кадр')
        if not cutting:
            self._label(advanced, tr('Режим обработки'), 9, MUTED).grid(row=0, column=0, sticky='w')
            localized_combobox(advanced, kind_var, ['Наложение', 'Стоп-кадр'],
                               style='Ad.TCombobox').grid(row=0, column=1, sticky='ew', pady=5)
        layout_vars = {}
        for row, (key, label, default) in enumerate([
            ('x', tr('X, % кадра'), 0), ('y', tr('Y, % кадра'), 0),
            ('width', tr('Ширина, %'), 100), ('height', tr('Высота, %'), 100),
            ('opacity', tr('Непрозрачность, 0–1'), 1), ('duration', tr('Длительность, с (пусто = весь баннер)'), '')], start=1):
            var = tk.StringVar(value=str((current or {}).get(key, default)))
            layout_vars[key] = var
            self._label(advanced, label, 9, MUTED).grid(row=row, column=0, sticky='w', pady=3)
            self._entry(advanced, var, width=18).grid(row=row, column=1, sticky='ew', pady=3, ipady=3)
        advanced.columnconfigure(1, weight=1)
        preview = tk.Canvas(box, height=175, bg=BG, highlightthickness=0)
        preview.pack(fill='x', padx=19, pady=8)
        def redraw(*_):
            preview.delete('all')
            px, py, pw, ph = 280, 4, 92, 164
            preview.create_rectangle(px, py, px+pw, py+ph, outline=MUTED, fill=PANEL2)
            try:
                vx, vy, vw, vh = (float(layout_vars[k].get()) for k in ('x', 'y', 'width', 'height'))
                preview.create_rectangle(px+pw*vx/100, py+ph*vy/100,
                                         px+pw*(vx+vw)/100, py+ph*(vy+vh)/100,
                                         outline=CYAN, fill=ACCENT, stipple='gray50')
                preview.create_text(125, 70, text=tr('Схема положения 9:16\nX/Y — от левого верхнего угла\nРазмер — область вписывания'), fill=MUTED, font=(FONT, 9))
            except ValueError:
                pass
        for var in layout_vars.values():
            var.trace_add('write', redraw)
        redraw()
        buttons = tk.Frame(top, bg=BG)
        buttons.pack(fill='x', padx=14, pady=12)
        def accept():
            try:
                seconds = parse_time(time_var.get())
                path = file_var.get().strip()
                if not Path(path).is_file():
                    raise ValueError(tr('Выбранный рекламный файл не найден.'))
                if index is None and len(self.insertions) >= 40:
                    raise ValueError(tr('Максимум 40 рекламных вставок.'))
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=top)
                return
            item = {'time': time_var.get().strip(), 'banner': path, 'mode': mode_var.get(),
                    'kind': 'overlay' if kind_var.get() == 'Наложение' else 'freeze',
                    **{key: var.get().strip() for key, var in layout_vars.items()}}
            try:
                prepare_insertions([item])
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=top)
                return
            if index is None:
                self.insertions.append(item)
            else:
                self.insertions[index] = item
            self.banner_file.set(path)
            self.insert_at.set(str(seconds))
            self._refresh_insertions()
            self._on_change()
            top.destroy()
        self._btn(buttons, tr('Сохранить вставку'), accept, 'primary').pack(side='left')
        self._btn(buttons, tr('Отмена'), top.destroy).pack(side='left', padx=9)
        top.bind('<Escape>', lambda _e: top.destroy())
        top.deiconify()
        top.grab_set()
        top.focus_set()

    def add_insertion(self):
        self._edit_insertion_dialog()

    def export_template(self):
        filename = filedialog.asksaveasfilename(parent=self.root, title=tr('Сохранить шаблон баннеров'),
                    defaultextension='.json', filetypes=[(tr('Шаблон'), '*.json')])
        if filename:
            try:
                save_preferences(Path(filename), {'version': 1, 'insertions': self.insertions})
            except OSError as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=self.root)

    def import_template(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(parent=self.root, title=tr('Загрузить шаблон'), filetypes=[(tr('Шаблон'), '*.json')])
        if filename:
            try:
                from video_ad_inserter_core import prepare_insertions
                data = json.loads(Path(filename).read_text('utf-8-sig'))
                self.insertions = prepare_insertions(data['insertions'])
                self._refresh_insertions()
                self._on_change()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                messagebox.showerror(APP_NAME, tr('Не удалось загрузить шаблон: {0}').format(exc), parent=self.root)

    def edit_insertion(self):
        index = self._selected_insertion_index()
        if index is not None:
            self._edit_insertion_dialog(index)

    def duplicate_insertion(self):
        if self.busy:
            return
        index = self._selected_insertion_index()
        if index is not None:
            if len(self.insertions) >= 40:
                messagebox.showwarning(APP_NAME, tr('Максимум 40 рекламных вставок.'))
                return
            self.insertions.insert(index+1, dict(self.insertions[index]))
            self._refresh_insertions()
            self._on_change()

    def remove_insertion(self):
        if self.busy:
            return
        index = self._selected_insertion_index()
        if index is not None and messagebox.askyesno(APP_NAME, tr('Удалить выбранную вставку из плана?')):
            del self.insertions[index]
            self._refresh_insertions()
            self._on_change()

    def _build_settings(self, page):
        content = self._scroll_page(page)
        ff = self._card(content, tr('FFmpeg и FFprobe'),
                        tr('Программа ищет инструменты рядом с EXE и в PATH. При необходимости можно выбрать другую сборку.'))
        ff.pack(fill='x', pady=(0, 10))
        self._path_row(ff, tr('Свой путь к FFmpeg'), self.ffmpeg_path, self.choose_ffmpeg, tr('Выбрать…'))
        ff_actions = tk.Frame(ff, bg=PANEL)
        ff_actions.pack(fill='x', padx=18, pady=(0, 7))
        self._btn(ff_actions, tr('Автоматически'), lambda: self.ffmpeg_path.set('')).pack(side='left')
        self._btn(ff_actions, tr('Проверить FFmpeg'), self.check_ffmpeg).pack(side='left', padx=7)
        self.ff_status = self._label(ff, '', 9, MUTED, wraplength=590, justify='left')
        self.ff_status.pack(anchor='w', padx=18, pady=(0, 13))
        settings = self._card(content, tr('Качество и баннеры'))
        settings.pack(fill='x', pady=(0, 12))
        self._section(settings, tr('Тип баннера'))
        self.mode_combo = localized_combobox(settings, self.banner_mode,
                                             ['auto', 'Прозрачный WebM (alpha)',
                                              'Синий фон MP4 (удалить фон)', 'Без удаления фона'],
                                             style='Ad.TCombobox')
        self.mode_combo.pack(fill='x', padx=19, pady=(1, 9))
        self._label(settings, tr('Режим по умолчанию для вставок с выбором «Общие настройки». Auto распознаёт WebM.'),
                    9, MUTED, wraplength=670, justify='left').pack(anchor='w', padx=19)
        self._section(settings, tr('Расположение рекламного баннера'))
        fit_combo = localized_combobox(settings, self.fit_mode,
                                       ['Вписать баннер (без искажения)', 'Заполнить кадр с обрезкой',
                                        'Растянуть на весь кадр'], style='Ad.TCombobox')
        fit_combo.pack(fill='x', padx=19, pady=(2, 9))
        self._form_row(settings, tr('Качество H.264 (CRF)'), self.crf,
                       tr('18 — высокое качество; 20–23 — меньший размер'), width=9)
        check = tk.Checkbutton(settings, text=tr('Воспроизводить звук баннера при стоп-кадре'),
                               variable=self.include_banner_audio, bg=PANEL, fg=FG,
                               selectcolor=PANEL2, activebackground=PANEL, activeforeground=FG,
                               font=(FONT, 10), relief='flat', anchor='w')
        check.pack(anchor='w', padx=16, pady=(6, 17))
        chroma = self._card(content, tr('Хромакей'), tr('Настройки удаления синего фона MP4 (для других режимов не используются).'))
        chroma.pack(fill='x', pady=(0, 15))
        self._form_row(chroma, tr('Цвет фона'), self.key_color, tr('Например 0x0000FF'), width=15)
        self._form_row(chroma, tr('Похожесть'), self.key_similarity, tr('0–1, по умолчанию 0.25'), width=15)
        self._form_row(chroma, tr('Смешивание краёв'), self.key_blend, tr('0–1, по умолчанию 0.08'), width=15)
        self._label(chroma, tr('Во время рекламы исходная дорожка ставится на паузу, затем продолжается.'),
                    9, CYAN).pack(anchor='w', padx=19, pady=(5, 16))

    def _build_logs(self, page):
        frame = self._card(page, tr('История обработки'))
        frame.pack(fill='both', expand=True)
        actions = tk.Frame(frame, bg=PANEL)
        actions.pack(fill='x', padx=18, pady=(0, 12))
        self._btn(actions, tr('Скопировать журнал'), self.copy_log).pack(side='left')
        self._btn(actions, tr('Очистить журнал'), self.clear_log).pack(side='left', padx=8)
        self._btn(actions, tr('Закрыть'), page.withdraw).pack(side='right')
        wrap = tk.Frame(frame, bg=PANEL2)
        wrap.pack(fill='both', expand=True, padx=18, pady=(0, 18))
        self.log = tk.Text(wrap, height=20, wrap='word', state='disabled', bg=PANEL2,
                           fg=FG, insertbackground=FG, relief='flat', bd=0,
                           font=(MONO, 10), padx=13, pady=12)
        self.log.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(wrap, orient='vertical', command=self.log.yview,
                            style='Ad.Vertical.TScrollbar')
        bar.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=bar.set)

    def _update_badges(self):
        ready = not self.busy and bool(getattr(self, 'rendered_files', []))
        state = 'disabled' if self.busy else 'normal'
        if getattr(self, '_button_state', None) == (state, ready):
            return
        self._button_state = (state, ready)
        self.start_btn.configure(state=state)
        self.subtitle_btn.configure(state=state)
        for button in self.mode_buttons:
            button.configure(state=state)
        self.cut_banner_menu.configure(state='disabled' if self.busy else 'readonly')
        self.cut_fast_toggle.configure(state=state)
        if self.busy:
            self.cancel_btn.pack(side='left', after=self.start_btn, padx=8)
            self.progress.pack(fill='x', padx=15, pady=(0, 3), before=self.footer_status)
        else:
            self.cancel_btn.pack_forget()
            self.progress.pack_forget()
        self.send_btn.configure(state='normal' if ready else 'disabled')

    def _update_tools(self, *_args):
        if hasattr(self, 'ff_status'):
            tools = resolve_video_tools(self.ffmpeg_path.get())
            self.ff_status.configure(text=tools[0] if tools else tr('Пара FFmpeg и FFprobe не найдена.'),
                                     fg=GREEN if tools else RED)

    def _restore_prefs(self):
        prefs = self.loaded_prefs
        for name, variable in [('cut_duration', self.cut_duration), ('cut_name', self.cut_name),
                               ('cut_start', self.cut_start)]:
            if isinstance(prefs.get(name), str):
                variable.set(prefs[name])
        cut_banners = prefs.get('cut_banners')
        if isinstance(cut_banners, str) and cut_banners in CUT_BANNER_MODES:
            self.cut_banners.set(cut_banners)
        if prefs.get('export_mode') in ('cut', 'whole'):
            self.export_mode.set(prefs['export_mode'])
        if isinstance(prefs.get('cut_tail'), bool):
            self.cut_tail.set(prefs['cut_tail'])
        if isinstance(prefs.get('cut_fast'), bool):
            self.cut_fast.set(prefs['cut_fast'])
        for name in ('banner_file', 'output_dir', 'insert_at', 'banner_mode', 'fit_mode',
                     'key_color', 'key_similarity', 'key_blend', 'crf'):
            value = prefs.get(name)
            if isinstance(value, str):
                getattr(self, name).set(value)
        if isinstance(prefs.get('include_banner_audio'), bool):
            self.include_banner_audio.set(prefs['include_banner_audio'])
        saved_ffmpeg = prefs.get('ffmpeg_path', '')
        self.ffmpeg_path.set(saved_ffmpeg if isinstance(saved_ffmpeg, str) and resolve_video_tools(saved_ffmpeg) else '')
        self.ffmpeg_path.trace_add('write', self._update_tools)
        saved = prefs.get('insertions')
        if isinstance(saved, list):
            self.insertions = []
            for e in saved[:40]:
                if not isinstance(e, dict) or not isinstance(e.get('banner'), str):
                    continue
                try:
                    parse_time(str(e.get('time', '')))
                except ValueError:
                    continue
                self.insertions.append({**e, 'time': str(e['time'])})
        elif prefs.get('banner_file'):
            self.insertions = [{'banner': prefs['banner_file'],
                                'time': prefs.get('insert_at', '00:00:20'), 'mode': 'Общие настройки'}]
        self._refresh_insertions()
        videos = prefs.get('video_files', [])
        if isinstance(videos, list):
            self.video_files = [p for p in videos if isinstance(p, str) and Path(p).is_file()][:500]
            self.refresh_video_list()

    def _get_prefs(self):
        opts = {name: getattr(self, name).get() for name in
                ('banner_file', 'output_dir', 'insert_at', 'banner_mode', 'fit_mode',
                 'key_color', 'key_similarity', 'key_blend', 'crf',
                 'include_banner_audio', 'ffmpeg_path')}
        opts['video_files'] = self.video_files[:500]
        opts['insertions'] = [dict(item) for item in self.insertions]
        opts.update(cut_duration=self.cut_duration.get(), cut_name=self.cut_name.get(),
                    cut_start=self.cut_start.get(), cut_tail=self.cut_tail.get(), cut_banners=self.cut_banners.get(),
                    cut_fast=self.cut_fast.get(), export_mode=self.export_mode.get())
        return opts

    def _on_change(self, *_args):
        if not self._initializing:
            try:
                save_preferences(self.pref_file, self._get_prefs())
            except OSError as e:
                self.status.set(tr('Не удалось сохранить настройки: {0}').format(e))

    def select_from_ready(self):
        if self.busy:
            return
        callback = getattr(self, 'on_select_from_ready', None)
        if callback:
            callback()
        else:
            self.choose_videos()

    def send_to_publisher(self):
        callback = getattr(self, 'on_send_to_publisher', None)
        if callback is None:
            messagebox.showwarning(APP_NAME, tr('Передача файлов недоступна в самостоятельном режиме.'), parent=self.root)
            return
        if self.busy:
            messagebox.showwarning(tr('Монтаж выполняется'), tr('Дождись окончания обработки.'), parent=self.root)
            return
        outputs = list(getattr(self, 'rendered_files', []))
        if not outputs:
            messagebox.showinfo(tr('Готовые видео'), tr('Сначала обработай ролики. В JetVideo можно передать только результаты последнего монтажа.'), parent=self.root)
            return
        callback(outputs)

    def save_on_exit(self) -> bool:
        if self.busy:
            if not messagebox.askyesno(APP_NAME, tr('Обработка ещё идёт. Закрытие прервёт кодирование. Выйти?'), parent=self.root):
                return False
            self.cancel_processing()
            return False
        try:
            save_preferences(self.pref_file, self._get_prefs())
        except OSError as exc:
            messagebox.showerror(APP_NAME, tr('Не удалось сохранить настройки: {0}').format(exc), parent=self.root)
            return False
        return True

    def choose_videos(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title=tr('Выбери исходные видео'), filetypes=file_types(VIDEO_TYPES))
        if paths:
            self.video_files = list(dict.fromkeys([*self.video_files, *paths]))
            self.refresh_video_list()
            if not self.output_dir.get():
                self.output_dir.set(str(Path(self.video_files[0]).parent / f'{PRODUCT_NAME} Output'))
            self._on_change()

    def remove_selected(self):
        if self.busy:
            return
        super().remove_selected()
        self._update_badges()
        self._on_change()

    def clear_videos(self):
        if self.busy:
            return
        self.video_files.clear()
        self.refresh_video_list()
        self._update_badges()
        self._on_change()

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end-1c'))
        self.status.set(tr('Журнал скопирован в буфер обмена.'))

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def check_ffmpeg(self):
        tools = resolve_video_tools(self.ffmpeg_path.get())
        if not tools:
            self._update_tools()
            return
        self.ff_status.configure(text=tr('Проверяю FFmpeg и FFprobe…'), fg=MUTED)
        checks = self.tool_checks

        def verify():
            try:
                versions = []
                for path in tools:
                    result = subprocess.run([path, '-version'], capture_output=True, text=True, timeout=8,
                                            encoding='utf-8', errors='replace',
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    if result.returncode or not result.stdout.strip():
                        raise RuntimeError(result.stderr.strip() or tr('Не запускается {0}.').format(Path(path).name))
                    versions.append(result.stdout.splitlines()[0])
                checks.put((tools, '\n'.join(versions), True))
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                checks.put((tools, str(exc), False))

        threading.Thread(target=verify, daemon=True, name='jojo-check-ffmpeg').start()

    def _process_events(self):
        super()._process_events()
        self._update_badges()
        while not self.tool_checks.empty():
            tools, result, ok = self.tool_checks.get_nowait()
            if tools == resolve_video_tools(self.ffmpeg_path.get()):
                self.ff_status.configure(text=result, fg=GREEN if ok else RED)
                self.log_message(result)
