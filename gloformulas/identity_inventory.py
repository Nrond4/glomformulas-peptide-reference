"""Isolated read-only inventory; never imports the migrating application connection."""
import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from .actions_store import GitHubStore
from .monitor_state import StateError, restore_state


def inventory(path):
    path = Path(path)
    before = path.read_bytes()
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise StateError('Inventory database integrity failed')
        products = []
        for row in db.execute('SELECT * FROM products ORDER BY id'):
            product = dict(row)
            pid = product['id']
            product['provenance'] = [dict(r) for r in db.execute('SELECT * FROM sources WHERE id=?', (product['source_id'],))]
            product['guide_references'] = [dict(r) for r in db.execute('SELECT * FROM guide_blocks WHERE product_id=? ORDER BY id', (pid,))]
            product['identity_review_references'] = [dict(r) for r in db.execute("SELECT * FROM audit_log WHERE entity_id=? ORDER BY id", (pid,))]
            product['flags'] = [dict(r) for r in db.execute("SELECT * FROM flags WHERE entity_type='product' AND entity_id=? ORDER BY id", (pid,))]
            # These are references for review, not proof of scientific identity or vial contents.
            product['review_required'] = True
            products.append(product)
        count = db.execute('SELECT count(*) FROM products').fetchone()[0]
        if not products or count != len(products) or len({p['id'] for p in products}) != count:
            raise StateError('Incomplete inventory')
    finally:
        db.close()
    if path.read_bytes() != before:
        raise StateError('Inventory changed its database copy')
    counts = Counter(p['identity_status'] for p in products)
    # Strict allowlist prevents arbitrary database strings reaching public summaries.
    safe = {status: counts.get(status, 0) for status in ('imported', 'unresolved', 'reviewed')}
    safe['other'] = count - sum(safe.values())
    return {'format': 'glo-identity-inventory-v1', 'database_sha256': hashlib.sha256(before).hexdigest(),
            'products': products}, {'products': count, 'identity_status_counts': safe}


def seal(detail, recipient, repository, session):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from .browser_transfer import validate_identity
    validate_identity(repository, session)
    if len(recipient) > 4096:
        raise StateError('Oversized recipient')
    jwk = json.loads(recipient)
    def number(value):
        return int.from_bytes(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)), 'big')
    if jwk.get('kty') != 'RSA' or 'd' in jwk:
        raise StateError('Public RSA recipient required')
    public = rsa.RSAPublicNumbers(number(jwk['e']), number(jwk['n'])).public_key()
    if public.key_size != 3072 or public.public_numbers().e != 65537:
        raise StateError('Unsupported recipient')
    header = json.dumps({'format': 'glo-inventory-envelope-v1', 'repository': repository, 'session': session}, sort_keys=True, separators=(',', ':'))
    key, nonce = os.urandom(32), os.urandom(12)
    cipher = AESGCM(key).encrypt(nonce, json.dumps(detail, sort_keys=True).encode(), header.encode())
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    b64 = lambda b: base64.b64encode(b).decode()
    return json.dumps({'header': header, 'nonce': b64(nonce), 'wrapped_key': b64(wrapped), 'ciphertext': b64(cipher)}).encode()


def run(store, secret, recipient, repository, session, out):
    # No seed fallback, migrations, source checks, or writeback to production.
    body = store.latest_state()
    if body is None:
        raise StateError('Latest production state unavailable')
    with tempfile.TemporaryDirectory(prefix='glo-inventory-') as tmp:
        root = Path(tmp) / 'state'
        restore_state(body, secret, root)
        detail, summary = inventory(root / 'reference.sqlite')
        encrypted = seal(detail, recipient, repository, session)
    out = Path(out)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    (out / 'inventory.enc.json').write_bytes(encrypted)
    (out / 'summary.json').write_text(json.dumps(summary, sort_keys=True))
    return summary


def main():
    os.umask(0o077)
    try:
        run(GitHubStore(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN']),
            os.environ['GLO_STATE_KEY'], os.environ['INVENTORY_RECIPIENT'],
            os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'],
            os.environ['INVENTORY_OUT'])
        print('Read-only inventory encrypted. See aggregate summary; scientific identity review remains required.')
        return 0
    except Exception:
        print('Inventory stopped safely; no production state or documents changed. Private error details suppressed.')
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
