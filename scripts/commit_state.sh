#!/usr/bin/env bash
# Commit state/ (and learned companies) back to the private repo. Safe to run when nothing changed.
set -euo pipefail
kind="${1:-scan}"
git config user.name "job-radar-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A state/ companies.yaml profile/resume.yaml profile/resume.md  # -A: also processed inbox deletions
if git diff --cached --quiet; then
  echo "no state changes"
  exit 0
fi
git commit -q -m "${kind}: $(date -u +%Y-%m-%dT%H:%MZ)"
for attempt in 1 2 3; do
  if git push -q origin HEAD:prod; then echo "state pushed"; exit 0; fi
  echo "push rejected (attempt $attempt), rebasing"
  git pull -q --rebase origin prod || { git rebase --abort || true; sleep 3; }
done
echo "could not push state after 3 attempts" >&2
exit 1
