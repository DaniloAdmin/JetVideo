import queue
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from video_ad_inserter_core import (
    MediaInfo, VideoAdInserter, prepare_insertions, validate_source_timeline, probe_media,
)


def generate(ffmpeg, filename, seconds, color, audio=None):
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
            f'color=c={color}:s=160x280:r=24:d={seconds}']
    if audio is not None:
        args += ['-f', 'lavfi', '-i', f'sine=frequency={audio}:sample_rate=48000:duration={seconds}',
                 '-shortest']
    args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if audio is not None:
        args += ['-c:a', 'aac']
    args.append(str(filename))
    subprocess.run(args, check=True)


class TestMultiValidation(unittest.TestCase):
    def test_timeline_sorts_stably_and_allows_simultaneous(self):
        with tempfile.TemporaryDirectory() as dirname:
            p = Path(dirname)/'test.mp4'; p.touch()
            entries = prepare_insertions([
                {'time': '00:00:02', 'banner': str(p)},
                {'time': '1', 'banner': str(p)},
                {'time': '01', 'banner': str(p)},
            ])
            self.assertEqual([e['time'] for e in entries], [1, 1, 2])
            self.assertEqual([e['ordinal'] for e in entries], [2, 3, 1])
            validate_source_timeline(entries, MediaInfo(4., 160, 280, '24/1', True))

    def test_missing_banner_and_out_of_bounds(self):
        with self.assertRaisesRegex(ValueError, 'не найден'):
            prepare_insertions([{'time': '1', 'banner': '/not/a/file'}])
        info = MediaInfo(2., 160, 280, '24/1', True)
        with self.assertRaisesRegex(ValueError, 'не помещается'):
            validate_source_timeline([{'time': 3.}], info)
        with self.assertRaisesRegex(ValueError, 'слишком близко'):
            validate_source_timeline([{'time': 1.0}, {'time': 1.01}], info)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires FFmpeg')
class TestMultipleRenders(unittest.TestCase):
    def setUp(self):
        self.ffmpeg = shutil.which('ffmpeg')
        self.ffprobe = shutil.which('ffprobe')

    def run_render(self, folder, source, insertions, output='result'):
        handler = VideoAdInserter.__new__(VideoAdInserter)
        handler.events = queue.Queue()
        opt = {'videos': [str(source)], 'insertions': insertions, 'output': str(folder / output),
               'banner_mode': 'auto', 'fit_mode': 'Растянуть на весь кадр',
               'key_color': '0x0000FF', 'key_similarity': .25, 'key_blend': .08,
               'crf': 24, 'include_banner_audio': True,
               'ffmpeg': self.ffmpeg, 'ffprobe': self.ffprobe}
        handler.worker(opt)
        return list(handler.events.queue), folder / output / f'{source.stem}_with_ad.mp4'

    def test_two_banners_with_sound_and_without_sound(self):
        with tempfile.TemporaryDirectory() as dirname:
            folder = Path(dirname)
            original = folder / '181.mp4'
            ad1 = folder / 'with_audio.mp4'
            ad2 = folder / 'silent.mp4'
            generate(self.ffmpeg, original, 3.0, 'red', 440)
            generate(self.ffmpeg, ad1, .60, 'blue', 880)
            generate(self.ffmpeg, ad2, .75, 'green', None)
            events, out = self.run_render(folder, original, [
                {'time': '1', 'banner': str(ad1), 'mode': 'Без удаления фона'},
                {'time': '2', 'banner': str(ad2), 'mode': 'Без удаления фона'},
            ])
            self.assertIn(('done', 1, 0, str(folder/'result')), events, events)
            self.assertTrue(out.exists())
            result = probe_media(self.ffprobe, str(out))
            self.assertTrue(result.has_audio)
            self.assertAlmostEqual(result.duration, 4.35, delta=.15)
            self.assertTrue(any('2 вставок' in msg for kind, msg, *rest in events if kind == 'log'))

    def test_same_timestamp_multiple_adverts(self):
        with tempfile.TemporaryDirectory() as dirname:
            folder = Path(dirname)
            original = folder/'182.mp4'; ad = folder/'ad.mp4'
            generate(self.ffmpeg, original, 2.4, 'red', 440)
            generate(self.ffmpeg, ad, .4, 'blue', 880)
            events, out = self.run_render(folder, original, [
                {'time': 1., 'banner': str(ad)}, {'time': 1., 'banner': str(ad)}])
            self.assertIn(('done', 1, 0, str(folder/'result')), events, events)
            self.assertAlmostEqual(probe_media(self.ffprobe, str(out)).duration, 3.2, delta=.15)

    def test_one_video_fails_does_not_stop_batch(self):
        with tempfile.TemporaryDirectory() as dirname:
            folder = Path(dirname)
            short = folder/'short.mp4'; long = folder/'long.mp4'; ad = folder/'ad.mp4'
            generate(self.ffmpeg, short, 1.0, 'red', None)
            generate(self.ffmpeg, long, 3.0, 'red', None)
            generate(self.ffmpeg, ad, .5, 'blue', None)
            handler = VideoAdInserter.__new__(VideoAdInserter)
            handler.events = queue.Queue()
            handler.worker({'videos': [str(short), str(long)],
                            'insertions': [{'time': 2, 'banner': str(ad)}],
                            'output': str(folder/'out'), 'banner_mode': 'auto',
                            'fit_mode': 'Растянуть на весь кадр', 'key_color':'0x0000FF',
                            'key_similarity': .25, 'key_blend': .08, 'crf': 24,
                            'include_banner_audio': False, 'ffmpeg': self.ffmpeg, 'ffprobe': self.ffprobe})
            events = list(handler.events.queue)
            self.assertIn(('done', 1, 1, str(folder/'out')), events, events)
            self.assertTrue((folder/'out'/'long_with_ad.mp4').exists())
            self.assertFalse((folder/'out'/'short_with_ad.mp4').exists())


if __name__ == '__main__':
    unittest.main()
