"""Literature discovery and claim/interaction review. Discovery never approves claims."""
import json
from datetime import date,timedelta
from urllib.parse import urlencode
from .core import source,key,now,encoded,audit,flag
from .http import Client

CATEGORIES=('uses','concurrent_use','precautions','insufficient_evidence')
ENDPOINT='https://www.ebi.ac.uk/europepmc/webservices/rest/search'


def initialize_coverage(db):
    for p in db.execute('SELECT id,identity_status FROM products').fetchall():
        for category in CATEGORIES:
            detail='Identity unresolved; do not infer medical identity from codename' if p['identity_status']=='unresolved' else 'No completed source review; absence of reviewed evidence is not proof of safety'
            db.execute('INSERT OR IGNORE INTO research_coverage VALUES(?,?,?,?,?)',(p['id'],category,'not_reviewed',None,detail))


def study_type(article):
    types=' '.join(article.get('pubTypeList',{}).get('pubType',[])).lower()
    if 'systematic review' in types or 'meta-analysis' in types:return 'systematic_review'
    if 'clinical trial' in types or 'randomized controlled trial' in types:return 'human_trial_candidate'
    return 'unassessed'


def ingest_articles(db,pid,payload):
    if not db.execute("SELECT 1 FROM products WHERE id=? AND identity_status<>'unresolved'",(pid,)).fetchone():raise ValueError('Research requires a known product identity')
    articles=payload.get('resultList',{}).get('result')
    if not isinstance(articles,list):raise ValueError('Unrecognized Europe PMC result structure')
    added=0
    for article in articles:
        if not article.get('source'):raise ValueError('Research result missing source namespace')
        ext=str(article['source'])+':'+str(article.get('id',''))
        if not article.get('id') or not article.get('title'):raise ValueError('Research result missing identifier/title')
        url='https://europepmc.org/article/'+str(article.get('source','MED'))+'/'+str(article['id'])
        sid=source(db,'literature_metadata',url,article,article.get('firstPublicationDate'))
        cid=key(pid,ext)
        retracted=str(article.get('isRetracted','')).upper() in ('Y','TRUE','1') or 'retracted publication' in ' '.join(article.get('pubTypeList',{}).get('pubType',[])).lower()
        exists=db.execute('SELECT source_id,retracted FROM research_candidates WHERE id=?',(cid,)).fetchone()
        retracted=retracted or bool(exists and exists['retracted'])
        db.execute('INSERT INTO research_candidates VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id,title=excluded.title,published_at=excluded.published_at,study_type=excluded.study_type,retracted=excluded.retracted',
                   (cid,pid,sid,ext,article['title'],article.get('firstPublicationDate'),study_type(article),int(retracted),'pending'))
        if not exists:added+=1
        if exists and exists['source_id']!=sid:
            db.execute("UPDATE evidence SET review_status='pending' WHERE id IN (SELECT evidence_id FROM evidence_context WHERE source_candidate_id=?)",(cid,))
            flag(db,'research_candidate',cid,'source_updated','Source metadata changed; linked claims require a fresh review.')
        if retracted:
            db.execute("UPDATE evidence SET review_status='pending' WHERE id IN (SELECT evidence_id FROM evidence_context WHERE source_candidate_id=?)",(cid,))
            db.execute("UPDATE interactions SET review_status='pending' WHERE evidence_id IN (SELECT evidence_id FROM evidence_context WHERE source_candidate_id=?)",(cid,))
            flag(db,'research_candidate',cid,'retracted_source','Source marked retracted; any linked claims/interactions returned to pending.')
    initialize_coverage(db)
    for category in CATEGORIES:
        db.execute('UPDATE research_coverage SET checked_at=?,detail=? WHERE product_id=? AND category=?',(now(),'Literature candidates collected; human review and applicability assessment still required',pid,category))
    audit(db,'research_discovery',pid,{'candidate_count':len(articles),'added':added})
    return {'candidates':len(articles),'added':added,'review_status':'pending'}


def discover(db,pid,client=None,max_results=100):
    p=db.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
    if not p or p['identity_status']=='unresolved':raise ValueError('Resolve product identity before literature search')
    if not 1<=max_results<=500:raise ValueError('Research result cap must be 1..500')
    name=p['name'].replace('"','')
    query=f'TITLE_ABS:"{name}" sort_date:y'
    client=client or Client({'www.ebi.ac.uk'})
    cursor='*';articles=[];total=None;seen=set()
    while len(articles)<max_results:
        target=ENDPOINT+'?'+urlencode({'query':query,'format':'json','resultType':'core','pageSize':min(100,max_results-len(articles)),'cursorMark':cursor})
        data=client.get(target).json();total=data.get('hitCount')
        rows=data.get('resultList',{}).get('result')
        if not isinstance(rows,list):raise ValueError('Literature endpoint returned an unreadable result')
        articles.extend(rows)
        nxt=data.get('nextCursorMark')
        if not rows or not nxt or nxt==cursor:break
        if nxt in seen:raise ValueError('Literature cursor repeated')
        seen.add(cursor);cursor=nxt
    result=ingest_articles(db,pid,{'resultList':{'result':articles}})
    result.update({'query':query,'total_hits':total,'coverage':'bounded_discovery_not_complete_review','limit':max_results})
    audit(db,'research_search_coverage',pid,result)
    return result


def set_context(db,payload):
    required=('evidence_id','population','route','species','outcome','locator','limitations','review_due')
    if any(not str(payload.get(x,'')).strip() for x in required):raise ValueError('Evidence needs population, route, species, outcome, source locator, limitations and review date')
    date.fromisoformat(payload['review_due'])
    candidate=payload.get('source_candidate_id')
    row=db.execute('SELECT product_id FROM evidence WHERE id=?',(payload['evidence_id'],)).fetchone()
    if not row:raise ValueError('Unknown evidence')
    if candidate:
        c=db.execute('SELECT product_id FROM research_candidates WHERE id=?',(candidate,)).fetchone()
        if not c or c['product_id']!=row['product_id']:raise ValueError('Candidate belongs to a different product')
    db.execute('INSERT INTO evidence_context VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET population=excluded.population,route=excluded.route,species=excluded.species,outcome=excluded.outcome,locator=excluded.locator,limitations=excluded.limitations,review_due=excluded.review_due,source_candidate_id=excluded.source_candidate_id',tuple(payload[x] for x in required)+(candidate,))
    # Changing applicability invalidates any previous approval.
    db.execute("UPDATE evidence SET review_status='pending',reviewer=NULL,reviewed_at=NULL WHERE id=?",(payload['evidence_id'],))
    db.execute("UPDATE interactions SET review_status='pending',reviewer=NULL,reviewed_at=NULL WHERE evidence_id=?",(payload['evidence_id'],))
    audit(db,'set_evidence_context',payload['evidence_id'],payload)


def reviewable(db,eid):
    row=db.execute('SELECT c.*,r.retracted FROM evidence_context c LEFT JOIN research_candidates r ON r.id=c.source_candidate_id WHERE c.evidence_id=?',(eid,)).fetchone()
    if not row:raise ValueError('Evidence applicability/source locator and review date are required')
    if date.fromisoformat(row['review_due'])<date.today():raise ValueError('Evidence review has expired')
    if row['retracted']:raise ValueError('Retracted sources cannot support an approved claim')
    return row


def add_interaction(db,payload):
    required=('product_id','other_agent','relationship','population','route','detail')
    if any(not str(payload.get(x,'')).strip() for x in required):raise ValueError('Interaction context fields are required')
    if payload['relationship'] not in ('documented_interaction','potential_precaution','insufficient_evidence'):raise ValueError('Interactions cannot be labeled safe or proven compatible')
    evidence_id=payload.get('evidence_id')
    if evidence_id:
        e=db.execute('SELECT product_id,category FROM evidence WHERE id=?',(evidence_id,)).fetchone()
        if not e or e['product_id']!=payload['product_id'] or e['category'] not in ('concurrent_use','precautions','insufficient_evidence'):raise ValueError('Interaction must link relevant evidence for this product')
    iid=key(*(payload[x] for x in required),evidence_id)
    db.execute('INSERT OR IGNORE INTO interactions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(iid,payload['product_id'],payload['other_agent'],evidence_id,payload['relationship'],payload['population'],payload['route'],'pending',None,None,payload['detail']))
    audit(db,'add_interaction',iid,payload)
    return iid


def approve_interaction(db,iid,reviewer):
    if not reviewer.strip():raise ValueError('Named reviewer required')
    row=db.execute('SELECT i.*,e.review_status AS claim_status FROM interactions i LEFT JOIN evidence e ON e.id=i.evidence_id WHERE i.id=?',(iid,)).fetchone()
    if not row or row['claim_status']!='approved':raise ValueError('An approved source-backed claim is required')
    reviewable(db,row['evidence_id'])
    db.execute("UPDATE interactions SET review_status='approved',reviewer=?,reviewed_at=? WHERE id=?",(reviewer,now(),iid))
    audit(db,'approve_interaction',iid,{'reviewer':reviewer})


def coverage_report(db):
    from .guide import approved
    initialize_coverage(db)
    for product in db.execute('SELECT id,identity_status FROM products').fetchall():
        for category in CATEGORIES:
            if product['identity_status']=='unresolved':
                status='identity_unresolved'
            elif approved(db,product['id'],category):
                status='reviewed_claims_available'
            else:
                status='review_required'
            db.execute('UPDATE research_coverage SET status=? WHERE product_id=? AND category=?',(status,product['id'],category))
    return [dict(r) for r in db.execute("SELECT p.name,p.codename,c.category,c.status,c.checked_at,c.detail,(SELECT count(*) FROM research_candidates r WHERE r.product_id=p.id AND r.status='pending') AS candidates FROM research_coverage c JOIN products p ON p.id=c.product_id ORDER BY p.rowid,c.category")]


def interaction_report(db,pid):
    records=[]
    for row in db.execute("SELECT i.* FROM interactions i JOIN evidence e ON e.id=i.evidence_id WHERE i.product_id=? AND i.review_status='approved' AND e.review_status='approved'",(pid,)):
        try:reviewable(db,row['evidence_id'])
        except ValueError:continue
        records.append(dict(row))
    return records
