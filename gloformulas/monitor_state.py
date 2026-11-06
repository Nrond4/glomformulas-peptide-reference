"""Encrypted, bounded Actions state. No plaintext database or credentials in artifacts."""
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_PLAINTEXT = 192 * 1024 * 1024
STATE_FILES = {
    'reference.sqlite', 'source-config.json',
    'imports/guide.json', 'imports/sheet.json', 'imports/folder.json', 'imports/catalog.json',
    'reports/latest.json', 'reports/latest.md', 'research-coverage.json', 'reference-guide.md',
}
CONFIG_KEYS = {
    'GUIDE_URL','SHEET_URL','FOLDER_URL','SHEET_GID','CATALOG_ADAPTER',
    'FREEDOM_RENDERED_LOOKUP','FREEDOM_CODES','FREEDOM_EXPECTATIONS',
    'RESEARCH_PRODUCT_IDS','GOOGLE_READ_SYNC',
}

class StateError(RuntimeError):
    pass


def validate_key(password):
    if not isinstance(password,str) or len(password.strip()) < 32:
        raise StateError('GLO_STATE_KEY must be a random secret of at least 32 characters')


def encrypt(body,password,kind='state'):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    validate_key(password)
    if kind not in ('state','report','transfer-key') or len(body)>MAX_PLAINTEXT:
        raise StateError('Invalid or oversized encryption input')
    header=('GLOM1:'+kind+'\n').encode();salt=os.urandom(16);nonce=os.urandom(12)
    key=Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode())
    return header+salt+nonce+AESGCM(key).encrypt(nonce,body,header)


def decrypt(body,password,kind='state'):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    validate_key(password)
    header=('GLOM1:'+kind+'\n').encode()
    if len(body)>MAX_ARCHIVE or not body.startswith(header) or len(body)<len(header)+44:
        raise StateError('Invalid encrypted file; no state was replaced')
    start=len(header);salt=body[start:start+16];nonce=body[start+16:start+28]
    key=Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode())
    try:return AESGCM(key).decrypt(nonce,body[start+28:],header)
    except InvalidTag:raise StateError('Encryption key mismatch or corrupted file; no state was replaced') from None


def check_database(path):
    if not Path(path).is_file():raise StateError('Existing reference database is required; refusing an empty replacement')
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise StateError('Database integrity check failed')
        counts={}
        for table in ('products','sources','lab_results','catalog_snapshots','flags'):
            counts[table]=db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        if not counts['products'] and not counts['catalog_snapshots']:raise StateError('Baseline has no product records or catalog snapshots; refusing an empty seed')


def state_archive(root):
    root=Path(root);check_database(root/'reference.sqlite')
    # sqlite backup captures WAL safely. No token, environment dump or credential file is included.
    output=io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        backup=Path(tmp)/'reference.sqlite'
        with sqlite3.connect((root/'reference.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(backup) as dst:src.backup(dst)
        entries={'reference.sqlite':backup.read_bytes()}
    for name in sorted(STATE_FILES-{'reference.sqlite'}):
        path=root/name
        if path.is_symlink():raise StateError('State files must not be symlinks')
        if path.is_file():entries[name]=path.read_bytes()
    if 'source-config.json' not in entries:raise StateError('Private source configuration is required for a seeded baseline')
    cfg=json.loads(entries['source-config.json'])
    if not isinstance(cfg,dict) or set(cfg)-CONFIG_KEYS:raise StateError('Source config contains unsupported keys; credentials must stay outside the state bundle')
    if sum(map(len,entries.values()))>MAX_PLAINTEXT:raise StateError('State reached the configured storage limit; preserve the previous artifact and review retention')
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,value in entries.items():archive.writestr(name,value)
    body=output.getvalue()
    if len(body)>MAX_ARCHIVE:raise StateError('State archive exceeded the storage limit')
    return body


def pack_state(root,password):
    body=encrypt(state_archive(root),password)
    if len(body)>MAX_ARCHIVE:raise StateError('Encrypted state exceeded the storage limit')
    return body


def restore_state(body,password,root):
    return restore_archive(decrypt(body,password),root)


def restore_archive(plaintext,root):
    if len(plaintext)>MAX_ARCHIVE:raise StateError('State archive exceeded the storage limit')
    try:
        with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
            names=archive.namelist()
            if len(names)!=len(set(names)) or any(n not in STATE_FILES for n in names):raise StateError('Unexpected archive paths; refusing state restoration')
            if 'reference.sqlite' not in names or 'source-config.json' not in names:raise StateError('Baseline database/configuration is missing')
            if sum(i.file_size for i in archive.infolist())>MAX_PLAINTEXT:raise StateError('Expanded state is oversized')
            data={name:archive.read(name) for name in names}
    except (zipfile.BadZipFile,RuntimeError) as exc:
        if isinstance(exc,StateError):raise
        raise StateError('Unreadable state archive') from None
    cfg=json.loads(data['source-config.json'])
    if not isinstance(cfg,dict) or set(cfg)-CONFIG_KEYS:raise StateError('Unsupported private configuration')
    with tempfile.TemporaryDirectory() as tmp:
        candidate=Path(tmp)/'reference.sqlite';candidate.write_bytes(data['reference.sqlite']);check_database(candidate)
    root=Path(root)
    # Never overwrite an already-open database or partial live run.
    if (root/'reference.sqlite').exists():raise StateError('Restore requires a fresh state directory')
    for name,value in data.items():
        dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(value);dest.chmod(0o600)
    return {'restored':True,'files':len(data)}
