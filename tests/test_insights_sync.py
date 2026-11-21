from pathlib import Path
import copy
from datetime import datetime, timezone
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import insights
from profiles import ProfileStore
import publisher_ui


@pytest.fixture
def fake(tmp_path, monkeypatch):
    ws = tmp_path/'workspace'; ws.mkdir()
    publisher_ui.initialize_workspace(ws)
    publisher_ui.save_env(ws/'.env', {key:'MOCK_'+key for key, _ in publisher_ui.ENV_KEYS})
    store = ProfileStore(ws)
    p1 = store.selected()
    p2 = store.create('Second', channel_name='second', channel_id='tt2', org_name='Test', org_id='org')
    store.save_channel(p1.id, channel_name='first', channel_id='tt1', org_name='Test', org_id='org')
    buffer_posts = {'tt1':[], 'tt2':[]}
    class FakeBuffer:
        def __init__(self, key): assert key=='MOCK_BUFFER_API_KEY'
        def organizations(self): return [{'id':'org','name':'Test'}]
        def channels(self, org): return [
            {'id':'tt1','name':'first','displayName':'first','service':'tiktok','isQueuePaused':False},
            {'id':'tt2','name':'second','displayName':'second','service':'tiktok','isQueuePaused':True}]
        def posts_paginated(self, org, channel_id, statuses, **kwargs):
            return [copy.deepcopy(p) for p in buffer_posts[channel_id] if p['status'] in statuses]
        def posts(self,org,cid,statuses): return self.posts_paginated(org,cid,statuses)
    class FakeR2:
        def __init__(self,*a,**k): pass
        def delete(self,key): pass
    monkeypatch.setattr(bot,'BufferClient',FakeBuffer)
    monkeypatch.setattr(bot,'R2Client',FakeR2)
    return ws,store,p1,p2,buffer_posts


def test_insights_all_accounts_and_unknown_metrics(fake):
    ws, store, p1, p2, buffer_posts = fake
    date = datetime.now(timezone.utc).isoformat()
    buffer_posts['tt1'].append({'id':'x','status':'sent','text':'Часть 200', 'sentAt':date,
         'metrics':[{'type':'views','value':1500},{'type':'reactions','value':13},{'type':'comments','value':2}]})
    buffer_posts['tt2'].append({'id':'y','status':'sent','text':'Часть 100', 'sentAt':date,
         'metrics':[{'type':'views','value':900},{'type':'shares','value':5},{'type':'reposts','value':1}]})
    report=insights.fetch_insights(ws,[p1.id,p2.id])
    assert not report['errors']
    rows=insights.filter_rows(report,None,30)
    totals=insights.summarize(rows)
    assert totals['views']['total']==2400 and totals['views']['covered']==2
    assert totals['comments']['total']==2 and totals['comments']['covered']==1
    assert totals['reposts']['total']==1
    assert totals['reach']['total'] is None
    assert len(insights.filter_rows(report,p2.id,30))==1


def test_sync_is_read_only_and_isolated_by_profile(fake):
    ws,store,p1,p2,data=fake
    bot.save_json(p1.root/'state.json',{'items':{'181': {'status':'scheduled','buffer_post_id':'old1','filename':'181.mp4'}}})
    bot.save_json(p2.root/'state.json',{'items':{'181': {'status':'scheduled','buffer_post_id':'old2','filename':'181.mp4'}}})
    state1=(p1.root/'state.json').read_bytes()
    state2=(p2.root/'state.json').read_bytes()
    data['tt2'].append({'id':'old2','status':'scheduled','text':'Часть 181','channelId':'tt2'})
    r=insights.scan_buffer(ws,[p1.id,p2.id])
    assert r['profiles'][p1.id]['rows'][0]['category']=='missing'
    assert r['profiles'][p2.id]['rows']==[]
    assert state1==(p1.root/'state.json').read_bytes()
    assert state2==(p2.root/'state.json').read_bytes()


def test_sync_recognizes_published_error_and_draft(fake):
    ws,store,p1,p2,data=fake
    rows={'182':{'status':'scheduled','buffer_post_id':'p1'},
          '183':{'status':'scheduled','buffer_post_id':'p2'},
          '184':{'status':'scheduled','buffer_post_id':'p3'}}
    bot.save_json(p1.root/'state.json',{'items':rows})
    data['tt1'] += [{'id':'p1','status':'sent','text':'Часть 182'},
                     {'id':'p2','status':'error','text':'Часть 183'},
                     {'id':'p3','status':'draft','text':'Часть 184'}]
    report=insights.scan_buffer(ws,[p1.id])
    issues={r['part']:r['category'] for r in report['profiles'][p1.id]['rows']}
    assert issues=={'182':'published','183':'failed','184':'moved'}


def test_buffer_pagination_and_metrics_flag(monkeypatch):
    cursors=[]
    def fake_gql(self, query, variables):
        assert 'metrics { type name value unit } metricsUpdatedAt' in query
        cursors.append(variables['after'])
        if variables['after'] is None:
            return {'posts': {'edges':[{'node':{'id':'1','status':'sent'}}], 'pageInfo':{'hasNextPage':True,'endCursor':'cursor1'}}}
        return {'posts': {'edges':[{'node':{'id':'2','status':'sent'}}], 'pageInfo':{'hasNextPage':False,'endCursor':'cursor2'}}}
    monkeypatch.setattr(bot.BufferClient,'gql',fake_gql)
    rows=bot.BufferClient('FAKE').posts_paginated('org','channel',['sent'],include_metrics=True)
    assert [r['id'] for r in rows]==['1','2']
    assert cursors==[None,'cursor1']


def test_buffer_pagination_fails_closed_when_cursor_repeats(monkeypatch):
    def fake_gql(self,q,v):
        return {'posts': {'edges':[{'node':{'id':'1','status':'sent'}}], 'pageInfo':{'hasNextPage':True, 'endCursor':'loop'}}}
    monkeypatch.setattr(bot.BufferClient,'gql',fake_gql)
    with pytest.raises(bot.BotError,match='пагинацию'):
        bot.BufferClient('FAKE').posts_paginated('org','channel',['scheduled'])


def test_sync_failed_scan_doesnt_label_missing(fake, monkeypatch):
    ws,store,p1,p2,data = fake
    bot.save_json(p1.root/'state.json',{'items':{'181':{'status':'scheduled','buffer_post_id':'old'}}})
    def broken(self, org, cid, statuses, **kw):
        raise bot.BotError('pagination failed')
    monkeypatch.setattr(bot.BufferClient,'posts_paginated',broken)
    r=insights.scan_buffer(ws,[p1.id])
    assert p1.id in r['errors']
    assert not r['profiles']
    assert bot.load_json(p1.root/'state.json',{})['items']['181']['status']=='scheduled'


def test_apply_confirmed_remote_state_backs_up_and_does_not_publish(fake):
    ws,store,p1,p2,data=fake
    path = p1.root/'scheduled'/'181.mp4'; path.write_bytes(b'movie')
    bot.save_json(p1.root/'state.json',{'items':{'181': {
        'status':'scheduled','buffer_post_id':'p1','filename':'181.mp4','r2_cleaned':True}}})
    data['tt1'].append({'id':'p1','status':'sent','text':'Часть 181'})
    result=insights.apply_verified_states(ws,[p1.id])
    assert result[p1.id]['changed']==1
    assert Path(result[p1.id]['backup']).is_file()
    assert (p1.root/'posted'/'181.mp4').read_bytes()==b'movie'
    assert not path.exists()
    assert bot.load_json(p1.root/'state.json',{})['items']['181']['status']=='sent'
    assert bot.load_json(p2.root/'state.json',{}) == {'items': {}}


def test_restore_draft_cannot_be_mistaken_for_deleted(fake):
    ws,store,p1,p2,data=fake
    scheduled = p1.root/'scheduled'/'201.mp4'; scheduled.write_bytes(b'movie')
    bot.save_json(p1.root/'state.json', {'items':{'201':{'status':'scheduled','buffer_post_id':'p1','filename':'201.mp4'}}})
    data['tt1'].append({'id':'p1','status':'draft','text':'Часть 201'})
    instance=bot.Bot(p1.root,credentials_path=ws/'.env',profile_id=p1.id)
    result=instance.restore_deleted_from_buffer([201])
    assert result['restored']==[] and result['skipped']==[201]
    assert scheduled.is_file()
