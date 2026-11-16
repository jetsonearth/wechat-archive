"""Minimal read-only SQLCipher 4 binding over ctypes.

Opens WeChat's encrypted databases with SQLITE_OPEN_READONLY and
PRAGMA query_only, so the live database and its WAL are never written or
checkpointed. Values come back as Python ints, floats, str or bytes.
"""
import ctypes as C
import ctypes.util
import os
from pathlib import Path

SQLITE_OK, SQLITE_ROW, SQLITE_DONE = 0, 100, 101
SQLITE_OPEN_READONLY = 0x1
SQLITE_INTEGER, SQLITE_FLOAT, SQLITE_TEXT, SQLITE_BLOB, SQLITE_NULL = 1, 2, 3, 4, 5

_lib = None


def library_path():
    for candidate in (os.environ.get('WX_SQLCIPHER'),
                      '/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib',
                      '/usr/local/opt/sqlcipher/lib/libsqlcipher.dylib',
                      ctypes.util.find_library('sqlcipher')):
        if candidate and (not candidate.startswith('/') or Path(candidate).exists()):
            return candidate
    return None


def lib():
    global _lib
    if _lib is not None:
        return _lib
    name = library_path()
    if not name:
        raise RuntimeError('SQLCipher not found. Install it with: brew install sqlcipher')
    L = C.CDLL(name)
    vp, cp, i, i64 = C.c_void_p, C.c_char_p, C.c_int, C.c_int64
    sigs = {
        'sqlite3_open_v2': ([cp, C.POINTER(vp), i, cp], i),
        'sqlite3_close_v2': ([vp], i),
        'sqlite3_key': ([vp, vp, i], i),
        'sqlite3_busy_timeout': ([vp, i], i),
        'sqlite3_prepare_v2': ([vp, cp, i, C.POINTER(vp), C.POINTER(cp)], i),
        'sqlite3_step': ([vp], i),
        'sqlite3_finalize': ([vp], i),
        'sqlite3_column_count': ([vp], i),
        'sqlite3_column_name': ([vp, i], cp),
        'sqlite3_column_type': ([vp, i], i),
        'sqlite3_column_int64': ([vp, i], i64),
        'sqlite3_column_double': ([vp, i], C.c_double),
        'sqlite3_column_blob': ([vp, i], vp),
        'sqlite3_column_bytes': ([vp, i], i),
        'sqlite3_bind_int64': ([vp, i, i64], i),
        'sqlite3_bind_text': ([vp, i, cp, i, vp], i),
        'sqlite3_bind_null': ([vp, i], i),
        'sqlite3_errmsg': ([vp], cp),
    }
    for fn, (args, res) in sigs.items():
        f = getattr(L, fn)
        f.argtypes, f.restype = args, res
    _lib = L
    return L


class Error(RuntimeError):
    pass


class Connection:
    """Read-only connection. Never logs SQL text or values (they may hold content)."""

    def __init__(self, path, key_hex):
        if len(key_hex) != 64:
            raise ValueError('raw SQLCipher key must be 64 hex characters')
        self.path = str(path)
        self._db = C.c_void_p()
        L = lib()
        rc = L.sqlite3_open_v2(os.fsencode(self.path), C.byref(self._db), SQLITE_OPEN_READONLY, None)
        if rc != SQLITE_OK:
            self.close()
            raise Error('open failed (%d): %s' % (rc, Path(self.path).name))
        try:
            k = ("x'%s'" % key_hex).encode('ascii')
            if L.sqlite3_key(self._db, k, len(k)) != SQLITE_OK:
                raise Error('key setup failed')
            L.sqlite3_busy_timeout(self._db, 3000)
            self.execute('PRAGMA cipher_compatibility = 4')
            self.execute('PRAGMA query_only = ON')
            self.execute('SELECT count(*) FROM sqlite_master')
        except BaseException:
            self.close()
            raise

    def execute(self, sql, params=()):
        return list(self.iterate(sql, params))

    def iterate(self, sql, params=()):
        L = lib()
        stmt = C.c_void_p()
        rc = L.sqlite3_prepare_v2(self._db, sql.encode('utf-8'), -1, C.byref(stmt), None)
        if rc != SQLITE_OK:
            raise Error('prepare failed (%d) in %s' % (rc, Path(self.path).name))
        try:
            for n, value in enumerate(params, 1):
                if value is None:
                    L.sqlite3_bind_null(stmt, n)
                elif isinstance(value, int):
                    L.sqlite3_bind_int64(stmt, n, value)
                else:
                    b = str(value).encode('utf-8')
                    L.sqlite3_bind_text(stmt, n, b, len(b), C.c_void_p(-1))  # SQLITE_TRANSIENT
            cols = L.sqlite3_column_count(stmt)
            names = [L.sqlite3_column_name(stmt, c).decode('utf-8') for c in range(cols)]
            while True:
                rc = L.sqlite3_step(stmt)
                if rc == SQLITE_DONE:
                    return
                if rc != SQLITE_ROW:
                    raise Error('step failed (%d) in %s' % (rc, Path(self.path).name))
                row = {}
                for c in range(cols):
                    t = L.sqlite3_column_type(stmt, c)
                    if t == SQLITE_INTEGER:
                        v = L.sqlite3_column_int64(stmt, c)
                    elif t == SQLITE_FLOAT:
                        v = L.sqlite3_column_double(stmt, c)
                    elif t == SQLITE_NULL:
                        v = None
                    else:
                        size = L.sqlite3_column_bytes(stmt, c)
                        ptr = L.sqlite3_column_blob(stmt, c)
                        raw = C.string_at(ptr, size) if ptr and size else b''
                        v = raw.decode('utf-8', 'replace') if t == SQLITE_TEXT else raw
                    row[names[c]] = v
                yield row
        finally:
            L.sqlite3_finalize(stmt)

    def tables(self):
        return [r['name'] for r in self.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def close(self):
        if self._db:
            lib().sqlite3_close_v2(self._db)
            self._db = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
