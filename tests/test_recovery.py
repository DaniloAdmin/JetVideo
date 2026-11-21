import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publisher_ui as app
import bot
import portfolio
from profiles import ProfileStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'bundled', lambda name: Path(__file__).resolve().parents[1] / name)
    ws = tmp_path / 'workspace'
    ws.mkdir()
    app.initialize_workspace(ws)
    cfg = app.read_json(ws / "config.json", {})
    cfg["file_stable_seconds"] = 0
    app.write_json(ws / "config.json", cfg)
    app.save_env(ws / '.env', {key: f'MOCK_{key}' for key, _ in app.ENV_KEYS})
    store = ProfileStore(ws)
    p = store.selected()
    store.save_channel(p.id, channel_name='user1', channel_id='tt1', org_name='Org', org_id='org')

    api_posts = []
    class FakeBuffer:
        def __init__(self, key):
            assert key == 'MOCK_BUFFER_API_KEY'
        def organizations(self):
            return [{'id':'org', 'name':'Org'}]
        def channels(self, org_id):
            return [{'id':'tt1','name':'user1','displayName':'user1','service':'tiktok','isQueuePaused':False},
                    {'id':'tt2','name':'user2','displayName':'user2','service':'tiktok','isQueuePaused':False}]
        def posts(self, org_id, channel_id, statuses):
            return [dict(row) for row in api_posts if row.get('channelId') == channel_id and row.get('status') in statuses]
        def create_video_post(self, channel_id, text, url, due_at, thumb_ms):
            row={'id':'new-' + channel_id, 'channelId':channel_id,'status':'scheduled',
                 'text':text,'dueAt':bot.iso_utc(due_at)}
            api_posts.append(row)
            return row
    class FakeR2:
        def __init__(self, *args, **kwargs):
            pass
        def upload(self, video, part):
            return 'https://example.invalid/video.mp4', f'key-{part}'
        def delete(self, key):
            pass

    monkeypatch.setattr(bot, 'BufferClient', FakeBuffer)
    monkeypatch.setattr(bot, 'R2Client', FakeR2)
    bot.set_log_callback(lambda text: None)
    yield ws, store, p, api_posts
    bot.set_log_callback(None)


def set_scheduled(profile, part=181, fname=None, status='scheduled', post_id=None):
    fname = fname or f'{part}_with_ad.mp4'
    (profile.root / 'scheduled' / fname).write_bytes(b'original-scheduled')
    state={'items':{str(part):{'status':status, 'buffer_post_id':post_id or f'old-{part}',
                                  'filename':fname, 'due_at':'2026-11-10T14:00:00Z'}}}
    app.write_json(profile.root / 'state.json', state)
    return state


def test_removed_buffer_post_returns_to_ready_preserves_audit_and_reuploads(env):
    ws, store, p, posts = env
    set_scheduled(p)
    cfg = app.read_json(p.root / 'config.json', {})
    cfg['episode_ranges'].append({'from':181,'to':181,'season':2,'episode':1})
    app.write_json(p.root/'config.json', cfg)
    results = portfolio.restore_profile_parts(ws, p.id, [181])
    assert results == {'restored':[181], 'skipped':[]}
    assert not (p.root / 'scheduled' / '181_with_ad.mp4').exists()
    assert (p.root / 'ready' / '181_with_ad.mp4').read_bytes() == b'original-scheduled'
    state = app.read_json(p.root / 'state.json', {})
    assert state['items']['181']['status'] == 'returned'
    assert state['recovery_history'][0]['old_buffer_post_id'] == 'old-181'
    assert 181 not in bot.Bot(p.root, credentials_path=ws/'.env', profile_id=p.id).occupied_parts([])
    assert portfolio.run_profiles(ws, [p.id], 'fill') == {p.id: None}
    assert len(posts) == 1 and posts[0]['channelId'] == 'tt1'
    state2=app.read_json(p.root/'state.json',{})
    assert state2['items']['181']['status'] == 'scheduled'
    assert len(state2['recovery_history']) == 1


def test_still_scheduled_in_buffer_remains_unchanged(env):
    ws, store, p, posts = env
    original = set_scheduled(p)
    posts.append({'id':'old-181','channelId':'tt1','status':'scheduled','text':'Часть 181'})
    results = portfolio.restore_profile_parts(ws, p.id, [181])
    assert results == {'restored': [], 'skipped': [181]}
    assert app.read_json(p.root / 'state.json',{}) == original
    assert (p.root / 'scheduled' / '181_with_ad.mp4').exists()


def test_same_part_under_different_live_id_blocks_recovery(env):
    ws, store, p, posts = env
    original = set_scheduled(p)
    posts.append({'id':'new-other-id','channelId':'tt1','status':'scheduled','text':'Часть 181'})
    assert portfolio.restore_profile_parts(ws, p.id, [181])['skipped'] == [181]
    assert app.read_json(p.root / 'state.json',{}) == original


def test_published_cannot_be_recovered_by_status_or_live_history(env):
    ws, store, p, posts = env
    set_scheduled(p, status='sent')
    assert portfolio.restore_profile_parts(ws, p.id, [181])['skipped'] == [181]
    set_scheduled(p)
    posts.append({'id':'old-181','channelId':'tt1','status':'sent','text':'Часть 181'})
    assert portfolio.restore_profile_parts(ws, p.id, [181])['skipped'] == [181]
    posts.clear()
    posts.append({'id':'other','channelId':'tt1','status':'sent','text':'Часть 181'})
    assert portfolio.restore_profile_parts(ws, p.id, [181])['skipped'] == [181]


def test_ready_copy_preserved_scheduled_copy_archived(env):
    ws, store, p, posts = env
    set_scheduled(p)
    (p.root/'ready'/'181.mp4').write_bytes(b'new-ready')
    assert portfolio.restore_profile_parts(ws, p.id, [181])['restored'] == [181]
    assert (p.root/'ready'/'181.mp4').read_bytes() == b'new-ready'
    assert (p.root/'recovery_backups'/'181_with_ad.mp4').read_bytes() == b'original-scheduled'
    assert not (p.root/'scheduled'/'181_with_ad.mp4').exists()


def test_missing_video_not_automatically_recovered(env):
    ws, store, p, posts = env
    state = set_scheduled(p)
    (p.root/'scheduled'/'181_with_ad.mp4').unlink()
    assert portfolio.restore_profile_parts(ws, p.id, [181])['skipped'] == [181]
    assert app.read_json(p.root/'state.json',{}) == state


def test_profile_isolation(env):
    ws, store, p1, posts = env
    p2 = store.create('Second', channel_name='user2', channel_id='tt2', org_name='Org', org_id='org')
    set_scheduled(p1)
    set_scheduled(p2)
    assert portfolio.restore_profile_parts(ws, p2.id, [181])['restored'] == [181]
    assert app.read_json(p1.root/'state.json',{})['items']['181']['status'] == 'scheduled'
    assert (p1.root/'scheduled'/'181_with_ad.mp4').exists()
    assert app.read_json(p2.root/'state.json',{})['items']['181']['status'] == 'returned'


def test_api_error_does_not_change_files_or_state(env, monkeypatch):
    ws, store, p, posts = env
    state = set_scheduled(p)
    def fail_posts(*args, **kwargs):
        raise bot.BotError('Buffer unavailable')
    monkeypatch.setattr(bot.BufferClient,'posts', fail_posts)
    with pytest.raises(bot.BotError):
        portfolio.restore_profile_parts(ws,p.id,[181])
    assert app.read_json(p.root/'state.json',{}) == state
    assert (p.root/'scheduled'/'181_with_ad.mp4').exists()


def test_100_active_posts_is_safe_after_complete_pagination(env):
    ws, store, p, posts = env
    set_scheduled(p)
    posts.extend({'id':str(n),'channelId':'tt1','status':'scheduled','text':f'Часть {n}'} for n in range(1,101))
    result = portfolio.restore_profile_parts(ws,p.id,[181])
    assert result['restored'] == [181]
    assert app.read_json(p.root/'state.json',{})['items']['181']['status'] == 'returned'


def test_bulk_restore_skips_blocked_and_restores_other(env):
    ws, store, p, posts = env
    set_scheduled(p)
    state = app.read_json(p.root/'state.json',{})
    state['items']['182'] = {'status':'scheduled', 'buffer_post_id':'old-182','filename':'182.mp4'}
    app.write_json(p.root/'state.json',state)
    (p.root/'scheduled'/'182.mp4').write_bytes(b'video')
    posts.append({'id':'old-182','channelId':'tt1','status':'scheduled','text':'Часть 182'})
    result = portfolio.restore_profile_parts(ws,p.id,[181,182])
    assert result == {'restored':[181], 'skipped':[182]}
    state2=app.read_json(p.root/'state.json',{})
    assert state2['items']['181']['status']=='returned'
    assert state2['items']['182']['status']=='scheduled'
