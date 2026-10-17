#!/usr/bin/env bash
# Publish dist/ to the PUBLIC dashboard repo as a single fresh commit on `prod` (force push).
# History is never kept there, so old ciphertexts don't pile up and the repo stays tiny.
# Only the allowlisted static files plus the encrypted data file are ever pushed.
set -euo pipefail
if [ -z "${DASHBOARD_REPO_TOKEN:-}" ]; then echo "DASHBOARD_REPO_TOKEN not set: skipping publish"; exit 0; fi
if [ ! -f dist/data/jobs.enc.json ]; then echo "dist/ not built: skipping publish"; exit 0; fi
python -c "from src.export import assert_publishable; assert_publishable()"
bash scripts/scan_secrets.sh dist

site="$(mktemp -d)"
cp -r dist/. "$site/"
touch "$site/.nojekyll"   # serve files as-is (no Jekyll processing)
cd "$site"
git init -q -b prod
git config user.name "job-radar-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -q -m "dashboard $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q --force "https://x-access-token:${DASHBOARD_REPO_TOKEN}@github.com/${DASHBOARD_REPO}.git" prod
echo "dashboard published to ${DASHBOARD_REPO} (prod)"
