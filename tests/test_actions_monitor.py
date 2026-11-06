import importlib.util
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from gloformulas.core import connect
from gloformulas.importers import import_guide
from gloformulas.actions_monitor import public_summary,restore_for_actions,run_monitor,finalize,stamp,private_config,password
from gloformulas.actions_store import GitHubStore,ARTIFACT,WORKFLOW
from gloformulas.monitor_state import pack_state,restore_state,encrypt,decrypt,StateError
from test_engine import document

KEY='synthetic-test-key-only-not-a-real-secret-1234'

class MemoryStore(GitHubStore):
    def __init__(self,artifacts=None,runs=None,body=None):
        super().__init__('owner/repo','synthetic-job-token')
        self.artifacts=artifacts or [];self.runs=runs or {};self.body=body;self.requests=[]
    def request(self,path,binary=False):
        self.requests.append(path)
        if '/actions/artifacts?' in path:return {'artifacts':self.artifacts}
        if '/actions/runs/' in path:return self.runs[int(path.rsplit('/',1)[1])]
        if '/zip' in path:return self.body
        raise AssertionError('Unexpected fixture request')

class ActionsTests(unittest.TestCase):
    def test_catalog_tracks_changed_coa_links_and_keeps_review_flag(self):
        from gloformulas.catalog import import_catalog
        from gloformulas.live_catalog import coa_links
        base={'complete':True,'products':[{'key':'x','name':'Example','url':'https://gloformulas.com/product/x','sizes':['10mg'],'coa_links':['https://gloformulas.com/a.pdf']}]}
        with tempfile.TemporaryDirectory() as tmp:
            db=connect(Path(tmp)/'db.sqlite');import_catalog(db,base)
            base['products'][0]['coa_links']=['https://gloformulas.com/b.pdf']
            changes=import_catalog(db,base)
            self.assertEqual([c[1] for c in changes],['coa_link_change'])
            self.assertEqual(db.execute("SELECT count(*) FROM flags WHERE code='unverified_catalog_coa_link'").fetchone()[0],2);db.close()
        links=coa_links('<a href="https://drive.google.com/file/d/synthetic/view"><span>Certificate of Analysis</span></a>','https://gloformulas.com/product/x')
        self.assertEqual(len(links),1)
    def test_setup_summary_lists_sources_not_checked(self):
        summary=public_summary({'status':'setup_required','setup_issue':'baseline_restore','checks':[{'component':'catalog','status':'not_attempted'}]})
        self.assertIn('Setup error: baseline_restore',summary);self.assertIn('catalog: **not_attempted**',summary)
    def test_mountain_schedule_winter_summer_and_dst_dates(self):
        zone=ZoneInfo('America/Boise')
        for day,hour in [('2026-01-15',15),('2026-07-15',14),('2026-03-08',14),('2026-11-01',15)]:
            local=datetime.fromisoformat(day+'T08:00:00').replace(tzinfo=zone)
            self.assertEqual(local.astimezone(timezone.utc).hour,hour)
        self.assertIn('2026-01-15_080000_MST',stamp(datetime(2026,1,15,15,tzinfo=timezone.utc)))
    def test_public_summary_never_echoes_source_details(self):
        poison='PRIVATE_NAME https://private.example TOKEN=secret'
        result={'status':poison,'changes':{'catalog_changes':3},'checks':[{'component':'catalog','status':'blocked','detail':{'reason':poison}},{'component':poison,'status':poison}]}
        output=public_summary(result)
        self.assertNotIn('PRIVATE_NAME',output);self.assertNotIn('private.example',output);self.assertNotIn('TOKEN=',output);self.assertIn('Catalog changes: 3',output);self.assertIn('catalog: **blocked**',output)
    def test_manual_run_does_not_claim_scheduled_execution_observed(self):
        from gloformulas.daily import schedule_status
        with patch.dict(os.environ,{'GLO_ACTIONS_MONITOR':'1','GITHUB_EVENT_NAME':'workflow_dispatch'}):r=schedule_status()
        self.assertFalse(r['daily_schedule_confirmed']);self.assertEqual(r['provider'],'github_actions')
        with patch.dict(os.environ,{'GLO_ACTIONS_MONITOR':'1','GITHUB_EVENT_NAME':'schedule'}):self.assertTrue(schedule_status()['daily_schedule_confirmed'])
    def test_missing_secret_fails_without_prompt_in_actions(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GLO_STATE_KEY':''}),patch('getpass.getpass',side_effect=AssertionError('No prompt on runner')):
            with self.assertRaises(StateError):password()
    def test_untrusted_pr_artifacts_ignored(self):
        artifacts=[{'id':8,'name':ARTIFACT,'workflow_run':{'id':2,'head_branch':'main'}}]
        runs={2:{'id':2,'path':WORKFLOW,'head_branch':'main','head_repository':{'full_name':'owner/repo'},'event':'pull_request'}}
        store=MemoryStore(artifacts,runs);self.assertIsNone(store.latest_state());self.assertFalse(any('/zip' in p for p in store.requests))
    def test_trusted_failed_run_state_is_reused(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('state.enc',b'encrypted-fixture')
        artifacts=[{'id':8,'name':ARTIFACT,'expired':False,'workflow_run':{'id':2,'head_branch':'main'}}]
        runs={2:{'id':2,'path':WORKFLOW,'head_branch':'main','head_repository':{'full_name':'owner/repo'},'event':'workflow_dispatch','conclusion':'failure'}}
        store=MemoryStore(artifacts,runs,data.getvalue());self.assertEqual(store.latest_state(),b'encrypted-fixture')
    def test_expired_history_does_not_reset_database(self):
        artifacts=[{'id':8,'name':ARTIFACT,'expired':True,'workflow_run':{'id':2,'head_branch':'main'}}]
        runs={2:{'id':2,'path':WORKFLOW,'head_branch':'main','head_repository':{'full_name':'owner/repo'},'event':'schedule'}}
        with self.assertRaises(StateError):MemoryStore(artifacts,runs).latest_state()
    def test_wrong_workflow_state_ignored(self):
        artifacts=[{'id':8,'name':ARTIFACT,'workflow_run':{'id':2,'head_branch':'main'}}]
        runs={2:{'id':2,'path':'.github/workflows/test.yml','head_branch':'main','head_repository':{'full_name':'owner/repo'},'event':'workflow_dispatch'}}
        self.assertIsNone(MemoryStore(artifacts,runs).latest_state())
    def test_storage_redirect_cannot_exfiltrate_token(self):
        store=GitHubStore('owner/repo','synthetic-token')
        with self.assertRaises(StateError):store.download_redirect('https://evil.example/state.zip')
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*a):self.close()
        with patch.object(store.opener,'open',return_value=Reply(b'encrypted')) as opened:
            store.download_redirect('https://results.blob.core.windows.net/signed')
        self.assertNotIn('Authorization',opened.call_args.args[0].headers)
    @unittest.skipUnless(importlib.util.find_spec('yaml'),'Install test extra for YAML validation')
    def test_workflow_schedule_security_and_manual_trigger(self):
        import yaml
        wf=yaml.load(Path('.github/workflows/daily-monitor.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(wf['on']['schedule'],[{'cron':'0 8 * * *','timezone':'America/Boise'}]);self.assertIn('workflow_dispatch',wf['on'])
        self.assertNotIn('pull_request',wf['on']);self.assertEqual(wf['permissions'],{'contents':'read','actions':'read'})
        self.assertEqual(wf['concurrency']['cancel-in-progress'],'false')
        text=Path('.github/workflows/daily-monitor.yml').read_text();self.assertNotIn('publish-plan',text);self.assertNotIn('google-sync',text);self.assertIn("github.ref == 'refs/heads/main'",text)
        uploads=[s for s in wf['jobs']['monitor']['steps'] if s.get('uses','').startswith('actions/upload-artifact')]
        self.assertEqual(len(uploads),2)
        for step in uploads:self.assertNotIn('reference.sqlite',step['with']['path']);self.assertNotIn('source-config',step['with']['path'])

@unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra for encryption tests')
class EncryptedStateTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{},clear=False);self.environment.start()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.seed=self.root/'seed';self.seed.mkdir()
        db=connect(self.seed/'reference.sqlite');import_guide(db,document());db.commit();db.close()
        (self.seed/'source-config.json').write_text(json.dumps({'GUIDE_URL':'https://docs.google.com/document/d/PRIVATE-ID/edit','GOOGLE_READ_SYNC':True}))
    def tearDown(self):self.temp.cleanup();self.environment.stop()
    def test_seed_roundtrip_preserves_database_and_excludes_credentials(self):
        (self.seed/'credentials.json').write_text('DO_NOT_INCLUDE')
        body=pack_state(self.seed,KEY)
        self.assertNotIn(b'SQLite',body);self.assertNotIn(b'PRIVATE-ID',body)
        with zipfile.ZipFile(io.BytesIO(decrypt(body,KEY))) as z:self.assertNotIn('credentials.json',z.namelist())
        restored=self.root/'restored';restore_state(body,KEY,restored)
        db=connect(restored/'reference.sqlite');self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0],1);db.close()
    def test_wrong_key_and_tampering_do_not_write(self):
        body=pack_state(self.seed,KEY)
        for data,key in [(body,KEY+'wrong'),(body[:-1]+bytes([body[-1]^1]),KEY)]:
            dest=self.root/'restore'
            with self.assertRaises(StateError):restore_state(data,key,dest)
            self.assertFalse(dest.exists())
    def test_archive_path_traversal_refused(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('../evil','payload')
        with self.assertRaises(StateError):restore_state(encrypt(buffer.getvalue(),KEY),KEY,self.root/'restored')
    def test_no_implicit_new_baseline(self):
        class Store:
            def latest_state(self):return None
            def seed(self,tag):raise AssertionError('Seed must require manual bootstrap')
        with self.assertRaises(StateError):restore_for_actions(self.root/'restore',KEY,Store())
    def test_manual_seed_restoration_forces_google_sync_off(self):
        body=pack_state(self.seed,KEY)
        class Store:
            def latest_state(self):return None
            def seed(self,tag):return body
        result=restore_for_actions(self.root/'restore',KEY,Store(),True)
        self.assertEqual(result['origin'],'encrypted_seed_release');self.assertFalse(json.loads((self.root/'restore/source-config.json').read_text())['GOOGLE_READ_SYNC'])
    def test_existing_state_wins_over_seed_even_when_bootstrap_selected(self):
        body=pack_state(self.seed,KEY)
        class Store:
            def latest_state(self):return body
            def seed(self,tag):raise AssertionError('Never reset existing state')
        result=restore_for_actions(self.root/'restore',KEY,Store(),True);self.assertEqual(result['origin'],'previous_encrypted_artifact')
    def test_partial_run_still_seals_state_and_private_report(self):
        out=self.root/'out'
        def runner(db,root,live):
            self.assertTrue(live)
            print('PRIVATE_RUN_OUTPUT')
            return {'status':'partial','run_id':'synthetic','changes':{'flags':1},'checks':[{'component':'catalog','status':'blocked','detail':{'reason':'PRIVATE_ERROR'}}]}
        code=run_monitor(self.seed,out,runner)
        self.assertEqual(code,2)
        finalize(self.seed,out,KEY)
        self.assertTrue((out/'state.enc').is_file());summary=(out/'summary.md').read_text();self.assertNotIn('PRIVATE_ERROR',summary)
        with zipfile.ZipFile(io.BytesIO(decrypt((out/'detail.enc').read_bytes(),KEY,'report'))) as z:
            self.assertIn('PRIVATE_ERROR',z.read('report.json').decode());self.assertIn('PRIVATE_RUN_OUTPUT',z.read('private-run.log').decode())
    def test_interrupted_run_never_reuses_old_report_as_current(self):
        reports=self.seed/'reports';reports.mkdir();(reports/'latest.json').write_text('{"run_id":"old"}');(reports/'latest.md').write_text('OLD_PRIVATE_REPORT')
        out=self.root/'out';out.mkdir();(out/'monitor-result.json').write_text('{"status":"running"}')
        finalize(self.seed,out,KEY)
        with zipfile.ZipFile(io.BytesIO(decrypt((out/'detail.enc').read_bytes(),KEY,'report'))) as z:
            self.assertNotIn('changes.md',z.namelist());self.assertEqual(json.loads(z.read('report.json'))['status'],'interrupted')
    def test_unsupported_config_keys_cannot_bundle_tokens(self):
        (self.seed/'source-config.json').write_text('{"access_token":"DO_NOT_INCLUDE"}')
        with self.assertRaises(StateError):pack_state(self.seed,KEY)
    def test_valid_but_empty_database_cannot_seed_monitoring(self):
        empty=self.root/'empty-valid';empty.mkdir();db=connect(empty/'reference.sqlite');db.close();(empty/'source-config.json').write_text('{}')
        with self.assertRaises(StateError):pack_state(empty,KEY)
    def test_report_ciphertext_cannot_be_restored_as_state(self):
        body=encrypt(b'synthetic private report',KEY,'report')
        with self.assertRaises(StateError):restore_state(body,KEY,self.root/'restore')
    def test_empty_database_not_silently_seeded(self):
        empty=self.root/'empty';empty.mkdir()
        with self.assertRaises(StateError):pack_state(empty,KEY)
    def test_weak_key_refused(self):
        with self.assertRaises(StateError):pack_state(self.seed,'short')
