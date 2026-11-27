import os
import queue
import shutil
import subprocess
import threading
import pytest
import bot
import publisher_ui as ui
from profiles import ProfileStore, ProfileError
from media_files import stable_file, video_id, fingerprint
from video_ad_inserter_core import VideoAdInserter, probe_media

@pytest.fixture
def publishing(tmp_path, monkeypatch):
    ui.initialize_workspace(tmp_path)
    cfg = ui.read_json(tmp_path/'config.json', {})
    cfg.update(file_stable_seconds=0, target_tiktok_channel_id='tt', buffer_organization_id='org')
    ui.write_json(tmp_path/'config.json', cfg)
    ui.save_env(tmp_path/'.env', {key: 'MOCK_' + key for key, _ in ui.ENV_KEYS})
    posts, uploads, deleted = [], [], []
    class Buffer:
        mode = 'ok'
        def __init__(self, key): pass
        def organizations(self): return [{'id':'org', 'name':'Example'}]
        def channels(self, org): return [{'id':'tt', 'service':'tiktok', 'name':'Example'}]
        def posts(self, *args): return list(posts)
        def create_video_post(self, cid, text, url, due, thumb):
            assert ui.read_json(tmp_path/'state.json', {})['items']
            assert next(reversed(ui.read_json(tmp_path/'state.json', {})['items'].values()))['status'] == 'submitting'
            if self.mode == 'rejected': raise bot.PostRejected('Rejected')
            row = dict(id='p'+str(len(posts)), text=text, dueAt=bot.iso_utc(due), channelId=cid, status='scheduled')
            if self.mode != 'lost_without_post': posts.append(row)
            if self.mode.startswith('lost'): raise TimeoutError('lost response')
            return row
    class R2:
        def __init__(self, *args, **kwargs): pass
        def upload(self, path, part):
            uploads.append(path.name)
            return 'https://example.invalid/' + str(part), str(part)
        def delete(self, key): deleted.append(key); return True
    monkeypatch.setattr(bot, 'BufferClient', Buffer)
    monkeypatch.setattr(bot, 'R2Client', R2)
    (tmp_path/'ready'/'Моё видео.mp4').write_bytes(b'video-content')
    return tmp_path, Buffer, posts, uploads, deleted

def test_lost_accepted_response_recovered_after_restart(publishing):
    root, cls, posts, uploads, deleted = publishing
    cls.mode = 'lost_accepted'
    with pytest.raises(bot.BotError): bot.Bot(root).fill()
    assert len(posts) == 1 and not deleted
    cls.mode = 'ok'
    bot.Bot(root).fill()
    assert len(posts) == 1 and len(uploads) == 1
    assert (root/'scheduled'/'Моё видео.mp4').exists()
    assert next(iter(ui.read_json(root/'state.json', {})['items'].values()))['buffer_post_id'] == 'p0'

def test_unknown_outcome_never_retried_even_when_remote_list_empty(publishing):
    root, cls, posts, uploads, deleted = publishing
    cls.mode = 'lost_without_post'
    with pytest.raises(bot.BotError): bot.Bot(root).fill()
    cls.mode = 'ok'
    bot.Bot(root).fill()
    assert len(uploads) == 1 and not posts and not deleted
    assert next(iter(ui.read_json(root/'state.json', {})['items'].values()))['status'] == 'uncertain'

def test_same_bytes_renamed_are_blocked_for_same_account(publishing):
    root, cls, posts, uploads, deleted = publishing
    bot.Bot(root).fill()
    shutil.copyfile(root/'scheduled'/'Моё видео.mp4', root/'ready'/'Другое имя.mp4')
    bot.Bot(root).fill()
    assert len(posts) == len(uploads) == 1

def test_sent_confirmation_required_before_posted_move(publishing):
    root, cls, posts, uploads, deleted = publishing
    bot.Bot(root).fill()
    assert not list((root/'posted').iterdir())
    posts[0]['status'] = 'sent'
    bot.Bot(root).fill()
    assert (root/'posted'/'Моё видео.mp4').exists() and deleted

def test_upload_can_resume_after_crash_before_mutation(publishing):
    root, cls, posts, uploads, deleted = publishing
    video = root/'ready'/'Моё видео.mp4'
    ui.write_json(root/'state.json', {'items': {str(video_id(video)): {
        'status':'uploaded', 'filename':video.name, 'sha256':fingerprint(video),
        'caption':'Kept caption', 'r2_url':'https://example.invalid/a', 'r2_key':'a'}}})
    bot.Bot(root).fill()
    assert len(posts) == 1 and not uploads and posts[0]['text'] == 'Kept caption'

def test_explicit_rejection_is_distinct_from_uncertain(publishing):
    root, cls, posts, uploads, deleted = publishing
    cls.mode = 'rejected'
    with pytest.raises(bot.PostRejected): bot.Bot(root).fill()
    assert next(iter(ui.read_json(root/'state.json', {})['items'].values()))['status'] == 'rejected'
    assert deleted and not posts

def enable_music_gate(root):
    with (root / '.env').open('a', encoding='utf-8') as stream:
        stream.write('ACR_CHECK_WATCH="1"\nACRCLOUD_TOKEN="test-only"\n')


def test_watch_stops_before_publishing_when_music_matches(publishing, monkeypatch):
    import portfolio
    root, _, posts, uploads, _ = publishing
    enable_music_gate(root)
    stop = threading.Event()
    original = root / 'ready' / 'Моё видео.mp4'
    other = root / 'ready' / '2.mp4'
    other.write_bytes(b'other-video')
    profile = ProfileStore(root).selected()

    def blocked(ready, credentials, stop_event, log):
        assert credentials['ACRCLOUD_TOKEN'] == 'test-only'
        assert stop_event is stop
        assert set(ready.iterdir()) == {original, other}
        raise bot.MusicPreflightBlocked(f'{other.name} · 00:34–00:51')

    monkeypatch.setattr(bot, 'preflight_music', blocked)
    with pytest.raises(bot.MusicPreflightBlocked, match='00:34–00:51'):
        portfolio.watch_profiles(root, [profile.id], stop)
    assert stop.is_set()
    assert not posts and not uploads
    assert set((root / 'ready').iterdir()) == {original, other}
    assert not ui.read_json(root / 'state.json', {})['items']


@pytest.mark.parametrize('change', ['none', 'changed', 'new'])
def test_music_gate_only_publishes_approved_content(publishing, monkeypatch, change):
    root, _, posts, uploads, _ = publishing
    enable_music_gate(root)
    original = root / 'ready' / 'Моё видео.mp4'

    def check(ready, credentials, stop_event, log):
        approved = {original: fingerprint(original)}
        if change == 'changed':
            original.write_bytes(b'changed-after-music-check')
        elif change == 'new':
            (ready / '2.mp4').write_bytes(b'added-after-music-check')
        return approved

    monkeypatch.setattr(bot, 'preflight_music', check)
    if change == 'none':
        bot.Bot(root).fill()
        assert uploads == [original.name] and len(posts) == 1
        assert (root / 'scheduled' / original.name).is_file()
    else:
        name = original.name if change == 'changed' else '2.mp4'
        with pytest.raises(bot.MusicPreflightBlocked) as blocked:
            bot.Bot(root).fill()
        assert name in str(blocked.value)
        assert original.is_file() and not uploads and not posts


def test_cancel_during_music_gate_leaves_ready_unpublished(publishing, monkeypatch):
    root, _, posts, uploads, _ = publishing
    enable_music_gate(root)
    stop = threading.Event()

    def cancelled(ready, credentials, stop_event, log):
        stop_event.set()
        video = next(ready.iterdir())
        return {video: fingerprint(video)}

    monkeypatch.setattr(bot, 'preflight_music', cancelled)
    with pytest.raises(bot.MusicCheckCancelled):
        bot.Bot(root, stop_event=stop).fill()
    assert stop.is_set() and not uploads and not posts

def test_bad_timezone_blocks_instead_of_using_system_timezone(publishing):
    root, *_ = publishing
    instance = bot.Bot(root)
    instance.config['timezone'] = 'invalid/zone'
    with pytest.raises(bot.BotError): instance.local_tz()

def test_generic_caption_without_episodes():
    assert bot.caption_for({'caption_templates':['{filename} {hashtags}'], 'hashtag_sets':[[]]}, 1, 'Обычный ролик.mp4') == 'Обычный ролик'
    assert bot.caption_for({'caption_templates':['Часть {part}: {season}/{episode}'], 'default_episode':{'season':2,'episode':3}}, 7) == 'Часть 7: 2/3'

def test_new_or_empty_files_wait(tmp_path):
    path = tmp_path/'new.mp4'
    path.write_bytes(b'partial')
    with pytest.raises(OSError):
        with stable_file(path, 15): pass
    path.write_bytes(b'')
    with pytest.raises(OSError):
        with stable_file(path, 0): pass

@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing semantics')
def test_file_open_for_copy_cannot_be_uploaded(tmp_path):
    path = tmp_path/'copy.mp4'
    with path.open('wb') as stream:
        stream.write(b'partial'); stream.flush()
        with pytest.raises(OSError):
            with stable_file(path, 0): pass

def test_profile_remove_preserves_all_files_and_prevents_shared_folders(tmp_path):
    ui.initialize_workspace(tmp_path)
    store = ProfileStore(tmp_path)
    second = store.create('Second')
    media = second.root/'ready'/'example.mp4'; media.write_bytes(b'keep')
    cfg = ui.read_json(second.root/'config.json', {})
    cfg['videos_folder'] = str(tmp_path/'ready')
    with pytest.raises(ProfileError): store.save_folders(second.id, cfg)
    store.remove(second.id)
    assert media.read_bytes() == b'keep' and (second.root/'state.json').exists()
    assert len(ProfileStore(tmp_path).list()) == 1

def test_watch_retries_account_after_transient_channel_failure(tmp_path, monkeypatch):
    import portfolio
    ui.initialize_workspace(tmp_path)
    store = ProfileStore(tmp_path)
    first, second = store.selected(), store.create('Second')
    calls, filled = {}, []
    stop = threading.Event()
    class Instance:
        config = {'watch_interval_minutes':1}
        def __init__(self, root, **kwargs): self.pid = kwargs['profile_id']
        def resolve(self):
            calls[self.pid] = calls.get(self.pid,0)+1
            if self.pid == second.id and calls[self.pid] == 1: raise TimeoutError('offline')
            self.channel = {'id':self.pid}
        def fill(self):
            filled.append(self.pid)
            if len(filled) == 2: stop.set()
    monkeypatch.setattr(portfolio.bot,'Bot',Instance)
    portfolio.watch_profiles(tmp_path,[first.id,second.id],stop)
    assert filled == [first.id,second.id]

def test_incomplete_pagination_is_not_absence(monkeypatch):
    client = bot.BufferClient('MOCK')
    monkeypatch.setattr(client, 'gql', lambda *a: {'posts':{'edges':[]}})
    with pytest.raises(bot.BotError): client.posts('o','c',['sent'])

def test_cancellation_terminates_active_child_process():
    import sys
    handler = VideoAdInserter.__new__(VideoAdInserter)
    handler.cancel_event = threading.Event()
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        handler._monitor_cancel(proc)
        handler.cancel_event.set()
        proc.wait(timeout=5)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None: proc.kill(); proc.wait()

@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='External FFmpeg required')
def test_multiple_moving_overlays_preserve_duration_and_source(tmp_path):
    from test_multi_inserts import generate
    source, banner = tmp_path/'source.mp4', tmp_path/'banner.mp4'
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    generate(ffmpeg, source, 2.4, 'red', 440)
    generate(ffmpeg, banner, 0.6, 'blue')
    original = source.read_bytes()
    handler = VideoAdInserter.__new__(VideoAdInserter)
    handler.events, handler.cancel_event = queue.Queue(), threading.Event()
    entries = [dict(time=0, banner=str(banner), kind='overlay', width=40, height=30, x=5,y=10, opacity=0.5, duration=1),
               dict(time=1, banner=str(banner), kind='overlay', width=25,height=25,x=60,y=60,opacity=1,duration=1)]
    handler.worker(dict(videos=[str(source)], insertions=entries, output=str(tmp_path/'out'),
        banner_mode='Без удаления фона',fit_mode='Вписать баннер (без искажения)',key_color='0x0000FF',
        key_similarity=.25,key_blend=.08,crf=24,include_banner_audio=True,ffmpeg=ffmpeg,ffprobe=ffprobe))
    assert ('done',1,0,str(tmp_path/'out')) in list(handler.events.queue), list(handler.events.queue)
    result = probe_media(ffprobe,str(tmp_path/'out'/'source_with_ad.mp4'))
    assert abs(result.duration - 2.4) < .15 and result.has_audio
    assert source.read_bytes() == original
    def pixel_at(t, x, y):
        cp = subprocess.run([ffmpeg, '-v','error','-ss',str(t),'-i',str(tmp_path/'out'/'source_with_ad.mp4'),
                             '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True)
        offset = (y*160+x)*3
        return tuple(cp.stdout[offset:offset+3])
    assert pixel_at(.25,150,240)[0] > 220
    mixed = pixel_at(.25,40,50)
    assert 85 < mixed[0] < 180 and 85 < mixed[2] < 180, mixed
    assert pixel_at(1.25,110,190)[2] > 220

@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='External FFmpeg required')
def test_later_bad_source_does_not_delete_completed_result(tmp_path):
    from test_multi_inserts import generate
    source, banner = tmp_path/'good.mp4', tmp_path/'banner.mp4'
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    generate(ffmpeg,source,2.0,'red'); generate(ffmpeg,banner,.3,'blue')
    handler = VideoAdInserter.__new__(VideoAdInserter)
    handler.events,handler.cancel_event = queue.Queue(),threading.Event()
    handler.worker(dict(videos=[str(source),str(tmp_path/'missing.mp4')],
        insertions=[dict(time=1,banner=str(banner))],output=str(tmp_path/'out'),
        banner_mode='Без удаления фона',fit_mode='Растянуть на весь кадр',key_color='0x0000FF',
        key_similarity=.25,key_blend=.08,crf=24,include_banner_audio=False,ffmpeg=ffmpeg,ffprobe=ffprobe))
    assert ('done',1,1,str(tmp_path/'out')) in list(handler.events.queue)
    assert (tmp_path/'out'/'good_with_ad.mp4').is_file()
