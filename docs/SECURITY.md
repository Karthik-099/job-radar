# Security

## Where secrets live

| Secret | Local | CI |
|---|---|---|
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | `./.env` (mode 600, git-ignored) | GitHub Actions secret |
| `DASHBOARD_PASSWORD`, `DASHBOARD_REPO_TOKEN` (Phase 4) | `./.env` | GitHub Actions secret |
| Gmail OAuth (Phase 5) | `./.env` | GitHub Actions secret |

Nothing secret is ever written to `state/`, `companies.yaml`, logs, or the dashboard repo.

## Guards in place

- **Git ignore**: `.env`, `.env.*`, `**/.env`, key files and OAuth JSON files are ignored (`.env.example` is the only exception).
- **Pre-commit hook** (`.githooks/pre-commit`, enabled with `git config core.hooksPath .githooks`):
  blocks staged `.env`/key files, key-shaped strings (Groq `gsk_`, OpenRouter `sk-or-`, Google `AIza`/`AQ.`, GitHub `ghp_`/`github_pat_`,
  OAuth refresh tokens), and email addresses or phone numbers under `profile/`.
- **Log redaction** (`src/envsecrets.py`): every secret read through `secret()` is masked as `***` in all log
  output, and key-shaped strings are masked even if they never went through `secret()`.
- **Keys go only to their own provider**, in request headers (not URLs).
- **`.env` permission check**: a warning is printed if the file is readable by other users.

## Paying nothing for AI

- Groq and Gemini keys are on free tiers with no card attached.
- OpenRouter: three independent locks. (1) Only models whose id ends in `:free` and whose every price is exactly
  0 are selected (`rank_free_models`). (2) Every request carries `provider.max_price = 0`, so OpenRouter refuses
  any paid route. (3) The key's credit limit is 0 (verified: `limit: 0`, `is_free_tier: true`).
- GitHub Actions: no payment method on the account means a $0 spending limit; plus our own guard: scans are paced
  evenly through the month and hard-stop at 1,050 minutes (src/budget.py), status clicks use no workflow at all.

## What is and isn't protected

| Data | Where | Protection |
|---|---|---|
| Resume (`profile/resume.md`) | private repo | Not public. Sent to the AI providers for scoring. Contact details removed. Free tiers (Gemini, and many OpenRouter free models) may log or train on prompts, which is why the resume has no phone, email or address. |
| Job list, statuses, notes | private repo `state/` | Not public. |
| Dashboard data (Phase 4) | **public** repo | AES-256-GCM, key from your passphrase via PBKDF2 (600,000 iterations). The ciphertext is public forever, so security depends on passphrase strength: use 5+ random words. |
| Dashboard password | your head | Never stored. The derived key lives in browser memory only and is wiped on lock. |
| Gmail (Phase 5) | Google | Read-only scope. Only subject, sender domain, date and matched job id are stored, never email bodies. |

## Dashboard, browser and logs

- **Public dashboard repo** holds only static files, pinned third-party libraries (`dashboard/vendor/`, licenses in its
  README) and AES-256-GCM ciphertext (`data/jobs.enc.json`, `data/world.enc.json`). The publish step refuses any other
  file, any plaintext data file, and any file containing your email, phone or resume lines.
- **Strict Content-Security-Policy**: scripts, styles and fonts only from the dashboard itself; network only to
  api.github.com; images also from YouTube thumbnails. No CDN, no analytics, no inline script.
- **Untrusted text** (job postings, emails, company names) is only ever inserted as text, never HTML; links must be
  http(s) and open with `noopener noreferrer`.
- **Prompt injection**: job postings and emails pass through `src/guard.py` before any AI sees them (invisible
  characters, hidden comments and lines that address an AI are removed); postings that tried it get a "Hidden text" flag.
- **Actions logs** live in the private repo (only you can open them) and the scan runs with `--quiet`: no job titles,
  companies, email subjects or resume text are printed. The public dashboard repo runs no workflows of its own.
- **Sync token**: stored in the browser encrypted with your passphrase. Use the separate `job-radar-inbox` repo
  (README step 8) so this token can only write inbox files, never code or workflow secrets. Inbox files are
  size-capped and validated; unknown kinds are dropped.
- **Resume upload**: the PDF is read in your browser; only its text is stored, in your private repo. Email, phone and
  links are removed before the text goes to the AI; every structured bullet must appear word for word in your PDF.

## If a key leaks

1. Revoke it immediately: Groq console > API Keys > delete; AI Studio > API keys > delete; OpenRouter > Keys > delete; GitHub > Settings > Developer settings > tokens > revoke (also the dashboard's inbox token; then Settings > Clear this device on each browser).
2. Create a new one, update `.env` and the GitHub secret.
3. If it was committed, rotating the key is the fix (rewriting git history does not un-leak it).
