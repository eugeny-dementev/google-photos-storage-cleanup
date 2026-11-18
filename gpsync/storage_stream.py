"""Incremental library audit and explicitly authorized, recoverable Trash."""
import json
import time
import uuid
import os
from gpsync.client import _extract_wrb_payload, PhotosApiError
from gpsync.parser import MediaItem
from gpsync.util import safe_get


def add_stream(commands):
    p=commands.add_parser('stream',help='Audit incrementally; --write enables verified Trash')
    p.add_argument('--expect-account',required=True)
    p.add_argument('--write',action='store_true')
    p.add_argument('--limit',type=int,default=10,help='Maximum confirmed moves; 0 means all eligible items')
    p.add_argument('--resume')


def qualifies(item,index):
    return (item.get('decision')=='eligible' and item.get('secondary_storage_flag') is True
        and type(item.get('charged_bytes')) is int and item['charged_bytes']>0
        and item.get('dedup_key') not in index.protected_dedup)


def in_trash(c,key,dedup):
    from gpsync.storage import pages
    for page in pages(c,'zy0IHe',lambda cursor:[cursor]):
        if any(safe_get(row,0)==key or safe_get(row,3)==dedup for row in page[0]):return True
    return False


def move_one(c,item):
    from gpsync.storage import _REQUEST_GATE
    _REQUEST_GATE.wait()
    data={'f.req':json.dumps([[['XwAOJf',json.dumps([None,1,[item['dedup_key']],3]),None,'generic']]]),'at':c.global_data['SNlM0e']}
    response=c.session.post(f"https://photos.google.com{c.global_data['Im6cmf']}/data/batchexecute",
        params={'rpcids':'XwAOJf','source-path':c.base_path,'f.sid':c.global_data['FdrFJe'],'bl':c.global_data['cfb2h'],'rt':'c'},data=data,timeout=60,allow_redirects=False)
    if response.status_code!=200:raise ValueError('Trash request rejected; no automatic retry')
    result=_extract_wrb_payload(response.text)
    if not isinstance(result,list) or not result:raise ValueError('Unexpected mutation response')
    if not in_trash(c,item['media_key'],item['dedup_key']):raise ValueError('Trash action not confirmed')


def read_library_page(raw, previous_timestamp):
    if not isinstance(raw,list):
        raise ValueError('Malformed library page')
    rows=safe_get(raw,0);cursor=safe_get(raw,1)
    if cursor is not None and not isinstance(cursor,str):raise ValueError('Malformed library cursor')
    if rows is None:rows=[]
    if not isinstance(rows,list):raise ValueError('Malformed library page')
    timestamp=safe_get(raw,2)
    return rows,cursor,previous_timestamp if timestamp is None else timestamp


def save_checkpoint(path,scan,account,cursor,timestamp):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as out:
        json.dump({'version':1,'scan':scan,'account':account,'cursor':cursor,'timestamp':timestamp,'complete':not bool(cursor)},out)
        out.flush();os.fsync(out.fileno())
    os.replace(temporary,path)


def audit_item(c,base,index):
    from gpsync.storage import enrich,crosscheck
    key=base['media_key']
    item=enrich(base,c.rpc('fDcn4b',[key,1,None,None,1],parse=False),index)
    return crosscheck(item,c.rpc('VrseUb',[key,None,None,None,None],parse=False))


def audit_with_recovery(c,base,index,settings,email):
    from gpsync.storage import checked_client
    try:return audit_item(c,base,index),c
    except PhotosApiError:
        fresh=checked_client(settings,email)
        # A working account-bound library read distinguishes one unreadable item from lost global access.
        read_library_page(fresh.rpc('lcxiM',[None,None,1,None,1,3],parse=False),None)
        try:return audit_item(fresh,base,index),fresh
        except PhotosApiError:
            return {**base,'decision':'metadata_unavailable','takes_space':None,'charged_bytes':None,
                'quality_code':None,'albums':sorted(index.get(base['media_key'],set())),
                'checked_at':time.time(),'provenance':'Metadata unavailable after fresh account and library verification; preserved'},fresh


def move_with_reconciliation(c,item,settings,email,journal):
    from gpsync.storage import checked_client
    try:move_one(c,item)
    except Exception as error:
        journal(item['media_key'],'uncertain',type(error).__name__)
        fresh=checked_client(settings,email)
        for delay in (0,2,5):
            if delay:time.sleep(delay)
            try:
                if in_trash(fresh,item['media_key'],item['dedup_key']):
                    print('Timed-out/unconfirmed Trash request reconciled through read-only Trash lookup.',flush=True)
                    return fresh
            except PhotosApiError:
                if delay==5:raise
        raise RuntimeError('Trash action remains uncertain; no mutation was retried') from error
    return c


def stream(args,settings,db):
    from gpsync.storage import checked_client,membership,enrich,crosscheck,owned,quota,report
    if args.limit<0:raise ValueError('Limit cannot be negative')
    c=checked_client(settings,args.expect_account)
    scan=args.resume or uuid.uuid4().hex
    previous=db.execute('select account,state from scans where id=?',(scan,)).fetchone()
    if previous and (previous[0]!=c.email or previous[1] not in ('streaming','paused','stream_complete')):
        raise ValueError('Not a matching streaming job')
    if args.resume and not previous:raise ValueError('Unknown streaming job')
    db.execute('insert or ignore into scans values(?,?,?,NULL)',(scan,c.email,'streaming'))
    db.execute('update scans set state=? where id=?',('streaming',scan));db.commit()
    directory=settings.data_dir/'reports'/scan;directory.mkdir(parents=True,exist_ok=True)
    print('Streaming job:',scan,flush=True)
    (directory/'quota-before.json').write_text(json.dumps(quota(c)))
    index=membership(c)
    print('Album protection complete. Processing library pages incrementally.',flush=True)
    seen=set();cursors=set();cursor=timestamp=None;checked=moved=0;metadata_failures=0
    checkpoint=directory/'checkpoint.json'
    starting_from_checkpoint=False
    if args.resume and checkpoint.exists():
        saved=json.loads(checkpoint.read_text())
        if saved.get('version')!=1 or saved.get('scan')!=scan or saved.get('account')!=c.email:
            raise ValueError('Checkpoint identity mismatch')
        if saved.get('complete'):
            db.execute('update scans set state=? where id=?',('stream_complete',scan));db.commit()
            report(db,scan,settings);print('Cleanup job already completed.',flush=True);return 0
        cursor=saved['cursor'];timestamp=saved['timestamp']
        if not isinstance(cursor,str) or not cursor:raise ValueError('Invalid saved cursor')
        starting_from_checkpoint=True
        print('Resuming after the last fully audited library page.',flush=True)
    def journal(key,state,detail=''):
        db.execute('insert into journal values(?,?,?,?,?)',(scan,key,state,time.time(),detail));db.commit()
    pending=db.execute("select key from journal where scan=? and rowid in(select max(rowid) from journal where scan=? group by key) and state in('intent','uncertain')",(scan,scan)).fetchall()
    for (pending_key,) in pending:
        saved=db.execute('select payload from items where scan=? and key=?',(scan,pending_key)).fetchone()
        if not saved or not in_trash(c,pending_key,json.loads(saved[0])['dedup_key']):
            raise ValueError('Unresolved prior mutation; no further writes allowed')
        journal(pending_key,'confirmed','Reconciled at startup without resending mutation')
    try:
        while True:
            request=[cursor,timestamp,100,None,1,3]
            try:
                raw=c.rpc('lcxiM',request,parse=False)
            except PhotosApiError:
                if not starting_from_checkpoint:raise
                c=checked_client(settings,args.expect_account)
                print('Saved cursor could not be read; safely restarting enumeration.',flush=True)
                cursor=timestamp=None;starting_from_checkpoint=False
                checkpoint.unlink(missing_ok=True)
                continue
            starting_from_checkpoint=False
            if isinstance(raw,list) and len(raw)<2:
                c=checked_client(settings,args.expect_account)
                raw=c.rpc('lcxiM',request,parse=False)
                print('Short terminal-page response rechecked with a fresh authenticated client.',flush=True)
            rows,cursor,timestamp=read_library_page(raw,timestamp)
            if not rows and cursor:
                print('Empty library page; following the next-page cursor.',flush=True)
            groups={}
            for row in rows:
                dedup=safe_get(row,3)
                groups.setdefault(dedup,[]).append(row)
            for row in rows:
                key=safe_get(row,0)
                if not isinstance(key,str):raise ValueError('Missing library identity')
                if key in seen:continue
                seen.add(key)
                status=db.execute('select state from journal where scan=? and key=? order by rowid desc limit 1',(scan,key)).fetchone()
                if status and status[0] in ('intent','uncertain'):
                    if in_trash(c,key,safe_get(row,3)):journal(key,'confirmed','Reconciled');continue
                    raise ValueError('Unresolved mutation; manual diagnosis required')
                if status and status[0]=='confirmed':continue
                parsed=MediaItem.from_library_data(row)
                base=dict(media_key=key,dedup_key=parsed.dedup_key,is_owned=owned(row),created_ts=parsed.creation_timestamp,
                    archived=parsed.is_archived,is_video=parsed.video_duration is not None,is_live_photo=parsed.is_live_photo,
                    partial_upload=safe_get(row,12,0)==20)
                album_protected=key in index or base['dedup_key'] in index.protected_dedup
                if args.write and (base['is_owned'] is not True or album_protected):
                    item={**base, 'takes_space':None, 'charged_bytes':None, 'quality_code':None,
                        'albums':sorted(index.get(key,set())),
                        'decision':('other_owner' if base['is_owned'] is False else 'unknown_owner') if base['is_owned'] is not True else 'album_protected',
                        'provenance':'lcxiM ownership flags; and complete album index; remaining metadata not requested for protected write-mode item'}
                    db.execute('insert or replace into items values(?,?,?)',(scan,key,json.dumps(item)));db.commit();checked+=1
                    print(json.dumps({'checked':checked,'media_key':key,'owned':base['is_owned'],
                        'decision':item['decision'],'action':'PRESERVED_WITHOUT_DETAIL_REQUESTS'}),flush=True)
                    continue
                item,c=audit_with_recovery(c,base,index,settings,args.expect_account)
                metadata_failures=metadata_failures+1 if item['decision']=='metadata_unavailable' else 0
                db.execute('insert or replace into items values(?,?,?)',(scan,key,json.dumps(item)));db.commit();checked+=1
                print(json.dumps({'checked':checked,'media_key':key,'owned':item['is_owned'],'counts_storage':item['takes_space'],
                    'independent_storage_check':item.get('secondary_storage_flag'),'charged_bytes':item['charged_bytes'],
                    'albums':len(item['albums']),'decision':item['decision']}),flush=True)
                if metadata_failures>=5:raise PhotosApiError('Five consecutive item metadata failures despite valid account; stopped for diagnosis')
                if not args.write or not qualifies(item,index):continue
                # A mutation key must identify one current page record; all album aliases are protected globally.
                if len(groups.get(item['dedup_key'],[]))!=1:
                    journal(key,'skipped','Ambiguous mutation identity');continue
                # Refresh metadata immediately before the write, after recording the initial proof.
                fresh,c=audit_with_recovery(c,base,index,settings,args.expect_account)
                if not qualifies(fresh,index) or fresh['dedup_key']!=item['dedup_key']:
                    journal(key,'skipped','Fresh metadata no longer qualifies');continue
                journal(key,'intent')
                c=move_with_reconciliation(c,fresh,settings,args.expect_account,journal)
                journal(key,'confirmed');moved+=1
                print(json.dumps({'action':'TRASH_CONFIRMED','media_key':key,'moved_this_run':moved,'charged_bytes':fresh['charged_bytes']}),flush=True)
                if args.limit and moved>=args.limit:
                    db.execute('update scans set state=? where id=?',('paused',scan));db.commit();return 0
            if cursor and (cursor in cursors or cursor==request[0]):raise ValueError('Repeated library cursor')
            save_checkpoint(checkpoint,scan,c.email,cursor,timestamp)
            print(json.dumps({'action':'PAGE_CHECKPOINT','more_pages':bool(cursor),'checked_this_run':checked,'moved_this_run':moved}),flush=True)
            if not cursor:break
            cursors.add(cursor)
        db.execute('update scans set state=? where id=?',('stream_complete',scan));db.commit()
    finally:
        report(db,scan,settings)
        (directory/'quota-after.json').write_text(json.dumps({'quota':quota(c),'confirmed_this_run':moved,'savings_status':'pending_server_accounting'}))
    return 0
