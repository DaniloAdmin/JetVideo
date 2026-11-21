from __future__ import annotations

from i18n import tr
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from media_files import VIDEO_EXTS, commit_new_file, video_id

VIDEO_PATTERN = re.compile(r"(?i)^(\d+)(?:_with_ad(?:_(\d+))?)?\.(?:mp4|mov|webm)$")
BLOCKED = {"uploaded", "submitting", "uncertain", "scheduled", "sending", "sent", "posted", "draft", "needs_approval", "error"}


@dataclass(frozen=True)
class CopyJob:
    source: Path
    destination: Path
    profile_name: str


@dataclass
class HandoffPlan:
    jobs: list[CopyJob]
    existing: list[CopyJob]
    skipped: list[str]
    renamed: list[str]


def build_plan(sources: Iterable[str | Path], profiles: Iterable[object]) -> HandoffPlan:
    jobs: list[CopyJob] = []
    existing: list[CopyJob] = []
    skipped: list[str] = []
    renamed: list[str] = []
    seen: set[str] = set()
    paths = [Path(source).expanduser() for source in sources]
    for profile in profiles:
        root = Path(profile.root)
        cfg = json.loads((root / 'config.json').read_text(encoding='utf-8'))
        folder = Path(cfg.get('videos_folder', 'ready'))
        ready = folder if folder.is_absolute() else root / folder
        state_path = root / 'state.json'
        history = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        records = history.get('items', {})
        name = str(profile.name)
        for src in paths:
            matched = VIDEO_PATTERN.fullmatch(src.name)
            if src.suffix.lower() not in VIDEO_EXTS:
                skipped.append(tr('{0}: {1} — поддерживаются MP4, MOV, WebM').format(name, src.name))
                continue
            if not src.is_file():
                skipped.append(tr('{0}: {1} — файл не существует').format(name, src.name))
                continue
            part = str(int(matched.group(1))) if matched else str(video_id(src))
            old = records.get(part, {})
            if isinstance(old, dict) and old.get('status') in BLOCKED:
                skipped.append(tr('{0}: часть {1} уже имеет статус «{2}» — проверь историю в JetVideo').format(name, part, old['status']))
                continue
            normalized = f'{part}_with_ad{src.suffix.lower()}' if matched and matched.group(2) else src.name
            dst = ready / normalized
            if matched and matched.group(2):
                renamed.append(f'{name}: {src.name} → {normalized}')
            if src.resolve() == dst.resolve():
                skipped.append(tr('{0}: {1} — уже находится в ready').format(name, src.name))
                continue
            key = str(dst.resolve())
            if key in seen:
                skipped.append(tr('{0}: {1} — для этой части уже выбран другой вариант').format(name, src.name))
                continue
            seen.add(key)
            job = CopyJob(src, dst, name)
            (existing if dst.exists() else jobs).append(job)
    return HandoffPlan(jobs, existing, skipped, renamed)


def transfer(plan: HandoffPlan, *, overwrite: bool = False) -> tuple[int, list[str]]:
    copies = [(job, False) for job in plan.jobs]
    if overwrite:
        copies.extend((job, True) for job in plan.existing)
    success = 0
    errors = []
    for job, replace_existing in copies:
        dst = job.destination
        temp = dst.with_name(f'.{dst.name}.transfer-{uuid.uuid4().hex}.tmp')
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and not replace_existing:
                errors.append(tr('{0}: {1} уже существует (не заменён)').format(job.profile_name, dst.name))
                continue
            shutil.copy2(job.source, temp)
            try:
                if replace_existing:
                    os.replace(temp, dst)
                else:
                    commit_new_file(temp, dst)
            except FileExistsError:
                errors.append(tr('{0}: {1} появился во время копирования').format(job.profile_name, dst.name))
                continue
            success += 1
        except OSError as exc:
            errors.append(f'{job.profile_name}: {dst.name} — {exc}')
        finally:
            temp.unlink(missing_ok=True)
    return success, errors
