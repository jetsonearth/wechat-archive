#!/bin/bash
# Run all wx tests. No real WeChat data is touched.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
export WX_HOME="$(mktemp -d)"
fail=0
for t in "$here"/test_*.py; do
  /usr/bin/python3 -I "$t" || fail=1
done
rm -rf "$WX_HOME"
exit $fail
