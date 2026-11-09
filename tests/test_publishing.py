import json
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gloformulas.core import connect, now, encoded, source
from gloformulas.catalog import import_catalog
from gloformulas.publishing import *
from gloformulas.docs_actions import prepare, apply, settings, gate, reconcile
from gloformulas.monitor_state import decrypt

def document(content='Legacy heading\n\n',revision='r1'):
    blocks=[];index=1
    for line in content.splitlines(True):
        end=index+utf16(line)
        blocks.append({'startIndex':index,'endIndex':end,'paragraph':{
          'elements':[{'startIndex':index,'endIndex':end,'textRun':{'content':line,'textStyle':{}}}],
          'paragraphStyle':{'namedStyleType':'NORMAL_TEXT'}}});index=end
    return {'documentId':'test','revisionId':revision,'tabs':[{'tabProperties':{'tabId':'tab'},'documentTab':{'body':{'content':blocks}}}]}

class Request:
    def __init__(self,result):self.result=result
    def execute(self,**kwargs):return self.result

class API:
    def __init__(self,doc):self.doc=doc;self.calls=0;self.fail=False
    def get(self,**kwargs):return Request(self.doc)
    def batchUpdate(self,**kwargs):
        self.calls+=1
        if self.fail:raise TimeoutError('private detail')
        content=''.join(text(b) for _,blocks in tabs(self.doc) for b in blocks)
        # Fake models text insertion/deletion; live connector test covers actual native API formatting.
        for r in kwargs['body']['requests']:
            if 'deleteContentRange' in r:
                a=r['deleteContentRange']['range'];content=content[:a['startIndex']-1]+content[a['endIndex']-1:]
            if 'insertText' in r:
                a=r['insertText'];i=a['location']['index']-1;content=content[:i]+a['text']+content[i:]
        self.doc=document(content,'r2');return Request({})

class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.out=self.root/'out';self.out.mkdir()
        self.db=connect(self.root/'reference.sqlite');initialize(self.db)
        self.result={'finished_at':now(),'checks':[{'component':'catalog','status':'checked'}]}
        (self.out/'monitor-result.json').write_text(encoded(self.result))
        self.env=patch.dict(os.environ,{'GLO_STATE_KEY':'s'*40,'GLO_DOCS_MODE':'test','GLO_DOCS_ORIGINAL_ID':'original',
            'GLO_DOCS_TEST_ID':'test','GITHUB_REF':'refs/heads/main','GLO_DOCS_BACKUP_UPLOADED':'true'})
        self.env.start()
    def tearDown(self):self.env.stop();self.db.close();self.tmp.cleanup()
    def catalog(self,products=None):
        import_catalog(self.db,{'complete':True,'products':products or [{'key':'a','name':'Listing A','url':'https://gloformulas.com/products/a','sizes':['5 mg']} ]});self.db.commit()
    def test_positive_catalog_and_size_changes(self):
        self.catalog();items=entries(self.db,self.result);self.assertEqual(items['catalog:a']['sizes'],['5 mg'])
        self.catalog([{'key':'a','name':'Listing A','url':'https://gloformulas.com/products/a','sizes':['10 mg']}])
        self.assertEqual(entries(self.db,self.result)['catalog:a']['sizes'],['10 mg'])
    def coa(self):
        from gloformulas.verification import record_report
        body=b'%PDF synthetic retained test artifact';sha=hashlib.sha256(body).hexdigest()
        url='https://coas.freedomdiagnosticstesting.com/GLOTEST.pdf'
        sid=source(self.db,'freedom_primary_report',url,{'sha256':sha})
        self.db.execute('INSERT INTO products VALUES(?,?,?,?,?,?)',('p','Test peptide','TEST','reviewed',sid,'Test heading'))
        self.db.execute('INSERT INTO report_artifacts VALUES(?,?,?,?,?,?)',(sha,body,'application/pdf',url,now(),len(body)))
        report={'product_id':'p','identity':'Test peptide','purity':'99%','measured_content':'5','content_unit':'mg',
          'nominal_content':'5','nominal_unit':'mg','lab':'Freedom Diagnostics','report_date':now()[:10],
          'batch':'B1','search_code':'GLOTEST','lab_authenticated':True,'vendor_batch_matched':True,
          'reviewer':'Synthetic reviewer','verification_artifact':sid,'artifact_sha256':sha}
        record_report(self.db,report,url)
        return {'finished_at':now(),'checks':[{'component':'freedom','status':'completed'}]},sha
    def test_only_reviewed_authenticated_coa_publishes_with_batch_units(self):
        result,sha=self.coa();items=entries(self.db,result)
        item=next(iter(items.values()));self.assertEqual(item['fields']['batch'],'B1');self.assertEqual(item['fields']['content_unit'],'mg')
    def test_coa_extraction_flags_and_pending_identity_block(self):
        result,sha=self.coa();self.db.execute("UPDATE products SET identity_status='imported'")
        self.assertEqual(entries(self.db,result),{})
        self.db.execute("UPDATE products SET identity_status='reviewed'")
        self.db.execute('INSERT INTO flags VALUES(?,?,?,?,?,?)',('flag','report_artifact',sha,'unreadable','Needs review','open'))
        self.assertEqual(entries(self.db,result),{})
    def test_missing_or_changed_retained_pdf_blocks_coa(self):
        result,sha=self.coa();self.db.execute("UPDATE report_artifacts SET content=?",(b'corrupted',))
        self.assertEqual(entries(self.db,result),{})
    def test_supplier_and_unverified_coas_do_not_publish(self):
        result,sha=self.coa()
        for attr in ('supplier','unverified'):
            self.db.execute('UPDATE lab_results SET attribution=?',(attr,));self.assertEqual(entries(self.db,result),{})
    def test_existing_coa_is_withdrawn_if_it_becomes_uncertain(self):
        result,sha=self.coa();items=entries(self.db,result)
        for ident,item in items.items():self.db.execute('INSERT INTO publish_entries VALUES(?,?)',(ident,encoded(item)))
        self.db.execute("UPDATE lab_results SET verification_status='mismatch'")
        self.assertEqual(entries(self.db,{'checks':[]}),{})
    def test_failed_and_replayed_catalog_never_remove_or_update(self):
        self.db.execute('INSERT INTO publish_entries VALUES(?,?)',('catalog:old',encoded({'kind':'catalog','name':'Old','sizes':['1 mg'],'source':'https://gloformulas.com/p','observed':'2026-01-01'})))
        self.catalog()
        for status in ('blocked','failed','deferred','replayed','partial'):
            result={'finished_at':now(),'checks':[{'component':'catalog','status':status}]}
            self.assertEqual(set(entries(self.db,result)),{'catalog:old'})
    def test_absence_never_removes(self):
        self.catalog();items=entries(self.db,self.result)
        for ident,item in items.items():self.db.execute('INSERT INTO publish_entries VALUES(?,?)',(ident,encoded(item)))
        self.catalog([{'key':'b','name':'B','url':'https://gloformulas.com/b','sizes':[]}])
        self.assertIn('catalog:a',entries(self.db,self.result))
    def test_approval_bound_to_payload(self):
        ident=enqueue(self.db,'removal',{'product_key':'a','source':'https://gloformulas.com/a','explicit_discontinuation':True})
        with self.assertRaises(PublishError):approve(self.db,ident,'wrong','Reviewer')
        row=self.db.execute('SELECT digest FROM publish_reviews').fetchone();approve(self.db,ident,row[0],'Reviewer')
    def test_absence_approval_rejected(self):
        payload={'product_key':'a','source':'https://gloformulas.com/a','explicit_discontinuation':False}
        ident=enqueue(self.db,'removal',payload)
        with self.assertRaises(PublishError):approve(self.db,ident,digest(payload),'Reviewer')
    def test_uncertain_coa_and_medical_not_generic_approved(self):
        for kind in ('uncertain_coa','medical_claim','interaction'):
            payload={'source':'https://example.org/evidence'};ident=enqueue(self.db,kind,payload)
            with self.assertRaises(PublishError):approve(self.db,ident,digest(payload),'Reviewer')
    def test_utf16(self):self.assertEqual(utf16('A😀'),3)
    def test_idempotence(self):
        value=render({});doc=document('Legacy\n\n'+value)
        self.assertEqual(plan(doc,value)['requests'],[])
    def test_missing_duplicate_or_reversed_markers(self):
        for content in (START,START+START+END,END+START):
            with self.assertRaises(PublishError):plan(document(content),render({}))
    def test_mixed_native_content_rejected(self):
        doc=document(START+END);doc['tabs'][0]['documentTab']['body']['content'].insert(1,{'startIndex':3,'table':{'tableRows':[]}})
        with self.assertRaises(PublishError):plan(doc,render({}))
    def test_disabled_distinct_docs_and_main(self):
        for setting in ({'GLO_DOCS_MODE':'disabled'},{'GLO_DOCS_TEST_ID':'original'},{'GITHUB_REF':'refs/heads/feature'}):
            with patch.dict(os.environ,setting),self.assertRaises(PublishError):settings()
    def test_live_requires_test_receipt(self):
        with self.assertRaises(PublishError):gate(self.db,'live','test')
    def test_live_receipt_is_document_and_version_bound(self):
        self.db.execute('INSERT INTO publish_history VALUES(?,?,?,?,?,?,?,?)',('receipt','test','test','hash','r','verified',now(),encoded({'version':VERSION})))
        with patch.dict(os.environ,{'GLO_DOCS_TEST_VERIFIED':'receipt'}):
            gate(self.db,'live','test')
            with self.assertRaises(PublishError):gate(self.db,'live','other-test')
            self.db.execute('UPDATE publish_history SET detail_json=?',(encoded({'version':'old-publisher'}),))
            with self.assertRaises(PublishError):gate(self.db,'live','test')
    def test_workflow_backup_order_and_default_off(self):
        import importlib.util
        if not importlib.util.find_spec('yaml'):self.skipTest('Install test extra')
        import yaml
        wf=yaml.load(Path('.github/workflows/daily-monitor.yml').read_text(),Loader=yaml.BaseLoader)
        steps=wf['jobs']['monitor']['steps'];names=[s.get('id') for s in steps]
        self.assertLess(names.index('checks'),names.index('docs_auth'))
        self.assertLess(names.index('docs_backup'),names.index('docs_apply'))
        self.assertLess(names.index('docs_apply'),names.index('seal'))
        auth=steps[names.index('docs_auth')];self.assertNotIn('credentials_json',auth['with'])
        self.assertIn("vars.GLO_DOCS_MODE == 'live'",auth['if'])
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_prepare_backup_is_encrypted(self):
        prepare(self.root,self.out,API(document()))
        body=(self.out/'docs-backup.enc').read_bytes();self.assertNotIn(b'Legacy heading',body)
        record=json.loads(decrypt(body,'s'*40,'report'));self.assertIn('document',record)
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_backup_required_before_write(self):
        service=API(document());prepare(self.root,self.out,service)
        with patch.dict(os.environ,{'GLO_DOCS_BACKUP_UPLOADED':'false'}),self.assertRaises(PublishError):apply(self.root,self.out,service)
        self.assertEqual(service.calls,0)
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_revision_conflict_no_write(self):
        service=API(document());prepare(self.root,self.out,service);service.doc['revisionId']='changed'
        with self.assertRaises(PublishError):apply(self.root,self.out,service)
        self.assertEqual(service.calls,0)
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_timeout_blocks_further_writes(self):
        service=API(document());prepare(self.root,self.out,service);service.fail=True
        with self.assertRaises(PublishError):apply(self.root,self.out,service)
        self.assertEqual(service.calls,1)
        with self.assertRaises(PublishError):prepare(self.root,self.out,service)
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_apply_and_idempotent_rerun(self):
        service=API(document());prepare(self.root,self.out,service);receipt=apply(self.root,self.out,service)
        self.assertEqual(managed(service.doc),render({}));self.assertTrue(receipt)
        self.assertFalse(prepare(self.root,self.out,service));apply(self.root,self.out,service)
        self.assertEqual(service.calls,1)
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_manual_managed_text_edit_pauses_publishing(self):
        service=API(document());prepare(self.root,self.out,service);apply(self.root,self.out,service)
        service.doc=document('Legacy heading\n\n'+render({}).replace('No newly verified','Human edited'))
        with self.assertRaises(PublishError):prepare(self.root,self.out,service)
    @unittest.skipUnless(importlib.util.find_spec('cryptography'),'Install monitor extra')
    def test_recovery_requires_exact_receipt_and_known_content(self):
        service=API(document());prepare(self.root,self.out,service);receipt=apply(self.root,self.out,service)
        self.db.execute("UPDATE publish_history SET status='uncertain'");self.db.commit()
        with patch.dict(os.environ,{'GLO_DOCS_REVIEW_DIGEST':'wrong'}),self.assertRaises(PublishError):reconcile(self.root,service)
        with patch.dict(os.environ,{'GLO_DOCS_REVIEW_DIGEST':receipt}):self.assertEqual(reconcile(self.root,service),'verified')

if __name__=='__main__':unittest.main()
