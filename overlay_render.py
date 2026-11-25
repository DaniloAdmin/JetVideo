from i18n import tr
from pathlib import Path
import os
import subprocess
import tempfile
from video_ad_inserter_core import PAD_TO_EVEN, source_input_args

def render_overlay(owner, opt, source, target, info, entries, index, total):
    args = [opt['ffmpeg'], '-hide_banner', '-nostdin', '-y'] + source_input_args(source, opt)
    for entry in entries:
        args += ['-loop', '1'] if Path(entry['banner']).suffix.lower() in ('.png', '.jpg', '.jpeg') else ['-stream_loop', '-1']
        if entry['mode'] == 'Прозрачный WebM (alpha)' and entry.get('codec') in ('vp8', 'vp9'):
            args += ['-c:v', 'libvpx-vp9' if entry['codec'] == 'vp9' else 'libvpx']
        args += ['-i', entry['banner']]
    filters = ['[0:v]setpts=PTS-STARTPTS,setsar=1[base0]']
    for i, entry in enumerate(entries):
        w = max(2, int(info.width * entry['width'] / 100) // 2 * 2)
        h = max(2, int(info.height * entry['height'] / 100) // 2 * 2)
        x, y = info.width * entry['x'] / 100, info.height * entry['y'] / 100
        start, duration = entry['time'], entry['duration']
        effects = 'format=rgba'
        if entry['mode'] == 'Синий фон MP4 (удалить фон)':
            effects += f",colorkey={opt['key_color']}:{opt['key_similarity']}:{opt['key_blend']}"
        effects += (f',scale={w}:{h}:force_original_aspect_ratio=decrease,'
                    f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0,'
                    f'colorchannelmixer=aa={entry["opacity"]},fps={info.fps},'
                    f'trim=duration={duration},setpts=PTS-STARTPTS+{start}/TB')
        filters.append(f'[{i+1}:v]{effects}[banner{i}]')
        filters.append(f"[base{i}][banner{i}]overlay={x}:{y}:eof_action=pass:repeatlast=0:"
                       f"enable='between(t,{start},{start+duration})'[base{i+1}]")
    filters.append(f'[base{len(entries)}]{PAD_TO_EVEN}[vout]')
    args += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', '[vout]',
             '-map', '0:a?', '-t', str(info.duration), '-c:v', 'libx264', '-preset', opt.get('x264_preset', 'medium'),
             '-crf', str(opt['crf']), '-pix_fmt', 'yuv420p', '-c:a', 'aac',
             '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', '-loglevel', 'error', '-f', 'mp4', target]
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as errors:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, text=True,
                              encoding='utf-8', errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as proc:
            owner._monitor_cancel(proc)
            for line in proc.stdout:
                if line.startswith('out_time_ms='):
                    try:
                        progress = min(99, int(int(line.split('=')[1]) / 1e6 / info.duration * 100))
                        owner.events.put(('status', tr('Наложение {0}/{1}: {2}%').format(index, total, progress)))
                        owner.events.put(('progress', (index-1)*100 + progress))
                    except ValueError:
                        pass
            code = proc.wait()
        errors.seek(0)
        detail = errors.read()[-2500:]
    if code or not Path(target).exists() or Path(target).stat().st_size < 1024:
        Path(target).unlink(missing_ok=True)
        raise RuntimeError(detail or tr('FFmpeg завершился с кодом {0}').format(code))
