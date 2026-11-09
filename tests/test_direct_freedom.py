import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gloformulas.core import connect
from gloformulas.http import FetchError,Response
from gloformulas.freedom import FreedomClient,direct_report_url,parse_text,extract_pdf
from gloformulas.actions_monitor import public_summary
from test_transport_pdf import pdf_fixture

class PDFClient:
    def __init__(self,routes):self.routes=routes;self.calls=[]
    def get(self,url):
        self.calls.append(url)
        result=self.routes.get(url)
        if isinstance(result,Exception):raise result
        if result is None:raise FetchError('Source returned HTTP 404',404)
        return Response(url,result,{'content-type':'application/pdf'})

def report(code='GLO0001'):
    return pdf_fixture(['Search Code '+code,'Client Synthetic client','Identity Example','Lot B1','Purity (HPLC-UV) 99.1%','Net Peptide Content 10.5mg','Reported Date 2020-01-01'])

class ParserTests(unittest.TestCase):
    def test_route_accepts_only_configured_glo_code(self):
        self.assertEqual(direct_report_url('glo 0001'),'https://coas.freedomdiagnosticstesting.com/GLO0001.pdf')
        for code in ('OTHER0001','GLO','GLO../private','GLO?token=x'):
            with self.assertRaises(ValueError):direct_report_url(code)
    def test_labeled_single_space_fields_and_reported_date(self):
        fields=parse_text('Search Code GLO0001\nClient Synthetic\nReported Date 11/03/2020\nIdentity (LC-MS) Example\nPurity (HPLC-UV) 99.1%\nNet Content Average 10.5mg\nLot B1')
        self.assertEqual(fields['search_code'],'GLO0001');self.assertEqual(fields['report_date'],'2020-11-03');self.assertEqual(fields['purity'],'99.1%');self.assertEqual(fields['identity'],'Example')
    def test_conflicting_printed_codes_not_resolved_by_filename(self):
        fields=parse_text('Search Code GLO0001\nSearch Code GLO0002')
        self.assertIsNone(fields['search_code']);self.assertIn('conflicting_search_code',fields['extraction_flags'])
    def test_public_summary_discloses_bounded_coverage_without_report_code(self):
        output=public_summary({'status':'completed_checks','checks':[{'component':'freedom','status':'completed','detail':{'coverage':'tracked_codes','records':[{'search_code':'GLO_PRIVATE_CODE'}]}}]})
        self.assertIn('configured report codes only',output);self.assertNotIn('GLO_PRIVATE_CODE',output)

@unittest.skipUnless(importlib.util.find_spec('pypdf'),'Install lab extra')
class DirectIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/'db.sqlite');self.url=direct_report_url('GLO0001')
    def tearDown(self):self.db.close();self.temp.cleanup()
    def test_direct_pdf_precedes_browser_and_is_not_downloaded_twice(self):
        http=PDFClient({self.url:report()})
        def no_browser(**kwargs):raise AssertionError('Direct report should not require the lookup UI')
        result=FreedomClient(http,no_browser).collect(self.db,['GLO0001'])
        self.assertEqual(result['status'],'completed');self.assertEqual(len(result['records']),1);self.assertEqual(http.calls,[self.url])
        self.assertEqual(result['records'][0]['attribution'],'unverified');self.assertEqual(self.db.execute('SELECT count(*) FROM report_artifacts').fetchone()[0],1)
    def test_direct_report_import_is_idempotent(self):
        client=FreedomClient(PDFClient({self.url:report()}))
        client.collect(self.db,['GLO0001']);client.collect(self.db,['GLO0001'])
        for table in ('lab_fetches','report_artifacts','lab_results'):self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],1)
    def test_404_falls_back_to_public_lookup(self):
        alternate='https://coas.freedomdiagnosticstesting.com/alternate.pdf';http=PDFClient({alternate:report()});rendered=[]
        def lookup(**kwargs):rendered.append(kwargs);return '<a href="'+alternate+'">Open PDF</a>'
        result=FreedomClient(http,lookup).collect(self.db,['GLO0001'])
        self.assertEqual(result['status'],'completed');self.assertEqual(http.calls,[self.url,alternate]);self.assertEqual(len(rendered),1)
    def test_access_denial_or_rate_limit_does_not_try_another_route(self):
        for status in (403,429):
            http=PDFClient({self.url:FetchError('Source returned HTTP '+str(status),status)})
            def no_browser(**kwargs):raise AssertionError('Do not route around an access/rate-limit response')
            result=FreedomClient(http,no_browser).collect(self.db,['GLO0001'])
            self.assertEqual(result['status'],'blocked');self.assertEqual(http.calls,[self.url])
    def test_html_is_not_accepted_as_a_report(self):
        result=FreedomClient(PDFClient({self.url:b'<html>Sign in</html>'})).collect(self.db,['GLO0001'])
        self.assertEqual(result['records'],[]);self.assertEqual(self.db.execute('SELECT count(*) FROM report_artifacts').fetchone()[0],0)
    def test_mismatched_printed_code_keeps_pdf_but_accepts_no_record(self):
        result=FreedomClient(PDFClient({self.url:report('GLO0002')})).collect(self.db,['GLO0001'])
        self.assertEqual(result['records'],[]);self.assertEqual(self.db.execute('SELECT count(*) FROM report_artifacts').fetchone()[0],1)
        self.assertTrue(self.db.execute("SELECT 1 FROM flags WHERE code='search_code_mismatch'").fetchone());self.assertEqual(self.db.execute('SELECT count(*) FROM lab_results').fetchone()[0],0)
    def test_non_glo_printed_code_is_not_associated_with_gloformulas(self):
        result=FreedomClient(PDFClient({self.url:report('OTHER0001')})).collect(self.db,['GLO0001'])
        self.assertEqual(result['records'],[]);self.assertTrue(self.db.execute("SELECT 1 FROM flags WHERE code='non_glo_printed_code'").fetchone())
    def test_missing_printed_code_is_not_filled_from_filename(self):
        result=FreedomClient(PDFClient({self.url:pdf_fixture(['Identity Example','Lot B1'])})).collect(self.db,['GLO0001'])
        self.assertEqual(result['records'],[]);self.assertTrue(self.db.execute("SELECT 1 FROM flags WHERE code='printed_search_code_unreadable'").fetchone())
    def test_unreadable_pdf_retained_for_manual_review(self):
        with patch('gloformulas.freedom.extract_pdf',side_effect=ValueError('Synthetic extraction failure')):
            result=FreedomClient(PDFClient({self.url:b'%PDFsynthetic'})).collect(self.db,['GLO0001'])
        self.assertEqual(result['records'],[]);self.assertTrue(self.db.execute("SELECT 1 FROM flags WHERE code='pdf_extraction_review_required'").fetchone());self.assertEqual(self.db.execute('SELECT count(*) FROM report_artifacts').fetchone()[0],1)
    def test_layout_fallback_reads_missing_field_but_never_resolves_conflict(self):
        class Page:
            def __init__(self,conflict):self.conflict=conflict
            def extract_text(self,**kwargs):
                if kwargs:return 'Search Code GLO0001'
                return 'Search Code GLO0001\nSearch Code GLO0002' if self.conflict else 'Identity Example'
        for conflict in (False,True):
            with patch('pypdf.PdfReader',return_value=type('Reader',(),{'pages':[Page(conflict)]})()):fields,pages=extract_pdf(b'%PDFsynthetic')
            self.assertEqual(fields['search_code'],None if conflict else 'GLO0001')
            self.assertIn('layout_text',pages[0])
