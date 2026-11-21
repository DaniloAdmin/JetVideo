from pathlib import Path
import ssl

datas = []
certificate_file = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if certificate_file.is_file():
    datas.append((str(certificate_file), '.'))
