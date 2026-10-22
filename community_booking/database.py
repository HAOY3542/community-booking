import json
import sqlite3
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS bookings(
 id TEXT PRIMARY KEY, management_hash TEXT NOT NULL, alias TEXT NOT NULL,
 facility TEXT NOT NULL, resource TEXT NOT NULL, booking_date TEXT NOT NULL,
 start_minute INTEGER NOT NULL, duration INTEGER NOT NULL, party INTEGER NOT NULL CHECK(party>0),
 guardian_required INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','cancelled')),
 checked_in INTEGER NOT NULL DEFAULT 0 CHECK(checked_in>=0 AND checked_in<=party),
 ticket_version INTEGER NOT NULL DEFAULT 1, agreement_version TEXT NOT NULL,
 agreement_hash TEXT NOT NULL, created_at TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS slots(
 booking_id TEXT REFERENCES bookings(id), resource TEXT NOT NULL, booking_date TEXT NOT NULL,
 start_minute INTEGER NOT NULL, party INTEGER NOT NULL,
 PRIMARY KEY(booking_id,start_minute)
);
CREATE INDEX IF NOT EXISTS slots_resource_date ON slots(resource,booking_date,start_minute);
CREATE TABLE IF NOT EXISTS overrides(
 resource TEXT NOT NULL, booking_date TEXT NOT NULL, start_minute INTEGER NOT NULL,
 capacity INTEGER NOT NULL CHECK(capacity>=0), PRIMARY KEY(resource,booking_date,start_minute)
);
CREATE TABLE IF NOT EXISTS checkins(
 id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
 request_id TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL, added INTEGER NOT NULL CHECK(added>0),
 total INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, actor TEXT NOT NULL,
 booking_id TEXT, details TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            # This candidate has one schema, and must never silently migrate an unrelated database.
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "schema_version" not in tables:
                raise ValueError("Refusing to initialize an unrelated database")
            if "schema_version" in tables and db.execute("SELECT version FROM schema_version").fetchall() != [(1,)]:
                raise ValueError("Unsupported schema version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO schema_version VALUES(1)")
        path.chmod(0o600)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.execute("COMMIT")
        except Exception:
            if write and db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()


def row_one(db, query, args=()):
    cursor = db.execute(query, args)
    row = cursor.fetchone()
    return dict(zip([c[0] for c in cursor.description], row)) if row else None


def rows_all(db, query, args=()):
    cursor = db.execute(query, args)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def audit(db, settings, kind, actor, booking_id=None, **details):
    db.execute("INSERT INTO audit(kind,actor,booking_id,details,created_at) VALUES(?,?,?,?,?)",
               (kind, actor, booking_id, json.dumps(details, ensure_ascii=False), settings.now().isoformat()))
