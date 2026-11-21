from __future__ import annotations

from i18n import tr
import os
from contextlib import contextmanager
from pathlib import Path


class WorkspaceBusy(RuntimeError):
    pass


@contextmanager
def exclusive_workspace(workspace: Path):
    path = Path(workspace) / '.jojo.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise WorkspaceBusy(tr('Этот workspace уже обрабатывается другим экземпляром JetVideo. Закрой старую программу/WATCH.')) from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise WorkspaceBusy(tr('Этот workspace уже обрабатывается другой программой JetVideo.')) from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
