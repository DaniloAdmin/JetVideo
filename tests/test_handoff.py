import json
import shutil
from types import SimpleNamespace

from handoff import build_plan, transfer


def profile(tmp_path, name):
    base = tmp_path / name
    base.mkdir()
    (base / 'ready').mkdir()
    (base / 'config.json').write_text(json.dumps({'videos_folder': 'ready'}), encoding='utf-8')
    (base / 'state.json').write_text(json.dumps({'items': {}}), encoding='utf-8')
    return SimpleNamespace(root=base, name=name)


def test_same_render_to_two_accounts(tmp_path):
    clip = tmp_path / '181_with_ad.mp4'
    clip.write_bytes(b'synthetic-video')
    first, second = profile(tmp_path, 'one'), profile(tmp_path, 'two')
    plan = build_plan([clip], [first, second])
    assert len(plan.jobs) == 2
    assert not plan.existing and not plan.skipped
    count, errors = transfer(plan)
    assert count == 2 and not errors
    assert (first.root / 'ready' / clip.name).read_bytes() == clip.read_bytes()
    assert (second.root / 'ready' / clip.name).read_bytes() == clip.read_bytes()
    assert json.loads((first.root/'state.json').read_text()) == {'items': {}}
    assert json.loads((second.root/'state.json').read_text()) == {'items': {}}


def test_never_overwrite_existing_ready_by_default(tmp_path):
    clip = tmp_path / '181_with_ad.mp4'; clip.write_bytes(b'new')
    one = profile(tmp_path, 'one')
    (one.root / 'ready' / clip.name).write_bytes(b'old')
    plan = build_plan([clip], [one])
    assert not plan.jobs and len(plan.existing) == 1
    count, errors = transfer(plan)
    assert count == 0 and not errors
    assert (one.root / 'ready' / clip.name).read_bytes() == b'old'
    count, errors = transfer(plan, overwrite=True)
    assert count == 1 and not errors
    assert (one.root / 'ready' / clip.name).read_bytes() == b'new'


def test_block_sent_and_scheduled_without_resetting_history(tmp_path):
    clip = tmp_path / '181_with_ad.mp4'; clip.write_bytes(b'new')
    other = tmp_path / '182_with_ad.mp4'; other.write_bytes(b'other')
    p = profile(tmp_path, 'one')
    state = {'items': {'181': {'status': 'scheduled'}, '182': {'status': 'sent'}}}
    (p.root / 'state.json').write_text(json.dumps(state))
    plan = build_plan([clip, other], [p])
    assert not plan.jobs and len(plan.skipped) == 2
    assert transfer(plan)[0] == 0
    assert json.loads((p.root / 'state.json').read_text()) == state


def test_invalid_names_missing_files_not_transferred(tmp_path):
    p = profile(tmp_path, 'first')
    for name in ['181_with_ad_2.mp4', 'notes.txt', 'no.mp4']:
        (tmp_path / name).write_bytes(b'a')
    plan = build_plan([tmp_path / '181_with_ad_2.mp4', tmp_path / 'notes.txt',
                       tmp_path / 'no.mp4', tmp_path / '999_with_ad.mp4'], [p])
    assert len(plan.jobs) == 2 and len(plan.skipped) == 2


def test_absolute_ready_path(tmp_path):
    p = profile(tmp_path, 'first')
    outside = tmp_path / 'outside'; outside.mkdir()
    (p.root / 'config.json').write_text(json.dumps({'videos_folder': str(outside)}))
    clip = tmp_path / '12.mp4'; clip.write_bytes(b'ok')
    plan = build_plan([clip], [p]); assert len(plan.jobs) == 1
    assert transfer(plan)[0] == 1
    assert (outside / clip.name).read_bytes() == b'ok'


def test_missing_after_plan_does_not_corrupt_files(tmp_path):
    p = profile(tmp_path, 'first')
    clip = tmp_path/'11.mp4'; clip.write_bytes(b'0')
    plan = build_plan([clip], [p]); clip.unlink()
    count, errors = transfer(plan)
    assert count == 0 and len(errors) == 1
    assert not (p.root / 'ready' / clip.name).exists()


def test_rerender_filename_is_normalized_for_bot(tmp_path):
    p = profile(tmp_path, 'first')
    src = tmp_path/'181_with_ad_2.mp4'; src.write_bytes(b'second-render')
    plan = build_plan([src], [p])
    assert len(plan.jobs) == 1 and len(plan.renamed) == 1
    assert plan.jobs[0].destination.name == '181_with_ad.mp4'
    assert transfer(plan)[0] == 1
    assert (p.root/'ready'/'181_with_ad.mp4').read_bytes() == b'second-render'
    assert src.exists()


def test_overwrite_confirmation_applies_only_to_known_matches(tmp_path, monkeypatch):
    account = profile(tmp_path, 'account')
    ready = account.root / 'ready'
    existing, incoming = tmp_path / '181.mp4', tmp_path / '182.mp4'
    existing.write_bytes(b'replacement')
    incoming.write_bytes(b'incoming')
    (ready / existing.name).write_bytes(b'old')
    plan = build_plan([existing, incoming], [account])
    copy = shutil.copy2

    def copy_with_new_collision(src, dst):
        result = copy(src, dst)
        if src == incoming:
            (ready / incoming.name).write_bytes(b'other process')
        return result

    monkeypatch.setattr(shutil, 'copy2', copy_with_new_collision)
    count, errors = transfer(plan, overwrite=True)
    assert count == 1 and len(errors) == 1
    assert (ready / existing.name).read_bytes() == b'replacement'
    assert (ready / incoming.name).read_bytes() == b'other process'
    assert not list(ready.glob('*.tmp'))
