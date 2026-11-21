import json
import shutil
import subprocess
from zipfile import ZipFile
import pytest
from app_info import EXE_NAME
from release_security import DOC_IMAGE_SHA256, inspect_bytes, scan
from package_release import digest, files_for_source
import package_release

def test_archive_allowlist_excludes_user_data_and_build_outputs():
    files = files_for_source()
    assert files
    assert not any(p.name in ('state.json','config.json','profiles.json','.env') for p in files)
    assert not any(p.suffix.lower() in ('.mp4','.webm','.exe','.dll') for p in files)
    assert not any(set(p.parts) & {'.venv','verification','build','dist','recovery'} for p in files)


def test_reviewed_documentation_screenshots_pass_media_scan():
    files = {p.relative_to(package_release.ROOT).as_posix(): p for p in files_for_source()}
    assert set(DOC_IMAGE_SHA256) <= files.keys()
    for name in DOC_IMAGE_SHA256:
        data = files[name].read_bytes()
        assert not inspect_bytes(name, data)
        assert not inspect_bytes('git-history/' + name, data)
        assert inspect_bytes(name, data + b'extra')
        assert inspect_bytes('unreviewed.png', data)


def test_packaging_writes_manifest_in_clean_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(package_release, 'ROOT', tmp_path)
    for name in package_release.RELEASE_DOCUMENTS:
        (tmp_path / name).write_text('fixture', encoding='utf-8')
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / EXE_NAME).write_bytes(b'executable fixture')

    tools = tmp_path / 'dist' / 'tools'
    tools.mkdir()
    for name in package_release.TOOLS:
        (tools / name).write_bytes(name.encode())
    ffmpeg_source = dist / 'ffmpeg-source.zip'
    with ZipFile(ffmpeg_source, 'w') as archive:
        archive.writestr('BINARY_SHA256.json', json.dumps({name: digest(tools / name) for name in package_release.TOOLS}))
    output = tmp_path / 'release' / 'test'
    package_release.main(['--executable', str(dist / EXE_NAME), '--output', str(output),
                          '--ffmpeg-directory', str(tools), '--ffmpeg-source', str(ffmpeg_source)])
    manifest = tmp_path / 'verification' / 'source-manifest.json'
    assert 'README.uk.md' in json.loads(manifest.read_text(encoding='utf-8'))
    with ZipFile(next(output.glob('*-Windows-x64.zip'))) as archive:
        assert archive.read(EXE_NAME) == b'executable fixture'
        assert archive.read('JetVideo.portable') == b'fixture'
        assert archive.read('LICENSE') == b'fixture'
        assert archive.read('tools/ffmpeg.exe') == b'ffmpeg.exe'
        assert archive.read('tools/ffprobe.exe') == b'ffprobe.exe'
    (tools / 'ffprobe.exe').write_bytes(b'mismatched fixture')
    with pytest.raises(ValueError, match='hashes do not match'):
        package_release.verify_ffmpeg(tools, ffmpeg_source)

@pytest.mark.skipif(not shutil.which('git'), reason='Git not installed')
def test_removed_secret_is_found_in_git_history_without_echoing_value(tmp_path):
    def git(*args):
        return subprocess.run(['git',*args],cwd=tmp_path,check=True,capture_output=True)
    git('init')
    git('config','user.name','Offline security test')
    git('config','user.email','offline@example.invalid')
    secret='ghp_' + 'a'*36
    (tmp_path/'.env').write_text('BUFFER_API_KEY='+secret,encoding='utf-8')
    git('add','.env'); git('commit','-m','Synthetic fixture')
    git('rm','.env'); git('commit','-m','Remove fixture')
    report=scan(tmp_path)
    assert not report['ok'] and report['findings']
    assert secret not in str(report)
