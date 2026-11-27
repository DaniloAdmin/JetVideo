from pathlib import Path
import os
import shutil
import sys


APP_NAME = 'JetVideo | AutoPost'
PRODUCT_NAME = 'JetVideo'
PROJECT_NAME = 'JetVideo — Automatic Video Publisher'
EXE_NAME = 'JetVideo.exe'
VERSION = '1.1.2'
RELEASE_TAG = 'v1.1.2'


def portable_home() -> Path | None:
    if getattr(sys, 'frozen', False):
        folder = Path(sys.executable).resolve().parent
        if (folder / 'JetVideo.portable').is_file():
            return folder / 'JetVideo Data'
    return None


def resolve_video_tools(preferred: str = '') -> tuple[str, str] | None:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)).resolve()
    application = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else root
    suffix = '.exe' if os.name == 'nt' else ''
    name = 'ffmpeg' + suffix

    def paired(candidate):
        if not candidate.is_file():
            return None
        probe = candidate.with_name('ffprobe' + suffix)
        if probe.is_file():
            return str(candidate.resolve()), str(probe.resolve())
        return None

    if preferred.strip():
        return paired(Path(preferred.strip().strip('"')).expanduser())
    for directory in (application / 'tools', application, root / 'tools', root):
        if tools := paired(directory / name):
            return tools
    executable = shutil.which(name)
    return paired(Path(executable)) if executable else None
