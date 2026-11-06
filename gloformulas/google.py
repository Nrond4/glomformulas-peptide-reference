"""Official Google APIs with Application Default Credentials; no secret logging."""
from pathlib import Path
from .core import encoded
from .importers import GUIDE_URL,SHEET_URL,FOLDER_URL
DOC_ID=GUIDE_URL.split('/d/')[1].split('/')[0]
SHEET_ID=SHEET_URL.split('/d/')[1].split('/')[0]
FOLDER_ID=FOLDER_URL.rsplit('/',1)[1]

def services(write=False):
 import google.auth
 from googleapiclient.discovery import build
 credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/drive.readonly','https://www.googleapis.com/auth/documents'+('' if write else '.readonly'),'https://www.googleapis.com/auth/spreadsheets.readonly'])
 return {name:build(name,version,credentials=credentials,cache_discovery=False) for name,version in [('docs','v1'),('drive','v3'),('sheets','v4')]}

def read(request):return request.execute(num_retries=3)

def sync(directory):
 api=services();root=Path(directory);root.mkdir(parents=True,exist_ok=True)
 doc=read(api['docs'].documents().get(documentId=DOC_ID,includeTabsContent=True))
 sheet=read(api['sheets'].spreadsheets().values().get(spreadsheetId=SHEET_ID,range="'工作表1'!A:Y"))
 files=[];token=None
 while True:
  r=read(api['drive'].files().list(q=f"'{FOLDER_ID}' in parents and trashed=false",pageSize=1000,pageToken=token,fields='nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,md5Checksum)'))
  files.extend(r.get('files',[]));token=r.get('nextPageToken')
  if not token:break
 for name,payload in [('guide',doc),('sheet',sheet),('folder',{'files':[dict(f,url=f.get('webViewLink',f"https://drive.google.com/file/d/{f['id']}/view")) for f in files]})]:
  dest=root/(name+'.json');temp=dest.with_suffix('.tmp');temp.write_text(encoded(payload));temp.replace(dest)
 return {'guide_revision':doc.get('revisionId'),'folder_files':len(files)}

def publish(db,plan):
 from .guide import make_plan
 api=services(write=True);doc=read(api['docs'].documents().get(documentId=DOC_ID,includeTabsContent=True))
 if plan.get('document_id')!=DOC_ID or doc.get('revisionId')!=plan.get('revision_id'):raise ValueError('Document/revision mismatch; regenerate plan')
 if make_plan(db,doc)!=plan:raise ValueError('Plan differs from currently approved evidence')
 if not plan['requests']:return {'updated':0}
 # Writes are deliberately not retried: an ambiguous outcome requires readback reconciliation.
 r=api['docs'].documents().batchUpdate(documentId=DOC_ID,body={'writeControl':{'requiredRevisionId':plan['revision_id']},'requests':plan['requests']}).execute()
 actual=read(api['docs'].documents().get(documentId=DOC_ID,includeTabsContent=True))
 if make_plan(db,actual)['requests']:raise RuntimeError('Readback differs; inspect before retrying')
 return {'updated':len(plan['requests'])//3,'write_control':r.get('writeControl')}
