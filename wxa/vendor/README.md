# Bundled key capture

`bootstrap_keys.py` and `capture_keys.py` read WeChat's per-database SQLCipher
keys from a temporary, ad-hoc-signed copy of WeChat under LLDB, then restore the
original. They never modify the real app, disable security settings, or use sudo.
Source and method: see SOURCE.json and KEY_GUIDE.md. Apple Silicon, WeChat 4.x.
