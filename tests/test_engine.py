import copy,json,tempfile,unittest
from datetime import date
from pathlib import Path
from gloformulas.core import connect,source
from gloformulas.importers import import_guide,import_sheet,import_folder
from gloformulas.verification import assess,record_report
from gloformulas.catalog import compare,import_catalog
from gloformulas.guide import render,make_plan,approve
from gloformulas.cli import check,status

def paragraph(value,start=1):return {'startIndex':start,'endIndex':start+len(value),'paragraph':{'elements':[{'textRun':{'content':value,'textStyle':{'bold':True}}}]}}
def document():
 cells=lambda values:{'tableCells':[{'content':[paragraph(v+'\n')]} for v in values]}
 return {'documentId':'test','revisionId':'r1','tabs':[{'tabId':'t.0','body':{'content':[paragraph('Example (GloFormulas: “EX”)\n'),paragraph('Research / established use: Inherited claim\n',30),{'table':{'tableRows':[cells(['Size','Lab','Purity','Content (Result)','Batch/Lot','Manufacturer']),cells(['10mg','Janoshik','99.5%','12mg','CS-01','cocerpeptides.com'])]}}]}}]}

class EngineTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.db=connect(Path(self.tmp.name)/'db.sqlite')
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_idempotent_lossless_import(self):
  d=document();import_guide(self.db,d);a=status(self.db);import_guide(self.db,d);self.assertEqual(a,status(self.db));self.assertEqual(json.loads(self.db.execute('SELECT raw_json FROM sources').fetchone()[0]),d)
 def test_supplier_not_vendor(self):
  import_guide(self.db,document());self.assertEqual(self.db.execute('SELECT attribution FROM lab_results').fetchone()[0],'supplier')
 def test_inherited_medical_claim_not_publishable(self):
  import_guide(self.db,document());self.assertNotIn('Inherited claim',render(self.db));self.assertEqual(make_plan(self.db,document())['requests'],[])
  eid=self.db.execute('SELECT id FROM evidence').fetchone()[0]
  with self.assertRaises(ValueError):approve(self.db,eid,'Reviewer','human_trial','2020-01-01')
 def test_unresolved_identity(self):
  d=document();d['tabs'][0]['body']['content'][0]=paragraph('UNRESOLVED (GloFormulas: “EX”)\n');import_guide(self.db,d);self.assertEqual(self.db.execute('SELECT identity_status FROM products').fetchone()[0],'unresolved')
 def test_supplier_sheet(self):
  s={'values':[['Name'],['Example','B','https://janoshik.com/tests/123']]};import_sheet(self.db,s);import_sheet(self.db,s);self.assertEqual(self.db.execute('SELECT count(*) FROM lab_results').fetchone()[0],1)
 def test_primary_missing_fields(self):self.assertIn('missing_identity',assess({})['flags'])
 def test_non_glo_freedom_never_vendor(self):
  for code in ('ABC123','','glo123','GLO'):
   r=assess({'lab':'Freedom Diagnostics','search_code':code,'lab_authenticated':True,'vendor_batch_matched':True});self.assertNotEqual(r['attribution'],'gloformulas')
 def test_glo_code_alone_not_verification(self):self.assertEqual(assess({'lab':'Freedom Diagnostics','search_code':'GLO123'})['attribution'],'unverified')
 def test_identity_batch_mismatch(self):
  r=assess({'identity':'A','expected_identity':'B','batch':'1','expected_batch':'2'});self.assertIn('identity_mismatch',r['flags']);self.assertIn('batch_mismatch',r['flags'])
 def test_future_and_invalid_date(self):
  self.assertIn('future_report_date',assess({'report_date':'2099-01-01'},today=date(2026,1,1))['flags']);self.assertIn('invalid_report_date',assess({'report_date':'n/a'})['flags'])
 def test_quantity_units_and_threshold(self):
  r={'measured_content':'12','nominal_content':'10','content_unit':'mg','nominal_unit':'mg'};self.assertIn('unusual_measured_content',assess(r)['flags']);r['nominal_unit']='IU';self.assertIn('content_unit_mismatch_or_missing',assess(r)['flags'])
 def test_qualified_purity_preserved(self):self.assertIn('qualified_or_unparsed_purity',assess({'purity':'>99.9%'})['flags'])
 def test_blend_not_inferred(self):self.assertIn('blend_specific_report_missing',assess({'blend':True})['flags'])
 def test_attestation_requires_reviewer_artifact(self):
  with self.assertRaises(ValueError):record_report(self.db,{'lab_authenticated':True},'https://example.org/report')
 def test_duplicate_codes_flagged(self):
  for x in ('A','B'):record_report(self.db,{'lab':'Lab','search_code':'X','identity':x},'https://example.org/'+x)
  self.assertTrue(self.db.execute("SELECT 1 FROM flags WHERE code='duplicate_lab_search_code'").fetchone())
 def test_catalog_diff(self):
  old={'complete':True,'products':[{'key':'1','name':'A','url':'https://example.org/a','sizes':['5mg']},{'key':'2','name':'B','url':'https://example.org/b','sizes':[]}]}
  new=copy.deepcopy(old);new['products'].pop();new['products'][0]['sizes']=['10mg'];new['products'][0]['name']='Changed';new['products'].append({'key':'3','name':'C','url':'https://example.org/c','sizes':[]})
  self.assertEqual({r[1] for r in compare(old,new)},{'size_change','listing_update','discontinued_candidate','new_product'})
 def test_empty_partial_duplicate_catalog_rejected(self):
  for p in ({'complete':False,'products':[]},{'complete':True,'products':[]},{'complete':True,'products':[{'key':'x','name':'x','url':'u','sizes':[]}]*2}):
   with self.assertRaises(ValueError):import_catalog(self.db,p)
 def test_catalog_idempotency(self):
  p={'complete':True,'products':[{'key':'x','name':'x','url':'u','sizes':[]}]};import_catalog(self.db,p);import_catalog(self.db,p);self.assertEqual(status(self.db)['catalog_snapshots'],1)
 def test_failed_run_logged(self):
  with self.assertRaises(FileNotFoundError):check(self.db,self.tmp.name,str(Path(self.tmp.name)/'r.json'))
  self.assertEqual(self.db.execute('SELECT status FROM runs').fetchone()[0],'failed')
 def test_docs_utf16_style_and_revision(self):
  import_guide(self.db,document());pid=self.db.execute('SELECT id FROM products').fetchone()[0];sid=source(self.db,'medical_primary_source','https://example.org/study',{})
  self.db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)',('e',pid,'uses','Reviewed 🧬 claim',sid,'2020-01-01','human_trial','approved','Reviewer','now'))
  self.db.execute('INSERT INTO evidence_context VALUES(?,?,?,?,?,?,?,?,?)',('e','adults','study route','human','reported outcome','Methods, page 1','Limited applicability','2099-01-01',None))
  plan=make_plan(self.db,document());self.assertEqual(plan['revision_id'],'r1');self.assertEqual(len(plan['requests']),3);r=plan['requests'][2]['updateTextStyle'];self.assertTrue(r['textStyle']['bold']);self.assertEqual(r['range']['tabId'],'t.0')

if __name__=='__main__':unittest.main()
