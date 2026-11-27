from pathlib import Path
import gc
import json
import tempfile
from time import monotonic, sleep
from unittest.mock import patch

from app_info import APP_NAME, PRODUCT_NAME
from i18n import LANGUAGES, language, tr


GUIDE = {
    'ru': ('README.ru.md', 'Быстрый старт', 'Нарезка видео'),
    'en': ('README.md', 'Quick start', 'Split videos'),
    'uk': ('README.uk.md', 'Швидкий старт', 'Нарізка відео'),
}
EDITOR_LABELS = {
    'ru': ('Монтаж и нарезка', 'Начать нарезку', 'Без баннеров'),
    'en': ('Video Editor', 'Start splitting', 'No banners'),
    'uk': ('Монтаж і нарізка', 'Почати нарізку', 'Без банерів'),
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


def assert_inside_window(widget):
    assert widget.winfo_viewable()
    top = widget.winfo_toplevel()
    x = widget.winfo_rootx() - top.winfo_rootx()
    y = widget.winfo_rooty() - top.winfo_rooty()
    assert 0 <= x < x + widget.winfo_width() <= top.winfo_width()
    assert 0 <= y < y + widget.winfo_height() <= top.winfo_height()


def check_navigation(app):
    def wait_for_layout(settled):
        deadline = monotonic() + 1
        while monotonic() < deadline:
            app.update()
            if settled():
                return
            sleep(.01)
        assert settled(), 'Navigation layout did not settle'

    def wait_for_host(host, hidden_host):
        wait_for_layout(lambda: host.winfo_viewable() and host.winfo_x() == 0)
        assert not hidden_host.winfo_ismapped()
        assert host.winfo_width() == app.stage.winfo_width()

    assert set(app.tabs) == {'editor', 'publisher'}
    for button in app.tabs.values():
        assert button['text'] and button['image']
    app.geometry('960x620')
    app.show('editor')
    app.update()
    app.tabs['publisher'].invoke()
    assert int(app.publisher_host.place_info()['x']) != 0
    app.geometry('980x632')
    app.update_idletasks()
    for key in ('publisher', 'editor', 'publisher', 'editor'):
        app.tabs[key].invoke()
        assert app._active == key

    app.geometry('960x620')
    wait_for_host(app.editor_host, app.publisher_host)
    assert_inside_window(app.editor.start_btn)
    assert_inside_window(app.editor.subtitle_btn)
    assert app.editor.source_card.winfo_rootx() < app.editor.options_card.winfo_rootx()

    app.tabs['publisher'].invoke()
    wait_for_host(app.publisher_host, app.editor_host)
    for key, button in app.publisher.nav_buttons.items():
        assert_inside_window(button)
        assert button['text'].strip() == app.publisher.titles[key][0] and button['image']
        button.invoke()
        assert app.publisher.active_page == key
    row = app.publisher.nav_buttons[app.publisher.active_page].master
    indicator = app.publisher.nav_indicator
    wait_for_layout(lambda: indicator.winfo_viewable() and indicator.winfo_y() == row.winfo_y() + 4)
    assert indicator.winfo_height() == row.winfo_height() - 8
    with patch('ui_motion.enabled', return_value=False):
        for key, host in (('editor', app.editor_host), ('publisher', app.publisher_host)):
            app.tabs[key].invoke()
            assert int(host.place_info()['x']) == 0


def check_ui(app):
    check_navigation(app)
    app.geometry('1080x680')
    checked = ['navigation/quick-switch-and-compact-window']
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
    assert set(app.header_actions) == {'help', 'language', 'more'}
    assert app.more_menu.index('end') == 1
    for index in range(2):
        assert str(app.more_menu.entrycget(index, 'state')) == 'disabled'
        assert not app.more_menu.entrycget(index, 'command')
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
    small_viewport = guide.text.winfo_width()
    assert 0 < small_width < small_viewport
    guide.geometry('1200x760+10+10')
    settle()
    wide_width = button.photo.width()
    wide_viewport = guide.text.winfo_width()
    assert small_width < wide_width < wide_viewport
    assert wide_width - small_width == wide_viewport - small_viewport
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
            assert button['image'] and button.winfo_viewable()
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
    from os import environ
    import shutil
    import sys
    import time
    from types import SimpleNamespace

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
        assert app._active == 'editor' and app.publisher is None
        assert not (home / 'data').exists()
        first_run.assert_not_called()
        app.tabs['publisher'].invoke()
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
                                     '-pix_fmt', 'yuv420p', '-bf', '0', '-g', '120', '-sc_threshold', '0',
                                     '-force_key_frames', '1.25', '-c:a', 'aac', '-shortest', str(video)])
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

            def export_parts():
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
                return [Path(name) for name in editor.rendered_files]

            rendered = export_parts()
            assert [path.name for path in rendered] == ['Часть 1.mp4', 'Часть 2.mp4']
            for path in rendered:
                metadata = probe_media(ffprobe, str(path))
                assert metadata.has_audio and abs(metadata.duration - 1) < .08

            editor.cut_banners.set('Без баннеров')
            editor.cut_fast.set(True)
            editor.cut_name.set('{part}')
            editor.cut_start.set('68')
            copied = export_parts()
            assert [path.name for path in copied] == ['68.mp4', '69.mp4']
            for path, duration in zip(copied, (1.25, .75)):
                metadata = probe_media(ffprobe, str(path))
                assert metadata.has_audio and abs(metadata.duration - duration) < .08

            subtitle_file = scratch / 'субтитри.srt'
            subtitle_file.write_text(
                '1\n00:00:00,250 --> 00:00:01,500\nЇї ґаджет знімає відео.\n\n'
                '2\n00:00:01,600 --> 00:00:02,300\nОн пажалуста подождёт.\n',
                encoding='utf-8-sig',
            )
            captioned = scratch / 'відео з субтитрами.mp4'
            replacement = scratch / 'інше відео.mp4'
            generated = run_capture([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=128x72:r=24:d=2.5',
                                     '-f', 'lavfi', '-i', 'sine=880:duration=2.5', '-c:v', 'libx264',
                                     '-pix_fmt', 'yuv420p', '-af', 'volume=0.005', '-c:a', 'aac',
                                     '-shortest', str(replacement)])
            assert generated.returncode == 0, generated.stderr

            def wait_editor(timeout=25):
                deadline = time.monotonic() + timeout
                while editor.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(.02)
                assert not editor.busy

            with patch('subtitle_ui.filedialog.askopenfilename', return_value=str(replacement)) as choose_file, \
                 patch('subtitle_ui.filedialog.asksaveasfilename', return_value=str(captioned)), \
                 patch('subtitle_ui.messagebox.showerror') as subtitle_errors:
                editor.subtitle_btn.invoke()
                wait_editor()
                captions = editor.subtitle_editor
                captions.top.geometry('640x490')
                app.update()
                for button in (captions.open_button, captions.transcribe_button, captions.spelling_button, captions.transcription_options,
                               captions.play_button, captions.export_button, captions.add_button, captions.delete_button,
                               captions.up_button, captions.down_button):
                    assert_inside_window(button)
                assert captions.delete_button['state'] == 'disabled'
                assert captions.source == video
                assert captions.speech_only.get()
                speech_options = captions.transcription_options.nametowidget(captions.transcription_options['menu'])
                speech_options.invoke('end')
                assert not captions.speech_only.get()
                speech_options.invoke('end')
                assert captions.speech_only.get()
                captions.open_button.invoke()
                wait_editor()
                choose_file.assert_called_once()
                assert captions.source == replacement and captions.file_label.get() == replacement.name
                assert captions.frame_size == (128, 72) and captions.image.getpixel((0, 0))[2] > 200
                assert abs(captions.info.duration - 2.5) < .08
                from subtitle_ui import LARGE_MODEL_NAME, VAD_MODEL_NAME, _whisper_tools
                whisper = _whisper_tools()
                speech_checked = whisper is not None and whisper[1].with_name(VAD_MODEL_NAME).is_file()
                large_checked = speech_checked and whisper[1].name == LARGE_MODEL_NAME
                one_click_checked = False
                if speech_checked:
                    assert captions.quality.get() == whisper[1].name
                    assert captions.language.get() == tr('Автоопределение')
                    captions.transcribe_button.invoke()
                    wait_editor()
                    subtitle_errors.assert_called_once_with(APP_NAME, tr('Речь для субтитров не найдена.'), parent=captions.top)
                    assert not captions.cues
                    subtitle_errors.reset_mock()
                if speech_checked and (speech_path := environ.get('JETVIDEO_QA_SPEECH')):
                    captions.source = None
                    choose_file.return_value = speech_path
                    captions.transcribe_button.invoke()
                    wait_editor(90)
                    assert captions.source == Path(speech_path)
                    assert captions.language.get() == tr('Автоопределение')
                    assert captions.detected_language == 'ru'
                    words = ' '.join(cue.text for cue in captions.cues).casefold()
                    if large_checked:
                        assert 'петя открыл рюкзак' in words and 'оказалось' in words, words
                    else:
                        assert 'рюкзак' in words, words
                    choose_file.return_value = str(replacement)
                    captions.open_button.invoke()
                    wait_editor()
                    one_click_checked = True
                choose_file.return_value = str(subtitle_file)
                captions.import_button.invoke()
                wait_editor()
                assert captions.cues[0].text == 'Її ґаджет знімає відео.'
                captions.listing.selection_set('1')
                app.update()
                captions.spelling_button.invoke()
                wait_editor()
                assert 'пажалуста' in [issue.word for issue in captions.spelling_issues[1]]
                assert captions.text.tag_ranges('spelling')
                point = captions.text.bbox('1.3')
                assert point is not None
                with patch.object(captions.spelling_popup, 'tk_popup') as popup:
                    captions.spelling_menu(SimpleNamespace(x=point[0] + 2, y=point[1] + 2, x_root=50, y_root=50))
                    wait_editor()
                    popup.assert_called_once()
                correction = next(index for index in range(captions.spelling_popup.index('end') + 1)
                                  if captions.spelling_popup.entrycget(index, 'label') == 'пожалуйста')
                captions.spelling_popup.invoke(correction)
                assert captions.cues[1].text == 'Он пожалуйста подождёт.'
                assert (captions.cues[1].start, captions.cues[1].end) == (1600, 2300)
                assert captions.options_menu.entrycget(captions.hold_option, 'state') == 'normal'
                captions.options_menu.invoke(captions.hold_option)
                assert [cue.end for cue in captions.cues] == [1600, round(captions.info.duration * 1000)]
                assert captions.options_menu.entrycget(captions.hold_option, 'state') == 'disabled'
                captions.listing.selection_set('0')
                app.update()
                captions.font.set('Montserrat')
                captions.y.set('24')
                captions.highlight.set(True)
                playback_checked = bool(ctypes.windll.winmm.waveOutGetNumDevs())
                if playback_checked:
                    captions.time.set(0)
                    captions.play_button.invoke()
                    deadline = time.monotonic() + 8
                    while captions.playback is not None and captions.time.get() < .5 and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.02)
                    assert captions.playback is not None and captions.playback.audio_elapsed >= .49, captions.status.get()
                    assert captions.canvas.itemcget(captions.caption_item, 'state') == 'normal'
                    captions.play_button.invoke()
                    paused_at = captions.time.get()
                    for _ in range(5):
                        app.update()
                        time.sleep(.02)
                    assert captions.playback is None and captions.time.get() == paused_at
                captions.export_button.invoke()
                wait_editor()
                assert captions.result == captioned
                captioned_info = probe_media(ffprobe, str(captioned))
                assert captioned_info.has_audio and (captioned_info.width, captioned_info.height) == (128, 72)
                captions.cut_button.invoke()
                assert editor.video_files == [str(captioned)]

                captions.show()
                captions.listing.selection_set('0')
                app.update()
                captions.text.focus_force()
                captions.text.mark_set('insert', '1.0')
                app.update()
                captions.text.event_generate('<KeyPress-Delete>')
                assert captions.text.get('1.0', 'end-1c') == 'ї ґаджет знімає відео.'
                assert captions.cues[0].text == 'Її ґаджет знімає відео.' and len(captions.cues) == 2

                remaining = captions.cues[1]
                timing = remaining.start, remaining.end
                captions.listing.focus_force()
                app.update()
                captions.listing.event_generate('<KeyPress-Delete>')
                app.update()
                assert captions.cues == [remaining] and captions.starts == [remaining.start]
                assert (captions.cues[0].start, captions.cues[0].end) == timing
                assert captions.listing.selection() == ('0',)
                assert captions.text.get('1.0', 'end-1c') == remaining.text
                assert captions.result is None and captioned.is_file()

                assert captions.delete_button['state'] == 'normal'
                captions.delete_button.invoke()
                app.update()
                assert not captions.cues and not captions.starts and not captions.listing.get_children()
                assert captions.text.get('1.0', 'end-1c') == ''
                assert captions.canvas.itemcget(captions.caption_item, 'state') == 'hidden'
                assert captions.delete_button['state'] == 'disabled'

                def start_phrase(start, end, phrase):
                    captions.add_button.invoke()
                    app.update()
                    dialog = captions.top.children['new_phrase']
                    widgets = {}
                    pending = dialog.winfo_children()
                    while pending:
                        widget = pending.pop()
                        key = ('save' if widget.winfo_class() == 'Button' and widget['text'] == tr('Сохранить')
                               else widget.winfo_name())
                        widgets[key] = widget
                        pending.extend(widget.winfo_children())
                    for field, value in (('start', start), ('end', end)):
                        widgets[field].delete(0, 'end')
                        widgets[field].insert(0, value)
                    widgets['phrase'].insert('1.0', phrase)
                    return dialog, widgets

                assert captions.up_button['state'] == captions.down_button['state'] == 'disabled'
                dialog, fields = start_phrase('00:00,800', '0.7', '')
                fields['phrase'].insert('1.0', 'Друга фраза')
                fields['save'].invoke()
                assert dialog.winfo_exists() and not captions.cues
                fields['end'].delete(0, 'end')
                fields['end'].insert(0, '1.9')
                fields['phrase'].delete('1.0', 'end')
                fields['save'].invoke()
                assert dialog.winfo_exists() and not captions.cues
                fields['phrase'].insert('1.0', 'Друга фраза')
                fields['save'].invoke()
                app.update()
                assert not dialog.winfo_exists()
                assert captions.listing.selection() == ('0',)
                assert captions.up_button['state'] == captions.down_button['state'] == 'disabled'

                dialog, fields = start_phrase('0.125', '00:00,650', 'Перша фраза')
                fields['save'].invoke()
                app.update()
                assert not dialog.winfo_exists() and app.grab_current() is captions.top
                assert captions.listing.selection() == ('0',)
                assert captions.time.get() == .125
                assert [cue.text for cue in captions.cues] == ['Перша фраза', 'Друга фраза']
                slots = [(125, 650), (800, 1900)]
                assert [(cue.start, cue.end) for cue in captions.cues] == slots
                assert captions.up_button['state'] == 'disabled' and captions.down_button['state'] == 'normal'
                captions.local_position.set(True)
                x, y, width, height = captions.video_box
                captions.move_caption(SimpleNamespace(x=x + .25 * width, y=y + .5 * height))
                captions.local_position.set(False)

                captions.down_button.invoke()
                app.update()
                assert [cue.text for cue in captions.cues] == ['Друга фраза', 'Перша фраза']
                assert [(cue.start, cue.end) for cue in captions.cues] == slots and captions.starts == [125, 800]
                assert [cue.position for cue in captions.cues] == [None, (25.0, 50.0)]
                assert captions.listing.selection() == ('1',) and captions.time.get() == .8
                assert captions.down_button['state'] == 'disabled' and captions.up_button['state'] == 'normal'
                captions.up_button.invoke()
                app.update()
                assert [cue.text for cue in captions.cues] == ['Перша фраза', 'Друга фраза']
                assert [(cue.start, cue.end) for cue in captions.cues] == slots
                assert captions.listing.selection() == ('0',)

                edited_srt = scratch / 'ручні субтитри.srt'
                with patch('subtitle_ui.filedialog.asksaveasfilename', return_value=str(edited_srt)):
                    captions.save_srt()
                from subtitles import read_srt
                assert [(cue.start, cue.end, cue.text) for cue in read_srt(edited_srt)] == [
                    (125, 650, 'Перша фраза'), (800, 1900, 'Друга фраза'),
                ]
                subtitle_errors.assert_not_called()

            from guide_ui import GuideWindow
            from music_check import MusicMatch, MusicResult
            from publisher_ui import ENV_KEYS, parse_env, save_env

            app.show('publisher')
            publisher = app.publisher
            publisher.switch('dashboard')
            app.update()
            actions = publisher.dashboard_actions
            assert actions.winfo_viewable()
            assert all(widget.cget('text').strip() != tr('Проверить музыку')
                       for widget in actions.master.winfo_children() if widget.winfo_class() == 'Button')
            menu = actions.nametowidget(actions.cget('menu'))
            music_item = next(index for index in range(menu.index('end') + 1)
                              if menu.type(index) == 'command'
                              and menu.entrycget(index, 'label') == tr('Проверить музыку'))
            env_path = publisher.workspace_dir / '.env'
            fake_keys = {key: 'test-only' for key, _ in ENV_KEYS}
            fake_keys.update(R2_ENDPOINT='https://r2.example.invalid', R2_PUBLIC_BASE_URL='https://media.example.invalid')
            save_env(env_path, fake_keys)
            ready = publisher._folder('videos_folder', 'ready')
            shutil.copy2(video, ready / 'Песня.mp4')
            shutil.copy2(rendered[0], ready / 'Без совпадений.mp4')

            menu.invoke(music_item)
            app.update()
            music_dialog = publisher.children['music_settings']
            form = music_dialog.children['credentials']
            token = form.grid_slaves(row=0, column=1)[0]
            container = form.grid_slaves(row=2, column=1)[0]
            token.insert(0, 'music-test-only')
            container.insert(0, '123')
            next(widget for widget in music_dialog.winfo_children() if widget.winfo_class() == 'Checkbutton').invoke()

            help_button = next(
                widget for parent in (music_dialog, *music_dialog.winfo_children())
                for widget in parent.winfo_children()
                if widget.winfo_class() == 'Button' and widget['text'].strip() == tr('Справка')
            )
            help_button.invoke()
            app.update()
            music_guide = next(widget for widget in music_dialog.winfo_children() if isinstance(widget, GuideWindow))
            assert music_guide.winfo_viewable() and music_guide.document == music_guide.readme
            section = music_guide.anchor_sections['music-check']
            assert music_guide.contents.current() == section
            title, start = music_guide.sections[section]
            assert title.startswith('Проверка музыки') and music_guide.text.dlineinfo(start)
            end = music_guide.sections[section + 1][1] if section + 1 < len(music_guide.sections) else 'end'
            instructions = music_guide.text.get(start, end)
            assert 'read-filescanning' in instructions and 'write-filescanning' in instructions
            assert not music_guide.text.dump(start, end, window=True, image=True)
            music_guide.destroy()
            app.update()
            assert music_dialog.winfo_viewable() and app.grab_current() is music_dialog
            assert token.get() == 'music-test-only' and container.get() == '123'
            assert not parse_env(env_path).get('ACRCLOUD_TOKEN')

            scan_button = next(
                widget for frame in music_dialog.winfo_children() for widget in frame.winfo_children()
                if widget.winfo_class() == 'Button' and widget['text'] == tr('Проверить ready')
            )
            detections = []

            def recognise(path, _stop):
                detections.append(path.name)
                if path.name == 'Песня.mp4':
                    return MusicResult(matches=(MusicMatch('Тестовый трек', 'Исполнитель', 350, 1450),))
                return MusicResult()

            def wait_for_music(worker, expected, *, timeout=15):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    app.update()
                    if not worker.is_alive() and expected in publisher.status_var.get():
                        return
                    time.sleep(.02)
                raise AssertionError(publisher.status_var.get())

            with patch('music_preflight.MusicScanner.scan', side_effect=recognise), \
                 patch('bot.Bot.resolve', side_effect=AssertionError('Buffer reached')) as buffer_resolve:
                scan_button.invoke()
                wait_for_music(publisher.worker, 'С совпадениями: 1')
                stored = parse_env(env_path)
                assert all(stored[key] == value for key, value in fake_keys.items())
                assert stored['ACR_CHECK_WATCH'] == '1' and stored['ACRCLOUD_CONTAINER_ID'] == '123'
                publisher.start_watch()
                wait_for_music(publisher.watch_worker, tr('WATCH остановлен проверкой музыки.'))
                assert publisher.watch_stop.is_set()
                buffer_resolve.assert_not_called()
            assert sorted(detections) == sorted(['Без совпадений.mp4', 'Песня.mp4'])
            log = publisher.log_box.get('1.0', 'end')
            assert 'Песня.mp4 · Найдена музыка · 00:00:00.350–00:00:01.450 · Исполнитель — Тестовый трек' in log
            assert 'Без совпадений.mp4 · Совпадений нет' in log
            assert len(list(ready.glob('*.mp4'))) == 2
            assert read_json(publisher.root_dir / 'state.json', {}) == {'items': {}}

            report = {'ok': True, 'version': VERSION, 'embedded_python': Path(runtime_path.value).name,
                      'system_path_only': True, 'empty_settings': True, 'workspace': 'JetVideo Data/data',
                      'different_working_directory': True, 'help': {'buffer_images': 5, 'r2_images': 9},
                      'tools': [str(Path(tool).relative_to(folder)) for tool in (ffmpeg, ffprobe)],
                      'rendered': [path.name for path in rendered], 'png_banner': True, 'audio': True,
                      'keyframe_parts': [path.name for path in copied], 'subtitles': True,
                      'subtitle_video_replacement': True, 'subtitle_playback': playback_checked,
                      'subtitle_speech_filter': speech_checked,
                      'subtitle_large_model': large_checked, 'subtitle_one_click_speech': one_click_checked,
                      'subtitle_spelling': True, 'subtitle_import_hold': True, 'subtitle_deletion': True,
                      'subtitle_manual_cues': True, 'music_check_watch_stop': True, 'music_help': True}
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
             patch('ui_motion.enabled', return_value=True), \
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
            assert app._active == 'editor' and app.publisher is None
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
