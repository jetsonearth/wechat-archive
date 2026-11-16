"""zstd frame decompression via the system libzstd (brew install zstd)."""
import ctypes as C
import ctypes.util
import os
from pathlib import Path

MAGIC = b'\x28\xb5\x2f\xfd'
LIMIT = 64 * 1024 * 1024
_lib = None


def library_path():
    for candidate in (os.environ.get('WX_ZSTD'),
                      '/opt/homebrew/opt/zstd/lib/libzstd.1.dylib',
                      '/usr/local/opt/zstd/lib/libzstd.1.dylib',
                      ctypes.util.find_library('zstd')):
        if candidate and (not candidate.startswith('/') or Path(candidate).exists()):
            return candidate
    return None


def _load():
    global _lib
    if _lib is None:
        name = library_path()
        if not name:
            raise RuntimeError('zstd not found. Install it with: brew install zstd')
        L = C.CDLL(name)
        L.ZSTD_getFrameContentSize.argtypes = [C.c_void_p, C.c_size_t]
        L.ZSTD_getFrameContentSize.restype = C.c_ulonglong
        L.ZSTD_decompress.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t]
        L.ZSTD_decompress.restype = C.c_size_t
        L.ZSTD_isError.argtypes = [C.c_size_t]
        L.ZSTD_isError.restype = C.c_uint
        _lib = L
    return _lib


def maybe_decompress(raw):
    if not raw or not raw.startswith(MAGIC):
        return raw
    L = _load()
    size = L.ZSTD_getFrameContentSize(raw, len(raw))
    if size == 2 ** 64 - 2:
        raise ValueError('invalid zstd frame')
    if size == 2 ** 64 - 1:  # unknown size: grow until it fits
        size = max(len(raw) * 8, 1 << 16)
    while True:
        if size > LIMIT:
            raise ValueError('zstd frame too large')
        buf = C.create_string_buffer(max(size, 1))
        n = L.ZSTD_decompress(buf, size, raw, len(raw))
        if not L.ZSTD_isError(n):
            return buf.raw[:n]
        size *= 4
