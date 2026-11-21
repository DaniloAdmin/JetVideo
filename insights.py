from __future__ import annotations

from i18n import tr
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bot
from profiles import ProfileStore

READ_STATUSES = ['scheduled', 'sending', 'sent', 'error', 'draft', 'needs_approval']
METRIC_NAMES = ('views', 'reactions', 'comments', 'shares', 'reposts', 'reach')


def _instance(store: ProfileStore, profile_id: str):
    profile = store.get(profile_id)
    instance = bot.Bot(profile.root, credentials_path=store.workspace / '.env', profile_id=profile.id)
    instance.resolve(allow_paused=True)
    return profile, instance


def normalize_metrics(metrics: list[dict[str, Any]] | None) -> dict[str, float]:
    result = {}
    for entry in metrics or []:
        name = str(entry.get('type', ''))
        if name in METRIC_NAMES and entry.get('value') is not None:
            result[name] = float(entry['value'])
    return result


def fetch_insights(workspace: Path, ids: list[str]) -> dict[str, Any]:
    store = ProfileStore(workspace)
    report = {'fetched_at': datetime.now(timezone.utc).isoformat(), 'profiles': {}, 'errors': {}}
    for pid in ids:
        profile = store.get(pid)
        try:
            _, instance = _instance(store, pid)
            posts = instance.buffer.posts_paginated(instance.org['id'], instance.channel['id'], ['sent'], include_metrics=True)
            rows = []
            for post in posts:
                rows.append({
                    'id': str(post['id']), 'text': str(post.get('text') or ''),
                    'date': post.get('sentAt') or post.get('dueAt') or post.get('createdAt') or '',
                    'metrics': normalize_metrics(post.get('metrics')),
                    'metrics_updated_at': post.get('metricsUpdatedAt'),
                    'part': bot.part_from_text(post.get('text') or ''),
                })
            report['profiles'][pid] = {'name': profile.name, 'rows': rows, 'channel': instance.channel.get('displayName') or instance.channel.get('name'), 'count': len(rows)}
        except Exception as exc:
            report['errors'][pid] = f'{profile.name}: {type(exc).__name__}: {exc}'
    return report


def filter_rows(report: dict, profile_id: str | None, days: int, now: datetime | None = None) -> list[dict]:
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    rows = []
    for pid, data in report.get('profiles', {}).items():
        if profile_id and pid != profile_id:
            continue
        for row in data.get('rows', []):
            try:
                dt = bot.parse_dt(row.get('date'))
            except (ValueError, TypeError):
                dt = None
            if dt is None or dt < cutoff or dt > now + timedelta(hours=1):
                continue
            rows.append({**row, 'profile_id': pid, 'profile_name': data['name']})
    rows.sort(key=lambda r: r.get('date') or '', reverse=True)
    return rows


def summarize(rows: list[dict]) -> dict[str, dict[str, int | None]]:
    result = {}
    for key in METRIC_NAMES:
        known = [row['metrics'][key] for row in rows if key in row.get('metrics', {})]
        result[key] = {'total': round(sum(known)) if known else None, 'covered': len(known), 'posts': len(rows)}
    return result


def scan_buffer(workspace: Path, ids: list[str]) -> dict[str, Any]:
    store = ProfileStore(workspace)
    report = {'fetched_at': datetime.now(timezone.utc).isoformat(), 'profiles': {}, 'errors': {}}
    for pid in ids:
        profile = store.get(pid)
        try:
            _, instance = _instance(store, pid)
            posts = instance.buffer.posts_paginated(instance.org['id'], instance.channel['id'], READ_STATUSES)
            by_id = {str(p.get('id')): p for p in posts}
            by_part = {}
            for p in posts:
                n = bot.part_from_text(p.get('text', ''))
                if n is not None:
                    by_part.setdefault(n, []).append(p)
            rows = []
            for part, item in instance.state.get('items', {}).items():
                local = str(item.get('status') or '')
                remote = by_id.get(str(item.get('buffer_post_id'))) if item.get('buffer_post_id') else None
                part_no = int(part) if str(part).isdigit() else None
                if remote is not None:
                    remote_status = str(remote.get('status') or '')
                    if local == remote_status:
                        category = 'ok'
                    elif remote_status == 'sent':
                        category = 'published'
                    elif remote_status == 'error':
                        category = 'failed'
                    elif remote_status in ('draft', 'needs_approval'):
                        category = 'moved'
                    else:
                        category = 'changed'
                elif local in ('scheduled', 'sending') and item.get('buffer_post_id'):
                    alternatives = by_part.get(part_no, []) if part_no is not None else []
                    category = 'conflict' if alternatives else 'missing'
                    remote_status = tr('другой пост') if alternatives else tr('не найден')
                else:
                    category = 'ok' if local in ('returned', 'sent', 'error') else 'unknown'
                    remote_status = '—'
                if category != 'ok':
                    rows.append({'profile_id': pid, 'profile_name': profile.name, 'part': str(part),
                                 'local': local, 'remote': remote_status, 'category': category,
                                 'post_id': str(item.get('buffer_post_id') or '')})
            report['profiles'][pid] = {'name': profile.name, 'rows': rows, 'post_count': len(posts)}
        except Exception as exc:
            report['errors'][pid] = f'{profile.name}: {type(exc).__name__}: {exc}'
    return report


def apply_verified_states(workspace: Path, ids: list[str]) -> dict[str, Any]:
    from process_lock import exclusive_workspace
    results = {}
    with exclusive_workspace(workspace):
        store = ProfileStore(workspace)
        for pid in ids:
            try:
                profile, instance = _instance(store, pid)
                posts = instance.buffer.posts_paginated(instance.org['id'], instance.channel['id'], READ_STATUSES)
                before = {k: v.get('status') for k, v in instance.state.get('items', {}).items()}
                import shutil
                backup = profile.root / 'state_backups'
                backup.mkdir(parents=True, exist_ok=True)
                backup_file = backup / ('before_sync_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f') + '.json')
                shutil.copy2(instance.state_path, backup_file)
                instance.reconcile(posts)
                after = {k: v.get('status') for k, v in instance.state.get('items', {}).items()}
                results[pid] = {'changed': sum(before.get(k) != v for k, v in after.items()), 'backup': str(backup_file)}
            except Exception as exc:
                results[pid] = {'error': f'{type(exc).__name__}: {exc}'}
    return results
