"""Resolve people the way a person refers to them, and display names back.

A query like "alex" or "张伟" is matched against remark (the name you
gave them), nickname, alias, pinyin and initials, across private chats and
group members. Ambiguity is returned, not guessed.
"""

DISPLAY = ('SELECT username, kind, '
           "COALESCE(NULLIF(remark,''), NULLIF(nick,''), NULLIF(alias,''), username) AS name, "
           'remark, nick, alias FROM contacts')


def display_map(con):
    return {r['username']: r for r in con.execute(DISPLAY)}


def name_of(con, username, cache=None):
    if username is None:
        return None
    if cache is not None and username in cache:
        return cache[username]['name']
    row = con.execute(DISPLAY + ' WHERE username=?', (username,)).fetchone()
    return row['name'] if row else username


def resolve_people(con, query):
    """Return list of {username, name, kind, why} matching a person query."""
    q = query.strip()
    like = '%' + q.lower() + '%'
    rows = con.execute(
        DISPLAY + ' WHERE lower(remark) LIKE ? OR lower(nick) LIKE ? OR lower(alias) LIKE ? '
        'OR lower(remark_pinyin) LIKE ? OR lower(pinyin) LIKE ? '
        'OR lower(remark_initials) LIKE ? OR lower(initials) LIKE ? OR username=?',
        (like, like, like, like, like, like, like, q)).fetchall()
    out = []
    for r in rows:
        why = []
        ql = q.lower()
        if r['remark'] and ql in r['remark'].lower():
            why.append('remark')
        if r['nick'] and ql in r['nick'].lower():
            why.append('nickname')
        if r['alias'] and ql in r['alias'].lower():
            why.append('alias')
        out.append({'username': r['username'], 'name': r['name'], 'kind': r['kind'],
                    'why': why or ['pinyin']})
    # exact remark/nick first, then friends before strangers/groups
    order = {'friend': 0, 'wecom': 1, 'group': 2, 'official': 3, 'stranger': 4, 'system': 5}
    out.sort(key=lambda m: (0 if q.lower() in (m['name'] or '').lower() else 1, order.get(m['kind'], 9)))
    return out


def resolve_groups(con, query):
    q = '%' + query.strip().lower() + '%'
    rows = con.execute(
        "SELECT chat_id, COALESCE(NULLIF(c.remark,''), NULLIF(c.nick,'')) AS name FROM chats "
        "LEFT JOIN contacts c ON c.username = chats.chat_id "
        "WHERE is_group=1 AND (lower(c.nick) LIKE ? OR lower(c.remark) LIKE ?)", (q, q)).fetchall()
    return [{'chat_id': r['chat_id'], 'name': r['name'] or r['chat_id']} for r in rows]
