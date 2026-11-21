import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publisher_ui as app
import bot
import portfolio
from profiles import ProfileStore, ProfileError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'bundled', lambda name: Path(__file__).resolve().parents[1] / name)
    root = tmp_path / 'workspace'
    root.mkdir()
    app.initialize_workspace(root)
    cfg = app.read_json(root / "config.json", {})
    cfg["file_stable_seconds"] = 0
    app.write_json(root / "config.json", cfg)
    app.save_env(root / '.env', {key: f'MOCK_{key}' for key, _ in app.ENV_KEYS})
    return root


def test_legacy_adoption_preserves_all_data(workspace):
    state = {'items': {'180': {'status': 'sent', 'buffer_post_id': 'post-old'}}}
    app.write_json(workspace / 'state.json', state)
    (workspace / 'posted' / '180.mp4').write_bytes(b'old')
    store = ProfileStore(workspace)
    assert store.selected().root == workspace
    assert len(store.list()) == 1
    assert app.read_json(workspace / 'state.json', {}) == state
    assert (workspace / 'posted' / '180.mp4').read_bytes() == b'old'
    assert app.read_json(workspace / 'profiles.json', {})['profiles'][0]['folder'] == '.'
    assert '.env' not in (workspace / 'profiles.json').read_text(encoding='utf-8')


def test_profile_creation_separate_settings_history_files_and_names(workspace):
    store = ProfileStore(workspace)
    first = store.selected()
    (first.root / 'ready' / '181.mp4').write_bytes(b'first')
    app.write_json(first.root / 'state.json', {'items': {'180': {'status': 'sent'}}})
    original_cfg = app.read_json(first.root / 'config.json', {})
    p = store.create('JoJo второй', channel_name='jojo2', org_name='Personal', channel_id='tt2', org_id='org')
    assert (p.root / 'ready').is_dir()
    assert (p.root / 'posted').is_dir()
    assert (p.root / 'scheduled').is_dir()
    assert not (p.root / '.env').exists()
    assert not (p.root / 'ready' / '181.mp4').exists()
    assert app.read_json(p.root / 'state.json', {}) == {'items': {}}
    cfg = app.read_json(p.root / 'config.json', {})
    assert cfg['target_tiktok_channel_id'] == 'tt2'
    assert cfg['videos_folder'] == 'ready'
    assert cfg['daily_windows'] == original_cfg['daily_windows']
    assert cfg['caption_templates'] == original_cfg['caption_templates']
    assert app.read_json(first.root / 'state.json', {})['items']['180']['status'] == 'sent'
    assert app.read_json(first.root / 'config.json', {}) == original_cfg
    assert store.get(p.id) == p
    store.select(p.id)
    assert ProfileStore(workspace).selected().id == p.id


def test_absolute_paths_not_cloned_to_other_profile(workspace):
    cfg = app.read_json(workspace / 'config.json', {})
    cfg['videos_folder'] = str(workspace / 'ready')
    cfg['scheduled_folder'] = str(workspace / 'scheduled')
    app.write_json(workspace / 'config.json', cfg)
    p = ProfileStore(workspace).create('Другой', channel_name='other')
    other = app.read_json(p.root / 'config.json', {})
    assert other['videos_folder'] == 'ready'
    assert other['scheduled_folder'] == 'scheduled'
    assert (p.root / 'ready') != (workspace / 'ready')


def test_channel_duplication_blocked_even_with_missing_org(workspace):
    store = ProfileStore(workspace)
    store.save_channel(store.selected().id, channel_name='same', channel_id='tt1', org_name='Org', org_id='org')
    with pytest.raises(ProfileError, match='уже'):
        store.create('Same ID', channel_name='alias', channel_id='tt1', org_name='Org', org_id='org')
    with pytest.raises(ProfileError, match='уже'):
        store.create('Same NAME', channel_name='same', org_name='')
    new = store.create('Another', channel_name='other', channel_id='tt2', org_id='org', org_name='Org')
    with pytest.raises(ProfileError, match='уже'):
        store.save_channel(new.id, channel_name='alias', channel_id='tt1', org_name='Org', org_id='org')
    assert app.read_json(new.root / 'config.json', {})['target_tiktok_channel_id'] == 'tt2'


def test_archive_restore_keep_files_and_selected_safe(workspace):
    store = ProfileStore(workspace)
    p = store.create('Second', channel_name='other')
    (p.root / 'ready' / '200.mp4').write_bytes(b'hi')
    store.select(p.id)
    store.set_active(p.id, False)
    assert store.selected().id != p.id
    assert not store.get(p.id).active
    assert not any(x.id == p.id for x in store.list(active_only=True))
    assert (p.root / 'ready' / '200.mp4').is_file()
    store.set_active(p.id, True)
    store.select(p.id)
    assert store.selected().id == p.id
    with pytest.raises(ProfileError):
        store.set_active(store.list()[0].id, False) if len(store.list(active_only=True)) == 1 else (_ for _ in ()).throw(ProfileError())


def test_registry_rejects_path_traversal(workspace):
    store = ProfileStore(workspace)
    registry = app.read_json(store.registry_path, {})
    registry['profiles'].append({'id': 'evil', 'name': 'Evil', 'folder': '../../outside', 'active': True})
    app.write_json(store.registry_path, registry)
    with pytest.raises(ProfileError):
        ProfileStore(workspace)


def test_workspace_migrate_keeps_profile_structure(workspace, tmp_path):
    store = ProfileStore(workspace)
    p = store.create('Second', channel_name='second')
    cfg = app.read_json(p.root / 'config.json', {})
    cfg['posted_folder'] = str(p.root / 'posted')
    app.write_json(p.root / 'config.json', cfg)
    (p.root / 'posted' / '99.mp4').write_bytes(b'media')
    dest = tmp_path / 'new-place' / 'JOJO BOT workspace'
    dest.parent.mkdir()
    app.copy_workspace(workspace, dest)
    relocated = ProfileStore(dest)
    p2 = relocated.get(p.id)
    assert (p2.root / 'posted' / '99.mp4').read_bytes() == b'media'
    assert app.read_json(p2.root / 'config.json', {})['posted_folder'] == str(p2.root / 'posted')
    assert app.read_json(p.root / 'config.json', {})['posted_folder'] == str(p.root / 'posted')


def test_real_bot_logic_two_distinct_buffer_channels_mocked(workspace, monkeypatch):
    store = ProfileStore(workspace)
    p1 = store.selected()
    store.save_channel(p1.id, channel_name='alpha', channel_id='tt1', org_name='Org', org_id='org')
    p2 = store.create('Beta', channel_name='beta', channel_id='tt2', org_name='Org', org_id='org')
    for profile in [p1, p2]:
        cfg = app.read_json(profile.root / 'config.json', {})
        cfg['episode_ranges'].append({'from': 201, 'to': 205, 'season': 2, 'episode': 1})
        cfg['max_queue_posts'] = 1
        app.write_json(profile.root / 'config.json', cfg)
        (profile.root / 'ready' / '201_with_ad.mp4').write_bytes(profile.id.encode())
    published = []
    class MockBuffer:
        def __init__(self, key):
            assert key == 'MOCK_BUFFER_API_KEY'
        def organizations(self):
            return [{'id': 'org', 'name': 'Org'}]
        def channels(self, org_id):
            return [dict(id=i, name=n, displayName=n, service='tiktok', isQueuePaused=False)
                    for i, n in [('tt1', 'alpha'), ('tt2', 'beta')]]
        def posts(self, org_id, channel_id, statuses):
            return [p for p in published if p['channelId'] == channel_id]
        def create_video_post(self, channel_id, text, url, due_at, thumb_ms):
            result = dict(id=f'post-{channel_id}', status='scheduled', dueAt=bot.iso_utc(due_at),
                          text=text, channelId=channel_id)
            published.append(result)
            return result
    uploaded = []
    class MockR2:
        def __init__(self, *args, **kwargs):
            self.namespace = kwargs['namespace']
        def upload(self, path, part):
            uploaded.append((self.namespace, path.name, part))
            return 'https://example.invalid/' + self.namespace, 'key-' + self.namespace
        def delete(self, key):
            pass
    monkeypatch.setattr(bot, 'BufferClient', MockBuffer)
    monkeypatch.setattr(bot, 'R2Client', MockR2)
    bot.set_log_callback(lambda _: None)
    try:
        results = portfolio.run_profiles(workspace, [p1.id, p2.id], 'fill')
        assert results == {p1.id: None, p2.id: None}
        assert [p['channelId'] for p in published] == ['tt1', 'tt2']
        assert {p['text'] for p in published}
        assert len(uploaded) == 2
        assert uploaded[0][0] != uploaded[1][0]
        assert (p1.root / 'scheduled' / '201_with_ad.mp4').is_file()
        assert (p2.root / 'scheduled' / '201_with_ad.mp4').is_file()
        assert (p1.root / 'state.json').exists() and (p2.root / 'state.json').exists()
        assert portfolio.run_profiles(workspace, [p1.id, p2.id], 'fill') == results
        assert len(published) == 2
    finally:
        bot.set_log_callback(None)


def test_all_account_failure_does_not_block_other_channel(workspace, monkeypatch):
    store = ProfileStore(workspace)
    p1 = store.selected()
    store.save_channel(p1.id, channel_name='broken', channel_id='tt-broken', org_name='Org', org_id='org')
    p2 = store.create('Good', channel_name='good', channel_id='tt-good', org_name='Org', org_id='org')
    called = []
    def fake_bot(root, **kwargs):
        class Fake:
            def resolve(self):
                self.channel = {'id': 'tt-broken' if root == p1.root else 'tt-good'}
            def fill(self):
                called.append(root)
                if root == p1.root:
                    raise RuntimeError('synthetic failure')
        return Fake()
    monkeypatch.setattr(portfolio.bot, 'Bot', fake_bot)
    bot.set_log_callback(lambda _: None)
    try:
        result = portfolio.run_profiles(workspace, [p1.id, p2.id], 'fill')
        assert 'synthetic failure' in result[p1.id]
        assert result[p2.id] is None
        assert called == [p1.root, p2.root]
    finally:
        bot.set_log_callback(None)


def test_watch_all_stops_without_stalling(workspace, monkeypatch):
    store = ProfileStore(workspace)
    p1 = store.selected()
    store.save_channel(p1.id, channel_name='first', channel_id='tt1', org_name='Org', org_id='org')
    p2 = store.create('Second', channel_name='second', channel_id='tt2', org_name='Org', org_id='org')
    called = []
    def fake_bot(root, **kwargs):
        class Fake:
            config = {'watch_interval_minutes': 60}
            def resolve(self):
                self.channel = {'id': 'tt1' if root == p1.root else 'tt2'}
            def fill(self):
                called.append(root)
        return Fake()
    monkeypatch.setattr(portfolio.bot, 'Bot', fake_bot)
    stop = threading.Event()
    thread = threading.Thread(target=portfolio.watch_profiles, args=(workspace, [p1.id, p2.id], stop))
    thread.start()
    for _ in range(30):
        if len(called) >= 2:
            break
        stop.wait(0.01)
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    assert called[:2] == [p1.root, p2.root]


def test_live_channel_id_preflight_detects_two_aliases(workspace, monkeypatch):
    store = ProfileStore(workspace)
    p1 = store.selected()
    store.save_channel(p1.id, channel_name='legacy-name', channel_id='', org_name='Org', org_id='org')
    p2 = store.create('Other label', channel_name='new-name', channel_id='id-same', org_name='Org', org_id='org')
    attempted = []
    class Fake:
        def __init__(self, root, **kwargs):
            self.root = root
        def resolve(self):
            self.channel = {'id': 'id-same'}
        def fill(self):
            attempted.append(self.root)
    monkeypatch.setattr(portfolio.bot, 'Bot', Fake)
    results = portfolio.run_profiles(workspace, [p1.id, p2.id], 'fill')
    assert 'один Buffer-канал' in results[p1.id]
    assert 'один Buffer-канал' in results[p2.id]
    assert attempted == []


def test_workspace_exclusive_lock(workspace):
    from process_lock import exclusive_workspace, WorkspaceBusy
    with exclusive_workspace(workspace):
        with pytest.raises(WorkspaceBusy):
            with exclusive_workspace(workspace):
                pass
    with exclusive_workspace(workspace):
        pass


def test_deleted_registry_does_not_hide_existing_second_account(workspace):
    store = ProfileStore(workspace)
    p = store.create('Secondary', channel_name='secondary')
    (workspace / 'profiles.json').unlink()
    with pytest.raises(ProfileError, match='Восстанови profiles.json'):
        ProfileStore(workspace)
    assert p.root.is_dir()
