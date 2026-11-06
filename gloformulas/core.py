import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def key(*parts):
    return hashlib.sha256(encoded(parts).encode()).hexdigest()


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(Path(__file__).with_name('schema.sql').read_text())
    return db


def source(db, kind, url, payload, published_at=None):
    raw = encoded(payload)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    sid = key(url, digest)
    db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',
               (sid, kind, url, now(), published_at, digest, raw))
    return sid


def flag(db, entity_type, entity_id, code, detail):
    db.execute('INSERT OR IGNORE INTO flags(id,entity_type,entity_id,code,detail) VALUES(?,?,?,?,?)',
               (key(entity_type, entity_id, code, detail), entity_type, entity_id, code, detail))


def audit(db, action, entity_id, detail):
    db.execute('INSERT INTO audit_log(created_at,action,entity_id,detail_json) VALUES(?,?,?,?)',
               (now(), action, entity_id, encoded(detail)))
