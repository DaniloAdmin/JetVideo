from pathlib import Path
import runpy
import ssl


def test_ssl_hook_includes_runtime_certificate(tmp_path, monkeypatch):
    monkeypatch.setattr(ssl, '__file__', str(tmp_path / 'ssl.py'))
    certificate = tmp_path / 'codex-rejected-cas.pem'
    certificate.write_text('synthetic-build-data', encoding='utf-8')
    hook = Path(__file__).resolve().parents[1] / 'build_hooks' / 'hook-ssl.py'
    assert runpy.run_path(str(hook))['datas'] == [(str(certificate), '.')]


def test_ssl_hook_supports_stock_python(tmp_path, monkeypatch):
    monkeypatch.setattr(ssl, '__file__', str(tmp_path / 'ssl.py'))
    hook = Path(__file__).resolve().parents[1] / 'build_hooks' / 'hook-ssl.py'
    assert runpy.run_path(str(hook))['datas'] == []
