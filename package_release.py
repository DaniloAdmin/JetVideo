import argparse
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

from app_info import EXE_NAME, PRODUCT_NAME, RELEASE_TAG, VERSION
from release_security import scan, inspect_bytes


ROOT = Path(__file__).resolve().parent
TOOLS = ('ffmpeg.exe', 'ffprobe.exe')
WHISPER_FILES = (
    'whisper-cli.exe', 'ggml-base.bin', 'ggml-silero-v5.1.2.bin',
    'LICENSE-whisper.cpp.txt', 'LICENSE-Whisper-model.txt', 'LICENSE-Silero.txt',
    'PROVENANCE.txt', 'PROVENANCE-Silero.txt',
    'ggml-base.dll', 'ggml.dll', 'libwhisper.dll',
    *(f'ggml-cpu-{cpu}.dll' for cpu in (
        'alderlake', 'cannonlake', 'cascadelake', 'cooperlake', 'haswell', 'icelake',
        'ivybridge', 'piledriver', 'sandybridge', 'sapphirerapids', 'skylakex',
        'sse42', 'x64', 'zen4',
    )),
)
RELEASE_DOCUMENTS = ('README.md', 'README.ru.md', 'README.uk.md', 'LICENSE', 'COPYRIGHT.md',
                     'THIRD_PARTY_NOTICES.md', 'SECURITY.md', '.env.example', 'starter_config.json',
                     'JetVideo.portable')


def files_for_source():
    selected = set()
    for pattern in ('*.py', '*.md', '*.txt', '*.lock', '*.bat', '*.spec', '*.iss'):
        selected.update(ROOT.glob(pattern))
    selected.update(ROOT / name for name in (*RELEASE_DOCUMENTS, '.gitignore', 'icon.ico'))
    for folder in ('tests', 'build_hooks', 'build_tools', '.github', 'docs', 'locales', 'third_party'):
        selected.update(p for p in (ROOT / folder).rglob('*') if p.is_file()
                        and '__pycache__' not in p.parts and p.suffix != '.pyc')
    return sorted((p for p in selected if p.is_file()), key=lambda p: p.relative_to(ROOT).as_posix())


def make_zip(target, files):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in files:
            archive.write(path, name)


def make_portable_zip(target, executable, ffmpeg_directory, whisper_directory):
    portable = [(executable, EXE_NAME)]
    portable.extend((ROOT / name, name) for name in RELEASE_DOCUMENTS)
    for directory in ('third_party', 'docs'):
        portable.extend((path, path.relative_to(ROOT).as_posix()) for path in sorted((ROOT / directory).rglob('*'))
                        if path.is_file())
    portable.extend((ffmpeg_directory / name, f'tools/{name}') for name in TOOLS)
    portable.extend((whisper_directory / name, f'tools/whisper/{name}') for name in WHISPER_FILES)
    make_zip(target, portable)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_ffmpeg(ffmpeg_directory, source_archive):
    with zipfile.ZipFile(source_archive) as archive:
        recorded = json.loads(archive.read('BINARY_SHA256.json'))
    actual = {name: digest(ffmpeg_directory / name) for name in TOOLS}
    if recorded != actual:
        raise ValueError('FFmpeg executable hashes do not match the corresponding-source archive.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build a portable JetVideo release with matching FFmpeg sources.')
    parser.add_argument('--executable', type=Path, default=ROOT / 'dist' / EXE_NAME)
    parser.add_argument('--output', type=Path, default=ROOT / 'release' / RELEASE_TAG)
    parser.add_argument('--ffmpeg-directory', type=Path, required=True)
    parser.add_argument('--ffmpeg-source', type=Path, required=True)
    parser.add_argument('--whisper-directory', type=Path, required=True)
    parser.add_argument('--dependency-sources', type=Path)
    args = parser.parse_args(argv)
    report = scan(ROOT)
    if not report['ok']:
        raise RuntimeError('Source scan failed. Run release_security.py.')
    if not args.executable.is_file():
        raise FileNotFoundError(args.executable)
    verify_ffmpeg(args.ffmpeg_directory, args.ffmpeg_source)
    for filename in WHISPER_FILES:
        if not (args.whisper_directory / filename).is_file():
            raise FileNotFoundError(args.whisper_directory / filename)
    source_files = files_for_source()
    for path in source_files:
        if inspect_bytes(path.relative_to(ROOT).as_posix(), path.read_bytes()):
            raise RuntimeError(f'Disallowed source file: {path.relative_to(ROOT)}')
    prefix = f'{PRODUCT_NAME}-{VERSION}'
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    source = output / f'{prefix}-source.zip'
    portable = output / f'{prefix}-Windows-x64.zip'
    ffmpeg = output / f'FFmpeg-JetVideo-{RELEASE_TAG}-source.zip'
    make_zip(source, [(p, p.relative_to(ROOT).as_posix()) for p in source_files])
    make_portable_zip(portable, args.executable, args.ffmpeg_directory, args.whisper_directory)
    shutil.copyfile(args.ffmpeg_source, ffmpeg)
    artifacts = [portable, source, ffmpeg]
    if args.dependency_sources is not None:
        dependencies = output / f'{prefix}-dependency-sources.zip'
        shutil.copyfile(args.dependency_sources, dependencies)
        artifacts.append(dependencies)
    hashes = [f'{digest(path)}  {path.name}' for path in artifacts]
    (output / 'SHA256SUMS.txt').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
    verification = ROOT / 'verification'
    verification.mkdir(exist_ok=True)
    (verification / 'source-manifest.json').write_text(
        json.dumps([p.relative_to(ROOT).as_posix() for p in source_files], indent=2), encoding='utf-8')
    print('\n'.join(path.name for path in artifacts))


if __name__ == '__main__':
    main()
