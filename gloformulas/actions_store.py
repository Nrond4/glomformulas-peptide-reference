"""GitHub-only artifact retrieval using the ephemeral job token (never logged)."""
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from .monitor_state import MAX_ARCHIVE,StateError

ARTIFACT='glo-monitor-state'
WORKFLOW='.github/workflows/daily-monitor.yml'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None

class GitHubStore:
    def __init__(self,repository,token,branch='main',current_run=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise StateError('Invalid repository identity')
        if not token:raise StateError('GitHub job token is unavailable')
        self.repo=repository;self.token=token;self.branch=branch;self.current_run=str(current_run or '')
        self.opener=urllib.request.build_opener(NoRedirect())
    def request(self,path,binary=False):
        if not path.startswith(f'/repos/{self.repo}/'):raise StateError('GitHub request left the current repository')
        # Artifact ZIP requests use GitHub's JSON API media type and return a redirect.
        # Only the release-asset API requires octet-stream to select binary contents.
        media='application/octet-stream' if binary and '/releases/assets/' in path else 'application/vnd.github+json'
        req=urllib.request.Request('https://api.github.com'+path,headers={'Authorization':'Bearer '+self.token,'Accept':media,'X-GitHub-Api-Version':'2022-11-28','User-Agent':'GloFormulasDailyMonitor'})
        try:
            with self.opener.open(req,timeout=40) as response:body=response.read(MAX_ARCHIVE+1)
        except urllib.error.HTTPError as exc:
            if binary and exc.code in (301,302,303,307,308):return self.download_redirect(exc.headers.get('Location',''))
            raise StateError(f'GitHub state request failed (HTTP {exc.code}); baseline was not reset') from None
        except (urllib.error.URLError,TimeoutError):raise StateError('GitHub state storage unavailable; baseline was not reset') from None
        if len(body)>MAX_ARCHIVE:raise StateError('GitHub download exceeded the size limit')
        return body if binary else json.loads(body)
    def download_redirect(self,target):
        # Never forward the GitHub token to signed artifact/release storage URLs.
        p=urllib.parse.urlparse(target)
        allowed=p.hostname and (p.hostname.endswith('.blob.core.windows.net') or p.hostname.endswith('.githubusercontent.com'))
        if p.scheme!='https' or not allowed or p.username or p.password:raise StateError('Unexpected GitHub storage redirect')
        try:
            with self.opener.open(urllib.request.Request(target,headers={'User-Agent':'GloFormulasDailyMonitor'}),timeout=40) as response:body=response.read(MAX_ARCHIVE+1)
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):raise StateError('GitHub encrypted artifact download failed') from None
        if len(body)>MAX_ARCHIVE:raise StateError('Encrypted download exceeded the size limit')
        return body
    def trusted_run(self,run_id):
        r=self.request(f'/repos/{self.repo}/actions/runs/{int(run_id)}')
        return (r.get('path')==WORKFLOW and r.get('event') in ('schedule','workflow_dispatch')
                and r.get('head_branch')==self.branch and r.get('head_repository',{}).get('full_name')==self.repo
                and str(r.get('id'))!=self.current_run)
    def latest_state(self):
        # Partial/failed source-check runs may still carry the newest valid database.
        for page in range(1,11):
            result=self.request(f'/repos/{self.repo}/actions/artifacts?name={ARTIFACT}&per_page=100&page={page}')
            rows=result.get('artifacts',[])
            for artifact in sorted(rows,key=lambda a:a['id'],reverse=True):
                if artifact.get('name')!=ARTIFACT:continue
                run=artifact.get('workflow_run',{})
                if run.get('head_branch')!=self.branch or not self.trusted_run(run['id']):continue
                if artifact.get('expired'):raise StateError('The last database artifact expired; recover a backup instead of starting empty')
                body=self.request(f"/repos/{self.repo}/actions/artifacts/{int(artifact['id'])}/zip",binary=True)
                try:
                    with zipfile.ZipFile(io.BytesIO(body)) as archive:
                        if archive.namelist()!=['state.enc']:raise StateError('Unexpected state artifact contents')
                        if archive.getinfo('state.enc').file_size>MAX_ARCHIVE:raise StateError('State artifact is oversized')
                        return archive.read('state.enc')
                except zipfile.BadZipFile:raise StateError('Corrupted GitHub state artifact') from None
            if len(rows)<100:return None
        raise StateError('Artifact lookup limit reached; refusing an unverified empty baseline')
    def seed(self,tag='monitor-seed'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',tag):raise StateError('Invalid seed release tag')
        release=self.request(f'/repos/{self.repo}/releases/tags/{tag}')
        matches=[a for a in release.get('assets',[]) if a.get('name')=='monitor-seed.enc']
        if len(matches)!=1:raise StateError('Upload monitor-seed.enc to the configured GitHub seed release first')
        return self.request(f"/repos/{self.repo}/releases/assets/{int(matches[0]['id'])}",binary=True)
