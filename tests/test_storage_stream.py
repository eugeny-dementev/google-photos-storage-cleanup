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
    assert run(args,settings)==0
    assert len(calls)==1
    assert calls[0]['allow_redirects'] is False
    assert not list((settings.data_dir/'reports').glob('*/checkpoint.json')), 'Partial page must not advance checkpoint'
    db=connect(settings)
    assert db.execute('select state from journal order by rowid desc').fetchone()[0]=='confirmed'


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
    for malformed in [None,{},['invalid','next'],[None,42]]:
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


def test_item_metadata_failure_is_preserved_after_successful_canary(monkeypatch):
    from gpsync.storage_stream import audit_with_recovery
    from gpsync.client import PhotosApiError
    monkeypatch.setattr('gpsync.storage_stream.audit_item',lambda *a:(_ for _ in ()).throw(PhotosApiError('item unreadable')))
    fresh=SimpleNamespace(rpc=lambda *a,**kw:[[],None])
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:fresh)
    item,client=audit_with_recovery(object(),{'media_key':'m','dedup_key':'d'},AlbumIndex(),object(),'self@example.test')
    assert client is fresh
    assert item['decision']=='metadata_unavailable' and item['charged_bytes'] is None
    assert not qualifies(item,AlbumIndex())


def test_metadata_error_is_not_skipped_when_global_canary_fails(monkeypatch):
    from gpsync.storage_stream import audit_with_recovery
    from gpsync.client import PhotosApiError
    def fail(*a,**kw):raise PhotosApiError('global failure')
    monkeypatch.setattr('gpsync.storage_stream.audit_item',fail)
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(rpc=fail))
    with pytest.raises(PhotosApiError):audit_with_recovery(object(),{'media_key':'m'},AlbumIndex(),object(),'self@example.test')


def test_refreshed_metadata_client_is_reused_after_read_recovery(monkeypatch):
    from gpsync.storage_stream import audit_with_recovery
    from gpsync.client import PhotosApiError
    original=object();fresh=SimpleNamespace(rpc=lambda *a,**kw:[[],None]);calls=[]
    def audit(c,*a):
        calls.append(c)
        if c is original:raise PhotosApiError('stale token')
        return {'decision':'eligible'}
    monkeypatch.setattr('gpsync.storage_stream.audit_item',audit)
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:fresh)
    item,c=audit_with_recovery(original,{'media_key':'m'},AlbumIndex(),object(),'self@example.test')
    assert item['decision']=='eligible' and c is fresh and calls==[original,fresh]


def test_unconfirmed_timeout_never_resends_mutation(monkeypatch):
    from gpsync.storage_stream import move_with_reconciliation
    calls=[];events=[]
    def send(*a):calls.append(1);raise TimeoutError('uncertain')
    monkeypatch.setattr('gpsync.storage_stream.move_one',send)
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:object())
    monkeypatch.setattr('gpsync.storage_stream.in_trash',lambda *a:False)
    monkeypatch.setattr('gpsync.storage_stream.time.sleep',lambda *a:None)
    with pytest.raises(RuntimeError,match='no mutation was retried'):
        move_with_reconciliation(object(),{'media_key':'m','dedup_key':'d'},object(),'self@example.test',lambda *a:events.append(a))
    assert calls==[1] and events==[('m','uncertain','TimeoutError')]


def test_album_items_skip_detail_requests_but_remain_protected(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path);row=['m',None,1,'d',0,1,None,[]]
    def rpc(method,*a,**kw):
        if method=='lcxiM':return [[row],None]
        if method=='EzwWhf':return []
        pytest.fail('Album item must not trigger metadata or writes')
    index=AlbumIndex();index['m']={'album'};index.protected_dedup.add('d')
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(email='self@example.test',rpc=rpc))
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:index)
    assert run(SimpleNamespace(storage_command='stream',expect_account='self@example.test',write=True,limit=0,resume=None),settings)==0
    db=connect(settings)
    assert json.loads(db.execute('select payload from items').fetchone()[0])['decision']=='album_protected'
    assert db.execute('select count(*) from journal').fetchone()[0]==0


def test_final_page_without_cursor_keeps_its_items():
    from gpsync.storage_stream import read_library_page
    row=['last-photo']
    assert read_library_page([[row]],123)==([row],None,123)
    assert read_library_page([[]],123)==([],None,123)
    assert read_library_page([],123)==([],None,123)


def test_stream_rechecks_and_processes_short_final_page(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path);row=['last',None,1,'d',0,1,None,[[27]]];reads=[]
    def rpc(method,args,**kw):
        if method=='EzwWhf':return []
        assert method=='lcxiM';reads.append(args);return [[row]]
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(email='self@example.test',rpc=rpc))
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    assert run(SimpleNamespace(storage_command='stream',expect_account='self@example.test',write=True,limit=0,resume=None),settings)==0
    assert len(reads)==2
    db=connect(settings);assert db.execute('select key from items').fetchall()==[('last',)]
    scan=db.execute('select id from scans').fetchone()[0]
    checkpoint=json.loads((settings.data_dir/'reports'/scan/'checkpoint.json').read_text())
    assert checkpoint['complete'] is True


def test_resume_uses_checkpoint_and_does_not_skip_next_page(tmp_path,monkeypatch):
    from gpsync.storage_stream import save_checkpoint
    settings=load_settings(data_dir=tmp_path);db=connect(settings)
    db.execute('insert into scans values(?,?,?,NULL)',('job','self@example.test','streaming'));db.commit()
    directory=settings.data_dir/'reports/job';directory.mkdir(parents=True)
    save_checkpoint(directory/'checkpoint.json','job','self@example.test','saved-next',123)
    reads=[]
    def rpc(method,args,**kw):
        if method=='EzwWhf':return []
        reads.append(args);return [[],None]
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(email='self@example.test',rpc=rpc))
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    assert run(SimpleNamespace(storage_command='stream',expect_account='self@example.test',write=True,limit=0,resume='job'),settings)==0
    assert reads==[['saved-next',123,100,None,1,3]]
    assert db.execute('select state from scans').fetchone()[0]=='stream_complete'


def test_checkpoint_account_mismatch_blocks_library_reads(tmp_path,monkeypatch):
    from gpsync.storage_stream import save_checkpoint
    settings=load_settings(data_dir=tmp_path);db=connect(settings)
    db.execute('insert into scans values(?,?,?,NULL)',('job','self@example.test','streaming'));db.commit()
    directory=settings.data_dir/'reports/job';directory.mkdir(parents=True)
    save_checkpoint(directory/'checkpoint.json','job','other@example.test','next',123)
    def rpc(method,*a,**kw):
        assert method=='EzwWhf','Library access must remain blocked';return []
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(email='self@example.test',rpc=rpc))
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    with pytest.raises(ValueError,match='identity mismatch'):
        run(SimpleNamespace(storage_command='stream',expect_account='self@example.test',write=True,limit=0,resume='job'),settings)


def test_invalid_saved_cursor_restarts_without_skipping(tmp_path,monkeypatch):
    from gpsync.storage_stream import save_checkpoint
    from gpsync.client import PhotosApiError
    settings=load_settings(data_dir=tmp_path);db=connect(settings)
    db.execute('insert into scans values(?,?,?,NULL)',('job','self@example.test','streaming'));db.commit()
    directory=settings.data_dir/'reports/job';directory.mkdir(parents=True)
    save_checkpoint(directory/'checkpoint.json','job','self@example.test','expired',123)
    reads=[]
    def rpc(method,args,**kw):
        if method=='EzwWhf':return []
        reads.append(args)
        if args[0]=='expired':raise PhotosApiError('Old cursor rejected')
        return [[],None]
    monkeypatch.setattr('gpsync.storage.checked_client',lambda *a:SimpleNamespace(email='self@example.test',rpc=rpc))
    monkeypatch.setattr('gpsync.storage.membership',lambda *a:AlbumIndex())
    assert run(SimpleNamespace(storage_command='stream',expect_account='self@example.test',write=True,limit=0,resume='job'),settings)==0
    assert [r[0] for r in reads]==['expired',None]
