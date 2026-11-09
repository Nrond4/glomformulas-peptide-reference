"""Official-host GLO lookup and report intake; prefix never substitutes for batch linkage."""
import hashlib
import io
import re
from urllib.parse import urljoin,urlparse,urlencode
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


def direct_report_url(code):
    # Official public route demonstrated by a supplied GLO report, not code enumeration.
    return 'https://coas.'+HOST+'/'+glo_code(code)+'.pdf'


def report_links(content,base=LOOKUP):
    links=[]
    for href,label in Page(content).anchors:
        target=urljoin(base,href);p=urlparse(target)
        labeled_pdf=bool(re.search(r'open\s*pdf|view\s*coa|download\s*coa',label,re.I))
        if p.scheme=='https' and p.hostname in HOSTS and not p.username and not p.password and (p.path.lower().endswith('.pdf') or labeled_pdf):
            links.append(target)
    return sorted(set(links))


LABELS={
 'search_code':r'Search\s*Code', 'batch':r'(?:Batch(?:\s*/\s*Lot)?|Lot)(?:\s*(?:Number|No\.?))?',
 'report_date':r'(?:Reported\s*Date|Report\s*Date|Date\s*(?:Issued|Reported)|Issued\s*Date)',
 'identity':r'(?:Peptide\s*Identity|Compound(?:\(s\))?\s*Identified|Identity(?:\s*Result|[ \t]*\(LC-MS\))?|Compound\s*Name)',
 'purity':r'(?:HPLC\s*)?Purity(?:[ \t]*\(HPLC(?:-UV)?\))?', 'measured_content':r'(?:Net\s*(?:Peptide\s*)?Content(?:[ \t]+Average)?|Measured\s*Content)',
 'company':r'(?:Company(?:\s*Name)?|Client(?:\s*Name)?)'}

def parse_text(text):
    fields={}
    for name,label in LABELS.items():
        matches=re.findall(r'(?im)^[ \t]*(?:'+label+r')[ \t]*(?::[ \t]*|\t+|[ ]+|\r?\n[ \t]*)([^\n\r]+)',text)
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
    fields=parse_text('\n'.join(p['text'] for p in pages))
    if not fields.get('search_code'):
        # Some table PDFs emit labels/values in an unreadable order in plain mode.
        # Preserve both renderings and only fill previously unreadable fields.
        for record,page in zip(pages,reader.pages):record['layout_text']=page.extract_text(extraction_mode='layout') or ''
        layout=parse_text('\n'.join(p['layout_text'] for p in pages))
        for name,value in layout.items():
            if name!='extraction_flags' and not fields.get(name) and value and 'conflicting_'+name not in fields.get('extraction_flags',[]):fields[name]=value
        fields['extraction_flags']=sorted(set(fields.get('extraction_flags',[])+layout.get('extraction_flags',[])))
    return fields,pages


class FreedomClient:
    def __init__(self,client=None,rendered=None,prefer_direct=True):
        self.client=client or Client(HOSTS);self.rendered=rendered;self.prefer_direct=prefer_direct;self._prefetched={}
    def search(self,code=None,company='GloFormulas'):
        if code is not None:
            code=glo_code(code)
            if self.prefer_direct:
                target=direct_report_url(code)
                try:response=self.client.get(target)
                except FetchError as exc:
                    # Only a missing route falls back to the public lookup. Never try
                    # another route to evade rate limits or an access denial.
                    if exc.status!=404:raise
                else:
                    if not response.body.startswith(b'%PDF'):raise ValueError('Official report download did not return PDF bytes')
                    self._prefetched[target]=response
                    return [target]
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
                response=self._prefetched.pop(target,None)
                if response is None:response=self.client.get(target)
                # Client enforces final redirect hosts too. Lab authenticity requires the actual PDF bytes.
                if urlparse(response.url).hostname not in HOSTS:raise ValueError('Report left official lab hosts')
                if not response.body.startswith(b'%PDF'):raise ValueError('Official report download did not return PDF bytes')
                digest=hashlib.sha256(response.body).hexdigest()
                db.execute('INSERT OR IGNORE INTO report_artifacts VALUES(?,?,?,?,?,?)',(digest,response.body,'application/pdf',response.url,now(),len(response.body)))
                try:fields,pages=extract_pdf(response.body)
                except Exception:
                    flag(db,'report_artifact',digest,'pdf_extraction_review_required','Official-host PDF retained privately; text extraction needs review.')
                    raise ValueError('Lab PDF extraction failed; original PDF retained for review') from None
                if not fields.get('search_code'):
                    flag(db,'report_artifact',digest,'printed_search_code_unreadable','Printed search code could not be read; do not substitute the filename or requested code.')
                    raise ValueError('Printed search code could not be read; original PDF retained for review')
                try:code=glo_code(fields['search_code'])
                except ValueError:
                    flag(db,'report_artifact',digest,'non_glo_printed_code','Printed report code is not GLO-prefixed; no GloFormulas report was accepted.')
                    raise ValueError('Printed report code is not GLO-prefixed; original PDF retained for review') from None
                if requested and code!=glo_code(requested):
                    flag(db,'report_artifact',digest,'search_code_mismatch','Printed search code differs from requested code; no report was accepted.')
                    raise ValueError('Returned report search code differs from request')
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
        return {'records':records,'failures':failures,'status':'completed' if records and not failures else ('partial' if records else 'blocked'),'scope':'GLO-only','coverage':'tracked_codes' if codes else 'company_lookup','vendor_batch_linkage':'exact reviewed expectation required'}


def lookup_page(page,*,code=None,company='GloFormulas',timeout=20000):
    """Use the observed public controls; distinguish navigation, form and result failures."""
    stage='navigation'
    try:
        response=page.goto(LOOKUP,wait_until='domcontentloaded',timeout=30000)
        status=getattr(response,'status',200) if response is not None else 200
        if status in (401,403,429,500,502,503,504):raise FetchError(f'Source returned HTTP {status}',status)
        stage='controls'
        if code:
            page.get_by_role('button',name='Search Code / Accession #',exact=True).click(timeout=10000)
            page.locator('input:visible:not([type=hidden])').first.fill(glo_code(code),timeout=10000)
            page.get_by_role('button',name='Look up',exact=True).click(timeout=10000)
        else:
            page.get_by_role('button',name='Company Name',exact=True).click(timeout=10000)
            page.get_by_placeholder('Enter company name here',exact=True).fill(company,timeout=10000)
            page.get_by_role('button',name='Search',exact=True).click(timeout=10000)
        stage='results'
        # Official results can say Open PDF while using a URL without a .pdf suffix.
        page.wait_for_function(r"""() => Array.from(document.querySelectorAll('a[href]')).some(a =>
            a.getClientRects().length && (new URL(a.href,location.href).pathname.toLowerCase().endsWith('.pdf') || /open\s*pdf|view\s*coa|download\s*coa/i.test(a.textContent)))""",timeout=timeout)
        content=page.content()
        if not report_links(content):raise FetchError('Freedom lookup results contain no readable official PDF links; not proof of missing COAs')
        return content
    except FetchError:raise
    except Exception as exc:
        if type(exc).__name__=='TimeoutError':
            raise FetchError('Freedom lookup '+stage+' timed out; not proof of missing COAs') from None
        raise FetchError('Freedom lookup '+stage+' could not be read; raw page content was not published') from None


def browser_lookup(*,code=None,company='GloFormulas'):
    """Read-only rendered lookup; no hidden endpoints, sign-ins or challenge bypass."""
    import os
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        proxy=os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        browser=p.chromium.launch(headless=True,proxy={'server':proxy} if proxy else None)
        try:return lookup_page(browser.new_page(),code=code,company=company)
        finally:browser.close()
