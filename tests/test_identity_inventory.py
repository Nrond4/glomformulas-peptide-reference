import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from gloformulas.core import connect
from gloformulas.identity_inventory import inventory, run
from gloformulas.monitor_state import StateError, pack_state

KEY = 'synthetic-inventory-key-never-production-123456'

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.path = self.state / 'reference.sqlite'
        db = connect(self.path)
        db.execute("INSERT INTO sources VALUES('source','guide','private-url','today',NULL,'hash','private raw')")
        for i, status in enumerate(('imported', 'unresolved', 'reviewed')):
            db.execute('INSERT INTO products VALUES(?,?,?,?,?,?)', (str(i), 'private-name', str(i), status, 'source', 'private heading'))
            db.execute("INSERT INTO audit_log(created_at,action,entity_id,detail_json) VALUES('today','identity-review',?,'private reference')", (str(i),))
        db.commit()
        db.close()
        (self.state / 'source-config.json').write_text('{}')
    def tearDown(self):
        self.tmp.cleanup()
    def test_complete_read_only_and_redacted(self):
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        detail, summary = inventory(self.path)
        self.assertEqual([p['id'] for p in detail['products']], ['0', '1', '2'])
        self.assertTrue(all(p['provenance'] and p['identity_review_references'] and p['review_required'] for p in detail['products']))
        self.assertEqual(summary, {'products': 3, 'identity_status_counts': {'imported': 1, 'unresolved': 1, 'reviewed': 1, 'other': 0}})
        self.assertNotIn('private', json.dumps(summary))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.iterdir()})
    def test_missing_database_fails_without_creation(self):
        with self.assertRaises(Exception): inventory(self.root / 'absent.sqlite')
        self.assertFalse((self.root / 'absent.sqlite').exists())
    def test_missing_artifact_no_seed_fallback_or_output(self):
        store = Mock(); store.latest_state.return_value = None
        with self.assertRaises(StateError): run(store, KEY, '{}', 'owner/repo', '123-1', self.root / 'out')
        store.seed.assert_not_called()
        self.assertFalse((self.root / 'out').exists())

@unittest.skipUnless(importlib.util.find_spec('cryptography'), 'monitor extra required')
class EncryptedInventoryTests(InventoryTests):
    def recipient(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        numbers = private.public_key().public_numbers()
        def encode(n): return base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, 'big')).decode().rstrip('=')
        return private, json.dumps({'kty': 'RSA', 'n': encode(numbers.n), 'e': encode(numbers.e)})
    def test_roundtrip_cleanup_and_authenticated_context(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
        private, jwk = self.recipient()
        store = Mock(); store.latest_state.return_value = pack_state(self.state, KEY)
        out = self.root / 'out'
        temporary = self.root / 'temporary'
        temporary.mkdir()
        import tempfile as temp_module
        original = temp_module.TemporaryDirectory
        with patch('gloformulas.identity_inventory.tempfile.TemporaryDirectory', side_effect=lambda **kw: original(dir=temporary, **kw)):
            run(store, KEY, jwk, 'owner/repo', '123-1', out)
        self.assertEqual(list(temporary.iterdir()), [])
        self.assertEqual({p.name for p in out.iterdir()}, {'summary.json', 'inventory.enc.json'})
        raw = (out / 'inventory.enc.json').read_bytes()
        self.assertNotIn(b'private-name', raw)
        env = json.loads(raw)
        key = private.decrypt(base64.b64decode(env['wrapped_key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        plain = AESGCM(key).decrypt(base64.b64decode(env['nonce']), base64.b64decode(env['ciphertext']), env['header'].encode())
        self.assertEqual(len(json.loads(plain)['products']), 3)
        with self.assertRaises(InvalidTag): AESGCM(key).decrypt(base64.b64decode(env['nonce']), base64.b64decode(env['ciphertext']), b'other-session')
    def test_corrupt_restore_and_invalid_recipient_no_output(self):
        store = Mock(); store.latest_state.return_value = b'corrupt'
        with self.assertRaises(StateError): run(store, KEY, '{}', 'owner/repo', '123-1', self.root / 'out')
        self.assertFalse((self.root / 'out').exists())
        store.latest_state.return_value = pack_state(self.state, KEY)
        with self.assertRaises(StateError): run(store, KEY, '{"kty":"EC"}', 'owner/repo', '123-1', self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

class WorkflowTests(unittest.TestCase):
    def test_isolation_retention_and_cleanup(self):
        text = (Path(__file__).parents[1] / '.github/workflows/identity-inventory.yml').read_text()
        self.assertNotIn('schedule:', text)
        self.assertNotIn('id-token:', text)
        self.assertNotIn('contents: write', text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('retention-days: 1', text)
        self.assertIn('if: always()', text)
        self.assertNotIn('actions_monitor', text)
        self.assertNotIn('docs_actions', text)
