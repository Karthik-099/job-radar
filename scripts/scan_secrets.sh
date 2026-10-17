#!/usr/bin/env bash
# Fail if any tracked file (or the given directory) contains a key-shaped string or a secret file.
set -euo pipefail
pattern='gsk_[A-Za-z0-9]{20,}|sk-or-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{30,}|AQ\.[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|1//[A-Za-z0-9_-]{30,}'
if [ $# -gt 0 ]; then
  # Skip the ciphertext: it is random base64 (structure is verified by assert_publishable instead).
  files=$(find "$1" -type f ! -name '*.enc.json')
else
  files=$(git ls-files)
  if echo "$files" | grep -Eq '(^|/)\.env$|(^|/)\.env\.[^e]|client_secret.*\.json|credentials.*\.json'; then
    echo "secret file is tracked by git"; exit 1
  fi
fi
hits=$(echo "$files" | grep -v '^tests/' | xargs -r grep -lE "$pattern" 2>/dev/null || true)
if [ -n "$hits" ]; then
  echo "key-shaped strings found in:"; echo "$hits"; exit 1
fi
echo "secret scan: clean"
