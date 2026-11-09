"""Stable supplier report identity and snapshot differences; no vendor promotion."""
import csv
import io
import json
import re
from urllib.parse import urlparse
from .core import source,key,now,encoded,flag,audit
from .config import SHEET_URL
from .http import Client,FetchError


def normalize(payload):
    values=payload.get('values',[])
    header=next((i for i,r in enumerate(values) if r and r[0].strip()=='Name'),None)
    if header is None:raise ValueError('Supplier header missing; cannot compare')
    rows={}
    for row in values[header+1:]:
        if not row or not str(row[0]).strip():continue
        name=str(row[0]).strip();batch=str(row[1]).strip() if len(row)>1 else ''
        links=sorted({v.strip() for v in row[2:] if isinstance(v,str) and v.strip().startswith('https://')})
        if not links:continue
        ident=key(name.casefold(),batch)
        item={'name':name,'batch':batch,'links':links}
        if ident in rows and rows[ident]!=item:raise ValueError('Conflicting duplicate supplier name/batch rows')
        rows[ident]=item
    if not rows:raise ValueError('Empty supplier report list; previous baseline retained')
    return rows


def compare_supplier(db,payload):
    rows=normalize(payload);sid=source(db,'supplier_sheet',SHEET_URL,payload)
    prior=db.execute('SELECT id,source_id,raw_json FROM supplier_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
    if prior and prior['source_id']==sid:return []
    snap=key('supplier-snapshot',sid,prior['id'] if prior else None)
    old=json.loads(prior['raw_json']) if prior else {};changes=[]
    for ident in sorted(old.keys()|rows.keys()):
        before,after=old.get(ident),rows.get(ident)
        kind='new_batch_or_product' if before is None else ('removed_supplier_row' if after is None else 'report_links_changed')
        if before!=after and prior:changes.append({'key':ident,'kind':kind,'before':before,'after':after})
    db.execute('INSERT INTO supplier_snapshots VALUES(?,?,?,?)',(snap,sid,now(),encoded(rows)))
    for change in changes:
        db.execute('INSERT OR IGNORE INTO supplier_changes VALUES(?,?,?,?,?,?)',(key(snap,change['key'],change['kind']),snap,change['key'],change['kind'],encoded(change['before']),encoded(change['after'])))
    for ident,row in rows.items():
        # Only exact batch matches are suggested. They still cannot establish vendor linkage.
        matches=db.execute('SELECT DISTINCT product_id FROM batches WHERE lot=? AND product_id IS NOT NULL',(row['batch'],)).fetchall() if row['batch'] else []
        pid=matches[0]['product_id'] if len(matches)==1 else None
        match_status='exact_lot_candidate' if pid else ('ambiguous_lot' if len(matches)>1 else 'unmatched')
        for link in row['links']:
            lid=key('supplier-link',ident,link)
            db.execute('INSERT OR IGNORE INTO supplier_links VALUES(?,?,?,?,?,?,?)',(lid,sid,row['name'],row['batch'],link,pid,match_status))
            # Reuse historical report-link intake; do not create copies each time the sheet changes.
            existing=db.execute("SELECT l.id FROM lab_results l JOIN sources s ON s.id=l.source_id WHERE s.url=? AND l.attribution='supplier'",(link,)).fetchone()
            if not existing:
                ls=source(db,'supplier_report_link',link,row)
                db.execute('INSERT OR IGNORE INTO lab_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key('supplier-report',link),None,None,None,ls,'Janoshik',None,None,None,None,None,'supplier','pending',encoded(row)))
            if pid:flag(db,'supplier_link',lid,'supplier_lot_candidate_only','Same printed lot found in guide; supplier test does not verify GloFormulas vial identity, provenance or safety.')
    audit(db,'compare_supplier',snap,{'baseline':prior is None,'changes':len(changes),'rows':len(rows)})
    return changes


def fetch_public_sheet(client=None):
    """Works without credentials only when the owner already permits public export."""
    from .config import config
    sheet_id=SHEET_URL.split('/d/')[1].split('/')[0]
    if sheet_id.startswith('CONFIGURE'):raise ValueError('Supplier spreadsheet URL has not been configured')
    target=f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid='+str(config.get('SHEET_GID',0))
    client=client or Client({'docs.google.com','drive.google.com','googleusercontent.com'},subdomain_roots={'googleusercontent.com'})
    response=client.get(target)
    if 'text/html' in response.headers.get('content-type','').lower() or '<html' in response.text[:1024].lower() or '<!doctype html' in response.text[:1024].lower():raise FetchError('Supplier export is not public CSV; use connected snapshot or read-only Google authorization')
    payload={'values':list(csv.reader(io.StringIO(response.text)))}
    normalize(payload)
    return payload
