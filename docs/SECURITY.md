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
- GitHub Actions: no payment method on the account means a $0 spending limit; plus our own 1,700-minute guard.

## What is and isn't protected

| Data | Where | Protection |
|---|---|---|
| Resume (`profile/resume.md`) | private repo | Not public. Sent to the AI providers for scoring. Contact details removed. Free tiers (Gemini, and many OpenRouter free models) may log or train on prompts, which is why the resume has no phone, email or address. |
| Job list, statuses, notes | private repo `state/` | Not public. |
| Dashboard data (Phase 4) | **public** repo | AES-256-GCM, key from your passphrase via PBKDF2 (600,000 iterations). The ciphertext is public forever, so security depends on passphrase strength: use 5+ random words. |
| Dashboard password | your head | Never stored. The derived key lives in browser memory only and is wiped on lock. |
| Gmail (Phase 5) | Google | Read-only scope. Only subject, sender domain, date and matched job id are stored, never email bodies. |

## If a key leaks

1. Revoke it immediately: Groq console > API Keys > delete; AI Studio > API keys > delete; OpenRouter > Keys > delete; GitHub > Settings > Developer settings > tokens > revoke.
2. Create a new one, update `.env` and the GitHub secret.
3. If it was committed, rotating the key is the fix (rewriting git history does not un-leak it).
