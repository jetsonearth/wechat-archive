"""Read-only access to one WeChat 4.x account's live databases."""
import hashlib
import re
from pathlib import Path

from .decode import room_members
from .keystore import KeyStore
from .sqlcipher import Connection

MSG_DB = re.compile(r'^(message|biz_message)_\d+\.db$')
MSG_TABLE = re.compile(r'^Msg_[0-9a-f]{32}$')


def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def contact_kind(username, local_type):
    if username.endswith('@chatroom'):
        return 'group'
    if username.startswith('gh_'):
        return 'official'
    if username.endswith('@openim'):
        return 'wecom'
    if username in ('filehelper', 'weixin', 'fmessage', 'medianote', 'floatbottle', 'newsapp', 'notifymessage'):
        return 'system'
    return {1: 'friend', 3: 'stranger'}.get(local_type, 'other')


class Account:
    def __init__(self, db_root, keystore_path, self_id):
        self.root = Path(db_root)
        self.base = self.root.parent
        self.keys = KeyStore(keystore_path)
        self.self_id = self_id

    def open(self, rel):
        path = self.root / rel
        key = self.keys.key_for(path)
        if not key:
            raise KeyError(rel)
        return Connection(path, key)

    def message_dbs(self):
        d = self.root / 'message'
        return sorted((p for p in d.glob('*.db') if MSG_DB.match(p.name)),
                      key=lambda p: (p.name.startswith('biz'), int(re.findall(r'\d+', p.name)[0])))

    @staticmethod
    def signature(path):
        """Cheap change detector: size and mtime of the db and its WAL."""
        parts = []
        for p in (Path(path), Path(str(path) + '-wal')):
            try:
                st = p.stat()
                parts.append('%d:%d' % (st.st_size, int(st.st_mtime_ns)))
            except FileNotFoundError:
                parts.append('-')
        return '|'.join(parts)

    def contacts(self):
        rows = []
        with self.open('contact/contact.db') as db:
            for table in ('contact', 'stranger'):
                for r in db.iterate('SELECT username, local_type, alias, remark, nick_name, quan_pin, pin_yin_initial, '
                                    'remark_quan_pin, remark_pin_yin_initial, description, delete_flag FROM %s' % table):
                    if not r['username']:
                        continue
                    r['kind'] = contact_kind(r['username'], r['local_type'])
                    r['table'] = table
                    rows.append(r)
            rooms = []
            for r in db.iterate('SELECT username, owner, ext_buffer FROM chat_room'):
                ext = r['ext_buffer'] if isinstance(r['ext_buffer'], bytes) else b''
                try:
                    members = room_members(ext)
                except Exception:
                    members = []
                rooms.append({'room': r['username'], 'owner': r['owner'], 'members': members})
        return rows, rooms

    def sessions(self):
        try:
            with self.open('session/session.db') as db:
                return db.execute('SELECT username, unread_count, last_timestamp, is_hidden FROM SessionTable')
        except (KeyError, RuntimeError):
            return []
