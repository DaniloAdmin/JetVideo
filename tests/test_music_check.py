import json
from pathlib import Path
import shutil
import subprocess
from threading import Event
from unittest.mock import Mock

import pytest
import requests

import music_check
from music_check import MusicCheckCancelled, MusicCheckError, MusicMatch, MusicResult, MusicScanner


_CONTAINER = {
    'id': 17, 'region': 'eu-west-1', 'engine': 1, 'buckets': [{'name': 'ACRCloud Music'}],
    'policy': {'type': 'traverse', 'interval': 0, 'rec_length': 10},
}
_JOB_ID = '37ad6cf5-43b9-4cf3-816d-1ca73198b115'
_TRACK = {
    'offset': 34, 'played_duration': 8, 'type': 'traverse',
    'result': {
        'title': 'Example track', 'artists': [{'name': 'An artist'}],
        'sample_begin_time_offset_ms': 2300, 'sample_end_time_offset_ms': 8800,
        'play_offset_ms': 64100,
    },
}


def _response(status=200, data=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({'data': data}).encode()
    response._content_consumed = True
    return response


def _stub_audio(monkeypatch, stream_index=2):
    runner = Mock(side_effect=lambda args, *_: Path(args[-1]).write_bytes(b'fake m4a'))
    monkeypatch.setattr(music_check, '_audio_stream', lambda *args: stream_index)
    monkeypatch.setattr(music_check, '_run_media', runner)
    monkeypatch.setattr(music_check, '_POLL_SECONDS', 0)
    return runner


def test_scanner_uploads_audio_and_reports_positions_in_the_clip(tmp_path, monkeypatch):
    runner = _stub_audio(monkeypatch)
    local_files = []
    requests_made = []
    responses = iter([
        _response(data=_CONTAINER),
        _response(data={'id': _JOB_ID}),
        _response(data=[{'id': _JOB_ID, 'state': 0}]),
        _response(data=[{'id': _JOB_ID, 'state': 1, 'results': {'music': [_TRACK]}}]),
        _response(204),
        _response(data={'id': _JOB_ID}),
        _response(data={'id': _JOB_ID, 'state': -1}),
        _response(204),
    ])
    video = tmp_path / 'Відео автора.mp4'

    def send(method, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        if method == 'POST':
            filename, file, mime = kwargs['files']['file']
            local_files.append(Path(file.name))
            assert filename != video.name and mime == 'audio/mp4'
            assert file.read() == b'fake m4a'
            assert kwargs['data'] == {'data_type': 'audio'}
        requests_made.append((method, url))
        return next(responses)

    monkeypatch.setattr(music_check.requests, 'request', send)
    scanner = MusicScanner('example-token', 'eu-west-1', '17', 'ffmpeg', 'ffprobe')
    match = MusicMatch('Example track', 'An artist', 36300, 42800)
    assert scanner.scan(video, Event()) == MusicResult(matches=(match,))
    assert scanner.scan(video, Event()) == MusicResult()
    assert all(not path.exists() for path in local_files)
    args = runner.call_args_list[0].args[0]
    assert args[args.index('-map') + 1] == '0:2'
    assert [method for method, _ in requests_made] == ['GET', 'POST', 'GET', 'GET', 'DELETE', 'POST', 'GET', 'DELETE']
    assert requests_made[4][1].endswith(f'/api/fs-containers/17/files/{_JOB_ID}')


def test_missing_audio_is_reported_without_cloud_calls(tmp_path, monkeypatch):
    runner = _stub_audio(monkeypatch, stream_index=None)
    request = Mock()
    monkeypatch.setattr(music_check.requests, 'request', request)
    scanner = MusicScanner('example-token', 'eu-west-1', '17', 'ffmpeg', 'ffprobe')
    assert scanner.scan(tmp_path / 'silent.mp4', Event()) == MusicResult(no_audio=True)
    runner.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize('method, payload', [
    ('POST', {'id': 'another-id,existing-id'}),
    ('GET', [{'id': 'another-id', 'state': -1}]),
    ('GET', {'id': _JOB_ID, 'state': 1, 'results': {}}),
    ('GET', {'id': _JOB_ID, 'state': 1, 'results': {'music': [{}]}}),
    ('GET', {'id': _JOB_ID, 'state': -2}),
])
def test_unusable_responses_are_never_reported_as_clear(tmp_path, monkeypatch, method, payload):
    _stub_audio(monkeypatch)
    responses = [_response(data=_CONTAINER)]
    if method == 'GET':
        responses.append(_response(data={'id': _JOB_ID}))
    responses.extend([_response(data=payload), _response(204)])
    request = Mock(side_effect=responses)
    monkeypatch.setattr(music_check.requests, 'request', request)
    with pytest.raises(MusicCheckError):
        MusicScanner('example-token', 'eu-west-1', '17', 'ffmpeg', 'ffprobe').scan(tmp_path / 'a.mp4', Event())
    if method == 'GET':
        assert request.call_args.args[0] == 'DELETE'
    else:
        assert request.call_count == 2


def test_scan_rejects_sparse_sampling_before_transmitting_audio(tmp_path, monkeypatch):
    runner = _stub_audio(monkeypatch)
    request = Mock(return_value=_response(data={**_CONTAINER, 'policy': {'type': 'points', 'points': 3}}))
    monkeypatch.setattr(music_check.requests, 'request', request)
    with pytest.raises(MusicCheckError, match='ACRCloud Music'):
        MusicScanner('example-token', 'eu-west-1', '17', 'ffmpeg', 'ffprobe').scan(tmp_path / 'a.mp4', Event())
    request.assert_called_once()
    runner.assert_not_called()


def test_cancelled_upload_cleans_only_its_job_and_skips_polling(tmp_path, monkeypatch):
    _stub_audio(monkeypatch)
    stop = Event()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == 'POST':
            stop.set()
            return _response(data={'id': _JOB_ID})
        return _response(204) if method == 'DELETE' else _response(data=_CONTAINER)

    monkeypatch.setattr(music_check.requests, 'request', request)
    with pytest.raises(MusicCheckCancelled):
        MusicScanner('example-token', 'eu-west-1', '17', 'ffmpeg', 'ffprobe').scan(tmp_path / 'a.mp4', stop)
    assert calls == ['GET', 'POST', 'DELETE']


def test_failed_cloud_cleanup_keeps_the_match(tmp_path, monkeypatch):
    _stub_audio(monkeypatch)
    request = Mock(side_effect=[
        _response(data=_CONTAINER),
        _response(data={'id': _JOB_ID}),
        _response(data=[{'id': _JOB_ID, 'state': 1, 'results': {'music': [_TRACK]}}]),
        requests.ConnectionError('example-token'),
    ])
    monkeypatch.setattr(music_check.requests, 'request', request)
    result = MusicScanner('example-token', 'eu-west-1', '17', 'ffmpeg', 'ffprobe').scan(tmp_path / 'a.mp4', Event())
    assert result.matches and result.cleanup_failed


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='FFmpeg and FFprobe required')
def test_bundled_tools_extract_default_mono_aac_without_source_metadata(tmp_path, monkeypatch):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    source = tmp_path / 'Відео.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=s=64x64:d=1',
                    '-f', 'lavfi', '-i', 'sine=f=440:d=1',
                    '-f', 'lavfi', '-i', 'sine=f=880:d=1',
                    '-map', '0:v', '-map', '1:a', '-map', '2:a', '-disposition:a:0', '0',
                    '-disposition:a:1', 'default', '-metadata', 'title=Private title',
                    '-c:v', 'libx264', '-c:a', 'aac', str(source)], check=True)
    assert music_check._audio_stream(ffprobe, source, Event()) == 2

    def request(method, url, **kwargs):
        if method == 'POST':
            _, audio, _ = kwargs['files']['file']
            result = subprocess.check_output([
                ffprobe, '-v', 'error', '-show_entries', 'stream=codec_type,channels,codec_name:format_tags=title',
                '-of', 'json', audio.name,
            ])
            data = json.loads(result)
            assert data['streams'] == [{'codec_name': 'aac', 'codec_type': 'audio', 'channels': 1}]
            assert not data.get('format', {}).get('tags')
            return _response(data={'id': _JOB_ID})
        if method == 'DELETE':
            return _response(204)
        return _response(data=([{'id': _JOB_ID, 'state': -1}] if url.endswith(_JOB_ID) else _CONTAINER))

    monkeypatch.setattr(music_check.requests, 'request', request)
    assert MusicScanner('example-token', 'eu-west-1', '17', ffmpeg, ffprobe).scan(source, Event()) == MusicResult()
