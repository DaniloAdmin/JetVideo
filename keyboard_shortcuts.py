EDIT_ACTIONS = {
    ord('A'): '<<SelectAll>>',
    ord('C'): '<<Copy>>',
    ord('V'): '<<Paste>>',
    ord('X'): '<<Cut>>',
    ord('Y'): '<<Redo>>',
    ord('Z'): '<<Undo>>',
}
SHIFT_MASK = 0x0001
MODE_SWITCH_MASK = 0x0010
ALT_MASK = 0x20000


def _handle_control_key(event, actions):
    if event.state & (SHIFT_MASK | MODE_SWITCH_MASK | ALT_MASK):
        return
    if action := actions.get(event.keycode):
        event.widget.event_generate(action)
        return 'break'


def install_windows_shortcuts(root):
    if root.tk.call('tk', 'windowingsystem') != 'win32':
        return
    for widget_class in ('Entry', 'TEntry', 'TCombobox', 'Text', 'Listbox'):
        actions = {key: event for key, event in EDIT_ACTIONS.items() if root.bind_class(widget_class, event)}
        root.bind_class(widget_class, '<Control-KeyPress>',
                        lambda event, actions=actions: _handle_control_key(event, actions), add='+')
