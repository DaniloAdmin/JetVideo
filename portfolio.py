from __future__ import annotations

from i18n import tr
import time
from pathlib import Path
from threading import Event

import bot
from profiles import ProfileStore
from process_lock import exclusive_workspace


class PortfolioError(RuntimeError):
    pass


def run_profiles(workspace: Path, ids: list[str], job: str) -> dict[str, str | None]:
    if job not in ('check', 'plan', 'fill', 'sync'):
        raise ValueError(tr('Неизвестная операция'))
    if len(ids) != len(set(ids)):
        raise PortfolioError(tr('Аккаунт повторяется в списке обработки'))
    with exclusive_workspace(workspace):
        store = ProfileStore(workspace)
        selected = [store.get(i) for i in ids]
        if any(not p.active for p in selected):
            raise PortfolioError(tr('Архивные аккаунты нельзя публиковать'))
        store.check_channels_unique(ids)
        results = {}
        prepared = {}
        if len(selected) > 1 and job == 'fill':
            used_channel_ids = {}
            for profile in selected:
                bot.set_log_context(profile.name)
                try:
                    instance = bot.Bot(profile.root, credentials_path=store.workspace / '.env', profile_id=profile.id)
                    instance.resolve()
                    channel_id = instance.channel['id']
                    if channel_id in used_channel_ids:
                        earlier = used_channel_ids[channel_id]
                        msg = tr('Два профиля ссылаются на один Buffer-канал: {0} и {1}. Отправка заблокирована.').format(earlier.name, profile.name)
                        results[earlier.id] = msg
                        results[profile.id] = msg
                        prepared.pop(earlier.id, None)
                    else:
                        used_channel_ids[channel_id] = profile
                        prepared[profile.id] = instance
                except Exception as exc:
                    results[profile.id] = f'{type(exc).__name__}: {exc}'
                    bot.log(tr('✕ Ошибка проверки канала: ') + results[profile.id])
                finally:
                    bot.set_log_context('')
        for profile in selected:
            if profile.id in results:
                continue
            bot.set_log_context(profile.name)
            try:
                instance = prepared.get(profile.id) or bot.Bot(profile.root, credentials_path=store.workspace / '.env', profile_id=profile.id)
                getattr(instance, job)()
                results[profile.id] = None
            except Exception as exc:
                results[profile.id] = f'{type(exc).__name__}: {exc}'
                bot.log(tr('✕ Ошибка: ') + results[profile.id])
            finally:
                bot.set_log_context('')
        return results


def restore_profile_parts(workspace: Path, profile_id: str, parts: list[int]) -> dict[str, list[int]]:
    with exclusive_workspace(workspace):
        store = ProfileStore(workspace)
        profile = store.get(profile_id)
        bot.set_log_context(profile.name)
        try:
            instance = bot.Bot(profile.root, credentials_path=store.workspace / '.env', profile_id=profile.id)
            return instance.restore_deleted_from_buffer(parts)
        finally:
            bot.set_log_context('')


def watch_profiles(workspace: Path, ids: list[str], stop_event: Event) -> None:
    if len(ids) != len(set(ids)):
        raise PortfolioError(tr('Аккаунт повторяется в WATCH'))
    with exclusive_workspace(workspace):
        store = ProfileStore(workspace)
        chosen = [store.get(i) for i in ids]
        if any(not p.active for p in chosen):
            raise PortfolioError(tr('Архивные аккаунты нельзя включать в WATCH'))
        store.check_channels_unique(ids)
        live_channels = {}
        if len(chosen) > 1:
            seen_channels = {}
            verified = []
            for p in chosen:
                bot.set_log_context(p.name)
                try:
                    instance = bot.Bot(p.root, credentials_path=store.workspace / '.env', profile_id=p.id)
                    instance.resolve()
                    channel = instance.channel['id']
                    if channel in seen_channels:
                        raise PortfolioError(tr('Профили «{0}» и «{1}» указывают на один Buffer-канал. WATCH заблокирован.').format(seen_channels[channel], p.name))
                    seen_channels[channel] = p.name
                    live_channels[p.id] = channel
                    verified.append(p)
                except PortfolioError:
                    raise
                except Exception as exc:
                    bot.log(tr('✕ Не удалось проверить канал; повторим в следующем цикле WATCH: {0}').format(exc))
                finally:
                    bot.set_log_context('')
        due = {p.id: 0.0 for p in chosen}
        bot.log(tr('WATCH: слежу за {0} аккаунтами. Операции идут по очереди.').format(len(chosen)))
        while not stop_event.is_set():
            for profile in chosen:
                if stop_event.is_set():
                    break
                if time.monotonic() < due[profile.id]:
                    continue
                bot.set_log_context(profile.name)
                interval = 60
                try:
                    instance = bot.Bot(profile.root, credentials_path=store.workspace / '.env', profile_id=profile.id)
                    interval = max(1, int(instance.config.get('watch_interval_minutes', 60)))
                    if len(chosen) > 1:
                        instance.resolve()
                        channel = instance.channel['id']
                        if any(pid != profile.id and cid == channel for pid, cid in live_channels.items()):
                            raise PortfolioError(tr('Buffer-канал уже используется другим профилем. Отправка заблокирована.'))
                        live_channels[profile.id] = channel
                    instance.fill()
                except Exception as exc:
                    bot.log(tr('✕ Ошибка WATCH: {0}: {1}').format(type(exc).__name__, exc))
                finally:
                    due[profile.id] = time.monotonic() + interval * 60
                    bot.set_log_context('')
            if stop_event.is_set():
                break
            wait_time = max(0.05, min(due.values()) - time.monotonic()) if due else 1
            stop_event.wait(wait_time)
        bot.log(tr('WATCH для аккаунтов остановлен.'))
