"""One-time public-key bridge: the cloud never needs the repository's symmetric key."""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .monitor_state import (
    MAX_ARCHIVE,StateError,state_archive,restore_archive,pack_state,restore_state,encrypt,decrypt,validate_key,
)

FORMAT='GLO-PUBLIC-TRANSFER-1'
LABEL=b'GloFormulas one-time database transfer v1'
MAX_ENVELOPE=MAX_ARCHIVE*2


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def validate_identity(repository,session):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise StateError('Invalid repository')
    if not re.fullmatch(r'[0-9]+-[0-9]+',session):raise StateError('Invalid transfer session')


def fingerprint(public):
    from cryptography.hazmat.primitives import serialization
    return hashlib.sha256(public.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def prepare(repository,session,secret,out,moment=None):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    validate_identity(repository,session);validate_key(secret)
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise StateError('A transfer session must not overwrite existing key files')
    out.mkdir(parents=True,exist_ok=True)
    moment=moment or datetime.now(timezone.utc)
    private=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    public=private.public_key()
    manifest={'format':FORMAT,'repository':repository,'session_id':session,'created_at':moment.isoformat(),
              'expires_at':(moment+timedelta(days=7)).isoformat(),'fingerprint':fingerprint(public),
              'public_key':public.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()}
    # Plain private key exists only in this process's memory. Disk/release receive encrypted bytes only.
    pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    wrapped=encrypt(pem,secret,'transfer-key')
    (out/'transfer-public.json').write_bytes(canonical(manifest))
    (out/'transfer-private.enc').write_bytes(wrapped)
    return manifest


def validate_public(manifest,repository,session,moment=None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    validate_identity(repository,session)
    if manifest.get('format')!=FORMAT or manifest.get('repository')!=repository or manifest.get('session_id')!=session:raise StateError('Transfer recipient/session mismatch')
    moment=moment or datetime.now(timezone.utc)
    created=datetime.fromisoformat(manifest['created_at']);expires=datetime.fromisoformat(manifest['expires_at'])
    if not created.tzinfo or not expires.tzinfo or not created<=moment<expires or expires-created>timedelta(days=7):raise StateError('Transfer session expired or has invalid dates')
    public=serialization.load_pem_public_key(manifest['public_key'].encode())
    if not isinstance(public,rsa.RSAPublicKey) or public.key_size!=3072 or public.public_numbers().e!=65537:raise StateError('Unsupported transfer key')
    if fingerprint(public)!=manifest.get('fingerprint'):raise StateError('Public-key fingerprint mismatch')
    return public


def database_manifest(state):
    """Verify preserved products and every table's row count from the exact SQLite snapshot."""
    with zipfile.ZipFile(io.BytesIO(state)) as archive:
        if archive.getinfo('reference.sqlite').file_size>MAX_ARCHIVE*3:raise StateError('Database snapshot exceeds limit')
        body=archive.read('reference.sqlite')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'snapshot.sqlite';path.write_bytes(body)
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise StateError('Database integrity check failed')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            counts={name:db.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
            records=db.execute('SELECT * FROM products ORDER BY id').fetchall()
    return {'table_counts':counts,'products_sha256':hashlib.sha256(canonical(records)).hexdigest(),'database_sha256':hashlib.sha256(body).hexdigest()}


def cloud_encrypt(root,manifest,repository,session,expected_products=46,moment=None):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public=validate_public(manifest,repository,session,moment)
    state=state_archive(root);stats=database_manifest(state)
    if expected_products<=0 or stats['table_counts'].get('products')!=expected_products:raise StateError('Original product count differs; no empty/substitute database is permitted')
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('state.zip',state);archive.writestr('integrity.json',canonical(stats))
    key=os.urandom(32);nonce=os.urandom(12)
    header={'format':FORMAT,'repository':repository,'session_id':session,'fingerprint':manifest['fingerprint']}
    ciphertext=AESGCM(key).encrypt(nonce,payload.getvalue(),canonical(header))
    wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=LABEL))
    result=dict(header,nonce=base64.b64encode(nonce).decode(),wrapped_key=base64.b64encode(wrapped).decode(),ciphertext=base64.b64encode(ciphertext).decode())
    body=canonical(result)
    if len(body)>MAX_ENVELOPE:raise StateError('Transfer exceeds bounded upload size')
    return body


def complete(body,manifest,wrapped_private,secret,repository,session,out,expected_products=46,moment=None):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    validate_public(manifest,repository,session,moment)
    if len(body)>MAX_ENVELOPE:raise StateError('Transfer is oversized')
    envelope=json.loads(body)
    header={'format':FORMAT,'repository':repository,'session_id':session,'fingerprint':manifest['fingerprint']}
    if any(envelope.get(k)!=v for k,v in header.items()):raise StateError('Encrypted transfer recipient/session mismatch')
    pem=decrypt(wrapped_private,secret,'transfer-key')
    private=serialization.load_pem_private_key(pem,password=None)
    if fingerprint(private.public_key())!=manifest['fingerprint']:raise StateError('Transfer private/public key mismatch')
    try:
        wrapped=base64.b64decode(envelope['wrapped_key'],validate=True);nonce=base64.b64decode(envelope['nonce'],validate=True)
        cipher=base64.b64decode(envelope['ciphertext'],validate=True)
        key=private.decrypt(wrapped,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=LABEL))
        plaintext=AESGCM(key).decrypt(nonce,cipher,canonical(header))
    except (ValueError,InvalidTag):raise StateError('Transfer authentication failed; no seed was created') from None
    with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
        if sorted(archive.namelist())!=['integrity.json','state.zip'] or sum(i.file_size for i in archive.infolist())>MAX_ARCHIVE*2:raise StateError('Unexpected transfer archive')
        state=archive.read('state.zip');expected=json.loads(archive.read('integrity.json'))
    actual=database_manifest(state)
    if actual!=expected or actual['table_counts'].get('products')!=expected_products or expected_products<=0:raise StateError('Transferred records do not match the original snapshot')
    out=Path(out)
    if out.exists():raise StateError('Refusing to overwrite an existing seed file')
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)/'restored';restore_archive(state,root)
        seed=pack_state(root,secret)
        verification=Path(tmp)/'verification';restore_state(seed,secret,verification)
        # Byte-for-byte state snapshots can differ after backup; logical products/counts must not.
        verified=database_manifest(state_archive(verification))
        if verified['table_counts']!=actual['table_counts'] or verified['products_sha256']!=actual['products_sha256']:raise StateError('Final seed verification failed')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(seed)
    return {'products':expected_products,'integrity':'ok','table_counts_preserved':True}


def publish_transfer(checkout,body,repository,session):
    """Cloud-side publication: only a validated ciphertext file on an isolated data branch.
    Never commits the work directory, database, key files or current working tree.
    """
    import subprocess
    validate_identity(repository,session)
    if len(body)>MAX_ENVELOPE:raise StateError('Transfer is oversized')
    envelope=json.loads(body)
    if envelope.get('format')!=FORMAT or envelope.get('repository')!=repository or envelope.get('session_id')!=session:raise StateError('Transfer metadata mismatch')
    if len(base64.b64decode(envelope['wrapped_key'],validate=True))!=384 or len(base64.b64decode(envelope['nonce'],validate=True))!=12 or len(base64.b64decode(envelope['ciphertext'],validate=True))<16:raise StateError('Not a valid encrypted transfer')
    expected={'format','repository','session_id','fingerprint','nonce','wrapped_key','ciphertext'}
    if set(envelope)!=expected:raise StateError('Unexpected transfer fields; refusing upload')
    checkout=Path(checkout).resolve();work=checkout/'work';work.mkdir(exist_ok=True)
    def git(*args,input=None,env=None):
        process=subprocess.run(['git',*args],cwd=checkout,input=input,capture_output=True,env=env)
        if process.returncode:raise StateError('Encrypted transfer Git operation failed; no plaintext was uploaded')
        return process.stdout.decode().strip()
    remote=git('remote','get-url','origin')
    if remote not in ('https://github.com/'+repository+'.git','https://github.com/'+repository):raise StateError('Origin is not the intended GitHub repository')
    destination='refs/heads/seed-transfer/'+session
    if git('ls-remote','--heads','origin',destination):raise StateError('This session already has a transfer branch; do not overwrite it')
    git('fetch','origin','main');base=git('rev-parse','origin/main')
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        env=dict(os.environ,GIT_INDEX_FILE=str(Path(tmp)/'index'))
        git('read-tree',base,env=env)
        blob=git('hash-object','-w','--stdin',input=body)
        git('update-index','--add','--cacheinfo','100644,'+blob+',transfer/database.glo-transfer.json',env=env)
        tree=git('write-tree',env=env)
        commit=git('commit-tree',tree,'-p',base,input=b'Add session-encrypted database transfer; no plaintext data\n')
    paths=git('diff-tree','--no-commit-id','--name-only','-r',base,commit).splitlines()
    if paths!=['transfer/database.glo-transfer.json']:raise StateError('Transfer commit contains unexpected files; upload refused')
    git('push','origin',commit+':'+destination)
    return {'branch':'seed-transfer/'+session,'commit':commit,'encrypted_only':True}


def main():
    parser=argparse.ArgumentParser(description='Browser-only encrypted transfer bridge')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--repository',required=True);p.add_argument('--session',required=True);p.add_argument('--out',required=True)
    p=sub.add_parser('encrypt');p.add_argument('--state',required=True);p.add_argument('--public',required=True);p.add_argument('--repository',required=True);p.add_argument('--session',required=True);p.add_argument('--output',required=True);p.add_argument('--fingerprint',required=True)
    p=sub.add_parser('publish');p.add_argument('--transfer',required=True);p.add_argument('--repository',required=True);p.add_argument('--session',required=True);p.add_argument('--checkout',default='.')
    p=sub.add_parser('complete');p.add_argument('--transfer',required=True);p.add_argument('--keys',required=True);p.add_argument('--repository',required=True);p.add_argument('--session',required=True);p.add_argument('--output',required=True)
    args=parser.parse_args();os.umask(0o077)
    try:
        if args.command=='prepare':
            manifest=prepare(args.repository,args.session,os.environ.get('GLO_STATE_KEY',''),args.out)
            print('Transfer session:',manifest['session_id']);print('Public-key fingerprint:',manifest['fingerprint'])
            print('Public recipient prepared. Private key is encrypted; no database was created.')
        elif args.command=='encrypt':
            manifest=json.loads(Path(args.public).read_text())
            if manifest.get('fingerprint')!=args.fingerprint:raise StateError('Public key differs from the fingerprint recorded in the Prepare workflow log')
            body=cloud_encrypt(args.state,manifest,args.repository,args.session)
            dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():raise StateError('Refusing to overwrite a transfer')
            dest.write_bytes(body);print('Existing 46-product database encrypted for this transfer session; repository secret was not needed.')
        elif args.command=='publish':
            result=publish_transfer(args.checkout,Path(args.transfer).read_bytes(),args.repository,args.session)
            print('Ciphertext-only transfer uploaded to',result['branch'],'; no main branch or plaintext database was changed.')
        else:
            keys=Path(args.keys)
            result=complete(Path(args.transfer).read_bytes(),json.loads((keys/'transfer-public.json').read_text()),(keys/'transfer-private.enc').read_bytes(),os.environ.get('GLO_STATE_KEY',''),args.repository,args.session,args.output)
            print('Encrypted monitor-seed.enc verified:',result['products'],'products; original records and table counts preserved.')
        return 0
    except Exception:
        print('Transfer stopped safely. Check the session, expiry, key, expected 46 records and setup instructions. No plaintext database was uploaded or replacement seed published.')
        return 1

if __name__=='__main__':raise SystemExit(main())
