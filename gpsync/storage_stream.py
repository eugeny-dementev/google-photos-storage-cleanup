"""Incremental library audit and explicitly authorized, recoverable Trash."""
import json
import time
import uuid
from gpsync.client import _extract_wrb_payload
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
    seen=set();cursors=set();cursor=timestamp=None;checked=moved=0
    def journal(key,state,detail=''):
        db.execute('insert into journal values(?,?,?,?,?)',(scan,key,state,time.time(),detail));db.commit()
    try:
        while True:
            raw=c.rpc('lcxiM',[cursor,timestamp,100,None,1,3],parse=False)
            rows=safe_get(raw,0)
            if not isinstance(rows,list):raise ValueError('Malformed library page')
            cursor=safe_get(raw,1);timestamp=safe_get(raw,2)
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
                if args.write and base['is_owned'] is not True:
                    item={**base, 'takes_space':None, 'charged_bytes':None, 'quality_code':None,
                        'albums':sorted(index.get(key,set())),
                        'decision':'other_owner' if base['is_owned'] is False else 'unknown_owner',
                        'provenance':'lcxiM ownership flags; remaining metadata not requested for protected write-mode item'}
                    db.execute('insert or replace into items values(?,?,?)',(scan,key,json.dumps(item)));db.commit();checked+=1
                    print(json.dumps({'checked':checked,'media_key':key,'owned':base['is_owned'],
                        'decision':item['decision'],'action':'PRESERVED_WITHOUT_DETAIL_REQUESTS'}),flush=True)
                    continue
                item=enrich(base,c.rpc('fDcn4b',[key,1,None,None,1],parse=False),index)
                item=crosscheck(item,c.rpc('VrseUb',[key,None,None,None,None],parse=False))
                db.execute('insert or replace into items values(?,?,?)',(scan,key,json.dumps(item)));db.commit();checked+=1
                print(json.dumps({'checked':checked,'media_key':key,'owned':item['is_owned'],'counts_storage':item['takes_space'],
                    'independent_storage_check':item['secondary_storage_flag'],'charged_bytes':item['charged_bytes'],
                    'albums':len(item['albums']),'decision':item['decision']}),flush=True)
                if not args.write or not qualifies(item,index):continue
                # A mutation key must identify one current page record; all album aliases are protected globally.
                if len(groups.get(item['dedup_key'],[]))!=1:
                    journal(key,'skipped','Ambiguous mutation identity');continue
                # Refresh metadata immediately before the write, after recording the initial proof.
                fresh=enrich(base,c.rpc('fDcn4b',[key,1,None,None,1],parse=False),index)
                fresh=crosscheck(fresh,c.rpc('VrseUb',[key,None,None,None,None],parse=False))
                if not qualifies(fresh,index) or fresh['dedup_key']!=item['dedup_key']:
                    journal(key,'skipped','Fresh metadata no longer qualifies');continue
                journal(key,'intent')
                try:move_one(c,fresh)
                except Exception as error:journal(key,'uncertain',type(error).__name__);raise
                journal(key,'confirmed');moved+=1
                print(json.dumps({'action':'TRASH_CONFIRMED','media_key':key,'moved_this_run':moved,'charged_bytes':fresh['charged_bytes']}),flush=True)
                if args.limit and moved>=args.limit:
                    db.execute('update scans set state=? where id=?',('paused',scan));db.commit();return 0
            if not cursor:break
            if cursor in cursors:raise ValueError('Repeated library cursor')
            cursors.add(cursor)
        db.execute('update scans set state=? where id=?',('stream_complete',scan));db.commit()
    finally:
        report(db,scan,settings)
        (directory/'quota-after.json').write_text(json.dumps({'quota':quota(c),'confirmed_this_run':moved,'savings_status':'pending_server_accounting'}))
    return 0
