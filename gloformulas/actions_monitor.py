"""GitHub Actions entrypoint and one-time encrypted baseline/report helpers."""
import argparse
import contextlib
import getpass
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .monitor_state import StateError,pack_state,restore_state,encrypt,decrypt,validate_key

ZONE=ZoneInfo('America/Boise')
COMPONENTS=('google_read_sync','catalog','supplier','freedom','literature','guide','folder')
STATUSES={'deferred','checked','completed','completed_checks','replayed','blocked','failed','partial','not_configured','not_attempted','candidates_pending_review','offline_validation','running','interrupted','setup_required'}
REASONS={
    'deferred':'Request postponed to respect the previous source rate limit.',
    'checked':'Source read completed.', 'completed':'Source check completed.',
    'replayed':'Saved source only; this was not a fresh source check.',
    'blocked':'Source could not be reached/read, or its connection/dependency is not ready.',
    'failed':'Source format or processing failed; review the encrypted details.',
    'partial':'Only part of the source could be checked.',
    'not_configured':'Source selection or connection is not configured.',
    'not_attempted':'Source was not checked.',
    'candidates_pending_review':'Research leads collected; human review remains required.',
}


DIAGNOSTICS={
    'source_sign_in_required':'Source requires sign-in; no unattended read authorization is configured.',
    'source_redirect_rejected':'Source redirected outside its approved file-serving hosts.',
    'lookup_navigation_timeout':'Freedom lookup page did not finish loading in time.',
    'lookup_controls_timeout':'Freedom search controls did not respond in time.',
    'lookup_results_timeout':'Freedom search did not produce readable report links in time; this is not proof of missing COAs.',
    'http_401':'Source requires authorization.',
    'http_403':'Source denied the automated request.',
    'http_404':'Source endpoint was not found.',
    'http_429':'Source rate limit was reached.',
    'http_500':'Source returned a server error.',
    'http_502':'Source returned a gateway error.',
    'http_503':'Source is temporarily unavailable.',
    'http_504':'Source gateway timed out.',
    'connection_unavailable':'Network/TLS connection could not be established.',
    'lookup_timeout':'Freedom lookup did not produce the expected page element or PDF in time.',
    'source_timeout':'Source request timed out.',
    'catalog_xml_unreadable':'Catalog sitemap was not readable XML.',
    'source_json_unreadable':'Source response was not readable JSON.',
    'robots_excluded':'Source robots rules exclude the requested catalog path.',
    'catalog_schema_unrecognized':'Readable product data or expected catalog schema was missing.',
    'catalog_incomplete':'Catalog totals, scope or pagination could not be verified.',
    'supplier_private_or_non_csv':'Supplier export requires access or did not return readable CSV.',
    'supplier_header_missing':'Supplier spreadsheet layout was not recognized.',
    'freedom_results_unreadable':'Lookup returned no readable official PDF links.',
    'snapshot_missing':'A required saved source snapshot is missing.',
    'optional_dependency_missing':'An optional source-reading dependency is unavailable.',
    'source_not_configured':'This source has not been selected/configured for unattended checks.',
    'unclassified_source_error':'Source check did not complete; raw details remain encrypted.',
}


def public_diagnostics(component,detail,status):
    """Map private error text to fixed public categories; never echo text or source URLs."""
    if not isinstance(detail,dict):return []
    reasons=[]
    if isinstance(detail.get('reason'),str):reasons.append(detail['reason'][:1000])
    failures=detail.get('failures',[])
    if isinstance(failures,list):
        reasons += [f['reason'][:1000] for f in failures[:20] if isinstance(f,dict) and isinstance(f.get('reason'),str)]
    codes=set()
    for reason in reasons:
        for code in re.findall(r'\bHTTP[ :]+(401|403|404|429|500|502|503|504)\b',reason,re.I):codes.add('http_'+code)
        if 'Source requires sign-in;' in reason:codes.add('source_sign_in_required')
        if 'Refused URL outside the configured HTTPS source hosts' in reason:codes.add('source_redirect_rejected')
        for step in ('navigation','controls','results'):
            if 'Freedom lookup '+step+' timed out' in reason:codes.add('lookup_'+step+'_timeout')
        if 'connection unavailable' in reason.lower():codes.add('connection_unavailable')
        if ('TimeoutError' in reason or 'timed out' in reason.lower()) and not any(c.startswith('lookup_') for c in codes):codes.add('lookup_timeout' if component=='freedom' else 'source_timeout')
        if reason=='ParseError':codes.add('catalog_xml_unreadable' if component=='catalog' else 'unclassified_source_error')
        if reason=='JSONDecodeError':codes.add('source_json_unreadable')
        if 'disallowed by robots.txt' in reason:codes.add('robots_excluded')
        if any(t in reason for t in ('structured data','structured product data','Unrecognized WooCommerce','Unrecognized Shopify','Unrecognized sitemap')):codes.add('catalog_schema_unrecognized')
        if any(t in reason for t in ('pagination','page limit','scope changed','total missing','scan incomplete','page budget')):codes.add('catalog_incomplete')
        if 'not public CSV' in reason:codes.add('supplier_private_or_non_csv')
        if 'Supplier header missing' in reason:codes.add('supplier_header_missing')
        if ('No readable lab PDF results' in reason or 'no readable official PDF links' in reason):codes.add('freedom_results_unreadable')
        if 'Required private source snapshot is missing' in reason:codes.add('snapshot_missing')
        if 'No module named' in reason:codes.add('optional_dependency_missing')
    if status=='not_configured':codes.add('source_not_configured')
    if status in ('blocked','failed','partial') and not codes:codes.add('unclassified_source_error')
    return sorted(codes)[:8]


SETUP_DIAGNOSTICS={
    'artifact_api_rejected':'GitHub rejected the encrypted-state request; inspect Actions read permission or API media-type compatibility.',
    'artifact_api_unavailable':'GitHub state storage could not be reached; the saved baseline was not reset.',
    'state_missing':'No prior encrypted database was found; explicit first-run initialization is required.',
    'artifact_expired':'Saved state has expired; recover the existing encrypted backup.',
    'artifact_redirect_rejected':'Encrypted-state storage redirected to an unapproved host.',
    'artifact_download_failed':'The encrypted artifact could not be downloaded.',
    'artifact_format_invalid':'Downloaded state did not have the expected archive format.',
    'state_authentication_failed':'State could not be authenticated with the configured key; no empty replacement was created.',
    'state_envelope_invalid':'State is corrupt, oversized, or not the expected encrypted file.',
    'key_missing_or_invalid':'Repository encryption secret is missing or does not meet the required format.',
    'state_restore_failed':'Database restore stopped safely; no empty replacement was created.',
}

for _code in ('401','403','404','410','415','429','500','502','503','504'):
    SETUP_DIAGNOSTICS['artifact_http_'+_code]='GitHub encrypted-state API returned HTTP '+_code+'. The stored baseline was not reset.'


def public_setup_diagnostic(exc):
    """Only fixed categories escape to public logs; raw exception strings never do."""
    text=str(exc)
    status=re.search(r'GitHub state request failed \(HTTP (401|403|404|410|415|429|500|502|503|504)\)',text)
    if status:return 'artifact_http_'+status[1]
    if 'GitHub state request failed (HTTP ' in text:return 'artifact_api_rejected'
    known={
        'GitHub state storage unavailable; baseline was not reset':'artifact_api_unavailable',
        'Unexpected GitHub storage redirect':'artifact_redirect_rejected',
        'GitHub encrypted artifact download failed':'artifact_download_failed',
        'Unexpected state artifact contents':'artifact_format_invalid',
        'Corrupted GitHub state artifact':'artifact_format_invalid',
        'Encryption key mismatch or corrupted file; no state was replaced':'state_authentication_failed',
        'Invalid encrypted file; no state was replaced':'state_envelope_invalid',
        'Repository secret GLO_STATE_KEY is missing':'key_missing_or_invalid',
        'GLO_STATE_KEY must be a random secret of at least 32 characters':'key_missing_or_invalid',
    }
    if text in known:return known[text]
    if text.startswith('No existing database artifact.'):return 'state_missing'
    if text.startswith('The last database artifact expired;'):return 'artifact_expired'
    return 'state_restore_failed'


def stamp(moment=None):
    return (moment or datetime.now(ZONE)).astimezone(ZONE).strftime('%Y-%m-%d_%H%M%S_%Z')


def public_summary(result):
    """Strict allowlist: public Actions summaries contain no names, links or raw errors."""
    status=result.get('status')
    status=status if status in STATUSES else 'failed'
    lines=[f'# GloFormulas daily check — {stamp()}',f'Result: **{status}**.',
           'Original Google Doc: **not modified**. No medical claim is automatically approved.',
           'Only completed checks contribute to change counts. Unavailable sources may contain unseen changes.','']
    counts=result.get('changes',{})
    for key,label in [('catalog_changes','Catalog changes'),('supplier_changes','Supplier changes'),('research_candidates','New literature leads'),('flags','New review flags'),('lab_fetches','Lab reports collected')]:
        number=counts.get(key)
        if isinstance(number,int) and 0<=number<10**9:lines.append(f'- {label}: {number}')
    for check in result.get('checks',[]):
        name=check.get('component');state=check.get('status')
        if name not in COMPONENTS:continue
        state=state if state in STATUSES else 'failed'
        lines.append(f'- {name}: **{state}**. {REASONS.get(state,"See encrypted details.")}')
        for code in public_diagnostics(name,check.get('detail',{}),state):
            lines.append(f'  - Diagnostic: {code}. {DIAGNOSTICS[code]}')
    if not result.get('checks'):
        lines.append('- Checks did not complete. Check the failed setup/run step and the activation instructions.')
        types={'ValueError':'invalid_monitor_input','TypeError':'monitor_type_error','OperationalError':'database_operation_failed','FileNotFoundError':'required_file_missing'}
        code=types.get(result.get('error_type'))
        if code:lines.append(f'- Monitor diagnostic: {code}. Details remain encrypted.')
    setup=result.get('setup_issue')
    if setup in ('dependency_installation','browser_installation','baseline_restore'):
        lines.append(f'- Setup error: {setup}. Check the activation guide and this failed step; source checks were not run.')
    lines+=['','Schedule: 8:00 AM America/Boise (Mountain Time, with daylight saving). GitHub may queue jobs after the scheduled time.',
            'Detailed reports and database state are encrypted. Retain the encryption key outside GitHub as a backup.',
            'Unverified COAs, unresolved identities and medical claims remain flagged for review.',
            'A COA does not prove sterility, clinical safety, efficacy, or safe peptide combinations.']
    return '\n'.join(lines)+'\n'


def private_config(root,override=None):
    path=Path(root)/'source-config.json'
    config=json.loads(path.read_text())
    if override:
        supplied=json.loads(override)
        if not isinstance(supplied,dict):raise StateError('Optional configuration secret must be a JSON object')
        config.update(supplied)
    from .monitor_state import CONFIG_KEYS
    if not isinstance(config,dict) or set(config)-CONFIG_KEYS:raise StateError('Unsupported configuration keys; do not place credentials in source settings')
    config['GOOGLE_READ_SYNC']=False  # This workflow has no Google credentials or write connection.
    config.setdefault('FREEDOM_RENDERED_LOOKUP',True)
    path.write_text(json.dumps(config));path.chmod(0o600)
    os.environ['GLO_SOURCE_CONFIG']=str(path.resolve())
    return config


def restore_for_actions(root,key,store,bootstrap=False,seed_tag='monitor-seed'):
    validate_key(key)
    body=store.latest_state()
    origin='previous_encrypted_artifact'
    if body is None:
        if not bootstrap:raise StateError('No existing database artifact. First run: upload an encrypted seed and select Initialize from seed release.')
        body=store.seed(seed_tag);origin='encrypted_seed_release'
    restored=restore_state(body,key,root)
    private_config(root,os.environ.get('GLO_SOURCE_CONFIG_JSON'))
    return dict(restored,origin=origin)


def run_monitor(root,out,runner=None):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    private_config(root)
    os.environ['GLO_ACTIONS_MONITOR']='1'
    result={'status':'running','checks':[]}
    (out/'monitor-result.json').write_text(json.dumps(result))
    # Source output stays private and is encrypted by finalize, never sent to the Actions log.
    with (out/'private-run.log').open('w') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        try:
            from .core import connect
            from .daily import daily
            db=connect(root/'reference.sqlite')
            try:result=(runner or daily)(db,root,live=True)
            finally:db.close()
        except Exception as exc:
            result={'status':'failed','checks':[],'error_type':type(exc).__name__}
    (out/'monitor-result.json').write_text(json.dumps(result))
    (out/'summary.md').write_text(public_summary(result))
    return 0 if result.get('status')=='completed_checks' else 2


def finalize(root,out,key):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    result_path=out/'monitor-result.json'
    result=json.loads(result_path.read_text()) if result_path.exists() else {'status':'interrupted','checks':[]}
    if result.get('status')=='running':result={'status':'interrupted','checks':[]}
    (out/'summary.md').write_text(public_summary(result))
    files={'report.json':json.dumps(result).encode()}
    for name,path in [('changes.md',root/'reports/latest.md'),('private-run.log',out/'private-run.log')]:
        if path.is_file():
            if name=='changes.md':
                latest=root/'reports/latest.json'
                if not result.get('run_id') or not latest.exists() or json.loads(latest.read_text()).get('run_id')!=result['run_id']:continue
            files[name]=path.read_bytes()
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,value in files.items():archive.writestr(name,value)
    # Both outputs are completed before exposing either artifact to the upload steps.
    state=pack_state(root,key);report=encrypt(buffer.getvalue(),key,'report')
    (out/'state.enc').write_bytes(state);(out/'detail.enc').write_bytes(report)
    return result


def password():
    if os.environ.get('GITHUB_ACTIONS')=='true' and not os.environ.get('GLO_STATE_KEY'):raise StateError('Repository secret GLO_STATE_KEY is missing')
    value=os.environ.get('GLO_STATE_KEY') or getpass.getpass('Encryption key (same value as GitHub GLO_STATE_KEY; not shown): ')
    validate_key(value)
    return value


def main():
    parser=argparse.ArgumentParser(description='Prepare baseline or run protected GitHub monitoring')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('seed');p.add_argument('--state',default='work');p.add_argument('--output',default='work/monitor-seed.enc')
    p=sub.add_parser('storage-error');p.add_argument('--out',required=True)
    p=sub.add_parser('setup-summary');p.add_argument('--out',required=True);p.add_argument('--stage',choices=['dependency_installation','browser_installation','baseline_restore'],required=True)
    p=sub.add_parser('restore');p.add_argument('--state',required=True);p.add_argument('--bootstrap',action='store_true')
    p=sub.add_parser('run');p.add_argument('--state',required=True);p.add_argument('--out',required=True)
    p=sub.add_parser('finalize');p.add_argument('--state',required=True);p.add_argument('--out',required=True)
    p=sub.add_parser('decrypt-report');p.add_argument('file');p.add_argument('--output',default='work/decrypted-report')
    args=parser.parse_args()
    os.umask(0o077)
    try:
        if args.command=='storage-error':
            path=Path(args.out)/'summary.md'
            existing=path.read_text() if path.exists() else public_summary({'status':'failed','checks':[]})
            path.write_text(existing+'\n**Storage error:** Updated encrypted state could not be saved. The prior baseline was not deleted; inspect the encryption/upload step before relying on this run.\n')
        elif args.command=='setup-summary':
            out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
            result={'status':'setup_required','setup_issue':args.stage,'checks':[{'component':c,'status':'not_attempted'} for c in COMPONENTS]}
            (out/'summary.md').write_text(public_summary(result))
        elif args.command=='seed':
            body=pack_state(args.state,password());dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
            print('Encrypted seed created. Upload only monitor-seed.enc as an asset of the monitor-seed GitHub release. Never upload the raw database or key.')
        elif args.command=='restore':
            from .actions_store import GitHubStore
            store=GitHubStore(os.environ['GITHUB_REPOSITORY'],os.environ.get('GH_TOKEN'),current_run=os.environ.get('GITHUB_RUN_ID'))
            restore_for_actions(args.state,password(),store,args.bootstrap,os.environ.get('GLO_SEED_TAG') or 'monitor-seed')
            print('Encrypted baseline restored and checked. Original Google Doc publishing is disabled.')
        elif args.command=='run':return run_monitor(args.state,args.out)
        elif args.command=='finalize':finalize(args.state,args.out,password());print('Encrypted state and dated report prepared.')
        else:
            body=decrypt(Path(args.file).read_bytes(),password(),'report')
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                allowed={'report.json','changes.md','private-run.log'}
                if any(n not in allowed for n in archive.namelist()):raise StateError('Unexpected report file paths')
                for name in archive.namelist():
                    dest=Path(args.output)/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(archive.read(name))
            print('Detailed report decrypted locally. Keep these files private.')
        return 0
    except Exception as exc:
        if args.command=='restore':
            code=public_setup_diagnostic(exc)
            print(f'Setup diagnostic: {code}. {SETUP_DIAGNOSTICS[code]}',file=sys.stderr)
        # No raw API errors, private URLs, source settings, signed URLs or secrets in public logs.
        print('Monitoring setup/state operation failed. Check the activation guide, key, seed, artifact retention and failed step. The database was not replaced with an empty baseline.',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
