from pathlib import Path
import shutil
import subprocess
from threading import Event
from time import monotonic, sleep

import pytest

from subtitles import CaptionPainter, Cue, FONTS, SubtitleStyle, _speech_cues, export_subtitled, frame_at, read_srt, regroup_captions
from video_ad_inserter_core import probe_media
from video_preview import VideoPreview


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'cp1251'])
def test_srt_preserves_russian_and_ukrainian_letters(tmp_path, encoding):
    document = tmp_path / 'субтитри.srt'
    phrase = 'Її ґаджет знімає відео. Ёлку нарядили.'
    document.write_text('1\n00:00:00,500 --> 00:00:01,400\n' + phrase + '\n', encoding=encoding)
    assert read_srt(document) == [Cue(500, 1400, phrase)]


@pytest.mark.parametrize('text', ['Ох! Ах… Ух.', 'А-а-а, м-м-м…', '«Ха-ха!»', '[музыка] (сміх)', '♪ Ла-ла-ла ♪', '…'])
def test_speech_only_skips_captions_with_no_words(text):
    assert _speech_cues([Cue(0, 1000, text)]) == []


def test_speech_only_preserves_short_words_and_spoken_phrases():
    cues = [Cue(0, 600, 'Да.'), Cue(600, 1000, 'Ні.'), Cue(1000, 2000, 'Її ґаджет знімає відео.', (1000, 1250, 1440, 1600)),
            Cue(2000, 3000, 'Ах, это (очень) хорошо.'), Cue(3000, 4000, '[Сміх] Слухай.', (3000, 3400))]
    assert _speech_cues(cues) == [*cues[:4], Cue(3000, 4000, 'Слухай.')]


def test_automatic_cues_follow_sentences_across_whisper_segments():
    source = [
        Cue(300, 2000, 'Он снова что-то сделал и опять', (300, 580, 850, 1150, 1450, 1750)),
        Cue(2000, 3500, 'завис. Петя открыл рюкзак, а', (2000, 2280, 2630, 2900, 3200)),
        Cue(3500, 4000, 'он пуст.', (3500, 3750)),
        Cue(4800, 5500, 'Петя закрыл рюкзак.'),
    ]
    regrouped = regroup_captions(source)
    assert [cue.text for cue in regrouped] == [
        'Он снова что-то сделал и опять завис.',
        'Петя открыл рюкзак, а он пуст.',
        'Петя закрыл рюкзак.',
    ]
    assert [(cue.start, cue.end) for cue in regrouped] == [(300, 2280), (2280, 4000), (4800, 5500)]
    assert regrouped[1].word_starts == (2280, 2630, 2900, 3200, 3500, 3750)


def test_automatic_cues_keep_short_link_word_with_the_next_group():
    text = 'Він взяв свій старий рюкзак і згадав, що залишив ключі'
    starts = tuple(range(0, 10_000, 1000))
    regrouped = regroup_captions([Cue(0, 10_000, text, starts)])
    assert regrouped[0].text == 'Він взяв свій старий рюкзак'
    assert regrouped[1].text == 'і згадав, що залишив ключі'


@pytest.mark.parametrize('family', tuple(FONTS))
def test_cyrillic_caption_bounds_follow_the_selected_position(family):
    style = SubtitleStyle(font=family, y=25)
    painter = CaptionPainter(style, (360, 640))
    layout = painter.layout(Cue(500, 1400, 'Її ґаджет знімає відео. Ёлку нарядили.'))
    low_layout = painter.layout(Cue(500, 1400, 'Її ґаджет знімає відео. Ёлку нарядили.', position=(50, 75)))
    assert painter.paint(layout, 1).getbbox()
    assert layout[0][3] <= 320 <= low_layout[0][1]


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='FFmpeg and FFprobe required')
def test_subtitle_export_preserves_gaps_and_renders_at_chosen_positions(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    source, output = tmp_path / 'Відео.mp4', tmp_path / 'Готово.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=180x320:r=30:d=3',
                    '-f', 'lavfi', '-i', 'sine=f=440:d=3', '-pix_fmt', 'yuv420p',
                    '-c:v', 'libx264', '-c:a', 'aac', '-t', '3', str(source)], check=True)
    cues = [Cue(500, 1300, 'Її відео', (500, 900)), Cue(1670, 2410, 'Ёлку нарядили', position=(50, 75))]
    info = probe_media(ffprobe, str(source))
    result = export_subtitled(ffmpeg, source, output, info, (180, 320), cues,
                               SubtitleStyle(highlight=True, y=25), 1, Event(), lambda _message: None)
    assert result == output and source.is_file()
    exported = probe_media(ffprobe, str(output))
    assert exported.has_audio and abs(exported.duration - 3) < .06

    def light_pixels(t, y):
        image = frame_at(ffmpeg, output, t).crop((0, y, 180, y + 160))
        return sum(r > 215 and g > 160 for r, g, _ in image.get_flattened_data())

    assert light_pixels(.18, 0) == light_pixels(1.48, 0) == light_pixels(2.62, 160) == 0
    assert light_pixels(.70, 0) > 0 and light_pixels(.70, 160) == 0
    assert light_pixels(1.98, 160) > 0 and light_pixels(1.98, 0) == 0
    keyframes = subprocess.check_output([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-skip_frame', 'nokey',
                                        '-show_entries', 'frame=best_effort_timestamp_time',
                                        '-of', 'default=nw=1:nk=1', str(output)], text=True)
    assert [float(line) for line in keyframes.splitlines() if line] == [0, 1, 2]


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_silent_preview_seeks_and_stops_decoder(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    source = tmp_path / 'preview.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=240x320:r=25:d=1',
                    '-f', 'lavfi', '-i', 'color=c=blue:s=240x320:r=25:d=1',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0', '-an',
                    '-c:v', 'libx264', str(source)], check=True)
    playback = VideoPreview(ffmpeg, source, 1.2, 2, (240, 320), 25, False)
    try:
        deadline = monotonic() + 6
        first = None
        while monotonic() < deadline:
            assert not playback.error, playback.error
            position, frame = playback.poll()
            if frame is not None and first is None:
                first = frame
            if position >= 1.45:
                break
            sleep(.015)
        assert first is not None and first.getpixel((10, 10))[2] > 200
        assert 1.45 <= position < 2
    finally:
        playback.close()
    deadline = monotonic() + 3
    while playback.processes and monotonic() < deadline:
        sleep(.02)
    assert not playback.processes
