"""Offline transport and encrypted-state regression tests; no live monitor calls."""
import io
import json
import os
import importlib.util
import tempfile
import unittest
import urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from gloformulas.http import Client,FetchError,Response,Redirects,retry_after_seconds
from gloformulas.catalog_http import CatalogClient,register_limit,reset_limits,robots_interval
from gloformulas.live_catalog import CatalogCrawler
from gloformulas.catalog import import_catalog
from gloformulas.core import connect,encoded
from gloformulas.daily import daily
from gloformulas.actions_monitor import public_summary,finalize,run_monitor
from test_engine import document

BASE='https://gloformulas.com'

class Reply(io.BytesIO):
    def __init__(self,url,body=b'User-agent: *\nAllow: /',status=200,headers=None):
        super().__init__(body);self.url=url;self.status=status;self.headers=headers or {}

def limited(url,headers=None):return urllib.error.HTTPError(url,429,'PRIVATE_ERROR',headers or {},None)

class CatalogTransportTests(unittest.TestCase):
    def test_first_429_stops_without_retry_or_fallback(self):
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        with patch.object(client.opener,'open',side_effect=limited(BASE+'/robots.txt')) as opened,patch('gloformulas.http.time.sleep'):
            with self.assertRaises(FetchError) as caught:crawler.run()
            self.assertEqual(opened.call_count,1)
            with self.assertRaises(FetchError):crawler.fetch('/')
            self.assertEqual(opened.call_count,1)
        trace=caught.exception.catalog_diagnostics
        self.assertEqual((trace['stage'],trace['request_count'],trace['http_status']),('robots',1,429))
    def test_later_stages_identified_and_scan_aborted(self):
        for stage in ('homepage','listing','sitemap','product_page'):
            client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
            with patch.object(client.opener,'open',side_effect=[Reply(BASE+'/robots.txt'),limited(BASE+'/PRIVATE-PRODUCT?secret=PRIVATE')]) as opened,patch('gloformulas.http.time.sleep'):
                crawler.robots()
                with self.assertRaises(FetchError) as caught:crawler.fetch('/PRIVATE-PRODUCT?secret=PRIVATE',stage)
            self.assertEqual(opened.call_count,2)
            self.assertEqual(caught.exception.catalog_diagnostics['stage'],stage)
            self.assertEqual(caught.exception.catalog_diagnostics['request_count'],2)
    def test_numeric_and_http_date_retry_after(self):
        future=datetime.now(timezone.utc)+timedelta(days=10)
        for value,minimum in [('600',600),(' '*300+'864000',864000),(future.strftime('%a, %d %b %Y %H:%M:%S GMT'),9*86400)]:
            client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
            with patch.object(client.opener,'open',side_effect=limited(BASE+'/robots.txt',{'Retry-After':value})),patch('gloformulas.http.time.sleep'):
                with self.assertRaises(FetchError) as caught:crawler.run()
            self.assertGreaterEqual(caught.exception.retry_after_seconds,minimum)
    def test_retry_date_rounds_up_for_catalog_only(self):
        instant=datetime(2026,1,1,0,0,0,500000,tzinfo=timezone.utc)
        value='Thu, 01 Jan 2026 00:00:02 GMT'
        self.assertEqual(retry_after_seconds(value,instant),1)
        self.assertEqual(retry_after_seconds(value,instant,round_up=True),2)
    def test_missing_invalid_zero_and_past_header_do_not_retry(self):
        for value in ('','invalid','0','Thu, 01 Jan 1970 00:00:00 GMT'):
            client=CatalogClient({'gloformulas.com'})
            with patch.object(client.opener,'open',side_effect=limited(BASE+'/robots.txt',{'Retry-After':value})) as opened,patch('gloformulas.http.time.sleep'):
                with self.assertRaises(FetchError):CatalogCrawler(client).run()
            self.assertEqual(opened.call_count,1)
    def test_normal_clients_keep_existing_retry_defaults(self):
        client=Client({'example.org'})
        self.assertEqual(client.interval,.25);self.assertEqual(client.attempts,3)
        with patch.object(client.opener,'open',side_effect=limited('https://example.org')) as opened,patch('gloformulas.http.time.sleep'):
            with self.assertRaises(FetchError):client.get('https://example.org')
        self.assertEqual(opened.call_count,3)
    def test_pacing_applies_between_requests(self):
        client=CatalogClient({'gloformulas.com'})
        with patch('gloformulas.http.time.monotonic',return_value=100),patch('gloformulas.http.time.sleep') as slept,patch.object(client.opener,'open',side_effect=lambda req,**kw:Reply(req.full_url)):
            client.get(BASE+'/robots.txt');client.get(BASE+'/')
        self.assertEqual(slept.call_args.args[0],5)
    def test_matching_robots_pacing_and_request_rate(self):
        content='User-agent: *\nCrawl-delay: 2\nRequest-rate: 1/20\nDisallow: /private'
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        with patch.object(client.opener,'open',return_value=Reply(BASE+'/robots.txt',content.encode())) as opened,patch('gloformulas.http.time.sleep'):
            crawler.robots();self.assertEqual(client.interval,20)
            with self.assertRaises(FetchError):crawler.fetch('/private')
        self.assertEqual(opened.call_count,1)
    def test_specific_groups_decimal_delays_and_invalid_rules(self):
        from gloformulas.http import AGENT
        rules='User-agent: *\nCrawl-delay: 40\n\nUser-agent: GloFormulasReference\nCrawl-delay: 10.5\nRequest-rate: 2/30'
        self.assertEqual(robots_interval(rules,AGENT),15)
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        with patch.object(client.opener,'open',return_value=Reply(BASE+'/robots.txt',rules.encode())),patch('gloformulas.http.time.sleep'):
            crawler.robots()
        self.assertEqual(client.interval,15)
        for rule in ('Crawl-delay: -1','Crawl-delay: NaN','Request-rate: 0/10','Request-rate: bad'):
            with self.assertRaises(FetchError):robots_interval('User-agent: *\n'+rule,AGENT)
    def test_long_robot_delay_stops_without_early_request(self):
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        with patch.object(client.opener,'open',return_value=Reply(BASE+'/robots.txt',b'User-agent: *\nCrawl-delay: 120')) as opened,patch('gloformulas.http.time.sleep'):
            crawler.robots()
            with self.assertRaises(FetchError):crawler.fetch('/')
        self.assertEqual(opened.call_count,1)
    def test_redirect_disallow_is_checked_before_redirect_request(self):
        import urllib.request
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        handler=next(h for h in client.opener.handlers if isinstance(h,Redirects))
        with patch.object(client.opener,'open',return_value=Reply(BASE+'/robots.txt',b'User-agent: *\nDisallow: /private')),patch('gloformulas.http.time.sleep'):
            crawler.robots()
            with self.assertRaises(FetchError):handler.redirect_request(urllib.request.Request(BASE+'/'),None,302,'Found',{},BASE+'/private')
        self.assertEqual(client.request_count,1)
    def test_redirect_attempts_are_counted_paced_and_policy_checked(self):
        import urllib.request
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        handler=next(h for h in client.opener.handlers if isinstance(h,Redirects))
        with patch('gloformulas.http.time.sleep') as slept:
            client.before_request(BASE+'/')
            request=handler.redirect_request(urllib.request.Request(BASE+'/'),None,302,'Found',{},BASE+'/next')
        self.assertEqual(request.full_url,BASE+'/next');self.assertEqual(client.request_count,2)
        self.assertGreater(slept.call_args.args[0],0)
        with self.assertRaises(FetchError):handler.redirect_request(urllib.request.Request(BASE+'/'),None,302,'Found',{},'https://evil.example/')
    def test_response_header_allowlist_and_public_privacy(self):
        client=CatalogClient({'gloformulas.com'});crawler=CatalogCrawler(client)
        headers={'Retry-After':'120','Set-Cookie':'PRIVATE_COOKIE','Authorization':'PRIVATE_TOKEN','Server':'PRIVATE_SERVER'}
        with patch.object(client.opener,'open',side_effect=limited(BASE+'/PRIVATE-PATH?secret=PRIVATE_QUERY',headers)),patch('gloformulas.http.time.sleep'):
            with self.assertRaises(FetchError) as caught:crawler.run()
        trace=caught.exception.catalog_diagnostics
        private=json.dumps(trace);self.assertNotIn('PRIVATE_COOKIE',private);self.assertNotIn('PRIVATE_TOKEN',private)
        self.assertIn('PRIVATE-PATH',private);self.assertIn('PRIVATE_SERVER',private)
        output=public_summary({'status':'partial','checks':[{'component':'catalog','status':'blocked','detail':{'reason':'Source returned HTTP 429','catalog_diagnostics':trace,'retry_at':'2026-11-10T15:00:00+00:00','consecutive_429':2,'cooldown_seconds':86400}}]})
        self.assertNotIn('PRIVATE',output);self.assertIn('stage: robots',output);self.assertIn('attempts (including redirects): 1',output)
        self.assertIn('America/Boise',output);self.assertIn('429',output)
    def test_malformed_public_diagnostics_cannot_inject_text(self):
        detail={'catalog_diagnostics':{'stage':'PRIVATE','request_count':'PRIVATE','http_status':'PRIVATE'},'retry_at':'PRIVATE','cooldown_seconds':'PRIVATE','consecutive_429':'PRIVATE'}
        output=public_summary({'status':'partial','checks':[{'component':'catalog','status':'blocked','detail':detail}]})
        self.assertNotIn('PRIVATE',output)

class CatalogStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=connect(self.root/'reference.sqlite')
        imports=self.root/'imports';imports.mkdir()
        (imports/'guide.json').write_text(json.dumps(document()));(imports/'folder.json').write_text('{"files":[]}')
        self.baseline={'complete':True,'products':[{'key':'a','name':'A','url':BASE+'/product/a','sizes':['5mg']}]}
        import_catalog(self.db,self.baseline);self.db.commit()
        self.services={'supplier':lambda:{'values':[['Name'],['Example 10mg','B','https://janoshik.com/tests/synthetic']]},'freedom':lambda db:{'status':'completed','records':[]}}
    def tearDown(self):self.db.close();self.temp.cleanup()
    def run_daily(self):
        with patch('gloformulas.daily.schedule_status',return_value={'daily_schedule_confirmed':False}):return daily(self.db,self.root,True,self.services)
    def test_increasing_bounded_backoff_and_positive_jitter(self):
        now=datetime.now(timezone.utc)
        delays=[]
        for _ in range(8):delays.append(register_limit(self.db,None,now,jitter=lambda a,b:b)['cooldown_seconds'])
        self.assertEqual(delays,sorted(delays));self.assertLessEqual(max(delays),7*86400)
        self.assertGreater(delays[0],86400)
        long=register_limit(self.db,14*86400,now,jitter=lambda a,b:0)
        self.assertEqual(long['cooldown_seconds'],14*86400)
    def test_reopening_preserves_streak_and_cooldown(self):
        register_limit(self.db,None,jitter=lambda a,b:0);self.db.commit();self.db.close()
        self.db=connect(self.root/'reference.sqlite')
        self.assertEqual(register_limit(self.db,None,jitter=lambda a,b:0)['consecutive_429'],2)
    def test_additive_upgrade_preserves_old_catalog_and_existing_cooldown(self):
        retry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        self.db.execute('DROP TABLE catalog_rate_state');self.db.execute('DELETE FROM schema_version WHERE version=4')
        self.db.execute("INSERT INTO source_backoff VALUES('catalog',?,'HTTP 429')",(retry,))
        self.db.commit();self.db.close();self.db=connect(self.root/'reference.sqlite')
        self.assertEqual(self.db.execute('SELECT raw_json FROM catalog_snapshots').fetchone()[0],encoded(self.baseline))
        self.assertEqual(self.db.execute('SELECT retry_at FROM source_backoff').fetchone()[0],retry)
        self.assertEqual(self.db.execute('SELECT count(*) FROM catalog_rate_state').fetchone()[0],0)
        self.assertTrue(self.db.execute('SELECT 1 FROM schema_version WHERE version=4').fetchone())
    def test_deferred_run_has_zero_attempts_and_keeps_baseline(self):
        def blocked():raise FetchError('Source returned HTTP 429',429,120)
        self.services['catalog']=blocked
        first=self.run_daily();second=self.run_daily()
        check=next(c for c in second['checks'] if c['component']=='catalog')
        self.assertEqual(check['status'],'deferred');self.assertEqual(check['detail']['catalog_diagnostics']['request_count'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM catalog_snapshots').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM catalog_changes').fetchone()[0],0)
        self.assertEqual(next(c for c in first['checks'] if c['component']=='supplier')['status'],'checked')
        self.assertEqual(next(c for c in second['checks'] if c['component']=='freedom')['status'],'completed')
    def test_failed_and_duplicate_scans_preserve_baseline_and_streak(self):
        register_limit(self.db,None,jitter=lambda a,b:0)
        self.db.execute("UPDATE source_backoff SET retry_at='2000-01-01T00:00:00+00:00'");self.db.commit()
        for payload in ({'complete':False,'products':[]},{'complete':True,'products':self.baseline['products']*2}):
            self.services['catalog']=lambda p=payload:p
            result=self.run_daily()
            self.assertEqual(next(c for c in result['checks'] if c['component']=='catalog')['status'],'failed')
            self.assertEqual(self.db.execute('SELECT count(*) FROM catalog_snapshots').fetchone()[0],1)
            self.assertEqual(self.db.execute('SELECT consecutive_429 FROM catalog_rate_state').fetchone()[0],1)
    def test_inaccessible_catalog_preserves_saved_import_and_history(self):
        saved=self.root/'imports/catalog.json';saved.write_text(encoded(self.baseline));before=saved.read_bytes()
        def unavailable():raise FetchError('Source connection unavailable')
        self.services['catalog']=unavailable;result=self.run_daily()
        self.assertEqual(saved.read_bytes(),before)
        self.assertEqual(result['changes']['catalog_changes'],0)
        self.assertEqual(self.db.execute('SELECT raw_json FROM catalog_snapshots').fetchone()[0],encoded(self.baseline))
    def test_success_detects_changes_and_resets_backoff(self):
        register_limit(self.db,None,jitter=lambda a,b:0)
        self.db.execute("UPDATE source_backoff SET retry_at='2000-01-01T00:00:00+00:00'");self.db.commit()
        newer=json.loads(json.dumps(self.baseline));newer['products'][0]['sizes']=['10mg']
        newer['products'].append({'key':'b','name':'B','url':BASE+'/product/b','sizes':['1mg']})
        self.services['catalog']=lambda:newer
        result=self.run_daily()
        self.assertEqual(result['changes']['catalog_changes'],2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM catalog_rate_state').fetchone()[0],0)
    def test_extreme_server_delay_does_not_crash_or_early_retry(self):
        result=register_limit(self.db,10**400,jitter=lambda a,b:0)
        self.assertTrue(result['retry_at'].startswith('9999-'))
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_cooldown_survives_existing_encrypted_state_transfer(self):
        from gloformulas.monitor_state import pack_state,restore_state
        (self.root/'source-config.json').write_text('{}')
        register_limit(self.db,None,jitter=lambda a,b:0);self.db.commit()
        dest=self.root/'restored';restore_state(pack_state(self.root,'k'*40),'k'*40,dest)
        with connect(dest/'reference.sqlite') as restored:
            self.assertEqual(restored.execute('SELECT consecutive_429 FROM catalog_rate_state').fetchone()[0],1)
            self.assertTrue(restored.execute("SELECT retry_at FROM source_backoff WHERE component='catalog'").fetchone())
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_sensitive_trace_only_in_encrypted_actions_outputs(self):
        import zipfile
        from gloformulas.monitor_state import decrypt
        (self.root/'source-config.json').write_text('{}')
        out=self.root/'out'
        trace={'stage':'listing','request_count':2,'http_status':429,'requests':[{'url':BASE+'/PRIVATE_PATH?key=PRIVATE_QUERY','headers':{'server':'PRIVATE_SERVER'}}]}
        result={'status':'partial','checks':[{'component':'catalog','status':'blocked','detail':{'reason':'Source returned HTTP 429','catalog_diagnostics':trace}}]}
        with patch.dict(os.environ):
            run_monitor(self.root,out,runner=lambda db,root,live:result)
            finalize(self.root,out,'k'*40)
        self.assertNotIn('PRIVATE', (out/'summary.md').read_text())
        self.assertNotIn(b'PRIVATE', (out/'detail.enc').read_bytes());self.assertNotIn(b'PRIVATE', (out/'state.enc').read_bytes())
        with zipfile.ZipFile(io.BytesIO(decrypt((out/'detail.enc').read_bytes(),'k'*40,'report'))) as archive:
            self.assertIn('PRIVATE_PATH',archive.read('report.json').decode())

if __name__=='__main__':unittest.main()
