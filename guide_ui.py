from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
from urllib.parse import unquote, urlsplit
import webbrowser

from PIL import Image, ImageTk

from i18n import tr
from publisher_colors import ACCENT, BG, CYAN, FG, FONT, MUTED, PANEL


def headset_icon(master):
    pixels = (
        '..................',
        '......######......',
        '....###....###....',
        '...##........##...',
        '..##..........##..',
        '..#............#..',
        '.##............##.',
        '.####........####.',
        '.####........####.',
        '.####........####.',
        '.####........####.',
        '..###........###..',
        '..............##..',
        '.............##...',
        '.........#####....',
        '.........###......',
        '..................',
        '..................',
    )
    icon = tk.PhotoImage(master=master, width=18, height=18)
    for y, row in enumerate(pixels):
        for x, pixel in enumerate(row):
            if pixel == '#':
                icon.put(MUTED, (x, y))
    return icon


def globe_icon(master):
    icon = tk.PhotoImage(master=master, width=20, height=20)
    for y in range(20):
        for x in range(20):
            dx, dy = x - 9.5, y - 9.5
            distance = dx * dx + dy * dy
            ellipse = (dx / 4) ** 2 + (dy / 8.5) ** 2
            if 59 <= distance <= 82 or (distance < 73 and (abs(dy) < 1 or .72 <= ellipse <= 1.25)):
                icon.put(FG, (x, y))
    return icon


def bind_tooltip(widget, text):
    timer = None
    popup = None

    def hide(_event=None):
        nonlocal timer, popup
        if timer is not None:
            widget.after_cancel(timer)
            timer = None
        if popup is not None:
            if popup.winfo_exists():
                popup.destroy()
            popup = None

    def show():
        nonlocal timer, popup
        timer = None
        popup = tk.Toplevel(widget)
        popup.withdraw()
        popup.overrideredirect(True)
        label = tk.Label(popup, text=text, bg=PANEL, fg=FG, font=(FONT, 10), padx=10, pady=6)
        label.pack()
        right = widget.winfo_rootx() + widget.winfo_width()
        x = max(0, right - label.winfo_reqwidth())
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        popup.geometry(f'+{x}+{y}')
        popup.deiconify()

    def schedule(_event):
        nonlocal timer
        hide()
        timer = widget.after(400, show)

    widget.bind('<Enter>', schedule, add='+')
    for event in ('<Leave>', '<ButtonPress>', '<Destroy>'):
        widget.bind(event, hide, add='+')


def place_window(window, parent, max_width, max_height, screen_fraction):
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    width = min(max_width, int(screen_width * screen_fraction))
    height = min(max_height, int(screen_height * screen_fraction))
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    x = max(0, min(x, screen_width - width))
    y = max(0, min(y, screen_height - height))
    window.geometry(f'{width}x{height}+{x}+{y}')


def read_png(path):
    with Image.open(path, formats=('PNG',)) as image:
        return image.convert('RGBA')


def scaled_photo(master, source, size):
    image = source if size == source.size else source.resize(size, Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image, master=master)


class ScreenshotWindow(tk.Toplevel):
    def __init__(self, parent, source, title):
        super().__init__(parent)
        self.withdraw()
        self.transient(parent)
        self.title(title)
        self.configure(bg=BG)
        place_window(self, parent, 2400, 1350, .9)
        self.minsize(420, 300)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.source = source
        self.photo = None
        self.requested_scale = None
        self.scale = 1.0

        toolbar = tk.Frame(self, bg=PANEL, padx=12, pady=8)
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text=tr('Закрыть'), command=self.destroy).pack(side='right')
        self.fit_button = ttk.Button(toolbar, text=tr('Вписать'), command=self.fit)
        self.fit_button.pack(side='left', padx=(0, 6))
        self.actual_button = ttk.Button(toolbar, text='100%', width=6, command=lambda: self.zoom(1.0))
        self.actual_button.pack(side='left', padx=(0, 6))
        self.zoom_out_button = ttk.Button(toolbar, text='−', width=3, command=lambda: self.zoom(self.scale / 1.25))
        self.zoom_out_button.pack(side='left')
        bind_tooltip(self.zoom_out_button, tr('Уменьшить'))
        self.zoom_in_button = ttk.Button(toolbar, text='+', width=3, command=lambda: self.zoom(self.scale * 1.25))
        self.zoom_in_button.pack(side='left')
        bind_tooltip(self.zoom_in_button, tr('Увеличить'))
        self.percent = tk.Label(toolbar, width=5, bg=PANEL, fg=FG, font=(FONT, 10))
        self.percent.pack(side='left', padx=6)

        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0, cursor='fleur')
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=self.canvas.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.picture = self.canvas.create_image(0, 0, anchor='nw')
        self.canvas.bind('<Configure>', self.render)
        self.canvas.bind('<MouseWheel>', self.scroll)
        self.canvas.bind('<Shift-MouseWheel>', self.scroll_horizontal)
        self.canvas.bind('<Control-MouseWheel>', self.zoom_wheel)
        self.canvas.bind('<ButtonPress-1>', lambda event: self.canvas.scan_mark(event.x, event.y))
        self.canvas.bind('<B1-Motion>', lambda event: self.canvas.scan_dragto(event.x, event.y, gain=1))
        self.deiconify()
        self.canvas.focus_set()

    def render(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        scale = self.requested_scale
        if scale is None:
            scale = min(1.0, width / self.source.width, height / self.source.height)
        size = tuple(max(1, round(length * scale)) for length in self.source.size)
        if self.photo is None or size != (self.photo.width(), self.photo.height()):
            self.photo = scaled_photo(self.canvas, self.source, size)
            self.canvas.itemconfigure(self.picture, image=self.photo)
        image_width, image_height = size
        self.canvas.coords(self.picture, max(0, (width - image_width) // 2), max(0, (height - image_height) // 2))
        self.extent = max(width, image_width), max(height, image_height)
        self.canvas.configure(scrollregion=(0, 0, *self.extent))
        self.scale = scale
        self.percent.configure(text=f'{scale:.0%}')

    def fit(self):
        self.requested_scale = None
        self.render()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def zoom(self, scale, x=None, y=None):
        if self.photo is None:
            return
        if x is None:
            x, y = self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2
        left, top = self.canvas.coords(self.picture)
        image_x = (self.canvas.canvasx(x) - left) / self.scale
        image_y = (self.canvas.canvasy(y) - top) / self.scale
        self.requested_scale = min(2.0, max(.1, scale))
        self.render()
        left, top = self.canvas.coords(self.picture)
        width, height = self.extent
        self.canvas.xview_moveto((left + image_x * self.scale - x) / width)
        self.canvas.yview_moveto((top + image_y * self.scale - y) / height)

    def scroll(self, event):
        self.canvas.yview_scroll(-3 if event.delta > 0 else 3, 'units')

    def scroll_horizontal(self, event):
        self.canvas.xview_scroll(-3 if event.delta > 0 else 3, 'units')

    def zoom_wheel(self, event):
        self.zoom(self.scale * (1.25 if event.delta > 0 else .8), event.x, event.y)


class GuideWindow(tk.Toplevel):
    _inline = re.compile(r'\[([^\]]+)\]\(([^\s)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`')
    _image = re.compile(r'!\[([^\]]+)\]\(([^\s)]+)\)')
    _heading = re.compile(r'^(#{1,6})\s+(.+)')
    _anchor = re.compile(r'<a name="([a-z0-9-]+)"></a>')

    def __init__(self, parent, readme: Path):
        super().__init__(parent)
        self.withdraw()
        self.readme = readme.resolve()
        self.document = self.readme
        self.sections = []
        self.title(tr('Справка · JetVideo'))
        self.transient(parent)
        self.configure(bg=BG)
        place_window(self, parent, 1400, 1040, .82)
        self.minsize(520, 360)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.screenshot = None
        self.preview_resize = None

        toolbar = tk.Frame(self, bg=PANEL, padx=18, pady=12)
        toolbar.pack(fill='x')
        tk.Label(toolbar, text=tr('Справка'), bg=PANEL, fg=FG, font=(FONT, 15, 'bold')).pack(side='left')
        tk.Button(toolbar, text=tr('Закрыть'), command=self.destroy, bg=BG, fg=FG, relief='flat',
                  padx=12, pady=5).pack(side='right')
        self.home = tk.Button(toolbar, text=tr('К началу'), command=lambda: self.load(self.readme),
                              bg=BG, fg=FG, relief='flat', padx=10, pady=5)
        self.home.pack(side='right', padx=8)
        style = ttk.Style(self)
        style.configure('Guide.TCombobox', foreground=FG, arrowcolor=FG, padding=6)
        style.map('Guide.TCombobox', fieldbackground=[('readonly', BG)],
                  background=[('readonly', PANEL)], foreground=[('readonly', FG)],
                  selectbackground=[('readonly', BG)], selectforeground=[('readonly', FG)])
        self.option_add('*TCombobox*Listbox.background', BG)
        self.option_add('*TCombobox*Listbox.foreground', FG)
        self.contents = ttk.Combobox(toolbar, state='readonly', width=34, font=(FONT, 10), style='Guide.TCombobox')
        self.contents.pack(side='left', fill='x', expand=True, padx=(16, 8))
        self.contents.bind('<<ComboboxSelected>>', self._jump)

        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True)
        self.text = tk.Text(body, wrap='word', bg=BG, fg=FG, font=(FONT, 11),
                            selectbackground=ACCENT, relief='flat', highlightthickness=0,
                            padx=24, pady=16, spacing1=2, spacing3=3, cursor='arrow')
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.text.yview)
        scroll.pack(side='right', fill='y')
        self.text.pack(side='left', fill='both', expand=True)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.bind('<Configure>', self.schedule_preview_resize)
        self.text.tag_configure('title', font=(FONT, 23, 'bold'), spacing3=10)
        self.text.tag_configure('section', font=(FONT, 15, 'bold'), foreground=CYAN, spacing1=10, spacing3=5)
        self.text.tag_configure('subsection', font=(FONT, 12, 'bold'), spacing1=6)
        self.text.tag_configure('bold', font=(FONT, 11, 'bold'))
        self.text.tag_configure('code', font=('Consolas', 10), background=PANEL)
        self.text.tag_configure('link', foreground=CYAN, underline=True)
        self.text.tag_configure('table', lmargin1=14, lmargin2=14)
        self.load(self.readme)
        self.deiconify()
        self.text.focus_set()

    def load(self, document):
        self.document = document
        self.sections = [(tr('Начало документа'), '1.0')]
        self.anchor_sections = {}
        self.contents.set('')
        self.text.configure(state='normal')
        for tag in self.text.tag_names():
            if tag.startswith('url_'):
                self.text.tag_delete(tag)
        self.text.delete('1.0', 'end')
        self.previews = []
        try:
            lines = document.read_text(encoding='utf-8-sig').splitlines()
        except (OSError, UnicodeError):
            lines = [tr('Не удалось открыть справку.'), '',
                     tr('Откройте README.md из исходников или распакуйте архив JetVideo заново.')]
        code_block = False
        for line in lines:
            if line.startswith('```'):
                code_block = not code_block
            elif code_block or document.suffix == '.example':
                self.text.insert('end', line + '\n', 'code')
            elif picture := self._image.fullmatch(line):
                self._insert_image(*picture.groups())
            elif anchor := self._anchor.fullmatch(line):
                self.anchor_sections[anchor[1]] = len(self.sections)
            elif heading := self._heading.match(line):
                level = len(heading[1])
                heading_text = heading[2]
                tag = 'title' if level == 1 else 'section' if level == 2 else 'subsection'
                if level == 2:
                    self.sections.append((heading_text, self.text.index('end-1c')))
                self.text.insert('end', heading_text + '\n', tag)
            elif line.startswith('|') and line.endswith('|'):
                cells = [cell.strip() for cell in line[1:-1].split('|')]
                if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                    self._insert_inline('  ·  '.join(cells), ('table',))
            else:
                if line.startswith('- '):
                    line = '• ' + line[2:]
                self._insert_inline(line)
        self.text.configure(state='disabled')
        self.resize_previews()
        self.text.yview_moveto(0)
        self.contents.configure(values=[title for title, _ in self.sections])
        self.contents.current(0)

    def open_section(self, document, anchor):
        if self.screenshot is not None and self.screenshot.winfo_exists():
            self.screenshot.destroy()
        if document != self.document:
            self.load(document)
        self.contents.current(self.anchor_sections.get(anchor, 0))
        self._jump()

    def _local_path(self, url):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        path = (self.document.parent / unquote(parsed.path)).resolve()
        return path if path.is_relative_to(self.readme.parent) else None

    def _insert_image(self, caption, url):
        path = self._local_path(url)
        if not path or path.suffix.lower() != '.png':
            self.text.insert('end', caption + '\n')
            return
        try:
            image = read_png(path)
        except (OSError, Image.DecompressionBombError):
            self.text.insert('end', caption + '\n')
            return
        button = tk.Button(self.text, command=lambda: self.show_screenshot(image, caption), cursor='hand2',
                           bg=BG, activebackground=PANEL, relief='flat', bd=0, padx=0, pady=0,
                           highlightthickness=1, highlightbackground=BG, highlightcolor=CYAN)
        button.bind('<MouseWheel>', self.scroll_preview)
        button.photo = None
        self.previews.append((button, image))
        self.text.window_create('end', window=button, pady=6)
        self.text.insert('end', '\n')
        self._insert_link(caption, url, ())
        self.text.insert('end', '\n')

    def schedule_preview_resize(self, _event):
        if self.preview_resize is not None:
            self.after_cancel(self.preview_resize)
        if self.previews:
            self.preview_resize = self.after(90, self.resize_previews)

    def resize_previews(self):
        if self.preview_resize is not None:
            self.after_cancel(self.preview_resize)
            self.preview_resize = None
        width = self.text.winfo_width()
        if width <= 1:
            width = self.text.winfo_reqwidth()
        width = max(1, width - 2 * int(self.text.cget('padx')) - 4)
        for button, source in self.previews:
            preview_width = min(width, source.width)
            if button.photo is None or button.photo.width() != preview_width:
                size = preview_width, max(1, round(source.height * preview_width / source.width))
                button.photo = scaled_photo(button, source, size)
                button.configure(image=button.photo)

    def scroll_preview(self, event):
        self.text.yview_scroll(-3 if event.delta > 0 else 3, 'units')

    def show_screenshot(self, image, caption):
        if self.screenshot is not None and self.screenshot.winfo_exists():
            self.screenshot.destroy()
        self.screenshot = ScreenshotWindow(self, image, caption)

    def destroy(self):
        if self.preview_resize is not None:
            self.after_cancel(self.preview_resize)
        super().destroy()

    def _insert_link(self, label, url, tags):
        tag = 'url_' + self.text.index('end-1c')
        self.text.insert('end', label, (*tags, 'link', tag))
        self.text.tag_bind(tag, '<Button-1>', lambda _event: self.after_idle(self._open, url, label))
        self.text.tag_bind(tag, '<Enter>', lambda _event: self.text.configure(cursor='hand2'))
        self.text.tag_bind(tag, '<Leave>', lambda _event: self.text.configure(cursor='arrow'))

    def _insert_inline(self, line, tags=()):
        end = 0
        for match in self._inline.finditer(line):
            self.text.insert('end', line[end:match.start()], tags)
            if match[1] is not None:
                self._insert_link(match[1], match[2], tags)
            else:
                self.text.insert('end', match[3] or match[4], (*tags, 'bold' if match[3] else 'code'))
            end = match.end()
        self.text.insert('end', line[end:] + '\n', tags)

    def _jump(self, _event=None):
        index = self.contents.current()
        if index >= 0:
            self.text.yview(self.sections[index][1])
            self.text.focus_set()

    def _open(self, url, label=None):
        parsed = urlsplit(url)
        if parsed.scheme in ('https', 'http') and parsed.netloc:
            webbrowser.open(url)
        elif parsed.fragment and not (parsed.scheme or parsed.netloc or parsed.path):
            self.open_section(self.document, parsed.fragment)
        elif path := self._local_path(url):
            if path.suffix.lower() in ('.md', '.example') or path.name == 'LICENSE':
                self.load(path)
            elif path.suffix.lower() == '.png':
                try:
                    image = read_png(path)
                except (OSError, Image.DecompressionBombError):
                    return
                self.show_screenshot(image, label or path.stem)
