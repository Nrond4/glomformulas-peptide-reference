"""Official-host GLO lookup and report intake; prefix never substitutes for batch linkage."""
import hashlib
import io
import re
from urllib.parse import urljoin,urlparse,urlencode,unquote
from .http import Client,FetchError
from .core import source,key,now,encoded,flag
from .live_catalog import Page
from .verification import record_report

HOST='freedomdiagnosticstesting.com'
HOSTS={HOST,'www.'+HOST,'coas.'+HOST}
LOOKUP='https://'+HOST+'/search-for-your-coa-based-on-the-unique-accession-number/'


def glo_code(value):
    value=re.sub(r'\s+','',str(value)).upper()
    if not re.fullmatch(r'GLO[A-Z0-9_-]+',value):raise ValueError('Only GLO-prefixed search codes are permitted')
    return value


def report_links(content,base=LOOKUP):
    links=[]
    for href in Page(content).links:
        target=urljoin(base,href);p=urlparse(target)
        if p.scheme=='https' and p.hostname in HOSTS and (p.path.lower().endswith('.pdf') or 'application/pdf' in href):
            links.append(target)
    return sorted(set(links))


LABELS={
 'search_code':r'Search\s*Code', 'batch':r'(?:Batch(?:\s*/\s*Lot)?|Lot)(?:\s*(?:Number|No\.?))?',
 'report_date':r'(?:Report\s*Date|Date\s*(?:Issued|Reported)|Issued\s*Date)',
 'identity':r'(?:Peptide\s*Identity|Compound(?:\(s\))?\s*Identified|Identity(?:\s*Result)?|Compound\s*Name)',
 'purity':r'(?:HPLC\s*)?Purity', 'measured_content':r'(?:Net\s*(?:Peptide\s*)?Content|Measured\s*Content)',
 'company':r'(?:Company(?:\s*Name)?|Client(?:\s*Name)?)'}

def parse_text(text):
    fields={}
    for name,label in LABELS.items():
        matches=re.findall(r'(?im)^[ \t]*(?:'+label+r')[ \t]*(?::[ \t]*|\t+|[ ]{2,}|\r?\n[ \t]*)([^\n\r]+)',text)
        values=sorted({m.strip() for m in matches if m.strip()})
        if len(values)==1:fields[name]=values[0]
        elif len(values)>1:fields[name]=None;fields.setdefault('extraction_flags',[]).append('conflicting_'+name)
    # Dates remain absent/flagged when their printed representation is ambiguous.
    if fields.get('report_date'):
        from datetime import datetime
        for fmt in ('%Y-%m-%d','%m/%d/%Y','%B %d, %Y','%b %d, %Y'):
            try:fields['report_date']=datetime.strptime(fields['report_date'],fmt).date().isoformat();break
            except ValueError:pass
    return fields


def extract_pdf(body):
    if not body.startswith(b'%PDF'):raise ValueError('Lab link did not return a PDF')
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(body))
    if len(reader.pages)>50:raise ValueError('Report page limit exceeded')
    pages=[{'page':i+1,'text':page.extract_text() or ''} for i,page in enumerate(reader.pages)]
    if not any(p['text'].strip() for p in pages):raise ValueError('Scanned report needs OCR and manual field confirmation')
    return parse_text('\n'.join(p['text'] for p in pages)),pages


class FreedomClient:
    def __init__(self,client=None,rendered=None):
        self.client=client or Client(HOSTS);self.rendered=rendered
    def search(self,code=None,company='GloFormulas'):
        if code is not None:code=glo_code(code)
        if re.sub(r'[^a-z]','',company.lower())!='gloformulas':raise ValueError('Only GloFormulas company discovery is permitted')
        if self.rendered:
            content=self.rendered(code=code,company=company)
        else:
            content=self.client.get(LOOKUP+('?' + urlencode({'code':code}) if code else '')).text
        links=report_links(content)
        if not links:
            raise FetchError('No readable lab PDF results; enable rendered lookup or inspect lookup changes. This is not proof of missing COAs.')
        return links
    def collect(self,db,codes=(),expected=None):
        expected=expected or {};links=[];failures=[];records=[]
        for code in (list(codes) or [None]):
            try:links.extend((code,u) for u in self.search(code))
            except Exception as exc:failures.append({'code':code,'reason':str(exc) if isinstance(exc,(FetchError,ValueError,ImportError)) else type(exc).__name__})
        seen=set()
        for requested,target in links:
            if target in seen:continue
            seen.add(target)
            try:
                response=self.client.get(target)
                # Client enforces final redirect hosts too. Lab authenticity requires the actual PDF bytes.
                if urlparse(response.url).hostname not in HOSTS:raise ValueError('Report left official lab hosts')
                fields,pages=extract_pdf(response.body)
                code=glo_code(fields.get('search_code',''))
                if requested and code!=glo_code(requested):raise ValueError('Returned report search code differs from request')
                digest=hashlib.sha256(response.body).hexdigest()
                db.execute('INSERT OR IGNORE INTO report_artifacts VALUES(?,?,?,?,?,?)',(digest,response.body,'application/pdf',response.url,now(),len(response.body)))
                artifact=source(db,'freedom_primary_report',response.url,{'sha256':digest,'pages':pages,'fields':fields})
                exp=expected.get(code,{})
                company_match=re.sub(r'[^a-z]','',fields.get('company','').lower())=='gloformulas'
                linkage=db.execute('SELECT kind,url FROM sources WHERE id=?',(exp.get('linkage_source_id'),)).fetchone() if exp.get('linkage_source_id') else None
                linkage_reviewed=bool(exp.get('linkage_reviewed_by') and linkage and linkage['kind'] in ('catalog','vendor_batch_record') and urlparse(linkage['url']).hostname in ('gloformulas.com','www.gloformulas.com'))
                matched=bool(linkage_reviewed and company_match and exp.get('product_id') and exp.get('identity') and exp.get('batch') and fields.get('identity')==exp['identity'] and fields.get('batch')==exp['batch'])
                report=dict(fields,lab='Freedom Diagnostics',search_code=code,lab_authenticated=True,
                    vendor_batch_matched=matched,reviewer='automated official-lab retrieval',verification_artifact=artifact,
                    artifact_sha256=digest,product_id=exp.get('product_id') if matched else None,
                    expected_identity=exp.get('identity'),expected_batch=exp.get('batch'))
                result=record_report(db,report,response.url)
                fid=key(code,digest)
                db.execute('INSERT OR IGNORE INTO lab_fetches VALUES(?,?,?,?,?,?,?)',(fid,code,artifact,now(),'lab_hosted_report',encoded(report),digest))
                for code_flag in fields.get('extraction_flags',[]):flag(db,'lab_fetch',fid,code_flag,'Printed fields conflict; manual report review required.')
                if not matched:flag(db,'lab_fetch',fid,'vendor_batch_link_unverified','Official lab report found; exact GloFormulas product and batch linkage remains pending.')
                records.append({'search_code':code,'status':result['verification_status'],'attribution':result['attribution']})
            except Exception as exc:
                failures.append({'url':target,'reason':str(exc) if isinstance(exc,(ValueError,FetchError)) else type(exc).__name__})
        return {'records':records,'failures':failures,'status':'completed' if records and not failures else ('partial' if records else 'blocked'),'scope':'GLO-only','vendor_batch_linkage':'exact reviewed expectation required'}


def browser_lookup(*,code=None,company='GloFormulas'):
    """Optional rendered public lookup; no hidden endpoints, logins or CAPTCHA bypass.
    Real page compatibility still requires a live smoke check on the deployment host.
    """
    import os
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        proxy=os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        browser=p.chromium.launch(headless=True,proxy={'server':proxy} if proxy else None)
        try:
            page=browser.new_page();page.goto(LOOKUP,wait_until='domcontentloaded',timeout=30000)
            if code:
                page.get_by_role('button',name='Search Code / Accession #',exact=True).click()
                page.locator('input:visible:not([type=hidden])').first.fill(glo_code(code))
                page.get_by_role('button',name='Look up',exact=True).click()
            else:
                page.get_by_role('button',name='Company Name',exact=True).click()
                page.get_by_placeholder('Enter company name here',exact=True).fill(company)
                page.get_by_role('button',name='Search',exact=True).click()
            page.locator('a[href*=".pdf"]').first.wait_for(state='attached',timeout=20000)
            return page.content()
        finally:browser.close()
