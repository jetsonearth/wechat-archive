"""Per-database SQLCipher keys, indexed by each database's 16-byte salt.

WeChat 4.x encrypts every database with its own raw key. The salt is the
first 16 bytes of the file and stays the same for the life of a database, so
a salt-indexed store opens the live file, any copy of it, and survives moves.
Keys never leave this file and are never printed.
"""
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

PAGE = 4096
HEX64 = re.compile(r'[0-9a-fA-F]{64}')


def first_page(path):
    with open(path, 'rb') as f:
        page = f.read(PAGE)
    if len(page) < PAGE or page.startswith(b'SQLite format 3\x00'):
        return None
    return page


def key_matches(key, page):
    """SQLCipher 4 default page HMAC check (HMAC-SHA512, 2-round PBKDF2 mac key)."""
    if len(key) != 32 or not page or len(page) != PAGE:
        return False
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(b ^ 0x3a for b in page[:16]), 2, 32)
    digest = hmac.new(mac_key, page[16:PAGE - 64] + (1).to_bytes(4, 'little'), hashlib.sha512).digest()
    return hmac.compare_digest(digest, page[PAGE - 64:])


def write_private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name('.' + path.name + '.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


class KeyStore:
    def __init__(self, path):
        self.path = Path(path)
        self.keys = {}  # salt hex -> {'key': hex, 'db': relative path when learned}
        if self.path.exists():
            if self.path.stat().st_mode & 0o077:
                raise RuntimeError('%s must be private: chmod 600 %s' % (self.path.name, self.path))
            data = json.loads(self.path.read_text())
            self.keys = data.get('salts', {})

    def save(self):
        write_private_json(self.path, {'version': 1, 'salts': self.keys})

    def key_for(self, db_path):
        page = first_page(db_path)
        if page is None:
            return None
        entry = self.keys.get(page[:16].hex())
        return entry['key'] if entry else None

    def add(self, db_rel, page, key):
        """Store a key only after it verifies against the database's first page."""
        if not key_matches(key, page):
            return False
        self.keys[page[:16].hex()] = {'key': key.hex(), 'db': db_rel}
        return True

    def coverage(self, db_root):
        """Return (covered, missing) relative paths of encrypted databases under db_root."""
        covered, missing = [], []
        root = Path(db_root)
        for p in sorted(root.rglob('*.db')):
            page = first_page(p)
            if page is None:
                continue
            rel = str(p.relative_to(root))
            entry = self.keys.get(page[:16].hex())
            ok = bool(entry) and key_matches(bytes.fromhex(entry['key']), page)
            (covered if ok else missing).append(rel)
        return covered, missing

    def import_candidates(self, db_root, candidate_keys):
        """Match loose candidate keys (from any old key file) to databases under db_root."""
        root = Path(db_root)
        added = 0
        pages = [(str(p.relative_to(root)), first_page(p)) for p in sorted(root.rglob('*.db'))]
        pages = [(rel, page) for rel, page in pages if page is not None]
        for rel, page in pages:
            if page[:16].hex() in self.keys:
                continue
            for key in candidate_keys:
                if self.add(rel, page, key):
                    added += 1
                    break
        return added


def candidates_from_files(paths):
    """Collect raw keys from legacy key files of any layout (path- or salt-indexed)."""
    found = set()

    def walk(value):
        if isinstance(value, str) and HEX64.fullmatch(value):
            found.add(bytes.fromhex(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for p in paths:
        try:
            walk(json.loads(Path(p).read_text()))
        except (OSError, ValueError):
            continue
    return found
