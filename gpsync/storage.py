"""Account-bound storage audit; cloud mutations require UI evidence and report review."""
from __future__ import annotations
import csv
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

def bounded(pool, fn, items):
    iterator=iter(items); pending=[]
    for _ in range(4):
        item=next(iterator,None)
        if item is not None: pending.append(pool.submit(fn,item))
    while pending:
        yield pending.pop(0).result()
        item=next(iterator,None)
        if item is not None: pending.append(pool.submit(fn,item))

from pathlib import Path
from gpsync.client import PhotosClient
from gpsync.parser import MediaItem
from gpsync.util import safe_get


def add_commands(sub):
    p = sub.add_parser('storage', help='Independent cloud storage audit and gated Trash')
    commands = p.add_subparsers(dest='storage_command', required=True)
    from gpsync.storage_stream import add_stream
    add_stream(commands)
    for name in ('scan', 'report', 'trash', 'validate'):
        c = commands.add_parser(name)
        if name != 'scan': c.add_argument('--scan-id', required=True)
        else: c.add_argument('--resume', metavar='SCAN_ID')
        if name in ('scan', 'trash'): c.add_argument('--expect-account', required=True)
        if name == 'validate': c.add_argument('--evidence', type=Path, required=True)
        if name == 'trash':
            c.add_argument('--reviewed-report', required=True, help='SHA-256 printed by storage report')
            c.add_argument('--limit', type=int, default=10)


def boolean(value):
    return bool(value) if type(value) is int and value in (0, 1) else None


def owned(raw):
    flags = safe_get(raw, 7)
    if not isinstance(flags, list) or any(not isinstance(x, list) for x in flags): return None
    return not any(27 in x for x in flags)


def details(raw):
    row = safe_get(raw, 0)
    if not isinstance(row, list) or not safe_get(row, 0): raise ValueError('Malformed item detail')
    storage = safe_get(row, 30)
    albums = safe_get(row, 19)
    if albums is not None and not isinstance(albums, list): raise ValueError('Malformed albums')
    amount = safe_get(storage, 1)
    if type(amount) is not int or amount < 0: amount = None
    return dict(media_key=safe_get(row, 0), dedup_key=safe_get(row, 11), filename=safe_get(row, 2),
        size=safe_get(row, 5), width=safe_get(row, 6), height=safe_get(row, 7),
        taken_ts=safe_get(row, 3), owner=safe_get(row, 27, 3, 0) or safe_get(row, 27, 4, 0) or safe_get(row, 28),
        source=safe_get(row, 27, 0), source_platform=safe_get(row, 27, 1, 2),
        quality_code=safe_get(storage, 2), takes_space=boolean(safe_get(storage, 0)), charged_bytes=amount,
        detail_albums=[safe_get(a, 0) for a in albums] if isinstance(albums, list) else None,
        checked_at=time.time(), provenance='fDcn4b:[0][30], [0][19], [0][27/28]')


def decision(item):
    if item.get('storage_conflict'): return 'conflicting_storage'
    if item.get('is_owned') is not True: return 'other_owner' if item.get('is_owned') is False else 'unknown_owner'
    if item.get('takes_space') is False and (item.get('charged_bytes') or 0)>0: return 'conflicting_storage'
    if item.get('takes_space') is not True: return 'free' if item.get('takes_space') is False else 'unknown_storage'
    if item.get('charged_bytes') == 0: return 'conflicting_storage'
    if item.get('membership_conflict'): return 'conflicting_membership'
    if not item.get('membership_complete'): return 'unknown_membership'
    if item.get('albums'): return 'album_protected'
    if item.get('partial_upload'): return 'partial_upload'
    if not item.get('dedup_key'): return 'missing_mutation_id'
    return 'eligible'


class Gate:
    def __init__(self): self.lock = threading.Lock(); self.next = 0
    def wait(self):
        with self.lock:
            delay = self.next - time.monotonic()
            if delay > 0: time.sleep(delay)
            self.next = time.monotonic() + .25


def pages(client, rpc, data):
    seen = set()
    cursor = None
    while True:
        raw = client.rpc(rpc, data(cursor), parse=False)
        if not isinstance(raw, list) or not isinstance(safe_get(raw, 0), list):
            raise ValueError('Incomplete enumeration: ' + rpc)
        yield raw
        cursor = safe_get(raw, 1)
        if not cursor: break
        if cursor in seen: raise ValueError('Repeated pagination cursor')
        seen.add(cursor)


def library(client):
    result = {}
    for source in (1, 2):
        cursor = timestamp = None
        seen = set()
        while True:
            raw = client.rpc('lcxiM', [cursor, timestamp, 500, None, 1, source], parse=False)
            if not isinstance(raw, list) or not isinstance(safe_get(raw, 0), list): raise ValueError('Incomplete library')
            for row in raw[0]:
                key = safe_get(row, 0)
                if not isinstance(key, str): raise ValueError('Missing media key')
                parsed = MediaItem.from_library_data(row)
                result[key] = dict(media_key=key, is_owned=owned(row), dedup_key=parsed.dedup_key,
                    created_ts=parsed.creation_timestamp, archived=parsed.is_archived,
                    is_video=parsed.video_duration is not None, is_live_photo=parsed.is_live_photo,
                    partial_upload=safe_get(row, 12, 0) == 20)
            print(f'Library source {source}: {len(result)} items enumerated',flush=True)
            cursor = safe_get(raw, 1); timestamp = safe_get(raw, 2)
            if not cursor: break
            if cursor in seen: raise ValueError('Repeated library cursor')
            seen.add(cursor)
    return result


class AlbumIndex(dict):
    def __init__(self): super().__init__(); self.protected_dedup=set()


def membership(client):
    albums = {}
    for raw in pages(client, 'Z5xsfc', lambda c: [c,None,None,None,1,None,None,100,[2],5]):
        for row in raw[0]:
            key = safe_get(row, 0)
            if not key: raise ValueError('Missing album key')
            albums[key] = None
    for raw in pages(client, 'F2A0H', lambda c: [c,None,2,None,3]):
        for row in raw[0]:
            key = safe_get(row, 6)
            if not key: raise ValueError('Missing shared album key')
            albums[key] = safe_get(row, 7)
    index = AlbumIndex()
    print(f'Protecting membership in {len(albums)} albums',flush=True)
    for number,(key, auth) in enumerate(albums.items(),1):
        cursor = None; seen = set()
        while True:
            raw = client.rpc('snAcKc', [key,cursor,None,auth], parse=False)
            items = safe_get(raw, 1)
            if items is None and safe_get(raw,3,0)==key and safe_get(raw,3,21)==0 and not safe_get(raw,2):
                items=[]
            if not isinstance(items, list): raise ValueError('Incomplete album membership')
            for row in items:
                media = safe_get(row, 0)
                if not media: raise ValueError('Missing album item key')
                dedup = safe_get(row,3)
                if not isinstance(dedup,str) or not dedup: raise ValueError('Album mutation identity unknown')
                index.protected_dedup.add(dedup)
                index.setdefault(media, set()).add(key)
            cursor = safe_get(raw, 2)
            if not cursor: break
            if cursor in seen: raise ValueError('Repeated album cursor')
            seen.add(cursor)
        print(f'Album membership: {number}/{len(albums)}',flush=True)
    return index


def enrich(base, raw, index):
    info = details(raw)
    if info['media_key'] != base['media_key']: raise ValueError('Item identity mismatch')
    if info['dedup_key'] and base.get('dedup_key') != info['dedup_key']: raise ValueError('Mutation identity mismatch')
    info['dedup_key'] = base.get('dedup_key')
    item = {**base, **info}
    if item.get('source') in (3,4) and item.get('is_owned') is True: item['is_owned']=None
    indexed = index.get(base['media_key'], set())
    listed = set(item['detail_albums'] or [])
    if None in listed: raise ValueError('Malformed album identity')
    item['albums'] = sorted(indexed | listed)
    item['membership_complete'] = True
    # Positive evidence always protects; contradictory absence must never authorize deletion.
    item['membership_conflict'] = bool(indexed and item['detail_albums'] == [])
    item['decision'] = decision(item)
    return item


def crosscheck(item, raw):
    row=safe_get(raw,0)
    if not isinstance(row,list) or safe_get(row,0)!=item['media_key']: raise ValueError('Item-info identity mismatch')
    ext=next((v for v in row if isinstance(v,dict) and '318563170' in v),{})
    storage=safe_get(ext,'318563170',0)
    flag=boolean(safe_get(storage,0));amount=safe_get(storage,1)
    item['secondary_storage_flag']=flag
    item['storage_conflict']=(flag is not None and item['takes_space'] is not None and flag!=item['takes_space'])
    if type(amount) is int and item['charged_bytes'] is not None and amount!=item['charged_bytes']: item['storage_conflict']=True
    item['decision']=decision(item)
    return item


_REQUEST_GATE=Gate()


def connect(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.data_dir / 'storage.sqlite')
    db.execute('pragma journal_mode=WAL')
    db.executescript("""CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY,account TEXT,state TEXT,validation TEXT);
    CREATE TABLE IF NOT EXISTS items(scan TEXT,key TEXT,payload TEXT,PRIMARY KEY(scan,key));
    CREATE TABLE IF NOT EXISTS journal(scan TEXT,key TEXT,state TEXT,at REAL,detail TEXT);""")
    return db


def checked_client(settings, email):
    network = json.loads((settings.data_dir / 'network.json').read_text())
    if not network.get('proxy'): raise ValueError('Cleanup proxy is not configured')
    c = PhotosClient(settings.cookies_path, settings.account_index, proxy=network['proxy'])
    if not c.email or c.email.casefold() != email.casefold(): raise ValueError('Google account mismatch')
    original=c.rpc
    def throttled(*a,**kw):
        _REQUEST_GATE.wait();return original(*a,**kw)
    c.rpc=throttled
    return c


def quota(c):
    try:
        raw = c.rpc('EzwWhf', [], parse=False)
        return dict(used=safe_get(raw,6,0), total=safe_get(raw,6,1), photos=safe_get(raw,6,3),at=time.time())
    except Exception as e: return {'unavailable':type(e).__name__}


def report(db, scan, settings):
    rows = [json.loads(r[0]) for r in db.execute('select payload from items where scan=? order by key',(scan,))]
    body = json.dumps(rows, sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()
    directory = settings.data_dir / 'reports' / scan
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'items.json').write_bytes(body)
    fields = ['media_key','filename','is_owned','takes_space','charged_bytes','quality_code','archived','is_video','is_live_photo','created_ts','taken_ts','source','owner','albums','decision']
    with (directory/'items.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fields,extrasaction='ignore');writer.writeheader()
        for row in rows:
            safe={k:(json.dumps(row[k]) if isinstance(row.get(k),(dict,list)) else row.get(k)) for k in fields}
            for k,v in safe.items():
                if isinstance(v,str) and v.startswith(('=','+','-','@')): safe[k]="'"+v
            writer.writerow(safe)
    summary={'scan_id':scan,'account':db.execute('select account from scans where id=?',(scan,)).fetchone()[0],
        'items':len(rows),'eligible':sum(r['decision']=='eligible' for r in rows),
        'charged_bytes_known':sum(r['charged_bytes'] or 0 for r in rows if r['decision']=='eligible'),
        'eligible_bytes_unknown':sum(r.get('charged_bytes') is None for r in rows if r['decision']=='eligible'),
        'journal':dict(db.execute('select state,count(*) from journal where scan=? group by state',(scan,)).fetchall()),
        'report_sha256':digest,'decisions':{d:sum(r['decision']==d for r in rows) for d in sorted(set(r['decision'] for r in rows))}}
    (directory/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2));return rows,digest,directory


def validation(rows, evidence):
    bykey={r['media_key']:r for r in rows}; covered=set()
    for sample in evidence:
        item=bykey[sample['media_key']]
        for field in ('is_owned','takes_space','albums','quality_code'):
            if field not in sample or sample[field]!=item.get(field): raise ValueError('UI evidence mismatch: '+field)
        if not sample.get('ui_checked_at') or not sample.get('notes'): raise ValueError('UI evidence needs time and notes')
        if item['is_owned'] is False: covered.add('external')
        if item['is_owned'] is True and item['takes_space'] is False: covered.add('free')
        if item['decision']=='eligible': covered.add('eligible')
        if item['albums']: covered.add('album')
        if item['takes_space'] is True and item.get('quality_code') not in (None,2): covered.add('charged_compressed')
    required={'external','free','eligible','album','charged_compressed'}
    if not required <= covered: raise ValueError('Missing UI sample classes: '+str(required-covered))


def run(args, settings):
    db=connect(settings)
    if args.storage_command=='stream':
        from gpsync.storage_stream import stream
        return stream(args,settings,db)
    if args.storage_command=='scan':
        c=checked_client(settings,args.expect_account)
        scan=args.resume or uuid.uuid4().hex
        old=db.execute('select account from scans where id=?',(scan,)).fetchone()
        if old and old[0]!=c.email: raise ValueError('Resume account mismatch')
        db.execute('insert or ignore into scans values(?,?,?,NULL)',(scan,c.email,'scanning'));db.commit()
        print('Scan:',scan,flush=True)
        before=quota(c); base=library(c); index=membership(c)
        cached={r[0]:json.loads(r[1]) for r in db.execute('select key,payload from items where scan=?',(scan,))}
        db.execute('delete from items where scan=?',(scan,));db.commit()
        gate=Gate(); local=threading.local()
        def fetch(item):
            if not hasattr(local,'client'): local.client=checked_client(settings,args.expect_account)
            gate.wait()
            result=enrich(item,local.client.rpc('fDcn4b',[item['media_key'],1,None,None,1],parse=False),index)
            return crosscheck(result,local.client.rpc('VrseUb',[item['media_key'],None,None,None,None],parse=False))
        todo=[]
        for item in base.values():
            previous=cached.get(item['media_key'])
            if previous and time.time()-previous['checked_at'] < 3600 and all(previous.get(k)==v for k,v in item.items()):
                refreshed=dict(previous)
                refreshed['albums']=sorted(set(previous.get('detail_albums') or []) | index.get(item['media_key'],set()))
                refreshed['membership_conflict']=bool(index.get(item['media_key']) and previous.get('detail_albums') == [])
                refreshed['decision']=decision(refreshed)
                db.execute('insert into items values(?,?,?)',(scan,item['media_key'],json.dumps(refreshed)))
            else: todo.append(item)
        db.commit()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                for n,item in enumerate(bounded(pool,fetch,todo),1):
                    db.execute('insert into items values(?,?,?)',(scan,item['media_key'],json.dumps(item)));db.commit()
                    if n%100==0: print('Metadata:',n,'/',len(todo),flush=True)
        except Exception:
            db.execute('update scans set state=? where id=?',('incomplete',scan));db.commit();raise
        db.execute('update scans set state=?,validation=NULL where id=?',('complete',scan));db.commit()
        _,_,directory=report(db,scan,settings)
        (directory/'quota-before.json').write_text(json.dumps(before,indent=2));return 0
    scan=args.scan_id
    state=db.execute('select account,state,validation from scans where id=?',(scan,)).fetchone()
    if not state: raise ValueError('Unknown scan')
    rows,digest,directory=report(db,scan,settings)
    if args.storage_command=='report': return 0
    if state[1]!='complete': raise ValueError('Incomplete scan; mutations blocked')
    if args.storage_command=='validate':
        evidence=json.loads(args.evidence.read_text());validation(rows,evidence)
        db.execute('update scans set validation=? where id=?',(json.dumps({'hash':digest,'evidence':evidence}),scan));db.commit();return 0
    if args.limit<1: raise ValueError('Limit must be positive')
    if not state[2] or json.loads(state[2])['hash']!=digest: raise ValueError('UI metadata validation required')
    if args.reviewed_report!=digest: raise ValueError('Reviewed report hash mismatch')
    c=checked_client(settings,args.expect_account)
    if c.email!=state[0]: raise ValueError('Scan account mismatch')
    current=library(c); index=membership(c); before=quota(c)
    (directory/'trash-quota-before.json').write_text(json.dumps(before,indent=2))
    candidates=[r for r in rows if r['decision']=='eligible']
    aliases={}
    for item in current.values(): aliases.setdefault(item.get('dedup_key'),set()).add(item['media_key'])
    allowed={r['media_key'] for r in candidates}
    def journal(key,status,detail=''):
        db.execute('insert into journal values(?,?,?,?,?)',(scan,key,status,time.time(),detail));db.commit()
    def in_trash(key,dedup):
        for page in pages(c,'zy0IHe',lambda cursor:[cursor]):
            if any(safe_get(r,3)==dedup or safe_get(r,0)==key for r in page[0]): return True
        return False
    moved=0
    for item in candidates:
        key=item['media_key']
        previous=db.execute('select state from journal where scan=? and key=? order by rowid desc limit 1',(scan,key)).fetchone()
        if previous and previous[0] in ('intent','uncertain'):
            if in_trash(key,item['dedup_key']):
                journal(key,'confirmed','Reconciled after interruption');continue
            raise ValueError('Unresolved previous action; no automatic retry: '+key)
        if previous and previous[0]=='confirmed': continue
        if moved>=args.limit: break
        if key not in current: journal(key,'skipped','No longer in library');continue
        fresh=enrich(current[key],c.rpc('fDcn4b',[key,1,None,None,1],parse=False),index)
        if fresh['decision']!='eligible' or fresh['dedup_key']!=item['dedup_key'] or not aliases.get(fresh['dedup_key'],set())<=allowed:
            journal(key,'skipped','Eligibility or identifiers changed');continue
        # Read item presence immediately before mutation; response also carries storage evidence.
        info=c.rpc('VrseUb',[key,None,None,None,None],parse=False)
        fresh=crosscheck(fresh,info)
        if fresh['decision']!='eligible': journal(key,'skipped','Conflicting fresh storage evidence');continue
        for alias in aliases.get(fresh['dedup_key'],set())-{key}:
            other=enrich(current[alias],c.rpc('fDcn4b',[alias,1,None,None,1],parse=False),index)
            if other['decision']!='eligible': raise ValueError('Protected mutation alias')
        journal(key,'intent')
        try:
            # rpc() retries reads; never use it for mutations.
            import urllib.parse
            payload={'f.req':json.dumps([[['XwAOJf',json.dumps([None,1,[fresh['dedup_key']],3]),None,'generic']]]), 'at':c.global_data['SNlM0e']}
            _REQUEST_GATE.wait()
            response=c.session.post(f"https://photos.google.com{c.global_data['Im6cmf']}/data/batchexecute",params={'rpcids':'XwAOJf','source-path':c.base_path,'f.sid':c.global_data['FdrFJe'],'bl':c.global_data['cfb2h'],'rt':'c'},data=payload,timeout=60)
            response.raise_for_status()
            if 'accounts.google.com' in response.url: raise ValueError('Authentication changed')
            from gpsync.client import _extract_wrb_payload
            mutation_result=_extract_wrb_payload(response.text)
            if not isinstance(mutation_result,list) or not mutation_result: raise ValueError('Unexpected Trash response')
            found=in_trash(key,fresh['dedup_key'])
            if not found: raise ValueError('Trash result unconfirmed')
            journal(key,'confirmed');moved+=1
        except Exception as error:
            journal(key,'uncertain',type(error).__name__);raise
    (directory/'quota-after.json').write_text(json.dumps({'before':before,'after':quota(c),'savings_status':'pending_server_accounting','confirmed_this_run':moved},indent=2))
    print('Confirmed moved to Trash:',moved);return 0
