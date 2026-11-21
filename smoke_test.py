from pathlib import Path
import gc
import json
import tempfile
from unittest.mock import patch

from app_info import APP_NAME, PRODUCT_NAME
from i18n import LANGUAGES, language, tr


GUIDE = {
    'ru': ('README.ru.md', 'Быстрый старт', 'Нарезка видео'),
    'en': ('README.md', 'Quick start', 'Split videos'),
    'uk': ('README.uk.md', 'Швидкий старт', 'Нарізка відео'),
}
EDITOR_LABELS = {
    'ru': ('▣  МОНТАЖ И НАРЕЗКА', 'Начать нарезку', 'Без баннеров'),
    'en': ('▣  VIDEO EDITOR', 'Start splitting', 'No banners'),
    'uk': ('▣  МОНТАЖ І НАРІЗКА', 'Почати нарізку', 'Без банерів'),
}


def raise_callback_error(_app, _error_type, error, traceback):
    raise error.with_traceback(traceback)


def check_history(app):
    from publisher_ui import write_json

    publisher = app.publisher
    state_file = publisher.root_dir / 'state.json'
    original = state_file.read_bytes()
    filename = 'JetVideo sample with a filename too long for a table cell.mp4'
    records = {str(i): {'status': 'sent', 'filename': f'{i}.mp4'} for i in range(1, 40)}
    records['40'] = {'status': 'scheduled', 'filename': filename}
    try:
        write_json(state_file, {'items': records})
        publisher.nav_buttons['history'].invoke()
        app.update()
        table = publisher.queue_tree
        rows = table.get_children()
        assert len(rows) == 40
        visible_rows = sum(bool(table.bbox(row)) for row in rows)
        assert visible_rows >= 8
        assert sum(table.column(col, 'width') for col in table['columns']) <= table.winfo_width()
        table.yview_moveto(1.0)
        app.update()
        assert table.bbox(rows[-1])
        publisher.history_search.set(filename)
        app.update()
        assert table.get_children() == ('40',)
        table.focus('40')
        table.selection_set('40')
        dialog = publisher.show_history_details()
        app.update()

        def visible_text(widget):
            if widget.winfo_class() == 'Text':
                return widget.get('1.0', 'end')
            return '\n'.join(visible_text(child) for child in widget.winfo_children())

        assert filename in visible_text(dialog)
        dialog.destroy()
        return visible_rows
    finally:
        publisher.history_search.set('')
        state_file.write_bytes(original)


def check_keyboard_shortcuts(app):
    publisher = app.publisher
    publisher.nav_buttons['connections'].invoke()
    app.update()
    entry = publisher.env_entries['R2_BUCKET_NAME']
    value = publisher.env_vars['R2_BUCKET_NAME']
    original = value.get()
    pastes = []

    def count_paste(_event):
        pastes.append(True)
        return 'break'

    binding = entry.bind('<<Paste>>', count_paste, add='+')
    try:
        value.set('keyboard-test')
        entry.focus_force()
        app.update()
        entry.event_generate('<KeyPress>', state=4, keycode=ord('A'))
        assert entry.index('sel.first') == 0 and entry.index('sel.last') == len(value.get())
        entry.event_generate('<KeyPress>', state=4, keycode=ord('V'))
        assert pastes == [True]
    finally:
        entry.unbind('<<Paste>>', binding)
        value.set(original)


def check_ui(app):
    app.geometry('1080x680')
    checked = []
    assert app.title().startswith(APP_NAME)
    app.show('publisher')
    for key in app.publisher.page_map:
        app.publisher.switch(key)
        app.update()
        checked.append('publisher/' + key)
    history_rows_visible = check_history(app)
    if app.tk.call('tk', 'windowingsystem') == 'win32':
        check_keyboard_shortcuts(app)
        checked.append('publisher/keyboard-shortcuts')
    app.show('editor')
    app.update()
    editor = app.editor
    tab_name, start_label, no_banners = EDITOR_LABELS[language()]
    assert app.tabs['editor']['text'] == tab_name
    assert editor.start_btn['text'] == start_label
    assert editor.cut_fields.winfo_ismapped()
    assert editor.cut_banner_menu.get() == no_banners
    editor.cut_banner_menu.current(1)
    editor.cut_banner_menu.event_generate('<<ComboboxSelected>>')
    assert editor.cut_banners.get() == 'Наложение баннеров'
    assert editor.cut_banner_menu.get() == tr('Наложение баннеров')
    editor.cut_banners.set('Без баннеров')
    editor.export_mode.set('whole')
    app.update()
    assert not editor.cut_fields.winfo_ismapped()
    assert editor.banner_details.winfo_ismapped()
    editor.export_mode.set('cut')
    checked.append('editor/workflow')
    for show, name in ((editor.show_log, 'log'), (editor.show_settings, 'settings')):
        show()
        app.update()
        window = getattr(editor, name + '_window')
        assert window.winfo_viewable()
        window.destroy()
        checked.append('editor/' + name)
    assert set(app.tabs) == {'publisher', 'editor'}
    assert tuple(app.header_actions) == ('help', 'donation', 'support', 'language')
    for key in ('donation', 'support'):
        assert str(app.header_actions[key]['state']) == 'disabled'
        assert not app.header_actions[key]['command']
    assert [app.language_menu.entrycget(i, 'label') for i in range(3)] == list(LANGUAGES.values())
    assert app.selected_language.get() == language()
    app.header_actions['help'].invoke()
    app.update()
    guide = app.guide
    filename, quick_start, split_section = GUIDE[language()]
    assert guide.readme.name == filename
    assert PRODUCT_NAME in guide.text.get('1.0', 'end')
    assert quick_start in guide.text.get('1.0', 'end')
    assert split_section in guide.contents['values']
    for url in ('README.md', 'README.ru.md', 'README.uk.md', 'docs/SPLITTING.md', 'SECURITY.md', '.env.example', 'LICENSE'):
        guide._open(url)
        assert guide.document.name == Path(url).name
        assert tr('Не удалось открыть справку.') not in guide.text.get('1.0', 'end')
        guide.home.invoke()
        assert guide.document == guide.readme
    setup = {'ru': 'R2_SETUP.ru.md', 'en': 'R2_SETUP.md', 'uk': 'R2_SETUP.uk.md'}[language()]
    assert f'docs/{setup}' in guide.readme.read_text(encoding='utf-8-sig')
    guide._open(f'docs/{setup}')
    assert guide.document.name == setup
    check_guide_screenshots(app, guide)
    check_connection_help(app, setup)
    check_captions_help(app)
    check_timezone_menu(app)
    guide.home.invoke()
    assert not guide.text.window_names()
    app.show_help()
    assert app.guide is guide
    guide.destroy()
    checked.append('header/help')
    checked.append('header/help-screenshot-viewer')
    checked.append('publisher/connection-field-help')
    checked.append('publisher/captions-help')
    checked.append('publisher/timezone-menu')
    return checked, history_rows_visible


def check_guide_screenshots(app, guide):
    import tkinter as tk

    def settle():
        ready = tk.BooleanVar(app, False)
        app.after(140, ready.set, True)
        app.wait_variable(ready)

    assert len(guide.text.window_names()) == 9
    button, source = guide.previews[1]
    guide.geometry('560x460+10+10')
    settle()
    small_width = button.photo.width()
    assert 440 < small_width < guide.text.winfo_width()
    guide.geometry('1200x760+10+10')
    settle()
    assert 1000 < button.photo.width() < guide.text.winfo_width()
    assert button.photo.width() > small_width
    guide.text.see(str(button))
    app.update()
    assert button.winfo_viewable()
    button.invoke()
    viewer = guide.screenshot
    viewer.geometry('620x430+12+12')
    app.update()
    assert viewer.source is source
    assert viewer.photo.width() <= viewer.canvas.winfo_width()
    assert viewer.photo.height() <= viewer.canvas.winfo_height()
    fit_width = viewer.photo.width()
    viewer.geometry('1060x700+12+12')
    app.update()
    assert viewer.photo.width() > fit_width
    viewer.actual_button.invoke()
    assert (viewer.photo.width(), viewer.photo.height()) == source.size
    assert viewer.canvas.xview()[1] - viewer.canvas.xview()[0] < 1
    assert viewer.canvas.yview()[1] - viewer.canvas.yview()[0] < 1
    viewer.zoom_in_button.invoke()
    assert viewer.scale == 1.25
    viewer.zoom_out_button.invoke()
    assert viewer.scale == 1
    viewer.canvas.event_generate('<MouseWheel>', state=4, delta=120, x=120, y=120)
    assert viewer.scale == 1.25
    viewer.fit_button.invoke()
    assert viewer.canvas.xview() == viewer.canvas.yview() == (0.0, 1.0)
    viewer.destroy()
    with patch('guide_ui.webbrowser.open') as open_browser:
        guide._open('r2-images/07-s3-credentials.png', 'S3 credentials')
        app.update()
        assert guide.screenshot.title() == 'S3 credentials'
        assert guide.screenshot.photo.width() > 0
        open_browser.assert_not_called()
    guide.screenshot.destroy()


def check_connection_help(app, r2_setup):
    publisher = app.publisher
    app.show('publisher')
    publisher.nav_buttons['connections'].invoke()
    app.update()
    guide = app.guide
    buffer_setup = r2_setup.replace('R2', 'BUFFER', 1)
    assert f'docs/{buffer_setup}' in guide.readme.read_text(encoding='utf-8-sig')
    assert publisher.env_help_buttons.keys() == publisher.env_entries.keys()
    assert len(publisher.env_help_buttons) == 6
    bucket = publisher.env_vars['R2_BUCKET_NAME']
    original = bucket.get()
    bucket.set('unsaved-help-example')
    try:
        for key, document, section in (
            ('BUFFER_API_KEY', buffer_setup, 1),
            ('R2_ENDPOINT', r2_setup, 2),
            ('R2_ACCESS_KEY_ID', r2_setup, 2),
            ('R2_SECRET_ACCESS_KEY', r2_setup, 2),
            ('R2_BUCKET_NAME', r2_setup, 1),
            ('R2_PUBLIC_BASE_URL', r2_setup, 3),
        ):
            button = publisher.env_help_buttons[key]
            assert button['text'] == '?' and button.winfo_viewable()
            button.invoke()
            app.update()
            assert app.guide is guide and guide.winfo_viewable()
            assert guide.document.name == document
            assert guide.contents.current() == section
            assert guide.text.dlineinfo(guide.sections[section][1])
            assert len(guide.text.window_names()) == (5 if document == buffer_setup else 9)
            if document == buffer_setup:
                for picture, source in guide.previews[2:]:
                    picture.invoke()
                    app.update()
                    assert guide.screenshot.source is source
                    assert guide.screenshot.photo.width() > 0
        assert not guide.screenshot.winfo_exists()
        assert bucket.get() == 'unsaved-help-example'
    finally:
        bucket.set(original)
        app.show('editor')


def check_captions_help(app):
    publisher = app.publisher
    guide = app.guide
    app.show('publisher')
    publisher.nav_buttons['captions'].invoke()
    app.update()
    assert publisher.captions_help_button.winfo_viewable()
    publisher.captions_help_button.invoke()
    app.update()
    assert guide.document == guide.readme
    assert guide.contents.get() == tr('Подписи и теги')
    assert guide.text.dlineinfo(guide.sections[guide.contents.current()][1])
    content = guide.text.get('1.0', 'end')
    assert '\n{hashtags}\n---\n' in content and '7_with_ad.mp4' in content
    guide._open('#connections')
    assert guide.contents.current() == guide.anchor_sections['connections']
    guide._open('#captions-and-tags')
    assert guide.contents.get() == tr('Подписи и теги')
    guide.withdraw()


def check_timezone_menu(app):
    from zoneinfo import available_timezones
    from publisher_ui import read_json

    publisher = app.publisher
    publisher.nav_buttons['schedule'].invoke()
    app.update()
    value = publisher.cfg_vars['timezone'][0]
    config = publisher.root_dir / 'config.json'
    original = config.read_bytes()
    initial = value.get()
    button = publisher.timezone_button
    try:
        button.event_generate('<ButtonPress-1>', x=20, y=10)
        app.update()
        menu = publisher.timezone_menu
        assert menu.winfo_viewable()
        assert set(menu.choices.get(0, 'end')) == available_timezones()
        assert menu.choices.size() >= 550
        assert menu.choices.get(menu.choices.curselection()[0]) == initial
        menu.query.set('does not exist')
        app.update()
        assert menu.choices.size() == 0 and menu.empty.winfo_viewable()
        menu.query.set('new YORK')
        app.update()
        assert menu.choices.get(0, 'end') == ('America/New_York',)
        x, y, _, height = menu.choices.bbox(0)
        menu.choices.event_generate('<ButtonRelease-1>', x=x + 5, y=y + height // 2)
        app.update()
        assert not menu.winfo_exists() and app.grab_current() is None
        assert value.get() == 'America/New_York' and config.read_bytes() == original
        with patch('publisher_ui.messagebox.showinfo'), patch('publisher_ui.messagebox.showerror') as error:
            publisher.schedule_save_button.invoke()
            error.assert_not_called()
        assert read_json(config, {})['timezone'] == 'America/New_York'
        button.event_generate('<ButtonPress-1>', x=20, y=10)
        app.update()
        menu = publisher.timezone_menu
        menu.query.set('tokyo')
        menu.event_generate('<ButtonPress-1>', x=-10, y=-10,
                            rootx=menu.winfo_rootx() - 10, rooty=menu.winfo_rooty() - 10)
        app.update()
        assert not menu.winfo_exists()
        assert value.get() == 'America/New_York'
        button.event_generate('<ButtonPress-1>', x=20, y=10)
        app.update()
        menu = publisher.timezone_menu
        assert menu.focus_get() is menu.search
        menu.search.insert(0, 'tokyo')
        assert menu.choices.get(0, 'end') == ('Asia/Tokyo',)
        menu.search.event_generate('<KeyPress-Escape>')
        app.update()
        assert not menu.winfo_exists()
        assert value.get() == 'America/New_York'
    finally:
        config.write_bytes(original)
        publisher._load_forms()
        guide = app.guide
        guide.deiconify()
        app.show('editor')


def check_portable_release(report_path: Path):
    import ctypes
    import shutil
    import sys
    import time

    from PIL import Image

    from app_info import VERSION, portable_home
    from editor_ui import resolve_video_tools
    from publisher_ui import read_json
    from studio import Studio
    from video_ad_inserter_core import probe_media, run_capture

    folder = Path(sys.executable).resolve().parent
    home = portable_home()
    assert home == folder / 'JetVideo Data' and not home.exists()
    assert Path.cwd().resolve() != folder
    assert not any(shutil.which(name) for name in ('python', 'python3', 'py', 'ffmpeg', 'ffprobe'))
    runtime_path = ctypes.create_unicode_buffer(32768)
    module_filename = ctypes.windll.kernel32.GetModuleFileNameW
    module_filename.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint)
    assert module_filename(sys.dllhandle, runtime_path, len(runtime_path))
    assert Path(runtime_path.value).resolve().is_relative_to(Path(sys._MEIPASS).resolve())
    ffmpeg, ffprobe = resolve_video_tools()
    assert (Path(ffmpeg), Path(ffprobe)) == (folder / 'tools/ffmpeg.exe', folder / 'tools/ffprobe.exe')

    with patch('publisher_ui.messagebox.askyesno', return_value=True) as first_run, \
         patch.object(Studio, 'report_callback_exception', raise_callback_error):
        app = Studio(locale_code='ru')
    try:
        first_run.assert_called_once()
        assert app.publisher.root_dir == home / 'data'
        assert not any(var.get() for var in app.publisher.env_vars.values())
        assert not (home / 'data/.env').exists()
        assert read_json(home / 'data/state.json', {}) == {'items': {}}
        app.header_actions['help'].invoke()
        app.update()
        assert app.guide.winfo_viewable()
        app.guide._open('docs/BUFFER_SETUP.ru.md')
        assert len(app.guide.text.window_names()) == 5
        app.guide._open('R2_SETUP.ru.md')
        assert len(app.guide.text.window_names()) == 9
        app.guide.destroy()

        with tempfile.TemporaryDirectory(prefix='jetvideo-ffmpeg-check-') as temp:
            scratch = Path(temp)
            video = scratch / 'видео.mp4'
            banner = scratch / 'баннер.png'
            output = scratch / 'результат'
            Image.new('RGB', (24, 24), 'blue').save(banner)
            generated = run_capture([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=96x160:r=24:d=2',
                                     '-f', 'lavfi', '-i', 'sine=440:duration=2', '-c:v', 'libx264',
                                     '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(video)])
            assert generated.returncode == 0, generated.stderr
            app.show('editor')
            editor = app.editor
            editor.video_files = [str(video)]
            editor.refresh_video_list()
            editor.output_dir.set(str(output))
            editor.export_mode.set('cut')
            editor.cut_duration.set('1')
            editor.cut_name.set('Часть {part}')
            editor.cut_banners.set('Наложение баннеров')
            editor.insertions = [dict(time='0.2', duration='0.5', banner=str(banner), kind='overlay',
                                      mode='Без удаления фона', x=0, y=0, width=50, height=50)]
            with patch('editor_ui.messagebox.showinfo'), \
                 patch('editor_ui.messagebox.showwarning') as warnings, \
                 patch('editor_ui.messagebox.showerror') as errors:
                editor.start_btn.invoke()
                assert editor.busy
                deadline = time.monotonic() + 25
                while editor.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(.02)
                assert not editor.busy
                warnings.assert_not_called()
                errors.assert_not_called()
            rendered = [Path(name) for name in editor.rendered_files]
            assert [path.name for path in rendered] == ['Часть 1.mp4', 'Часть 2.mp4']
            for path in rendered:
                metadata = probe_media(ffprobe, str(path))
                assert metadata.has_audio and abs(metadata.duration - 1) < .08
            report = {'ok': True, 'version': VERSION, 'embedded_python': Path(runtime_path.value).name,
                      'system_path_only': True, 'empty_settings': True, 'workspace': 'JetVideo Data/data',
                      'different_working_directory': True, 'help': {'buffer_images': 5, 'r2_images': 9},
                      'tools': [str(Path(tool).relative_to(folder)) for tool in (ffmpeg, ffprobe)],
                      'rendered': [path.name for path in rendered], 'png_banner': True, 'audio': True}
    finally:
        app.destroy()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def run(report_path: Path, hold=False):
    import bot
    import editor_ui
    import publisher_ui
    import requests
    import ssl
    import studio

    data_path = publisher_ui.data_home()
    editor_path = editor_ui.preference_path()
    with tempfile.TemporaryDirectory(prefix='jetvideo-empty-') as temp:
        root = Path(temp)
        workspace = root / 'data'
        workspace.mkdir()
        publisher_ui.initialize_workspace(workspace)
        with patch.object(publisher_ui, 'data_home', return_value=root), \
             patch.object(publisher_ui.ModernApp, '_choose_startup_workspace', return_value=(workspace, root/'preferences.json')), \
             patch.object(studio.Studio, 'report_callback_exception', raise_callback_error), \
             patch.object(editor_ui, 'preference_path', return_value=root/'editor.json'), \
             patch.object(requests, 'post', side_effect=AssertionError('Unexpected network request')), \
             patch.object(requests, 'get', side_effect=AssertionError('Unexpected network request')), \
             patch.object(studio.messagebox, 'askyesno', side_effect=AssertionError('Unexpected confirmation')):
            ssl.create_default_context()
            client = bot.R2Client('https://example.invalid', 'MOCKACCESS', 'MOCKSECRET',
                                 'example', 'https://example.invalid')
            assert client.s3.meta.endpoint_url == 'https://example.invalid'
            client.s3.close()

            checked = {}
            history_rows_visible = {}
            app = studio.Studio(locale_code='ru')
            for code in LANGUAGES:
                assert language() == code
                checked[code], history_rows_visible[code] = check_ui(app)
                if code != 'uk':
                    app.editor.cut_name.set('Custom {part}')
                    app.editor.rendered_files = ['example.mp4']
                    app.language_menu.invoke(list(LANGUAGES).index(code) + 1)
                    assert app.reopen is not None
                    assert json.loads(app.language_file.read_text(encoding='utf-8')) == {
                        'language': app.reopen['locale_code'],
                    }
                    assert app.reopen['initial_page'] == 'editor'
                    options = app.reopen
                    del app
                    gc.collect()
                    app = studio.Studio(**options)
                    assert app.editor.cut_name.get() == 'Custom {part}'
                    assert app.editor.rendered_files == ['example.mp4']

            assert not (workspace/'.env').exists()
            assert json.loads((workspace/'state.json').read_text()) == {'items': {}}
            app.destroy()
            del app
            gc.collect()
            app = studio.Studio()
            assert language() == 'uk'
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                'ok': True, 'pages': checked['ru'], 'localized_pages': checked, 'language_persisted': True,
                'blank_credentials': True, 'queue_unchanged': True, 'tls_and_r2_client': True,
                'data_path': str(data_path), 'editor_path': str(editor_path),
                'history_visible_rows_at_1080x680': history_rows_visible,
                'video_tools': editor_ui.resolve_video_tools(),
            }, indent=2), encoding='utf-8')
            if hold:
                while True:
                    app.mainloop()
                    if app.reopen is None:
                        break
                    options = app.reopen
                    del app
                    gc.collect()
                    app = studio.Studio(**options)
            else:
                app.destroy()
