"""Fail-closed factual publishing. Existing guide content is never regenerated."""
import copy
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from .core import encoded, key, now
from .importers import tabs, text

START = 'GLOFORMULAS VERIFIED UPDATES — START\n'
END = 'GLOFORMULAS VERIFIED UPDATES — END\n'
VERSION = 'docs-publisher-1'
TITLE = 'Verified product and COA updates\n'
NOTICE = ('Catalog names describe listings, not independently confirmed peptide identities. '
          'COAs do not establish sterility, clinical safety, efficacy or safe combinations.\n')

class PublishError(RuntimeError):
    pass

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS publish_entries(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publish_reviews(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload_json TEXT NOT NULL,
      digest TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',reviewer TEXT,reviewed_at TEXT);
    CREATE TABLE IF NOT EXISTS publish_history(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,mode TEXT NOT NULL,
      digest TEXT NOT NULL,revision TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,detail_json TEXT NOT NULL);
    ''')

def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()

def short(value):
    value=str(value)
    if not value or len(value)>500 or any(ord(c)<32 for c in value) or START.strip() in value or END.strip() in value:
        raise PublishError('Invalid factual field')
    return value

def https(url):
    from urllib.parse import urlparse
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or any(ord(c)<33 for c in url):
        raise PublishError('HTTPS source required')
    return url

def enqueue(db,kind,payload):
    if kind not in ('removal','medical_claim','interaction','uncertain_coa'):
        raise PublishError('Unsupported review kind')
    ident=key(kind,payload);body=encoded(payload)
    db.execute('INSERT OR IGNORE INTO publish_reviews(id,kind,payload_json,digest) VALUES(?,?,?,?)',
               (ident,kind,body,digest(payload)))
    return ident

def approve(db,ident,expected_digest,reviewer):
    row=db.execute('SELECT * FROM publish_reviews WHERE id=?',(ident,)).fetchone()
    if not row or row['digest']!=expected_digest or not reviewer.strip():
        raise PublishError('Exact proposal and named reviewer required')
    payload=json.loads(row['payload_json'])
    https(payload['source'])
    if row['kind']=='removal' and payload.get('explicit_discontinuation') is not True:
        raise PublishError('Absence is not discontinuation evidence')
    if row['kind']=='uncertain_coa':
        raise PublishError('Resolve identity and verify the lab record before publishing')
    if row['kind'] in ('medical_claim','interaction'):
        # Use the existing primary-source/context/review rules, never a generic approval bypass.
        from .guide import approved
        evidence=db.execute('SELECT * FROM evidence WHERE id=?',(payload.get('evidence_id'),)).fetchone()
        if not evidence or evidence['id'] not in [r['id'] for r in approved(db,evidence['product_id'],evidence['category'])]:
            raise PublishError('Current, manually approved primary-source evidence required')
        if row['kind']=='interaction':
            if not db.execute("SELECT 1 FROM interactions WHERE evidence_id=? AND review_status='approved' AND reviewer IS NOT NULL",(evidence['id'],)).fetchone():
                raise PublishError('Separate interaction review required')
    db.execute("UPDATE publish_reviews SET status='approved',reviewer=?,reviewed_at=? WHERE id=?",
               (reviewer,now(),ident))

def fresh(value):
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        return timedelta(0)<=datetime.now(timezone.utc)-date<=timedelta(hours=24)
    except (ValueError,TypeError):
        return False

def entries(db,result):
    """Positive current observations only. Keep last known facts on any source failure."""
    initialize(db)
    items={r['id']:json.loads(r['payload_json']) for r in db.execute('SELECT * FROM publish_entries')}
    checks={r['component']:r for r in result.get('checks',[])}
    if checks.get('catalog',{}).get('status')=='checked' and fresh(result.get('finished_at')):
        row=db.execute('SELECT * FROM catalog_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
        if row and row['complete'] and fresh(row['observed_at']):
            from .catalog import validate
            data=json.loads(row['raw_json']);validate(data)
            for p in data['products']:
                url=https(p['url'])
                from urllib.parse import urlparse
                if urlparse(url).hostname not in ('gloformulas.com','www.gloformulas.com'):
                    raise PublishError('Catalog publisher requires official product URLs')
                item={'kind':'catalog','name':short(p['name']),'sizes':[short(s) for s in p['sizes']],
                      'source':url,'observed':row['observed_at'][:10]}
                items['catalog:'+p['key']]=item
            # Removed products stay present until explicit discontinuation evidence is reviewed.
    existing_coas={ident for ident in items if ident.startswith('coa:')}
    items={ident:item for ident,item in items.items() if ident not in existing_coas}
    fresh_coas=checks.get('freedom',{}).get('status') in ('completed','checked') and fresh(result.get('finished_at'))
    # Revalidate prior COAs even when a live source is unavailable.
    for r in db.execute("SELECT l.*,s.url FROM lab_results l JOIN sources s ON s.id=l.source_id WHERE verification_status='authenticated' AND attribution='gloformulas'"):
        if not fresh_coas and 'coa:'+r['id'] not in existing_coas:continue
        if db.execute('SELECT count(*) FROM lab_results WHERE lab=? AND search_code=?',(r['lab'],r['search_code'])).fetchone()[0]>1:continue
        if not r['product_id'] or not r['batch_id'] or not (r['search_code'] or '').startswith('GLO'):
            continue
        if db.execute("SELECT 1 FROM flags WHERE status='open' AND entity_id IN (?,?,?,?)",(r['id'],r['product_id'],r['batch_id'],r['source_id'])).fetchone():
            continue
        raw=json.loads(r['raw_json'])
        if not raw.get('verification_artifact') or not raw.get('vendor_batch_matched') or not raw.get('lab_authenticated'):
            continue
        from .verification import assess
        if assess(raw)['flags'] or raw.get('extraction_flags'):continue
        artifact=db.execute('SELECT * FROM report_artifacts WHERE sha256=?',(raw.get('artifact_sha256'),)).fetchone()
        if not artifact or hashlib.sha256(artifact['content']).hexdigest()!=raw.get('artifact_sha256'):continue
        from urllib.parse import urlparse
        if urlparse(artifact['url']).hostname not in ('coas.freedomdiagnosticstesting.com','freedomdiagnosticstesting.com','www.freedomdiagnosticstesting.com'):continue
        primary=db.execute("SELECT * FROM sources WHERE id=? AND kind='freedom_primary_report'",(raw['verification_artifact'],)).fetchone()
        if not primary or json.loads(primary['raw_json']).get('sha256')!=raw.get('artifact_sha256'):continue
        if db.execute("SELECT 1 FROM flags WHERE status='open' AND entity_id=?",(raw['artifact_sha256'],)).fetchone():continue
        if db.execute("SELECT 1 FROM lab_fetches f JOIN flags x ON x.entity_id=f.id WHERE f.artifact_sha256=? AND x.status='open'",(raw['artifact_sha256'],)).fetchone():continue
        p=db.execute("SELECT * FROM products WHERE id=? AND identity_status='reviewed'",(r['product_id'],)).fetchone()
        if not p:continue
        items['coa:'+r['id']]={'kind':'coa','name':short(p['name']),'source':https(r['url']),
           'fields':{k:short(r[k]) for k in ('lab','report_date','search_code','identity','purity','measured_content') if r[k]}}
        for field in ('batch','content_unit','nominal_content','nominal_unit'):
            if raw.get(field) is not None:items['coa:'+r['id']]['fields'][field]=short(raw[field])
    for row in db.execute("SELECT * FROM publish_reviews WHERE status='approved'"):
        p=json.loads(row['payload_json'])
        if row['kind']=='removal' and p.get('explicit_discontinuation') is True:
            https(p['source'])
            ident='catalog:'+p['product_key']
            if ident in items:items[ident]=dict(items[ident],retired=True,retirement_source=p['source'])
        # Medical prose is intentionally kept out of this first publisher. Approval never implicitly edits legacy claims.
    return items

def render(items):
    lines=[START,TITLE,NOTICE]
    if not items:lines.append('No newly verified product facts are ready for publication. Existing guide entries remain unchanged.\n')
    for ident,p in sorted(items.items()):
        if p['kind']=='catalog':
            status=' — confirmed discontinued' if p.get('retired') else ''
            lines.append(f"Catalog listing: {short(p['name'])}{status}\n")
            lines.append('Vial sizes: '+(', '.join(p['sizes']) or 'Not specified in this listing')+'\n')
            lines.append('Observed: '+p['observed']+'; Source: '+https(p['source'])+'\n')
            if p.get('retirement_source'):lines.append('Discontinuation source: '+https(p['retirement_source'])+'\n')
        elif p['kind']=='coa':
            lines.append('Verified COA: '+short(p['name'])+'\n')
            lines.append('; '.join(k.replace('_',' ')+': '+v for k,v in p['fields'].items())+'\n')
            lines.append('Source: '+https(p['source'])+'\n')
    return ''.join(lines)+END

def utf16(value):return len(value.encode('utf-16-le'))//2

def plan(doc,content):
    if not doc.get('revisionId'):raise PublishError('Revision control required')
    found=[];alltabs=list(tabs(doc))
    for tid,blocks in alltabs:
        if any((START.strip() in text(b) and text(b)!=START) or (END.strip() in text(b) and text(b)!=END) for b in blocks):
            raise PublishError('Managed markers must be separate complete paragraphs')
        starts=[b for b in blocks if text(b)==START]
        ends=[b for b in blocks if text(b)==END]
        if starts or ends:
            if len(starts)!=1 or len(ends)!=1:raise PublishError('Managed markers missing or duplicated')
            start,end=starts[0]['startIndex'],ends[0]['endIndex']
            if end<=start:raise PublishError('Managed markers reversed')
            selected=[b for b in blocks if start<=b.get('startIndex',-1)<end]
            if any('paragraph' not in b or any('textRun' not in e for e in b['paragraph'].get('elements',[])) for b in selected):
                raise PublishError('Managed section contains unsupported native content')
            found.append((tid,start,end,''.join(text(b) for b in selected)))
    if len(found)>1:raise PublishError('Multiple managed sections')
    if found:tid,start,end,previous=found[0]
    else:
        if len(alltabs)!=1:raise PublishError('Multi-tab initialization requires a reviewed destination')
        tid,blocks=alltabs[0];start=blocks[-1]['endIndex']-1;end=start;previous=''
        if text(blocks[-1])!='\n':raise PublishError('Add a blank final paragraph before initializing')
        # Insert after the existing final paragraph newline, without inheriting its content.
        content='\n'+content
    scope={} if tid=='legacy' else {'tabId':tid}
    requests=[]
    if previous==content:return {'requests':[],'content':content,'digest':digest(content)}
    if previous:requests.append({'deleteContentRange':{'range':dict(scope,startIndex=start,endIndex=end-1)}})
    inserted=content[:-1] if previous else content
    requests.append({'insertText':{'location':dict(scope,index=start),'text':inserted}})
    stop=start+utf16(inserted)
    requests.append({'updateParagraphStyle':{'range':dict(scope,startIndex=start,endIndex=stop),
      'paragraphStyle':{'namedStyleType':'NORMAL_TEXT'},'fields':'namedStyleType'}})
    requests.append({'updateTextStyle':{'range':dict(scope,startIndex=start,endIndex=stop),
      'textStyle':{'bold':False,'italic':False,'fontSize':{'magnitude':10,'unit':'PT'}},'fields':'bold,italic,fontSize,link'}})
    title_start=start+utf16(('\n' if not previous else '')+START)
    requests.append({'updateParagraphStyle':{'range':dict(scope,startIndex=title_start,endIndex=title_start+utf16(TITLE)-1),
      'paragraphStyle':{'namedStyleType':'HEADING_1'},'fields':'namedStyleType'}})
    for match in re.finditer(r'https://[^\s]+',inserted):
        requests.append({'updateTextStyle':{'range':dict(scope,startIndex=start+utf16(inserted[:match.start()]),endIndex=start+utf16(inserted[:match.end()])),
          'textStyle':{'link':{'url':match.group()}},'fields':'link'}})
    if len(requests)>900:raise PublishError('Publication exceeds bounded batch size')
    return {'requests':requests,'content':content.lstrip('\n'),'digest':digest(content.lstrip('\n'))}

def managed(doc):
    sections=[]
    for tid,blocks in tabs(doc):
        active=False;value=''
        for b in blocks:
            t=text(b)
            if t==START:active=True
            if active:value+=t
            if t==END and active:sections.append(value);active=False
    if len(sections)!=1:raise PublishError('Cannot verify managed section')
    return sections[0]

def preserved_prefix(doc):
    result=[]
    for tid,blocks in tabs(doc):
        kept=[]
        for b in blocks:
            if text(b)==START:break
            kept.append(copy.deepcopy(b))
        # Initialization consumes only an empty terminal paragraph, never substantive content.
        while kept and text(kept[-1])=='\n':kept.pop()
        result.append((tid,kept))
    return result

def unchanged_prefix(before,after):
    """Compare all substantive native blocks preceding our section exactly."""
    return preserved_prefix(before)==preserved_prefix(after)
