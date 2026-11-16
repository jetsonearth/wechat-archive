"""Where WeChat keeps its data, and where wx keeps its own."""
import json
import os
import re
from pathlib import Path

WECHAT_APP = Path('/Applications/WeChat.app')
XWECHAT = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'


def home():
    d = Path(os.environ.get('WX_HOME', Path.home() / '.wechat-archive')).expanduser()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    if d.stat().st_mode & 0o077:
        os.chmod(str(d), 0o700)
    return d


def config_path():
    return home() / 'config.json'


def load_config():
    p = config_path()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_config(cfg):
    from .keystore import write_private_json
    write_private_json(config_path(), cfg)


def accounts():
    """Logged-in WeChat 4.x accounts on this Mac, newest activity first."""
    if not XWECHAT.is_dir():
        return []
    found = []
    for d in XWECHAT.iterdir():
        db = d / 'db_storage'
        if (db / 'message').is_dir():
            wal = list((db / 'message').glob('message_*.db-wal')) + list((db / 'message').glob('message_*.db'))
            mtime = max((p.stat().st_mtime for p in wal), default=0)
            found.append({'dir': d, 'db_root': db, 'wxid': self_id_from_dir(d.name), 'last_active': mtime})
    return sorted(found, key=lambda a: -a['last_active'])


def self_id_from_dir(name):
    """xwechat_files/<wxid>_<4 hex> → <wxid>."""
    m = re.match(r'^(.+)_[0-9a-f]{4}$', name)
    return m.group(1) if m else name


def current_account():
    cfg = load_config()
    accts = accounts()
    if cfg.get('account_dir'):
        for a in accts:
            if str(a['dir']) == cfg['account_dir']:
                return a
        raise RuntimeError('Configured WeChat account folder no longer exists: %s. Run: wx setup' % cfg['account_dir'])
    if not accts:
        raise RuntimeError('No WeChat 4.x data found on this Mac. Log in to WeChat for Mac once, then run: wx setup')
    if len(accts) > 1:
        raise RuntimeError('Several WeChat accounts found; pick one with: wx setup --account <folder>')
    return accts[0]
