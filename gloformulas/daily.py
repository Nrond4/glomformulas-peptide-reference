"""Independent source checks with honest partial/offline outcomes and private summaries."""
import json
import subprocess
from pathlib import Path
from .core import encoded,key,now,audit
from .config import config
from .http import FetchError
from .importers import import_guide,import_folder
from .catalog import import_catalog
from .supplier import compare_supplier,fetch_public_sheet
from .research import initialize_coverage,coverage_report,discover
from .guide import render


def save_json(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(encoded(payload));temp.replace(path)


def schedule_status():
    result={'hosting_confirmed':False,'daily_schedule_confirmed':False,'detail':'No active scheduler has been confirmed. Prepared code does not mean daily checks are running.'}
    try:
        enabled=subprocess.run(['systemctl','is-enabled','gloformulas.timer'],capture_output=True,text=True,timeout=3)
        active=subprocess.run(['systemctl','is-active','gloformulas.timer'],capture_output=True,text=True,timeout=3)
        result.update({'timer_enabled':enabled.stdout.strip()=='enabled','timer_active':active.stdout.strip()=='active'})
        result['daily_schedule_confirmed']=result['timer_enabled'] and result['timer_active']
        if result['daily_schedule_confirmed']:result['detail']='Timer active on this host; confirm this host remains available and inspect successful source-check reports.'
    except (OSError,subprocess.TimeoutExpired):pass
    return result


def daily(db,state_dir='work',live=False,services=None):
    services=services or {};root=Path(state_dir);imports=root/'imports';rid=key(now());started=now()
    db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid,started,None,'running',None));db.commit()
    starting_rows={table:db.execute(f'SELECT coalesce(max(rowid),0) FROM {table}').fetchone()[0] for table in ('catalog_changes','supplier_changes','flags')}
    before={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('catalog_changes','supplier_changes','research_candidates','flags','lab_fetches')}
    checks=[]
    def stage(name,operation,success='checked'):
        try:
            with db:detail=operation()
            detail={} if detail is None else detail
            outcome=detail.get('status',success) if isinstance(detail,dict) else success
        except (FetchError,FileNotFoundError,ImportError) as exc:
            outcome='blocked';detail={'reason':str(exc) if isinstance(exc,(FetchError,ImportError)) else 'Required private source snapshot is missing'}
        except Exception as exc:
            outcome='failed';detail={'reason':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
        check={'component':name,'status':outcome,'detail':detail,'checked_at':now()}
        checks.append(check)
        with db:db.execute('INSERT INTO source_checks(run_id,component,status,checked_at,detail_json) VALUES(?,?,?,?,?)',(rid,name,outcome,check['checked_at'],encoded(detail)))
        return outcome
    google_synced=False
    if live and config.get('GOOGLE_READ_SYNC'):
        def google_read():
            from .google import sync
            return services.get('google',sync)(imports)
        google_synced=stage('google_read_sync',google_read)=='checked'
    if live:
        def catalog_live():
            from .live_catalog import CatalogCrawler
            payload=services.get('catalog',lambda:CatalogCrawler().run(config.get('CATALOG_ADAPTER','auto')))()
            changes=import_catalog(db,payload)
            save_json(imports/'catalog.json',payload)
            return {'changes':len(changes),'products':len(payload['products']),'scope':payload.get('scope'),'freshness':'live'}
        stage('catalog',catalog_live)
        def supplier_live():
            payload=services.get('supplier',fetch_public_sheet)()
            changes=compare_supplier(db,payload);save_json(imports/'sheet.json',payload)
            return {'changes':len(changes),'freshness':'live','attribution':'supplier_only'}
        stage('supplier',supplier_live)
        def freedom_live():
            from .freedom import FreedomClient,browser_lookup
            service=services.get('freedom')
            if service:return service(db)
            renderer=browser_lookup if config.get('FREEDOM_RENDERED_LOOKUP') else None
            return FreedomClient(rendered=renderer).collect(db,config.get('FREEDOM_CODES',[]),config.get('FREEDOM_EXPECTATIONS',{}))
        stage('freedom',freedom_live)
        def research_live():
            ids=config.get('RESEARCH_PRODUCT_IDS',[])
            if not ids:return {'status':'not_configured','reason':'Choose verified identities for bounded literature discovery; no medical claims will be approved automatically'}
            results=[]
            for pid in ids:results.append(services.get('research',discover)(db,pid))
            return {'results':results,'status':'candidates_pending_review'}
        stage('literature',research_live)
    else:
        def replay_catalog():
            payload=json.loads((imports/'catalog.json').read_text());changes=import_catalog(db,payload)
            return {'changes':len(changes),'freshness':'saved_snapshot_only'}
        stage('catalog',replay_catalog,'replayed')
        stage('supplier',lambda:{'changes':len(compare_supplier(db,json.loads((imports/'sheet.json').read_text()))),'freshness':'saved_snapshot_only'},'replayed')
        checks.extend([{'component':'freedom','status':'not_attempted','detail':{'reason':'Offline run: laboratory lookup not attempted'},'checked_at':now()},{'component':'literature','status':'not_attempted','detail':{'reason':'Offline run: no live literature search'},'checked_at':now()}])
        with db:
            for c in checks[-2:]:db.execute('INSERT INTO source_checks(run_id,component,status,checked_at,detail_json) VALUES(?,?,?,?,?)',(rid,c['component'],c['status'],c['checked_at'],encoded(c['detail'])))
    # Saved guide/folder imports support local comparison; never pretend these were freshly fetched.
    for name,func in [('guide',import_guide),('folder',import_folder)]:
        stage(name,lambda n=name,f=func:{'source':f(db,json.loads((imports/(n+'.json')).read_text())),'freshness':'live_read_only_api' if google_synced else 'saved_snapshot_only'},'checked' if google_synced else 'replayed')
    with db:initialize_coverage(db)
    after={t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in before}
    delta={t:after[t]-before[t] for t in before}
    failures=[c for c in checks if c['status'] in ('failed','blocked','partial','not_configured')]
    change_details={}
    for table in ('catalog_changes','supplier_changes'):
        records=[dict(row) for row in db.execute(f'SELECT * FROM {table} WHERE rowid>? ORDER BY rowid LIMIT 50',(starting_rows[table],))]
        for record in records:
            for field in ('before_json','after_json'):
                record[field.replace('_json','')]=json.loads(record.pop(field))
        change_details[table]=records
    open_flags=db.execute("SELECT count(*) FROM flags WHERE status='open'").fetchone()[0]
    result={'run_id':rid,'started_at':started,'finished_at':now(),'mode':'live_attempt' if live else 'offline_replay','status':'partial' if failures else ('completed_checks' if live else 'offline_validation'), 'changes':delta,'change_details':change_details,'change_detail_limit':50,'open_review_flags':open_flags,'checks':checks,'google_doc':'unchanged_read_only_run','medical_claims':'never_automatically_approved','automation':schedule_status(),'limitation':'No COA establishes sterility, clinical safety, efficacy or safe peptide combinations.'}
    with db:
        db.execute('UPDATE runs SET finished_at=?,status=?,summary_json=? WHERE id=?',(result['finished_at'],result['status'],encoded(result),rid));audit(db,'daily_check',rid,{'status':result['status'],'mode':result['mode']})
    reports=root/'reports';save_json(reports/(rid+'.json'),result);save_json(reports/'latest.json',result)
    md=['# Daily reference check',f"Mode: {result['mode']}. Result: {result['status']}.",'Original Google Doc: unchanged. Medical claims: held for review.','Counts cover completed checks only; unavailable sources may contain unseen changes.','',f"Catalog changes: {delta['catalog_changes']}; supplier changes: {delta['supplier_changes']}; new literature candidates: {delta['research_candidates']}; new review flags: {delta['flags']}.",'']
    md += [f"- {c['component']}: {c['status']}" for c in checks]
    md += ['',f'Open review flags: {open_flags}.']
    for table,records in change_details.items():
        for record in records[:10]:
            item=record['after'] or record['before'] or {}
            label=str(item.get('name') or record.get('product_key') or record.get('item_key'))[:140].replace('\n',' ')
            md.append(f"- {label}: {record['kind']}")
    for table,label,column in [('catalog_changes','Catalog','kind'),('supplier_changes','Supplier','kind'),('flags','Review flag','code')]:
        rows=db.execute(f'SELECT {column},count(*) FROM {table} WHERE rowid>? GROUP BY {column}',(starting_rows[table],)).fetchall()
        md += [f'- {label}: {r[0]} ({r[1]})' for r in rows]
    md+=['','Daily automation has not been confirmed.' if not result['automation']['daily_schedule_confirmed'] else result['automation']['detail'],'',result['limitation']]
    (reports/'latest.md').write_text('\n'.join(md))
    (root/'reference-guide.md').write_text(render(db))
    with db:coverage=coverage_report(db)
    save_json(root/'research-coverage.json',coverage)
    return result
