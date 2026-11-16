# AGENTS.md — wechat-archive

A local, read-only WeChat reader. `wx` keeps a SQLite index (`~/.wechat-archive/index.db`) in step with WeChat for Mac's live SQLCipher databases and answers queries over it. This file is for working **on the code**; to **use** the tool, read `wxa/SKILL.md` (also at `skill/SKILL.md`).

## Layout

```
wxa/
  sqlcipher.py   read-only SQLCipher 4 binding (ctypes)
  zstd.py        libzstd frame decompression
  keystore.py    per-database keys indexed by salt; verify by page HMAC
  proto.py       schema-less protobuf reader (ext_buffer / packed_info)
  source.py      one account's live databases, read-only
  decode.py      one message row -> readable, searchable record  ← core logic
  index.py       SQLite schema + incremental Syncer (fast path: size/mtime, then new rows)
  query.py       read-side queries (with / said / search / inbox / read / chats)
  names.py       resolve a person query -> contacts; display names back
  media.py       locate an image/video/file on disk
  paths.py       accounts, config, ~/.wechat-archive
  setup.py       `wx setup`: import or capture keys
  doctor.py      `wx doctor`: readiness checks + history coverage, for an agent driving setup
  skill.py       `wx install-skill`: copy SKILL.md into agent skill folders
  SKILL.md       the agent-facing skill (shipped in the package)
  cli.py         the `wx` command
  vendor/        bundled key-capture (bootstrap_keys.py, capture_keys.py)
tests/           synthetic only; no real data. Run: bash tests/run.sh
```

## Principles

- **Read-only, local, no sending.** Opens are `SQLITE_OPEN_READONLY` + `query_only`. The tool cannot send messages or change WeChat; don't add that. Keys and index are chmod 600 and never leave the machine; never print a key or raw chat content in logs.
- **No copying.** The point of the rewrite is that queries read the live databases through the index, not a pile of snapshot/export directories. Keep it that way: sync is incremental, keyed by each DB's size+mtime then new `local_id`s, plus a short recent-window re-read for revokes/edits.
- **Keys by salt.** A database's 16-byte salt is stable for its life, so keys are stored per salt and work for the live file and any copy. Never reintroduce absolute-path-keyed key files.
- **Output is for agents.** `--json` is lean by default (no internal ids, empty fields dropped, long text clipped, 50 rows); `--full` restores everything. Watch token cost when adding fields.
- **Decode is the hard part.** `decode.py` turns WeChat's type codes and XML into one readable line plus structured fields. When adding a message type, add a case there and a test in `tests/test_decode.py` with a hand-written payload (never real data).

## Tests

`bash tests/run.sh` — decode cases on synthetic payloads, and an end-to-end index/query run against a fake account. Keep them green and data-free so anyone can run them.
