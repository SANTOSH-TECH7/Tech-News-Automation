# Tech Pulse - personal tech news app

Personal project (not related to any client work). Handover notes: read before changing
anything. Designed 12-13 Nov 2026 as a WhatsApp digest; switched on 15 Nov 2026 to an
installable web app with push notifications (see "History").

## Goal

The owner wants the latest tech news on their own Android phone, automatically, throughout
the day, with a notification for each digest, at **zero cost**, and without handing their
phone number or other personal details to a third party. One user only.

## Requirements the owner gave

- **Cost:** nothing paid, anywhere.
- **Privacy:** no phone number / email shared with outside gateways.
- **Phone:** Android. No sideloaded APK, no changes to phone security settings.
- **Notifications:** yes, one per digest.
- **Topics:** general tech, AI, data science, data engineering and its tools (Databricks,
  Fabric, Spark, dbt, etc.), data analyst / BI (Power BI, Tableau), gadgets, startups.
- **Format:** each story has a headline, a short description and a link to the article.
- **Hosting:** the owner's personal GitHub account.

## Design

1. **Sources:** RSS/Atom feeds plus Hacker News (via hnrss.org). Free, no API keys.
2. **Script:** `news.py` fetches feeds, drops anything already sent, picks a balanced set
   of fresh stories and prepends them to `docs/news.json`.
3. **App:** static site in `docs/`, served by GitHub Pages (main branch, `/docs` folder).
   Installed on the phone from Chrome ("Install app"). Reads `news.json`.
4. **Scheduler:** GitHub Actions cron. Runs without any PC being on.
5. **State:** `state/sent.json` (already-sent list) and `docs/news.json` are committed back
   to the repo by the workflow after each run.
6. **Notification:** standard Web Push. After the commit, the workflow waits until GitHub
   Pages serves the new `news.json`, then sends one push to the phone.

## Files

| File | Purpose |
|---|---|
| `news.py` | Fetch, select, publish, notify. Standard library only, except `--notify` which needs `pywebpush` |
| `.github/workflows/news.yml` | Schedule, run, commit, notify |
| `docs/index.html` | The app: story feed, category filter, notification setup panel (bell button) |
| `docs/sw.js` | Service worker: shows push notifications, offline fallback |
| `docs/manifest.webmanifest`, `docs/icon-*.png` | Make the page installable |
| `docs/news.json` | Feed shown by the app: `{updated, stories: [{title, url, summary, source, category, when, batch}]}`, newest first, capped at 80 |
| `state/sent.json` | Created on the first real run. List of `{u, t, ts}`, pruned to 30 days |

## How `news.py` works

- **Modes:** `python news.py` (fetch + write files), `--dry-run` (print only, writes
  nothing; on Windows set `PYTHONUTF8=1` so the emoji print), `--notify` (send the push
  for the latest digest in `docs/news.json`).
- **Settings at the top:** `MAX_ITEMS = 8` stories per run, `MAX_PER_SOURCE = 2`,
  `MAX_AGE_HOURS = 36`, `SUMMARY_CHARS = 230`, `FEED_KEEP = 80`, `PUSH_TTL = 3h`.
- **Feeds:** `FEEDS` list of `(source name, default category, url)`, 21 entries.
- **Categories:** AI, Data Engineering, Data Science, BI & Analytics, Tech, Startups,
  Gadgets. Stories from the general feeds are re-filed when the title matches a regex in
  `TOPIC_RULES`.
- **Selection (`select`):** stories from the last 36 hours not in the sent list;
  round-robin across categories, newest first, max 2 per source.
- **De-duplication:** by cleaned URL and by title similarity (`similar`), against this
  run's picks and titles sent in the last 24 hours.
- **Delivery (`publish`):** one swappable function that writes `docs/news.json`.
- **Notification (`notify`):** needs env vars `VAPID_PRIVATE_KEY` and `PUSH_SUBSCRIPTION`.
  If either is missing it skips quietly, so the feed works before notifications are set
  up. HTTP 404/410 from the push service means the subscription expired: the owner opens
  the app, taps the bell, copies the text and updates the `PUSH_SUBSCRIPTION` secret.
  Optional `VAPID_SUBJECT` (default `mailto:noreply@example.com`) and `PAGES_URL`
  (default derived from `GITHUB_REPOSITORY`).

## Push keys

- The VAPID **public** key is hard-coded in `docs/index.html` (`VAPID_PUBLIC_KEY`).
- The matching **private** key is only in the GitHub secret `VAPID_PRIVATE_KEY`
  (base64url of the raw 32-byte P-256 key). It is not stored in the repo.
- If the key pair is ever regenerated, both must change together and the phone must
  re-subscribe (clear the site's data in Chrome, reopen, tap the bell).

## Schedule

`cron: "0 2,5,8,11,14,17 * * *"` (UTC) = 7:30, 10:30, 13:30, 16:30, 19:30, 22:30 IST.
Six digests a day, up to 8 stories each. GitHub's scheduler can start several minutes
late. Also has `workflow_dispatch` for manual runs.

## Status (15 Nov 2026)

Done and checked locally: `news.py` compiles; against the live feeds 20 of 21 loaded
(VentureBeat failed) and 8 stories were selected; `publish` produced a valid feed file.

Never tested: the GitHub Actions workflow, GitHub Pages hosting, installing the app on the
phone, and the push notification end to end.

## Owner setup steps

1. Create a **public** repo on GitHub (free GitHub Pages needs public) and push.
2. Repo Settings -> Pages -> Deploy from a branch -> `main`, folder `/docs`.
3. Add the Actions secret `VAPID_PRIVATE_KEY`.
4. On the phone, open `https://<username>.github.io/<repo>/` in Chrome, tap
   "Install app", open it, tap "Turn on notifications", allow, tap "Copy".
5. Save the copied text as the Actions secret `PUSH_SUBSCRIPTION`.
6. Actions tab -> "Tech News Digest" -> "Run workflow" to test.

## Known gaps

- **Public page:** anyone with the link can read the feed (headlines only; marked noindex).
- **Subscription can expire** (reinstall, cleared Chrome data, long inactivity) and then
  needs the manual re-copy in step 4-5.
- **Battery savers** on some Android brands can delay notifications; exempt Chrome.
- **Repeated big stories:** differently worded takes on one event can land in one digest.
- **VentureBeat** returns HTTP 429 from the owner's laptop; failed feeds are skipped.
- **Windows laptop quirk (15 Nov 2026):** C: was full and only PowerShell could write
  into this folder (Git Bash, Python and Node writes were refused).

## History / options rejected

| Option | Why not |
|---|---|
| CallMeBot WhatsApp gateway (original plan) | Owner did not want to share their phone number with a third party |
| Meta WhatsApp Cloud API | Needs approved templates, charged per message |
| Unofficial WhatsApp libraries, or posting to the "You" chat | Against WhatsApp terms, ban risk, full access to chats |
| Email digest via a spare Gmail account | Offered; owner preferred an app |
| Telegram bot | Offered; owner preferred an app |
| Native APK | Needs "install unknown apps", Play Protect warnings, heavier build |

## Working rules for this repo

- Keep everything zero-cost. Standard library only, apart from `pywebpush` for `--notify`.
- Never commit the VAPID private key or the push subscription; they live only in GitHub
  secrets (or a local `.env`, which is git-ignored).
- Commit messages: plain, no Co-Authored-By or "Generated with" lines.
- Keep delivery in one swappable function so the channel can change without touching
  fetching or selection.
