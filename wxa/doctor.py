"""`wx doctor` — is this Mac ready, and how much of the user's history can wx see?

Each check reports ok / warn / fail with a one-line fix. `--json` is for an agent
driving setup: it follows `next` in order and relays the human-only steps.
Read-only: it never starts capture, never touches WeChat, never syncs.
"""
import argparse
import json
import platform
import plistlib
import shutil
import subprocess
import time
from datetime import datetime

from . import paths, sqlcipher, zstd
from .keystore import KeyStore

MIGRATE = ('On the phone: Me → Settings → General → Manage Chat History (聊天记录迁移与备份) → '
           'Migrate → Migrate to computer, choose all chats. Keep WeChat for Mac logged in and both '
           'devices on the same Wi-Fi until it finishes, then run `wx status`.')


def check(name, status, detail, fix=None):
    return {'check': name, 'status': status, 'detail': detail, **({'fix': fix} if fix else {})}


def wechat_version():
    try:
        with open(paths.WECHAT_APP / 'Contents/Info.plist', 'rb') as f:
            return plistlib.load(f).get('CFBundleShortVersionString')
    except OSError:
        return None


def clt_ok():
    if not shutil.which('xcode-select'):
        return False
    r = subprocess.run(['xcode-select', '-p'], capture_output=True)
    if r.returncode:
        return False
    return subprocess.run(['/usr/bin/lldb', '-P'], capture_output=True).returncode == 0


def history(con, account_base):
    """Coverage of the index: span, per-year counts, share of media with originals on disk."""
    row = con.execute('SELECT count(*) n, min(ts) a, max(ts) b FROM messages').fetchone()
    if not row[0]:
        return None
    years = {y: n for y, n in con.execute(
        "SELECT strftime('%Y', ts, 'unixepoch', '+8 hours') y, count(*) FROM messages GROUP BY y")}
    sample = con.execute("SELECT chat_id, media, ts, kind, file_name FROM messages "
                         "WHERE kind IN ('image','video') AND media IS NOT NULL AND chat_id IS NOT NULL "
                         "ORDER BY random() LIMIT 200").fetchall()
    originals = None
    if sample and account_base is not None:
        from .media import locate
        full = 0
        for r in sample:
            p = locate(account_base, *r)
            if p and not (p.name.endswith('_t.dat') or '_thumb' in p.name):
                full += 1
        originals = round(full / len(sample), 2)
    first = datetime.fromtimestamp(row[1])
    return {'messages': row[0], 'first': first.strftime('%Y-%m-%d'),
            'last': datetime.fromtimestamp(row[2]).strftime('%Y-%m-%d %H:%M'),
            'days_covered': int((row[2] - row[1]) / 86400), 'per_year': years,
            'media_originals_share': originals}


def diagnose():
    out = []
    arm = platform.system() == 'Darwin' and platform.machine() == 'arm64'
    out.append(check('mac', 'ok' if arm else 'fail', '%s %s' % (platform.system(), platform.machine()),
                     None if arm else 'wx needs macOS on Apple Silicon.'))

    ver = wechat_version()
    out.append(check('wechat_app', 'ok' if ver and ver.startswith('4.') else ('warn' if ver else 'fail'),
                     'WeChat for Mac %s' % ver if ver else 'not installed',
                     None if ver and ver.startswith('4.') else 'Install WeChat for Mac 4.x from weixin.qq.com and log in.'))

    accts = paths.accounts()
    if not accts:
        out.append(check('account', 'fail', 'no WeChat 4.x data on this Mac',
                         'Open WeChat for Mac and log in once (scan the QR code with your phone).'))
    else:
        out.append(check('account', 'ok' if len(accts) == 1 else 'warn',
                         '%d account folder(s): %s' % (len(accts), ', '.join(a['dir'].name for a in accts)),
                         None if len(accts) == 1 else 'Pick one: wx setup --account <folder>'))

    libs_ok = bool(sqlcipher.library_path()) and bool(zstd.library_path())
    out.append(check('libraries', 'ok' if libs_ok else 'fail',
                     'sqlcipher %s, zstd %s' % ('found' if sqlcipher.library_path() else 'missing',
                                               'found' if zstd.library_path() else 'missing'),
                     None if libs_ok else 'brew install sqlcipher zstd'))

    try:
        acct = paths.current_account()
    except RuntimeError:
        acct = None
    covered, msg_missing = [], []
    if acct:
        ks = KeyStore(paths.home() / 'keys.json')
        covered, missing = ks.coverage(acct['db_root'])
        msg_missing = [m for m in missing if m.startswith('message/') and not m.endswith('unspportmsg.db')]
    if covered and not msg_missing:
        out.append(check('keys', 'ok', 'keys open %d databases' % len(covered)))
    else:
        clt = clt_ok()
        out.append(check('developer_tools', 'ok' if clt else 'fail',
                         'Xcode Command Line Tools %s' % ('present' if clt else 'missing (needed once, for key capture)'),
                         None if clt else 'xcode-select --install   (a system dialog opens; wait for it to finish)'))
        out.append(check('keys', 'fail' if not covered else 'warn',
                         'no keys yet' if not covered else 'no key for %s' % ', '.join(msg_missing),
                         'Quit WeChat (menu → Quit WeChat), then: wx setup --capture. '
                         'A temporary WeChat window opens: click Enter Weixin / confirm login on the phone.'))

    hist = None
    if covered:
        from . import index
        con = index.connect()
        try:
            base = acct['dir'] if acct else None
            hist = history(con, base)
        finally:
            con.close()
        if hist is None:
            out.append(check('index', 'warn', 'index is empty', 'wx status   (first sync takes about a minute)'))
        else:
            thin = hist['days_covered'] < 120
            out.append(check('history', 'warn' if thin else 'ok',
                             '%d messages, %s → %s' % (hist['messages'], hist['first'], hist['last']),
                             MIGRATE if thin else None))
    return out, hist


def next_steps(checks):
    return [c['fix'] for c in checks if c['status'] != 'ok' and c.get('fix')]


def run(argv=None):
    ap = argparse.ArgumentParser(prog='wx doctor', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    t0 = time.time()
    checks, hist = diagnose()
    steps = next_steps(checks)
    ready = all(c['status'] != 'fail' for c in checks) and any(c['check'] == 'history' for c in checks)
    if args.json:
        print(json.dumps({'ready': ready, 'checks': checks, 'history': hist, 'next': steps,
                          'migrate_from_phone': MIGRATE}, ensure_ascii=False))
        return 0 if ready else 1
    mark = {'ok': '✓', 'warn': '!', 'fail': '✗'}
    for c in checks:
        print('%s %-16s %s' % (mark[c['status']], c['check'], c['detail']))
    if hist:
        years = '  '.join('%s:%d' % kv for kv in sorted(hist['per_year'].items()))
        print('  per year         %s' % years)
        if hist['media_originals_share'] is not None:
            print('  media originals  %d%% of sampled images/videos are on this Mac '
                  '(the rest are thumbnails; the phone usually has more)'
                  % round(100 * hist['media_originals_share']))
    if steps:
        print('\nNext:')
        for i, s in enumerate(steps, 1):
            print('  %d. %s' % (i, s))
    else:
        print('\nReady. (%.1fs)' % (time.time() - t0))
    return 0 if ready else 1
