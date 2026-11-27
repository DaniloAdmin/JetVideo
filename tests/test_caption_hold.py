from dataclasses import replace
from threading import Event

import subtitles
from subtitles import Cue, cue_at, hold_captions, read_srt, transcribe


def test_caption_lingers_for_800_ms_and_leaves_long_pause_blank():
    original = [
        Cue(14_000, 17_000, 'Первая фраза.', (14_000, 15_800), (50.0, 75.0)),
        Cue(19_000, 21_000, 'Вторая фраза.', (19_000, 20_100)),
        Cue(31_000, 33_000, 'Третья фраза.', (31_000, 32_150)),
    ]

    cues = hold_captions(original, 35_000)

    assert cues == [replace(cue, end=cue.end + 800) for cue in original]
    assert [cue.end for cue in original] == [17_000, 21_000, 33_000]
    starts = [cue.start for cue in cues]
    assert [cue_at(cues, starts, time) for time in (17_799, 17_800, 18_999, 19_000)] == [0, None, None, 1]
    assert cue_at(cues, starts, 30_000) is None


def test_hold_respects_next_caption_and_video_end():
    original = [
        Cue(1000, 2000, 'Раз.'),
        Cue(2400, 3000, 'Два.'),
        Cue(3000, 4000, 'Тихий голос.'),
    ]

    cues = hold_captions(original, 4250)

    assert cues == [
        replace(original[0], end=2400, word_starts=(1000,)),
        original[1],
        replace(original[2], end=4250, word_starts=(3000, 3500)),
    ]
    starts = [cue.start for cue in cues]
    assert [cue_at(cues, starts, time) for time in (2399, 2400, 3000, 4249, 4250)] == [0, 1, 2, 2, None]


def test_hold_never_shortens_overlapping_or_out_of_bounds_cues():
    original = [Cue(1000, 2500, 'Первый.'), Cue(2000, 4000, 'Второй.')]

    assert hold_captions(original, 3500) == original


def test_transcription_holds_captions_but_srt_import_keeps_authored_times(monkeypatch, tmp_path):
    first = Cue(14_000, 17_000, 'Первая фраза.', (14_000, 15_800))
    second = Cue(19_000, 21_000, 'Вторая фраза.', (19_000, 20_100))
    monkeypatch.setattr(subtitles, '_extract_transcription_audio', lambda *_: 21_300)
    monkeypatch.setattr(subtitles, '_recognize_audio', lambda *_: ([first, second], 'ru'))

    recognized = transcribe(
        'ffmpeg', tmp_path / 'video.mp4', 'whisper', subtitles.LARGE_MODEL_NAME,
        'ru', Event(), lambda _: None, duration=21.3,
    )
    assert recognized == [replace(first, end=17_800), replace(second, end=21_300)]

    srt = tmp_path / 'original.srt'
    srt.write_text(
        '1\n00:00:14,000 --> 00:00:17,000\nПервая фраза.\n\n'
        '2\n00:00:19,000 --> 00:00:21,000\nВторая фраза.\n',
        encoding='utf-8',
    )
    assert read_srt(srt) == [replace(first, word_starts=()), replace(second, word_starts=())]
