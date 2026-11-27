from array import array
from pathlib import Path
import shutil
import subprocess
import threading
import wave

import pytest

import subtitles
from subtitles import Cue, LARGE_MODEL_NAME, transcribe


def test_two_hour_transcription_joins_duplicate_seam_and_preserves_final_timing(monkeypatch, tmp_path):
    windows = []
    progress = []
    languages = []

    def extract(ffmpeg, source, directory, cancel, start, end):
        windows.append((start, end))
        return end - start

    def recognize(directory, whisper, model, language, cancel, vad_model, progress):
        assert language == 'auto'
        if len(windows) == 1:
            return [Cue(479_000, 481_360, 'Он очень чётко', (479_000, 479_720, 480_240))], 'ru'
        if len(windows) == 2:
            return [Cue(3_180, 6_300, 'Он очень чётко произнёс всю фразу.',
                        (3_180, 3_800, 4_260, 4_900, 5_350, 5_760))], 'uk'
        if len(windows) == 15:
            length = windows[-1][1] - windows[-1][0]
            return [Cue(length - 1300, length + 400, 'Последняя реплика.')], 'ru'
        return [], 'en'

    monkeypatch.setattr(subtitles, '_extract_transcription_audio', extract)
    monkeypatch.setattr(subtitles, '_recognize_audio', recognize)
    cues = transcribe('ffmpeg', tmp_path / 'lot.mp4', 'whisper-cli', LARGE_MODEL_NAME, 'auto',
                      threading.Event(), progress.append, duration=7200, on_language=languages.append)

    assert len(windows) == 15
    assert windows[:2] == [(0, 484_000), (476_000, 964_000)]
    assert windows[-1] == (6_716_000, 7_200_000)
    assert [cue.text for cue in cues] == ['Он очень чётко произнёс всю фразу.', 'Последняя реплика.']
    assert cues[0].start == 479_000
    assert cues[0].word_starts[2] == 480_260
    assert cues[-1].end == 7_200_000
    assert languages == ['ru']
    assert progress[-1] == 'Распознавание речи: 100% (15/15)'


def test_auto_language_ignores_empty_windows_and_keeps_processing(monkeypatch, tmp_path):
    monkeypatch.setattr(subtitles, '_TRANSCRIPTION_CHUNK_MS', 10_000)
    monkeypatch.setattr(subtitles, '_TRANSCRIPTION_OVERLAP_MS', 1000)
    monkeypatch.setattr(subtitles, '_extract_transcription_audio', lambda *args: args[-1] - args[-2])
    windows = iter([([], 'en'), ([Cue(2500, 3300, 'Перша фраза.')], 'uk'),
                    ([Cue(3900, 4900, 'Вторая фраза.')], 'ru')])
    monkeypatch.setattr(subtitles, '_recognize_audio', lambda *_: next(windows))
    languages = []
    cues = transcribe('ffmpeg', tmp_path / 'video.mp4', 'whisper', LARGE_MODEL_NAME, 'auto',
                      threading.Event(), lambda *_: None, duration=30, on_language=languages.append)
    assert languages == ['uk']
    assert [cue.start for cue in cues] == [11_500, 22_900]
    assert [cue.text for cue in cues] == ['Перша фраза.', 'Вторая фраза.']


def test_cancelling_transcription_discards_incomplete_result(monkeypatch, tmp_path):
    monkeypatch.setattr(subtitles, '_TRANSCRIPTION_CHUNK_MS', 5000)
    cancel = threading.Event()
    extraction_count = []

    def extract(ffmpeg, source, directory, cancel, start, end):
        extraction_count.append(start)
        return end - start

    def recognize(*args):
        cancel.set()
        return [Cue(0, 1700, 'Пример текста.')], 'ru'

    monkeypatch.setattr(subtitles, '_extract_transcription_audio', extract)
    monkeypatch.setattr(subtitles, '_recognize_audio', recognize)
    assert transcribe('ffmpeg', tmp_path / 'video.mp4', 'whisper', LARGE_MODEL_NAME, 'ru',
                      cancel, lambda *_: None, duration=30) is None
    assert extraction_count == [0]


def test_transcription_empty_audio_range_is_normal_but_all_silence_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(subtitles, '_extract_transcription_audio', lambda *_: 0)

    def recognize(*args):
        pytest.fail('An empty extracted range must not start the recognizer')

    monkeypatch.setattr(subtitles, '_recognize_audio', recognize)
    with pytest.raises(ValueError, match='Речь для субтитров не найдена'):
        transcribe('ffmpeg', tmp_path / 'video.mp4', 'whisper', LARGE_MODEL_NAME, 'ru',
                   threading.Event(), lambda *_: None, duration=7200)


def test_transcription_read_failure_propagates(monkeypatch, tmp_path):
    def extract(*args):
        raise RuntimeError('decoding failed')

    monkeypatch.setattr(subtitles, '_extract_transcription_audio', extract)
    with pytest.raises(RuntimeError, match='decoding failed'):
        transcribe('ffmpeg', tmp_path / 'video.mp4', 'whisper', LARGE_MODEL_NAME, 'ru',
                   threading.Event(), lambda *_: None, duration=7200)


def test_recognizer_uses_matching_large_alignment_preset_and_does_not_read_stale_json(monkeypatch, tmp_path):
    previous = tmp_path / 'captions.json'
    previous.write_text('a previous chunk', encoding='utf-8')

    def process(args, directory, cancel, progress):
        assert args[args.index('-dtw') + 1] == 'large.v3'
        assert '-pp' in args
        assert not previous.exists()
        previous.write_text('{"result":{"language":"uk"},"transcription":[]}', encoding='utf-8')
        return True

    monkeypatch.setattr(subtitles, '_wait_process', process)
    result = subtitles._recognize_audio(tmp_path, 'whisper', Path(LARGE_MODEL_NAME), 'auto',
                                        threading.Event(), None, lambda *_: None)
    assert result == ([], 'uk')


def test_vad_word_timings_do_not_stretch_across_long_filtered_silence(monkeypatch, tmp_path):
    def process(args, directory, cancel, progress):
        assert '-ojf' in args and '--vad' in args
        (directory / 'process.log').write_bytes(
            b'whisper_vad: vad_segment_info: orig_start: 1.00, orig_end: 3.00, vad_start: 0.00, vad_end: 2.00\n'
            b'whisper_vad: vad_segment_info: orig_start: 474.00, orig_end: 476.00, vad_start: 2.20, vad_end: 4.20\n')
        (directory / 'captions.json').write_text(
            '{"result":{"language":"ru"},"transcription":['
            '{"offsets":{"from":1000,"to":291150},"text":" Опять завис.",'
            '"tokens":[{"text":" Опять","offsets":{"from":110,"to":650}},'
            '{"text":" завис","offsets":{"from":650,"to":1420}},{"text":".","offsets":{"from":2190,"to":2190}}]},'
            '{"offsets":{"from":291150,"to":476000},"text":" Петя открыл рюкзак.",'
            '"tokens":[{"text":" П","offsets":{"from":2270,"to":2300}},{"text":"етя","offsets":{"from":2300,"to":2420}},'
            '{"text":" открыл","offsets":{"from":2420,"to":3180}},{"text":" рюкзак.","offsets":{"from":3180,"to":3980}}]}]}',
            encoding='utf-8',
        )
        return True

    monkeypatch.setattr(subtitles, '_wait_process', process)
    words, language = subtitles._recognize_audio(tmp_path, 'whisper', LARGE_MODEL_NAME, 'auto', threading.Event(),
                                                 Path('vad.bin'), lambda *_: None)
    cues = list(subtitles._joined_phrases(subtitles._chunk_words(words, 0, 484_000)))
    assert language == 'ru'
    assert [cue.text for cue in cues] == ['Опять завис.', 'Петя открыл рюкзак.']
    assert [(cue.start, cue.end) for cue in cues] == [(1110, 2420), (474_070, 475_780)]
    assert cues[1].word_starts == (474_070, 474_220, 474_980)


def test_vad_missing_word_offsets_are_distributed_only_over_real_speech(tmp_path):
    log = tmp_path / 'process.log'
    log.write_bytes(
        b'whisper_vad: vad_segment_info: orig_start: 1.00, orig_end: 3.00, vad_start: 0.00, vad_end: 2.00\n'
        b'whisper_vad: vad_segment_info: orig_start: 474.00, orig_end: 476.00, vad_start: 2.20, vad_end: 4.20\n')
    document = {'transcription': [
        {'offsets': {'from': 1100, 'to': 475_900}, 'text': 'Он сказал, она ответила.'},
    ]}
    words = subtitles._vad_cues(document, log)
    cues = list(subtitles._joined_phrases(subtitles._chunk_words(words, 0, 484_000)))
    assert [cue.text for cue in cues] == ['Он сказал,', 'она ответила.']
    assert cues[0].end == 3000
    assert cues[1].start == 474_000


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg integration test')
def test_extracted_chunk_preserves_delayed_audio_track_alignment(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    video = tmp_path / 'late-audio.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=10:d=8',
                    '-itsoffset', '3', '-f', 'lavfi', '-i', 'sine=880:sample_rate=16000:duration=4',
                    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
                    str(video)], check=True, capture_output=True)

    elapsed = subtitles._extract_transcription_audio(ffmpeg, video, tmp_path, threading.Event(), 2500, 4500)

    with wave.open(str(tmp_path / 'speech.wav'), 'rb') as audio:
        samples = array('h', audio.readframes(audio.getnframes()))
        first_signal = next(index for index, sample in enumerate(samples) if abs(sample) > 100)
        first_signal_ms = 1000 * first_signal / audio.getframerate()
    assert elapsed == 2000
    assert abs(first_signal_ms - 500) < 25
