import json
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publisher_ui as app
import bot
from editor_ui import preference_path


def test_choose_ad_when_both_exist(tmp_path):
    p = tmp_path / "ready"
    p.mkdir()
    (p / "181.mp4").write_bytes(b"plain")
    (p / "181_with_ad.mp4").write_bytes(b"ad")
    (p / "182.mov").write_bytes(b"more")
    (p / "not_a_number.mp4").write_bytes(b"skip")
    assert [(n, file.name) for n, file in bot.list_videos(p) if n < 10**18] == [
        (181, "181_with_ad.mp4"), (182, "182.mov")
    ]


def test_env_roundtrip(tmp_path):
    keys = {k: f"value_{i}#&" for i, (k, _) in enumerate(app.ENV_KEYS)}
    app.save_env(tmp_path / ".env", keys)
    assert app.parse_env(tmp_path / ".env") == keys
    assert "#&" in (tmp_path / ".env").read_text()


def test_workspace_requires_explicit_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "bundled", lambda name: Path(__file__).resolve().parents[1] / name)
    home = tmp_path / "simulated_C" / "AppData" / "JOJO BOT"
    with __import__("pytest").raises(app.WorkspaceSelectionRequired) as err:
        app.setup_workspace(home)
    assert not err.value.missing_previous
    assert not (home / "workspace").exists()
    root = tmp_path / "D" / "JOJO BOT" / "workspace"
    root.mkdir(parents=True)
    selected, pref = app.setup_workspace(home, root)
    assert selected == root
    assert (root / "config.json").exists()
    assert (root / "state.json").exists()
    assert (root / "ready").is_dir()
    assert not (root / ".env").exists()
    assert app.read_json(pref, {})["workspace"] == str(root.resolve())
    assert not (home / "workspace").exists()
    assert app.setup_workspace(home)[0] == root


def test_portable_first_launch_uses_an_empty_workspace(tmp_path, monkeypatch):
    system_home = tmp_path / 'system'
    clean = tmp_path / 'clean'
    clean.mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(clean / 'JetVideo.exe'))
    monkeypatch.setenv('LOCALAPPDATA', str(system_home))
    monkeypatch.setenv('APPDATA', str(system_home))
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: system_home))
    old_home = app.data_home()
    old_editor = preference_path()
    old_workspace = old_home / 'data'
    app.initialize_workspace(old_workspace)
    app.write_json(old_home / 'preferences.json', {'workspace': str(old_workspace)})
    app.write_json(old_workspace / 'state.json', {'items': {'1': {'status': 'sent'}}})
    app.write_json(old_editor, {'output_dir': 'existing'})
    (clean / 'JetVideo.portable').touch()

    new_home = app.data_home()
    assert new_home == clean / 'JetVideo Data'
    assert preference_path() == new_home / 'editor-preferences.json'
    assert not new_home.exists()
    prompts = []

    def accept(_title, prompt, **_options):
        prompts.append(prompt)
        return True

    monkeypatch.setattr(app.messagebox, 'askyesno', accept)
    workspace, _ = app.ModernApp._choose_startup_workspace(SimpleNamespace(home=new_home))
    assert workspace == new_home / 'data'
    assert prompts and str(workspace) in prompts[0]
    assert app.read_json(workspace / 'state.json', None) == {'items': {}}
    assert not (workspace / '.env').exists()
    assert app.read_json(old_workspace / 'state.json', None) == {'items': {'1': {'status': 'sent'}}}
    assert app.read_json(old_editor, None) == {'output_dir': 'existing'}


def test_missing_saved_workspace_never_recreates_c(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "bundled", lambda name: Path(__file__).resolve().parents[1] / name)
    home = tmp_path / "simulated_C"
    existing = tmp_path / "old_drive" / "workspace"
    existing.mkdir(parents=True)
    app.setup_workspace(home, existing)
    new_path = tmp_path / "new_drive" / "workspace"
    new_path.parent.mkdir()
    shutil.move(existing, new_path)
    with __import__("pytest").raises(app.WorkspaceSelectionRequired) as err:
        app.setup_workspace(home)
    assert err.value.missing_previous
    assert not existing.exists()
    assert not (home / "workspace").exists()
    assert app.setup_workspace(home, new_path)[0] == new_path
    assert app.setup_workspace(home)[0] == new_path


def test_legacy_workspace_adopted(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "bundled", lambda name: Path(__file__).resolve().parents[1] / name)
    home = tmp_path / "AppData"
    legacy = home / "workspace"
    legacy.mkdir(parents=True)
    app.initialize_workspace(legacy)
    assert app.find_workspace(home)[2] == "legacy"
    assert app.setup_workspace(home)[0] == legacy
    assert app.find_workspace(home)[2] == "saved"


def test_copy_workspace_preserves_state_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "bundled", lambda name: Path(__file__).resolve().parents[1] / name)
    source = tmp_path / "C" / "workspace"
    source.mkdir(parents=True)
    app.initialize_workspace(source)
    app.write_json(source / "state.json", {"items": {"180": {"status": "sent"}}})
    app.save_env(source / ".env", {"BUFFER_API_KEY": "private-test"})
    (source / "ready" / "181_with_ad.mp4").write_bytes(b"video")
    cfg = app.read_json(source / "config.json", {})
    cfg["videos_folder"] = str(source / "ready")
    app.write_json(source / "config.json", cfg)
    target = tmp_path / "D" / "JOJO BOT workspace"
    target.parent.mkdir()
    app.copy_workspace(source, target)
    assert (target / "ready" / "181_with_ad.mp4").read_bytes() == b"video"
    assert app.read_json(target / "state.json", {})["items"]["180"]["status"] == "sent"
    assert app.parse_env(target / ".env")["BUFFER_API_KEY"] == "private-test"
    assert app.read_json(target / "config.json", {})["videos_folder"] == str(target / "ready")
    assert (source / "ready" / "181_with_ad.mp4").exists()
    assert app.read_json(source / "config.json", {})["videos_folder"] == str(source / "ready")
    with __import__("pytest").raises(FileExistsError):
        app.copy_workspace(source, target)
    with __import__("pytest").raises(ValueError):
        app.copy_workspace(source, source / "nested")


def test_offline_fill_and_reconcile(tmp_path, monkeypatch):
    cfg = json.loads((Path(__file__).resolve().parents[1] / "starter_config.json").read_text(encoding="utf-8"))
    cfg["target_tiktok_channel"] = "demo-channel"
    cfg["buffer_organization_name"] = "Personal"
    cfg["episode_ranges"].append({"from": 181, "to": 182, "season": 1, "episode": 18})
    cfg["max_queue_posts"] = 1
    cfg["file_stable_seconds"] = 0
    app.write_json(tmp_path / "config.json", cfg)
    (tmp_path / "ready").mkdir()
    (tmp_path / "ready" / "181.mp4").write_bytes(b"plain")
    (tmp_path / "ready" / "181_with_ad.mp4").write_bytes(b"ad")
    app.save_env(tmp_path / ".env", {k: f"TEST_{k}" for k, _ in app.ENV_KEYS})

    class MockBuffer:
        posted = []
        status = "scheduled"
        def __init__(self, key):
            assert key == "TEST_BUFFER_API_KEY"
        def organizations(self):
            return [{"id": "org", "name": "Personal"}]
        def channels(self, org_id):
            return [{"id": "channel", "service": "tiktok", "name": "demo-channel", "displayName": "demo-channel", "isQueuePaused": False}]
        def posts(self, org_id, channel_id, statuses):
            return [{**p, "status": self.status} for p in self.posted]
        def create_video_post(self, channel_id, text, url, due_at, thumb_ms):
            post = {"id": "post-181", "status": "scheduled", "dueAt": bot.iso_utc(due_at),
                    "text": text, "channelId": channel_id}
            self.posted.append(post)
            return post

    class MockR2:
        deleted = []
        def __init__(self, *args, **kwargs):
            pass
        def upload(self, path, part):
            assert path.name == "181_with_ad.mp4"
            return "https://example.invalid/video.mp4", "test-key"
        def delete(self, key):
            self.deleted.append(key)

    monkeypatch.setattr(bot, "BufferClient", MockBuffer)
    monkeypatch.setattr(bot, "R2Client", MockR2)
    bot.set_log_callback(lambda text: None)
    try:
        instance = bot.Bot(tmp_path)
        instance.fill()
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["items"]["181"]["filename"] == "181_with_ad.mp4"
        assert (tmp_path / "scheduled" / "181_with_ad.mp4").exists()
        assert (tmp_path / "ready" / "181.mp4").exists()
        instance.buffer.status = "sent"
        instance.fill()
        assert (tmp_path / "posted" / "181_with_ad.mp4").exists()
        assert MockR2.deleted == ["test-key"]

        event = threading.Event()
        t = threading.Thread(target=instance.watch, args=(event,), daemon=True)
        t.start()
        time.sleep(0.1)
        event.set()
        t.join(1.5)
        assert not t.is_alive()
    finally:
        bot.set_log_callback(None)
