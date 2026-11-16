"""Read-side queries over the index. Returns plain dicts; the CLI formats them."""
import json
import re
from datetime import datetime, timedelta, timezone

from . import names

TZ = timezone(timedelta(hours=8))


def to_ts(value, end=False):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    m = re.match(r'^(\d+)\s*(d|w|m)$', s)
    if m:
        n = int(m.group(1)) * {'d': 1, 'w': 7, 'm': 30}[m.group(2)]
        return int((datetime.now(TZ) - timedelta(days=n)).timestamp())
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s, '%Y-%m-%d')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    if end and len(s) <= 10:
        dt += timedelta(days=1)
    return int(dt.timestamp())


def fmt_time(ts):
    return datetime.fromtimestamp(ts, TZ).strftime('%Y-%m-%d %H:%M') if ts else ''


def _row(con, r, cache):
    sender = names.name_of(con, r['sender_id'], cache) if r['sender_id'] else None
    return {'time': fmt_time(r['ts']), 'ts': r['ts'],
            'chat': names.name_of(con, r['chat_id'], cache) if r['chat_id'] else r['tbl'],
            'chat_id': r['chat_id'],
            'sender': 'Me' if r['from_self'] else sender,
            'from_self': bool(r['from_self']), 'kind': r['kind'], 'text': r['text'],
            'url': r['url'], 'file_name': r['file_name'], 'media': r['media'],
            'quote_sender': names.name_of(con, None, cache) if False else r['quote_sender'],
            'quote_text': r['quote_text'],
            'source_db': r['db'], 'local_id': r['local_id'], 'server_id': r['server_id']}


def _collect(con, where, params, limit, order_desc=True, newest_first_output=False):
    cache = names.display_map(con)
    sql = ('SELECT * FROM messages WHERE %s ORDER BY ts %s, sort_seq %s LIMIT ?'
           % (where, 'DESC' if order_desc else 'ASC', 'DESC' if order_desc else 'ASC'))
    rows = con.execute(sql, (*params, limit)).fetchall()
    matched = con.execute('SELECT count(*) FROM messages WHERE %s' % where, params).fetchone()[0]
    out = [_row(con, r, cache) for r in rows]
    if order_desc and not newest_first_output:
        out.reverse()
    return {'matched': matched, 'returned': len(out), 'messages': out}


def _time_clause(since, until):
    clause, params = [], []
    if since:
        clause.append('ts >= ?')
        params.append(to_ts(since))
    if until:
        clause.append('ts < ?')
        params.append(to_ts(until, end=True))
    return clause, params


def _chat_ids_for_people(con, person):
    people = names.resolve_people(con, person)
    friends = [p for p in people if p['kind'] != 'group']
    return people, [p['username'] for p in friends]


def _primary_people(people):
    """Pick the people a person-query most likely means, or [] if genuinely ambiguous.

    A single real contact (friend/wecom) wins over strangers and officials, so an
    old stranger entry with the same name does not force disambiguation.
    """
    real = [p for p in people if p['kind'] in ('friend', 'wecom')]
    if len(real) == 1:
        return real
    if real:
        return real
    non_group = [p for p in people if p['kind'] != 'group']
    return non_group[:1] if len(non_group) == 1 else []


def read_chat(con, chat_id, since=None, until=None, limit=200):
    clause = ['chat_id = ?']
    params = [chat_id]
    tc, tp = _time_clause(since, until)
    clause += tc
    params += tp
    return _collect(con, ' AND '.join(clause), params, limit)


def with_person(con, person, since=None, until=None, limit=200, include_groups=False):
    people, _ = _chat_ids_for_people(con, person)
    if not people:
        return {'error': 'no_match', 'query': person, 'matched': 0, 'returned': 0, 'messages': []}
    primary = _primary_people(people)
    if len(primary) > 1:
        return {'ambiguous': [{'name': p['name'], 'username': p['username'], 'kind': p['kind']} for p in primary], 'query': person}
    if not primary:
        return {'ambiguous': [{'name': p['name'], 'username': p['username'], 'kind': p['kind']} for p in people[:12]], 'query': person}
    ids = [p['username'] for p in primary]
    placeholders = ','.join('?' * len(ids))
    clause = ['chat_id IN (%s)' % placeholders]
    params = list(ids)
    tc, tp = _time_clause(since, until)
    clause += tc
    params += tp
    result = _collect(con, ' AND '.join(clause), params, limit)
    result['resolved'] = [{'name': p['name'], 'username': p['username'], 'kind': p['kind']} for p in people[:len(ids)]]
    return result


def said_by(con, person, since='7d', until=None, limit=400):
    """What a person said across every chat (groups included), newest first."""
    people, _ = _chat_ids_for_people(con, person)
    if not people:
        return {'error': 'no_match', 'query': person, 'messages': []}
    primary = _primary_people(people)
    if len(primary) > 1:
        return {'ambiguous': [{'name': p['name'], 'username': p['username'], 'kind': p['kind']} for p in primary], 'query': person}
    ids = [p['username'] for p in primary] or [people[0]['username']]
    placeholders = ','.join('?' * len(ids))
    clause = ['sender_id IN (%s)' % placeholders]
    params = list(ids)
    tc, tp = _time_clause(since, until)
    clause += tc
    params += tp
    result = _collect(con, ' AND '.join(clause), params, limit, newest_first_output=True)
    result['resolved'] = [{'name': p['name'], 'username': p['username']} for p in people if p['username'] in ids]
    return result


def _fts_terms(keyword):
    tokens = [t for t in re.split(r'\s+', keyword.strip()) if t]
    return ' AND '.join('"%s"' % t.replace('"', '""') for t in tokens)


def _match_clause(keyword):
    """Trigram FTS for tokens of 3+ chars; LIKE fallback when any token is shorter.

    The trigram tokenizer cannot match a 1- or 2-character query (common for
    short Chinese words), so those fall back to a scan with LIKE.
    """
    tokens = [t for t in re.split(r'\s+', keyword.strip()) if t]
    if tokens and all(len(t) >= 3 for t in tokens):
        return 'messages.id IN (SELECT rowid FROM fts WHERE fts MATCH ?)', [_fts_terms(keyword)]
    clause = ' AND '.join(['body LIKE ?'] * len(tokens)) or '1'
    return clause, ['%' + t + '%' for t in tokens]


def search(con, keyword, person=None, chat_id=None, since=None, until=None, limit=100, kinds=None):
    match_sql, match_params = _match_clause(keyword)
    where = [match_sql]
    params = list(match_params)
    if chat_id:
        where.append('chat_id = ?')
        params.append(chat_id)
    elif person:
        people, friend_ids = _chat_ids_for_people(con, person)
        ids = friend_ids or [p['username'] for p in people[:1]]
        if ids:
            where.append('chat_id IN (%s)' % ','.join('?' * len(ids)))
            params += ids
    if kinds:
        where.append('kind IN (%s)' % ','.join('?' * len(kinds)))
        params += kinds
    tc, tp = _time_clause(since, until)
    where += tc
    params += tp
    return _collect(con, ' AND '.join(where), params, limit, newest_first_output=True)


def inbox(con, since=None, until=None, limit=200, kinds=None):
    """Things you sent to yourself (filehelper + self-chat): links, notes, media."""
    self_id = con.execute("SELECT value FROM meta WHERE key='self_id'").fetchone()
    self_id = self_id[0] if self_id else None
    chats = ['filehelper']
    if self_id:
        chats.append(self_id)
    where = ['chat_id IN (%s)' % ','.join('?' * len(chats))]
    params = list(chats)
    if kinds:
        where.append('kind IN (%s)' % ','.join('?' * len(kinds)))
        params += kinds
    tc, tp = _time_clause(since, until)
    where += tc
    params += tp
    return _collect(con, ' AND '.join(where), params, limit, newest_first_output=True)


def list_chats(con, kind=None, query=None, limit=60):
    clause, params = ['msg_count > 0'], []
    sql = ("SELECT chats.chat_id, is_group, msg_count, first_ts, last_ts, unread, biz, "
           "COALESCE(NULLIF(c.remark,''), NULLIF(c.nick,''), chats.chat_id) AS name, c.kind AS ckind "
           "FROM chats LEFT JOIN contacts c ON c.username = chats.chat_id")
    if kind == 'group':
        clause.append('is_group = 1')
    elif kind == 'friend':
        clause.append("is_group = 0 AND c.kind = 'friend'")
    if query:
        clause.append("(lower(c.remark) LIKE ? OR lower(c.nick) LIKE ?)")
        q = '%' + query.lower() + '%'
        params += [q, q]
    sql += ' WHERE ' + ' AND '.join(clause) + ' ORDER BY last_ts DESC LIMIT ?'
    params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [{'name': r['name'], 'chat_id': r['chat_id'], 'is_group': bool(r['is_group']),
             'kind': r['ckind'], 'messages': r['msg_count'], 'unread': r['unread'],
             'first': fmt_time(r['first_ts']), 'last': fmt_time(r['last_ts'])} for r in rows]


def stats(con):
    row = con.execute('SELECT count(*) n, min(ts) a, max(ts) b FROM messages').fetchone()
    chats = con.execute('SELECT count(*) FROM chats WHERE msg_count > 0').fetchone()[0]
    contacts = con.execute('SELECT count(*) FROM contacts').fetchone()[0]
    last_sync = con.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
    return {'messages': row['n'], 'chats': chats, 'contacts': contacts,
            'first': fmt_time(row['a']), 'last': fmt_time(row['b']),
            'last_sync': fmt_time(int(last_sync[0])) if last_sync else None}
