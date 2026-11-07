import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from gloformulas.core import connect
from gloformulas.http import Client,FetchError,Response,retry_after_seconds
from gloformulas.supplier import fetch_public_sheet
from gloformulas.freedom import lookup_page,report_links
from gloformulas.daily import daily
from gloformulas.actions_monitor import public_summary
from test_engine import document

class Control:
    def __init__(self,page):self.page=page
    @property
    def first(self):return self
    def click(self,**kwargs):
        if self.page.fail=='controls':raise TimeoutError('PRIVATE_TIMEOUT_DETAILS')
    def fill(self,value,**kwargs):self.page.filled=value

class LookupPage:
    def __init__(self,content='',fail=None,status=200):self.html=content;self.fail=fail;self.status=status;self.javascript=None
    def goto(self,*args,**kwargs):
        if self.fail=='navigation':raise TimeoutError('PRIVATE_NAVIGATION')
        return self
    def get_by_role(self,*args,**kwargs):return Control(self)
    def get_by_placeholder(self,*args,**kwargs):return Control(self)
    def locator(self,*args,**kwargs):return Control(self)
    def wait_for_function(self,script,**kwargs):
        self.javascript=script
        if self.fail=='results':raise TimeoutError('PRIVATE_RESULT_DETAILS')
    def content(self):return self.html

class ConnectionTests(unittest.TestCase):
    def test_google_csv_file_host_redirect_is_scoped_to_supplier_client(self):
        supplier=Client({'docs.google.com'},subdomain_roots={'googleusercontent.com'})
        supplier.check_url('https://doc-12-34-sheets.googleusercontent.com/export/synthetic')
        for u in ('https://googleusercontent.com.evil.example/export','https://evilgoogleusercontent.com/export','http://doc-sheets.googleusercontent.com/export'):
            with self.assertRaises(FetchError):supplier.check_url(u)
        with self.assertRaises(FetchError):Client({'docs.google.com'}).check_url('https://doc-sheets.googleusercontent.com/export')
    def test_google_sign_in_redirect_stops_without_credentials(self):
        with self.assertRaises(FetchError) as caught:Client({'docs.google.com'},subdomain_roots={'googleusercontent.com'}).check_url('https://accounts.google.com/ServiceLogin')
        self.assertIn('requires sign-in',str(caught.exception));self.assertNotIn('ServiceLogin',str(caught.exception))
    def test_supplier_html_sign_in_is_not_a_new_baseline(self):
        fake=type('Fake',(),{'get':lambda self,url:Response(url,b'<html>Sign in</html>',{'content-type':'text/html'})})()
        with patch('gloformulas.supplier.SHEET_URL','https://docs.google.com/spreadsheets/d/synthetic/edit'):
            with self.assertRaises(FetchError):fetch_public_sheet(fake)
    def test_supplier_valid_csv_read(self):
        fake=type('Fake',(),{'get':lambda self,url:Response(url,b'Name,Batch coding,Latest COA\nExample 10mg,B1,https://janoshik.com/tests/synthetic\n',{'content-type':'text/csv'})})()
        with patch('gloformulas.supplier.SHEET_URL','https://docs.google.com/spreadsheets/d/synthetic/edit'):
            self.assertEqual(fetch_public_sheet(fake)['values'][1][1],'B1')
    def test_retry_after_handles_seconds_http_dates_and_bad_values(self):
        instant=datetime(2026,11,7,0,0,tzinfo=timezone.utc)
        self.assertEqual(retry_after_seconds('120',instant),120)
        self.assertEqual(retry_after_seconds('Sat, 07 Nov 2026 00:10:00 GMT',instant),600)
        self.assertEqual(retry_after_seconds('Fri, 06 Nov 2026 00:00:00 GMT',instant),0)
        self.assertIsNone(retry_after_seconds('not a date',instant))
    def test_long_retry_after_date_is_not_ignored(self):
        client=Client({'example.org'},interval=0)
        delay='Sun, 07 Nov 2100 00:10:00 GMT'
        with patch.object(client.opener,'open',side_effect=urllib.error.HTTPError('https://example.org',429,'Later',{'Retry-After':delay},None)) as opened:
            with self.assertRaises(FetchError) as caught:client.get('https://example.org')
        self.assertEqual(opened.call_count,1);self.assertEqual(caught.exception.status,429);self.assertGreater(caught.exception.retry_after_seconds,30)
    def test_pdf_named_link_can_use_extensionless_official_url(self):
        content='<a href="https://coas.freedomdiagnosticstesting.com/report/123">Open PDF</a>'
        self.assertEqual(report_links(content),['https://coas.freedomdiagnosticstesting.com/report/123'])
        self.assertEqual(report_links('<a href="https://evil.example/report/123">Open PDF</a>'),[])
    def test_rendered_lookup_accepts_semantic_report_link(self):
        page=LookupPage('<a href="https://coas.freedomdiagnosticstesting.com/report/123">Open PDF</a>')
        self.assertIn('Open PDF',lookup_page(page));self.assertEqual(page.filled,'GloFormulas')
        self.assertIn(r'open\s*pdf',page.javascript);self.assertNotIn(r'open\\s*pdf',page.javascript)
    def test_lookup_timeouts_identify_phase_without_raw_error_text(self):
        for phase in ('navigation','controls','results'):
            with self.assertRaises(FetchError) as caught:lookup_page(LookupPage(fail=phase))
            reason=str(caught.exception);self.assertIn(phase+' timed out',reason);self.assertNotIn('PRIVATE_',reason)
            summary=public_summary({'status':'partial','checks':[{'component':'freedom','status':'blocked','detail':{'failures':[{'reason':reason}]}}]})
            self.assertIn('lookup_'+phase+'_timeout',summary)
    def test_lookup_http_429_is_not_treated_as_absent_reports(self):
        with self.assertRaises(FetchError) as caught:lookup_page(LookupPage(status=429))
        self.assertEqual(caught.exception.status,429)
    def test_empty_lookup_stays_unverified_not_a_missing_coa_conclusion(self):
        with self.assertRaises(FetchError) as caught:lookup_page(LookupPage('<p>No results</p>'))
        self.assertIn('not proof of missing COAs',str(caught.exception))
    def test_supplier_redirect_diagnostics_are_safe(self):
        for reason,code in [('Refused URL outside the configured HTTPS source hosts','source_redirect_rejected'),('Source requires sign-in; no unattended authorization is configured','source_sign_in_required')]:
            output=public_summary({'status':'partial','checks':[{'component':'supplier','status':'blocked','detail':{'reason':reason}}]})
            self.assertIn(code,output)
    def test_rate_cooldown_persists_and_other_sources_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);imports=root/'imports';imports.mkdir()
            (imports/'guide.json').write_text(json.dumps(document()));(imports/'folder.json').write_text('{"files":[]}')
            db=connect(root/'reference.sqlite');calls=[]
            def catalog():calls.append('catalog');raise FetchError('Source returned HTTP 429',429,3600)
            supplier=lambda:{'values':[['Name'],['Example 10mg','B','https://janoshik.com/tests/synthetic']]}
            services={'catalog':catalog,'supplier':supplier,'freedom':lambda db:{'status':'completed','records':[]}}
            with patch('gloformulas.daily.schedule_status',return_value={'daily_schedule_confirmed':False}):
                first=daily(db,root,True,services);second=daily(db,root,True,services)
            self.assertEqual(calls,['catalog']);self.assertEqual([c['status'] for c in second['checks'] if c['component']=='catalog'],['deferred']);self.assertEqual(second['status'],'partial')
            self.assertTrue(db.execute('SELECT 1 FROM supplier_snapshots').fetchone());self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0],1)
            self.assertIn('deferred',public_summary(second));db.close()
            # The next job opens the same encrypted-carried-forward database; cooldown survives reopening.
            db=connect(root/'reference.sqlite');self.assertTrue(db.execute("SELECT 1 FROM source_backoff WHERE component='catalog'").fetchone())
            db.execute("UPDATE source_backoff SET retry_at='2000-01-01T00:00:00+00:00'");db.commit()
            with patch('gloformulas.daily.schedule_status',return_value={'daily_schedule_confirmed':False}):daily(db,root,True,services)
            self.assertEqual(calls,['catalog','catalog']);db.close()
