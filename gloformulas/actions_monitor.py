"""GitHub Actions entrypoint and one-time encrypted baseline/report helpers."""
import argparse
import contextlib
import getpass
import io
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .monitor_state import StateError,pack_state,restore_state,encrypt,decrypt,validate_key

ZONE=ZoneInfo('America/Boise')
COMPONENTS=('google_read_sync','catalog','supplier','freedom','literature','guide','folder')
STATUSES={'checked','completed','completed_checks','replayed','blocked','failed','partial','not_configured','not_attempted','candidates_pending_review','offline_validation','running','interrupted','setup_required'}
REASONS={
    'checked':'Source read completed.', 'completed':'Source check completed.',
    'replayed':'Saved source only; this was not a fresh source check.',
    'blocked':'Source could not be reached/read, or its connection/dependency is not ready.',
    'failed':'Source format or processing failed; review the encrypted details.',
    'partial':'Only part of the source could be checked.',
    'not_configured':'Source selection or connection is not configured.',
    'not_attempted':'Source was not checked.',
    'candidates_pending_review':'Research leads collected; human review remains required.',
}


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
    if not result.get('checks'):lines.append('- Checks did not complete. Check the failed setup/run step and the activation instructions.')
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
    except Exception:
        # No raw API errors, private URLs, source settings, signed URLs or secrets in public logs.
        print('Monitoring setup/state operation failed. Check the activation guide, key, seed, artifact retention and failed step. The database was not replaced with an empty baseline.',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
