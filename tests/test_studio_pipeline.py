from __future__ import annotations

import json
import queue
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from handoff import build_plan, transfer
from video_ad_inserter_core import VideoAdInserter, probe_media


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
                    reason='FFmpeg/FFprobe required for real encoding')
def test_rendered_video_is_ready_for_two_tiktok_accounts(tmp_path):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')

    def create(path, seconds, color, freq):
        subprocess.run([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', f'color=c={color}:s=160x280:r=24:d={seconds}',
            '-f', 'lavfi', '-i', f'sine=frequency={freq}:sample_rate=48000:duration={seconds}',
            '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)
        ], capture_output=True, check=True)

    original, ad1, ad2 = [tmp_path / name for name in ('201.mp4', 'ad_a.mp4', 'ad_b.mp4')]
    create(original, 3, 'red', 440)
    create(ad1, .4, 'blue', 880)
    create(ad2, .6, 'yellow', 600)
    rendered = tmp_path / 'rendered'
    handler = VideoAdInserter.__new__(VideoAdInserter)
    handler.events = queue.Queue()
    handler.worker({
        'videos': [str(original)], 'output': str(rendered),
        'insertions': [{'time': '1', 'banner': str(ad1)}, {'time': '2', 'banner': str(ad2)}],
        'banner_mode': 'auto', 'fit_mode': 'Растянуть на весь кадр',
        'key_color': '0x0000FF', 'key_similarity': .25, 'key_blend': .08,
        'crf': 24, 'include_banner_audio': True,
        'ffmpeg': ffmpeg, 'ffprobe': ffprobe,
    })
    events = list(handler.events.queue)
    assert ('done', 1, 0, str(rendered)) in events
    output = rendered / '201_with_ad.mp4'
    assert any(x[0] == 'rendered' and x[1] == str(output) for x in events)
    result = probe_media(ffprobe, str(output))
    assert abs(result.duration - 4) < .18
    assert result.has_audio

    profiles = []
    for name in ('channel_one', 'channel_two'):
        root = tmp_path / name
        (root / 'ready').mkdir(parents=True)
        (root / 'config.json').write_text(json.dumps({'videos_folder': 'ready'}))
        (root / 'state.json').write_text(json.dumps({'items': {}}))
        profiles.append(SimpleNamespace(root=root, name=name))

    plan = build_plan([output], profiles)
    assert len(plan.jobs) == 2
    count, errors = transfer(plan)
    assert count == 2 and not errors
    for profile in profiles:
        assert (profile.root / 'ready' / output.name).read_bytes() == output.read_bytes()
        assert json.loads((profile.root / 'state.json').read_text()) == {'items': {}}
