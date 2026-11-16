"""wx — command line over your local WeChat index.

Design: every query first does a fast incremental sync (unless --no-sync), so
you always read current data without copying databases. Output is human text by
default and JSON with --json, for agents.
"""
import argparse
import json
import sys
import time

from . import index, names, paths, query
from .source import Account


def eprint(*a):
    print(*a, file=sys.stderr)


def get_account():
    a = paths.current_account()
    ks = paths.home() / 'keys.json'
    return Account(a['db_root'], ks, a['wxid'])


def ensure_synced(con, force=False, quiet=False):
    try:
        account = get_account()
    except RuntimeError as e:
        eprint(str(e))
        return None
    ks = paths.home() / 'keys.json'
    covered, missing = account.keys.coverage(account.root)
    need = [m for m in missing if m.startswith('message/')]
    if not covered:
        eprint('No database keys yet. Run: wx doctor')
        return account
    t0 = time.time()
    report = index.sync(con, account, force=force,
                        progress=(None if quiet else _progress()))
    if not quiet and report['changed']:
        eprint('  synced %d messages in %.1fs' % (report['changed'], time.time() - t0))
    if report['missing_keys']:
        eprint('  note: no key for %s (run: wx setup --capture)' % ', '.join(report['missing_keys']))
    return account


def _progress():
    last = [0.0]
    def cb(stage, n=0):
        now = time.time()
        if stage == 'messages' and now - last[0] > 0.5:
            eprint('  indexing… %d' % n)
            last[0] = now
    return cb


LEAN_OPTIONAL = ('quote_sender', 'quote_text', 'url', 'file_name')


def media_path(m, account):
    if not (m.get('media') or m.get('file_name')) or account is None or not m.get('chat_id'):
        return None
    from . import media as medialib
    p = medialib.locate(account.base, m['chat_id'], m['media'], m['ts'], m['kind'], m.get('file_name'))
    return str(p) if p else None


CLIP = 400


def clip(s):
    return s if len(s) <= CLIP else s[:CLIP] + '…[+%d chars, --full for all]' % (len(s) - CLIP)


def lean(m, show_media, account):
    """The fields an agent actually reads; empty ones and internal ids dropped."""
    out = {'time': m['time'], 'chat': m['chat']}
    if m['sender'] and m['sender'] != m['chat']:
        out['sender'] = m['sender']
    if m['kind'] != 'text':
        out['kind'] = m['kind']
    if m['text']:
        out['text'] = clip(m['text'])
    for k in LEAN_OPTIONAL:
        if m.get(k):
            out[k] = clip(m[k]) if k == 'quote_text' else m[k]
    if show_media:
        p = media_path(m, account)
        if p:
            out['path'] = p
    return out


def paging_hint(result):
    msgs = result.get('messages') or []
    if not msgs or result.get('matched', 0) <= result.get('returned', 0):
        return None
    oldest = min(m['time'] for m in msgs)
    return ('showing %d of %d; older: add --until "%s", or narrow with --since/--person/--kind'
            % (result['returned'], result['matched'], oldest))


def dump_json(result, full=False, show_media=False, account=None):
    """Compact JSON: header fields on one line, then one message per line."""
    msgs = result.get('messages')
    if full or msgs is None:
        print(json.dumps(result, ensure_ascii=False, indent=None if not full else 1))
        return
    head = {k: v for k, v in result.items() if k != 'messages'}
    ids = {}
    for m in msgs:
        if m.get('chat') and m.get('chat_id'):
            ids.setdefault(m['chat'], m['chat_id'])
    if ids:
        head['chat_ids'] = ids
    hint = paging_hint(result)
    if hint:
        head['more'] = hint
    body = ',\n'.join(json.dumps(lean(m, show_media, account), ensure_ascii=False, separators=(',', ':'))
                      for m in msgs)
    text = json.dumps(head, ensure_ascii=False, separators=(',', ':'))
    print(text[:-1] + (',' if head else '') + '"messages":[\n' + body + '\n]}')


def dump_rows(rows):
    print('[\n' + ',\n'.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) for r in rows) + '\n]')


def render(result, as_json, show_media=False, account=None, full=False):
    if as_json:
        dump_json(result, full, show_media, account)
        return
    if 'error' in result:
        eprint('No match for "%s".' % result.get('query', ''))
        return
    if 'ambiguous' in result:
        eprint('Several people match "%s" — rerun with a more specific name or --chat <id>:' % result['query'])
        for p in result['ambiguous']:
            eprint('  %-20s %s (%s)' % (p['name'], p['username'], p['kind']))
        return
    msgs = result.get('messages', [])
    if 'resolved' in result:
        who = ', '.join('%s' % r['name'] for r in result['resolved'])
        eprint('— %s —' % who)
    for m in msgs:
        line = '[%s] %s' % (m['time'], m['sender'] or m['chat'])
        if m.get('chat') and m['sender'] and m['chat'] != m['sender'] and not m['from_self']:
            line += ' @%s' % m['chat']
        print(line)
        body = m['text'] or ''
        if m.get('quote_text'):
            body = '⤷ 引用%s: %s\n%s' % (' ' + m['quote_sender'] if m.get('quote_sender') else '',
                                       m['quote_text'], body)
        print('  ' + body.replace('\n', '\n  '))
        if show_media:
            p = media_path(m, account)
            if p:
                print('  📎 %s' % p)
        print()
    if 'matched' in result:
        eprint(paging_hint(result) or '%d matched.' % result['matched'])


def cmd_search(con, args, account):
    kinds = args.kind.split(',') if args.kind else None
    res = query.search(con, args.keyword, person=args.person, chat_id=args.chat,
                       since=args.since, until=args.until, limit=args.limit, kinds=kinds)
    render(res, args.json, args.media, account, args.full)


def cmd_with(con, args, account):
    res = query.with_person(con, args.person, since=args.since, until=args.until,
                            limit=args.limit, include_groups=args.groups)
    render(res, args.json, args.media, account, args.full)


def cmd_said(con, args, account):
    res = query.said_by(con, args.person, since=args.since, until=args.until, limit=args.limit)
    render(res, args.json, args.media, account, args.full)


def cmd_inbox(con, args, account):
    kinds = args.kind.split(',') if args.kind else None
    res = query.inbox(con, since=args.since, until=args.until, limit=args.limit, kinds=kinds)
    render(res, args.json, args.media, account, args.full)


def cmd_read(con, args, account):
    res = query.read_chat(con, args.chat, since=args.since, until=args.until, limit=args.limit)
    render(res, args.json, args.media, account, args.full)


def cmd_who(con, args, account):
    people = names.resolve_people(con, args.person)
    if args.json:
        dump_rows(people[:args.limit])
        return
    if not people:
        eprint('No match for "%s".' % args.person)
        return
    for p in people[:args.limit]:
        print('%-22s %-10s %s' % (p['name'], p['kind'], p['username']))


def cmd_chats(con, args, account):
    rows = query.list_chats(con, kind=args.kind, query=args.query, limit=args.limit)
    if args.json:
        dump_rows(rows)
        return
    for r in rows:
        tag = 'group' if r['is_group'] else (r['kind'] or '')
        unread = ('  (%d unread)' % r['unread']) if r['unread'] else ''
        print('%-24s %-7s %6d msgs  last %s%s' % (r['name'][:24], tag, r['messages'], r['last'], unread))
        if args.json is False and args.show_id:
            print('    %s' % r['chat_id'])


def cmd_status(con, args, account):
    st = query.stats(con)
    if args.json:
        st['account'] = account.self_id if account else None
        print(json.dumps(st, ensure_ascii=False))
        return
    eprint('Account: %s' % (account.self_id if account else '—'))
    print('%d messages across %d chats, %d contacts' % (st['messages'], st['chats'], st['contacts']))
    print('range %s → %s' % (st['first'], st['last']))
    print('last sync %s' % st['last_sync'])


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', help='machine-readable output')
    common.add_argument('--no-sync', action='store_true', help='skip the pre-query refresh')
    common.add_argument('--quiet', action='store_true', help='no progress on stderr')
    p = argparse.ArgumentParser(prog='wx', parents=[common],
                                description='Read your own WeChat history locally.',
                                epilog='Also: wx doctor (check setup), wx setup (keys), wx install-skill (agent skill).')
    sub = p.add_subparsers(dest='command', required=True, parser_class=lambda **k: argparse.ArgumentParser(parents=[common], **k))

    def add_time(sp):
        sp.add_argument('--since', help='start (YYYY-MM-DD, or 7d / 2w / 1m)')
        sp.add_argument('--until', help='end (exclusive)')
        sp.add_argument("--limit", type=int, default=50, help="max messages (default 50)")
        sp.add_argument('--media', action='store_true', help='print local media paths')
        sp.add_argument('--kind', help='filter kinds, comma separated (text,image,link,file,...)')
        sp.add_argument('--full', action='store_true', help='with --json: every field, including internal ids')

    s = sub.add_parser('search', help='full-text search across all chats')
    s.add_argument('keyword')
    s.add_argument('--person', help='limit to a person')
    s.add_argument('--chat', help='limit to an exact chat_id')
    add_time(s)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser('with', help='conversation with a person')
    s.add_argument('person')
    s.add_argument('--groups', action='store_true', help='also include group chats they are in')
    add_time(s)
    s.set_defaults(func=cmd_with)

    s = sub.add_parser('said', help='what a person said across every chat')
    s.add_argument('person')
    add_time(s)
    s.set_defaults(func=cmd_said)

    s = sub.add_parser('inbox', help='what you sent to yourself (links, notes, media)')
    add_time(s)
    s.set_defaults(func=cmd_inbox)

    s = sub.add_parser('read', help='read one chat by exact chat_id')
    s.add_argument('chat')
    add_time(s)
    s.set_defaults(func=cmd_read)

    s = sub.add_parser('who', help='find a contact')
    s.add_argument('person')
    s.add_argument('--limit', type=int, default=20)
    s.set_defaults(func=cmd_who)

    s = sub.add_parser('chats', help='list conversations')
    s.add_argument('--kind', choices=['group', 'friend'])
    s.add_argument('--query')
    s.add_argument('--limit', type=int, default=60)
    s.add_argument('--show-id', action='store_true')
    s.set_defaults(func=cmd_chats)

    s = sub.add_parser('status', help='index summary')
    s.set_defaults(func=cmd_status)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'setup':
        from . import setup as setup_mod
        return setup_mod.run(argv[1:])
    if argv and argv[0] == 'doctor':
        from . import doctor
        return doctor.run(argv[1:])
    if argv and argv[0] == 'install-skill':
        from . import skill
        return skill.run(argv[1:])
    args = build_parser().parse_args(argv)
    con = index.connect()
    account = None
    if not args.no_sync:
        account = ensure_synced(con, quiet=args.quiet)
    else:
        try:
            account = get_account()
        except RuntimeError:
            account = None
    try:
        args.func(con, args, account)
    except BrokenPipeError:
        pass
    finally:
        con.close()


if __name__ == '__main__':
    main()
