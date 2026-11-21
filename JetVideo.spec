from PyInstaller.utils.hooks import collect_all
td, tb, ti = collect_all('tzdata')
datas = [('starter_config.json', '.'), ('icon.ico', '.'), ('README.md', '.'),
         ('README.ru.md', '.'), ('README.uk.md', '.'), ('locales', 'locales'), ('docs', 'docs'),
         ('SECURITY.md', '.'), ('.env.example', '.'), ('LICENSE', '.'), ('COPYRIGHT.md', '.'),
         ('THIRD_PARTY_NOTICES.md', '.'), ('third_party', 'third_party')] + td
a = Analysis(['studio.py'], pathex=[], binaries=tb, datas=datas,
             hiddenimports=ti, hookspath=['build_hooks'], hooksconfig={},
             runtime_hooks=[], excludes=['pytest'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='JetVideo',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
          icon='icon.ico')
