from pathlib import Path
import sys


APP_NAME = 'JetVideo | AutoPost'
PRODUCT_NAME = 'JetVideo'
PROJECT_NAME = 'JetVideo — Automatic Video Publisher'
EXE_NAME = 'JetVideo.exe'
VERSION = '1.0.0'
RELEASE_TAG = 'v4.3.0'


def portable_home() -> Path | None:
    if getattr(sys, 'frozen', False):
        folder = Path(sys.executable).resolve().parent
        if (folder / 'JetVideo.portable').is_file():
            return folder / 'JetVideo Data'
    return None
