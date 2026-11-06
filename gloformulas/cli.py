import argparse,json,logging
from pathlib import Path
from .core import connect,encoded,key,now,audit,source
from .importers import import_guide,import_sheet,import_folder
from .catalog import import_catalog
from .verification import record_report
from .guide import render,make_plan,approve
IMPORTERS={'guide':import_guide,'sheet':import_sheet,'folder':import_folder,'catalog':import_catalog}

def status(db):
 return {t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['products','vial_sizes','batches','lab_results','evidence','sources','catalog_snapshots','catalog_changes','flags']}

def check(db,directory,report_path):
 rid=key(now());before=status(db)
 db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid,now(),None,'running',None));db.commit()
 try:
  with db:
   root=Path(directory)
   for name in ('guide','sheet','folder'):IMPORTERS[name](db,json.loads((root/(name+'.json')).read_text()))
   if (root/'catalog.json').exists():import_catalog(db,json.loads((root/'catalog.json').read_text()))
   counts=status(db)
   result={'run_id':rid,'status':'completed','counts':counts,'added':{k:v-before[k] for k,v in counts.items()},'catalog_status':'compared' if (root/'catalog.json').exists() else 'blocked_no_catalog_snapshot','mode':'local_snapshots_only','medical_publication':'disabled'}
   db.execute('UPDATE runs SET finished_at=?,status=?,summary_json=? WHERE id=?',(now(),'completed',encoded(result),rid))
  dest=Path(report_path);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(encoded(result));return result
 except Exception as exc:
  with db:db.execute('UPDATE runs SET finished_at=?,status=?,summary_json=? WHERE id=?',(now(),'failed',encoded({'error_type':type(exc).__name__}),rid))
  raise

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',default='work/reference.sqlite');s=p.add_subparsers(dest='command',required=True)
 s.add_parser('init');s.add_parser('status')
 q=s.add_parser('import');q.add_argument('kind',choices=IMPORTERS);q.add_argument('file')
 q=s.add_parser('verify');q.add_argument('file');q.add_argument('--url',required=True)
 q=s.add_parser('guide');q.add_argument('output')
 q=s.add_parser('docs-plan');q.add_argument('document');q.add_argument('output')
 q=s.add_parser('publish-plan');q.add_argument('file')
 q=s.add_parser('google-sync');q.add_argument('--directory',default='work/imports')
 q=s.add_parser('check');q.add_argument('--imports',default='work/imports');q.add_argument('--report',default='work/change-report.json')
 q=s.add_parser('approve');q.add_argument('evidence_id');q.add_argument('--reviewer',required=True);q.add_argument('--quality',required=True);q.add_argument('--publication-date',required=True)
 q=s.add_parser('add-evidence');q.add_argument('file')
 q=s.add_parser('daily');q.add_argument('--state',default='work');q.add_argument('--live',action='store_true')
 q=s.add_parser('catalog-live');q.add_argument('--adapter',default='auto',choices=['auto','sitemap','woocommerce','shopify'])
 q=s.add_parser('freedom-live');q.add_argument('--code',action='append',default=[]);q.add_argument('--rendered',action='store_true')
 q=s.add_parser('supplier-compare');q.add_argument('file')
 q=s.add_parser('supplier-fetch')
 q=s.add_parser('research-discover');q.add_argument('product_id');q.add_argument('--limit',type=int,default=100)
 q=s.add_parser('research-import');q.add_argument('product_id');q.add_argument('file')
 q=s.add_parser('evidence-context');q.add_argument('file')
 q=s.add_parser('interaction-add');q.add_argument('file')
 q=s.add_parser('interaction-review');q.add_argument('interaction_id');q.add_argument('--reviewer',required=True)
 s.add_parser('research-status');s.add_parser('schedule-status')
 args=p.parse_args();logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s');db=connect(args.db)
 try:
  with db:
   c=args.command
   if c=='daily':
    from .daily import daily
    result=daily(db,args.state,args.live)
   elif c=='catalog-live':
    from .live_catalog import CatalogCrawler
    payload=CatalogCrawler().run(args.adapter);changes=import_catalog(db,payload);result={'products':len(payload['products']),'changes':len(changes),'scope':payload['scope']}
   elif c=='freedom-live':
    from .freedom import FreedomClient,browser_lookup
    result=FreedomClient(rendered=browser_lookup if args.rendered else None).collect(db,args.code)
   elif c=='supplier-compare':
    from .supplier import compare_supplier
    result={'changes':compare_supplier(db,json.loads(Path(args.file).read_text()))}
   elif c=='supplier-fetch':
    from .supplier import compare_supplier,fetch_public_sheet
    result={'changes':compare_supplier(db,fetch_public_sheet())}
   elif c=='research-discover':
    from .research import discover
    result=discover(db,args.product_id,max_results=args.limit)
   elif c=='research-import':
    from .research import ingest_articles
    result=ingest_articles(db,args.product_id,json.loads(Path(args.file).read_text()))
   elif c=='evidence-context':
    from .research import set_context
    set_context(db,json.loads(Path(args.file).read_text()));result={'context':'saved; approval reset to pending'}
   elif c=='interaction-add':
    from .research import add_interaction
    result={'interaction_id':add_interaction(db,json.loads(Path(args.file).read_text()))}
   elif c=='interaction-review':
    from .research import approve_interaction
    approve_interaction(db,args.interaction_id,args.reviewer);result={'reviewed':args.interaction_id}
   elif c=='research-status':
    from .research import coverage_report
    result=coverage_report(db)
   elif c=='schedule-status':
    from .daily import schedule_status
    result=schedule_status()
   elif c=='import':result={'source':IMPORTERS[args.kind](db,json.loads(Path(args.file).read_text()))}
   elif c=='verify':result=record_report(db,json.loads(Path(args.file).read_text()),args.url)
   elif c=='guide':Path(args.output).write_text(render(db));result={'draft':args.output}
   elif c=='docs-plan':
    plan=make_plan(db,json.loads(Path(args.document).read_text()));Path(args.output).write_text(encoded(plan));result={'requests':len(plan['requests'])}
   elif c=='publish-plan':
    from .google import publish
    result=publish(db,json.loads(Path(args.file).read_text()));audit(db,'publish_guide','original',result)
   elif c=='google-sync':
    from .google import sync
    result=sync(args.directory)
   elif c=='approve':approve(db,args.evidence_id,args.reviewer,args.quality,args.publication_date);result={'approved':args.evidence_id}
   elif c=='add-evidence':
    e=json.loads(Path(args.file).read_text());sid=source(db,'medical_primary_source',e['url'],e);eid=key(e['product_id'],e['category'],e['claim'],sid)
    db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)',(eid,e['product_id'],e['category'],e['claim'],sid,e.get('publication_date'),'unassessed','pending',None,None));result={'evidence_id':eid}
   elif c=='check':result=check(db,args.imports,args.report)
   else:result=status(db)
  print(json.dumps(result,ensure_ascii=False,indent=2))
  if isinstance(result,dict) and result.get("status") in ("partial","blocked","failed"):raise SystemExit(2)
 except Exception as exc:
  logging.error('%s: %s',type(exc).__name__,exc);raise SystemExit(1)
 finally:db.close()
if __name__=='__main__':main()
