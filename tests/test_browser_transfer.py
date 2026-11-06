import base64
import copy
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from gloformulas.core import connect
from gloformulas.importers import import_guide
from gloformulas.monitor_state import StateError,restore_state
from gloformulas.browser_transfer import prepare,cloud_encrypt,complete,publish_transfer
from test_engine import document

KEY='synthetic-transfer-test-key-never-a-real-secret-123456'
REPO='owner/repo';SESSION='12345-1'

@unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
class BrowserTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.state=self.root/'state';self.state.mkdir()
        db=connect(self.state/'reference.sqlite');import_guide(db,document());db.commit();db.close()
        (self.state/'source-config.json').write_text('{"GUIDE_URL":"https://docs.google.com/document/d/PRIVATE-SOURCE-ID/edit"}')
        self.keys=self.root/'keys';self.manifest=prepare(REPO,SESSION,KEY,self.keys)
    def tearDown(self):self.temp.cleanup()
    def encrypted(self):return cloud_encrypt(self.state,self.manifest,REPO,SESSION,expected_products=1)
    def finish(self,body,**kwargs):
        return complete(body,self.manifest,(self.keys/'transfer-private.enc').read_bytes(),kwargs.pop('secret',KEY),kwargs.pop('repository',REPO),kwargs.pop('session',SESSION),self.root/'monitor-seed.enc',expected_products=kwargs.pop('expected_products',1),**kwargs)
    def test_prepare_writes_no_plaintext_private_key(self):
        self.assertEqual({p.name for p in self.keys.iterdir()},{'transfer-public.json','transfer-private.enc'})
        self.assertNotIn('PRIVATE KEY',(self.keys/'transfer-public.json').read_text())
        self.assertNotIn(b'PRIVATE KEY',(self.keys/'transfer-private.enc').read_bytes())
    def test_cloud_encrypt_needs_no_repository_secret_and_preserves_records(self):
        with patch.dict(os.environ,{'GLO_STATE_KEY':''}):body=self.encrypted()
        self.assertNotIn(b'PRIVATE-SOURCE-ID',body);self.assertNotIn(b'SQLite',body);self.assertNotIn(KEY.encode(),body)
        self.assertEqual(self.finish(body)['products'],1)
        restored=self.root/'restored';restore_state((self.root/'monitor-seed.enc').read_bytes(),KEY,restored)
        with sqlite3.connect(restored/'reference.sqlite') as db:self.assertEqual(db.execute('SELECT codename FROM products').fetchone()[0],'EX')
    def test_exact_expected_count_required_no_empty_replacement(self):
        with self.assertRaises(StateError):cloud_encrypt(self.state,self.manifest,REPO,SESSION)
        body=self.encrypted()
        with self.assertRaises(StateError):self.finish(body,expected_products=46)
        self.assertFalse((self.root/'monitor-seed.enc').exists())
    def test_wrong_secret_cannot_create_seed(self):
        with self.assertRaises(StateError):self.finish(self.encrypted(),secret=KEY+'wrong')
        self.assertFalse((self.root/'monitor-seed.enc').exists())
    def test_ciphertext_tampering_stops_seed_creation(self):
        envelope=json.loads(self.encrypted());data=bytearray(base64.b64decode(envelope['ciphertext']));data[-1]^=1;envelope['ciphertext']=base64.b64encode(data).decode()
        with self.assertRaises(StateError):self.finish(json.dumps(envelope).encode())
    def test_wrong_repository_and_session_blocked(self):
        for repository,session in [('other/repo',SESSION),(REPO,'99999-1')]:
            with self.assertRaises(StateError):cloud_encrypt(self.state,self.manifest,repository,session,expected_products=1)
    def test_expired_session_refuses_encryption_and_finish(self):
        future=datetime.now(timezone.utc)+timedelta(days=8)
        with self.assertRaises(StateError):cloud_encrypt(self.state,self.manifest,REPO,SESSION,expected_products=1,moment=future)
        with self.assertRaises(StateError):self.finish(self.encrypted(),moment=future)
    def test_mixed_private_recipient_refused(self):
        other=self.root/'other';prepare(REPO,'54321-1',KEY,other)
        with self.assertRaises(StateError):complete(self.encrypted(),self.manifest,(other/'transfer-private.enc').read_bytes(),KEY,REPO,SESSION,self.root/'bad.enc',expected_products=1)
    def test_existing_seed_not_overwritten(self):
        path=self.root/'monitor-seed.enc';path.write_bytes(b'existing')
        with self.assertRaises(StateError):self.finish(self.encrypted())
        self.assertEqual(path.read_bytes(),b'existing')
    def test_session_keys_not_overwritten(self):
        with self.assertRaises(StateError):prepare(REPO,SESSION,KEY,self.keys)
    def test_publish_refuses_plaintext_or_extra_fields(self):
        envelope=json.loads(self.encrypted());envelope['raw_database']='DO_NOT_UPLOAD'
        with self.assertRaises(StateError):publish_transfer(self.root,json.dumps(envelope).encode(),REPO,SESSION)
        with self.assertRaises(Exception):publish_transfer(self.root,b'SQLite format 3',REPO,SESSION)
    def test_publish_isolated_index_changes_only_ciphertext_path(self):
        import subprocess
        commands=[];body=self.encrypted()
        def git(args,**kw):
            commands.append((args,kw))
            op=args[1]
            result={'remote':'https://github.com/'+REPO+'.git','ls-remote':'','rev-parse':'a'*40,'hash-object':'b'*40,'write-tree':'c'*40,'commit-tree':'d'*40,'diff-tree':'transfer/database.glo-transfer.json'}.get(op,'')
            return subprocess.CompletedProcess(args,0,result.encode(),b'')
        with patch('subprocess.run',side_effect=git):result=publish_transfer(self.root,body,REPO,SESSION)
        self.assertTrue(result['encrypted_only'])
        hashed=[k['input'] for a,k in commands if a[1]=='hash-object'];self.assertEqual(hashed,[body])
        self.assertFalse(any('add' in a or 'checkout' in a for a,k in commands))
        push=[a for a,k in commands if a[1]=='push'][0];self.assertEqual(push[-1],'d'*40+':refs/heads/seed-transfer/'+SESSION)

class TransferWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('yaml'),'Install test extra')
    def test_browser_only_workflow_main_only_and_no_plaintext_upload(self):
        import yaml
        text=Path('.github/workflows/seed-transfer.yml').read_text();wf=yaml.load(text,Loader=yaml.BaseLoader)
        self.assertEqual(list(wf['on']),['workflow_dispatch']);self.assertEqual(wf['jobs']['transfer']['if'],"github.ref == 'refs/heads/main'")
        self.assertNotIn('pull_request',text);self.assertNotIn('upload-artifact',text);self.assertNotIn('--clobber',text.replace('# No --clobber: an existing seed asset must never be overwritten silently.',''))
        uploads=[step['run'] for step in wf['jobs']['transfer']['steps'] if 'run' in step and 'gh release upload' in step['run']]
        self.assertEqual(len(uploads),2)
        for upload in uploads:self.assertNotIn('reference.sqlite',upload);self.assertNotIn('state.zip',upload)
