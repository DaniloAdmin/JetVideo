from pathlib import Path
from hashlib import sha256
import json
import re
import subprocess
import sys

PRIVATE = {'.env', 'config.json', 'state.json', 'profiles.json', 'preferences.json',
           'insights_cache.json', 'sync_options.json', 'sync_report.json'}
SKIP = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'build', 'dist',
        'release', 'verification', 'third_party'}
DOC_IMAGE_SHA256 = {
    'docs/buffer-images/01-open-settings.png': '186077eed19acf700e253e82cbd0891848fb1871aec2158dec7cc1855dc853c3',
    'docs/buffer-images/02-open-api.png': '20ed8dd09735bcfdab6099d9583be922dfae4a0028df067ed8373b1ffe7830bc',
    'docs/buffer-images/03-personal-access.png': 'f32b99f3b1e3307f8418f2e7bf781801672bfdfde9efc2e95a741b645a9bb5b7',
    'docs/buffer-images/generate-api-key.png': '54626017152351be7c4b5d02a668d36fb6045943088a7aefb84602130cfaf403',
    'docs/buffer-images/new-key.png': 'e0011dacacdd21162539b7eac7a0e1f6c1c58a283a463f2eb5ec131484271d79',
    'docs/r2-images/01-open-r2.png': '9b26035d740114a39e31dc04e4131af96b86926b035634fdc2b7896f52f9809d',
    'docs/r2-images/02-create-bucket.png': '513279665cf65a75f0c26da5cf00e404a5364af872b8f6d843932d7abb052917',
    'docs/r2-images/03-bucket-form.png': '7d34334354135b11b61dc6d4970d388bfdd275b85dbb64d210d34c7934ace9e3',
    'docs/r2-images/04-manage-tokens.png': '058fdc176f81c4ac81c99ffdda24e93c0a975d72d79bdb5b8548243ca3a3db4e',
    'docs/r2-images/05-user-token.png': '3410a5e39baaef19ca26cdd760b5ccdc0a70fe284d6d7385943b42dd00392fba',
    'docs/r2-images/06-bucket-permissions.png': '6d8421dda09da1e3574e78b1d29679a2fec6c0ca6a8129c325d578c587f6bbef',
    'docs/r2-images/07-s3-credentials.png': '73575a212f865026b71dcb980b6755bffba34453cd8db681c5f0bb13def6a263',
    'docs/r2-images/08-open-bucket.png': '618c3a5d7dd0a4635e460f7345bcf78123c008436027790b34875b6106d9e9b1',
    'docs/r2-images/09-bucket-settings.png': '8c719d348a45b9074a50ab50a088405dc877828b72baab885ea30e6bf0d0092c',
}
PATTERNS = [re.compile(rb'AKIA[0-9A-Z]{16}'),
            re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
            re.compile(rb'(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}'),
            re.compile(rb'(?i)(?:BUFFER_API_KEY|R2_ACCESS_KEY_ID|R2_SECRET_ACCESS_KEY)[\x22\x27]?\s*[=:]\s*[\x22\x27]?([A-Za-z0-9_./+-]{24,})')]

def inspect_bytes(label, data):
    findings = []
    if Path(label).name in PRIVATE:
        findings.append({'path': label, 'reason': 'private filename'})
    if Path(label).suffix.lower() in ('.mp4', '.mov', '.webm', '.mkv', '.avi', '.png', '.jpg', '.jpeg'):
        expected = DOC_IMAGE_SHA256.get(label.replace('\\', '/').removeprefix('git-history/'))
        if expected is None or sha256(data).hexdigest() != expected:
            findings.append({'path': label, 'reason': 'user media must not be in source'})
    for pattern in PATTERNS:
        if pattern.search(data):
            findings.append({'path': label, 'reason': 'possible credential/private key'})
            break
    return findings

def scan(root: Path):
    findings = []
    for path in root.rglob('*'):
        if not path.is_file() or set(path.relative_to(root).parts) & SKIP:
            continue
        findings += inspect_bytes(str(path.relative_to(root)), path.read_bytes())
    history = 'no Git repository'
    if (root/'.git').exists():
        history = 'all reachable and reflog Git blobs scanned'
        result = subprocess.run(['git','rev-list','--objects','--all','--reflog'], cwd=root, capture_output=True, check=True)
        seen = set()
        for row in result.stdout.decode('utf-8', 'replace').splitlines():
            oid, _, name = row.partition(' ')
            if oid in seen: continue
            seen.add(oid)
            kind = subprocess.run(['git','cat-file','-t',oid],cwd=root,capture_output=True,check=True).stdout.strip()
            if kind == b'blob':
                blob = subprocess.run(['git','cat-file','blob',oid],cwd=root,capture_output=True,check=True).stdout
                findings += inspect_bytes('git-history/' + name, blob)
    return {'ok': not findings, 'git_history': history, 'findings':findings}

if __name__ == '__main__':
    report = scan(Path(__file__).resolve().parent)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    sys.exit(0 if report['ok'] else 1)
