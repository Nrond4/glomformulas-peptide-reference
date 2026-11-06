import json
from .core import audit,now
from .importers import CATEGORIES,tabs,text
LIMITATION='COA results do not establish sterility, clinical safety, efficacy, or combination compatibility.'

def approved(db,pid,category):
 return db.execute("SELECT e.*,s.url,s.kind,c.population,c.route,c.species,c.limitations FROM evidence e JOIN sources s ON s.id=e.source_id JOIN evidence_context c ON c.evidence_id=e.id LEFT JOIN research_candidates r ON r.id=c.source_candidate_id WHERE e.product_id=? AND e.category=? AND e.review_status='approved' AND e.reviewer IS NOT NULL AND publication_date IS NOT NULL AND quality<>'unassessed' AND s.kind='medical_primary_source' AND date(c.review_due)>=date('now') AND coalesce(r.retracted,0)=0 ORDER BY e.id",(pid,category)).fetchall()

def approve(db,eid,reviewer,quality,publication_date):
 from datetime import date
 if date.fromisoformat(publication_date)>date.today():raise ValueError('Publication date is in the future')
 from .research import reviewable
 reviewable(db,eid)
 row=db.execute('SELECT e.id,s.kind FROM evidence e JOIN sources s ON s.id=e.source_id WHERE e.id=?',(eid,)).fetchone()
 if not row or row['kind']!='medical_primary_source':raise ValueError('Primary-source evidence required; inherited guide claims cannot be approved')
 if not reviewer.strip() or quality not in ('label','systematic_review','human_trial','observational','preclinical','other'):raise ValueError('Named reviewer and assessed evidence quality required')
 db.execute("UPDATE evidence SET review_status='approved',reviewer=?,reviewed_at=?,quality=?,publication_date=? WHERE id=?",(reviewer,now(),quality,publication_date,eid))
 audit(db,'approve_evidence',eid,{'reviewer':reviewer,'quality':quality})

def render(db):
 lines=['# PEPTIDE REFERENCE GUIDE','GloFormulas Codenames — Review Draft',LIMITATION,'']
 for p in db.execute('SELECT * FROM products ORDER BY rowid'):
  lines += [f"## {p['name']} (GloFormulas: “{p['codename']}”)",f"Identity status: {p['identity_status']}"]
  for prefix,category in CATEGORIES.items():
   claims=approved(db,p['id'],category)
   lines.append(prefix+' '+(' '.join(f"{r['claim']} [Source]({r['url']}) ({r['publication_date']}; {r['quality']}; {r['species']}; {r['population']}; {r['route']}; limitation: {r['limitations']}; reviewed by {r['reviewer']})" for r in claims) if claims else 'No reviewed source-backed claim available.'))
  from .research import interaction_report
  for interaction in interaction_report(db,p['id']):
   lines.append(f"Documented co-use record: {interaction['other_agent']} — {interaction['relationship']}; {interaction['population']}; {interaction['route']}. {interaction['detail']}")
  lines+=['','### Available Sizes & Certificate of Analysis Data','']
  for r in db.execute('SELECT l.*,v.label FROM lab_results l LEFT JOIN vial_sizes v ON v.id=l.size_id WHERE l.product_id=?',(p['id'],)):
   lines.append(f"- {r['label'] or 'Size unknown'} — {r['lab'] or 'Lab unknown'}; {r['attribution']}; {r['verification_status']}")
   lines.append('  '+'; '.join(f'{k}: {v}' for k,v in json.loads(r['raw_json']).items() if not k.startswith('_')))
  lines.append('')
 return '\n'.join(lines)

def make_plan(db,doc):
 changes=[]
 for tid,blocks in tabs(doc):
  pid=None
  for block in blocks:
   value=text(block)
   if '(GloFormulas:' in value:
    found=db.execute('SELECT id FROM products WHERE raw_heading=?',(value.strip(),)).fetchone();pid=found['id'] if found else None
   if not pid:continue
   for prefix,category in CATEGORIES.items():
    if not value.startswith(prefix):continue
    claims=approved(db,pid,category)
    if not claims:continue
    replacement=prefix+' '+' '.join(f"{r['claim']} [{r['url']}; {r['publication_date']}; {r['quality']}; {r['species']}; {r['population']}; {r['route']}; limitation: {r['limitations']}]" for r in claims)
    if value.rstrip('\n')==replacement:continue
    runs=[e['textRun'] for e in block['paragraph']['elements'] if e.get('textRun',{}).get('content','').strip()]
    if len(runs)!=1:raise ValueError('Mixed-style paragraph requires manual formatting review')
    changes.append((block['startIndex'],tid,block['endIndex']-1,replacement,runs[0].get('textStyle',{})))
 requests=[]
 for start,tid,end,replacement,style in sorted(changes,reverse=True):
  scope={'tabId':tid} if tid!='legacy' else {};length=len(replacement.encode('utf-16-le'))//2
  requests += [{'deleteContentRange':{'range':dict(scope,startIndex=start,endIndex=end)}},{'insertText':{'location':dict(scope,index=start),'text':replacement}},{'updateTextStyle':{'range':dict(scope,startIndex=start,endIndex=start+length),'textStyle':style,'fields':'*'}}]
 return {'document_id':doc.get('documentId'),'revision_id':doc.get('revisionId'),'requests':requests}
