from pathlib import Path
import gc
import hashlib
import queue
import shutil
import subprocess
import threading

import pytest

from bot import part_from_text
from i18n import language, set_language, tr
from media_files import video_id
from video_ad_inserter_core import MediaInfo, VideoAdInserter, probe_media
from video_splitter import output_name, segments_for, validate_options, run_split


@pytest.mark.parametrize('duration', ['120', '02:00', '00:02:00'])
def test_two_minute_input_formats(duration):
    assert validate_options(dict(clip_duration=duration, start_part=3, name_template='часть {part}')) == (120, 3)


@pytest.mark.parametrize('duration', ['0', '-1', 'nan', 'inf', 'hello'])
def test_invalid_duration_is_rejected(duration):
    with pytest.raises(ValueError): validate_options(dict(clip_duration=duration))


@pytest.mark.parametrize('template', ['../{part}', 'x/{part}', '{source.__class__}', '{part:999999999d}',
                                      '{missing}', 'CON.{part}', 'video{part}.', '{part!r}'])
def test_unsafe_output_names_rejected(template):
    with pytest.raises(ValueError): output_name(template, 1, 'input.mp4')


def test_names_and_part_identity():
    assert output_name('часть {part}', 7, 'source.mp4') == 'часть 7.mp4'
    assert output_name('часть', 8, 'source.mp4') == 'часть 8.mp4'
    assert output_name('{source} — {part:03d}.mp4', 9, 'Видео.mp4') == 'Видео — 009.mp4'
    assert video_id(Path('Часть 7.mp4')) == video_id(Path('7.mp4')) == 7


@pytest.mark.parametrize(('code', 'expected'), [
    ('ru', 'часть 7.mp4'), ('en', 'Part 7.mp4'), ('uk', 'частина 7.mp4'),
])
def test_localized_default_preserves_part_number(code, expected):
    current = language()
    try:
        set_language(code)
        name = output_name(tr('часть {part}'), 7, 'source.mp4')
        assert name == expected
        assert video_id(Path(name)) == part_from_text(Path(name).stem) == 7
    finally:
        set_language(current)


def test_exact_boundaries_and_tail_policy():
    info = MediaInfo(5.25, 160, 280, '24/1', True)
    jobs = segments_for('input.mp4', info, 2, 7, 'часть {part}')
    assert [(j.start, j.duration, j.part) for j in jobs] == [(0,2,7),(2,2,8),(4,1.25,9)]
    assert len(segments_for('input.mp4', info, 2, 7, 'часть {part}', False)) == 2
    assert len(segments_for('input.mp4', MediaInfo(4.000000001,160,280,'24/1',False),2,1,'{part}')) == 2


requires_ffmpeg = pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='External FFmpeg required')


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('External FFmpeg required')
    from test_multi_inserts import generate
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    source = tmp_path/'input.mp4'
    subprocess.run([ffmpeg,'-v','error','-y',
        '-f','lavfi','-i','color=c=red:s=160x280:r=24:d=2',
        '-f','lavfi','-i','color=c=lime:s=160x280:r=24:d=2',
        '-f','lavfi','-i','color=c=yellow:s=160x280:r=24:d=0.416667',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4.416667',
        '-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
        '-map','[v]','-map','3:a','-c:v','libx264','-g','240','-sc_threshold','0',
        '-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)], check=True)
    banner = tmp_path/'banner.mp4'
    generate(ffmpeg,banner,.75,'blue',880)
    return dict(source=source, banner=banner, ffmpeg=ffmpeg, ffprobe=ffprobe,
                digest=hashlib.sha256(source.read_bytes()).digest())


def options(media, tmp_path, mode='none'):
    return dict(videos=[str(media['source'])], output=str(tmp_path/'out'),
                ffmpeg=media['ffmpeg'], ffprobe=media['ffprobe'], clip_duration='2',
                name_template='часть {part}', start_part=7, keep_tail=True, split_banner_mode=mode,
                insertions=[dict(banner=str(media['banner']),time='.5',duration='.75',x=0,y=0,width=50,height=50,opacity=1)],
                banner_mode='Без удаления фона',fit_mode='Вписать баннер (без искажения)',
                include_banner_audio=True,crf=24,key_color='0x0000FF',key_similarity=.25,key_blend=.08)


def handler():
    owner = VideoAdInserter.__new__(VideoAdInserter)
    owner.events, owner.cancel_event = queue.Queue(), threading.Event()
    return owner


def pixel(media, path, t, x, y):
    cp = subprocess.run([media['ffmpeg'],'-v','error','-ss',str(t),'-i',str(path),
                        '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
                         capture_output=True,check=True)
    offset = (y*160+x)*3
    return tuple(cp.stdout[offset:offset+3])


@requires_ffmpeg
def test_plain_cut_exact_lengths_sound_and_unchanged_source(media,tmp_path):
    owner, opt = handler(), options(media,tmp_path)
    opt['insertions'] = [{'banner':'missing.mp4','time':'invalid'}]
    run_split(owner,opt)
    assert ('done',3,0,opt['output']) in list(owner.events.queue), list(owner.events.queue)
    files = [tmp_path/'out'/f'часть {i}.mp4' for i in (7,8,9)]
    for path, expected in zip(files,(2,2,5/12)):
        info=probe_media(media['ffprobe'],str(path))
        assert info.has_audio and abs(info.duration-expected)<.07, (path,info)
    assert pixel(media,files[0],.25,100,200)[0] > 220
    assert pixel(media,files[1],.25,100,200)[1] > 220
    assert hashlib.sha256(media['source'].read_bytes()).digest()==media['digest']
    assert not list((tmp_path/'out').glob('*.tmp'))


@requires_ffmpeg
def test_overlay_repeats_on_clip_local_clock_and_skips_short_tail(media,tmp_path):
    owner, opt = handler(), options(media,tmp_path,'overlay')
    run_split(owner,opt)
    events=list(owner.events.queue)
    assert ('done',3,0,opt['output']) in events, events
    first, second, tail = [tmp_path/'out'/f'часть {i}.mp4' for i in (7,8,9)]
    assert pixel(media,first,.25,30,30)[0]>220
    assert pixel(media,first,.75,30,30)[2]>220
    assert pixel(media,second,.25,30,30)[1]>220
    assert pixel(media,second,.75,30,30)[2]>220
    assert pixel(media,second,1.7,30,30)[1]>220
    assert pixel(media,tail,.2,30,30)[2]<40
    assert any(e[0]=='log' and 'пропущен' in e[1] for e in events)
    assert abs(probe_media(media['ffprobe'],str(second)).duration-2)<.07


@requires_ffmpeg
def test_freeze_banners_use_correct_source_segment(media,tmp_path):
    owner, opt = handler(), options(media,tmp_path,'freeze')
    opt['keep_tail']=False
    run_split(owner,opt)
    assert ('done',2,0,opt['output']) in list(owner.events.queue),list(owner.events.queue)
    second=tmp_path/'out'/'часть 8.mp4'
    assert abs(probe_media(media['ffprobe'],str(second)).duration-2.75)<.1
    assert pixel(media,second,.8,130,230)[1]>220
    assert pixel(media,second,.8,30,30)[2]>220
    assert pixel(media,second,1.6,130,230)[1]>220


@requires_ffmpeg
@pytest.mark.parametrize('mode', ['overlay', 'freeze'])
def test_odd_dimensions_export_with_banner_and_default_filename(media, tmp_path, mode):
    source = tmp_path / 'odd.mp4'
    subprocess.run([
        media['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=s=161x281:r=24:d=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv444p', str(source),
    ], check=True)
    owner, opt = handler(), options(media, tmp_path, mode)
    opt['videos'] = [str(source)]
    opt.pop('name_template')
    run_split(owner, opt)
    events = list(owner.events.queue)
    assert ('done', 1, 0, opt['output']) in events, events
    result = probe_media(media['ffprobe'], str(tmp_path / 'out' / 'часть 7.mp4'))
    assert (result.width, result.height) == (162, 282)
    assert abs(result.duration - (2.75 if mode == 'freeze' else 2)) < .1


def test_whole_video_keeps_a_file_created_during_encoding(tmp_path, monkeypatch):
    import video_ad_inserter_core as core

    banner = tmp_path / 'banner.mp4'
    banner.touch()
    out = tmp_path / 'out'
    final = out / 'video_with_ad.mp4'
    owner = handler()
    monkeypatch.setattr(core, 'probe_media', lambda *_: MediaInfo(2, 160, 280, '24/1', False))

    def encode(_opt, _source, target, *_args):
        final.write_bytes(b'other process')
        Path(target).write_bytes(b'render')

    monkeypatch.setattr(owner, 'make_one', encode)
    owner.worker({
        'videos': ['video.mp4'], 'ffprobe': 'ffprobe', 'output': str(out),
        'banner_mode': 'auto', 'insertions': [{'banner': str(banner), 'time': '1'}],
    })
    events = list(owner.events.queue)
    assert ('done', 0, 1, str(out)) in events, events
    assert final.read_bytes() == b'other process'
    assert list(out.iterdir()) == [final]


@requires_ffmpeg
def test_global_numbering_across_sources_and_no_overwrite(media,tmp_path):
    owner,opt = handler(),options(media,tmp_path)
    opt.update(videos=[str(media['source']),str(media['source'])],keep_tail=False,start_part=1)
    folder=tmp_path/'out';folder.mkdir()
    existing=folder/'часть 1.mp4'; existing.write_bytes(b'keep-user-file')
    run_split(owner,opt)
    assert ('done',4,0,opt['output']) in list(owner.events.queue)
    assert existing.read_bytes()==b'keep-user-file'
    assert (folder/'часть 1_2.mp4').is_file() and (folder/'часть 4.mp4').is_file()


@requires_ffmpeg
def test_failure_of_later_source_preserves_completed_parts(media,tmp_path):
    owner,opt = handler(),options(media,tmp_path)
    opt['videos'].append(str(tmp_path/'does-not-exist.mp4'))
    run_split(owner,opt)
    assert ('done',3,1,opt['output']) in list(owner.events.queue)
    assert len(list((tmp_path/'out').glob('*.mp4')))==3


@requires_ffmpeg
def test_cancel_during_encoding_cleans_only_partial_output(media,tmp_path):
    owner,opt = handler(),options(media,tmp_path)
    monitor=owner._monitor_cancel
    def cancel_when_launched(proc):
        owner.cancel_event.set()
        monitor(proc)
    owner._monitor_cancel=cancel_when_launched
    run_split(owner,opt)
    assert ('cancelled',0,0) in list(owner.events.queue)
    assert not list((tmp_path/'out').iterdir())
    assert hashlib.sha256(media['source'].read_bytes()).digest()==media['digest']


@requires_ffmpeg
def test_cancel_between_parts_keeps_finished_clip(media,tmp_path):
    owner,opt=handler(),options(media,tmp_path)
    original_put=owner.events.put
    def put(event):
        original_put(event)
        if event[0]=='rendered': owner.cancel_event.set()
    owner.events.put=put
    run_split(owner,opt)
    assert ('cancelled',1,0) in list(owner.events.queue)
    assert [p.name for p in (tmp_path/'out').iterdir()]==['часть 7.mp4']


@requires_ffmpeg
def test_silent_video_can_be_split_without_audio_stream(tmp_path):
    from test_multi_inserts import generate
    source=tmp_path/'silent.mp4'
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    generate(ffmpeg,source,2.25,'red')
    opt=options(dict(source=source,banner=tmp_path/'missing.mp4',ffmpeg=ffmpeg,ffprobe=ffprobe),tmp_path)
    owner=handler(); run_split(owner,opt)
    assert ('done',2,0,opt['output']) in list(owner.events.queue),list(owner.events.queue)
    assert not probe_media(ffprobe,str(tmp_path/'out'/'часть 7.mp4')).has_audio


@requires_ffmpeg
@pytest.mark.parametrize('mode', ['cut', 'whole'])
def test_ui_starts_export_and_persists_options(media,tmp_path,monkeypatch,mode):
    import time
    import tkinter as tk
    import editor_ui
    monkeypatch.setattr(editor_ui,'preference_path',lambda:tmp_path/'prefs.json')
    monkeypatch.setattr(editor_ui.messagebox,'showinfo',lambda *a,**k:None)
    errors=[]
    monkeypatch.setattr(editor_ui.messagebox,'showerror',lambda *a,**k:errors.append(a))
    monkeypatch.setattr(editor_ui.messagebox,'showwarning',lambda *a,**k:errors.append(a))
    gc.collect()
    root=tk.Tk();root.withdraw()
    try:
        editor=editor_ui.ModernVideoAdInserter(root)
        editor.video_files=[str(media['source'])]
        editor.refresh_video_list()
        editor.ffmpeg_path.set(media['ffmpeg'])
        editor.output_dir.set(str(tmp_path/'ui-out'))
        editor.cut_duration.set('00:00:02')
        editor.cut_name.set('Тест {part:03d}')
        editor.cut_start.set('4')
        editor.cut_tail.set(False)
        editor.export_mode.set(mode)
        if mode == 'whole':
            editor.insertions = [dict(time='0.5', banner=str(media['banner']), kind='overlay',
                                      mode='Без удаления фона', width=50, height=50)]
        editor.start_btn.invoke()
        assert editor.busy and not errors, errors
        editor.processing_thread.join(timeout=15)
        assert not editor.processing_thread.is_alive()
        deadline=time.monotonic()+3
        while editor.busy and time.monotonic()<deadline:
            root.update();time.sleep(.02)
        assert not editor.busy and not errors, errors
        expected = ['Тест 004.mp4', 'Тест 005.mp4'] if mode == 'cut' else ['input_with_ad.mp4']
        assert [Path(p).name for p in editor.rendered_files] == expected
        assert len(editor.video_list.get(0,'end'))==1
        prefs=editor_ui.load_preferences(tmp_path/'prefs.json')
        assert prefs['cut_duration']=='00:00:02' and prefs['cut_start']=='4' and not prefs['cut_tail']
        assert prefs['export_mode'] == mode
    finally:
        root.destroy()
