import json
from types import SimpleNamespace
import pytest
from gpsync.storage import owned, details, decision, enrich, crosscheck, pages, validation, run, connect
from gpsync.config import load_settings


def raw(owned_flag=True, space=1, amount=12, quality=1, albums=None):
    row=[None]*31
    row[0]='m';row[2]='photo.jpg';row[5]=999;row[11]='d';row[19]=albums;row[30]=[space,amount,quality]
    return [row]


def item(**overrides):
    value=dict(media_key='m',is_owned=True,dedup_key='d',takes_space=True,charged_bytes=12,
        albums=[],membership_complete=True,quality_code=1,created_ts=1700000000,taken_ts=100)
    return dict(value,**overrides)


@pytest.mark.parametrize('flags,expected',[(None,None),('invalid',None),([None],None),([],True),([[27]],False)])
def test_ownership_is_strict(flags,expected):
    row=[None]*8;row[7]=flags
    assert owned(row) is expected


@pytest.mark.parametrize('changes,reason',[
    ({},'eligible'),({'is_owned':None},'unknown_owner'),({'is_owned':False},'other_owner'),
    ({'takes_space':False,'charged_bytes':0},'free'),({'takes_space':None},'unknown_storage'),
    ({'charged_bytes':0},'conflicting_storage'),({'takes_space':False},'conflicting_storage'),
    ({'albums':['shared']},'album_protected'),({'membership_complete':False},'unknown_membership'),
    ({'storage_conflict':True},'conflicting_storage'),({'quality_code':876},'eligible'),
    ({'archived':True,'is_video':True},'eligible')])
def test_selection(changes,reason):
    assert decision(item(**changes))==reason


def test_dates_never_authorize():
    assert decision(item(taken_ts=1,created_ts=1))=='eligible'
    assert decision(item(takes_space=False,charged_bytes=0,quality_code=2))=='free'


def test_false_zero_and_unknown_survive_sqlite(tmp_path):
    settings=load_settings(data_dir=tmp_path);db=connect(settings)
    value=details(raw(space=0,amount=0,quality=999))
    db.execute('insert into items values(?,?,?)',('s','m',json.dumps(value)));db.commit()
    out=json.loads(db.execute('select payload from items').fetchone()[0])
    assert out['takes_space'] is False and out['charged_bytes']==0 and out['quality_code']==999


def test_membership_union_and_conflict():
    a=enrich(item(),raw(albums=[['shared']]),{})
    assert a['decision']=='album_protected'
    a=enrich(item(),raw(albums=[]),{'m':{'album'}})
    assert a['decision']=='conflicting_membership'
    assert enrich(item(),raw(),{})['decision']=='eligible'


def test_secondary_storage_conflict():
    value=crosscheck(item(),[['m',{'318563170':[[0,0,2]]}]])
    assert value['decision']=='conflicting_storage'


def test_pagination_rejects_repeated_or_missing_data():
    c=SimpleNamespace(rpc=lambda *a,**k:[[], 'same'])
    with pytest.raises(ValueError):list(pages(c,'rpc',lambda cursor:[]))
    c.rpc=lambda *a,**k:None
    with pytest.raises(ValueError):list(pages(c,'rpc',lambda cursor:[]))


def test_ui_gate_cannot_be_empty():
    with pytest.raises(ValueError):validation([],[])


def test_trash_requires_validation_before_network(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path);db=connect(settings)
    db.execute('insert into scans values(?,?,?,NULL)',('s','a@example.test','complete'));db.commit()
    args=SimpleNamespace(storage_command='trash',scan_id='s',expect_account='a@example.test',reviewed_report='x',limit=1)
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:pytest.fail('Network must remain gated'))
    with pytest.raises(ValueError,match='validation'):run(args,settings)


def test_mutation_timeout_is_not_retried_and_resume_reconciles(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path);db=connect(settings)
    candidate=item(decision='eligible')
    digest=__import__('hashlib').sha256(json.dumps([candidate],sort_keys=True).encode()).hexdigest()
    db.execute('insert into scans values(?,?,?,?)',('s','a@example.test','complete',json.dumps({'hash':digest})))
    db.execute('insert into items values(?,?,?)',('s','m',json.dumps(candidate)));db.commit()
    calls=[]
    def post(*a,**kw): calls.append(1);raise TimeoutError('uncertain')
    def rpc(method,*a,**kw):
        if method=='fDcn4b':return raw()
        if method=='VrseUb':return [['m']]
        if method=='zy0IHe':return [[['m',None,None,'d']],None]
        return []
    c=SimpleNamespace(email='a@example.test',rpc=rpc,session=SimpleNamespace(post=post),
        global_data={'SNlM0e':'test','Im6cmf':'/','FdrFJe':'test','cfb2h':'test'},base_path='/')
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:c)
    monkeypatch.setattr('gpsync.storage.library',lambda *a:{'m':item()})
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:{})
    args=SimpleNamespace(storage_command='trash',scan_id='s',expect_account=c.email,reviewed_report=digest,limit=1)
    with pytest.raises(TimeoutError):run(args,settings)
    assert len(calls)==1
    assert db.execute('select state from journal order by rowid desc').fetchone()[0]=='uncertain'
    assert run(args,settings)==0
    assert len(calls)==1
    assert db.execute('select state from journal order by rowid desc').fetchone()[0]=='confirmed'


def test_account_pin_rejects_other_account(monkeypatch,tmp_path):
    from gpsync.storage import checked_client
    (tmp_path/'network.json').write_text(json.dumps({'proxy':'http://127.0.0.1:1234'}))
    monkeypatch.setattr('gpsync.storage.PhotosClient',lambda *a,**kw:SimpleNamespace(email='other@example.test'))
    with pytest.raises(ValueError,match='mismatch'):checked_client(load_settings(data_dir=tmp_path),'expected@example.test')


def test_shared_album_index_uses_documented_key_and_auth(monkeypatch):
    from gpsync.storage import membership
    calls=[]
    def rpc(method,args,**kwargs):
        calls.append((method,args))
        if method=='Z5xsfc':return [[]]
        if method=='F2A0H':
            row=[None]*18;row[6]='shared-album';row[7]='auth-token'
            return [[row]]
        if method=='snAcKc':return [None,[['photo',None,None,'photo-dedup']],None]
        raise AssertionError(method)
    result=membership(SimpleNamespace(rpc=rpc))
    assert result=={'photo':{'shared-album'}}
    assert ('snAcKc',['shared-album',None,None,'auth-token']) in calls


@pytest.mark.parametrize('identity,count,accepted',[('album',0,True),('wrong',0,False),('album',1,False),('album',None,False)])
def test_album_empty_response_requires_explicit_identity_and_zero_count(identity,count,accepted):
    from gpsync.storage import membership
    def rpc(method,args,**kwargs):
        if method=='Z5xsfc':return [[['album']]]
        if method=='F2A0H':return [[]]
        if method=='snAcKc':
            info=[None]*22;info[0]=identity;info[21]=count
            return [None,None,None,info]
        raise AssertionError(method)
    c=SimpleNamespace(rpc=rpc)
    if accepted:assert membership(c)=={}
    else:
        with pytest.raises(ValueError,match='Incomplete'):membership(c)
