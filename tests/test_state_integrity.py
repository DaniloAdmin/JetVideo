import json
from unittest.mock import Mock

import pytest

import bot


@pytest.mark.parametrize('content', [b'', b'\0' * 12328, b'{"items":', b'\xff'])
def test_corrupted_history_is_reported_without_changes(tmp_path, monkeypatch, content):
    (tmp_path / 'config.json').write_text('{"videos_folder":"ready"}', encoding='utf-8')
    history = tmp_path / 'state.json'
    history.write_bytes(content)
    buffer_client = Mock(side_effect=AssertionError('No API clients allowed'))
    r2_client = Mock(side_effect=AssertionError('No API clients allowed'))
    monkeypatch.setattr(bot, 'BufferClient', buffer_client)
    monkeypatch.setattr(bot, 'R2Client', r2_client)
    with pytest.raises(bot.BotError, match='Повреждён JSON-файл') as err:
        bot.Bot(tmp_path)
    assert str(history.resolve()) in str(err.value)
    assert 'повторным публикациям' in str(err.value)
    assert history.read_bytes() == content
    assert not (tmp_path / 'ready').exists()
    buffer_client.assert_not_called()
    r2_client.assert_not_called()


@pytest.mark.parametrize('state', [[], {}, {'items': []}, {'items': {'1': None}}])
def test_invalid_history_structure_is_blocked(tmp_path, state):
    (tmp_path / 'config.json').write_text('{"videos_folder":"ready"}', encoding='utf-8')
    history = tmp_path / 'state.json'
    history.write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(bot.BotError, match='Некорректная структура истории'):
        bot.Bot(tmp_path)
    assert not (tmp_path / 'ready').exists()
    assert json.loads(history.read_text(encoding='utf-8')) == state


def test_json_with_windows_utf8_bom_is_supported(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{"items":{}}', encoding='utf-8-sig')
    assert bot.load_json(path, {}) == {'items': {}}


def test_failed_disk_flush_preserves_previous_history(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    previous = b'{"items":{"1":{"status":"sent"}}}'
    path.write_bytes(previous)

    def failed_flush(_fd):
        raise OSError('Simulated disk flush failure')

    monkeypatch.setattr(bot.os, 'fsync', failed_flush)
    with pytest.raises(OSError, match='disk flush failure'):
        bot.save_json(path, {'items': {}})
    assert path.read_bytes() == previous
