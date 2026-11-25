from __future__ import annotations

from i18n import tr
import json
import os
import uuid
import queue
import re
import shutil
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from zoneinfo import ZoneInfo

import bot
from app_info import APP_NAME, PRODUCT_NAME, VERSION, portable_home
from guide_ui import bind_tooltip
from handoff import CopyJob, HandoffPlan, transfer
import portfolio
from profiles import ProfileStore
from insights_ui import InsightsMixin
from timezone_ui import TimezoneMenu
from ui_icons import icon
from ui_motion import Tween

BG = "#090E1A"
SIDEBAR = "#101829"
NAV_SELECTED = "#242847"
NAV_HOVER = "#19243A"
PANEL = "#151E30"
PANEL2 = "#1B2840"
BORDER = "#2B3850"
FG = "#F4F6FF"
MUTED = "#98A8C1"
ACCENT = "#8B80FA"
ACCENT_HOVER = "#a39bff"
CYAN = "#4BD8D0"
GREEN = "#54D9A7"
YELLOW = "#F5C875"
RED = "#FB8393"
FONT = "Segoe UI"
MONO = "Consolas"
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
ENV_KEYS = [
    ("BUFFER_API_KEY", "Buffer API Key"),
    ("R2_ENDPOINT", "R2 Endpoint"),
    ("R2_ACCESS_KEY_ID", "R2 Access Key ID"),
    ("R2_SECRET_ACCESS_KEY", "R2 Secret Access Key"),
    ("R2_BUCKET_NAME", "R2 Bucket"),
    ("R2_PUBLIC_BASE_URL", "R2 Public URL"),
]
SECRET_KEYS = {"BUFFER_API_KEY", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"}


def bundled(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def data_home() -> Path:
    if portable := portable_home():
        return portable
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base or Path.home()) / "JetVideo Modern"
    return Path.home() / ".local" / "share" / "JetVideo Modern"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, OSError, json.JSONDecodeError):
        if path.exists() and path.name in ('config.json', 'state.json', 'profiles.json'):
            raise ValueError(tr('Нельзя прочитать {0}. Восстанови резервную копию; данные не сброшены.').format(path.name))
        return default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class WorkspaceSelectionRequired(Exception):

    def __init__(self, missing_previous: bool = False):
        self.missing_previous = missing_previous
        super().__init__(tr('Рабочая папка не найдена') if missing_previous else tr('Выбери рабочую папку'))


def find_workspace(home: Path) -> tuple[Path | None, Path, str]:
    pref = home / "preferences.json"
    opts = read_json(pref, {})
    chosen = opts.get("workspace") if isinstance(opts, dict) else None
    if chosen:
        root = Path(chosen).expanduser()
        return (root if root.is_dir() else None), pref, ("saved" if root.is_dir() else "missing")

    legacy = home / "workspace"
    if (legacy / "config.json").is_file():
        return legacy, pref, "legacy"
    return None, pref, "new"


def validate_workspace(root: Path):
    if not root.is_dir():
        raise ValueError(tr('Выбранная папка не существует. Создай её в окне выбора папки.'))
    if not (root / "config.json").is_file() and any(root.iterdir()):
        raise ValueError(tr('Папка не пустая и в ней нет config.json. Выбери пустую папку или существующую рабочую папку {0}.').format(PRODUCT_NAME))


def setup_workspace(home: Path, selected: Path | None = None) -> tuple[Path, Path]:
    root, pref, _status = find_workspace(home)
    if selected is None:
        if root is None:
            raise WorkspaceSelectionRequired(_status == "missing")
    else:
        root = Path(selected).expanduser().resolve()
    validate_workspace(root)
    if selected is None and not all((root / file).is_file() for file in ("config.json", "state.json")):
        raise WorkspaceSelectionRequired(True)
    initialize_workspace(root)
    write_json(pref, {"workspace": str(root.resolve())})
    return root, pref


def copy_workspace(source: Path, target: Path) -> Path:
    source = source.resolve()
    target = target.resolve()
    if not source.is_dir() or not (source / "config.json").is_file() or not (source / "state.json").is_file():
        raise ValueError(tr('Исходная папка не содержит config.json и state.json'))
    if source == target or source in target.parents or target in source.parents:
        raise ValueError(tr('Нельзя переносить workspace внутрь самого себя или в родительскую папку'))
    if target.exists():
        raise FileExistsError(tr('Папка уже существует: {0}. Выбери другое расположение.').format(target))
    if not target.parent.is_dir():
        raise ValueError(tr('Родительская папка не существует'))
    staging = target.with_name(f".{target.name}.copying-{uuid.uuid4().hex[:10]}")
    try:
        shutil.copytree(source, staging)
        if not (staging / "state.json").is_file() or not (staging / "config.json").is_file():
            raise OSError(tr('Не удалось полностью скопировать историю и настройки'))
        for cfg_path in staging.rglob("config.json"):
            cfg = read_json(cfg_path, {})
            changed = False
            for key in ("videos_folder", "scheduled_folder", "posted_folder"):
                value = cfg.get(key)
                if value and Path(value).is_absolute():
                    folder = Path(value).resolve()
                    if folder == source or source in folder.parents:
                        cfg[key] = str(target / folder.relative_to(source))
                        changed = True
            if changed:
                write_json(cfg_path, cfg)
        staging.rename(target)
        return target
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def initialize_workspace(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "config.json").exists():
        template = bundled("starter_config.json")
        if template.exists():
            shutil.copyfile(template, root / "config.json")
        else:
            raise FileNotFoundError(tr('Не найден starter_config.json внутри программы'))
    if not (root / "state.json").exists():
        write_json(root / "state.json", {"items": {}})
    config = read_json(root / "config.json", {})
    for key, fallback in (("videos_folder", "ready"), ("scheduled_folder", "scheduled"), ("posted_folder", "posted")):
        folder = Path(config.get(key, fallback))
        (folder if folder.is_absolute() else root / folder).mkdir(parents=True, exist_ok=True)


def parse_env(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values
    result = {k: v or '' for k, v in dotenv_values(path, interpolate=False).items()} if path.exists() else {}
    bot._secrets.update(result.get(k, '') for k in SECRET_KEYS)
    return result


def save_env(path: Path, values: dict[str, str]):
    bot._secrets.update(values.get(k, '') for k in SECRET_KEYS)
    lines = [f"# {PRODUCT_NAME} credentials — private, never share this file."]
    for key, _ in ENV_KEYS:
        val = values.get(key, "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
        lines.append(f'{key}="{val}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        if os.name != "nt":
            path.chmod(0o600)
    except OSError:
        pass


def reveal(path: Path):
    if path.is_dir():
        path.mkdir(exist_ok=True, parents=True)
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", str(path)])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", str(path)])


class ModernApp(InsightsMixin, tk.Frame):
    def __init__(self, master, *, connection_help=None, captions_help=None):
        super().__init__(master, bg=BG)
        self.home = data_home()
        try:
            self.workspace_dir, self.preferences = self._choose_startup_workspace()
        except WorkspaceSelectionRequired:
            self.destroy()
            raise
        self.store = ProfileStore(self.workspace_dir)
        self.root_dir = self.store.selected().root
        self.config_data = read_json(self.root_dir / "config.json", {})
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.migrating = False
        self.watch_worker: threading.Thread | None = None
        self.watch_stop = threading.Event()
        self.active_page = "dashboard"
        self.page_map: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.nav_icons: dict[str, tuple[tk.PhotoImage, tk.PhotoImage]] = {}
        self.nav_motion = Tween(self)
        self.history_rows = {}
        self._visible_history = []
        self._history_scope = self.root_dir
        self.titles = {
            "dashboard": (tr('Главная'), ""),
            "history": (tr('История публикаций'), ""),
            "accounts": (tr('Аккаунты'), tr('Общие ключи Buffer + R2, но отдельные очереди, расписания и видео.')),
            "insights": (tr('Статистика'), tr('Просмотры, лайки и реакции по всем TikTok-аккаунтам')),
            "sync": (tr('Синхронизация'), tr('Поиск удалённых постов и исправление статусов Buffer')),
            "schedule": (tr('Расписание'), tr('Настрой частоту и время выхода частей')),
            "episodes": (tr('Сезоны и серии'), tr('Соответствие номеров частей эпизодам')),
            "captions": (tr('Подписи и теги'), tr('Шаблоны публикаций TikTok')),
            "connections": (tr('Подключения'), ""),
            "logs": (tr('Журнал действий'), ""),
        }
        self.configure(bg=BG)
        self._init_style()
        self._build_shell()
        self._build_dashboard()
        self._build_history()
        self._build_accounts()
        self._build_insights()
        self._build_sync()
        self._build_schedule()
        self._build_episodes()
        self._build_captions(captions_help)
        self._build_connections(connection_help)
        self._build_logs()
        self._load_forms()
        self._refresh_accounts()
        self.switch("dashboard")
        bot.set_log_callback(lambda s: self.messages.put(("log", s)))
        self.after(100, self._flush_messages)
        self.after(300, self._periodic_refresh)


    def _choose_startup_workspace(self) -> tuple[Path, Path]:
        root, _, status = find_workspace(self.home)
        if root is not None:
            try:
                return setup_workspace(self.home)
            except WorkspaceSelectionRequired:
                status = "missing"
        if status == "missing":
            messagebox.showwarning(
                tr('Рабочая папка перемещена'),
                tr('Сохранённый workspace больше не найден.\n\nВыбери новое расположение папки с config.json и state.json. Программа НЕ будет создавать пустую папку на диске C.'), parent=self)
        else:
            use_default = messagebox.askyesno(
                tr('Первый запуск {0}').format(APP_NAME),
                tr('Создать настройки и историю в этой папке?\n\n{0}\n\nПапки видео можно выбрать отдельно в «Расписание».\n«Нет» — выбрать существующую рабочую папку или другое расположение.').format(self.home / 'data'), parent=self)
            if use_default:
                chosen = self.home / 'data'
                chosen.mkdir(parents=True, exist_ok=True)
                return setup_workspace(self.home, chosen)
        while True:
            folder = filedialog.askdirectory(
                title=tr('{0} — выбери рабочую папку или создай новую').format(PRODUCT_NAME), parent=self,
                initialdir=str(Path.home()))
            if not folder:
                raise WorkspaceSelectionRequired(status == "missing")
            chosen = Path(folder)
            if (chosen / "config.json").is_file() and not (chosen / "state.json").is_file():
                if not messagebox.askyesno(tr('Нет истории публикаций'), tr('В этой папке нет state.json! Это может привести к повторной публикации старых частей. Подключить всё равно?'), parent=self):
                    continue
            try:
                return setup_workspace(self.home, chosen)
            except (OSError, ValueError) as exc:
                messagebox.showerror(tr('Не удалось выбрать workspace'), str(exc), parent=self)

    def _init_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Modern.Treeview", font=(FONT, 10), rowheight=34, background=PANEL,
                        foreground=FG, fieldbackground=PANEL, borderwidth=0)
        style.map("Modern.Treeview", background=[("selected", "#39476B")], foreground=[("selected", "#FFFFFF")])
        style.configure("History.Modern.Treeview", rowheight=29)
        style.configure("Modern.Treeview.Heading", font=(FONT, 10, "bold"), foreground=MUTED,
                        background=PANEL2, padding=(11, 11), borderwidth=0, relief="flat")
        style.map("Modern.Treeview.Heading", background=[("active", "#283855")])
        style.configure("Profile.TCombobox", font=(FONT, 10), fieldbackground=PANEL2,
                        background=PANEL2, foreground=FG, arrowcolor=FG, borderwidth=0, padding=6)
        style.map("Profile.TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", FG)])
        style.configure("Timezone.TMenubutton", background=PANEL2, foreground=FG, arrowcolor=FG,
                        bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2,
                        padding=(8, 7), font=(FONT, 11), anchor="w", width=26)
        style.map("Timezone.TMenubutton", background=[("active", "#2B3A59")])
        style.configure("Actions.TMenubutton", background=PANEL2, foreground=FG, arrowcolor=MUTED,
                        bordercolor=PANEL2, lightcolor=PANEL2, darkcolor=PANEL2,
                        padding=(11, 8), font=(FONT, 10, "bold"), focuscolor=ACCENT)
        style.map("Actions.TMenubutton", background=[("active", NAV_HOVER)])
        for scrollbar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(scrollbar, troughcolor=PANEL, background=PANEL2,
                            darkcolor=PANEL2, lightcolor=PANEL2, borderwidth=0)

    @staticmethod
    def label(parent, text: str, size: int = 11, color: str = FG, bold: bool = False, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent, text: str, command, kind="normal", size=10, *, glyph=None, **kwargs) -> tk.Button:
        bg, hover, fg = {
            "primary": (ACCENT, ACCENT_HOVER, "#101226"),
            "normal": (PANEL2, "#2B3A59", FG),
            "ghost": (PANEL, "#273650", FG),
            "danger": ("#422A3A", "#654057", RED),
        }[kind]
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activeforeground=fg, activebackground=hover,
                      borderwidth=0, relief="flat", highlightthickness=1,
                      highlightbackground=parent.cget("bg"), highlightcolor=ACCENT,
                      cursor="hand2", font=(FONT, size, "bold"), padx=12, pady=8, **kwargs)
        if glyph:
            b.icon = icon(b, glyph, fg, size=15)
            b.configure(image=b.icon, compound="left", text=f"  {text}")
        b.bind("<Enter>", lambda e: b.configure(bg=hover) if b.cget("state") != "disabled" else None)
        b.bind("<Leave>", lambda e: b.configure(bg=bg) if b.cget("state") != "disabled" else None)
        return b

    def actions_menu(self, parent, items):
        button = ttk.Menubutton(parent, text=tr('Другие действия'), style="Actions.TMenubutton",
                                cursor="hand2", takefocus=True)
        menu = tk.Menu(button, tearoff=False, bg=PANEL2, fg=FG, activebackground=ACCENT,
                       activeforeground=BG, font=(FONT, 10))
        for item in items:
            if item is None:
                menu.add_separator()
            else:
                label, callback = item
                menu.add_command(label=label, command=callback)
        button.configure(menu=menu)
        return button

    def entry(self, parent, variable, width=28, secret=False) -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, bg=PANEL2, fg=FG, insertbackground=FG,
                        selectbackground=ACCENT, font=(FONT, 11), relief="flat", width=width,
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT, show="●" if secret else "")

    def panel(self, parent, title: str | None = None, subtitle: str | None = None, **kwargs) -> tk.Frame:
        container = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, **kwargs)
        if title:
            head = tk.Frame(container, bg=PANEL)
            head.pack(fill="x", padx=17, pady=(13, 9))
            self.label(head, title, 13, FG, True).pack(anchor="w")
            if subtitle:
                hint = self.label(head, subtitle, 10, MUTED, justify="left")
                hint.pack(anchor="w", pady=(3, 0))
                head.bind("<Configure>", lambda event: hint.configure(wraplength=max(1, event.width)))
        return container

    def _build_shell(self):
        sidebar = tk.Frame(self, bg=SIDEBAR, width=212)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        account_box = tk.Frame(sidebar, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        account_box.pack(fill="x", padx=12, pady=(13, 12))
        account_head = tk.Frame(account_box, bg=PANEL)
        account_head.pack(fill="x", padx=(10, 6), pady=(5, 1))
        self.label(account_head, tr('АКТИВНЫЙ TIKTOK'), 9, MUTED, True).pack(side="left")
        add_account = self.button(account_head, "", self.add_profile, "ghost", glyph="plus")
        add_account.configure(padx=4, pady=3, text="")
        add_account.pack(side="right")
        bind_tooltip(add_account, tr('+  Добавить аккаунт'))
        self.profile_selector = ttk.Combobox(account_box, state="readonly", style="Profile.TCombobox", width=18)
        self.profile_selector.pack(fill="x", padx=7, pady=(0, 7))
        self.profile_selector.bind("<<ComboboxSelected>>", self._on_profile_selected)
        nav_list = tk.Frame(sidebar, bg=SIDEBAR)
        nav_list.pack(fill="x")
        nav = (
            ("dashboard", "overview"),
            ("history", "history"),
            ("insights", "analytics"),
            None,
            ("schedule", "schedule"),
            ("episodes", "clips"),
            ("captions", "captions"),
            None,
            ("accounts", "accounts"),
            ("connections", "plug"),
            ("sync", "check"),
            ("logs", "logs"),
        )
        for item in nav:
            if item is None:
                tk.Frame(nav_list, bg=BORDER, height=1).pack(fill="x", padx=17, pady=6)
                continue
            key, glyph = item
            row = tk.Frame(nav_list, bg=SIDEBAR)
            row.pack(fill="x", padx=(12, 9), pady=1)
            b = tk.Button(row, text=f"  {self.titles[key][0]}", anchor="w", relief="flat",
                          borderwidth=0, bg=SIDEBAR, fg=MUTED, activebackground=NAV_HOVER,
                          activeforeground=FG, highlightthickness=1, highlightbackground=SIDEBAR,
                          highlightcolor=ACCENT, cursor="hand2", font=(FONT, 10), padx=10, pady=3,
                          command=lambda k=key: self.switch(k))
            inactive_image = icon(b, glyph, MUTED)
            active_image = icon(b, glyph, ACCENT)
            self.nav_icons[key] = (inactive_image, active_image)
            b.configure(image=inactive_image, compound="left")
            b.bind("<Enter>", lambda _event, k=key, button=b: button.configure(bg=NAV_HOVER)
                   if k != self.active_page else None)
            b.bind("<Leave>", lambda _event, k=key, button=b: button.configure(bg=SIDEBAR)
                   if k != self.active_page else None)
            b.pack(side="left", fill="x", expand=True)
            self.nav_buttons[key] = b
        nav_font = tkfont.Font(self, font=(FONT, 10, "bold"))
        widest = max(nav_font.measure(button["text"]) for button in self.nav_buttons.values())
        sidebar.configure(width=max(212, widest + 70))
        self.nav_indicator = tk.Frame(nav_list, bg=ACCENT, width=3)

        content = tk.Frame(self, bg=BG)
        content.pack(side="left", fill="both", expand=True)
        header = tk.Frame(content, bg=BG)
        header.pack(fill="x", padx=22, pady=(13, 11))
        htxt = tk.Frame(header, bg=BG)
        htxt.pack(side="left")
        self.header_title = self.label(htxt, tr('Главная'), 21, FG, True)
        self.header_title.pack(anchor="w")
        self.header_sub = self.label(htxt, "", 10, MUTED, wraplength=500, justify="left")
        self.schedule_save_button = self.button(header, tr('✓  Сохранить расписание'), self.save_schedule, "primary")
        self.history_shortcut = self.button(header, tr('История публикаций'), lambda: self.switch("history"),
                                            "ghost", glyph="history")
        self.body = tk.Frame(content, bg=BG)
        self.body.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        for k in self.titles:
            f = tk.Frame(self.body, bg=BG)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.page_map[k] = f
        footer = tk.Frame(content, bg=BG)
        footer.pack(fill="x", padx=22, pady=(0, 9))
        self.status_var = tk.StringVar(value=tr('●  Готов к работе'))
        tk.Label(footer, textvariable=self.status_var, bg=BG, fg=GREEN, font=(FONT, 10)).pack(side="left")
        self.workspace_var = tk.StringVar(value="")

    def switch(self, key: str):
        self.active_page = key
        self.page_map[key].tkraise()
        title, sub = self.titles[key]
        self.header_title.configure(text=title)
        if sub:
            self.header_sub.configure(text=sub)
            self.header_sub.pack(anchor="w", pady=(2, 0))
        else:
            self.header_sub.pack_forget()
        if key == "schedule":
            self.schedule_save_button.pack(side="right")
        else:
            self.schedule_save_button.pack_forget()
        if key == "dashboard":
            self.history_shortcut.pack(side="right")
        else:
            self.history_shortcut.pack_forget()
        for k, button in self.nav_buttons.items():
            active = k == key
            button.configure(bg=NAV_SELECTED if active else SIDEBAR, fg=FG if active else MUTED,
                             image=self.nav_icons[k][active], font=(FONT, 10, "bold" if active else "normal"))
        self.after_idle(self._move_nav_indicator, key)
        if key in ("dashboard", "history"):
            self._refresh_dashboard()

    def _move_nav_indicator(self, key):
        if key != self.active_page:
            return
        row = self.nav_buttons[key].master
        end = row.winfo_y() + 4
        start = self.nav_indicator.winfo_y() if self.nav_indicator.winfo_ismapped() else end
        height = max(1, row.winfo_height() - 8)

        def draw(progress):
            y = round(start + (end - start) * progress)
            self.nav_indicator.place(x=9, y=y, height=height)

        if start == end:
            self.nav_motion.cancel()
            draw(1)
        else:
            self.nav_motion.play(draw)

    def _build_dashboard(self):
        page = self.page_map["dashboard"]
        actions = self.panel(page, tr('Быстрые действия'))
        actions.configure(highlightbackground="#3B3970")
        actions.pack(fill="x", pady=(0, 13))
        primary = tk.Frame(actions, bg=PANEL)
        primary.pack(fill="x", padx=16, pady=(0, 12))
        self.add_videos_button = self.button(primary, tr('Добавить видео'), self.add_videos,
                                             glyph="plus")
        self.add_videos_button.pack(side="left", padx=(0, 8))
        self.fill_queue_button = self.button(primary, tr('Заполнить очередь'),
                                              lambda: self.run_job("fill"), "primary", glyph="upload")
        self.fill_queue_button.pack(side="left")
        self.dashboard_actions = self.actions_menu(primary, (
            (f"{tr('Заполнить очередь')} · {tr('ВСЕ АККАУНТЫ').lower()}",
             lambda: self.run_job("fill", all_accounts=True)),
            (tr('✓  Проверить'), lambda: self.run_job("check")),
            (tr('◷  Предпросмотр'), lambda: self.run_job("plan")),
            (tr('▶▶  WATCH для всех'), lambda: self.start_watch(all_accounts=True)),
            None,
            ("ready", lambda: self.open_folder("videos_folder", "ready")),
            ("scheduled", lambda: self.open_folder("scheduled_folder", "scheduled")),
            ("posted", lambda: self.open_folder("posted_folder", "posted")),
            None,
            (tr('Открыть Buffer'), lambda: webbrowser.open("https://publish.buffer.com/")),
        ))
        self.dashboard_actions.pack(side="right")
        tk.Frame(actions, bg=BORDER, height=1).pack(fill="x", padx=16)
        watch = tk.Frame(actions, bg=PANEL)
        watch.pack(fill="x", padx=16, pady=8)
        self.watch_badge = self.label(watch, tr('● WATCH ВЫКЛ'), 9, MUTED, True)
        self.watch_badge.pack(side="left")
        self.watch_on_btn = self.button(watch, tr('▶  Включить WATCH'), self.start_watch, "ghost", 9)
        self.watch_off_btn = self.button(watch, tr('■  Остановить WATCH'), self.stop_watch, "danger", 9)
        self.watch_on_btn.pack(side="right")

        stats = tk.Frame(page, bg=BG)
        stats.pack(fill="x")
        self.stats = {}
        for column, (key, title, color, glyph) in enumerate((
            ("ready", tr('Ожидают загрузки'), ACCENT, "video"),
            ("scheduled", tr('В очереди'), CYAN, "schedule"),
            ("posted", tr('Опубликованы'), GREEN, "check"),
            ("history", tr('В истории'), YELLOW, "history"),
        )):
            card = self.panel(stats)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            stats.columnconfigure(column, weight=1, uniform="stats")
            badge = tk.Label(card, bg=PANEL2, padx=6, pady=6)
            badge.icon = icon(badge, glyph, color, size=16)
            badge.configure(image=badge.icon)
            badge.pack(anchor="w", padx=12, pady=(12, 2))
            value = tk.StringVar(value="—")
            tk.Label(card, textvariable=value, fg=FG, bg=PANEL,
                     font=(FONT, 22, "bold")).pack(anchor="w", padx=12)
            self.label(card, title, 9, MUTED, wraplength=120, justify="left").pack(anchor="w", padx=12, pady=(1, 12))
            self.stats[key] = value

    def _build_history(self):
        history = self.panel(self.page_map["history"])
        history.pack(fill="both", expand=True)
        bar = tk.Frame(history, bg=PANEL)
        bar.pack(fill="x", padx=12, pady=(11, 8))
        search = tk.Frame(bar, bg=PANEL)
        search.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.label(search, tr('Поиск по имени файла или номеру части'), 9, MUTED,
                   wraplength=275, justify="left").pack(anchor="w", pady=(0, 3))
        self.history_search = tk.StringVar(self)
        self.entry(search, self.history_search, width=16).pack(fill="x", ipady=6)
        self.history_detail_button = self.button(bar, tr('Подробности'), self.show_history_details, "normal", 9)
        self.history_detail_button.pack(side="left", anchor="s")
        self.history_detail_button.configure(state="disabled")
        self.actions_menu(bar, (
            (tr('Обновить'), self._refresh_dashboard),
            (tr('Сверить Buffer'), lambda: self.run_job('sync')),
            (tr('↩ Вернуть в ready'), self.restore_selected_parts),
        )).pack(side="right", anchor="s", padx=(7, 0))

        table = tk.Frame(history, bg=PANEL)
        table.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.history_columns = (
            ("file", tr('ИМЯ ФАЙЛА'), 221, "w"),
            ("status", tr('СТАТУС'), 170, "w"),
            ("due", tr('ПУБЛИКАЦИЯ'), 154, "w"),
            ("part", tr('ЧАСТЬ'), 62, "center"),
        )
        self.queue_tree = ttk.Treeview(table, columns=[column[0] for column in self.history_columns],
                                       show="headings", style="History.Modern.Treeview", height=8)
        for col, title, width, anchor in self.history_columns:
            self.queue_tree.heading(col, text=title)
            self.queue_tree.column(col, width=width, minwidth=145 if col == "file" else 60,
                                   anchor=anchor, stretch=col == "file")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.queue_tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.queue_tree.xview)

        def scroll_horizontal(first, last):
            horizontal.set(first, last)
            if float(first) > 0 or float(last) < 1:
                horizontal.grid(row=1, column=0, sticky="ew")
            else:
                horizontal.grid_remove()

        self.queue_tree.configure(yscrollcommand=vertical.set, xscrollcommand=scroll_horizontal)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        self.history_empty = self.label(table, tr('Нет записей'), 11, MUTED)
        self.history_empty.place(relx=.5, rely=.5, anchor="center")
        self.queue_tree.tag_configure("even", background=PANEL)
        self.queue_tree.tag_configure("odd", background="#19233A")
        self.queue_tree.bind("<<TreeviewSelect>>", self._update_history_selection)
        self.queue_tree.bind("<Double-1>", self._open_history_row)
        self.queue_tree.bind("<Return>", self._open_history_row)
        self.history_search.trace_add("write", lambda *_args: self._render_history(reset_scroll=True))

    def _update_history_selection(self, _event=None):
        self.history_detail_button.configure(state="normal" if len(self.queue_tree.selection()) == 1 else "disabled")

    def _open_history_row(self, event):
        if event.num == 1 and self.queue_tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            return None
        self.show_history_details()
        return "break"

    def show_history_details(self):
        selected = self.queue_tree.selection()
        if len(selected) != 1 or selected[0] not in self.history_rows:
            return None
        values = self.history_rows[selected[0]]
        dialog = tk.Toplevel(self)
        dialog.title(tr('Подробности'))
        dialog.transient(self.winfo_toplevel())
        dialog.configure(bg=PANEL)
        dialog.minsize(350, 210)
        body = tk.Frame(dialog, bg=PANEL)
        body.pack(fill="both", expand=True, padx=18, pady=(18, 10))
        details = tk.Text(body, wrap="word", width=55, height=9, bg=PANEL2, fg=FG,
                          borderwidth=0, padx=12, pady=10, font=(FONT, 11))
        scroll = ttk.Scrollbar(body, orient="vertical", command=details.yview)
        details.configure(yscrollcommand=scroll.set)
        details.tag_configure("heading", foreground=MUTED, font=(FONT, 9, "bold"))
        for index, ((_, title, _, _), value) in enumerate(zip(self.history_columns, values, strict=True)):
            details.insert("end", title + "\n", "heading")
            details.insert("end", f"{value or '—'}\n" + ("\n" if index == 0 else ""))
        details.configure(state="disabled")
        scroll.pack(side="right", fill="y")
        details.pack(side="left", fill="both", expand=True)
        self.button(dialog, tr('Закрыть'), dialog.destroy, "normal", 9).pack(anchor="e", padx=18, pady=(0, 16))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        return dialog

    def _build_schedule(self):
        page = self.page_map["schedule"]
        settings = self.panel(page, tr('Основные параметры'))
        settings.pack(fill="x", pady=(0, 9))
        fields = [
            ("timezone", tr('Часовой пояс'), "text"),
            ("max_queue_posts", tr('Лимит очереди Buffer'), "int"),
            ("watch_interval_minutes", tr('Интервал WATCH, минут'), "int"),
            ("minimum_schedule_lead_minutes", tr('Запас до публикации, минут'), "int"),
            ("thumbnail_offset_ms", tr('Кадр обложки, мс'), "int"),
            ('file_stable_seconds', tr('Ожидание готовности файла, с'), 'int'),
        ]
        self.cfg_vars: dict[str, tuple[tk.StringVar, str]] = {}
        form = tk.Frame(settings, bg=PANEL)
        form.pack(fill="x", padx=17, pady=(0, 7))
        for index, (key, title, typ) in enumerate(fields):
            c = index % 2
            r = index // 2
            box = tk.Frame(form, bg=PANEL)
            box.grid(row=r, column=c, sticky="ew", padx=(0, 9) if c == 0 else (9, 0), pady=(0, 6))
            label = self.label(box, title, 10, MUTED)
            label.pack(anchor="w", pady=(0, 4))
            v = tk.StringVar()
            self.cfg_vars[key] = (v, typ)
            if key == "timezone":
                self.timezone_button = ttk.Menubutton(box, textvariable=v, style="Timezone.TMenubutton",
                                                      takefocus=True, cursor="hand2")
                self.timezone_button.pack(fill="x")
                label.configure(cursor="hand2")
                label.bind("<Button-1>", self._open_timezone_menu)
                for event in ("<Button-1>", "<Return>", "<space>", "<Down>"):
                    self.timezone_button.bind(event, self._open_timezone_menu)
            else:
                self.entry(box, v).pack(fill="x", ipady=5)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self.button(page, tr('Папки ready / scheduled / posted'), self.choose_profile_folders,
                    "ghost", 9, glyph="folder").pack(side="bottom", anchor="w", pady=(7, 0))
        wins = self.panel(page, tr('Окна публикаций'), tr('По одному ролику в каждое окно. Время выбирается случайно внутри интервала'))
        wins.pack(fill="both", expand=True)
        rows = tk.Frame(wins, bg=PANEL)
        rows.pack(fill="both", expand=True, padx=17, pady=(0, 11))
        operations = tk.Frame(rows, bg=PANEL)
        operations.pack(side="right", fill="y", padx=(11, 0))
        self.button(operations, tr('+ Добавить окно'), self.add_window, size=9).pack(fill="x", pady=(0, 4))
        self.button(operations, tr('✎ Изменить'), self.edit_window, size=9).pack(fill="x", pady=(0, 4))
        self.button(operations, tr('− Удалить'), lambda: self.remove_selected(self.windows_tree),
                    "danger", 9).pack(fill="x")
        self.windows_tree = ttk.Treeview(rows, columns=("start", "end"), show="headings", style="Modern.Treeview", height=3)
        self.windows_tree.heading("start", text=tr('НАЧАЛО'))
        self.windows_tree.heading("end", text=tr('КОНЕЦ'))
        self.windows_tree.column("start", width=160, anchor="center")
        self.windows_tree.column("end", width=160, anchor="center")
        scroll = ttk.Scrollbar(rows, orient="vertical", command=self.windows_tree.yview)
        self.windows_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.windows_tree.pack(side="left", fill="both", expand=True)

    def _open_timezone_menu(self, _event):
        self.timezone_menu = TimezoneMenu(self.timezone_button, self.cfg_vars['timezone'][0])
        return 'break'

    def _build_episodes(self):
        page = self.page_map["episodes"]
        row = tk.Frame(page, bg=BG)
        row.pack(side="bottom", fill="x", pady=(8, 0))
        table_panel = self.panel(page, tr('Диапазоны частей'), tr('Нужны только для шаблонов с {season} и {episode}; для обычных видео оставь пустыми'))
        table_panel.pack(fill="both", expand=True)
        table = tk.Frame(table_panel, bg=PANEL)
        table.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        keys = ("from", "to", "season", "episode")
        self.episode_tree = ttk.Treeview(table, columns=keys, show="headings", style="Modern.Treeview")
        for k, title in zip(keys, (tr('С ЧАСТИ'), tr('ПО ЧАСТЬ'), tr('СЕЗОН'), tr('СЕРИЯ'))):
            self.episode_tree.heading(k, text=title)
            self.episode_tree.column(k, anchor="center", minwidth=80)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.episode_tree.yview)
        self.episode_tree.configure(yscrollcommand=scroll.set)
        self.episode_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.button(row, tr('+  Добавить диапазон'), self.add_episode).pack(side="left", padx=(0, 9))
        self.button(row, tr('✎  Изменить'), self.edit_episode).pack(side="left", padx=(0, 9))
        self.button(row, tr('−  Удалить'), lambda: self.remove_selected(self.episode_tree), "danger").pack(side="left")
        self.button(row, tr('✓  Сохранить серии'), self.save_episodes, "primary").pack(side="right")

    def _build_captions(self, captions_help):
        page = self.page_map["captions"]
        intro = tk.Frame(page, bg=BG)
        intro.pack(fill="x", pady=(0, 9))
        if captions_help is not None:
            self.captions_help_button = self.button(intro, f"?  {tr('Справка')}", captions_help, size=9)
            self.captions_help_button.configure(padx=10, pady=5, highlightthickness=1,
                                                highlightbackground=BG, highlightcolor=ACCENT)
            self.captions_help_button.pack(side="right", padx=(12, 0))
        self.label(intro, tr('Переменные: {filename}, {part}, {season}, {episode}, {hashtags}. Сезоны и серии необязательны.'),
                   10, MUTED, wraplength=490, justify="left").pack(side="left")
        actions = tk.Frame(page, bg=BG)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        self.button(actions, tr('✓  Сохранить подписи и теги'), self.save_captions, "primary").pack(side="right")
        self.button(actions, tr('Предпросмотр описания'), self.preview_caption).pack(side="left")
        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="both", expand=True)
        left = self.panel(wrap, tr('Шаблоны текста'), tr('Между шаблонами — отдельная строка ---'))
        right = self.panel(wrap, tr('Наборы хэштегов'), tr('Каждая строка = отдельный набор тегов'))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1, uniform="editor")
        wrap.grid_columnconfigure(1, weight=1, uniform="editor")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.caption_box = tk.Text(left, bg=PANEL2, fg=FG, insertbackground=FG,
                                   highlightthickness=1, highlightbackground=BORDER, relief="flat",
                                   font=(MONO, 11), wrap="word", width=1, padx=13, pady=13, undo=True)
        self.caption_box.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.tags_box = tk.Text(right, bg=PANEL2, fg=FG, insertbackground=FG,
                                highlightthickness=1, highlightbackground=BORDER, relief="flat",
                                font=(MONO, 11), wrap="word", width=1, padx=13, pady=13, undo=True)
        self.tags_box.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    def _build_connections(self, connection_help):
        page = self.page_map["connections"]
        p = self.panel(page, "Buffer + Cloudflare R2", tr('Ключи общие. Канал TikTok каждого профиля выбирается в разделе «Аккаунты»'))
        p.pack(fill="x", pady=(0, 10))
        form = tk.Frame(p, bg=PANEL)
        form.pack(fill="x", padx=17, pady=(0, 11))
        self.env_vars = {}
        self.env_entries = {}
        self.env_help_buttons = {}
        for r, (key, title) in enumerate(ENV_KEYS):
            self.label(form, title, 10, MUTED).grid(row=r, column=0, sticky="w", pady=5)
            var = tk.StringVar()
            entry = self.entry(form, var, secret=key in SECRET_KEYS)
            entry.grid(row=r, column=1, sticky="ew", padx=(15, 0), pady=4, ipady=6)
            self.env_vars[key] = var
            self.env_entries[key] = entry
            if connection_help is not None:
                help_button = self.button(form, "", lambda name=key: connection_help(name), glyph="help")
                help_button.configure(text="", padx=5, pady=5)
                help_button.grid(row=r, column=2, padx=(8, 0))
                bind_tooltip(help_button, tr('Как заполнить {0}').format(title))
                self.env_help_buttons[key] = help_button
        form.columnconfigure(1, weight=1)
        self.show_secret = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(form, text=tr('Показать ключи'), variable=self.show_secret, command=self._toggle_secrets,
                            bg=PANEL, fg=MUTED, selectcolor=PANEL2, activebackground=PANEL,
                            activeforeground=FG, font=(FONT, 10))
        cb.grid(row=len(ENV_KEYS), column=1, sticky="w", padx=(18, 0), pady=(10, 0))
        buttons = tk.Frame(page, bg=BG)
        buttons.pack(fill="x")
        self.button(buttons, tr('✓  Сохранить подключения'), self.save_connections, "primary").pack(side="left", padx=(0, 9))
        self.button(buttons, tr('↻  Проверить подключения'), lambda: self.run_job("check")).pack(side="left")
        self.button(buttons, tr('⌁  Открыть .env'), self.open_credentials, "ghost").pack(side="right")

    def _build_logs(self):
        page = self.page_map["logs"]
        wp = self.panel(page, tr('Рабочая папка'))
        wp.pack(fill="x", pady=(0, 10))
        tk.Label(wp, textvariable=self.workspace_var, bg=PANEL, fg=MUTED, font=(FONT, 10),
                 wraplength=520, justify="left").pack(anchor="w", padx=17, pady=(0, 7))
        wb = tk.Frame(wp, bg=PANEL)
        wb.pack(fill="x", padx=17, pady=(0, 11))
        self.button(wb, tr('▣  Открыть workspace'), lambda: reveal(self.workspace_dir), "ghost", 9).pack(side="left", padx=(0, 8))
        self.button(wb, tr('⇄  Подключить папку'), self.change_workspace, "normal", 9).pack(side="left", padx=(0, 8))
        self.button(wb, tr('↗  Перенести на другой диск'), self.migrate_workspace, "primary", 9).pack(side="left")

        p = self.panel(page, tr('Журнал'))
        p.pack(fill="both", expand=True)
        bar = tk.Frame(p, bg=PANEL)
        bar.pack(fill="x", padx=17, pady=(0, 8))
        self.button(bar, tr('Очистить экран'), lambda: self.log_box.delete("1.0", "end"), "ghost", 9).pack(side="left", padx=(0, 8))
        self.button(bar, tr('Копировать'), self.copy_logs, "ghost", 9).pack(side="left", padx=(0, 8))
        log_area = tk.Frame(p, bg=PANEL)
        log_area.pack(fill="both", expand=True, padx=17, pady=(0, 11))
        self.log_box = tk.Text(log_area, bg="#0C1423", fg="#CEDAF1", insertbackground=FG,
                               selectbackground="#44518B", highlightthickness=1,
                               highlightbackground=BORDER, relief="flat", wrap="word",
                               font=(MONO, 10), padx=14, pady=14, state="disabled")
        scroll = ttk.Scrollbar(log_area, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)
        self.append_log(tr('{0} {1} запущен. Каждый аккаунт имеет собственные видео и историю.\n').format(PRODUCT_NAME, VERSION))

    def _build_accounts(self):
        page = self.page_map['accounts']
        ops = tk.Frame(page, bg=BG)
        ops.pack(fill='x', pady=(0, 9))
        self.button(ops, tr('+  Новый аккаунт'), self.add_profile, 'primary').pack(side='left', padx=(0, 7))
        self.button(ops, tr('⇄  Переключиться'), self.switch_to_selected_profile).pack(side='left', padx=(0, 7))
        self.button(ops, tr('⌁  Привязать Buffer'), self.bind_profile).pack(side='left')
        self.actions_menu(ops, (
            (tr('✎  Переименовать'), self.rename_profile),
            (tr('▣  Открыть папку'), self.open_profile_folder),
            (tr('⇢  Архив / восстановить'), self.toggle_profile_archive),
            (tr('↑↑  Загрузить для всех'), lambda: self.run_job('fill', all_accounts=True)),
            None,
            (tr('Удалить профиль'), self.remove_profile),
        )).pack(side='right')
        panel = self.panel(page)
        panel.pack(fill='both', expand=True)
        table = tk.Frame(panel, bg=PANEL)
        table.pack(fill='both', expand=True, padx=12, pady=12)
        self.accounts_tree = ttk.Treeview(table, columns=('name', 'channel', 'status', 'ready', 'queued', 'posted'),
                                          show='headings', style='Modern.Treeview', height=5)
        for key, title, width in [
            ('name', tr('ПРОФИЛЬ'), 145), ('channel', 'TIKTOK / BUFFER', 135), ('status', tr('СОСТОЯНИЕ'), 100),
            ('ready', 'READY', 59), ('queued', tr('В ОЧЕРЕДИ'), 89), ('posted', 'POSTED', 64)
        ]:
            self.accounts_tree.heading(key, text=title)
            self.accounts_tree.column(key, width=width, minwidth=50, anchor='w' if key in ('name', 'channel', 'status') else 'center')
        sb = ttk.Scrollbar(table, orient='vertical', command=self.accounts_tree.yview)
        self.accounts_tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.accounts_tree.pack(side='left', fill='both', expand=True)
        self.accounts_tree.bind('<Double-1>', lambda _e: self.switch_to_selected_profile())

    def choose_profile_folders(self):
        if self._busy_accounts():
            return
        config = dict(self.config_data)
        old = {k: self._folder(k, v) for k, v in
               zip(('videos_folder', 'scheduled_folder', 'posted_folder'), ('ready', 'scheduled', 'posted'))}
        for key, title in zip(old, (tr('ready — готовые видео'), tr('scheduled — очередь'), tr('posted — опубликованные'))):
            selected = filedialog.askdirectory(title=title, initialdir=str(old[key]), parent=self)
            if not selected:
                return
            config[key] = selected
        try:
            state = bot.load_json(self.root_dir / 'state.json', {'items': {}})
            if state.get('items') and any(Path(config[k]).resolve() != old[k].resolve() for k in old):
                messagebox.showinfo(tr('Смена папок'), tr('История сохранится. Перенеси видео из прежних папок в новые самостоятельно при остановленном WATCH.'), parent=self)
            self.store.save_folders(self.store.selected().id, config)
            self.config_data = config
            self._refresh_dashboard()
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr('Папки'), str(exc), parent=self)

    def remove_profile(self):
        if self._busy_accounts():
            return
        profile_id = self._selected_account_id()
        if not profile_id:
            return
        profile = self.store.get(profile_id)
        if not messagebox.askyesno(tr('Удалить профиль'), tr('Убрать «{0}» из списка?\nВидео, настройки и история останутся на диске.\nПубликации в Buffer останутся в очереди.').format(profile.name), parent=self):
            return
        try:
            self.store.remove(profile_id)
            self.root_dir = self.store.selected().root
            self.config_data = read_json(self.root_dir / 'config.json', {})
            self._load_forms()
            self._refresh_accounts()
            self._refresh_dashboard()
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr('Профили'), str(exc), parent=self)

    def preview_caption(self):
        videos = bot.list_videos(self._folder('videos_folder', 'ready'))
        initial = videos[0][1].name if videos else tr('Моё видео.mp4')
        values = self._edit_dialog(tr('Предпросмотр описания'), (tr('Имя видео'), tr('Номер части (если нужен)')), (initial, '1'))
        if not values:
            return
        try:
            config = dict(self.config_data)
            config['caption_templates'] = [t.strip() for t in re.split(r'\n\s*---\s*\n', self.caption_box.get('1.0', 'end')) if t.strip()]
            config['hashtag_sets'] = [s.split() for s in self.tags_box.get('1.0', 'end').splitlines() if s.strip()] or [[]]
            text = bot.caption_for(config, int(values[1]), values[0])
            messagebox.showinfo(tr('Итоговое описание — случайный вариант шаблона'), text, parent=self)
        except (ValueError, KeyError, bot.BotError) as exc:
            messagebox.showerror(tr('Шаблон'), str(exc), parent=self)

    def _refresh_accounts(self):
        if not hasattr(self, 'accounts_tree'):
            return
        profiles = self.store.list()
        active = [p for p in profiles if p.active]
        self.selector_profile_ids = [p.id for p in active]
        self.profile_selector.configure(values=[p.name for p in active])
        selected = self.store.selected().id
        if selected in self.selector_profile_ids:
            self.profile_selector.current(self.selector_profile_ids.index(selected))
        saved_selection = self.accounts_tree.selection()
        wanted = saved_selection[0] if saved_selection else selected
        for item in self.accounts_tree.get_children():
            self.accounts_tree.delete(item)
        for p in profiles:
            cfg = read_json(p.root / 'config.json', {})
            state = read_json(p.root / 'state.json', {'items': {}})
            items = state.get('items', {})
            ready = p.root / cfg.get('videos_folder', 'ready')
            if not ready.is_absolute():
                ready = p.root / ready
            ready_count = len(bot.list_videos(ready))
            self.accounts_tree.insert('', 'end', iid=p.id, values=(
                p.name + ('  ●' if p.id == selected else ''),
                ('@' + str(cfg['target_tiktok_channel']) if cfg.get('target_tiktok_channel') else tr('Не привязан')),
                tr('Активен') if p.active else tr('В архиве'),
                ready_count,
                sum(1 for v in items.values() if v.get('status') in ('scheduled', 'sending')),
                sum(1 for v in items.values() if v.get('status') == 'sent'),
            ), tags=('even' if profiles.index(p) % 2 == 0 else 'odd',))
        if wanted in self.accounts_tree.get_children():
            self.accounts_tree.selection_set(wanted)

    def _selected_account_id(self):
        sel = self.accounts_tree.selection()
        return sel[0] if sel else self.store.selected().id

    def _busy_accounts(self):
        return self._busy_changing_workspace()

    def _unsaved_settings(self) -> bool:
        cfg = read_json(self.root_dir / 'config.json', {})
        for key, (var, _) in self.cfg_vars.items():
            if var.get().strip() != str(cfg.get(key, '')):
                return True
        windows = [(str(r.get('start', '')), str(r.get('end', ''))) for r in cfg.get('daily_windows', [])]
        current_windows = [tuple(map(str, self.windows_tree.item(i, 'values'))) for i in self.windows_tree.get_children()]
        if windows != current_windows:
            return True
        episode_rows = [tuple(str(r.get(k, '')) for k in ('from', 'to', 'season', 'episode')) for r in cfg.get('episode_ranges', [])]
        current_rows = [tuple(map(str, self.episode_tree.item(i, 'values'))) for i in self.episode_tree.get_children()]
        if episode_rows != current_rows:
            return True
        captions = [v.strip() for v in re.split(r'\n\s*---\s*\n', self.caption_box.get('1.0', 'end')) if v.strip()]
        tags = [line.split() for line in self.tags_box.get('1.0', 'end').splitlines() if line.strip()]
        saved_tags = [row for row in cfg.get('hashtag_sets', []) if row]
        if captions != cfg.get('caption_templates', []) or tags != saved_tags:
            return True
        return any(var.get().strip() != parse_env(self.workspace_dir / '.env').get(key, '') for key, var in self.env_vars.items())

    def switch_profile(self, profile_id: str):
        if profile_id == self.store.selected().id:
            return True
        if self.migrating or (self.worker and self.worker.is_alive()):
            messagebox.showwarning(tr('Операция выполняется'), tr('Сначала дождись окончания текущего действия.'), parent=self)
            self._refresh_accounts()
            return False
        if self._unsaved_settings() and not messagebox.askyesno(tr('Несохранённые изменения'),
                tr('В настройках текущего аккаунта есть несохранённые изменения. Переключиться без их сохранения?'), parent=self):
            self._refresh_accounts()
            return False
        try:
            self.store.select(profile_id)
            self.root_dir = self.store.selected().root
            self.queue_tree.selection_remove(self.queue_tree.selection())
            self._load_forms()
            self._refresh_accounts()
            self.switch('dashboard')
            self.append_log(tr('⇄ Выбран аккаунт: {0}\n').format(self.store.selected().name))
            return True
        except Exception as exc:
            self._refresh_accounts()
            messagebox.showerror(tr('Ошибка переключения'), str(exc), parent=self)
            return False

    def _on_profile_selected(self, event=None):
        idx = self.profile_selector.current()
        if idx >= 0 and idx < len(self.selector_profile_ids):
            self.switch_profile(self.selector_profile_ids[idx])

    def switch_to_selected_profile(self):
        self.switch_profile(self._selected_account_id())

    def _fetch_buffer_channels(self, dialog, channel_combo, entries, channel_cache, info_label):
        if not parse_env(self.workspace_dir / '.env').get('BUFFER_API_KEY'):
            messagebox.showwarning(tr('Нет ключа Buffer'), tr('Сначала открой «Подключения» и сохрани Buffer API Key.'), parent=dialog)
            return
        info_label.configure(text=tr('Загружаю TikTok-каналы из Buffer…'), fg=YELLOW)
        def worker():
            try:
                client = bot.BufferClient(parse_env(self.workspace_dir / '.env')['BUFFER_API_KEY'])
                options = []
                for org in client.organizations():
                    for chan in client.channels(org['id']):
                        if str(chan.get('service', '')).lower() == 'tiktok':
                            options.append({'name': str(chan.get('name') or chan.get('displayName') or ''),
                                            'display': str(chan.get('displayName') or chan.get('name') or ''),
                                            'id': str(chan['id']), 'org_name': str(org['name']), 'org_id': str(org['id'])})
                self.messages.put(('channels_found', (dialog, channel_combo, entries, channel_cache, info_label, options, None)))
            except Exception as exc:
                self.messages.put(('channels_found', (dialog, channel_combo, entries, channel_cache, info_label, [], str(exc))))
        threading.Thread(target=worker, name='buffer-channels', daemon=True).start()

    def _profile_dialog(self, *, create=True, profile_id=None):
        current = self.store.get(profile_id) if profile_id else None
        config = read_json(current.root / 'config.json', {}) if current else {}
        dialog = tk.Toplevel(self)
        dialog.title(tr('Новый TikTok-аккаунт') if create else tr('Привязка к Buffer'))
        dialog.geometry('590x540' if create else '590x480')
        dialog.resizable(False, False)
        dialog.configure(bg=PANEL)
        dialog.transient(self)
        dialog.grab_set()
        self.label(dialog, tr('НОВЫЙ TIKTOK-ПРОФИЛЬ') if create else tr('ПРИВЯЗКА: {0}').format(current.name), 15, FG, True).pack(anchor='w', padx=25, pady=(23, 8))
        self.label(dialog, tr('В Buffer должны быть подключены нужные TikTok-каналы.'), 10, MUTED).pack(anchor='w', padx=25, pady=(0, 17))
        entries = {}
        def field(label, key, default=''):
            block = tk.Frame(dialog, bg=PANEL)
            block.pack(fill='x', padx=25, pady=(0, 10))
            self.label(block, label, 10, MUTED).pack(anchor='w', pady=(0, 5))
            var = tk.StringVar(value=default)
            self.entry(block, var, 34).pack(fill='x', ipady=7)
            entries[key] = var
        if create:
            field(tr('Название в {0}, например «Второй канал»').format(PRODUCT_NAME), 'name', '')
        field(tr('TikTok-аккаунт в Buffer (@username)'), 'channel_name', config.get('target_tiktok_channel', ''))
        field(tr('Buffer Organization (оставь пустым, если она одна)'), 'org_name', config.get('buffer_organization_name', ''))
        self.label(dialog, tr('Или выбери подключённый канал автоматически:'), 10, MUTED).pack(anchor='w', padx=25, pady=(3, 5))
        picker = tk.Frame(dialog, bg=PANEL)
        picker.pack(fill='x', padx=25)
        combo = ttk.Combobox(picker, state='readonly', width=33, font=(FONT, 10))
        combo.pack(side='left', fill='x', expand=True, padx=(0, 8))
        channel_cache = {'options': [], 'selected': None}
        def choose_channel(event=None):
            index = combo.current()
            if index < 0 or index >= len(channel_cache['options']):
                return
            chosen = channel_cache['options'][index]
            entries['channel_name'].set(chosen['name'])
            entries['org_name'].set(chosen['org_name'])
            channel_cache['selected'] = chosen
        combo.bind('<<ComboboxSelected>>', choose_channel)
        def invalidate_selection(*_):
            chosen = channel_cache['selected']
            if chosen and (entries['channel_name'].get() != chosen['name'] or entries['org_name'].get() != chosen['org_name']):
                channel_cache['selected'] = None
        entries['channel_name'].trace_add('write', invalidate_selection)
        entries['org_name'].trace_add('write', invalidate_selection)
        info = self.label(dialog, tr('После выбора канал закрепится по ID — переименование не помешает.'), 9, MUTED)
        info.pack(anchor='w', padx=25, pady=(7, 10))
        self.button(picker, tr('⟳  Найти в Buffer'), lambda: self._fetch_buffer_channels(dialog, combo, entries, channel_cache, info), 'normal', 9).pack(side='right')
        result = {}
        foot = tk.Frame(dialog, bg=PANEL)
        foot.pack(side='bottom', fill='x', padx=25, pady=22)
        def accept():
            channel = entries['channel_name'].get().strip().lstrip('@')
            if not channel:
                messagebox.showwarning(tr('Нет канала'), tr('Выбери канал из Buffer или введи его имя.'), parent=dialog)
                return
            selected = channel_cache['selected']
            values = dict(name=entries['name'].get().strip() if create else current.name,
                          channel_name=channel, org_name=entries['org_name'].get().strip(),
                          channel_id=selected['id'] if selected else '',
                          org_id=selected['org_id'] if selected else '')
            if create and not values['name']:
                messagebox.showwarning(tr('Название профиля'), tr('Введи название нового профиля.'), parent=dialog)
                return
            if not selected and not messagebox.askyesno(tr('Привязка по имени'),
                    tr('Канал не выбран из списка Buffer. Привязка по имени менее надёжна. Создать/сохранить всё равно?'), parent=dialog):
                return
            result.update(values)
            dialog.destroy()
        self.button(foot, tr('✓  Создать профиль') if create else tr('✓  Сохранить привязку'), accept, 'primary').pack(side='right')
        self.button(foot, tr('Отмена'), dialog.destroy, 'ghost').pack(side='right', padx=(0, 8))
        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        dialog.wait_window()
        return result or None

    def add_profile(self):
        if self._busy_accounts():
            return
        details = self._profile_dialog(create=True)
        if not details:
            return
        try:
            profile = self.store.create(details.pop('name'), source_id=self.store.selected().id, **details)
            self._refresh_accounts()
            self.switch_profile(profile.id)
            self.append_log(tr('✓ Создан профиль «{0}» в папке {1}\n').format(profile.name, profile.root))
            messagebox.showinfo(tr('Аккаунт добавлен'), tr('Готово! Папки ready, scheduled, posted созданы.\n\n{0}\n\nСкопируй видео в ready или воспользуйся «Добавить видео».').format(profile.root), parent=self)
        except Exception as exc:
            messagebox.showerror(tr('Не удалось добавить'), str(exc), parent=self)

    def bind_profile(self):
        if self._busy_accounts():
            return
        pid = self._selected_account_id()
        details = self._profile_dialog(create=False, profile_id=pid)
        if not details:
            return
        try:
            details.pop('name', None)
            self.store.save_channel(pid, **details)
            if pid == self.store.selected().id:
                self._load_forms()
            self._refresh_accounts()
            messagebox.showinfo(tr('Сохранено'), tr('TikTok-канал привязан к профилю.'), parent=self)
        except Exception as exc:
            messagebox.showerror(tr('Ошибка привязки'), str(exc), parent=self)

    def rename_profile(self):
        if self._busy_accounts():
            return
        pid = self._selected_account_id()
        old = self.store.get(pid).name
        val = self._edit_dialog(tr('Переименовать аккаунт'), (tr('Название'),), (old,))
        if not val:
            return
        try:
            self.store.rename(pid, val[0])
            self._refresh_accounts()
            self.workspace_var.set(f'{self.store.selected().name}  •  {self.root_dir}')
        except Exception as exc:
            messagebox.showerror(tr('Ошибка'), str(exc), parent=self)

    def toggle_profile_archive(self):
        if self._busy_accounts():
            return
        pid = self._selected_account_id()
        profile = self.store.get(pid)
        state = tr('архивировать') if profile.active else tr('восстановить')
        if not messagebox.askyesno(tr('Профиль'), tr('{0} «{1}»?\n\nВсе файлы, история, очереди и настройки останутся на диске.\nАрхивные профили не участвуют в публикации для всех.').format(state.capitalize(), profile.name), parent=self):
            return
        try:
            self.store.set_active(pid, not profile.active)
            self.root_dir = self.store.selected().root
            self._load_forms()
            self._refresh_accounts()
        except Exception as exc:
            messagebox.showerror(tr('Ошибка'), str(exc), parent=self)

    def open_profile_folder(self):
        profile = self.store.get(self._selected_account_id())
        reveal(profile.root)

    def _load_forms(self):
        self.config_data = read_json(self.root_dir / "config.json", {})
        for key, (var, _typ) in self.cfg_vars.items():
            var.set(str(self.config_data.get(key, 15 if key == "file_stable_seconds" else "")))
        for t in (self.windows_tree, self.episode_tree):
            for i in t.get_children():
                t.delete(i)
        for row in self.config_data.get("daily_windows", []):
            self.windows_tree.insert("", "end", values=(row.get("start", ""), row.get("end", "")))
        for row in self.config_data.get("episode_ranges", []):
            self.episode_tree.insert("", "end", values=(row.get("from"), row.get("to"), row.get("season"), row.get("episode")))
        self.caption_box.delete("1.0", "end")
        self.caption_box.insert("1.0", "\n---\n".join(self.config_data.get("caption_templates", [])))
        self.tags_box.delete("1.0", "end")
        self.tags_box.insert("1.0", "\n".join(" ".join(tags) for tags in self.config_data.get("hashtag_sets", [])))
        env = parse_env(self.workspace_dir / ".env")
        for key, var in self.env_vars.items():
            var.set(env.get(key, ""))
        self.workspace_var.set(f"{self.store.selected().name}  •  {self.root_dir}")
        self._refresh_dashboard()

    def _folder(self, config_key: str, fallback: str) -> Path:
        value = Path(self.config_data.get(config_key, fallback))
        return value if value.is_absolute() else self.root_dir / value

    def open_folder(self, config_key: str, fallback: str):
        f = self._folder(config_key, fallback)
        f.mkdir(parents=True, exist_ok=True)
        reveal(f)

    def _choose_video_targets(self):
        profiles = self.store.list(active_only=True)
        if len(profiles) == 1:
            return [profiles[0]]
        dialog = tk.Toplevel(self)
        dialog.title(tr('Куда добавить видео?'))
        dialog.geometry('470x' + str(min(630, 190 + len(profiles) * 48)))
        dialog.resizable(False, False)
        dialog.configure(bg=PANEL)
        dialog.transient(self)
        dialog.grab_set()
        self.label(dialog, tr('ВЫБЕРИ TIKTOK-АККАУНТЫ'), 13, FG, True).pack(anchor='w', padx=22, pady=(21, 6))
        self.label(dialog, tr('Одни и те же файлы можно отправить в ready нескольких профилей.'), 10, MUTED).pack(anchor='w', padx=22, pady=(0, 15))
        checks = {}
        for p in profiles:
            check = tk.BooleanVar(value=p.id == self.store.selected().id)
            cb = tk.Checkbutton(dialog, text=f'{p.name}  •  {read_json(p.root / "config.json", {}).get("target_tiktok_channel", "—")}',
                                variable=check, bg=PANEL, fg=FG, selectcolor=PANEL2, activebackground=PANEL,
                                activeforeground=FG, font=(FONT, 11), anchor='w')
            cb.pack(fill='x', padx=22, pady=5)
            checks[p.id] = check
        result = []
        bottom = tk.Frame(dialog, bg=PANEL)
        bottom.pack(side='bottom', fill='x', padx=22, pady=18)
        def finish():
            result.extend(p for p in profiles if checks[p.id].get())
            if result:
                dialog.destroy()
            else:
                messagebox.showwarning(tr('Аккаунты'), tr('Выбери хотя бы один аккаунт.'), parent=dialog)
        self.button(bottom, tr('✓  Добавить видео'), finish, 'primary').pack(side='right')
        self.button(bottom, tr('Выбрать все'), lambda: [v.set(True) for v in checks.values()], 'ghost', 9).pack(side='left')
        self.button(bottom, tr('Отмена'), dialog.destroy, 'ghost', 9).pack(side='right', padx=(0, 8))
        dialog.wait_window()
        return result

    def add_videos(self):
        if self._busy_accounts():
            return
        targets = self._choose_video_targets()
        if not targets:
            return
        paths = filedialog.askopenfilenames(title=tr('Выбери нарезки для TikTok'),
                filetypes=[(tr('Видео'), '*.mp4 *.mov *.webm'), (tr('Все файлы'), '*.*')], parent=self)
        if not paths:
            return
        valid = []
        ignored = []
        for path in paths:
            src = Path(path)
            if src.suffix.lower() in VIDEO_EXTS:
                valid.append(src)
            else:
                ignored.append(src.name)
        if not valid:
            messagebox.showwarning(tr('Видео'), tr('Выбери MP4, MOV или WebM.'), parent=self)
            return
        jobs = []
        existing = []
        for profile in targets:
            cfg = read_json(profile.root / 'config.json', {})
            folder = Path(cfg.get('videos_folder', 'ready'))
            folder = folder if folder.is_absolute() else profile.root / folder
            for src in valid:
                dst = folder / src.name
                if src.resolve() == dst.resolve():
                    continue
                job = CopyJob(src, dst, profile.name)
                (existing if dst.exists() else jobs).append(job)
        overwrite = False
        if existing:
            names = '\n'.join(f'{job.profile_name}: {job.destination.name}' for job in existing[:7])
            overwrite = messagebox.askyesno(tr('Заменить совпадающие файлы?'),
                tr('У некоторых профилей файлы уже есть:\n\n') + names +
                ('\n…' if len(existing) > 7 else '') + tr('\n\nЗаменить существующие?'), parent=self)
        if not jobs and not overwrite:
            self.append_log(tr('Новых файлов для копирования нет.\n'))
            return
        self.switch('logs')
        self.status_var.set(tr('●  Копирование видео в профили…'))
        plan = HandoffPlan(jobs, existing, [], [])

        def work():
            try:
                done, errors = transfer(plan, overwrite=overwrite)
            except Exception as exc:
                done, errors = 0, [str(exc)]
            self.messages.put(('import_done', (done, errors, ignored)))
        self.worker = threading.Thread(target=work, name='jojo-import', daemon=True)
        self.worker.start()

    def _periodic_refresh(self):
        self._refresh_dashboard()
        self._refresh_accounts()
        self.after(5000, self._periodic_refresh)

    def _render_history(self, *, reset_scroll=False):
        query = self.history_search.get().strip().casefold()
        rows = [(iid, values) for iid, values in self.history_rows.items()
                if not query or query in values[0].casefold() or query in str(values[3])]
        if rows:
            self.history_empty.place_forget()
        else:
            self.history_empty.configure(text=tr('Нет совпадений') if query and self.history_rows else tr('Нет записей'))
            self.history_empty.place(relx=.5, rely=.5, anchor="center")
        if rows != self._visible_history:
            tree = self.queue_tree
            old_items = tree.get_children()
            anchor = None
            if old_items and not reset_scroll:
                first = min(len(old_items) - 1, round(tree.yview()[0] * len(old_items)))
                anchor = old_items[first]
            selected = set(tree.selection())
            focus = tree.focus()
            if old_items:
                tree.delete(*old_items)
            iids = []
            for index, (iid, values) in enumerate(rows):
                tree.insert("", "end", iid=iid, values=values, tags=("even" if index % 2 == 0 else "odd",))
                iids.append(iid)
            tree.selection_set([iid for iid in iids if iid in selected])
            if focus in iids:
                tree.focus(focus)
            if anchor in iids:
                tree.yview_moveto(iids.index(anchor) / len(iids))
            self._visible_history = rows
        if reset_scroll:
            self.queue_tree.yview_moveto(0)
        self._update_history_selection()

    def _refresh_dashboard(self):
        if not self.winfo_exists():
            return
        if self.root_dir != self._history_scope:
            self._history_scope = self.root_dir
            self.history_rows = {}
            self.history_search.set("")
        try:
            nums = {}
            for key, config_key in (("ready", "videos_folder"), ("scheduled", "scheduled_folder"), ("posted", "posted_folder")):
                folder = self._folder(config_key, key)
                nums[key] = sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS) if folder.exists() else 0
            state = bot.load_json(self.root_dir / "state.json", {"items": {}})
            records = state.get("items", {})
            nums["history"] = len(records)
            nums["scheduled"] = sum(1 for item in records.values() if item.get("status") in ("scheduled", "sending"))
            for k, v in nums.items():
                self.stats[k].set(str(v))
            status_labels = {
                "uploaded": tr('↑ Загружено в R2'), "submitting": tr('◷ Запрос отправлен'),
                "uncertain": tr('! Нужна сверка'), "rejected": tr('! Отклонено'),
                "sent": tr('✓ Опубликовано'), "scheduled": tr('◷ Запланировано'),
                "sending": tr('◷ Отправка'), "error": tr('! Ошибка'), "returned": tr('↩ Возвращено'),
            }
            try:
                tz = ZoneInfo(self.config_data.get("timezone", "Europe/Kyiv"))
            except (ValueError, TypeError, KeyError, OSError):
                tz = None
            rows = {}
            parts = sorted(records, key=lambda part: int(part) if part.isdecimal() else -1, reverse=True)
            for part in parts:
                item = records[part]
                status = item.get("status") or "—"
                due = str(item.get("due_at") or "")
                try:
                    if tz is not None and due:
                        due = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(tz).strftime("%d.%m.%Y  %H:%M")
                except (ValueError, OSError, OverflowError):
                    pass
                display_part = part if part.isdecimal() and int(part) < 10**18 else "—"
                rows[part] = (str(item.get("filename") or ""), status_labels.get(status, status), due, display_part)
            for number, video in bot.list_videos(self._folder('videos_folder', 'ready')):
                if str(number) not in records:
                    rows[str(number)] = (video.name, tr('Готово / ожидание файла'), '', number if number < 10**18 else '—')
            self.history_rows = rows
            self._render_history()
        except Exception as exc:
            self.status_var.set(tr('Ошибка чтения данных: {0}').format(exc))
        watching = bool(self.watch_worker and self.watch_worker.is_alive())
        self.watch_badge.configure(text=tr('● WATCH АКТИВЕН') if watching else tr('● WATCH ВЫКЛ'),
                                   fg=GREEN if watching else MUTED)
        button, hidden = (self.watch_off_btn, self.watch_on_btn) if watching else (self.watch_on_btn, self.watch_off_btn)
        if not button.winfo_manager():
            hidden.pack_forget()
            button.pack(side="right")

    def restore_selected_parts(self):
        if self._busy_changing_workspace():
            return
        selected = [int(part) for part in self.queue_tree.selection() if str(part).isdigit()]
        if not selected:
            messagebox.showinfo(tr('Вернуть в ready'), tr('Сначала выдели одну или несколько частей в таблице «История публикаций».'), parent=self)
            return
        state = read_json(self.root_dir / "state.json", {"items": {}})
        allowed = [part for part in selected if state.get("items", {}).get(str(part), {}).get("status") in ("scheduled", "sending", "uncertain", "submitting", "error")]
        if not allowed:
            messagebox.showwarning(tr('Вернуть в ready'), tr('Выбери запланированный, ошибочный или неподтверждённый ролик. Опубликованные возвращать нельзя.'), parent=self)
            return
        if not messagebox.askyesno(tr('Проверить и вернуть видео'),
                tr('Проверить {0} выбранных частей аккаунта «{1}»?\n\nУбедись, что ты удалил эти публикации из Buffer. Бот проверит их статус: действующие и найденные опубликованные посты не будут затронуты.\n\nЕсли пост не найден, файл вернётся в ready, а история возврата сохранится. Это НЕ отправит видео повторно — для этого отдельно нажми «Заполнить очередь».\n\nВНИМАНИЕ: Buffer может отдавать неполную историю старых публикаций. Прежде чем возвращать давно запланированные части, убедись, что они не вышли в TikTok.').format(len(allowed), self.store.selected().name),
                parent=self):
            return
        workspace = self.workspace_dir
        profile = self.store.selected()
        self.switch('logs')
        self.append_log(tr('\n──── ВОЗВРАТ В READY · {0} · {1:%d.%m.%Y %H:%M} ────\n').format(profile.name, datetime.now()))
        self.status_var.set(tr('●  Проверяю Buffer и возвращаю видео…'))
        def work():
            try:
                results = portfolio.restore_profile_parts(workspace, profile.id, allowed)
                summary = tr('Возвращено: {0}; пропущено: {1}.').format(len(results['restored']), len(results['skipped']))
                self.messages.put(("done", ("restore", summary, False)))
            except Exception as exc:
                self.messages.put(("done", ("restore", f"{type(exc).__name__}: {exc}", True)))
        self.worker = threading.Thread(target=work, name='jojo-restore', daemon=True)
        self.worker.start()

    def run_job(self, job: str, all_accounts: bool = False):
        if self.migrating or (self.worker and self.worker.is_alive()) or (self.watch_worker and self.watch_worker.is_alive()):
            messagebox.showwarning(tr('Задача уже идёт'), tr('Сначала дождись текущей операции или останови WATCH.'), parent=self)
            return
        profiles = self.store.list(active_only=True) if all_accounts else [self.store.selected()]
        if job == 'fill':
            scope = tr('ВСЕ {0} аккаунта(ов)').format(len(profiles)) if all_accounts else profiles[0].name
            if not messagebox.askyesno(tr('Заполнение очередей Buffer'), tr('Отправить видео в Buffer для: {0}?\n\nКаждый аккаунт использует СВОЙ TikTok-канал, очередь и расписание.').format(scope), parent=self):
                return
        self.switch('logs')
        names = {'fill': tr('Заполнение Buffer'), 'check': tr('Проверка Buffer/R2'),
                 'plan': tr('Предпросмотр плана'), 'sync': tr('Сверить Buffer')}
        heading = f"{names[job]} · {(tr('ВСЕ АККАУНТЫ') if all_accounts else profiles[0].name)}"
        self.append_log(f'\n{"─" * 55}\n{heading} · {datetime.now():%d.%m.%Y %H:%M}\n')
        self.status_var.set(tr('●  Выполняется: ') + heading)
        workspace = self.workspace_dir
        ids = [p.id for p in profiles]
        def work():
            try:
                results = portfolio.run_profiles(workspace, ids, job)
                errors = {i: e for i, e in results.items() if e}
                summary = tr('Успешно: {0} / {1}.').format(len(results) - len(errors), len(results))
                if errors:
                    summary += tr(' Ошибки: ') + '; '.join(f'{self.store.get(i).name}: {e}' for i, e in errors.items())
                self.messages.put(('done', (job, summary, bool(errors))))
            except Exception as exc:
                self.messages.put(('done', (job, f'{type(exc).__name__}: {exc}', True)))
        self.worker = threading.Thread(target=work, name=f'jojo-{job}', daemon=True)
        self.worker.start()

    def start_watch(self, all_accounts: bool = False):
        if self.migrating or (self.worker and self.worker.is_alive()) or (self.watch_worker and self.watch_worker.is_alive()):
            messagebox.showwarning('WATCH', tr('Уже идёт другая операция.'), parent=self)
            return
        profiles = self.store.list(active_only=True) if all_accounts else [self.store.selected()]
        self.watch_stop.clear()
        self.switch('logs')
        self.append_log(tr('\n──── WATCH: {0} аккаунт(ов) ────\n').format(len(profiles)))
        self.status_var.set(tr('●  WATCH работает'))
        workspace = self.workspace_dir
        ids = [p.id for p in profiles]
        def work():
            err = None
            try:
                portfolio.watch_profiles(workspace, ids, self.watch_stop)
            except Exception as exc:
                err = f'{type(exc).__name__}: {exc}'
            self.messages.put(('watch_done', err))
        self.watch_worker = threading.Thread(target=work, name='jojo-watch', daemon=True)
        self.watch_worker.start()
        self._refresh_dashboard()

    def stop_watch(self):
        if not self.watch_worker or not self.watch_worker.is_alive():
            self.status_var.set(tr('●  WATCH не запущен'))
            return
        self.watch_stop.set()
        self.append_log(tr('Запрошена остановка WATCH. Текущая загрузка, если есть, завершится перед остановкой.\n'))
        self.status_var.set(tr('●  WATCH останавливается'))

    def _flush_messages(self):
        try:
            for _ in range(200):
                typ, data = self.messages.get_nowait()
                if typ == "log":
                    self.append_log(str(data))
                elif typ in ("insights_done", "insights_error", "sync_done", "sync_error", "sync_restore_done", "sync_apply_done"):
                    self._handle_insights_message(typ, data)
                elif typ == "done":
                    name, summary, has_error = data
                    self.status_var.set(tr('●  Есть ошибки') if has_error else tr('●  Операция завершена'))
                    self.append_log(('⚠ ' if has_error else '✓ ') + summary + '\n')
                    self._refresh_dashboard()
                    self._refresh_accounts()
                elif typ == 'import_done':
                    count, errors, ignored = data
                    self.append_log(tr('✓ Скопировано файлов: {0}.\n').format(count))
                    if ignored:
                        self.append_log(tr('Пропущены имена: ') + ', '.join(ignored[:15]) + '\n')
                    if errors:
                        self.append_log(tr('Ошибки копирования:\n') + '\n'.join(errors[:20]) + '\n')
                    self.status_var.set(tr('●  Импорт завершён') if not errors else tr('●  Есть ошибки импорта'))
                    self._refresh_dashboard()
                    self._refresh_accounts()
                elif typ == 'channels_found':
                    dialog, combo, entries, cache, label, options, error = data
                    if not dialog.winfo_exists():
                        continue
                    if error:
                        label.configure(text=tr('Ошибка Buffer: ') + error[:100], fg=RED)
                    else:
                        cache['options'] = options
                        combo.configure(values=[f"{c['org_name']}  /  @{c['display']}" for c in options])
                        label.configure(text=tr('Найдено TikTok-каналов: {0}. Выбери нужный в списке.').format(len(options)),
                                        fg=GREEN if options else YELLOW)
                elif typ == "workspace_copied":
                    target, error = data
                    self.migrating = False
                    if error is None:
                        try:
                            self._activate_workspace(target)
                            self.append_log(tr('✓ Workspace скопирован, новая папка: {0}\n').format(target))
                            self.append_log(tr('Старый workspace оставлен как резервная копия.\n'))
                            self.status_var.set(tr('●  Workspace перенесён'))
                            messagebox.showinfo(tr('Перенос завершён'), tr('Теперь {0} использует:\n{1}\n\nСтарая папка сохранена. После проверки её можно удалить вручную.').format(PRODUCT_NAME, target), parent=self)
                        except Exception as exc:
                            self.status_var.set(tr('●  Не удалось переключить workspace'))
                            self.append_log(tr('✕ Ошибка переключения: {0}\n').format(exc))
                            messagebox.showerror(tr('Ошибка переключения'), str(exc), parent=self)
                    else:
                        self.status_var.set(tr('●  Ошибка переноса'))
                        self.append_log(tr('✕ Перенос не завершён: {0}\n').format(error))
                        messagebox.showerror(tr('Ошибка переноса'), error, parent=self)
                elif typ == "watch_done":
                    self.status_var.set(tr('●  WATCH выключен') if data is None else tr('●  Ошибка WATCH'))
                    if data:
                        self.append_log(f"✕ WATCH: {data}\n")
                    self._refresh_dashboard()
        except queue.Empty:
            pass
        self.after(140, self._flush_messages)

    def append_log(self, line: str):
        line = bot.redact(line)
        try:
            folder = self.home / 'logs'
            folder.mkdir(parents=True, exist_ok=True)
            logfile = folder / 'jojo.log'
            if logfile.exists() and logfile.stat().st_size > 2_000_000:
                logfile.replace(folder / 'jojo.previous.log')
            with logfile.open('a', encoding='utf-8') as stream:
                stream.write(line)
        except OSError:
            pass
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def copy_logs(self):
        self.clipboard_clear()
        self.clipboard_append(self.log_box.get("1.0", "end-1c"))

    def _edit_dialog(self, title, labels, initial):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        vals = []
        for i, (label, value) in enumerate(zip(labels, initial)):
            self.label(dialog, label, 11, MUTED).grid(row=i, column=0, padx=(20, 13), pady=12, sticky="w")
            v = tk.StringVar(value=str(value))
            vals.append(v)
            self.entry(dialog, v, 17).grid(row=i, column=1, padx=(0, 20), pady=12, ipady=8)
        result = {}
        def ok():
            result["values"] = tuple(v.get().strip() for v in vals)
            dialog.destroy()
        self.button(dialog, tr('Сохранить'), ok, "primary").grid(row=len(labels), column=0, columnspan=2, pady=(8, 19))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_window()
        return result.get("values")

    def add_window(self):
        val = self._edit_dialog(tr('Новое окно'), (tr('Начало HH:MM'), tr('Конец HH:MM')), ("11:30", "13:00"))
        if val and self._check_window(*val):
            self.windows_tree.insert("", "end", values=val)

    def edit_window(self):
        sel = self.windows_tree.selection()
        if sel:
            val = self._edit_dialog(tr('Редактирование окна'), (tr('Начало HH:MM'), tr('Конец HH:MM')), self.windows_tree.item(sel[0], "values"))
            if val and self._check_window(*val):
                self.windows_tree.item(sel[0], values=val)

    def _check_window(self, begin, end) -> bool:
        valid = bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", begin) and
                     re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end) and begin < end)
        if not valid:
            messagebox.showerror(tr('Некорректный интервал'), tr('Введи время HH:MM. Начало должно быть раньше конца.'), parent=self)
        return valid

    @staticmethod
    def remove_selected(tree):
        for item in tree.selection():
            tree.delete(item)

    def save_schedule(self):
        if self._busy_accounts():
            return
        try:
            new = dict(read_json(self.root_dir / "config.json", {}))
            for key, (var, typ) in self.cfg_vars.items():
                v = var.get().strip()
                new[key] = int(v) if typ == "int" else v
                if typ == "int" and new[key] < (0 if key == "thumbnail_offset_ms" else 1):
                    raise ValueError(tr('Поле «{0}» должно быть положительным числом').format(key))
            ZoneInfo(new["timezone"])
            windows = [self.windows_tree.item(i, "values") for i in self.windows_tree.get_children()]
            if not windows or not all(self._check_window(*v) for v in windows):
                raise ValueError(tr('Добавь хотя бы одно корректное окно публикации'))
            new["daily_windows"] = [{"start": a, "end": b} for a, b in windows]
            write_json(self.root_dir / "config.json", new)
            self.config_data = new
            messagebox.showinfo(tr('Готово'), tr('Расписание сохранено!'), parent=self)
        except Exception as exc:
            messagebox.showerror(tr('Ошибка сохранения'), str(exc), parent=self)

    def _episode_dialog(self, initial=("", "", "1", "")):
        val = self._edit_dialog(tr('Диапазон частей'), (tr('С части'), tr('По часть'), tr('Сезон'), tr('Серия')), initial)
        if not val:
            return None
        try:
            nums = tuple(map(int, val))
            if min(nums) <= 0 or nums[0] > nums[1]:
                raise ValueError()
            return nums
        except ValueError:
            messagebox.showerror(tr('Некорректный диапазон'), tr('Укажи положительные числа. Конец не может быть раньше начала.'), parent=self)
            return None

    def add_episode(self):
        val = self._episode_dialog()
        if val:
            self.episode_tree.insert("", "end", values=val)

    def edit_episode(self):
        sel = self.episode_tree.selection()
        if sel:
            val = self._episode_dialog(self.episode_tree.item(sel[0], "values"))
            if val:
                self.episode_tree.item(sel[0], values=val)

    def save_episodes(self):
        if self._busy_accounts():
            return
        rows = []
        for i in self.episode_tree.get_children():
            a, b, s, e = map(int, self.episode_tree.item(i, "values"))
            rows.append({"from": a, "to": b, "season": s, "episode": e})
        rows.sort(key=lambda x: x["from"])
        for a, b in zip(rows, rows[1:]):
            if a["to"] >= b["from"]:
                messagebox.showerror(tr('Перекрытие частей'), tr('Диапазоны {0}–{1} и {2}–{3} пересекаются.').format(a['from'], a['to'], b['from'], b['to']), parent=self)
                return
        cfg = read_json(self.root_dir / "config.json", {})
        cfg["episode_ranges"] = rows
        write_json(self.root_dir / "config.json", cfg)
        self.config_data = cfg
        messagebox.showinfo(tr('Готово'), tr('Сезоны и серии сохранены.'), parent=self)

    def save_captions(self):
        if self._busy_accounts():
            return
        texts = [t.strip() for t in re.split(r"\n\s*---\s*\n", self.caption_box.get("1.0", "end")) if t.strip()]
        tags = [line.split() for line in self.tags_box.get("1.0", "end").splitlines() if line.strip()]
        if not texts:
            messagebox.showerror(tr('Ошибка'), tr('Нужен хотя бы один шаблон.'), parent=self)
            return
        for text in texts:
            try:
                text.format(part=1, season=1, episode=1, filename=tr('Пример видео'), hashtags='#video')
            except (KeyError, ValueError, IndexError) as exc:
                messagebox.showerror(tr('Ошибка шаблона'), tr('Проверь фигурные скобки в шаблоне:\n{0}').format(exc), parent=self)
                return
        cfg = read_json(self.root_dir / "config.json", {})
        cfg.update(caption_templates=texts, hashtag_sets=tags or [[]])
        write_json(self.root_dir / "config.json", cfg)
        self.config_data = cfg
        messagebox.showinfo(tr('Готово'), tr('Подписи и хэштеги сохранены.'), parent=self)

    def _toggle_secrets(self):
        for key in SECRET_KEYS:
            self.env_entries[key].configure(show="" if self.show_secret.get() else "●")

    def save_connections(self):
        if self._busy_accounts():
            return
        values = {key: var.get().strip() for key, var in self.env_vars.items()}
        save_env(self.workspace_dir / ".env", values)
        messagebox.showinfo(tr('Готово'), tr('Ключи сохранены в локальный .env.'), parent=self)

    def open_credentials(self):
        file = self.workspace_dir / ".env"
        if not file.exists():
            messagebox.showinfo(tr('Подключения'), tr('Сначала сохрани подключения.'), parent=self)
            return
        reveal(file)

    def _busy_changing_workspace(self) -> bool:
        if self.migrating or (self.worker and self.worker.is_alive()) or (self.watch_worker and self.watch_worker.is_alive()):
            messagebox.showwarning(tr('Операция выполняется'), tr('Сначала останови WATCH и дождись завершения текущей операции.'), parent=self)
            return True
        return False

    def _activate_workspace(self, chosen: Path):
        root, _ = setup_workspace(self.home, chosen)
        self.workspace_dir = root
        self.store = ProfileStore(root)
        self.root_dir = self.store.selected().root
        self._load_forms()
        self._refresh_accounts()
        self._insights_report = None
        self._sync_report = None
        self._update_profile_filters()
        self._load_sync_prefs()
        self.switch("dashboard")

    def change_workspace(self):
        if self._busy_changing_workspace():
            return
        folder = filedialog.askdirectory(title=tr('Подключить рабочую папку {0}').format(PRODUCT_NAME),
                                         initialdir=str(self.workspace_dir.parent), parent=self)
        if not folder:
            return
        chosen = Path(folder)
        if chosen.resolve() == self.workspace_dir.resolve():
            return
        if not (chosen / "config.json").is_file():
            messagebox.showerror(tr('Это не workspace'), tr('Выбери папку с config.json. Для создания новой папки используй «Перенести на другой диск».'), parent=self)
            return
        if not (chosen / "state.json").is_file() and not messagebox.askyesno(
                tr('Нет истории публикаций'), tr('В выбранной папке нет state.json. Это может привести к повторным публикациям. Подключить?'), parent=self):
            return
        if not messagebox.askyesno(tr('Подключить workspace'), tr('Теперь работать в папке:\n{0}\n\nСтарые файлы останутся на месте — данные не копируются.').format(chosen), parent=self):
            return
        try:
            self._activate_workspace(chosen)
            self.append_log(tr('Подключён workspace: {0}\n').format(chosen))
        except Exception as exc:
            messagebox.showerror(tr('Ошибка'), str(exc), parent=self)

    def migrate_workspace(self):
        if self._busy_changing_workspace():
            return
        parent = filedialog.askdirectory(title=tr('Выбери ДИСК или папку, в которой создать новый workspace'),
                                          initialdir=str(self.workspace_dir.parent), parent=self)
        if not parent:
            return
        target = Path(parent) / f"{PRODUCT_NAME} workspace"
        if self.workspace_dir.resolve() in target.resolve().parents or target.resolve() in self.workspace_dir.resolve().parents:
            messagebox.showerror(tr('Неподходящий путь'), tr('Нельзя копировать workspace внутрь исходной папки или поверх неё.'), parent=self)
            return
        if target.exists():
            messagebox.showerror(tr('Папка уже есть'), tr('Папка уже существует:\n{0}\n\nВыбери другую папку назначения или подключи существующий workspace.').format(target), parent=self)
            return
        if not messagebox.askyesno(tr('Перенос workspace'),
                tr('Скопировать все аккаунты и данные из:\n{0}\n\nв:\n{1}\n\nВключая большие видеофайлы и историю Buffer. Старая папка останется как резервная копия.\nПри копировании нельзя запускать WATCH и другие операции. Продолжить?').format(self.workspace_dir, target), parent=self):
            return
        self.switch("logs")
        self.status_var.set(tr('●  Копирование workspace…'))
        self.append_log(tr('\nКопирование workspace: {0} → {1}\n').format(self.workspace_dir, target))
        source = self.workspace_dir
        def work():
            try:
                copy_workspace(source, target)
                self.messages.put(("workspace_copied", (target, None)))
            except Exception as exc:
                self.messages.put(("workspace_copied", (target, f"{type(exc).__name__}: {exc}")))
        self.migrating = True
        self.worker = threading.Thread(target=work, name="jojo-workspace-copy", daemon=True)
        self.worker.start()

    def confirm_close(self) -> bool:
        if self.migrating:
            messagebox.showwarning(tr('Копирование workspace'), tr('Нельзя закрывать {0}, пока копируются видео и история. Дождись завершения переноса.').format(PRODUCT_NAME), parent=self)
            return False
        if self.watch_worker and self.watch_worker.is_alive():
            if not messagebox.askyesno(tr('WATCH работает'), tr('Закрыть окно? WATCH остановится; текущая загрузка может прерваться.\n\nНадёжнее сначала остановить WATCH.'), parent=self):
                return False
            self.watch_stop.set()
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(tr('Загрузка в процессе'), tr('Загрузка ещё не завершена. Закрытие может прервать её. Закрыть?'), parent=self):
                return False
        bot.set_log_callback(None)
        return True
