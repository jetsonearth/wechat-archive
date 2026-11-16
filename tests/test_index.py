"""End-to-end: sync a synthetic account into the index and query it.

No encryption and no real data — a fake account yields rows shaped like
WeChat's, so the index, decode and query layers are exercised together.
"""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wxa import index, query


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


NOW = int(time.time())
SELF = 'wxid_self'
FRIEND = 'wxid_friend_c'   # "Alex"
ROOM = '12345@chatroom'


class Conn:
    """Mimics wxa.sqlcipher.Connection.iterate/execute/tables for a table dict."""
    def __init__(self, tables):
        self._t = tables

    def tables(self):
        return list(self._t)

    def iterate(self, sql, params=()):
        return iter(self._run(sql))

    def execute(self, sql, params=()):
        return self._run(sql)

    def _run(self, sql):
        if 'Name2Id' in sql:
            return [{'rowid': 1, 'user_name': SELF}, {'rowid': 2, 'user_name': FRIEND}]
        for name, rows in self._t.items():
            if '"%s"' % name in sql:
                return rows
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def msg(local_id, sender_rowid, ts, body, local_type=1, packed=None):
    return {'local_id': local_id, 'server_id': 1000 + local_id, 'local_type': local_type,
            'sort_seq': ts, 'real_sender_id': sender_rowid, 'create_time': ts,
            'message_content': body, 'packed_info_data': packed}


class FakeAccount:
    self_id = SELF

    def __init__(self, root, extra=None):
        self.root = Path(root)
        self.base = self.root.parent
        tbl = 'Msg_' + md5(FRIEND)
        room_tbl = 'Msg_' + md5(ROOM)
        self._dbs = {
            'message/message_0.db': {
                tbl: [
                    msg(1, 2, NOW - 400, '方案我看了'),
                    msg(2, 1, NOW - 390, '好的谢谢'),
                    msg(3, 2, NOW - 100, '明天到深圳'),
                ] + (extra or []),
                room_tbl: [
                    msg(10, 2, NOW - 50, 'wxid_friend_c:\nUnitree just shipped the robots'),
                ],
            },
        }

    class _Keys:
        def coverage(self, root):
            return ['message/message_0.db'], []
    keys = _Keys()

    def message_dbs(self):
        return [self.root / 'message/message_0.db']

    def open(self, rel):
        return Conn(self._dbs[rel])

    def contacts(self):
        contacts = [
            {'username': SELF, 'local_type': 1, 'kind': 'friend', 'alias': '', 'remark': 'Me',
             'nick_name': 'Sam', 'quan_pin': '', 'pin_yin_initial': '', 'remark_quan_pin': '',
             'remark_pin_yin_initial': '', 'description': '', 'delete_flag': 0, 'table': 'contact'},
            {'username': FRIEND, 'local_type': 1, 'kind': 'friend', 'alias': '', 'remark': 'Alex',
             'nick_name': 'Alex Y', 'quan_pin': 'alex', 'pin_yin_initial': 'c',
             'remark_quan_pin': 'alex', 'remark_pin_yin_initial': 'c', 'description': '',
             'delete_flag': 0, 'table': 'contact'},
        ]
        rooms = [{'room': ROOM, 'owner': SELF, 'members': [(FRIEND, 'Alex')]}]
        return contacts, rooms

    def sessions(self):
        return []

    @staticmethod
    def signature(path):
        return str(time.time())


def build():
    tmp = tempfile.mkdtemp(prefix='wx-test-')
    root = Path(tmp) / 'db_storage'
    (root / 'message').mkdir(parents=True)
    con = index.connect(Path(tmp) / 'index.db')
    acct = FakeAccount(root)
    index.Syncer(con, acct).run(force=True)
    return con, acct, tmp


def test_message_count():
    con, _, _ = build()
    assert query.stats(con)['messages'] == 4


def test_with_person_resolves_by_remark():
    con, _, _ = build()
    res = query.with_person(con, 'alex', since=None, limit=50)
    assert 'ambiguous' not in res and res['matched'] == 3
    assert res['messages'][0]['sender'] in ('Alex', 'Me')


def test_said_includes_group_with_prefix_sender():
    con, _, _ = build()
    res = query.said_by(con, 'alex', since=None, limit=50)
    texts = [m['text'] for m in res['messages']]
    assert any('Unitree' in t for t in texts)  # said in the group, attributed via prefix


def test_search_trigram():
    con, _, _ = build()
    res = query.search(con, '深圳', since=None, limit=10)
    assert res['matched'] == 1 and '深圳' in res['messages'][0]['text']


def test_incremental_only_new_rows():
    con, acct, tmp = build()
    before = query.stats(con)['messages']
    # add one new message and re-sync without force
    tbl = 'Msg_' + md5(FRIEND)
    acct._dbs['message/message_0.db'][tbl].append(msg(4, 1, NOW - 10, '收到'))
    index.Syncer(con, acct).run(force=False)
    assert query.stats(con)['messages'] == before + 1


def test_who():
    con, _, _ = build()
    from wxa import names
    people = names.resolve_people(con, 'alex')
    assert people and people[0]['name'] == 'Alex'


def test_lean_json_output():
    import io, json, contextlib
    from wxa import cli
    con, _, _ = build()
    res = query.with_person(con, 'alex', since=None, limit=2)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.dump_json(res)
    out = json.loads(buf.getvalue())
    assert out['returned'] == 2 and 'more' in out and '--until' in out['more']
    assert out['chat_ids'] == {'Alex': FRIEND}
    for m in out['messages']:
        assert set(m) <= {'time', 'chat', 'sender', 'kind', 'text', 'quote_sender', 'quote_text', 'url', 'file_name'}
        assert 'server_id' not in m and 'local_id' not in m
    assert cli.clip('x' * 500).startswith('x' * 400) and '+100 chars' in cli.clip('x' * 500)


if __name__ == '__main__':
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    ok = 0
    for fn in fns:
        try:
            fn()
            ok += 1
        except Exception:
            print('FAIL', fn.__name__)
            traceback.print_exc()
    print('%d/%d index tests passed' % (ok, len(fns)))
    sys.exit(0 if ok == len(fns) else 1)
