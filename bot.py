from __future__ import annotations

from i18n import tr
import argparse
import json
import os
import random
import re
import shutil
import sys
import time
import uuid
import threading
from media_files import video_id, fingerprint, stable_file, validate_folders
from music_check import MusicCheckCancelled
from music_preflight import MusicPreflightBlocked, preflight_music
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any

import mimetypes
from urllib.parse import quote

import boto3
import requests
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values
from threading import Event
from typing import Callable

BUFFER_API = "https://api.buffer.com"
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
_log_callback: Callable[[str], None] | None = None
_log_context = threading.local()

def set_log_context(name: str = "") -> None:
    _log_context.name = name


def set_log_callback(callback: Callable[[str], None] | None) -> None:
    global _log_callback
    _log_callback = callback


PART_PATTERNS = [
    re.compile(r"\b(?:част[ьи]|частина|part)[ _-]*(\d+)\b", re.I),
    re.compile(r"\b(\d+)[ _-]*(?:част[ьи]|частина|part)\b", re.I),
]

class BotError(RuntimeError):
    pass

class PostRejected(BotError):
    pass

_secrets: set[str] = set()

def redact(value) -> str:
    text = str(value)
    hidden = tr('[скрыто]')
    for secret in sorted(_secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, hidden)
    return re.sub(r'Bearer\s+[^\s"\']+', f'Bearer {hidden}', text, flags=re.I)

def log(msg: str) -> None:
    msg = redact(msg)
    prefix = f"[{_log_context.name}] " if getattr(_log_context, "name", "") else ""
    text = f"[{datetime.now().astimezone():%Y-%m-%d %H:%M:%S}] {prefix}{msg}"
    if _log_callback is not None:
        _log_callback(text + "\n")
    else:
        print(text, flush=True)

def load_json(path: Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise BotError(
            tr('Повреждён JSON-файл: {0}. Операция остановлена. Восстанови файл из проверенной резервной копии. Не заменяй историю state.json пустой: это может привести к повторным публикациям.').format(path.resolve())
        ) from exc

def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)

def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def part_from_text(text: str) -> int | None:
    for pattern in PART_PATTERNS:
        m = pattern.search(text or "")
        if m:
            return int(m.group(1))
    return None

def list_videos(folder: Path) -> list[tuple[int, Path]]:
    chosen: dict[int, Path] = {}
    if not folder.exists():
        return []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        part = video_id(p)
        is_ad = p.stem.lower().endswith("_with_ad")
        if part not in chosen or (is_ad and not chosen[part].stem.lower().endswith("_with_ad")):
            chosen[part] = p
    return sorted(chosen.items(), key=lambda row: (row[0] >= 10**18,
                  row[1].name.casefold() if row[0] >= 10**18 else row[0]))

def episode_for(config: dict[str, Any], part: int) -> tuple[int, int]:
    for row in config.get("episode_ranges", []):
        if int(row["from"]) <= part <= int(row["to"]):
            return int(row["season"]), int(row["episode"])
    default = config.get("default_episode")
    if default:
        return int(default["season"]), int(default["episode"])
    raise BotError(tr('Для части {0} не указан сезон/серия в config.json').format(part))

DEFAULT_CAPTION_TEMPLATES = ["{filename}\n\n{hashtags}"]

DEFAULT_HASHTAG_SETS = [[]]

def caption_for(config: dict[str, Any], part: int, filename: str = '') -> str:
    templates = config.get("caption_templates") or DEFAULT_CAPTION_TEMPLATES
    tag_sets = config.get("hashtag_sets") or [config.get('hashtags', [])]

    override = (config.get("part_overrides") or {}).get(str(part), {})
    template = override.get("caption_template") or random.choice(templates)
    season, episode = ('', '')
    if re.search(r'\{(?:season|episode)(?:[!:}]|$)', template):
        season, episode = episode_for(config, part)
    tags = override.get("hashtags") or random.choice(tag_sets)
    if isinstance(tags, str):
        tags_text = tags.strip()
    else:
        tags_text = " ".join(tags)

    return template.format(
        part=part if part < 10**18 else '', season=season, episode=episode,
        filename=Path(filename).stem if filename else str(part), hashtags=tags_text
    ).strip()

class BufferClient:
    def __init__(self, key: str):
        self.key = key

    def gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = requests.post(
            BUFFER_API,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
            timeout=45,
            )
        except requests.RequestException as exc:
            raise BotError(tr('Нет ответа Buffer. Проверь сеть; результат отправки будет сверен перед повтором.')) from exc
        if r.status_code in (401, 403):
            raise BotError(tr('Buffer: проверь API-ключ и права доступа (HTTP 401/403).'))
        if r.status_code == 429:
            raise BotError(tr('Buffer: лимит запросов. Повтори позже; Retry-After: ') + str(r.headers.get('Retry-After', tr('не указан'))))
        try:
            payload = r.json()
        except Exception:
            raise BotError(tr('Buffer HTTP {0}: некорректный ответ сервиса').format(r.status_code))
        if r.status_code >= 400 or payload.get("errors"):
            raise BotError(tr('Buffer: ошибка запроса (HTTP {0}); проверь права, параметры и лимиты в Buffer.').format(r.status_code))
        return payload.get("data", {})

    def organizations(self):
        q = """query { account { organizations { id name ownerEmail } } }"""
        return self.gql(q)["account"]["organizations"]

    def channels(self, org_id: str):
        q = """query($org: OrganizationId!) { channels(input:{organizationId:$org}) { id name displayName service isQueuePaused } }"""
        return self.gql(q, {"org": org_id})["channels"]

    def posts_paginated(self, org_id: str, channel_id: str, statuses: list[str],
                        *, include_metrics: bool = False, max_pages: int = 100):
        allowed = {'draft', 'needs_approval', 'scheduled', 'sending', 'sent', 'error'}
        if not statuses or set(statuses) - allowed:
            raise BotError(tr('Неподдерживаемые статусы Buffer'))
        status_expr = ','.join(statuses)
        extra = 'metrics { type name value unit } metricsUpdatedAt' if include_metrics else ''
        q = f"""
        query($org: OrganizationId!, $channel: ChannelId!, $after: String) {{
          posts(first:100, after:$after, input:{{organizationId:$org,
            sort:[{{field:dueAt,direction:desc}},{{field:createdAt,direction:desc}}],
            filter:{{status:[{status_expr}], channelIds:[$channel]}}}}) {{
            edges {{ node {{ id text status dueAt sentAt createdAt channelId {extra} }} }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}"""
        posts = []
        cursor = None
        seen = set()
        for _ in range(max_pages):
            data = self.gql(q, {'org': org_id, 'channel': channel_id, 'after': cursor})['posts']
            if not isinstance(data.get('edges'), list) or not isinstance((data.get('pageInfo') or {}).get('hasNextPage'), bool):
                raise BotError(tr('Buffer вернул неполный список; вывод об отсутствии постов запрещён.'))
            nodes = [e['node'] for e in data['edges']]
            if any(not p.get('id') or not p.get('status') for p in nodes):
                raise BotError(tr('Buffer вернул неполные записи; сверка отменена.'))
            posts.extend(nodes)
            page = data.get('pageInfo') or {}
            if not page.get('hasNextPage'):
                return posts
            next_cursor = page.get('endCursor')
            if not next_cursor or next_cursor in seen:
                raise BotError(tr('Buffer вернул зацикленную или неполную пагинацию. Синхронизация отменена'))
            seen.add(next_cursor)
            cursor = next_cursor
        raise BotError(tr('Слишком много страниц Buffer. Увеличь лимит в настройках разработчика: результаты не полные'))

    def posts(self, org_id: str, channel_id: str, statuses: list[str]):
        return self.posts_paginated(org_id, channel_id, statuses)

    def create_video_post(self, channel_id: str, text: str, url: str, due_at: datetime, thumb_ms: int):
        q = """
        mutation($input: CreatePostInput!) {
          createPost(input:$input) {
            __typename
            ... on PostActionSuccess { post { id text status dueAt channelId } }
            ... on MutationError { message }
          }
        }"""
        inp = {
            "text": text,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": iso_utc(due_at),
            "assets": [{"video": {"url": url, "metadata": {"thumbnailOffset": int(thumb_ms)}}}],
        }
        result = self.gql(q, {"input": inp})["createPost"]
        if result.get("__typename") != "PostActionSuccess":
            if result.get('__typename') and result.get('message'):
                raise PostRejected(tr('Buffer отклонил пост: ') + redact(result['message']))
            raise BotError(tr('Buffer не подтвердил результат createPost.'))
        return result["post"]

class R2Client:
    def __init__(
        self,
        endpoint: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        public_base_url: str,
        namespace: str = "default",
    ):
        self.bucket = bucket
        self.namespace = re.sub(r"[^A-Za-z0-9_-]", "_", namespace)[:64] or "default"
        self.public_base_url = public_base_url.rstrip("/")
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint.rstrip("/"),
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=__import__('botocore.config', fromlist=['Config']).Config(
                connect_timeout=15, read_timeout=60, retries={'max_attempts': 3},
                request_checksum_calculation='when_required', response_checksum_validation='when_required'),
        )
        self.transfer = TransferConfig(
            multipart_threshold=64 * 1024 * 1024,
            multipart_chunksize=16 * 1024 * 1024,
            max_concurrency=4,
            use_threads=True,
        )

    def public_url(self, key: str) -> str:
        return f"{self.public_base_url}/{quote(key, safe='/')}"

    def upload(self, path: Path, part: int) -> tuple[str, str]:
        ext = path.suffix.lower() or ".mp4"
        key = f"multibott/{self.namespace}/jojo_part_{part}_{uuid.uuid4().hex[:12]}{ext}"
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"

        log(f"R2: {path.name} ({size/1024/1024:.1f} MB)")

        try:
            self.s3.upload_file(
                str(path),
                self.bucket,
                key,
                ExtraArgs={"ContentType": mime},
                Config=self.transfer,
            )
        except (BotoCoreError, ClientError) as exc:
            raise BotError(tr('R2: не удалось загрузить видео. Проверь сеть, ключи и права на bucket.')) from exc

        url = self.public_url(key)

        try:
            r = requests.head(url, allow_redirects=True, timeout=30)
            if r.status_code >= 400:
                r = requests.get(
                    url,
                    headers={"Range": "bytes=0-0"},
                    stream=True,
                    allow_redirects=True,
                    timeout=30,
                )
            if r.status_code >= 400:
                raise BotError(
                    tr('R2 файл загрузился, но публичный URL недоступен (HTTP {0}): {1}').format(r.status_code, url)
                )
        except requests.RequestException as exc:
            raise BotError(tr('Не удалось проверить публичный R2 URL: {0}').format(exc)) from exc

        return url, key

    def delete(self, key: str) -> bool:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            log(tr('Не удалось удалить R2-копию {0}: {1}').format(key, exc))
            return False

    def check_access(self) -> None:
        key = f"multibott/__check_{int(time.time())}.txt"
        body = b"multibott-r2-check"
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="text/plain",
            )
        except (BotoCoreError, ClientError) as exc:
            raise BotError(f"R2 credentials/bucket error: {exc}") from exc

        url = self.public_url(key)
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200 or r.content != body:
                raise BotError(
                    tr('R2 API работает, но публичный URL не работает: HTTP {0}, {1}').format(r.status_code, url)
                )
        finally:
            try:
                self.s3.delete_object(Bucket=self.bucket, Key=key)
            except Exception:
                pass

        log(tr('R2 OK: запись, публичное чтение и удаление работают.'))


class Bot:
    def __init__(self, root: Path, *, credentials_path: Path | None = None, profile_id: str = "default",
                 stop_event: Event | None = None):
        self.root = Path(root)
        self.profile_id = profile_id
        credentials = dotenv_values(credentials_path or self.root / ".env", interpolate=False)
        self.stop_event = stop_event
        self.check_music = (credentials.get('ACR_CHECK_WATCH') or '').strip() == '1'
        self.music_credentials = {
            key: credentials.get(key) for key in ('ACRCLOUD_TOKEN', 'ACRCLOUD_REGION', 'ACRCLOUD_CONTAINER_ID')
        }
        token = (self.music_credentials['ACRCLOUD_TOKEN'] or '').strip()
        if token:
            _secrets.add(token)
        self.config = load_json(root / "config.json", {})
        if not self.config:
            raise BotError(tr('Нет config.json'))
        if not isinstance(self.config, dict):
            raise BotError(tr('Некорректная структура config.json: {0}').format(self.root / 'config.json'))

        self.state_path = root / "state.json"
        self.state = load_json(self.state_path, {"items": {}})
        if (not isinstance(self.state, dict)
                or not isinstance(self.state.get('items'), dict)
                or any(not isinstance(item, dict) for item in self.state['items'].values())):
            raise BotError(
                tr('Некорректная структура истории: {0}. Операция остановлена. Восстанови историю из проверенной резервной копии.').format(self.state_path.resolve())
            )

        self.ready, self.scheduled, self.posted = validate_folders(root, self.config)
        for d in (self.ready, self.scheduled, self.posted):
            d.mkdir(parents=True, exist_ok=True)

        vals = {
            "BUFFER_API_KEY": (credentials.get("BUFFER_API_KEY") or "").strip(),
            "R2_ENDPOINT": (credentials.get("R2_ENDPOINT") or "").strip(),
            "R2_ACCESS_KEY_ID": (credentials.get("R2_ACCESS_KEY_ID") or "").strip(),
            "R2_SECRET_ACCESS_KEY": (credentials.get("R2_SECRET_ACCESS_KEY") or "").strip(),
            "R2_BUCKET_NAME": (credentials.get("R2_BUCKET_NAME") or "").strip(),
            "R2_PUBLIC_BASE_URL": (credentials.get("R2_PUBLIC_BASE_URL") or "").strip(),
        }
        missing = [k for k, v in vals.items() if not v]
        if missing:
            raise BotError(tr('Не заполнены в .env: ') + ", ".join(missing))

        _secrets.update(vals[k] for k in ('BUFFER_API_KEY', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY'))

        self.buffer = BufferClient(vals["BUFFER_API_KEY"])
        self.r2 = R2Client(
            vals["R2_ENDPOINT"],
            vals["R2_ACCESS_KEY_ID"],
            vals["R2_SECRET_ACCESS_KEY"],
            vals["R2_BUCKET_NAME"],
            vals["R2_PUBLIC_BASE_URL"],
            namespace=self.profile_id,
        )
        self.org = None
        self.channel = None

    def resolve(self, *, allow_paused: bool = False):
        orgs = self.buffer.organizations()
        wanted_org_id = str(self.config.get("buffer_organization_id", "")).strip()
        wanted_org = self.config.get("buffer_organization_name", "").strip().lower()
        if wanted_org_id:
            matches = [o for o in orgs if o["id"] == wanted_org_id]
            if not matches:
                raise BotError(tr('Организация Buffer с ID {0} не найдена. Проверь аккаунт.').format(wanted_org_id))
            self.org = matches[0]
        elif wanted_org:
            matches = [o for o in orgs if o["name"].strip().lower() == wanted_org]
            if not matches:
                raise BotError(tr('Organization не найдена. Доступно: {0}').format([o['name'] for o in orgs]))
            self.org = matches[0]
        elif len(orgs) == 1:
            self.org = orgs[0]
        else:
            raise BotError(tr('Укажи buffer_organization_name. Доступно: {0}').format([o['name'] for o in orgs]))

        channels = self.buffer.channels(self.org["id"])
        tt = [c for c in channels if str(c.get("service", "")).lower() == "tiktok"]
        wanted_id = str(self.config.get("target_tiktok_channel_id", "")).strip()
        wanted = self.config.get("target_tiktok_channel", "").strip().lower().lstrip("@")
        if wanted_id:
            matches = [c for c in tt if str(c.get("id")) == wanted_id]
            if not matches:
                raise BotError(tr('TikTok-канал Buffer с ID {0} больше не доступен. Проверь привязку профиля.').format(wanted_id))
            self.channel = matches[0]
        elif wanted:
            matches = [c for c in tt if wanted in {str(c.get("name","")).lower().lstrip("@"), str(c.get("displayName", "")).lower().lstrip("@")}]
            if not matches:
                raise BotError(tr("TikTok '{0}' не найден. Доступно: {1}").format(wanted, [(c.get('name'), c.get('displayName')) for c in tt]))
            self.channel = matches[0]
        else:
            raise BotError(tr('TikTok-канал не привязан. Открой «Аккаунты» → «Привязать Buffer» и выбери канал.'))

        if self.channel.get("isQueuePaused") and not allow_paused:
            raise BotError(tr('Очередь TikTok-канала в Buffer стоит на паузе'))
        log(f"Buffer OK: {self.org['name']} -> {self.channel.get('displayName') or self.channel.get('name')}")

    def all_posts(self):
        return self.buffer.posts(self.org["id"], self.channel["id"], ["scheduled", "sending", "sent", "error", "draft", "needs_approval"])

    def reconcile(self, posts):
        for key, item in self.state.get('items', {}).items():
            if item.get('status') not in ('submitting', 'uncertain'):
                continue
            matches = [p for p in posts if p.get('text') == item.get('caption')
                       and parse_dt(p.get('dueAt')) == parse_dt(item.get('due_at'))
                       and p.get('channelId', self.channel['id']) == self.channel['id']]
            if len(matches) == 1:
                item['buffer_post_id'] = matches[0]['id']
                item['status'] = matches[0]['status']
                log(tr('✓ Восстановлен ответ Buffer для {0}').format(item.get('filename', key)))
            else:
                item['status'] = 'uncertain'
                log(tr('⚠ {0}: результат отправки не подтверждён. Автоповтор заблокирован; проверь Buffer/TikTok.').format(item.get('filename', key)))
            save_json(self.state_path, self.state)
        by_id = {p["id"]: p for p in posts}
        changed = False
        for part, item in list(self.state.get("items", {}).items()):
            post = by_id.get(item.get("buffer_post_id"))
            if not post:
                continue
            status = post.get("status")
            if item.get("status") == "sent" and status != "sent":
                log(tr('⚠ Часть {0}: локально уже опубликована, Buffer показывает {1}. Не меняю статус опубликованного ролика автоматически').format(part, status))
                continue
            if item.get("status") != status:
                item["status"] = status
                changed = True
            filename = Path(item.get("filename", "")).name
            file = self.scheduled / filename
            source_name = Path(item.get('source_filename', filename)).name
            if not file.is_file() and (self.ready / source_name).is_file():
                file = self.ready / source_name
            if status == "sent":
                if file.is_file():
                    dest = self.posted / file.name
                    if dest.exists():
                        dest = self.posted / f"{file.stem}_{int(time.time())}{file.suffix}"
                    shutil.move(str(file), str(dest))
                    log(tr('✓ Часть {0} опубликована -> posted/').format(part))
                if self.config.get("cleanup_r2_after_sent", True) and item.get("r2_key") and not item.get("r2_cleaned"):
                    if self.r2.delete(item["r2_key"]) is not False:
                        item["r2_cleaned"] = True
                changed = True
            elif status == "error":
                if file.is_file() and not (self.ready / file.name).exists():
                    shutil.move(str(file), str(self.ready / file.name))
                log(tr('⚠ Часть {0}: Buffer error, файл возвращён в ready/').format(part))
                changed = True
            elif status in ('scheduled', 'sending', 'draft', 'needs_approval') and file.parent == self.ready and file.is_file():
                dest = self.scheduled / filename
                if not dest.exists():
                    shutil.move(str(file), str(dest))
        if changed:
            save_json(self.state_path, self.state)

    def restore_deleted_from_buffer(self, parts: list[int]) -> dict[str, list[int]]:
        self.resolve(allow_paused=True)
        if not parts:
            raise BotError(tr('Выбери хотя бы одну часть в истории публикаций'))
        if len(parts) != len(set(parts)) or any(not isinstance(n, int) or n <= 0 for n in parts):
            raise BotError(tr('Некорректный список частей для возврата'))

        all_live = self.buffer.posts(self.org["id"], self.channel["id"],
                                     ["scheduled", "sending", "sent", "error", "draft", "needs_approval"])
        self.reconcile(all_live)
        active = [p for p in all_live if p.get('status') in ('scheduled', 'sending', 'draft', 'needs_approval')]
        sent = [p for p in all_live if p.get('status') == 'sent']
        by_id = {str(p.get("id")): p for p in all_live}
        active_parts = {n for p in active if (n := part_from_text(p.get("text", ""))) is not None}
        sent_parts = {n for p in sent if (n := part_from_text(p.get("text", ""))) is not None}

        results: dict[str, list[int]] = {"restored": [], "skipped": []}
        for part in parts:
            key = str(part)
            item = self.state.get("items", {}).get(key)
            if not item or item.get("status") not in ("scheduled", "sending", "submitting", "uncertain", "error"):
                log(tr('Часть {0}: возврат доступен только для статуса «Запланировано/Отправка»').format(part))
                results["skipped"].append(part)
                continue
            original = by_id.get(str(item.get("buffer_post_id")))
            if original is not None:
                log(tr('⛔ Часть {0}: пост всё ещё в Buffer (статус {1}). Ничего не изменено').format(part, original.get('status')))
                results["skipped"].append(part)
                continue
            if part in active_parts or part in sent_parts:
                log(tr('⛔ Часть {0}: найден другой действующий или опубликованный пост с этим номером. Ничего не изменено').format(part))
                results["skipped"].append(part)
                continue

            original_name = Path(str(item.get("filename", ""))).name
            if not original_name or original_name in (".", ".."):
                log(tr('⚠ Часть {0}: имя исходного файла отсутствует').format(part))
                results["skipped"].append(part)
                continue
            scheduled_file = self.scheduled / original_name
            existing_ready = dict(list_videos(self.ready)).get(part)
            if not scheduled_file.is_file() and existing_ready is None:
                log(tr('⚠ Часть {0}: файл не найден ни в scheduled, ни в ready').format(part))
                results["skipped"].append(part)
                continue
            if scheduled_file.is_file():
                if existing_ready is None and not (self.ready / original_name).exists():
                    shutil.move(str(scheduled_file), str(self.ready / original_name))
                else:
                    backup_dir = self.root / "recovery_backups"
                    backup_dir.mkdir(exist_ok=True)
                    dest = backup_dir / original_name
                    if dest.exists():
                        dest = backup_dir / f"{Path(original_name).stem}_{uuid.uuid4().hex[:8]}{Path(original_name).suffix}"
                    shutil.move(str(scheduled_file), str(dest))
                    log(tr('Часть {0}: дубль из scheduled сохранён в recovery_backups/').format(part))
            now = datetime.now(timezone.utc).isoformat()
            self.state.setdefault("recovery_history", []).append({
                "part": part, "old_buffer_post_id": item.get("buffer_post_id"),
                "old_status": item.get("status"), "recovered_at": now,
                "reason": "Manual recovery: Buffer post not found in queried statuses",
            })
            item["status"] = "returned"
            item["returned_at"] = now
            save_json(self.state_path, self.state)
            results["restored"].append(part)
            log(tr('↩ Часть {0}: возвращена в ready; теперь её можно снова поставить в очередь').format(part))
        log(tr('Возврат завершён: {0} возвращено, {1} пропущено. Публикации не создавались.').format(len(results['restored']), len(results['skipped'])))
        return results

    def sync(self):
        self.resolve(allow_paused=True)
        self.reconcile(self.all_posts())
        log(tr('Сверка очереди завершена. Новые публикации не создавались.'))

    def occupied_parts(self, posts):
        out = set()
        for p in posts:
            n = part_from_text(p.get("text", ""))
            if n is not None:
                out.add(n)
        for k, v in self.state.get("items", {}).items():
            if v.get("status") in {"uploaded", "submitting", "uncertain", "scheduled", "sending", "sent", "draft", "needs_approval", "error"}:
                try: out.add(int(k))
                except ValueError: pass
        return out

    def local_tz(self):
        name = str(self.config.get("timezone", "Europe/Kyiv")).strip()
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            raise BotError(tr('Неизвестный часовой пояс: {0}. Укажи IANA-имя, например Europe/Kyiv.').format(name))

    @staticmethod
    def _hm(value: str) -> tuple[int, int]:
        try:
            h, m = value.split(":", 1)
            h, m = int(h), int(m)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            return h, m
        except Exception:
            raise BotError(tr('Некорректное время окна: {0!r}. Нужно HH:MM').format(value))

    def daily_windows(self):
        rows = self.config.get("daily_windows") or [
            {"start": "11:30", "end": "13:00"},
            {"start": "19:30", "end": "21:30"},
        ]
        windows = []
        for row in rows:
            sh, sm = self._hm(str(row["start"]))
            eh, em = self._hm(str(row["end"]))
            if (eh, em) <= (sh, sm):
                raise BotError(tr('Окна публикаций через полночь пока не поддерживаются'))
            windows.append(((sh, sm), (eh, em)))
        windows.sort(key=lambda x: x[0])
        return windows

    def _slot_key(self, dt: datetime):
        local = dt.astimezone(self.local_tz())
        for idx, (start_hm, end_hm) in enumerate(self.daily_windows()):
            start = local.replace(hour=start_hm[0], minute=start_hm[1], second=0, microsecond=0)
            end = local.replace(hour=end_hm[0], minute=end_hm[1], second=0, microsecond=0)
            if start <= local <= end:
                return (local.date().isoformat(), idx)
        return None

    def used_window_slots(self, posts):
        used = set()
        for p in posts:
            if p.get("status") not in {"scheduled", "sending", "sent"}:
                continue
            dt = parse_dt(p.get("dueAt") or p.get("sentAt"))
            if dt:
                key = self._slot_key(dt)
                if key:
                    used.add(key)
        for item in self.state.get('items', {}).values():
            if item.get('status') in ('submitting', 'uncertain'):
                dt = parse_dt(item.get('due_at'))
                if dt and (key := self._slot_key(dt)):
                    used.add(key)
        return used

    def next_due(self, cursor: datetime, used_slots: set[tuple[str, int]]):
        tz = self.local_tz()
        now_local = datetime.now(tz)
        lead = timedelta(minutes=int(self.config.get("minimum_schedule_lead_minutes", 15)))
        earliest = max(cursor.astimezone(tz), now_local + lead)
        windows = self.daily_windows()

        for day_offset in range(366):
            day = earliest.date() + timedelta(days=day_offset)
            for idx, (start_hm, end_hm) in enumerate(windows):
                key = (day.isoformat(), idx)
                if key in used_slots:
                    continue

                start = datetime(day.year, day.month, day.day, start_hm[0], start_hm[1], tzinfo=tz)
                end = datetime(day.year, day.month, day.day, end_hm[0], end_hm[1], tzinfo=tz)
                lower = max(start, earliest)
                if lower > end:
                    continue

                span_minutes = int((end - lower).total_seconds() // 60)
                due = lower + timedelta(minutes=random.randint(0, max(0, span_minutes)))
                due = due.replace(second=0, microsecond=0)
                used_slots.add(key)
                return due.astimezone(timezone.utc)

        raise BotError(tr('Не удалось подобрать время публикации'))

    def check(self):
        self.resolve()
        self.r2.check_access()
        posts = self.all_posts()
        n = sum(1 for p in posts if p.get("status") in {"scheduled", "sending"})
        log(tr('Очередь Buffer: {0} пост(ов). Видео ready/: {1}.').format(n, len(list_videos(self.ready))))
        log(tr('Проверка завершена успешно.'))

    def plan(self):
        self.resolve()
        posts = self.all_posts()
        occupied = self.occupied_parts(posts)
        queued = sum(1 for p in posts if p.get("status") in {"scheduled", "sending"})
        slots = max(0, int(self.config.get("max_queue_posts", 10)) - queued)
        used_slots = self.used_window_slots(posts)
        future_times = [
            parse_dt(p.get("dueAt")) for p in posts
            if p.get("status") in {"scheduled", "sending"} and p.get("dueAt")
        ]
        future_times = [x for x in future_times if x]
        cursor = max([datetime.now(timezone.utc)] + future_times)
        log(tr('--- ПЛАН БЕЗ ЗАГРУЗКИ ---'))
        for part, path in [(n,p) for n,p in list_videos(self.ready) if n not in occupied][:slots]:
            due = self.next_due(cursor, used_slots)
            log(f"{path.name} -> {due.astimezone(self.local_tz()):%d.%m.%Y %H:%M}")
            log(caption_for(self.config, part, path.name))
            cursor = due
        log("--------------------------")

    def fill(self, *, stop_event: Event | None = None):
        checked = None
        if self.check_music:
            stop_event = stop_event or self.stop_event or Event()
            checked = preflight_music(self.ready, self.music_credentials, stop_event, log)
            if stop_event.is_set():
                raise MusicCheckCancelled()
        self.resolve()
        binding = self.state.get('channel_id')
        if binding and binding != self.channel['id']:
            raise BotError(tr('История принадлежит другому Buffer-каналу. Создай отдельный профиль.'))
        self.state['channel_id'] = self.channel['id']
        posts = self.all_posts()
        self.reconcile(posts)
        posts = self.all_posts()
        occupied = self.occupied_parts(posts)
        queued = sum(1 for p in posts if p.get("status") in {"scheduled", "sending"})
        maxq = int(self.config.get("max_queue_posts", 10))
        queued += sum(1 for v in self.state['items'].values() if v.get('status') in ('submitting', 'uncertain'))
        slots = max(0, maxq - queued)
        log(tr('Buffer queue: {0}/{1}; свободно {2}.').format(queued, maxq, slots))
        if slots == 0:
            return
        candidates = [(n,p) for n,p in list_videos(self.ready)
                      if n not in occupied or self.state['items'].get(str(n), {}).get('status') == 'uploaded']
        if checked is not None:
            for _, video in candidates:
                if video not in checked:
                    raise MusicPreflightBlocked(
                        tr('Файл появился после проверки музыки: {0}. Запусти проверку снова.').format(video.name)
                    )
        if not candidates:
            blocked = [(n, self.state.get("items", {}).get(str(n), {}).get("status"))
                       for n, _ in list_videos(self.ready) if n in occupied]
            if blocked:
                sample = ", ".join(f"{n} ({status or 'Buffer'})" for n, status in blocked[:12])
                log(tr('Нет новых частей: {0} файл(ов) в ready заблокированы историей или Buffer: {1}').format(len(blocked), sample))
                log(tr('Если ты удалил посты в Buffer, выдели эти части на главной и нажми «↩ Вернуть в ready».'))
            else:
                log(tr('Новых видео для очереди нет. Поддерживаются MP4, MOV и WebM с любыми именами.'))
            return

        used_slots = self.used_window_slots(posts)
        future_times = [
            parse_dt(p.get("dueAt")) for p in posts
            if p.get("status") in {"scheduled", "sending"} and p.get("dueAt")
        ]
        future_times = [x for x in future_times if x]
        cursor = max([datetime.now(timezone.utc)] + future_times)
        thumb = int(self.config.get("thumbnail_offset_ms", 1000))
        submitted = 0
        for part, video in candidates:
            if submitted >= slots:
                break
            try:
                with stable_file(video, float(self.config.get('file_stable_seconds', 15))):
                    digest = fingerprint(video)
                    if checked is not None and digest != checked[video]:
                        raise MusicPreflightBlocked(
                            tr('Файл изменился после проверки музыки: {0}. Запусти проверку снова.').format(video.name)
                        )
                    duplicate = any(k != str(part) and v.get('sha256') == digest
                                    and v.get('status') not in ('returned', 'rejected')
                                    for k, v in self.state['items'].items())
                    if duplicate:
                        log(tr('↷ {0}: содержимое уже есть в истории этого аккаунта.').format(video.name))
                        continue
                    previous = self.state['items'].get(str(part), {})
                    if previous.get('status') == 'uploaded' and previous.get('sha256') != digest:
                        raise BotError(tr('Загруженный файл изменился. Сохрани исходный файл и проверь историю.'))
                    due = self.next_due(cursor, used_slots)
                    text = previous.get('caption') if previous.get('status') == 'uploaded' else caption_for(self.config, part, video.name)
                    if checked is not None and stop_event.is_set():
                        raise MusicCheckCancelled()
                    if previous.get('status') == 'uploaded':
                        url, r2_key = previous['r2_url'], previous['r2_key']
                    else:
                        url, r2_key = self.r2.upload(video, part)
                    item = {'filename': video.name, 'source_filename': video.name, 'due_at': iso_utc(due),
                            'r2_key': r2_key, 'r2_url': url, 'status': 'uploaded',
                            'caption': text, 'sha256': digest,
                            'channel_id': self.channel['id'],
                            'created_at': datetime.now(timezone.utc).isoformat()}
                    self.state['items'][str(part)] = item
                    save_json(self.state_path, self.state)
                    item['status'] = 'submitting'
                    save_json(self.state_path, self.state)
                    try:
                        post = self.buffer.create_video_post(self.channel['id'], text, url, due, thumb)
                        if not post.get('id') or not post.get('status'):
                            raise BotError(tr('Buffer вернул неполное подтверждение.'))
                    except PostRejected:
                        item['status'] = 'rejected'
                        save_json(self.state_path, self.state)
                        self.r2.delete(r2_key)
                        raise
                    except Exception as exc:
                        item['status'] = 'uncertain'
                        save_json(self.state_path, self.state)
                        try:
                            self.reconcile(self.all_posts())
                        except Exception:
                            pass
                        raise BotError(tr('{0}: ответ Buffer не подтверждён; повтор заблокирован. Выполни сверку. {1}').format(video.name, redact(exc))) from exc
                    item.update(buffer_post_id=post['id'], status=post['status'], due_at=post.get('dueAt') or iso_utc(due))
                    save_json(self.state_path, self.state)
            except OSError as exc:
                log(f'↷ {video.name}: {exc}')
                continue
            except Exception:
                raise
            dest = self.scheduled / video.name
            if dest.exists():
                dest = self.scheduled / f"{video.stem}_{uuid.uuid4().hex[:8]}{video.suffix}"
            item['filename'] = dest.name
            save_json(self.state_path, self.state)
            try:
                shutil.move(str(video), str(dest))
            except OSError as exc:
                log(tr('⚠ Пост в Buffer создан, но видео осталось в ready: {0}. Повторно не отправлять!').format(exc))
                raise BotError(tr('Пост создан в Buffer, но файл не перемещён. История сохранена, проверь папки.')) from exc
            log(tr('✓ Часть {0} добавлена в Buffer').format(part))
            submitted += 1
            cursor = due

    def watch(self, stop_event: Event | None = None):
        mins = max(1, int(self.config.get("watch_interval_minutes", 60)))
        stop_event = stop_event or self.stop_event or Event()
        log(tr('WATCH: пополняю очередь каждые {0} мин.').format(mins))
        try:
            while not stop_event.is_set():
                try:
                    self.fill(stop_event=stop_event)
                except MusicPreflightBlocked:
                    stop_event.set()
                    raise
                except (KeyboardInterrupt, MusicCheckCancelled):
                    break
                except Exception as exc:
                    log(tr('ОШИБКА: {0}: {1}').format(type(exc).__name__, exc))
                if stop_event.wait(mins * 60):
                    break
        finally:
            log(tr('WATCH: остановлен.'))

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--plan", action="store_true")
    g.add_argument("--watch", action="store_true")
    args = ap.parse_args()
    try:
        bot = Bot(Path(__file__).resolve().parent)
        if args.check: bot.check()
        elif args.plan: bot.plan()
        elif args.watch: bot.watch()
        else: bot.fill()
    except (BotError, MusicPreflightBlocked) as exc:
        print(tr('\nОШИБКА: {0}').format(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        print(tr('\nОстановлено.'))
    except Exception as exc:
        print(tr('\nНЕОЖИДАННАЯ ОШИБКА: {0}: {1}').format(type(exc).__name__, exc))
        sys.exit(1)

if __name__ == "__main__":
    main()
