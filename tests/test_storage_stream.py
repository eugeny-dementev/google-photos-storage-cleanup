import json
from types import SimpleNamespace
import pytest
from gpsync.storage import AlbumIndex,run,connect
from gpsync.storage_stream import qualifies
from gpsync.config import load_settings


def test_stream_requires_two_positive_storage_checks_and_preserves_aliases():
    index=AlbumIndex()
    value={'decision':'eligible','secondary_storage_flag':True,'charged_bytes':1,'dedup_key':'d'}
    assert qualifies(value,index)
    for field,replacement in [('secondary_storage_flag',None),('charged_bytes',0),('decision','album_protected')]:
        assert not qualifies(dict(value,**{field:replacement}),index)
    index.protected_dedup.add('d')
    assert not qualifies(value,index)


@pytest.mark.parametrize('timeout',[False,True])
def test_stream_mutates_before_exhausting_library_and_never_retries(tmp_path,monkeypatch,timeout):
    settings=load_settings(data_dir=tmp_path);calls=[]
    lib=['m',None,1,'d',0,1,None,[]]
    detail=[None]*31;detail[0]='m';detail[11]='d';detail[19]=[];detail[30]=[1,10,1]
    def rpc(method,args,**kw):
        if method=='lcxiM':return [[lib],'more-pages',1]
        if method=='fDcn4b':return [detail]
        if method=='VrseUb':return [['m',{'318563170':[[1,10,1]]}]]
        if method=='zy0IHe':return [[['m',None,None,'d']],None]
        return []
    def post(*a,**kw):
        calls.append(kw)
        if timeout:raise TimeoutError('uncertain')
        return SimpleNamespace(status_code=200,text=json.dumps([['wrb.fr','XwAOJf',json.dumps([1])]]))
    client=SimpleNamespace(email='self@example.test',rpc=rpc,session=SimpleNamespace(post=post),
        global_data={'SNlM0e':'x','Im6cmf':'/','FdrFJe':'x','cfb2h':'x'},base_path='/')
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:client)
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    monkeypatch.setattr('gpsync.storage._REQUEST_GATE.wait',lambda:None)
    args=SimpleNamespace(storage_command='stream',expect_account=client.email,write=True,limit=1,resume=None)
    if timeout:
        with pytest.raises(TimeoutError):run(args,settings)
    else:assert run(args,settings)==0
    assert len(calls)==1
    assert calls[0]['allow_redirects'] is False
    db=connect(settings)
    assert db.execute('select state from journal order by rowid desc').fetchone()[0]==('uncertain' if timeout else 'confirmed')


def test_write_mode_preserves_foreign_item_without_detail_requests(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path)
    lib=['foreign',None,1,'dedup',0,1,None,[[27]]]
    methods=[]
    def rpc(method,args,**kw):
        methods.append(method)
        if method=='lcxiM':return [[lib],None,None]
        if method=='EzwWhf':return []
        pytest.fail('Foreign item must not trigger detail or Trash request: '+method)
    client=SimpleNamespace(email='self@example.test',rpc=rpc)
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:client)
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    args=SimpleNamespace(storage_command='stream',expect_account=client.email,write=True,limit=10,resume=None)
    assert run(args,settings)==0
    db=connect(settings);item=json.loads(db.execute('select payload from items').fetchone()[0])
    assert item['decision']=='other_owner'
    assert item['takes_space'] is None and item['charged_bytes'] is None
    assert db.execute('select count(*) from journal').fetchone()[0]==0


def test_sparse_page_keeps_cursor_and_last_timestamp():
    from gpsync.storage_stream import read_library_page
    assert read_library_page([None,'next'],123)==([], 'next',123)
    assert read_library_page([None,None],123)==([],None,123)
    for malformed in [None,{},[None],['invalid','next'],[None,42]]:
        with pytest.raises(ValueError):read_library_page(malformed,123)


def test_empty_pages_are_followed_without_deleting_or_skipping_later_items(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path);requests=[]
    foreign=['foreign',None,1,'dedup',0,1,None,[[27]]]
    responses=iter([[[], 'empty-page',123],[None,'later-page'],[[foreign],None,124]])
    def rpc(method,args,**kw):
        if method=='EzwWhf':return []
        assert method=='lcxiM'
        requests.append(args)
        return next(responses)
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(email='self@example.test',rpc=rpc))
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    args=SimpleNamespace(storage_command='stream',expect_account='self@example.test',write=True,limit=0,resume=None)
    assert run(args,settings)==0
    assert requests[2][:2]==['later-page',123]
    db=connect(settings)
    assert db.execute('select key from items').fetchall()==[('foreign',)]
    assert db.execute('select count(*) from journal').fetchone()[0]==0
