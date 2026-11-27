from threading import Event

import pytest

import music_preflight
from music_check import MusicCheckError, MusicMatch, MusicResult


def test_music_batch_checks_later_files_after_match_and_failure(tmp_path, monkeypatch):
    for index in range(10):
        (tmp_path / f'{index + 1:02d}.mp4').write_bytes(str(index).encode())
    (tmp_path / 'ignore.txt').write_text('unrelated', encoding='utf-8')
    scanned = []

    class Scanner:
        def __init__(self, *_args):
            pass

        def scan(self, path, stop):
            scanned.append(path.name)
            if path.name == '01.mp4':
                return MusicResult(matches=(MusicMatch('Sample', 'Artist', 120, 3500),))
            if path.name == '05.mp4':
                raise MusicCheckError('simulated service failure')
            return MusicResult()

    monkeypatch.setattr(music_preflight, 'MusicScanner', Scanner)
    monkeypatch.setattr(music_preflight, 'resolve_video_tools', lambda: ('fake ffmpeg', 'fake ffprobe'))
    music_preflight._results.clear()
    log = []
    try:
        with pytest.raises(music_preflight.MusicPreflightBlocked):
            music_preflight.preflight_music(tmp_path, {}, Event(), log.append)
        assert scanned == [f'{index + 1:02d}.mp4' for index in range(10)]
        assert any('01.mp4' in line and '00:00:00.120–00:00:03.500' in line and 'Sample' in line for line in log)
        assert any('05.mp4' in line and 'simulated service failure' in line for line in log)
        assert any('10.mp4' in line and line.startswith('✓') for line in log)
        assert len(list(tmp_path.glob('*.mp4'))) == 10
        scanned.clear()
        with pytest.raises(music_preflight.MusicPreflightBlocked):
            music_preflight.preflight_music(tmp_path, {}, Event(), log.append)
        assert scanned == ['05.mp4']
    finally:
        music_preflight._results.clear()
