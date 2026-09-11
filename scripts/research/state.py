"""Transactional discovery queue and idempotent publication outbox.

The caller must hold the worker's single-process lock before recover(). Remote
publication uses the outbox ID as its unique key, then acknowledges locally.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = '/joe mccann/current'
MONTHS = ('January February March April May June July August September October November December').split()


def _safe_error(error):
    if isinstance(error, BaseException):
        if error.__class__.__name__ == 'ModelError':
            msg = str(error).strip()
            return msg[:200] if msg else 'ModelError'
        return type(error).__name__
    return 'processing_failed'


def date_scopes(now=None, timezone='America/New_York'):
    now = now or datetime.now(ZoneInfo(timezone))
    if now.tzinfo is None:
        raise ValueError('An aware timestamp is required')
    today = now.astimezone(ZoneInfo(timezone)).date()
    return [(f'{d.year}/{MONTHS[d.month-1]}/{MONTHS[d.month-1][:3]} {d.day:02}'.lower(), d.isoformat())
            for d in (today - timedelta(days=1), today)]


def eligible(entry):
    path = entry.get('path_lower', '')
    return (entry.get('.tag') == 'file' and path.lower().endswith('.pdf')
            and not re.search(r'(?<![a-z])(?:the[\s_-]*)?market[\s_-]*ear(?![a-z])', path, re.I))


def work_key(entry):
    return hashlib.sha256((entry['id'] + '\0' + entry['rev']).encode()).hexdigest()


class State:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink() or path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
            raise ValueError('Symlink state rejected')
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS cursors(scope TEXT PRIMARY KEY, cursor TEXT NOT NULL, folder_date TEXT);
        CREATE TABLE IF NOT EXISTS work(
          key TEXT PRIMARY KEY, file_id TEXT NOT NULL, rev TEXT NOT NULL,
          path TEXT NOT NULL, scope TEXT NOT NULL, folder_date TEXT,
          metadata TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
          error TEXT, result TEXT, UNIQUE(file_id,rev));
        CREATE TABLE IF NOT EXISTS ingestion(
          work_key TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
          pdf TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS outbox(
          id TEXT PRIMARY KEY, work_key TEXT NOT NULL, payload TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending');
        ''')

        # Dropbox paths are case-insensitive. Collapse old display-case aliases;
        # ambiguous cursors trigger an idempotent relist, preserving folder dates.
        with self.db:
            rows = self.db.execute('SELECT * FROM cursors WHERE scope != lower(scope)').fetchall()
            for row in rows:
                canonical = row['scope'].lower()
                existing = self.db.execute('SELECT * FROM cursors WHERE scope=?', (canonical,)).fetchone()
                cursor = '' if existing else row['cursor']
                folder_date = row['folder_date'] or (existing['folder_date'] if existing else None)
                self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)', (canonical,cursor,folder_date))
                self.db.execute('DELETE FROM cursors WHERE scope=?', (row['scope'],))
            self.db.execute('UPDATE work SET scope=lower(scope) WHERE scope != lower(scope)')

    def close(self):
        self.db.close()

    def scopes(self):
        return [r[0] for r in self.db.execute('SELECT scope FROM cursors ORDER BY scope')]

    def cursor(self, scope):
        row = self.db.execute('SELECT cursor FROM cursors WHERE scope=?', (scope.lower(),)).fetchone()
        return (row[0] or None) if row else None

    def reset_cursor(self, scope):
        # Revision keys survive a cursor reset and make a full relist idempotent.
        with self.db:
            self.db.execute("UPDATE cursors SET cursor='' WHERE scope=?", (scope.lower(),))

    def ingest_page(self, scope, page, folder_date=None):
        if (not isinstance(scope, str) or scope.startswith('/') or ':' in scope or '\\' in scope
                or any(p in ('', '.', '..') for p in scope.split('/'))):
            raise ValueError('Invalid scope')
        scope = scope.lower()
        prefix = ROOT + '/' + scope
        if not isinstance(page.get('cursor'), str) or not page['cursor']:
            raise ValueError('Missing cursor')
        added = 0
        with self.db:
            previous = self.db.execute('SELECT folder_date FROM cursors WHERE scope=?', (scope.lower(),)).fetchone()
            folder_date = folder_date or (previous[0] if previous else None)
            for entry in page['entries']:
                path = entry.get('path_lower', '')
                if not path.startswith(prefix + '/') or any(p in ('', '.', '..') for p in path.split('/')[1:]):
                    raise ValueError('Queue entry outside scope')
                if entry.get('.tag') == 'deleted':
                    # Deleting a directory tombstones all unfinished descendants.
                    rows = self.db.execute("SELECT key,path FROM work WHERE status != 'published'").fetchall()
                    for row in rows:
                        if row['path'] == path or row['path'].startswith(path + '/'):
                            self.db.execute("UPDATE work SET status='deleted' WHERE key=?", (row['key'],))
                            self.db.execute("UPDATE outbox SET status='cancelled' WHERE work_key=? AND status='pending'", (row['key'],))
                    continue
                if not eligible(entry):
                    continue
                if not all(isinstance(entry.get(k), str) and entry[k] for k in ('id','rev','content_hash')):
                    raise ValueError('Incomplete file revision')
                key = work_key(entry)
                if self.db.execute('SELECT 1 FROM work WHERE key=?', (key,)).fetchone():
                    continue
                # A new revision supersedes unpublished work, never published history.
                self.db.execute("UPDATE outbox SET status='cancelled' WHERE status='pending' AND work_key IN (SELECT key FROM work WHERE file_id=? AND rev!=?)", (entry['id'], entry['rev']))
                self.db.execute("UPDATE work SET status='superseded' WHERE file_id=? AND rev!=? AND status NOT IN ('published','deleted')", (entry['id'], entry['rev']))
                added += self.db.execute('''INSERT OR IGNORE INTO work(key,file_id,rev,path,scope,folder_date,metadata)
                  VALUES(?,?,?,?,?,?,?)''', (key,entry['id'],entry['rev'],path,scope,folder_date,json.dumps(entry))).rowcount
            self.db.execute('INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET cursor=excluded.cursor,folder_date=excluded.folder_date', (scope,page['cursor'],folder_date))
        return added

    def unparsed(self, limit=20):
        rows = self.db.execute("""SELECT w.*, COALESCE(i.attempts,0) AS parse_attempts
            FROM work w LEFT JOIN ingestion i ON i.work_key=w.key
            WHERE w.status='pending' AND (i.status IS NULL OR i.status='pending')
            AND COALESCE(i.available_at,0)<=?
            ORDER BY COALESCE(i.attempts,0)>0, w.folder_date DESC, w.rowid DESC LIMIT ?""", (time.time(),limit))
        return [{**dict(r), 'metadata':json.loads(r['metadata'])} for r in rows]

    def claim_parse(self, key):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO ingestion(work_key) SELECT key FROM work WHERE key=? AND status='pending'", (key,))
            return bool(self.db.execute("""UPDATE ingestion SET status='parsing',attempts=attempts+1
                WHERE work_key=? AND status='pending' AND available_at<=?
                AND EXISTS(SELECT 1 FROM work WHERE key=? AND status='pending')""", (key,time.time(),key)).rowcount)

    def parsed(self, key, pdf):
        with self.db:
            self.db.execute("UPDATE ingestion SET status='ready',pdf=?,error=NULL WHERE work_key=? AND status='parsing'", (pdf,key))

    def parse_retry(self, key, error, delay=60):
        safe = _safe_error(error)
        with self.db:
            self.db.execute("""UPDATE ingestion SET status=CASE WHEN attempts>=6 THEN 'held' ELSE 'pending' END,
                error=?,available_at=? WHERE work_key=? AND status='parsing'""",
                (safe,time.time()+max(0,delay),key))
            self.db.execute('''UPDATE work SET status='complete',result=?,error=? WHERE key=? AND status='pending'
                AND EXISTS(SELECT 1 FROM ingestion WHERE work_key=? AND status='held')''',
                (json.dumps({'status':'held','stage':'extraction','error':safe}),safe,key,key))

    def ready(self, limit=20):
        rows = self.db.execute("""SELECT w.*,i.pdf FROM work w JOIN ingestion i ON i.work_key=w.key
            WHERE w.status='pending' AND i.status='ready' AND w.available_at<=?
            ORDER BY w.attempts>0,w.folder_date DESC,w.rowid DESC LIMIT ?""", (time.time(),limit))
        return [{**dict(r), 'metadata':json.loads(r['metadata'])} for r in rows]

    def pending(self, limit=20):
        rows = self.db.execute("SELECT * FROM work WHERE status='pending' AND available_at<=? ORDER BY rowid LIMIT ?", (time.time(),limit))
        return [{**dict(r), 'metadata':json.loads(r['metadata'])} for r in rows]

    def claim(self, key):
        with self.db:
            return bool(self.db.execute("UPDATE work SET status='processing',attempts=attempts+1 WHERE key=? AND status='pending' AND available_at<=?", (key,time.time())).rowcount)

    def is_processing(self, key):
        row = self.db.execute('SELECT status FROM work WHERE key=?', (key,)).fetchone()
        return bool(row and row[0] == 'processing')

    def complete(self, key, result=None, publications=()):
        with self.db:
            row = self.db.execute('SELECT status FROM work WHERE key=?', (key,)).fetchone()
            if not row or row[0] != 'processing':
                raise ValueError('Only claimed work can complete')
            for payload in publications:
                publication_id = payload['id']
                existing = self.db.execute('SELECT work_key,payload FROM outbox WHERE id=?', (publication_id,)).fetchone()
                encoded = json.dumps(payload, sort_keys=True)
                if existing and (existing[0] != key or existing[1] != encoded):
                    previous = self.db.execute('SELECT file_id,rev,status FROM work WHERE key=?', (existing[0],)).fetchone()
                    current = self.db.execute('SELECT file_id,rev FROM work WHERE key=?', (key,)).fetchone()
                    if not (previous and previous['file_id'] == current['file_id']
                            and previous['rev'] != current['rev']
                            and previous['status'] in ('superseded', 'published')):
                        raise ValueError('Publication ID collision')
                    self.db.execute("UPDATE outbox SET work_key=?,payload=?,status='pending' WHERE id=?", (key,encoded,publication_id))
                else:
                    self.db.execute('INSERT OR IGNORE INTO outbox(id,work_key,payload) VALUES(?,?,?)', (publication_id,key,encoded))
            self.db.execute("UPDATE work SET status='complete',result=?,error=NULL WHERE key=?", (json.dumps(result),key))

    def retry(self, key, error, delay=60):
        # Error must be a safe classification, never an HTTP body or credentials.
        safe_error = _safe_error(error)
        with self.db:
            self.db.execute("UPDATE work SET status='pending',error=?,available_at=? WHERE key=? AND status='processing'", (safe_error,time.time()+max(0,delay),key))

    def recover(self):
        with self.db:
            self.db.execute("UPDATE ingestion SET status='pending' WHERE status='parsing'")
            return self.db.execute("UPDATE work SET status='pending' WHERE status='processing'").rowcount

    def outbox(self, limit=20):
        return [{'id':r['id'], 'work_key':r['work_key'], 'payload':json.loads(r['payload'])}
                for r in self.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY rowid LIMIT ?", (limit,))]

    def outbox_current(self, publication_id, key):
        return bool(self.db.execute("""SELECT 1 FROM outbox o JOIN work w ON w.key=o.work_key
            WHERE o.id=? AND o.work_key=? AND o.status='pending' AND w.status='complete'""",
            (publication_id,key)).fetchone())

    def published(self, publication_id):
        with self.db:
            self.db.execute("UPDATE outbox SET status='published' WHERE id=? AND status='pending'", (publication_id,))
            self.db.execute("""UPDATE work SET status='published' WHERE key=(SELECT work_key FROM outbox WHERE id=?)
                AND status='complete' AND NOT EXISTS(SELECT 1 FROM outbox WHERE work_key=work.key AND status='pending')""", (publication_id,))
