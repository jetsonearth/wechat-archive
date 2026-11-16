"""`wx setup` — one-time account + key configuration.

Keys can arrive two ways:
  --import FILE   adopt keys you already have (any JSON layout), matched to
                  databases by salt. Nothing touches WeChat.
  --capture       run the bundled bootstrap: copy WeChat to a temp bundle,
                  ad-hoc sign the copy, read the per-database keys from that
                  copy under LLDB, restore the original. Interrupts WeChat once.

Either way the result is a salt-indexed keystore at ~/.wechat-archive/keys.json,
which then opens the live databases and any future copy of them.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import paths
from .keystore import KeyStore, candidates_from_files



def pick_account(folder=None):
    accts = paths.accounts()
    if not accts:
        raise RuntimeError('No WeChat 4.x data on this Mac. Log in to WeChat for Mac once, then rerun.')
    if folder:
        for a in accts:
            if a['dir'].name == folder or str(a['dir']) == folder:
                return a
        raise RuntimeError('No account folder named %s' % folder)
    if len(accts) == 1:
        return accts[0]
    names = '\n'.join('  %s  (wxid %s)' % (a['dir'].name, a['wxid']) for a in accts)
    raise RuntimeError('Several accounts found; choose one with --account <folder>:\n' + names)


def save_account(acct):
    cfg = paths.load_config()
    cfg['account_dir'] = str(acct['dir'])
    cfg['self_id'] = acct['wxid']
    paths.save_config(cfg)


def do_import(ks, db_root, files):
    found = candidates_from_files(files)
    if not found:
        raise RuntimeError('No 64-hex keys found in: %s' % ', '.join(files))
    added = ks.import_candidates(db_root, found)
    ks.save()
    return added


def legacy_key_files():
    """Key files a previous setup may have left on this Mac."""
    spots = [Path.home() / '.wechat-local/keys.json']
    out = []
    for s in spots:
        if s.is_file():
            out.append(str(s))
        elif s.is_dir():
            out += [p for p in glob.glob(str(s / '**/*.json'), recursive=True)
                    if 'key' in os.path.basename(p).lower()]
    return out


def do_capture(acct, timeout):
    """Run the bundled bootstrap, then import whatever it captured by salt."""
    if subprocess.run(['pgrep', '-x', 'WeChat'], capture_output=True).returncode == 0:
        raise RuntimeError('Quit WeChat for Mac normally first (menu → Quit), then rerun. Nothing was changed.')
    vendor = Path(__file__).with_name('vendor')
    with tempfile.TemporaryDirectory(prefix='wx-capture-') as tmp:
        state = Path(tmp) / 'state'
        cmd = [sys.executable, str(vendor / 'bootstrap_keys.py'),
               '--db-root', str(acct['db_root']), '--state-dir', str(state),
               '--timeout', str(timeout), '--acknowledge-debug-copy']
        print('Starting key capture. A temporary WeChat window will open — sign in there if asked.',
              file=sys.stderr)
        rc = subprocess.run(cmd).returncode
        produced = state / 'keys.json'
        if not produced.exists():
            raise RuntimeError('Key capture did not produce keys (exit %d). See the messages above.' % rc)
        return str(produced)


def run(argv=None):
    ap = argparse.ArgumentParser(prog='wx setup', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--account', help='WeChat account folder name (when more than one is logged in)')
    ap.add_argument('--import', dest='imports', nargs='+', metavar='FILE', help='adopt existing key file(s)')
    ap.add_argument('--capture', action='store_true', help='capture keys from WeChat (interrupts it once)')
    ap.add_argument('--timeout', type=int, default=180, help='capture timeout seconds')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    acct = pick_account(args.account)
    save_account(acct)
    ks = KeyStore(paths.home() / 'keys.json')

    added = 0
    if args.imports:
        added = do_import(ks, acct['db_root'], args.imports)
    elif args.capture:
        produced = do_capture(acct, args.timeout)
        added = do_import(ks, acct['db_root'], [produced])
    else:
        # No explicit source: try keys we may already have lying around.
        existing = legacy_key_files()
        if existing:
            added = do_import(ks, acct['db_root'], existing)

    covered, missing = ks.coverage(acct['db_root'])
    msg_missing = [m for m in missing if m.startswith('message/') and not m.endswith('unspportmsg.db')]
    result = {'account': acct['wxid'], 'account_dir': acct['dir'].name,
              'keys_added': added, 'databases_covered': len(covered),
              'message_dbs_missing_key': msg_missing}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('Account: %s' % acct['wxid'])
        print('Keys cover %d databases.' % len(covered))
        if not covered:
            print('\nNo keys yet. Get them one of two ways:')
            print('  wx setup --import /path/to/keys.json     # if you already have keys')
            print('  wx setup --capture                       # capture from WeChat (quit WeChat first)')
        elif msg_missing:
            print('Missing a key for: %s' % ', '.join(msg_missing))
            print('Run: wx setup --capture')
        else:
            print('Ready. Try:  wx status')
    return 0 if (covered or not (args.capture or args.imports)) else 2
