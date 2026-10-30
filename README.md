# job-radar

Free, fully GitHub-hosted job search automation for DevOps / SRE / platform roles. Every 4 hours it
searches India first, then remote, then the rest of the world, filters to roles you can actually get
(stated minimum 3 years or less), scores each against your resume with free AI, and publishes an
encrypted, password-protected dashboard. You apply by hand; nothing logs in anywhere or applies for you.

- **Dashboard**: `https://<your-github-username>.github.io/job-radar-dashboard/` (GitHub Pages, free)
- **Cost**: zero. Scans are paced through the month and hard-stop at 1,050 Actions minutes (CI adds ~50), so the
  total stays around 1,100 of the free 2,000/month at the very worst. Dashboard clicks cost 0 minutes.
- **Branch**: `prod` everywhere (this repo and the dashboard repo).

## What it does

| Area | Details |
|---|---|
| Sources | Indeed, Naukri, LinkedIn (public guest pages only, never your account); 400+ company career sites (Greenhouse, Lever, Ashby, SmartRecruiters, Workday) incl. Y Combinator companies, that grow automatically; Amazon/AWS and Microsoft job APIs; remote boards (Remotive, RemoteOK, We Work Remotely, Himalayas, Jobicy, HN Who is Hiring); Arbeitnow (visa-sponsored, Europe); worldwide rotation over 71 Indeed country sites + LinkedIn |
| Filters | target titles (~45 patterns), senior/lead/manager/level-3+ excluded, experience ≤ 3 years, ≤ 30 days old, expired postings removed, local-language-only jobs abroad removed, training "academies" removed |
| Ranking | **Apply next**: resume fit × location (Bangalore > Pune/Hyderabad > India > remote > abroad with visa) × experience fit × freshness × company, with reasons; closing-soon boost |
| AI apply kit | fit score, level fit, matched/missing skills, interview topics, red flags |
| Tailored resume | per job, printed to PDF in your own layout (one A4 page, Lato): bullets and skills reordered for the job, JD keywords added only when your resume already proves them, ATS keyword match before/after |
| Referral message | your 5-line format with the role and the employer's job ID filled in, plus a short note that fits LinkedIn's 300 characters; you send it yourself |
| Gmail sync | read-only: replies (rejection, assessment, interview, offer) move jobs you already marked applied; unclear emails are listed for you, never guessed |
| Autofill | Tampermonkey script for Greenhouse/Lever/Ashby forms: fills your details on click, optional AI draft for "Why us?"; never submits, never runs on LinkedIn |
| Learn | recent videos from 26 DevOps/cloud/AI channels, ranked by your stack and skill gaps |
| Lifecycle | closed-job checker (daily), ghost/repost warning, follow-up reminders after 7 days, daily goal + streak |
| Analytics | new jobs per day by region, your applications vs goal, skill gaps, strengths, hiring momentum, funnel, sources |
| Alerts | an email from GitHub when good new jobs arrive, plus a morning summary at ~8:47 AM IST |

## Architecture

A serverless system: GitHub Actions is the only compute (a fresh VM for ~5 minutes per run), the private repo is
the database, GitHub Pages is a global CDN for the static dashboard, and your browser decrypts the data locally.
No server, no database, $0. The full design, libraries, endpoints and cost model are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```mermaid
flowchart TB
    TRIGGER(["Cron every 4 hours<br/>or the Run workflow button"])
    REPO_IN[("<b>Private repo job-radar: the database</b><br/>state/ jobs, AI cache, minutes used<br/>profile/ resume, preferences<br/>companies.yaml: 775 career sites<br/>state/inbox/ your marks from the browser")]

    subgraph RUNNER["GitHub Actions runner: serverless, a fresh Ubuntu VM for ~5 minutes per run"]
        GUARD{"<b>Minutes guard</b> budget.py<br/>under 1,050 / month and on pace?"}
        STOP(["Record 1 min, stop"])
        LOAD["<b>Load</b> restore cached venv, read state,<br/>apply your marks from the inbox<br/>actions/cache, inbox.py"]
        FETCH["<b>Fetch engine</b> base.py + net.py<br/>one thread per source, 8 companies at a time,<br/>retries, 0.35 s per-host gap, 180 s budget,<br/>learn.py adds new career sites it sees"]
        PROC["<b>Clean and rank</b><br/>dedupe, filters, geo: title, seniority,<br/>max 3 yrs, age, language, visa<br/>recommend, match, tailor keywords"]
        AISTEP["<b>AI</b> scoring.py, letters.py, guard.py<br/>cache first, then free lanes,<br/>keyword score if none answer"]
        LIFE["<b>Lifecycle</b> closed-job check, Gmail replies,<br/>Learn videos, world employers"]
        SAVE["<b>Save</b> commit and push state<br/>commit_state.sh"]
        EXPORT["<b>Export</b> gzip + AES-256-GCM<br/>export.py, crypto.py via cryptography"]
        GUARD -- "no" --> STOP
        GUARD -- "yes" --> LOAD --> FETCH --> PROC --> AISTEP --> LIFE --> SAVE --> EXPORT
    end

    subgraph WEB["The internet: public endpoints, no login"]
        BOARDS["<b>Job boards</b><br/>Indeed GraphQL API, Naukri API,<br/>LinkedIn guest search, Glassdoor, Bayt<br/><i>python-jobspy: requests, curl_cffi</i>"]
        ATS["<b>775 company career sites</b><br/>Greenhouse, Lever, Ashby, SmartRecruiters,<br/>Workable, Recruitee, BambooHR APIs,<br/>Teamtailor RSS, Workday CXS API<br/><i>requests + beautifulsoup4</i>"]
        BIG["<b>Big tech + remote boards</b><br/>Amazon, Microsoft APIs, Remotive,<br/>RemoteOK, Himalayas, Jobicy, Arbeitnow,<br/>WWR RSS, HN via Algolia<br/><i>requests, xml.etree</i>"]
    end

    subgraph SIDE["Services called during the run"]
        AI["<b>Free AI lanes, in parallel</b><br/>Groq gpt-oss, Gemini Flash-Lite<br/>fallback: GitHub Models, OpenRouter :free"]
        READ["<b>Read-only inputs</b><br/>Gmail API, YouTube RSS,<br/>Wikidata SPARQL"]
    end

    CDN["<b>GitHub Pages: global CDN</b><br/>static app: index.html, app.js, map.js, CSS, vendor libs<br/>data: jobs.enc.json, world.enc.json, ciphertext only"]
    MAIL(["Email digest<br/>via GitHub issue"])

    subgraph BROWSER["Your browser: no backend"]
        UNLOCK["Passphrase -> PBKDF2 600k -> AES-GCM<br/>decrypted locally with WebCrypto"]
        APP["<b>Dashboard</b> app.js<br/>Today, Jobs, Pipeline, Insights, Learn<br/>Map: d3-geo + WebGL + NASA imagery<br/>tailored resume PDF, pdf.js upload"]
        SYNC{"Mark applied:<br/>token has Contents<br/>permission?"}
        UNLOCK --> APP --> SYNC
    end

    WF["update-status workflow<br/>batched, max 1 run per 5 min"]
    REPO_OUT[("<b>Private repo job-radar</b><br/>same repo, written back:<br/>new state, your marks in state/inbox/")]

    TRIGGER --> GUARD
    REPO_IN -- "checkout" --> LOAD
    BOARDS --> FETCH
    ATS --> FETCH
    BIG --> FETCH
    AISTEP <-- "HTTPS, prompt prefix cached" --> AI
    READ --> LIFE
    EXPORT -- "force-push" --> CDN
    EXPORT -- "notify.py" --> MAIL
    SAVE -- "git push" --> REPO_OUT
    CDN --> UNLOCK
    SYNC -- "yes: GitHub contents API,<br/>a small file, 0 min" --> REPO_OUT
    SYNC -- "no" --> WF
    WF -- "applies marks" --> REPO_OUT
```

### How a run works, step by step

Numbers below are from real scans (October 2026).

**1. The schedule fires.** GitHub's scheduler reads `cron: "17 */4 * * *"` in `.github/workflows/scan.yml` and
queues a job at :17 past every 4th hour (UTC). GitHub sometimes delays or skips scheduled runs when it is busy, so
in practice 4-6 runs a day happen. The **Run workflow** button queues the same job immediately.

**2. A machine is allocated.** GitHub takes a clean virtual machine from its pool (`ubuntu-latest`; for a private
repo that is a 2-core Linux VM with about 7 GB of RAM). Nothing from the previous run is on it, and it is destroyed
when the job ends. This is what "serverless" means here: there is no machine of yours that stays on; compute exists
only for the ~5 minutes a run needs, and GitHub bills only those minutes (free up to 2,000 a month).

**3. The code arrives.** `actions/checkout` copies the private repo onto the VM. That brings the code *and* the
data: the repo is the database, so `state/jobs.json` (every job you track), the AI cache, your resume and
preferences, the 775 career sites and your pending dashboard marks are all just files on disk now.

**4. The minutes guard decides (about 5 s).** Before anything is installed, `python3 -m src.budget check` reads
`state/usage.json`. If this month is already at 1,050 minutes, or ahead of an even pace for the date, the run
records one minute, commits that, and stops. Otherwise it continues. This is why the bill can never grow.

**5. Python is made ready (about 5 s).** `actions/setup-python` selects Python 3.11 and `actions/cache` restores the
whole virtual environment saved by an earlier run (`python-jobspy`, `requests`, `beautifulsoup4`, `PyYAML`,
`python-dateutil`, `cryptography`). Only when `requirements.txt` changes is it reinstalled (about 25 s).

**6. Your marks are applied (under 1 s).** `src/inbox.py` reads every file in `state/inbox/` (the Applied / stage /
note changes your browser saved), validates each one, applies it to the job, and deletes the file. An uploaded
resume is structured here too (`src/resume_import.py`).

**7. The internet is fetched (up to 180 s, usually 100-170 s).** `src/sources/base.py` starts **25 workers at
once, one per source** (Indeed, Naukri, Glassdoor, Bayt, LinkedIn, Greenhouse, Lever, Ashby, Workday, ...). They
run side by side inside the one Python process because almost all their time is spent *waiting* for websites to
answer, and waiting doesn't need the CPU. While Indeed is waiting for a reply, Greenhouse is reading one.
- The **job-board workers** call `python-jobspy`, which sends each search term x city (for example "devops engineer"
  in Bangalore) to the board's own search endpoint: Indeed's GraphQL API, Naukri's job API, LinkedIn's public guest
  search, and Glassdoor and Bayt with a browser-like connection (`curl_cffi`). Results come back as a table and are
  turned into job records.
- Each **career-site worker** opens up to **8 companies at a time**. For Greenhouse that means: GET
  `boards-api.greenhouse.io/v1/boards/<company>/jobs` (a JSON list), keep only titles that match your roles and
  that aren't already known, then GET each kept job's details and turn its HTML description into text with
  `beautifulsoup4`. Workday works the same way with a POST search; RSS feeds are read with `xml.etree`.
- Every request goes through one shared HTTP session (`src/net.py`): it retries on errors and rate limits with
  increasing waits, honours `Retry-After`, leaves 0.35 s between requests to the same website, and gives up on a
  slow site after 20 s.
- A worker that crashes is logged and ignored; one still running at 180 s is abandoned. The rest of the run
  continues with whatever arrived. Typical haul: about 1,700 unique postings.

**8. The postings are cleaned and ranked (a few seconds).**
- `enrich()` works out region (India, remote, abroad), country, "3+ years", visa and deadline wording.
- `dedupe()` merges the same job seen on several boards or cities.
- `JobFilter` applies your rules (titles, no senior roles, max 3 years, recency, language, visa).
- `drop_same_role()` removes roles you already track or applied to; about 200 per run are skipped this way.
- The ~120 ATS keywords and the employer's job id are pulled out of the full description, and `rank()` gives each
  job an apply priority from 0 to 100.
- Links that point to a company's own career site teach `learn.py` a new company to poll directly next time.

**9. AI scores the best new jobs (up to ~2 min).** The top unscored jobs (at most 48) are checked against the result
cache first: a job already scored for this resume costs nothing. The rest are cleaned of hidden instructions
(`src/guard.py`) and sent 4 per request to free models. Groq (two models) and Gemini work **in parallel lanes**,
each taking the next batch from a shared queue; if one hits its per-minute limit it pauses while the others carry
on, and GitHub Models / OpenRouter free models join only if they fall behind. Each answer is checked (score,
must-haves, brief, red flags, interview topics) and cached for 60 days. If no model answers, the keyword score
stands. Then up to 6 cover letters are drafted for top picks and fact-checked against your resume.

**10. Housekeeping (seconds to a minute).** Once a day, saved job links are re-opened to flag closed postings.
Gmail (read-only) is checked for replies about jobs you applied to. About three times a day the Learn videos are
refreshed from YouTube RSS, and once a month the employers-by-country list from Wikidata.

**11. State is saved (a few seconds).** `state/jobs.json` is written atomically (a crash can't corrupt it),
minutes used are recorded, and `scripts/commit_state.sh` commits and pushes to the private repo, retrying with a
rebase if your browser saved something in the meantime.

**12. The dashboard is published (about 10 s).** `src/export.py` builds one data file (jobs, tailoring plans,
analytics, videos), gzips it, and encrypts it with AES-256-GCM using a key derived from your passphrase (PBKDF2,
600,000 rounds). Asset links get a version stamp so browsers never mix old and new code. A safety check refuses to
publish anything that isn't the allow-listed files or that contains your email, phone or resume text. Then
`publish_dashboard.sh` force-pushes the folder as a single commit to the public `job-radar-dashboard` repo, and
GitHub Pages serves it from its global CDN about a minute later.

**13. You get an email.** `src/notify.py` posts a GitHub issue (or a comment) when good new jobs arrive and each
morning; GitHub emails it to you.

**14. The machine is destroyed.** Total: about 4-6 billed minutes. Nothing keeps running until the next schedule.

**In your browser.** The page and the encrypted data download from the CDN as soon as you open it. Your passphrase
turns into the key inside the browser (WebCrypto) and decrypts the data on your device; the passphrase and the
decrypted data never leave it. Everything you see (lists, charts, the globe drawn with WebGL, tailored resume PDFs)
is computed in the page. When you mark a job, the page saves a small file into `state/inbox/` through GitHub's API
(free), or, if your token lacks that permission, starts the small update-status workflow (batched). Step 6 of the
next run applies it.

### Why this design

- **$0 to run.** Every piece is inside a free tier, and the minutes guard makes overspending impossible.
- **Nothing to operate.** No server to patch, restart or keep online; a run that fails just tries again next time.
- **Private on a public host.** The CDN only ever holds ciphertext; only your passphrase opens it.
- **Fast where you are.** The dashboard is static files on a CDN, and all the work happens before you open it.
- **Resilient.** Each source is isolated and time-boxed, AI has several free fallbacks plus a keyword score, and
  every change to your data is a Git commit you can inspect or roll back.
- **Easy to grow.** A new source is one function; a new company is one line in `companies.yaml`.

## One-time setup (about 15 minutes)

You need: the free Groq and Gemini keys (already in your local `.env`), and two new GitHub items.

### 1. Create the public dashboard repo
github.com → **+** (top right) → **New repository** → name `job-radar-dashboard` → **Public** → leave
"Add a README" unchecked → **Create repository**. Leave it empty; the workflow fills it.

### 2. Create the publish token (lets this repo push to the dashboard repo only)
github.com → your photo → **Settings** → **Developer settings** → **Personal access tokens** →
**Fine-grained tokens** → **Generate new token**:
- Name `job-radar-publish`, Expiration: 1 year (set a calendar reminder to renew)
- Repository access: **Only select repositories** → `job-radar-dashboard`
- Permissions → Repository permissions → **Contents: Read and write** (nothing else)
- **Generate token**, copy it (starts with `github_pat_`).

### 3. Pick a dashboard passphrase
5 or more random words, e.g. `copper lantern mango orbit velvet` (make your own). The encrypted file is
public forever, so this passphrase is the only thing protecting it. Store it in your password manager.

### 4. Add 5 secrets to this repo
github.com/Karthik-099/job-radar → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**, once for each:

| Name | Value |
|---|---|
| `GROQ_API_KEY` | from your `.env` |
| `GEMINI_API_KEY` | from your `.env` |
| `OPENROUTER_API_KEY` | from your `.env` (credit limit 0, free models only) |
| `DASHBOARD_PASSWORD` | the passphrase from step 3 |
| `DASHBOARD_REPO_TOKEN` | the token from step 2 |

### 5. First run
**Actions** tab → if asked, click **I understand my workflows, go ahead and enable them** → **scan** →
**Run workflow** → branch `prod` → tick **backfill** → **Run workflow**. Takes about 5 minutes.

### 6. Turn on GitHub Pages
job-radar-dashboard → **Settings** → **Pages** → Source **Deploy from a branch** → Branch **prod** /
**(root)** → **Save**. About a minute later the dashboard is live at
`https://karthik-099.github.io/job-radar-dashboard/`.

### 7. Email alerts
github.com → your photo → **Settings** → **Notifications**: set the default email to your Gmail and make
sure **Watching → Email** is ticked. On the job-radar repo page, **Watch** should show "All Activity".

### 8. Optional: sync your marks to every device
Uses the token you already made (`job-radar-status`). Paste it in the dashboard → **Settings** → Sync →
**Save** → **Test**. Without a token, marks are saved in that browser only (still fully usable).

The dashboard picks the cheapest way the token allows:
- **Free (recommended, 1 minute once):** give the same token one more permission. github.com → your photo →
  **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → click
  `job-radar-status` → **Edit** → **Repository permissions** → **Contents** → **Read and write** → **Update**.
  Then press **Test** in the dashboard. Marks are saved as small files (0 Actions minutes) and other devices see them
  after Refresh.
- **Without that permission:** it still works through a short workflow run (~1-2 Actions minutes), batched to at most
  one run every 5 minutes. Counted in the 1,050-minute cap.

Extra isolation (optional, advanced): set repo variable `INBOX_REPO` + secret `INBOX_TOKEN` to keep the inbox in a
separate private repo, so the browser token can never write code. Not needed for normal use.

### 9. Optional: Gmail status sync (read-only, about 10 minutes, once)
Jobs you marked **Applied** move to Rejected / Shortlisted / Interview / Offer when the company replies.
The token can only *read*: Google refuses delete, move, label or send for it.

1. console.cloud.google.com → top bar project picker → **New project** → name `job-radar` → **Create**, then select it.
2. Search bar: **Gmail API** → **Enable**.
3. Left menu **APIs & Services** → **OAuth consent screen** (or **Google Auth Platform**) → **Get started**:
   app name `job-radar`, your Gmail as support email → Audience **External** → your Gmail as contact → **Create**.
4. **Audience** → **Test users** → **Add users** → your Gmail → **Save**. Then on the same page click
   **Publish app** → **Confirm** (otherwise Google expires the token every 7 days; you are the only user, and
   the "unverified app" warning you'll see on sign-in is expected for a personal app).
5. **Data access** → **Add or remove scopes** → tick `.../auth/gmail.readonly` only → **Update** → **Save**.
6. **Clients** → **Create client** → type **Desktop app** → name `job-radar` → **Create** → copy the Client ID
   and Client secret into your local `.env` as `GMAIL_CLIENT_ID=` and `GMAIL_CLIENT_SECRET=`.
7. Terminal, in this folder: `.venv/bin/python -m src.gmail_auth` → browser opens → pick your Gmail →
   "Google hasn't verified this app" → **Continue** → tick read access → **Continue**. It saves
   `GMAIL_REFRESH_TOKEN` into `.env` (nothing is printed).
8. Check before trusting it: `.venv/bin/python -m src.gmail_sync --dry-run` lists what it *would* change.
   If Google refuses sign-in after step 4 ("access blocked"), set the app back to **Testing**: it works the same,
   but the token expires every 7 days (the scan then skips Gmail and logs it; rerun step 7 and update the secret).
9. Add 3 repo secrets (Settings → Secrets and variables → Actions → New repository secret), copying each value
   from `.env`: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`. The next scan uses them.

### 10. Optional: autofill for Greenhouse, Lever and Ashby
Install the Tampermonkey extension → dashboard **Settings** → **Install autofill script** → **Install**.
Tampermonkey icon → **Job Radar: my details** → fill your details once (stored only in your browser).
On an application page click **⚡ Fill my details**, check everything, attach the tailored PDF, submit yourself.

### 11. Optional: more videos in Learn
A free YouTube Data API key (console.cloud.google.com → APIs & Services → Library → **YouTube Data API v3** →
Enable → Credentials → **Create credentials → API key**, restrict it to that API) adds each section's most-watched
videos of the month from all of YouTube. Save it as repo secret `YOUTUBE_API_KEY`. Uses ~2,000 of 10,000 free daily units.

## Daily use

1. Open the email or the dashboard, enter your passphrase.
2. **Today** shows Apply next, fresh jobs from the last 24 hours, closing-soon, and follow-ups.
3. **Open job** → apply on the company site → **Mark applied**. It moves to the Applied column.
4. Open **Details**: **Download tailored resume (PDF)** (print window → Save as PDF, A4, headers/footers off),
   the referral message (replace `[Name]`, copy), interview topics, and **Find a referrer** (opens a normal
   LinkedIn people search in your browser; nothing on LinkedIn is automated).
5. Jobs you applied to, and the same role found again under another city or board, disappear from the lists.
6. **Learn** has fresh videos by topic (System Design, DevOps, DevSecOps, Architecture, AWS, Azure, Kubernetes,
   Observability & SRE, AI & MLOps, Career). **Map** is a satellite globe (NASA imagery; "Map style" for a plain one): click any country for its
   matching jobs and top tech employers. **Insights** shows your funnel, reply rates by source, and whether high fit scores get replies.
7. Keyboard: `Ctrl K` search everything, `g j` jobs, `j`/`k` move, `a` applied, `x` hide, `?` all shortcuts.
   Themes: the moon button (Light, Dark, Midnight, Aurora, or match the system).

## Local commands

```bash
.venv/bin/pip install -r requirements-dev.txt
git config core.hooksPath .githooks                     # once: blocks committing secrets
.venv/bin/python -m pytest && node tests/js/crypto.test.mjs
.venv/bin/python -m src.main --dry-run                  # everything except saving/publishing/emailing
.venv/bin/python -m src.main --source workday --dry-run # one source
.venv/bin/python -m src.main --dry-run --countries japan,canada
.venv/bin/python -m src.discover "Company A" "Company B" # add companies' career sites
```

| Flag | Meaning |
|---|---|
| `--dry-run` | fetch, filter, score, print; write nothing |
| `--source NAME` | only these sources (repeatable or `a,b`) |
| `--backfill` | look back 30 days instead of each source's window |
| `--countries a,b` | world sources scan these countries instead of the rotation |
| `--max-llm N` | AI-score at most N jobs this run (cache hits are free) |
| `--no-score` / `--no-fetch` | skip AI scoring / work from saved state only |
| `--export` / `--notify` | build the encrypted dashboard / post the email digest (CI) |
| `--check-closed` | run the closed-job check now |
| `--gmail` | update applied jobs from Gmail replies (read-only) |

## Tuning

Everything lives in [profile/preferences.yaml](profile/preferences.yaml): title patterns, exclusions,
experience cap, city boosts, abroad countries, language rule, sources and their search terms, daily goal.
Your resume for scoring is [profile/resume.md](profile/resume.md) (no contact details, sent to the AI). The PDF is
printed from [profile/resume.yaml](profile/resume.yaml) (never sent to any AI): edit both when your resume changes.
Learn channels: [profile/youtube_channels.yaml](profile/youtube_channels.yaml).

## Docs

- [docs/SECURITY.md](docs/SECURITY.md): what is and isn't protected, and what to do if a key leaks
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): common problems and fixes
