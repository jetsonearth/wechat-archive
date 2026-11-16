"""`wx install-skill` — put the wechat-archive skill where coding agents look for it.

Default: every agent home that exists on this Mac (~/.claude, ~/.codex, ~/.agents).
The skill is a copy of the SKILL.md shipped inside this package, so it always
matches the installed `wx`.
"""
import argparse
import json
from pathlib import Path

NAME = 'wechat-archive'
AGENTS = {'claude': '~/.claude/skills', 'codex': '~/.codex/skills', 'agents': '~/.agents/skills'}


def source():
    return Path(__file__).with_name('SKILL.md')


def targets(which, custom):
    if custom:
        return [Path(custom).expanduser()]
    chosen = AGENTS if which == 'all' else {which: AGENTS[which]}
    out = []
    for name, d in chosen.items():
        root = Path(d).expanduser()
        if which != 'all' or root.parent.exists():
            out.append(root / NAME)
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(prog='wx install-skill', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--agent', choices=['all', *AGENTS], default='all')
    ap.add_argument('--dir', help='install into this skills folder instead (…/wechat-archive)')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    text = source().read_text()
    done = []
    for t in targets(args.agent, args.dir):
        t.mkdir(parents=True, exist_ok=True)
        (t / 'SKILL.md').write_text(text)
        done.append(str(t / 'SKILL.md'))
    if args.json:
        print(json.dumps({'installed': done}))
    elif done:
        print('\n'.join('installed %s' % d for d in done))
    else:
        print('No agent folders found (~/.claude, ~/.codex, ~/.agents). Use --dir <skills folder>.')
    return 0 if done else 1
