"""The local index: one SQLite file kept in step with WeChat's live databases.

The first sync reads everything once. Later syncs compare file size and mtime,
then read only new rows plus a short recent window (to catch revokes and edits),
so a query normally starts in milliseconds and a busy day of new messages costs
a second or two.
"""
import fcntl
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import paths
from .decode import decode
from .source import Account, md5

SCHEMA_VERSION = 1
RECENT_WINDOW = 3 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS contacts(
  username TEXT PRIMARY KEY, kind TEXT, nick TEXT, remark TEXT, alias TEXT,
  pinyin TEXT, initials TEXT, remark_pinyin TEXT, remark_initials TEXT,
  description TEXT, deleted INTEGER);
CREATE TABLE IF NOT EXISTS members(room TEXT, username TEXT, display TEXT, PRIMARY KEY(room, username));
CREATE TABLE IF NOT EXISTS chats(
  chat_id TEXT PRIMARY KEY, is_group INTEGER, msg_count INTEGER, first_ts INTEGER, last_ts INTEGER,
  unread INTEGER DEFAULT 0, biz INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  db TEXT NOT NULL, tbl TEXT NOT NULL, local_id INTEGER NOT NULL, server_id INTEGER,
  chat_id TEXT, sender_id TEXT, from_self INTEGER, ts INTEGER, sort_seq INTEGER,
  type INTEGER, subtype INTEGER, kind TEXT, text TEXT, title TEXT, url TEXT, source TEXT,
  quote_sender TEXT, quote_text TEXT, file_name TEXT, file_size INTEGER, media TEXT,
  extra TEXT, status TEXT, body TEXT,
  UNIQUE(db, tbl, local_id));
CREATE INDEX IF NOT EXISTS m_chat_ts ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS m_sender_ts ON messages(sender_id, ts);
CREATE INDEX IF NOT EXISTS m_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS m_kind_ts ON messages(kind, ts);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(body, content='messages', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS m_ai AFTER INSERT ON messages BEGIN
  INSERT INTO fts(rowid, body) VALUES (new.id, new.body); END;
CREATE TRIGGER IF NOT EXISTS m_ad AFTER DELETE ON messages BEGIN
  INSERT INTO fts(fts, rowid, body) VALUES ('delete', old.id, old.body); END;
CREATE TRIGGER IF NOT EXISTS m_au AFTER UPDATE OF body ON messages BEGIN
  INSERT INTO fts(fts, rowid, body) VALUES ('delete', old.id, old.body);
  INSERT INTO fts(rowid, body) VALUES (new.id, new.body); END;
CREATE TABLE IF NOT EXISTS sync_tables(db TEXT, tbl TEXT, max_local_id INTEGER, PRIMARY KEY(db, tbl));
CREATE TABLE IF NOT EXISTS sync_files(db TEXT PRIMARY KEY, sig TEXT);
"""

COLUMNS = ('db', 'tbl', 'local_id', 'server_id', 'chat_id', 'sender_id', 'from_self', 'ts', 'sort_seq',
           'type', 'subtype', 'kind', 'text', 'title', 'url', 'source', 'quote_sender', 'quote_text',
           'file_name', 'file_size', 'media', 'extra', 'status', 'body')
UPSERT = ('INSERT INTO messages(%s) VALUES(%s) ON CONFLICT(db, tbl, local_id) DO UPDATE SET %s' % (
    ','.join(COLUMNS), ','.join('?' * len(COLUMNS)),
    ','.join('%s=excluded.%s' % (c, c) for c in COLUMNS[3:])))


def index_path():
    return paths.home() / 'index.db'


def connect(path=None):
    path = Path(path or index_path())
    if not path.exists():
        os.close(os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o600))
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    return con


def meta(con, key, default=None):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute('INSERT INTO meta VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


@contextmanager
def sync_lock(timeout=120):
    lock = open(str(paths.home() / '.sync.lock'), 'w')
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() > deadline:
                lock.close()
                raise RuntimeError('another wx sync is still running')
            time.sleep(0.2)
    try:
        yield
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _body(d):
    parts, seen = [], set()
    for v in (d['text'], d['title'], d['source'], d['file_name'], d['url'], d['quote_text']):
        if v and v not in seen:
            seen.add(v)
            parts.append(v)
    return '\n'.join(parts)


class Syncer:
    """Pull new/changed rows from one account's live databases into the index."""

    def __init__(self, con, account, progress=None):
        self.con = con
        self.acct = account
        self.progress = progress or (lambda *a: None)
        self.chat_by_md5 = {}
        self.missing_keys = []
        self.changed = 0

    def _file_sig(self, rel):
        return self.acct.signature(self.acct.root / rel)

    def _stored_sig(self, rel):
        row = self.con.execute('SELECT sig FROM sync_files WHERE db=?', (rel,)).fetchone()
        return row[0] if row else None

    def _set_sig(self, rel, sig):
        self.con.execute('INSERT INTO sync_files VALUES(?, ?) ON CONFLICT(db) DO UPDATE SET sig=excluded.sig', (rel, sig))

    def sync_contacts(self, force=False):
        rel = 'contact/contact.db'
        sig = self._file_sig(rel)
        if not force and self._stored_sig(rel) == sig:
            self._load_chat_map()
            return
        self.progress('contacts')
        contacts, rooms = self.acct.contacts()
        with self.con:
            for c in contacts:
                self.con.execute(
                    'INSERT INTO contacts VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET '
                    'kind=excluded.kind, nick=excluded.nick, remark=excluded.remark, alias=excluded.alias, '
                    'pinyin=excluded.pinyin, initials=excluded.initials, remark_pinyin=excluded.remark_pinyin, '
                    'remark_initials=excluded.remark_initials, description=excluded.description, deleted=excluded.deleted',
                    (c['username'], c['kind'], c['nick_name'], c['remark'], c['alias'], c['quan_pin'],
                     c['pin_yin_initial'], c['remark_quan_pin'], c['remark_pin_yin_initial'],
                     c['description'], c['delete_flag']))
            for room in rooms:
                self.con.execute('DELETE FROM members WHERE room=?', (room['room'],))
                for username, display in room['members']:
                    self.con.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)', (room['room'], username, display))
            self._set_sig(rel, sig)
        self._load_chat_map()

    def _load_chat_map(self):
        for (username,) in self.con.execute('SELECT username FROM contacts'):
            self.chat_by_md5[md5(username)] = username
        for (room,) in self.con.execute('SELECT DISTINCT room FROM members'):
            self.chat_by_md5[md5(room)] = room

    def sync_sessions(self):
        with self.con:
            for s in self.acct.sessions():
                self.con.execute('UPDATE chats SET unread=? WHERE chat_id=?', (s['unread_count'], s['username']))

    def sync_messages(self, force=False):
        for path in self.acct.message_dbs():
            rel = 'message/' + path.name
            sig = self._file_sig(rel)
            if not force and self._stored_sig(rel) == sig:
                continue
            try:
                conn = self.acct.open(rel)
            except KeyError:
                self.missing_keys.append(rel)
                continue
            with conn:
                self._sync_db(rel, conn, force)
            with self.con:
                self._set_sig(rel, sig)

    def _sync_db(self, rel, conn, force):
        is_biz = rel.endswith('biz_message_0.db') or 'biz_message' in rel
        senders = {r['rowid']: r['user_name']
                   for r in conn.execute('SELECT rowid, user_name FROM Name2Id')}
        from .source import MSG_TABLE
        tables = [t for t in conn.tables() if MSG_TABLE.match(t)]
        for tbl in tables:
            chat_id = self.chat_by_md5.get(tbl[4:])
            row = self.con.execute('SELECT max_local_id FROM sync_tables WHERE db=? AND tbl=?', (rel, tbl)).fetchone()
            seen_max = 0 if (force or not row) else row[0]
            floor = seen_max - 0  # new rows strictly greater than seen_max
            where = 'local_id > %d' % floor
            if seen_max and not force:
                # also re-read a recent window to pick up revokes/edits to already-indexed rows
                cutoff = int(time.time()) - RECENT_WINDOW
                where = '(local_id > %d OR create_time >= %d)' % (floor, cutoff)
            new_max = seen_max
            batch = []
            query = ('SELECT local_id, server_id, local_type, sort_seq, real_sender_id, create_time, '
                     'message_content, packed_info_data FROM "%s" WHERE %s ORDER BY local_id' % (tbl, where))
            for r in conn.iterate(query):
                rec = self._record(rel, tbl, chat_id, is_biz, r, senders)
                batch.append(rec)
                new_max = max(new_max, r['local_id'])
                if len(batch) >= 2000:
                    self._flush(batch)
                    batch = []
            if batch:
                self._flush(batch)
            if new_max != seen_max or force:
                with self.con:
                    self.con.execute('INSERT INTO sync_tables VALUES(?,?,?) ON CONFLICT(db,tbl) '
                                     'DO UPDATE SET max_local_id=excluded.max_local_id', (rel, tbl, new_max))
        if tables:
            self._recount(rel)

    def _record(self, rel, tbl, chat_id, is_biz, r, senders):
        import json
        sender = senders.get(r['real_sender_id'])
        is_group = bool(chat_id and chat_id.endswith('@chatroom'))
        d = decode(r['local_type'], r['message_content'], r['packed_info_data'], is_group)
        if is_group and d['sender_hint'] and not sender:
            sender = d['sender_hint']
        values = {
            'db': rel, 'tbl': tbl, 'local_id': r['local_id'], 'server_id': r['server_id'],
            'chat_id': chat_id, 'sender_id': sender,
            'from_self': 1 if sender == self.acct.self_id else (0 if sender else None),
            'ts': r['create_time'], 'sort_seq': r['sort_seq'],
            'type': d['type'], 'subtype': d['subtype'], 'kind': d['kind'], 'text': d['text'],
            'title': d['title'], 'url': d['url'], 'source': d['source'],
            'quote_sender': d['quote_sender'], 'quote_text': d['quote_text'],
            'file_name': d['file_name'], 'file_size': d['file_size'], 'media': d['media'],
            'extra': json.dumps(d['extra'], ensure_ascii=False) if d['extra'] else None,
            'status': d['status'], 'body': _body(d)}
        return tuple(values[c] for c in COLUMNS)

    def _flush(self, batch):
        with self.con:
            self.con.executemany(UPSERT, batch)
        self.changed += len(batch)
        self.progress('messages', self.changed)

    def _recount(self, rel):
        with self.con:
            self.con.execute(
                'INSERT INTO chats(chat_id, is_group, msg_count, first_ts, last_ts, biz) '
                'SELECT chat_id, MAX(chat_id LIKE "%@chatroom"), COUNT(*), MIN(ts), MAX(ts), ? '
                'FROM messages WHERE db=? AND chat_id IS NOT NULL GROUP BY chat_id '
                'ON CONFLICT(chat_id) DO UPDATE SET msg_count=excluded.msg_count, '
                'first_ts=MIN(chats.first_ts, excluded.first_ts), last_ts=MAX(chats.last_ts, excluded.last_ts)',
                (1 if 'biz' in rel else 0, rel))

    def run(self, force=False):
        self.sync_contacts(force)
        self.sync_messages(force)
        self.sync_sessions()
        set_meta(self.con, 'last_sync', int(time.time()))
        set_meta(self.con, 'self_id', self.acct.self_id)
        self.con.commit()
        return {'changed': self.changed, 'missing_keys': self.missing_keys}


def sync(con, account, force=False, progress=None):
    with sync_lock():
        return Syncer(con, account, progress).run(force=force)
