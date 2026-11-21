from __future__ import annotations

from i18n import tr
import threading
import webbrowser
import time
import json
import tkinter as tk
from tkinter import ttk, messagebox

import insights
import portfolio
from publisher_colors import BG, PANEL, FG, MUTED, FONT


def _number(value):
    return '—' if value is None else f'{value:,}'.replace(',', ' ')


class InsightsMixin:
    def _build_insights(self):
        page = self.page_map['insights']
        intro = self.panel(page, tr('Аналитика TikTok'),
                           tr('Статистика опубликованных видео через Buffer API • обновление метрик раз в сутки'))
        intro.pack(fill='x', pady=(0, 13))
        top = tk.Frame(intro, bg=PANEL)
        top.pack(fill='x', padx=18, pady=(0, 12))
        self.label(top, tr('АККАУНТ'), 9, MUTED, True).pack(side='left', padx=(0, 8))
        self.insights_profile = ttk.Combobox(top, state='readonly', width=25, style='Profile.TCombobox')
        self.insights_profile.pack(side='left', padx=(0, 16))
        self.insights_profile.bind('<<ComboboxSelected>>', lambda e: self._render_insights())
        self.label(top, tr('ПЕРИОД'), 9, MUTED, True).pack(side='left', padx=(0, 8))
        self.insights_days = ttk.Combobox(top, values=[tr('7 дней'),tr('30 дней'),tr('90 дней'),tr('365 дней')],
                                          state='readonly', width=12, style='Profile.TCombobox')
        self.insights_days.set(tr('30 дней'))
        self.insights_days.pack(side='left', padx=(0, 15))
        self.insights_days.bind('<<ComboboxSelected>>', lambda e: self._render_insights())
        self.button(top, tr('⟳  Обновить все'), self.refresh_insights, 'primary').pack(side='right')
        self.button(top, '↗ Buffer', lambda: webbrowser.open('https://publish.buffer.com/')).pack(side='right', padx=8)
        self.insights_note = tk.StringVar(value=tr('Нажми «Обновить все», чтобы получить статистику из Buffer.'))
        tk.Label(intro, textvariable=self.insights_note, bg=PANEL, fg=MUTED,
                 font=(FONT, 9), anchor='w', justify='left', wraplength=900).pack(fill='x', padx=18, pady=(0, 14))
        cards = tk.Frame(page, bg=BG)
        cards.pack(fill='x', pady=(0, 14))
        self.insights_values = {}
        for i, (key, label) in enumerate([
            ('views',tr('Просмотры')),('reactions',tr('Лайки / реакции')),('comments',tr('Комментарии')),
            ('shares',tr('Отправки')),('reposts',tr('Репосты')),('reach',tr('Охват'))]):
            card = self.panel(cards)
            card.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 6, 0))
            cards.columnconfigure(i, weight=1)
            self.label(card, label, 9, MUTED).pack(anchor='w', padx=11, pady=(13, 2))
            v = tk.StringVar(value='—')
            tk.Label(card, textvariable=v, bg=PANEL, fg=FG, font=(FONT, 17, 'bold')).pack(anchor='w', padx=11, pady=(0, 12))
            self.insights_values[key] = v
        listing = self.panel(page, tr('Опубликованные видео'), tr('Сортировка по просмотрам, аккаунтам и реакции — нажми заголовок столбца'))
        listing.pack(fill='both', expand=True)
        area = tk.Frame(listing, bg=PANEL)
        area.pack(fill='both', expand=True, padx=14, pady=(0, 14))
        cols = ('account','date','part','text','views','reactions','comments','shares','reposts')
        self.insights_tree = ttk.Treeview(area, columns=cols, show='headings', style='Modern.Treeview')
        settings = [('account',tr('АККАУНТ'),115),('date',tr('ДАТА'),115),('part',tr('ЧАСТЬ'),65),
                    ('text',tr('ПОДПИСЬ'),210),('views',tr('ПРОСМОТРЫ'),100),('reactions',tr('ЛАЙКИ'),85),
                    ('comments',tr('КОММ.'),80),('shares',tr('ОТПРАВКИ'),95),('reposts',tr('РЕПОСТЫ'),90)]
        for key, heading, width in settings:
            self.insights_tree.heading(key, text=heading, command=lambda k=key: self._sort_insights(k))
            self.insights_tree.column(key, width=width, minwidth=65, anchor='e' if key in insights.METRIC_NAMES else 'w')
        sb = ttk.Scrollbar(area, orient='vertical', command=self.insights_tree.yview)
        self.insights_tree.configure(yscrollcommand=sb.set)
        self.insights_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self._insights_sort = ('date', True)
        self._insights_report = None
        self._insights_rows = []
        self._update_profile_filters()

    def _build_sync(self):
        page = self.page_map['sync']
        intro = self.panel(page, tr('Сверка очереди Buffer'),
                           tr('Поиск удалённых постов, ошибок и изменений статуса. Сканирование ничего не изменяет.'))
        intro.pack(fill='x', pady=(0, 14))
        row = tk.Frame(intro, bg=PANEL)
        row.pack(fill='x', padx=18, pady=(0, 9))
        self.button(row, tr('⟳  Проверить все аккаунты'), self.refresh_sync, 'primary').pack(side='left', padx=(0, 9))
        self.button(row, tr('↩ Вернуть выделенные в ready'), self.restore_sync_selected).pack(side='left', padx=(0, 9))
        self.button(row, tr('✓ Применить статусы Buffer'), self.apply_sync_changes).pack(side='left')
        self.sync_auto = tk.BooleanVar(value=False)
        self._auto_sync_last = time.monotonic()
        self._load_sync_prefs()
        tk.Checkbutton(intro, text=tr('Автопроверка каждые 30 минут (только чтение, без автоматического возврата)'),
                       variable=self.sync_auto, command=self._save_sync_prefs, bg=PANEL, fg=MUTED,
                       activebackground=PANEL, activeforeground=FG, selectcolor=PANEL,
                       font=(FONT, 9), cursor='hand2').pack(anchor='w', padx=19, pady=(0, 8))
        self.after(60000, self._auto_sync_tick)
        self.sync_note = tk.StringVar(value=tr('После сканирования выбери отсутствующие публикации, которые нужно восстановить.'))
        tk.Label(intro, textvariable=self.sync_note, bg=PANEL, fg=MUTED, font=(FONT, 10),
                 justify='left', anchor='w', wraplength=900).pack(fill='x', padx=18, pady=(0, 15))
        listing = self.panel(page, tr('Расхождения с историей JetVideo'),
                             tr('Пост не найден ≠ обязательно удалён в TikTok. Прежде чем возвращать его, проверь Buffer.'))
        listing.pack(fill='both', expand=True, pady=(0, 14))
        area = tk.Frame(listing, bg=PANEL)
        area.pack(fill='both', expand=True, padx=14, pady=(0, 15))
        cols = ('account', 'part', 'local', 'remote', 'issue', 'post')
        self.sync_tree = ttk.Treeview(area, columns=cols, show='headings', style='Modern.Treeview', selectmode='extended')
        for key, title, width in [('account',tr('АККАУНТ'),150), ('part',tr('ЧАСТЬ'),80), ('local',tr('В JETVIDEO'),120),
                                   ('remote',tr('В BUFFER'),140), ('issue',tr('СИТУАЦИЯ'),220), ('post',tr('ID ПОСТА'),230)]:
            self.sync_tree.heading(key, text=title)
            self.sync_tree.column(key, width=width, minwidth=70)
        sb = ttk.Scrollbar(area, orient='vertical', command=self.sync_tree.yview)
        self.sync_tree.configure(yscrollcommand=sb.set)
        self.sync_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self._sync_report = None
        self._sync_rows = {}

    def _sync_prefs_path(self):
        return self.workspace_dir / 'sync_options.json'

    def _load_sync_prefs(self):
        try:
            raw = json.loads(self._sync_prefs_path().read_text(encoding='utf-8'))
            self.sync_auto.set(raw.get('auto_scan_30m') is True)
        except (OSError, ValueError, TypeError):
            self.sync_auto.set(False)
        self._auto_sync_last = time.monotonic()

    def _save_sync_prefs(self):
        path = self._sync_prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps({'auto_scan_30m': bool(self.sync_auto.get())}), encoding='utf-8')
        temp.replace(path)
        self._auto_sync_last = time.monotonic()

    def _auto_sync_tick(self):
        if not self.winfo_exists():
            return
        if (self.sync_auto.get() and time.monotonic() - self._auto_sync_last >= 1800
                and not self.migrating
                and not (self.worker and self.worker.is_alive())
                and not (self.watch_worker and self.watch_worker.is_alive())):
            self.refresh_sync()
        self.after(60000, self._auto_sync_tick)

    def _update_profile_filters(self):
        profiles = self.store.list(active_only=True)
        self._profile_display = {tr('Все аккаунты'): None}
        for p in profiles:
            self._profile_display[f'{p.name} · {p.id}'] = p.id
        old = self.insights_profile.get() if hasattr(self, 'insights_profile') else ''
        if hasattr(self, 'insights_profile'):
            self.insights_profile.configure(values=list(self._profile_display))
            self.insights_profile.set(old if old in self._profile_display else tr('Все аккаунты'))

    def refresh_insights(self):
        if self._busy_changing_workspace():
            return
        self._update_profile_filters()
        ids = [p.id for p in self.store.list(active_only=True)]
        self.insights_note.set(tr('Загружаю посты и метрики Buffer для всех аккаунтов…'))
        workspace = self.workspace_dir
        def work():
            try:
                result = insights.fetch_insights(workspace, ids)
                self.messages.put(('insights_done', result))
            except Exception as exc:
                self.messages.put(('insights_error', str(exc)))
        self.worker = threading.Thread(target=work, daemon=True, name='studio-insights')
        self.worker.start()

    def _sort_insights(self, key):
        previous, desc = self._insights_sort
        self._insights_sort = (key, not desc if previous == key else key in ('date','views','reactions','comments','shares','reposts'))
        self._render_insights()

    def _render_insights(self):
        if not self._insights_report:
            return
        pid = self._profile_display.get(self.insights_profile.get())
        days = int(self.insights_days.get().split()[0])
        rows = insights.filter_rows(self._insights_report, pid, days)
        key, desc = self._insights_sort
        def rank(row):
            value = row.get('metrics', {}).get(key) if key in insights.METRIC_NAMES else row.get('profile_name' if key=='account' else key)
            if key == 'part':
                value = row.get('part')
            if value is None:
                return (1, 0)
            return (0, value)
        rows.sort(key=rank, reverse=desc)
        self._insights_rows = rows
        for item in self.insights_tree.get_children():
            self.insights_tree.delete(item)
        for n, row in enumerate(rows):
            m = row['metrics']
            date = str(row['date'])[:10]
            values = (row['profile_name'], date, row.get('part') or '—',
                      row['text'].replace('\n',' ')[:75], *(_number(m.get(k)) for k in ('views','reactions','comments','shares','reposts')))
            self.insights_tree.insert('', 'end', iid=str(n), values=values)
        summary = insights.summarize(rows)
        for key, var in self.insights_values.items():
            stat = summary[key]
            number = _number(stat['total'])
            if stat['total'] is not None and stat['covered'] < len(rows):
                number += '*'
            var.set(number)
        errors = self._insights_report.get('errors', {})
        freshness = [r['metrics_updated_at'] for r in rows if r.get('metrics_updated_at')]
        message = tr('Постов за {0} дней: {1} · Данные получены {2} UTC.').format(days, len(rows), self._insights_report.get('fetched_at', '')[:19].replace('T', ' '))
        if freshness:
            message += tr(' Метрики Buffer обновлялись до {0}.').format(max(freshness)[:16].replace('T', ' '))
        message += tr(' * Неполный итог: метрика есть не у всех видео.')
        if errors:
            message += tr('\nОшибки: ') + '; '.join(errors.values())[:380]
        self.insights_note.set(message)

    def refresh_sync(self):
        if self._busy_changing_workspace():
            return
        ids = [p.id for p in self.store.list(active_only=True)]
        workspace = self.workspace_dir
        self.sync_note.set(tr('Проверяю все страницы очередей Buffer, не изменяя файлы…'))
        self._auto_sync_last = time.monotonic()
        def work():
            try:
                self.messages.put(('sync_done', insights.scan_buffer(workspace, ids)))
            except Exception as exc:
                self.messages.put(('sync_error', str(exc)))
        self.worker = threading.Thread(target=work, daemon=True, name='studio-buffer-sync')
        self.worker.start()

    def _render_sync(self):
        for item in self.sync_tree.get_children():
            self.sync_tree.delete(item)
        self._sync_rows = {}
        titles = {'missing':tr('Пост отсутствует'), 'conflict':tr('Есть другая публикация'),
                  'published':tr('Уже опубликован'), 'failed':tr('Ошибка отправки'),
                  'moved':tr('Перенесён/ожидает одобрения'), 'changed':tr('Статус изменён'),
                  'unknown':tr('Неизвестное состояние')}
        for data in self._sync_report.get('profiles', {}).values():
            for row in data.get('rows', []):
                iid = str(len(self._sync_rows))
                self._sync_rows[iid] = row
                self.sync_tree.insert('', 'end', iid=iid, values=(row['profile_name'], row['part'],
                    row['local'], row['remote'], titles.get(row['category'], row['category']), row['post_id']))
        missing = sum(r['category']=='missing' for r in self._sync_rows.values())
        issues = len(self._sync_rows)
        errors = self._sync_report.get('errors', {})
        msg = tr('Проверено аккаунтов: {0}; расхождений: {1}; отсутствует постов: {2}. ').format(len(self._sync_report.get('profiles', {})), issues, missing)
        msg += tr('Для возврата выдели строки «Пост отсутствует». Переотправка — только отдельной кнопкой на главной.')
        if errors:
            msg += tr('\nОшибки проверки: ') + '; '.join(errors.values())[:450]
        self.sync_note.set(msg)

    def restore_sync_selected(self):
        if self._busy_changing_workspace():
            return
        if self._sync_report is None:
            messagebox.showinfo(tr('Синхронизация'), tr('Сначала проверь Buffer.'), parent=self)
            return
        selected = [self._sync_rows[i] for i in self.sync_tree.selection() if i in self._sync_rows]
        if not selected:
            messagebox.showinfo(tr('Возврат'), tr('Выдели отсутствующие посты в таблице (Ctrl для нескольких).'), parent=self)
            return
        if any(r['category'] != 'missing' or not r['part'].isdigit() for r in selected):
            messagebox.showwarning(tr('Возврат'), tr('Возвращать можно только строки «Пост отсутствует». Остальные публикации не изменены.'), parent=self)
            return
        grouped = {}
        for r in selected:
            grouped.setdefault(r['profile_id'], []).append(int(r['part']))
        if not messagebox.askyesno(tr('Безопасный возврат'),
            tr('Вернуть {0} видео в ready после ПОВТОРНОЙ проверки Buffer?\n\nУбедись, что эти видео не опубликованы в TikTok. Исчезновение поста из Buffer само по себе не означает, что видео не вышло.\n\nЭто не отправит видео повторно: для публикации нужно отдельно нажать «Заполнить очередь».').format(len(selected)),
            parent=self, default='no'):
            return
        ws = self.workspace_dir
        def work():
            report = {}
            for pid, parts in grouped.items():
                try:
                    report[pid] = portfolio.restore_profile_parts(ws, pid, parts)
                except Exception as exc:
                    report[pid] = {'error': str(exc)}
            self.messages.put(('sync_restore_done', report))
        self.worker = threading.Thread(target=work, daemon=True, name='studio-sync-restore')
        self.worker.start()
        self.sync_note.set(tr('Повторно проверяю Buffer и возвращаю выбранные части…'))

    def apply_sync_changes(self):
        if self._busy_changing_workspace():
            return
        if not self._sync_report or not self._sync_report.get('profiles'):
            messagebox.showinfo(tr('Синхронизация'), tr('Сначала выполни проверку Buffer.'), parent=self)
            return
        ids = list(self._sync_report['profiles'])
        if not messagebox.askyesno(tr('Подтверждение'),
             tr('Обновить статусы сохранённых постов по актуальным данным Buffer?\n\nУже опубликованные ролики будут перемещены из scheduled в posted; видео с ошибками — возвращены в ready. Резервные копии state.json сохранятся в state_backups.\n\nУдалённые посты НЕ будут автоматически возвращены в очередь. Новые посты НЕ создаются.'),
             parent=self, default='no'):
            return
        ws = self.workspace_dir
        def work():
            try:
                self.messages.put(('sync_apply_done', insights.apply_verified_states(ws, ids)))
            except Exception as exc:
                self.messages.put(('sync_error', str(exc)))
        self.worker = threading.Thread(target=work, daemon=True, name='studio-sync-apply')
        self.worker.start()
        self.sync_note.set(tr('Применяю подтверждённые статусы Buffer…'))

    def _handle_insights_message(self, kind, payload):
        if kind == 'insights_done':
            self._insights_report = payload
            self._render_insights()
            self.status_var.set(tr('●  Аналитика обновлена'))
        elif kind == 'insights_error':
            self.insights_note.set(tr('Ошибка загрузки статистики: ') + str(payload))
            self.status_var.set(tr('●  Ошибка аналитики'))
        elif kind == 'sync_done':
            self._sync_report = payload
            self._render_sync()
            self.status_var.set(tr('●  Сверка Buffer завершена'))
        elif kind == 'sync_error':
            self.sync_note.set(tr('Ошибка синхронизации: ') + str(payload))
            self.status_var.set(tr('●  Ошибка сверки Buffer'))
        elif kind == 'sync_restore_done':
            restored = sum(len(x.get('restored', [])) for x in payload.values())
            errors = [v['error'] for v in payload.values() if 'error' in v]
            self.sync_note.set(tr('Возвращено в ready: {0}. Ошибки: {1}. Нажми «Проверить все аккаунты» для повторной сверки.').format(restored, len(errors)))
            self.status_var.set(tr('●  Возврат завершён'))
            self.append_log(tr('Синхронизация: возвращено {0} видео. Ошибки: {1}\n').format(restored, errors))
            self._sync_report = None
            self._refresh_dashboard()
        elif kind == 'sync_apply_done':
            changed = sum(x.get('changed', 0) for x in payload.values())
            errors = [x['error'] for x in payload.values() if 'error' in x]
            self.sync_note.set(tr('Обновлено статусов: {0}. Ошибки: {1}. Выполни новую проверку для актуальной таблицы.').format(changed, len(errors)))
            self.status_var.set(tr('●  Статусы обновлены'))
            self.append_log(tr('Синхронизация: изменено статусов {0}. Ошибки: {1}\n').format(changed, errors))
            self._sync_report = None
            self._refresh_dashboard()
