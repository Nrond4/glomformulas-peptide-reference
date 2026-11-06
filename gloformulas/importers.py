"""Lossless raw snapshots first; conservative structured extraction second."""
import re
from .core import encoded, key, source, flag, audit

from .config import GUIDE_URL, SHEET_URL, FOLDER_URL
CATEGORIES = {'Research / established use:': 'uses', 'No documented adverse interactions identified when used concurrently with:': 'concurrent_use', 'Potential interactions or precautions:': 'precautions', 'Insufficient evidence:': 'insufficient_evidence'}


def text(block):
    if 'paragraph' in block:
        return ''.join(x.get('textRun', {}).get('content', '') for x in block['paragraph'].get('elements', []))
    if 'table' in block:
        return '\n'.join('\t'.join(cell_text(c) for c in r['tableCells']) for r in block['table']['tableRows'])
    return ''


def cell_text(cell):
    return ''.join(text(b) for b in cell.get('content', [])).strip()


def tabs(doc):
    # Accept both official Docs API and connector-flattened resources.
    if doc.get('body'):
        yield 'legacy', doc['body'].get('content', [])
    def descend(items):
        for tab in items:
            tid = tab.get('tabId') or tab.get('tabProperties', {}).get('tabId')
            body = tab.get('body') or tab.get('documentTab', {}).get('body', {})
            yield tid, body.get('content', [])
            yield from descend(tab.get('childTabs', []))
    yield from descend(doc.get('tabs', []))


def import_guide(db, doc):
    sid = source(db, 'guide', GUIDE_URL, doc)
    count = 0
    for tid, blocks in tabs(doc):
        pid = None
        for i, block in enumerate(blocks):
            value = text(block).strip()
            if '(GloFormulas:' in value:
                name, code = value.split('(GloFormulas:', 1)
                code = code.rsplit(')', 1)[0].strip().strip('“”"')
                pid = key('product', code)
                status = 'unresolved' if name.strip() == 'UNRESOLVED' else 'imported'
                db.execute('INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?)', (pid, name.strip(), code, status, sid, value))
                if status == 'unresolved':
                    flag(db, 'product', pid, 'unresolved_identity', 'Preserved codename; identity requires primary-source confirmation.')
                count += 1
            db.execute('INSERT OR IGNORE INTO guide_blocks VALUES(?,?,?,?,?,?,?)',
                       (key(sid, tid, i), pid, sid, tid, block.get('startIndex'), block.get('endIndex'), encoded(block)))
            if not pid:
                continue
            for prefix, category in CATEGORIES.items():
                if value.startswith(prefix):
                    claim = value[len(prefix):].strip()
                    db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (key(pid, category, claim, sid), pid, category, claim, sid, None, 'unassessed', 'pending', None, None))
            if 'table' not in block:
                continue
            rows = block['table']['tableRows']
            headers = [cell_text(c) for c in rows[0]['tableCells']]
            for n, row in enumerate(rows[1:]):
                vals = [cell_text(c) for c in row['tableCells']]
                raw = dict(zip(headers, vals))
                raw['_cells'] = vals  # preserve even irregular rows
                size = raw.get('Size', '')
                size_id = key(pid, size)
                db.execute('INSERT OR IGNORE INTO vial_sizes VALUES(?,?,?,?)', (size_id, pid, size, sid))
                rid = key(sid, tid, i, n)
                lot = raw.get('Batch/Lot')
                batch_id = key(pid, lot, sid) if lot else None
                if batch_id:
                    db.execute('INSERT OR IGNORE INTO batches VALUES(?,?,?,?)', (batch_id, pid, lot, sid))
                lab = raw.get('Lab')
                attr = 'supplier' if 'cocer' in encoded(raw).lower() else ('unverified' if lab else 'missing')
                db.execute('INSERT OR IGNORE INTO lab_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, pid, size_id, batch_id, sid, lab, None, None, None, raw.get('Purity'), raw.get('Content (Result)'), attr, 'pending', encoded(raw)))
                # Blend component quantities remain separate and never prove a combined vial's safety.
                for header in headers:
                    if header in ('BPC-157', 'TB-500', 'GHK-Cu', 'KPV', 'TB-500 (Thymosin B4)'):
                        if not db.execute('SELECT 1 FROM formulations WHERE product_id=? AND component=? AND amount=? AND source_id=?', (pid, header, raw[header], sid)).fetchone():
                            db.execute('INSERT INTO formulations(product_id,component,amount,source_id) VALUES(?,?,?,?)', (pid, header, raw[header], sid))
                flag(db, 'lab_result', rid, 'primary_report_required', 'Imported guide transcription; lab authenticity, vendor linkage, identity and batch are not verified.')
                from .verification import assess
                content_match = re.match(r'^(\d+(?:\.\d+)?)\s*mg', raw.get('Content (Result)', ''), re.I)
                nominal_match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*mg', size, re.I)
                review = assess({'lab':lab, 'purity':raw.get('Purity'),
                    'measured_content':content_match[1] if content_match else None,
                    'nominal_content':nominal_match[1] if nominal_match else None,
                    'content_unit':'mg', 'nominal_unit':'mg'})
                for code in review['flags']:
                    if code in ('unusual_purity','qualified_or_unparsed_purity','unusual_measured_content'):
                        flag(db,'lab_result',rid,code,'Review imported transcription against the primary report; threshold is not a safety specification.')
                if lot and 'cap' in lot.lower():
                    flag(db,'lab_result',rid,'lot_may_be_vial_description','Batch field contains a cap description; retain raw value pending review.')
        for product in db.execute('SELECT id FROM products WHERE source_id=?',(sid,)).fetchall():
            if not db.execute('SELECT 1 FROM lab_results WHERE product_id=?',(product['id'],)).fetchone():
                flag(db,'product',product['id'],'missing_coa','No structured product-specific COA row imported; inspect preserved narrative and primary reports.')
        audit(db, 'import_guide', sid, {'headings': count, 'revision': doc.get('revisionId')})
    return sid


def import_sheet(db, sheet):
    sid = source(db, 'supplier_sheet', SHEET_URL, sheet)
    values = sheet.get('values', [])
    header = next((i for i,r in enumerate(values) if r and r[0] == 'Name'), None)
    if header is None:
        raise ValueError('Supplier sheet header not recognized; raw snapshot preserved only if transaction commits')
    for i, row in enumerate(values[header+1:], header+2):
        if not row or not row[0]:
            continue
        links = [v for v in row[2:] if isinstance(v,str) and v.startswith('https://')]
        for j, url in enumerate(links):
            report_sid = source(db, 'supplier_report_link', url, {'row': i, 'cells': row, 'sheet_source_id': sid})
            rid = key(sid,i,j)
            db.execute('INSERT OR IGNORE INTO lab_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (rid,None,None,None,report_sid,'Janoshik',None,None,None,None,None,'supplier','pending',encoded({'name':row[0],'batch':row[1] if len(row)>1 else None,'url':url})))
            flag(db,'lab_result',rid,'supplier_link_only','Report not fetched; supplier testing does not establish a GloFormulas vial or batch match.')
    audit(db,'import_sheet',sid,{'rows':len(values)})
    return sid


def import_folder(db, payload):
    sid = source(db,'coa_folder_listing',FOLDER_URL,payload)
    data = payload.get('content', payload)
    if isinstance(data,str):
        import json
        data=json.loads(data)
    files=data.get('files',[])
    for item in files:
        fsid=source(db,'coa_file_metadata',item['url'],item)
        flag(db,'source',fsid,'report_unread','File metadata only; original image/PDF and laboratory verification still required.')
    if len(files)>=100:
        flag(db,'source',sid,'possibly_partial_listing','Connector limits folder listings to 100 direct children; use paginated Drive API sync.')
    audit(db,'import_folder',sid,{'files':len(files)})
    return sid
