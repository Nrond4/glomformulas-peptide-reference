"""Two-stage publishing: encrypted backup upload must succeed before apply."""
import argparse
import json
import os
from pathlib import Path
from .core import connect, encoded, now, key
from .importers import tabs, text
from .monitor_state import encrypt
from .publishing import (PublishError, VERSION, initialize, entries, render, plan,
                         managed, unchanged_prefix, preserved_prefix, digest)

def api():
    import google.auth
    from googleapiclient.discovery import build
    credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/documents'])
    return build('docs','v1',credentials=credentials,cache_discovery=False).documents()

def read(service,ident):
    return service.get(documentId=ident,includeTabsContent=True).execute(num_retries=2)

def settings():
    original=os.environ.get('GLO_DOCS_ORIGINAL_ID','').strip()
    test=os.environ.get('GLO_DOCS_TEST_ID','').strip()
    mode=os.environ.get('GLO_DOCS_MODE','disabled')
    if mode not in ('test','live'):raise PublishError('Publishing disabled')
    if not original or not test or original==test:raise PublishError('Distinct original and test document IDs required')
    if os.environ.get('GITHUB_REF')!='refs/heads/main':raise PublishError('Main branch required')
    return mode,original if mode=='live' else test,test

def gate(db,mode,test):
    if mode!='live':return
    receipt=os.environ.get('GLO_DOCS_TEST_VERIFIED','')
    row=db.execute("SELECT * FROM publish_history WHERE document_id=? AND mode='test' AND status='verified' ORDER BY rowid DESC LIMIT 1",(test,)).fetchone()
    if not row or receipt!=row['id'] or json.loads(row['detail_json']).get('version')!=VERSION:
        raise PublishError('Successful current-version test and explicit human verification required')

def prepare(root,out,service=None):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    mode,target,test=settings()
    with connect(root/'reference.sqlite') as db:
        initialize(db);gate(db,mode,test)
        # An unresolved network write is never retried automatically.
        if db.execute("SELECT 1 FROM publish_history WHERE document_id=? AND status IN ('applying','uncertain')",(target,)).fetchone():
            raise PublishError('Uncertain previous write requires reconciliation')
        result=json.loads((out/'monitor-result.json').read_text())
        current=read(service or api(),target)
        previous=db.execute("SELECT digest FROM publish_history WHERE document_id=? AND status='verified' ORDER BY rowid DESC LIMIT 1",(target,)).fetchone()
        if previous and digest(managed(current))!=previous['digest']:
            raise PublishError('Managed section was edited manually; review before replacing')
        if not previous and any(text(b)=='GLOFORMULAS VERIFIED UPDATES — START\n' for _,blocks in tabs(current) for b in blocks):
            if managed(current)!=render({}):raise PublishError('Untracked managed content requires recovery review')
        facts=entries(db,result);proposal=plan(current,render(facts))
        record={'version':VERSION,'mode':mode,'target':target,'test':test,'document':current,
                'plan':proposal,'facts':facts,'created_at':now()}
        (out/'publish-plan.json').write_text(encoded(record))
        # Full native document JSON including styles, tabs, links and native controls. No Google credential.
        (out/'docs-backup.enc').write_bytes(encrypt(encoded(record).encode(),os.environ['GLO_STATE_KEY'],'report'))
        return bool(proposal['requests'])

def apply(root,out,service=None):
    root=Path(root);out=Path(out)
    if os.environ.get('GLO_DOCS_BACKUP_UPLOADED')!='true':raise PublishError('Durable encrypted backup required')
    record=json.loads((out/'publish-plan.json').read_text())
    mode,target,test=settings()
    if (mode,target,test)!=(record['mode'],record['target'],record['test']):raise PublishError('Target configuration changed')
    service=service or api();before=read(service,target)
    if before['revisionId']!=record['document']['revisionId']:raise PublishError('Concurrent edit: regenerate the publication')
    receipt=key(VERSION,target,record['plan']['digest'],record['created_at'])
    with connect(root/'reference.sqlite') as db:
        initialize(db);gate(db,mode,test)
        if db.execute("SELECT 1 FROM publish_history WHERE document_id=? AND status IN ('applying','uncertain')",(target,)).fetchone():
            raise PublishError('Previous write needs reconciliation')
        db.execute('INSERT INTO publish_history VALUES(?,?,?,?,?,?,?,?)',
          (receipt,target,mode,record['plan']['digest'],before['revisionId'],'applying',now(),encoded({
              'version':VERSION,'facts':record['facts'],'prefix_digest':digest(preserved_prefix(before)),
              'before_digest':digest(managed(before)) if any(text(b)=='GLOFORMULAS VERIFIED UPDATES — START\n' for _,blocks in tabs(before) for b in blocks) else None})))
        db.commit()
        print('Publication operation receipt: '+receipt)
        try:
            if record['plan']['requests']:
                # No retry: a timeout can mean the batch was already applied.
                service.batchUpdate(documentId=target,body={'writeControl':{'requiredRevisionId':before['revisionId']},
                    'requests':record['plan']['requests']}).execute()
            actual=read(service,target)
            if managed(actual)!=record['plan']['content'] or not unchanged_prefix(before,actual):
                raise PublishError('Readback mismatch; inspect backup and version history')
            db.execute('DELETE FROM publish_entries')
            for ident,payload in record['facts'].items():
                db.execute('INSERT OR REPLACE INTO publish_entries VALUES(?,?)',(ident,encoded(payload)))
            db.execute("UPDATE publish_history SET status='verified' WHERE id=?",(receipt,))
            db.commit()
        except Exception:
            db.execute("UPDATE publish_history SET status='uncertain' WHERE id=?",(receipt,));db.commit()
            raise PublishError('Write outcome uncertain; publishing paused pending reconciliation') from None
    return receipt

def reconcile(root,service=None):
    """Explicit browser-triggered recovery, only when the exact known state can be proven."""
    mode,target,test=settings()
    actual=read(service or api(),target)
    with connect(Path(root)/'reference.sqlite') as db:
        initialize(db)
        row=db.execute("SELECT * FROM publish_history WHERE document_id=? AND status IN ('applying','uncertain') ORDER BY rowid DESC LIMIT 1",(target,)).fetchone()
        if not row:raise PublishError('No uncertain operation')
        if os.environ.get('GLO_DOCS_REVIEW_DIGEST')!=row['id']:raise PublishError('Explicit operation receipt required')
        detail=json.loads(row['detail_json'])
        if digest(preserved_prefix(actual))!=detail['prefix_digest']:raise PublishError('Original content differs: restore using Google version history')
        try:current=digest(managed(actual))
        except PublishError:current=None
        status='verified' if current==row['digest'] else ('not_applied' if current==detail.get('before_digest') else None)
        if not status:raise PublishError('Unexpected content: restore using Google version history')
        if status=='verified':
            db.execute('DELETE FROM publish_entries')
            for ident,payload in detail.get('facts',{}).items():
                db.execute('INSERT OR REPLACE INTO publish_entries VALUES(?,?)',(ident,encoded(payload)))
        db.execute('UPDATE publish_history SET status=? WHERE id=?',(status,row['id']))
    return status

def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['prepare','apply','review','reconcile'])
    parser.add_argument('--state',required=True);parser.add_argument('--out',required=True);args=parser.parse_args()
    try:
        if args.operation=='reconcile':
            if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':raise PublishError('Manual recovery required')
            print('Recovery reconciled: '+reconcile(args.state)+'.')
        elif args.operation=='review':
            from .publishing import enqueue, approve
            if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.environ.get('GITHUB_REF')!='refs/heads/main':
                raise PublishError('Reviews require a manual main-branch run')
            request=json.loads(os.environ['GLO_DOCS_REVIEW_JSON'])
            with connect(Path(args.state)/'reference.sqlite') as db:
                initialize(db);ident=enqueue(db,request['kind'],request['payload'])
                expected=digest(request['payload'])
                if os.environ.get('GLO_DOCS_REVIEW_ACTION')=='approve':
                    approve(db,ident,os.environ.get('GLO_DOCS_REVIEW_DIGEST',''),os.environ.get('GITHUB_ACTOR',''))
                    print('Exact proposal approved by the manual workflow initiator.')
                else:print('Review queued. Exact proposal digest: '+expected)
        elif args.operation=='prepare':
            changed=prepare(args.state,args.out)
            print('Encrypted document backup prepared. Planned changes: '+('yes' if changed else 'none')+'.')
        else:
            receipt=apply(args.state,args.out)
            # Receipt is an opaque hash, no document IDs, prose, sources or credentials.
            print('Document readback verified. Test/operation receipt: '+receipt)
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:
                stream.write('\nGoogle Docs publishing: readback verified. Receipt: `'+receipt+'`.\n')
    except Exception:
        print('Google Docs publishing stopped safely. Check configuration, document sharing, revision conflicts or uncertain-write history. No automatic retry.')
        raise SystemExit(1) from None

if __name__=='__main__':main()
