from __future__ import annotations

import gc
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, filedialog, ttk

import bot
import publisher_ui
from i18n import LANGUAGES, language, set_language, system_language, tr
from publisher_ui import ModernApp, WorkspaceSelectionRequired, bundled, BG, PANEL, PANEL2, BORDER, FG, MUTED, ACCENT, ACCENT_HOVER, FONT
from editor_ui import ModernVideoAdInserter
from guide_ui import GuideWindow, bind_tooltip, globe_icon, headset_icon
from handoff import build_plan, transfer
from app_info import APP_NAME
from keyboard_shortcuts import install_windows_shortcuts


CONNECTION_HELP = {
    'BUFFER_API_KEY': ('BUFFER_SETUP', 'buffer-api'),
    'R2_ENDPOINT': ('R2_SETUP', 'r2-credentials'),
    'R2_ACCESS_KEY_ID': ('R2_SETUP', 'r2-credentials'),
    'R2_SECRET_ACCESS_KEY': ('R2_SETUP', 'r2-credentials'),
    'R2_BUCKET_NAME': ('R2_SETUP', 'r2-bucket'),
    'R2_PUBLIC_BASE_URL': ('R2_SETUP', 'r2-public-url'),
}


class Studio(tk.Tk):
    def __init__(self, *, locale_code=None, initial_page='publisher', previous_outputs=()):
        self.language_file = publisher_ui.data_home() / 'ui-language.json'
        saved = publisher_ui.read_json(self.language_file, {})
        code = saved.get('language') if isinstance(saved, dict) else None
        set_language(locale_code or (code if isinstance(code, str) and code in LANGUAGES else system_language()))
        super().__init__()
        install_windows_shortcuts(self)
        self.title(APP_NAME)
        self.geometry(f'{min(1420, self.winfo_screenwidth()-40)}x{min(920, self.winfo_screenheight()-90)}')
        self.minsize(1080, 680)
        self.configure(bg=BG)
        self.publisher: ModernApp | None = None
        self.editor: ModernVideoAdInserter | None = None
        self.guide: GuideWindow | None = None
        self._active = None
        self._transfer_busy = False
        self._previous_outputs = previous_outputs
        self.reopen = None
        self._events = queue.Queue()
        self.after(100, self._drain_events)
        try:
            self.iconbitmap(str(bundled('icon.ico')))
        except (OSError, tk.TclError):
            pass
        self._create_shell()
        self.bind('<F1>', self.show_help)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.show(initial_page)

    def _create_shell(self):
        top = tk.Frame(self, bg=PANEL, height=77, highlightthickness=1, highlightbackground=BORDER)
        top.pack(side='top', fill='x')
        top.pack_propagate(False)
        ident = tk.Frame(top, bg=PANEL)
        ident.pack(side='left', padx=(23, 25), pady=14)
        tk.Label(ident, text='J', bg=ACCENT, fg='#101226', font=(FONT, 19, 'bold'), width=2).pack(side='left')
        detail = tk.Frame(ident, bg=PANEL)
        detail.pack(side='left', padx=12)
        tk.Label(detail, text=APP_NAME, bg=PANEL, fg=FG, font=(FONT, 14, 'bold')).pack(anchor='w')
        tk.Label(detail, text=tr('Нарезка, баннеры и публикации в TikTok'), bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor='w')
        self._build_header_actions(top)
        self.tabs = {}
        for key, title in [('publisher', tr('◉  ПУБЛИКАЦИИ')), ('editor', tr('▣  МОНТАЖ И НАРЕЗКА'))]:
            button = tk.Button(top, text=title, command=lambda value=key: self.show(value),
                               bg=PANEL2, fg=MUTED, activebackground=ACCENT_HOVER,
                               activeforeground=FG, font=(FONT, 11, 'bold'), relief='flat',
                               borderwidth=0, highlightthickness=0, padx=24, pady=13, cursor='hand2')
            button.pack(side='left', padx=5, pady=14)
            self.tabs[key] = button
        self.stage = tk.Frame(self, bg=BG)
        self.stage.pack(fill='both', expand=True)
        self.publisher_host = tk.Frame(self.stage, bg=BG)
        self.editor_host = tk.Frame(self.stage, bg=BG)
        for tab in (self.publisher_host, self.editor_host):
            tab.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _build_header_actions(self, top):
        actions = tk.Frame(top, bg=PANEL)
        actions.pack(side='right', padx=(8, 22))
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Header.TButton', background=PANEL2, foreground=FG, borderwidth=1,
                        bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2,
                        focuscolor=ACCENT, padding=0, font=(FONT, 14, 'bold'))
        style.map('Header.TButton', background=[('disabled', PANEL2), ('active', ACCENT_HOVER)],
                  foreground=[('disabled', MUTED), ('active', BG)])
        self.header_actions = {}
        for key, label, hint, command in (
            ('help', '?', tr('Справка (F1)'), self.show_help),
            ('donation', '$', tr('Поддержать разработчика (позже)'), None),
            ('support', tr('Техподдержка'), tr('Техподдержка (позже)'), None),
            ('language', tr('Язык'), tr('Язык интерфейса'), self.show_languages),
        ):
            slot = tk.Frame(actions, bg=PANEL, width=36, height=36)
            slot.pack(side='left', padx=4)
            slot.pack_propagate(False)
            button = ttk.Button(slot, text=label, command=command, style='Header.TButton',
                                state='normal' if command else 'disabled', takefocus=bool(command),
                                cursor='hand2' if command else 'arrow')
            if key in ('support', 'language'):
                draw_icon = headset_icon if key == 'support' else globe_icon
                button.icon = draw_icon(button)
                button.configure(image=button.icon, compound='image')
            button.pack(fill='both', expand=True)
            bind_tooltip(button, hint)
            self.header_actions[key] = button
        self.language_menu = tk.Menu(self.header_actions['language'], tearoff=False, bg=PANEL2, fg=FG,
                                     activebackground=ACCENT, activeforeground=BG, font=(FONT, 11))
        self.selected_language = tk.StringVar(self, value=language())
        for code, name in LANGUAGES.items():
            self.language_menu.add_radiobutton(label=name, value=code, variable=self.selected_language,
                                               command=lambda selected=code: self.change_language(selected))

    def show_languages(self):
        anchor = self.header_actions['language']
        self.selected_language.set(language())
        try:
            self.language_menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        finally:
            self.language_menu.grab_release()

    def change_language(self, code):
        self.selected_language.set(language())
        if code == language() or code not in LANGUAGES:
            return
        if self._transfer_busy or (self.editor and self.editor.busy):
            messagebox.showinfo(tr('Язык'), tr('Дождись окончания текущей обработки и переключи язык снова.'), parent=self)
            return
        if self.publisher:
            if self.publisher._busy_accounts():
                return
            if self.publisher._unsaved_settings() and not messagebox.askyesno(
                tr('Несохранённые изменения'),
                tr('В настройках есть несохранённые изменения. Сменить язык без их сохранения?'), parent=self,
            ):
                return
        if self.editor and not self.editor.save_on_exit():
            return
        try:
            publisher_ui.write_json(self.language_file, {'language': code})
        except OSError as exc:
            messagebox.showerror(tr('Язык'), tr('Не удалось сохранить язык: {0}').format(exc), parent=self)
            return
        outputs = tuple(getattr(self.editor, 'rendered_files', self._previous_outputs))
        self.reopen = {'locale_code': code, 'initial_page': self._active, 'previous_outputs': outputs}
        self.destroy()

    def show_help(self, _event=None):
        if self.guide is not None and self.guide.winfo_exists():
            self.guide.deiconify()
            self.guide.lift()
            self.guide.focus_set()
        else:
            filename = {'ru': 'README.ru.md', 'uk': 'README.uk.md', 'en': 'README.md'}[language()]
            self.guide = GuideWindow(self, bundled(filename))
        return 'break'

    def show_connection_help(self, key):
        document, section = CONNECTION_HELP[key]
        locale = language()
        suffix = '' if locale == 'en' else f'.{locale}'
        self.show_help()
        self.guide.open_section(bundled(f'docs/{document}{suffix}.md').resolve(), section)

    def show_captions_help(self):
        self.show_help()
        self.guide.open_section(self.guide.readme, 'captions-and-tags')

    def show(self, key: str):
        host = self.publisher_host if key == 'publisher' else self.editor_host
        if key == 'publisher' and self.publisher is None:
            for old in self.publisher_host.winfo_children():
                old.destroy()
            try:
                self.publisher = ModernApp(host, connection_help=self.show_connection_help,
                                           captions_help=self.show_captions_help)
                self.publisher.pack(fill='both', expand=True)
            except WorkspaceSelectionRequired:
                self._workspace_placeholder()
                self._active = 'publisher'
                self.show('editor')
                return
        elif key == 'editor' and self.editor is None:
            frame = tk.Frame(host, bg=BG)
            frame.pack(fill='both', expand=True)
            self.editor = ModernVideoAdInserter(frame)
            self.editor.on_send_to_publisher = self.handoff_rendered
            self.editor.on_select_from_ready = self.select_from_ready
            if self._previous_outputs:
                self.editor.rendered_files = list(self._previous_outputs)
                self.editor._update_badges()
        host.tkraise()
        self._active = key
        for tab, button in self.tabs.items():
            button.configure(bg=ACCENT if tab == key else PANEL2,
                             fg='#101226' if tab == key else MUTED)

    def _workspace_placeholder(self):
        for child in self.publisher_host.winfo_children():
            child.destroy()
        panel = tk.Frame(self.publisher_host, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        panel.pack(padx=70, pady=70, fill='x')
        tk.Label(panel, text=tr('Подключи рабочую папку JetVideo'), bg=PANEL, fg=FG,
                 font=(FONT, 17, 'bold')).pack(anchor='w', padx=25, pady=(25, 9))
        tk.Label(panel, text=tr('Монтаж доступен без настройки TikTok. Для публикации выбери существующий workspace или создай новый.'),
                 bg=PANEL, fg=MUTED, font=(FONT, 11), wraplength=750, justify='left').pack(anchor='w', padx=25, pady=(0, 16))
        tk.Button(panel, text=tr('Выбрать workspace'), command=lambda: self.show('publisher'),
                  bg=ACCENT, fg='#101226', font=(FONT, 11, 'bold'), relief='flat',
                  padx=18, pady=10).pack(anchor='w', padx=25, pady=(0, 22))

    def handoff_rendered(self, outputs: list[str]):
        if self._transfer_busy:
            return
        if not self.publisher:
            self.show('publisher')
            if not self.publisher:
                messagebox.showinfo(tr('Передача видео'), tr('Сначала подключи workspace JetVideo во вкладке «Публикации».'), parent=self)
                return
            self.show('editor')
        if self.publisher._busy_accounts():
            messagebox.showwarning(tr('JetVideo занят'), tr('Перед передачей дождись завершения операций с аккаунтами.'), parent=self)
            return
        targets = self.publisher._choose_video_targets()
        if not targets:
            return
        try:
            plan = build_plan(outputs, targets)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(tr('Передача видео'), tr('Не удалось проверить ready и историю профилей:\n{0}').format(exc), parent=self)
            return
        detail = (tr('Готово к передаче: {0} файл(ов).\nСовпадающие файлы: {1}.\nПропущено (имя, история или файл): {2}.\nПереименовано для JetVideo: {3}.').format(len(plan.jobs), len(plan.existing), len(plan.skipped), len(plan.renamed)))
        if plan.renamed:
            detail += '\n\n' + '\n'.join(plan.renamed[:4])
        if plan.skipped:
            detail += '\n\n' + '\n'.join(plan.skipped[:6])
            if len(plan.skipped) > 6:
                detail += '\n…'
        if not plan.jobs and not plan.existing:
            messagebox.showinfo(tr('Передача видео'), detail, parent=self)
            return
        overwrite = False
        if plan.existing:
            choice = messagebox.askyesnocancel(
                tr('Совпадающие видео в ready'), detail + tr('\n\nЗаменить совпадающие файлы?\n«Нет» — пропустить их, «Отмена» — ничего не делать.'),
                parent=self, default='no')
            if choice is None:
                return
            overwrite = choice
        if not plan.jobs and not overwrite:
            messagebox.showinfo(tr('Готово'), tr('Новых видео для передачи нет. Совпадающие файлы оставлены без изменений.'), parent=self)
            return
        if not messagebox.askyesno(tr('Подтвердить передачу'),
                 tr('Скопировать {0} готовых видео в ready выбранных аккаунтов?\n\nЭто НЕ отправит их в Buffer и не изменит историю публикаций.').format(len(plan.jobs) + (len(plan.existing) if overwrite else 0)),
                 parent=self, default='yes'):
            return
        self._transfer_busy = True
        def work():
            try:
                done, errors = transfer(plan, overwrite=overwrite)
            except Exception as exc:
                done, errors = 0, [str(exc)]
            self._events.put(("handoff_done", (done, errors, plan)))
        threading.Thread(target=work, daemon=True, name='studio-ready-transfer').start()

    def _drain_events(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == 'handoff_done':
                    self._handoff_done(*payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def select_from_ready(self):
        if not self.publisher:
            self.show('publisher')
            if not self.publisher:
                messagebox.showwarning(APP_NAME, tr('Подключи рабочую папку во вкладке «Публикации».'), parent=self)
                return
            self.show('editor')
        profile = self.publisher.store.selected()
        cfg = self.publisher.config_data
        folder = Path(cfg.get('videos_folder', 'ready'))
        folder = folder if folder.is_absolute() else profile.root / folder
        folder.mkdir(parents=True, exist_ok=True)
        paths = filedialog.askopenfilenames(
            parent=self, title=tr('Ролики из ready — {0}').format(profile.name), initialdir=str(folder),
            filetypes=[(tr('Видео'), '*.mp4 *.mov *.webm'), (tr('Все файлы'), '*.*')])
        if not paths:
            return
        added = 0
        for raw in paths:
            if raw not in self.editor.video_files:
                self.editor.video_files.append(raw)
                added += 1
        self.editor.refresh_video_list()
        self.editor._update_badges()
        self.editor._on_change()
        if added:
            self.editor.status.set(tr('Добавлено из ready: {0} видео. Аккаунт: {1}.').format(added, profile.name))

    def _handoff_done(self, done, errors, plan):
        self._transfer_busy = False
        if self.publisher:
            self.publisher._refresh_dashboard()
            self.publisher._refresh_accounts()
            self.publisher.append_log(tr('JetVideo: передано в ready {0} файл(ов). Ошибки: {1}.\n').format(done, len(errors)))
        report = tr('Скопировано в ready: {0}.\nОшибки копирования: {1}.\n\nДля публикации перейди во вкладку «Публикации» и нажми «Заполнить очередь».').format(done, len(errors))
        if errors:
            report += '\n\n' + '\n'.join(errors[:5])
        messagebox.showinfo(tr('Передача завершена'), report, parent=self)

    def _close(self):
        if self._transfer_busy:
            messagebox.showwarning(tr('Передача видео'), tr('Копирование файлов ещё идёт. Дождись завершения, прежде чем закрывать приложение.'), parent=self)
            return
        if self.editor and not self.editor.save_on_exit():
            return
        if self.publisher and not self.publisher.confirm_close():
            return
        self.destroy()

    def destroy(self):
        for callback in self.tk.splitlist(self.tk.call('after', 'info')):
            self.tk.call('after', 'cancel', callback)
        bot.set_log_callback(None)
        super().destroy()


def main():
    import sys
    if '--portable-test' in sys.argv:
        import traceback
        from smoke_test import check_portable_release
        report = Path(sys.argv[sys.argv.index('--portable-test') + 1])
        try:
            check_portable_release(report)
        except Exception:
            report.with_suffix('.error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            raise SystemExit(1)
        return
    if '--smoke-test' in sys.argv:
        from smoke_test import run
        run(Path(sys.argv[sys.argv.index('--smoke-test') + 1]), hold='--hold' in sys.argv)
        return
    try:
        options = {}
        while options is not None:
            app = Studio(**options)
            app.mainloop()
            options = app.reopen
            del app
            if options is not None:
                gc.collect()
    except (OSError, ValueError, RuntimeError) as exc:
        from bot import redact
        messagebox.showerror(tr('Не удалось открыть JetVideo'),
                             tr('Проверь рабочую папку и резервную копию настроек. Данные не сброшены.\n\n') + redact(exc))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
