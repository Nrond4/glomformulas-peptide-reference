"""Deterministic review rules, never a certificate of clinical safety."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from .core import key, encoded, source, flag, audit


def numeric(raw):
    if raw is None:
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:%|mg)?\s*', str(raw), re.I)
    return Decimal(match[1]) if match else None


def assess(report, *, today=None, low_purity=Decimal('98'), content_tolerance=Decimal('10')):
    """Thresholds are review triggers, not specifications or safety cutoffs."""
    today = today or date.today()
    flags=[]
    lab=str(report.get('lab') or '')
    code=str(report.get('search_code') or '').strip()
    verified=report.get('lab_authenticated') is True
    linked=report.get('vendor_batch_matched') is True
    attr='supplier' if report.get('supplier_testing') is True else 'unverified'
    if 'freedom' in lab.lower() and not re.fullmatch(r'GLO[A-Za-z0-9_-]+',code):
        flags.append('freedom_non_glo_or_missing_code')
        linked=False
    if verified and linked and not report.get('supplier_testing'):
        attr='gloformulas'
    required=['identity','purity','measured_content','lab','report_date','batch','search_code']
    flags.extend('missing_'+x for x in required if report.get(x) in (None,''))
    if not verified: flags.append('lab_authenticity_unverified')
    if not linked: flags.append('vendor_batch_link_unverified')
    if report.get('expected_identity') and report.get('identity') != report['expected_identity']:
        flags.append('identity_mismatch')
    if report.get('expected_batch') and report.get('batch') != report['expected_batch']:
        flags.append('batch_mismatch')
    try:
        if report.get('report_date') and date.fromisoformat(report['report_date'])>today:
            flags.append('future_report_date')
    except (ValueError,TypeError): flags.append('invalid_report_date')
    purity=numeric(report.get('purity'))
    if purity is not None and (purity<0 or purity>100 or purity<low_purity): flags.append('unusual_purity')
    if report.get('purity') and purity is None: flags.append('qualified_or_unparsed_purity')
    content=numeric(report.get('measured_content'))
    nominal=numeric(report.get('nominal_content'))
    if content is not None and nominal is not None:
        if report.get('content_unit') != report.get('nominal_unit') or not report.get('content_unit'):
            flags.append('content_unit_mismatch_or_missing')
        elif nominal<=0 or abs(content/nominal-1)*100>content_tolerance:
            flags.append('unusual_measured_content')
    if report.get('blend') and not report.get('blend_specific_report'):
        flags.append('blend_specific_report_missing')
    blocking={'identity_mismatch','batch_mismatch','future_report_date','invalid_report_date','freedom_non_glo_or_missing_code'}
    status='authenticated_with_flags' if verified else 'pending'
    if verified and not flags: status='authenticated'
    if blocking.intersection(flags): status='mismatch'
    return {'attribution':attr,'verification_status':status,'flags':sorted(set(flags)),
            'limitation':'COA results do not establish sterility, clinical safety, efficacy, or concurrent-use compatibility.'}


def record_report(db, report, url):
    # Reviewer attestations must identify their reviewer and the primary lab artifact.
    if (report.get('lab_authenticated') or report.get('vendor_batch_matched')) and not (report.get('reviewer') and report.get('verification_artifact')):
        raise ValueError('Authentication/linkage attestations require reviewer and verification_artifact')
    result=assess(report)
    sid=source(db,'reviewed_coa_record',url,report)
    rid=key('report',sid)
    pid=report.get('product_id')
    if pid and not db.execute('SELECT 1 FROM products WHERE id=?',(pid,)).fetchone(): raise ValueError('Unknown product_id')
    batch_id=key(pid,report['batch'],sid) if report.get('batch') else None
    if batch_id: db.execute('INSERT OR IGNORE INTO batches VALUES(?,?,?,?)',(batch_id,pid,report['batch'],sid))
    duplicates=db.execute('SELECT id FROM lab_results WHERE lab=? AND search_code=? AND id<>?',(report.get('lab'),report.get('search_code'),rid)).fetchall() if report.get('search_code') else []
    db.execute('INSERT OR IGNORE INTO lab_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (rid,pid,None,batch_id,sid,report.get('lab'),report.get('report_date'),report.get('search_code'),report.get('identity'),report.get('purity'),str(report['measured_content']) if report.get('measured_content') is not None else None,result['attribution'],result['verification_status'],encoded(report)))
    for code in result['flags']: flag(db,'lab_result',rid,code,code.replace('_',' '))
    for duplicate in duplicates: flag(db,'lab_result',rid,'duplicate_lab_search_code',duplicate['id'])
    audit(db,'verify_report',rid,result)
    return result
