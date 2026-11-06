import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gloformulas.core import connect,source
from gloformulas.http import Client,Response,FetchError
from gloformulas.live_catalog import CatalogCrawler,product_data
from gloformulas.catalog import import_catalog
from gloformulas.supplier import compare_supplier,normalize
from gloformulas.freedom import FreedomClient,glo_code,report_links,parse_text
from gloformulas.research import ingest_articles,set_context,add_interaction,approve_interaction,coverage_report,discover
from gloformulas.guide import approve,approved,render
from gloformulas.daily import daily
from test_engine import document
from gloformulas.importers import import_guide

class FakeHTTP:
    def __init__(self,routes):self.routes=routes;self.calls=[]
    def get(self,url):
        self.calls.append(url)
        r=self.routes.get(url)
        if isinstance(r,Exception):raise r
        if r is None:raise FetchError('fixture URL not configured: '+url)
        if isinstance(r,Response):return r
        return Response(url,r.encode() if isinstance(r,str) else json.dumps(r).encode(),{})

class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=connect(self.root/'db.sqlite');import_guide(self.db,document());self.db.commit();self.pid=self.db.execute('SELECT id FROM products').fetchone()[0]
    def tearDown(self):self.db.close();self.temp.cleanup()
    def sheet(self,batch='A',links=None):return {'values':[['Name','Batch coding','Latest COA'],['Example 10mg',batch]+(links or ['https://janoshik.com/tests/a'])]}
    def catalog(self,name='Example',scope=None):
        c={'complete':True,'products':[{'key':'x','name':name,'url':'https://gloformulas.com/product/x','sizes':['10mg']}]}
        if scope:c['scope']=scope
        return c
    def claim(self,category='concurrent_use'):
        sid=source(self.db,'medical_primary_source','https://example.org/study',{'title':'Synthetic study'})
        self.db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)',('claim',self.pid,category,'Synthetic reviewed finding',sid,'2020-01-01','unassessed','pending',None,None))
        set_context(self.db,{'evidence_id':'claim','population':'Study adults','route':'Study route','species':'human','outcome':'Study endpoint','locator':'Results table 2','limitations':'Small study; no inference beyond this population','review_due':'2099-01-01'})
        return 'claim'
    def test_supplier_reordering_creates_no_changes(self):
        s=self.sheet();s['values'].append(['Other 20mg','B','https://janoshik.com/tests/b']);compare_supplier(self.db,s)
        s['values'][1:]=reversed(s['values'][1:]);self.assertEqual(compare_supplier(self.db,s),[])
        self.assertEqual(self.db.execute("SELECT count(*) FROM lab_results WHERE attribution='supplier'").fetchone()[0],3) # one guide row plus two links
    def test_supplier_detects_new_removed_and_link_change(self):
        compare_supplier(self.db,self.sheet());changes=compare_supplier(self.db,self.sheet(links=['https://janoshik.com/tests/new']));self.assertEqual(changes[0]['kind'],'report_links_changed')
        changes=compare_supplier(self.db,self.sheet('B'));self.assertEqual({c['kind'] for c in changes},{'new_batch_or_product','removed_supplier_row'})
    def test_supplier_reversion_not_ignored(self):
        a=self.sheet();compare_supplier(self.db,a);compare_supplier(self.db,self.sheet('B'));self.assertEqual(len(compare_supplier(self.db,a)),2)
    def test_supplier_invalid_baseline_preserved(self):
        compare_supplier(self.db,self.sheet())
        with self.assertRaises(ValueError):compare_supplier(self.db,{'values':[['Name']]})
        self.assertEqual(self.db.execute('SELECT count(*) FROM supplier_snapshots').fetchone()[0],1)
    def test_supplier_batch_match_never_promotes_vendor(self):
        compare_supplier(self.db,self.sheet('CS-01'));self.assertEqual(self.db.execute('SELECT match_status FROM supplier_links').fetchone()[0],'exact_lot_candidate');self.assertEqual(self.db.execute("SELECT count(*) FROM lab_results WHERE attribution='gloformulas'").fetchone()[0],0)
    def test_supplier_conflicting_duplicate_rejected(self):
        s=self.sheet();s['values'].append(['Example 10mg','A','https://janoshik.com/tests/different'])
        with self.assertRaises(ValueError):normalize(s)
    def test_catalog_return_to_previous_content_detected(self):
        a=self.catalog();import_catalog(self.db,a);import_catalog(self.db,self.catalog('Changed'));self.assertEqual(import_catalog(self.db,a)[0][1],'listing_update')
    def test_catalog_scope_change_not_discontinuation(self):
        import_catalog(self.db,self.catalog(scope='sitemap'))
        with self.assertRaises(ValueError):import_catalog(self.db,self.catalog(scope='woocommerce'))
    def test_jsonld_product_sizes_and_fields(self):
        content='<script type="application/ld+json">'+json.dumps({'@type':'Product','name':'Example 10mg','sku':'E','offers':{'availability':'https://schema.org/OutOfStock'}})+'</script>'
        row=product_data(content,'https://gloformulas.com/product/e')[0];self.assertEqual(row['sizes'],['10mg']);self.assertEqual(row['availability'],['OutOfStock'])
    def test_sitemap_complete_and_robot_obeyed(self):
        base='https://gloformulas.com';product='<script type="application/ld+json">{"@type":"Product","name":"Example 10mg"}</script>'
        routes={base+'/robots.txt':'User-agent: *\nAllow: /',base+'/sitemap.xml':'<urlset><url><loc>'+base+'/product/a</loc></url></urlset>',base+'/product/a':product}
        result=CatalogCrawler(FakeHTTP(routes)).run('sitemap');self.assertTrue(result['complete']);self.assertEqual(len(result['products']),1)
        routes[base+'/robots.txt']='User-agent: *\nDisallow: /product/'
        with self.assertRaises(FetchError):CatalogCrawler(FakeHTTP(routes)).run('sitemap')
    def test_unreadable_product_page_blocks_snapshot(self):
        b='https://gloformulas.com';http=FakeHTTP({b+'/robots.txt':'User-agent: *\nAllow: /',b+'/sitemap.xml':'<urlset><url><loc>'+b+'/product/a</loc></url></urlset>',b+'/product/a':'<div>Loading…</div>'})
        with self.assertRaises(ValueError):CatalogCrawler(http).run('sitemap')
    def test_woocommerce_total_and_pagination(self):
        b='https://gloformulas.com';u=b+'/wp-json/wc/store/v1/products?per_page=100&page=1';rows=[{'id':1,'name':'Example','permalink':b+'/product/a','attributes':[{'name':'Size','terms':[{'name':'10mg'}]}],'is_in_stock':False}]
        h=FakeHTTP({b+'/robots.txt':'User-agent: *\nAllow: /',u:Response(u,json.dumps(rows).encode(),{'x-wp-total':'1'}),b+'/product/a':'<p>Product page without a posted COA</p>'})
        result=CatalogCrawler(h).run('woocommerce');self.assertEqual(result['products'][0]['sizes'],['10mg']);self.assertEqual(result['products'][0]['availability'],'out_of_stock')
    def test_woocommerce_missing_total_not_complete(self):
        b='https://gloformulas.com';u=b+'/wp-json/wc/store/v1/products?per_page=100&page=1';h=FakeHTTP({b+'/robots.txt':'User-agent: *\nAllow: /',u:[]})
        with self.assertRaises(ValueError):CatalogCrawler(h).run('woocommerce')
    def test_shopify_repeated_page_rejected(self):
        b='https://gloformulas.com';rows={'products':[{'id':1,'title':'Example','handle':'example','variants':[]}]};h=FakeHTTP({b+'/robots.txt':'User-agent: *\nAllow: /',b+'/products.json?limit=250&page=1':rows,b+'/products.json?limit=250&page=2':rows})
        with self.assertRaises(ValueError):CatalogCrawler(h).run('shopify')
    def test_http_rejects_host_escape_and_unsafe_scheme(self):
        h=Client({'gloformulas.com'})
        for u in ('http://gloformulas.com','https://evil.example/','https://gloformulas.com@evil.example/','https://gloformulas.com:444/'):
            with self.assertRaises(FetchError):h.check_url(u)
    def test_freedom_glo_only_and_normalization(self):
        self.assertEqual(glo_code('glo 123'),'GLO123')
        for code in ('ABC123','GLO','XGLO123','GLO?evil'):
            with self.assertRaises(ValueError):glo_code(code)
    def test_freedom_official_pdf_links_only(self):
        content='<a href="https://coas.freedomdiagnosticstesting.com/GLO123.pdf">PDF</a><a href="https://evil.example/GLO123.pdf">Other</a>'
        self.assertEqual(len(report_links(content)),1)
    def test_freedom_no_results_not_missing_coa(self):
        with self.assertRaises(FetchError):FreedomClient(rendered=lambda **k:'<div>Loading</div>').search('GLO123')
    def test_freedom_extraction_conflict_and_date(self):
        f=parse_text('Search Code: GLO123\nLot: B1\nPurity: 99.1%\nNet Content: 10.5mg\nReport Date: October 1, 2026\nIdentity: Example\nCompany: GloFormulas')
        self.assertEqual(f['report_date'],'2026-10-01');self.assertEqual(f['measured_content'],'10.5mg')
        self.assertIsNone(parse_text('Purity: 99%\nPurity: 98%')['purity'])
    def test_freedom_wrong_code_never_records(self):
        target='https://coas.freedomdiagnosticstesting.com/report.pdf';h=FakeHTTP({target:Response(target,b'%PDFsynthetic',{})})
        with patch('gloformulas.freedom.extract_pdf',return_value=({'search_code':'OTHER123'},[{'page':1,'text':'x'}])):
            r=FreedomClient(h,lambda **k:'<a href="'+target+'">PDF</a>').collect(self.db,['GLO123'])
        self.assertEqual(r['records'],[]);self.assertEqual(r['status'],'blocked')
    def test_freedom_authenticity_separate_from_batch_and_idempotent(self):
        target='https://coas.freedomdiagnosticstesting.com/report.pdf';h=FakeHTTP({target:Response(target,b'%PDFsynthetic',{})})
        fields={'search_code':'GLO123','company':'GloFormulas','identity':'Example','batch':'B','purity':'99.1%','measured_content':'10mg','report_date':'2020-01-01'}
        client=FreedomClient(h,lambda **k:'<a href="'+target+'">PDF</a>')
        with patch('gloformulas.freedom.extract_pdf',return_value=(fields,[{'page':1,'text':'Synthetic fixture'}])):
            a=client.collect(self.db,['GLO123']);client.collect(self.db,['GLO123'])
        self.assertEqual(a['records'][0]['attribution'],'unverified');self.assertEqual(self.db.execute('SELECT count(*) FROM lab_fetches').fetchone()[0],1);self.assertEqual(self.db.execute('SELECT count(*) FROM report_artifacts').fetchone()[0],1)
    def test_literature_discovery_pagination_and_cap(self):
        class LiteratureHTTP:
            def __init__(self):self.calls=[]
            def get(inner,url):
                from urllib.parse import parse_qs,urlparse
                inner.calls.append(url);params=parse_qs(urlparse(url).query)
                if params['cursorMark']==['*']:
                    payload={'hitCount':2,'nextCursorMark':'second','resultList':{'result':[{'id':'1','source':'MED','title':'First synthetic article'}]}}
                else:
                    payload={'hitCount':2,'nextCursorMark':'second','resultList':{'result':[{'id':'2','source':'MED','title':'Second synthetic article'}]}}
                return Response(url,json.dumps(payload).encode(),{})
        http=LiteratureHTTP();result=discover(self.db,self.pid,http,max_results=2)
        self.assertEqual(result['candidates'],2);self.assertEqual(len(http.calls),2);self.assertEqual(result['coverage'],'bounded_discovery_not_complete_review')
    def test_report_automatic_vendor_link_requires_reviewed_vendor_source(self):
        target='https://coas.freedomdiagnosticstesting.com/report.pdf';h=FakeHTTP({target:Response(target,b'%PDFsynthetic',{})})
        fields={'search_code':'GLO123','company':'GloFormulas','identity':'Example','batch':'B','purity':'99.1%','measured_content':'10mg','report_date':'2020-01-01'}
        expected={'GLO123':{'product_id':self.pid,'identity':'Example','batch':'B'}}
        client=FreedomClient(h,lambda **k:'<a href="'+target+'">PDF</a>')
        with patch('gloformulas.freedom.extract_pdf',return_value=(fields,[{'page':1,'text':'Synthetic fixture'}])):
            result=client.collect(self.db,['GLO123'],expected)
        self.assertEqual(result['records'][0]['attribution'],'unverified')
        vendor=source(self.db,'vendor_batch_record','https://gloformulas.com/product/example',{'batch':'B'})
        expected['GLO123'].update({'linkage_source_id':vendor,'linkage_reviewed_by':'Named reviewer'})
        with patch('gloformulas.freedom.extract_pdf',return_value=(fields,[{'page':1,'text':'Synthetic fixture'}])):
            result=client.collect(self.db,['GLO123'],expected)
        self.assertEqual(result['records'][0]['attribution'],'gloformulas')
    def test_full_simulated_daily_run_captures_changes_without_google_writes(self):
        imports=self.root/'imports';imports.mkdir();(imports/'guide.json').write_text(json.dumps(document()));(imports/'folder.json').write_text('{"files":[]}')
        services={'catalog':lambda:self.catalog(),'supplier':lambda:self.sheet(),'freedom':lambda db:{'status':'completed','records':[]},'research':lambda db,pid:{'status':'pending'}}
        with patch('gloformulas.daily.config',{'RESEARCH_PRODUCT_IDS':[self.pid]}),patch('gloformulas.daily.schedule_status',return_value={'daily_schedule_confirmed':False}),patch('gloformulas.google.publish',side_effect=AssertionError('Must not publish')):
            first=daily(self.db,self.root,True,services)
            services.update({'catalog':lambda:self.catalog('New name'),'supplier':lambda:self.sheet('B')})
            second=daily(self.db,self.root,True,services)
        self.assertEqual(first['status'],'completed_checks');self.assertEqual(second['changes']['catalog_changes'],1);self.assertEqual(second['changes']['supplier_changes'],2);self.assertEqual(second['google_doc'],'unchanged_read_only_run')
    def test_research_discovery_never_claim_approval(self):
        payload={'resultList':{'result':[{'id':'1','source':'MED','title':'Synthetic article','firstPublicationDate':'2020-01-01'}]}};ingest_articles(self.db,self.pid,payload)
        self.assertEqual(self.db.execute('SELECT status FROM research_candidates').fetchone()[0],'pending');self.assertEqual(len(approved(self.db,self.pid,'uses')),0)
    def test_claim_requires_context_and_future_date_blocked(self):
        self.claim()
        with self.assertRaises(ValueError):approve(self.db,'claim','Reviewer','human_trial','2099-01-01')
        approve(self.db,'claim','Reviewer','human_trial','2020-01-01');self.assertEqual(len(approved(self.db,self.pid,'concurrent_use')),1)
        self.db.execute("UPDATE evidence_context SET review_due='2000-01-01'");self.assertEqual(approved(self.db,self.pid,'concurrent_use'),[])
    def test_retraction_revokes_linked_approval(self):
        self.claim();payload={'resultList':{'result':[{'id':'1','source':'MED','title':'Synthetic article'}]}};ingest_articles(self.db,self.pid,payload);cid=self.db.execute('SELECT id FROM research_candidates').fetchone()[0]
        self.db.execute('UPDATE evidence_context SET source_candidate_id=?',(cid,));approve(self.db,'claim','Reviewer','human_trial','2020-01-01');payload['resultList']['result'][0]['isRetracted']='Y';ingest_articles(self.db,self.pid,payload)
        self.assertEqual(self.db.execute("SELECT review_status FROM evidence WHERE id='claim'").fetchone()[0],'pending')
    def test_safe_combination_label_rejected(self):
        with self.assertRaises(ValueError):add_interaction(self.db,{'product_id':self.pid,'other_agent':'Other','relationship':'safe','population':'Adults','route':'Study route','detail':'Synthetic'})
    def test_interaction_requires_reviewed_claim(self):
        self.claim();iid=add_interaction(self.db,{'product_id':self.pid,'other_agent':'Other','evidence_id':'claim','relationship':'potential_precaution','population':'Adults','route':'Study route','detail':'Synthetic finding'})
        with self.assertRaises(ValueError):approve_interaction(self.db,iid,'Reviewer')
        approve(self.db,'claim','Reviewer','human_trial','2020-01-01');approve_interaction(self.db,iid,'Reviewer');self.assertIn('Documented co-use record',render(self.db))
    def test_context_edit_revokes_previous_approval(self):
        self.claim();approve(self.db,'claim','Reviewer','human_trial','2020-01-01');self.claim();self.assertEqual(approved(self.db,self.pid,'concurrent_use'),[])
    def test_all_products_have_four_coverage_categories(self):
        r=coverage_report(self.db);self.assertEqual(len(r),4);self.assertEqual({x['status'] for x in r},{'review_required'})
    def test_daily_partial_continues_and_never_publishes(self):
        imports=self.root/'imports';imports.mkdir();(imports/'guide.json').write_text(json.dumps(document()));(imports/'folder.json').write_text('{"files":[]}')
        def blocked():raise FetchError('source offline')
        with patch('gloformulas.daily.schedule_status',return_value={'daily_schedule_confirmed':False}):
            result=daily(self.db,self.root,True,{'catalog':blocked,'supplier':lambda:self.sheet(),'freedom':lambda db:{'status':'blocked','reason':'fixture'}})
        self.assertEqual(result['status'],'partial');self.assertTrue(self.db.execute('SELECT 1 FROM supplier_snapshots').fetchone());self.assertEqual(result['google_doc'],'unchanged_read_only_run');self.assertTrue((self.root/'reports/latest.md').exists())
    def test_daily_offline_is_not_live(self):
        imports=self.root/'imports';imports.mkdir();(imports/'guide.json').write_text(json.dumps(document()));(imports/'folder.json').write_text('{"files":[]}');(imports/'sheet.json').write_text(json.dumps(self.sheet()));(imports/'catalog.json').write_text(json.dumps(self.catalog()))
        with patch('gloformulas.daily.schedule_status',return_value={'daily_schedule_confirmed':False}):r=daily(self.db,self.root)
        self.assertEqual(r['mode'],'offline_replay');self.assertEqual(r['status'],'offline_validation')

if __name__=='__main__':unittest.main()
