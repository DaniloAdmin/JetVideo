import os
from time import monotonic


def enabled():
    if os.name == 'nt':
        from ctypes import byref, windll
        from ctypes.wintypes import BOOL

        allowed = BOOL()
        if windll.user32.SystemParametersInfoW(0x1042, 0, byref(allowed), 0):
            return bool(allowed.value)
    return True


class Tween:
    def __init__(self, widget):
        self.widget = widget
        self.pending = None

    def cancel(self):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None

    def play(self, draw):
        self.cancel()
        if not enabled():
            draw(1.0)
            return

        started = monotonic()

        def tick():
            progress = min(1.0, (monotonic() - started) / .15)
            draw(1.0 - (1.0 - progress) ** 3)
            self.pending = self.widget.after(15, tick) if progress < 1.0 else None

        tick()
