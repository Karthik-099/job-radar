# Troubleshooting

Start with the run page: **Actions → scan → latest run**. The job summary shows per-source counts,
seconds and errors, rejections by reason, AI scoring counts, and run duration.

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard says "No data published yet" | First scan hasn't finished, or Pages isn't on | Run **scan** with backfill; then dashboard repo → Settings → Pages → branch `prod` / root |
| Dashboard 404 | Pages not enabled, or repo not named `job-radar-dashboard` | Enable Pages; if you used another name, set repo variable `DASHBOARD_REPO` = `owner/name` (Settings → Secrets and variables → Actions → Variables) |
| "Wrong passphrase" with the right one | `DASHBOARD_PASSWORD` secret differs from what you type (spaces, case) | Re-save the secret, re-run scan |
| Publish step: "DASHBOARD_REPO_TOKEN not set" | Secret missing | Add it (README step 4) |
| Publish step: 403 / permission denied | Token lacks access to the dashboard repo or expired | Regenerate: only `job-radar-dashboard`, Contents read and write |
| No emails | Notifications off, or you're not watching the repo | README step 7. Check the repo's **Issues** tab: a "Job Radar · date" issue means digests are being posted |
| Emails too frequent | Many good jobs | Raise `WORTH_ALERT` in `src/notify.py` (default priority 60) |
| AI kit missing on cards | Job not scored yet (40 per run), or all AI lanes hit limits | It fills in over the next runs. Logs show `scoring: N via LLM` and any `lane closed` |
| `lane closed ... HTTP 401/403` | API key wrong or revoked | Re-create the key, update the secret |
| `lane closed ... daily limit` | Free daily quota used | Automatic: other lanes continue, keyword scores fill the gap, quota resets daily |
| Source shows `timeout` or 0 jobs | Board slow or blocking CI IPs (LinkedIn, Google often do) | Nothing to do; other sources cover it. To test locally: `--source NAME --dry-run -v` |
| Many irrelevant titles | Include patterns too broad | Edit `titles.include` / `titles.exclude` in preferences.yaml |
| Good jobs missing | Title doesn't match, or stated years > 3 | Check rejection counts in the run summary; widen `titles.include` or set `experience.max_required_years: 4` |
| Runs skipped with "minutes used ... SKIP" | Budget guard reached 1,700 minutes this month | Resets on the 1st. Usage is in `state/usage.json` |
| "Applied" ticks not syncing | No sync token, wrong repo, or token lacks Actions write | Dashboard → Settings → Test. Token: only `job-radar`, Actions read and write |
| `state pushed` fails repeatedly | Concurrent manual commits | Pull locally and push again; workflows share one concurrency group |
| Scheduled runs stop | GitHub pauses schedules if the workflow file is broken | Check the Actions tab for a red banner; re-enable the workflow |
| Pre-commit blocks a commit | It found a key, `.env`, or contact details in profile/ | Remove it. Never use `--no-verify` for real secrets |

## Useful local checks

```bash
.venv/bin/python -m src.main --source greenhouse --dry-run -v   # one source, verbose
.venv/bin/python -m src.budget check                             # minutes used this month
bash scripts/scan_secrets.sh                                     # secret scan of tracked files
```

## New features

| Symptom | Likely cause | Fix |
|---|---|---|
| "Applied today" higher than Insights / GitHub | Marks still syncing, or a sync run was cancelled by GitHub | Automatic now: every sync re-sends all unconfirmed marks; the pill shows "N saving to GitHub…" until the data includes them |
| Log: `gmail sync skipped: ... invalid_grant` | Refresh token expired (app in Testing mode = 7 days) or revoked | `python -m src.gmail_auth`, then update the `GMAIL_REFRESH_TOKEN` secret |
| Gmail didn't update a job | Job not marked applied, email unclear, or two applied jobs at that company | See **Today → Check by hand**; `python -m src.gmail_sync --dry-run` explains each skip |
| Resume PDF has a date/URL at the top | Browser print headers on | Print dialog → More settings → untick **Headers and footers** |
| Resume PDF is two pages | Much longer resume.yaml | Shorten a bullet; the dashboard shrinks text down to ~8pt before giving up |
| Learn tab empty | Feeds not fetched yet (every ~12h) | Wait for the next scan |
