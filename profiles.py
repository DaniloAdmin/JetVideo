from __future__ import annotations

from i18n import tr
import copy
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from media_files import validate_folders

REGISTRY = 'profiles.json'
DEFAULT_PROFILE_ID = 'original'
VIDEO_FOLDERS = ('videos_folder', 'scheduled_folder', 'posted_folder')
DEFAULT_FOLDERS = ('ready', 'scheduled', 'posted')


class ProfileError(ValueError):
    pass


def _read(path: Path, default: Any):
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError) as exc:
        raise ProfileError(tr('Нельзя прочитать {0}: {1}').format(path.name, exc)) from exc


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    root: Path
    active: bool = True


def _new_registry():
    return {'version': 1, 'selected_id': DEFAULT_PROFILE_ID, 'profiles': [
        {'id': DEFAULT_PROFILE_ID, 'name': tr('Основной аккаунт'), 'folder': '.', 'active': True}
    ]}


def _validate(data: Any, workspace: Path) -> None:
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('profiles'), list):
        raise ProfileError(tr('Неизвестный формат profiles.json; сделай резервную копию и проверь файл.'))
    if not data['profiles']:
        raise ProfileError(tr('В profiles.json нет аккаунтов'))
    ids = set()
    folders = set()
    for row in data['profiles']:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', row['id']):
            raise ProfileError(tr('Некорректный ID профиля'))
        if row['id'] in ids:
            raise ProfileError(tr('Повторный ID в profiles.json'))
        ids.add(row['id'])
        if not isinstance(row.get('name'), str) or not row['name'].strip():
            raise ProfileError(tr('Пустое имя профиля'))
        folder = row.get('folder')
        if not isinstance(folder, str) or not folder:
            raise ProfileError(tr('Не указан каталог профиля'))
        rel = Path(folder)
        resolved = (workspace / rel).resolve()
        workspace_resolved = workspace.resolve()
        if rel.is_absolute() or (resolved != workspace_resolved and (workspace_resolved / 'profiles').resolve() not in resolved.parents):
            raise ProfileError(tr('Небезопасный каталог профиля: {0}').format(folder))
        if str(resolved).casefold() in folders:
            raise ProfileError(tr('Одинаковые каталоги для двух профилей'))
        folders.add(str(resolved).casefold())
    if data.get('selected_id') not in ids:
        raise ProfileError(tr('Сохранённый выбранный профиль отсутствует'))
    if not any(row.get('active', True) for row in data['profiles']):
        raise ProfileError(tr('Должен быть хотя бы один активный профиль'))


class ProfileStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        if not (self.workspace / 'config.json').is_file() or not (self.workspace / 'state.json').is_file():
            raise ProfileError(tr('В рабочей папке нет config.json и state.json'))
        self.registry_path = self.workspace / REGISTRY
        if not self.registry_path.exists():
            previous_profiles = self.workspace / 'profiles'
            if previous_profiles.exists() and any(previous_profiles.glob('*/state.json')):
                raise ProfileError(tr('profiles.json отсутствует, но найдены данные дополнительных аккаунтов. Восстанови profiles.json из резервной копии: создавать новый список небезопасно.'))
            _save(self.registry_path, _new_registry())
        self.data = _read(self.registry_path, {})
        _validate(self.data, self.workspace)

    def refresh(self):
        self.data = _read(self.registry_path, {})
        _validate(self.data, self.workspace)
        return self

    def _commit(self):
        _validate(self.data, self.workspace)
        _save(self.registry_path, self.data)

    def list(self, *, active_only=False) -> list[Profile]:
        result = [Profile(row['id'], row['name'], (self.workspace / row['folder']).resolve(), row.get('active', True))
                  for row in self.data['profiles']]
        return [p for p in result if p.active] if active_only else result

    def get(self, profile_id: str) -> Profile:
        for p in self.list():
            if p.id == profile_id:
                return p
        raise ProfileError(tr('Аккаунт не найден'))

    def selected(self) -> Profile:
        return self.get(self.data['selected_id'])

    def select(self, profile_id: str):
        if not self.get(profile_id).active:
            raise ProfileError(tr('Профиль в архиве. Сначала восстанови его.'))
        self.data['selected_id'] = profile_id
        self._commit()

    def _check_unique_channel(self, cfg: dict, exclude_id=None):
        cid = str(cfg.get('target_tiktok_channel_id', '')).strip()
        name = str(cfg.get('target_tiktok_channel', '')).strip().casefold().lstrip('@')
        org_id = str(cfg.get('buffer_organization_id', '')).strip()
        org_name = str(cfg.get('buffer_organization_name', '')).strip().casefold()
        for p in self.list():
            if p.id == exclude_id:
                continue
            other = _read(p.root / 'config.json', {})
            other_cid = str(other.get('target_tiktok_channel_id', '')).strip()
            if cid and other_cid and cid == other_cid:
                raise ProfileError(tr('Этот Buffer-канал уже привязан к «{0}»').format(p.name))
            other_name = str(other.get('target_tiktok_channel', '')).strip().casefold().lstrip('@')
            other_org_id = str(other.get('buffer_organization_id', '')).strip()
            other_org_name = str(other.get('buffer_organization_name', '')).strip().casefold()
            demonstrably_different = bool((org_id and other_org_id and org_id != other_org_id) or
                                           (org_name and other_org_name and org_name != other_org_name and
                                            not (org_id and other_org_id and org_id == other_org_id)))
            if name and other_name and name == other_name and not demonstrably_different and (not cid or not other_cid):
                raise ProfileError(tr('TikTok «{0}» уже настроен для «{1}»').format(name, p.name))

    def create(self, name: str, *, source_id: str | None = None,
               channel_name: str = '', org_name: str = '', channel_id: str = '', org_id: str = '') -> Profile:
        name = name.strip()
        if not (1 <= len(name) <= 70):
            raise ProfileError(tr('Название аккаунта должно содержать от 1 до 70 символов'))
        if any(p.name.casefold() == name.casefold() for p in self.list()):
            raise ProfileError(tr('Аккаунт с таким названием уже есть'))
        source = self.get(source_id or self.selected().id)
        cfg = _read(source.root / 'config.json', {})
        cfg = copy.deepcopy(cfg)
        for key, folder in zip(VIDEO_FOLDERS, DEFAULT_FOLDERS):
            cfg[key] = folder
        cfg['target_tiktok_channel'] = channel_name.strip().lstrip('@')
        cfg['buffer_organization_name'] = org_name.strip()
        cfg['target_tiktok_channel_id'] = channel_id.strip()
        cfg['buffer_organization_id'] = org_id.strip()
        self._check_unique_channel(cfg)
        slug = 'p_' + uuid.uuid4().hex[:12]
        root = self.workspace / 'profiles' / slug
        if root.exists():
            raise ProfileError(tr('Случайный конфликт ID папки. Попробуй ещё раз.'))
        try:
            root.mkdir(parents=True, exist_ok=False)
            _save(root / 'config.json', cfg)
            _save(root / 'state.json', {'items': {}})
            for folder in DEFAULT_FOLDERS:
                (root / folder).mkdir(exist_ok=False)
            self.data['profiles'].append({'id': slug, 'name': name, 'folder': f'profiles/{slug}', 'active': True})
            self._commit()
        except Exception:
            self.data['profiles'] = [p for p in self.data['profiles'] if p['id'] != slug]
            if root.exists():
                shutil.rmtree(root)
            raise
        return self.get(slug)

    def rename(self, profile_id: str, name: str):
        name = name.strip()
        if not name or len(name) > 70:
            raise ProfileError(tr('Название должно содержать от 1 до 70 символов'))
        if any(p.id != profile_id and p.name.casefold() == name.casefold() for p in self.list()):
            raise ProfileError(tr('Название уже занято'))
        self.get(profile_id)
        for row in self.data['profiles']:
            if row['id'] == profile_id:
                row['name'] = name
        self._commit()

    def set_active(self, profile_id: str, active: bool):
        selected = self.get(profile_id)
        if selected.active == active:
            return
        if not active and sum(1 for p in self.list(active_only=True)) <= 1:
            raise ProfileError(tr('Нельзя архивировать последний активный аккаунт'))
        for row in self.data['profiles']:
            if row['id'] == profile_id:
                row['active'] = active
        if not active and self.data['selected_id'] == profile_id:
            self.data['selected_id'] = next(row['id'] for row in self.data['profiles'] if row.get('active', True))
        self._commit()

    def save_channel(self, profile_id: str, *, channel_name: str, channel_id: str,
                     org_name: str, org_id: str):
        p = self.get(profile_id)
        cfg = _read(p.root / 'config.json', {})
        state = _read(p.root / 'state.json', {'items': {}})
        old_channel = state.get('channel_id') or cfg.get('target_tiktok_channel_id')
        if state.get('items') and old_channel and channel_id.strip() != old_channel:
            raise ProfileError(tr('В профиле есть история другого канала. Создай отдельный профиль.'))
        cfg.update(target_tiktok_channel=channel_name.strip().lstrip('@'),
                   target_tiktok_channel_id=channel_id.strip(),
                   buffer_organization_name=org_name.strip(),
                   buffer_organization_id=org_id.strip())
        if not cfg['target_tiktok_channel']:
            raise ProfileError(tr('Укажи TikTok-канал'))
        self._check_unique_channel(cfg, exclude_id=profile_id)
        _save(p.root / 'config.json', cfg)

    def check_channels_unique(self, profile_ids: list[str]):
        chosen = set(profile_ids)
        for p in self.list():
            if p.id not in chosen:
                continue
            cfg = _read(p.root / 'config.json', {})
            self._check_unique_channel(cfg, exclude_id=p.id)
        self.check_folders_unique()

    def check_folders_unique(self, replacement_id=None, replacement_config=None):
        used = []
        for profile in self.list():
            config = replacement_config if profile.id == replacement_id else _read(profile.root / 'config.json', {})
            for folder in validate_folders(profile.root, config):
                for other, name in used:
                    if folder == other or folder in other.parents or other in folder.parents:
                        raise ProfileError(tr('Папки аккаунтов пересекаются: «{0}» и «{1}».').format(name, profile.name))
                used.append((folder, profile.name))

    def save_folders(self, profile_id, config):
        self.check_folders_unique(profile_id, config)
        _save(self.get(profile_id).root / 'config.json', config)
        for folder in validate_folders(self.get(profile_id).root, config):
            folder.mkdir(parents=True, exist_ok=True)

    def remove(self, profile_id):
        profile = self.get(profile_id)
        remaining = [p for p in self.list(active_only=True) if p.id != profile_id]
        if not remaining:
            raise ProfileError(tr('Нельзя удалить последний активный аккаунт.'))
        self.data['profiles'] = [row for row in self.data['profiles'] if row['id'] != profile_id]
        if self.data['selected_id'] == profile_id:
            self.data['selected_id'] = remaining[0].id
        self._commit()
        return profile.root
