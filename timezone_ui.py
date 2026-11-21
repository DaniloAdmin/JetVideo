from functools import cache
import tkinter as tk
from tkinter import ttk
from zoneinfo import available_timezones

from i18n import tr
from publisher_colors import ACCENT, BG, FG, FONT, MUTED, PANEL


@cache
def timezones():
    return tuple(sorted(available_timezones(), key=str.casefold))


class TimezoneMenu(tk.Toplevel):
    def __init__(self, anchor, value):
        super().__init__(anchor)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(anchor.winfo_toplevel())
        self.configure(bg=PANEL, padx=10, pady=9, highlightthickness=1, highlightbackground=ACCENT)
        self.anchor = anchor
        self.value = value
        self.query = tk.StringVar(self)

        tk.Label(self, text=tr('Поиск по городу или региону'), fg=MUTED, bg=PANEL,
                 font=(FONT, 10)).pack(anchor='w')
        self.search = ttk.Entry(self, textvariable=self.query, font=(FONT, 11))
        self.search.pack(fill='x', pady=(4, 8))
        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True)
        self.choices = tk.Listbox(body, width=37, height=10, bg=BG, fg=FG, relief='flat',
                                  font=(FONT, 11), selectbackground=ACCENT, selectforeground=BG,
                                  highlightthickness=0, exportselection=False, activestyle='none')
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.choices.yview)
        scroll.pack(side='right', fill='y')
        self.choices.pack(side='left', fill='both', expand=True)
        self.choices.configure(yscrollcommand=scroll.set)
        self.empty = tk.Label(body, text=tr('Часовые пояса не найдены'), fg=MUTED, bg=BG, font=(FONT, 10))

        self.query_trace = self.query.trace_add('write', self.filter_choices)
        self.filter_choices()
        self.search.bind('<Down>', self.focus_choices)
        self.choices.bind('<ButtonRelease-1>', self.choose_clicked)
        self.bind('<Return>', self.choose_selected)
        self.bind('<Escape>', self.dismiss)
        self.bind('<ButtonPress-1>', self.dismiss_outside)
        self.update_idletasks()
        width = max(anchor.winfo_width(), self.winfo_reqwidth())
        height = self.winfo_reqheight()
        x = min(anchor.winfo_rootx(), self.winfo_screenwidth() - width)
        y = anchor.winfo_rooty() + anchor.winfo_height()
        if y + height > self.winfo_screenheight():
            y = max(0, anchor.winfo_rooty() - height)
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.search.focus_force()

    def filter_choices(self, *_args):
        query = self.query.get().casefold().replace('_', ' ').replace('/', ' ').split()
        matches = [name for name in timezones()
                   if all(word in name.casefold().replace('_', ' ').replace('/', ' ') for word in query)]
        self.choices.delete(0, 'end')
        if matches:
            self.empty.place_forget()
            self.choices.insert('end', *matches)
            current = self.value.get()
            index = matches.index(current) if current in matches else 0
            self.choices.selection_set(index)
            self.choices.activate(index)
            self.choices.see(index)
        else:
            self.empty.place(relx=.5, rely=.5, anchor='center')

    def focus_choices(self, _event):
        if self.choices.size():
            self.choices.focus_set()
        return 'break'

    def choose_clicked(self, event):
        index = self.choices.nearest(event.y)
        row = self.choices.bbox(index)
        if row and row[1] <= event.y < row[1] + row[3]:
            self.value.set(self.choices.get(index))
            return self.dismiss()

    def choose_selected(self, _event):
        selected = self.choices.curselection()
        if selected:
            self.value.set(self.choices.get(selected[0]))
            return self.dismiss()
        return 'break'

    def dismiss_outside(self, event):
        if not (0 <= event.x_root - self.winfo_rootx() < self.winfo_width()
                and 0 <= event.y_root - self.winfo_rooty() < self.winfo_height()):
            return self.dismiss()

    def dismiss(self, _event=None):
        self.destroy()
        self.anchor.focus_force()
        return 'break'

    def destroy(self):
        self.query.trace_remove('write', self.query_trace)
        super().destroy()
