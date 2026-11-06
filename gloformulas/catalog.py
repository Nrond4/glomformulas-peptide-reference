"""Only compare validated, complete catalog snapshots; absence is a candidate."""
import json
from .core import encoded,key,now,source,flag,audit


def validate(payload):
    if payload.get('complete') is not True: raise ValueError('Incomplete catalog cannot become a comparison baseline')
    items=payload.get('products')
    if not isinstance(items,list) or not items: raise ValueError('Empty/malformed catalog requires review, never mass discontinuation')
    seen=set()
    for p in items:
        if not all(p.get(x) for x in ('key','name','url')): raise ValueError('Catalog products require key, name, url')
        if p['key'] in seen: raise ValueError('Duplicate catalog key')
        seen.add(p['key'])
        if not isinstance(p.get('sizes'),list): raise ValueError('sizes must be a list (possibly empty)')
    return {p['key']:p for p in items}


def compare(before, after):
    old,new=validate(before),validate(after)
    changes=[]
    for k in sorted(old.keys()|new.keys()):
        if k not in old: changes.append((k,'new_product',None,new[k]))
        elif k not in new: changes.append((k,'discontinued_candidate',old[k],None))
        else:
            if sorted(old[k]['sizes'])!=sorted(new[k]['sizes']): changes.append((k,'size_change',old[k],new[k]))
            a={x:y for x,y in old[k].items() if x!='sizes'}
            b={x:y for x,y in new[k].items() if x!='sizes'}
            if a!=b: changes.append((k,'listing_update',old[k],new[k]))
    return changes


def import_catalog(db,payload,url='https://gloformulas.com'):
    validate(payload)
    sid=source(db,'catalog',url,payload)
    snapshot_id=key('snapshot',sid)
    if db.execute('SELECT 1 FROM catalog_snapshots WHERE id=?',(snapshot_id,)).fetchone(): return []
    previous=db.execute('SELECT raw_json FROM catalog_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
    changes=compare(json.loads(previous['raw_json']),payload) if previous else []
    db.execute('INSERT INTO catalog_snapshots VALUES(?,?,?,?,?)',(snapshot_id,sid,now(),1,encoded(payload)))
    for k,kind,before,after in changes:
        cid=key(snapshot_id,k,kind)
        db.execute('INSERT INTO catalog_changes VALUES(?,?,?,?,?,?)',(cid,snapshot_id,k,kind,encoded(before),encoded(after)))
        if kind=='discontinued_candidate': flag(db,'catalog_change',cid,kind,'Absent from complete snapshot; confirm discontinuation manually.')
    audit(db,'catalog_comparison',snapshot_id,{'baseline':previous is None,'changes':len(changes)})
    return changes
