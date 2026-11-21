import queue
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from video_ad_inserter_core import parse_time, probe_media, VideoAdInserter
from editor_ui import save_preferences, load_preferences


class TestBasics(unittest.TestCase):
    def test_time_formats(self):
        self.assertEqual(parse_time('01:02'), 62)
        self.assertEqual(parse_time('1:01:01'), 3661)
        self.assertEqual(parse_time('20,5'), 20.5)
        with self.assertRaises(ValueError): parse_time('-2')
        with self.assertRaises(ValueError): parse_time('hello')

    def test_prefs_persistence(self):
        with tempfile.TemporaryDirectory() as path:
            file = Path(path) / 'config' / 'settings.json'
            save_preferences(file, {'output_dir': 'D:/Videos/JJ', 'insert_at': '00:00:10'})
            self.assertEqual(load_preferences(file)['output_dir'], 'D:/Videos/JJ')
            self.assertEqual(load_preferences(Path(path)/'missing'), {})

    def test_unique_filename(self):
        with tempfile.TemporaryDirectory() as path:
            handler = VideoAdInserter.__new__(VideoAdInserter)
            first = Path(handler.unique_output_path(path, '/input/181.mp4'))
            self.assertEqual(first.name, '181_with_ad.mp4')
            first.touch()
            second = Path(handler.unique_output_path(path, '/input/181.mp4'))
            self.assertEqual(second.name, '181_with_ad_2.mp4')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools not available')
class TestRendering(unittest.TestCase):
    def test_video_audio_stops_during_ad_and_returns(self):
        ffmpeg = shutil.which('ffmpeg')
        ffprobe = shutil.which('ffprobe')
        with tempfile.TemporaryDirectory() as path:
            path = Path(path)
            video = path / '181.mp4'
            banner = path / 'ad.mp4'
            output = path / 'result'
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=red:s=160x280:r=24:d=2.4',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2.4',
                '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                str(video)], check=True)
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=blue:s=160x280:r=24:d=0.8',
                '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=0.8',
                '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                str(banner)], check=True)
            handler = VideoAdInserter.__new__(VideoAdInserter)
            handler.events = queue.Queue()
            opt = {'videos': [str(video)], 'banner': str(banner), 'output': str(output),
                'insert_seconds': 1., 'banner_mode': 'Без удаления фона',
                'fit_mode': 'Растянуть на весь кадр', 'key_color': '0x0000FF',
                'key_similarity': .25, 'key_blend': .08, 'crf': 24,
                'include_banner_audio': True, 'ffmpeg': ffmpeg, 'ffprobe': ffprobe}
            handler.worker(opt)
            events = list(handler.events.queue)
            errors = [x for x in events if x[0] in ('fatal',) or (x[0] == 'log' and 'ОШИБКА' in x[1])]
            self.assertFalse(errors, errors)
            self.assertIn(('done', 1, 0, str(output)), events)
            result = output / '181_with_ad.mp4'
            self.assertTrue(result.exists())
            info = probe_media(ffprobe, str(result))
            self.assertTrue(info.has_audio)
            self.assertAlmostEqual(info.duration, 3.2, delta=0.15)

if __name__ == '__main__': unittest.main()
