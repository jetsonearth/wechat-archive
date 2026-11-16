#!/bin/bash
# Install wx (wechat-archive). macOS on Apple Silicon, WeChat for Mac 4.x.
# Safe to rerun. Installs Homebrew packages and the wx command; never touches WeChat.
set -euo pipefail

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"

[ "$(uname)" = "Darwin" ] || die "wx supports macOS only."
[ "$(uname -m)" = "arm64" ] || die "wx needs Apple Silicon (M1 or later)."
command -v brew >/dev/null 2>&1 || die "Homebrew is required. Install it from https://brew.sh, then rerun."

say "1/4 Libraries (sqlcipher, zstd)"
for pkg in sqlcipher zstd; do
  brew list --versions "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done

say "2/4 The wx command"
command -v uv >/dev/null 2>&1 || brew install uv
uv tool install --force --quiet "$here"
bin="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
case ":$PATH:" in
  *":$bin:"*) ;;
  *) uv tool update-shell >/dev/null 2>&1 || true
     export PATH="$bin:$PATH"
     echo "  added $bin to your shell PATH (open a new terminal for it to stick)" ;;
esac

say "3/4 Agent skill"
wx install-skill || true

say "4/4 Developer tools (needed once, to read the database keys)"
if xcode-select -p >/dev/null 2>&1 && /usr/bin/lldb -P >/dev/null 2>&1; then
  echo "  present"
else
  echo "  missing — opening Apple's installer; finish it, then continue with wx setup --capture"
  xcode-select --install >/dev/null 2>&1 || true
fi

echo
wx doctor || true
