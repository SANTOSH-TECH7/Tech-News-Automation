# Tech News → WhatsApp digest

Personal project (not related to any client work). This file is the full handover from the
session that designed and built it on 12–13 Nov 2026. Read it before changing anything.

## Goal

The owner wants the latest tech news pushed to their own WhatsApp number, automatically,
throughout the day, at **zero cost**. Only one recipient: their own phone.

## Requirements the owner gave

- **Channel:** WhatsApp, via CallMeBot (free personal-use gateway). Chosen by the owner.
- **Cost:** nothing paid, anywhere. No paid APIs, no paid hosting.
- **Topics:** general tech, AI, data science, data engineering and its tools (Databricks,
  Fabric, Spark, dbt, etc.), data analyst / BI (Power BI, Tableau), gadgets, startups.
- **Frequency:** updates throughout the day.
- **Format:** each story has a headline, a short few-line description and a link to the
  article on its own site. Must look neat and attractive in WhatsApp.
- **Hosting:** the owner's personal GitHub account.

## Design (agreed with the owner)

1. **Sources:** RSS/Atom feeds plus Hacker News (via hnrss.org). Free, no API keys.
2. **Script:** `news.py` fetches feeds, drops anything already sent, picks a balanced set
   of fresh stories, formats WhatsApp messages.
3. **Scheduler:** GitHub Actions cron. Runs without any PC being on.
4. **State:** the "already sent" list is `state/sent.json`, committed back to the repo by
   the workflow after each run.
5. **Delivery:** CallMeBot — one HTTP GET per message.

### Options considered and rejected

| Option | Why not |
|---|---|
| Meta WhatsApp Cloud API (official) | Bot-initiated messages need approved templates and are charged per message |
| Unofficial libraries (Baileys, whatsapp-web.js) | Against WhatsApp terms, ban risk, needs an always-on machine |
| Twilio sandbox | Trial credit only; must re-join every 72 hours |
| TextMeBot | Paid |
| AI-written summaries | Needs an LLM API; summaries use the feed's own description instead |
| Telegram bot | Not rejected — it is the agreed **fallback** if CallMeBot stays unavailable (free, official, no waiting list). Owner has not chosen it yet |

## Files

| File | Purpose |
|---|---|
| `news.py` | The whole program. Python standard library only (no pip installs). Works on Python 3.9+ |
| `.github/workflows/news.yml` | Schedule + run + commit of `state/sent.json` |
| `state/sent.json` | Created on the first real send. List of `{u: url, t: title, ts: iso-time}`, pruned to 30 days |
| `.gitignore` | `__pycache__`, `.env` |

## How `news.py` works

- **Settings at the top:** `MAX_ITEMS = 8` stories per run, `ITEMS_PER_MESSAGE = 4`
  (so two WhatsApp messages per digest), `MAX_PER_SOURCE = 2`, `MAX_AGE_HOURS = 36`,
  `SUMMARY_CHARS = 230`.
- **Feeds:** `FEEDS` list of `(source name, default category, url)`, 21 entries:
  TechCrunch AI, VentureBeat, MIT Tech Review, Google AI, OpenAI, Hugging Face,
  Databricks, Microsoft Fabric, AWS Big Data, Data Engineering Weekly, Seattle Data Guy,
  Confluent, KDnuggets, Towards Data Science, Power BI Blog, Tableau, The Verge,
  Ars Technica, Hacker News (front page, 200+ points), TechCrunch, Engadget.
- **Categories:** AI, Data Engineering, Data Science, BI & Analytics, Tech, Startups,
  Gadgets. Stories from the general feeds (Tech / Startups / Gadgets) are re-filed into a
  specific category when the title matches a regex in `TOPIC_RULES`.
- **Selection (`select`):** only stories from the last 36 hours that are not in the sent
  list; round-robin across categories, newest first, max 2 per source.
- **De-duplication:** by cleaned URL (utm_ parameters stripped) and by title similarity
  (`similar`: 3+ shared words longer than 3 letters, or word-set Jaccard ≥ 0.6), checked
  against this run's picks and titles sent in the last 24 hours.
- **Summary:** the feed's description, HTML stripped, boilerplate removed, cut at a
  sentence boundary. Hacker News items get "Trending on Hacker News — N points, M comments".
- **Message format** (WhatsApp markdown: `*bold*`, `_italic_`):

  ```
  🗞️ *TECH PULSE* (1/2)
  🕒 Thu 12 Nov, 10:05 PM IST
  ━━━━━━━━━━━━━━━

  🛠️ *DATA ENGINEERING*

  ▪️ *Headline*
  _Two-line summary._
  📰 Source · 1h ago
  🔗 https://...
  ```

- **Sending (`send_whatsapp`):**
  `https://api.callmebot.com/whatsapp.php?phone=<phone>&text=<url-encoded text>&apikey=<key>`,
  up to 3 attempts, 6 s pause between messages. A story is added to the sent list only
  if its message was accepted.
- **Dry run:** `python news.py --dry-run`, or simply run without the two env vars. Prints
  the messages, sends nothing, does not touch the state file. On Windows set
  `PYTHONIOENCODING=utf-8` first so the emoji print.
- **Env vars:** `CALLMEBOT_PHONE` (number with country code, no spaces),
  `CALLMEBOT_APIKEY`.

## Schedule

`cron: "0 2,5,8,11,14,17 * * *"` (UTC) = 7:30, 10:30, 13:30, 16:30, 19:30, 22:30 IST.
Six digests a day, up to 8 stories each. GitHub's scheduler can start several minutes
late. Also has `workflow_dispatch` for manual runs. Usage is roughly 200 of the 2,000
free Actions minutes a month on a private repo.

## Current status (as of 13 Nov 2026)

Done:
- `news.py` and the workflow are written.
- Dry run against the live feeds worked: 19 of 21 feeds returned data, 8 stories selected,
  output formatted as above.
- Local git repo initialised on `main` with the code committed.

Not done / never tested:
- **Nothing has ever been sent to WhatsApp.** The send path is untested end to end.
- The GitHub repo has not been created and nothing is pushed (GitHub CLI was not
  installed on the first laptop).
- The GitHub Actions workflow has never run.

### Blocker: CallMeBot is full

On 13 Nov 2026 the CallMeBot page
(https://www.callmebot.com/blog/free-api-whatsapp-messages/) said the free WhatsApp bot
is full and hid the bot's phone number (`+34*********`), with "check back in a few days".
No API key can be obtained until the number is visible again. Re-check the page before
assuming this still holds.

## Remaining steps for the owner

1. **Get the CallMeBot key** (once the number is visible on the page above):
   1. Save the bot's `+34…` number from the page as a contact.
   2. Send it this exact WhatsApp message: `I allow callmebot to send me messages`
   3. Within about two minutes it replies "API Activated for your phone number. Your
      APIKEY is NNNNNN".
   4. If no reply in two minutes, the page says to retry after 24 hours, not immediately.
   5. Quick test in a browser:
      `https://api.callmebot.com/whatsapp.php?phone=+91XXXXXXXXXX&text=hello&apikey=KEY`
2. **Create an empty repo** on the owner's personal GitHub, e.g. `tech-news-whatsapp`
   (private is fine).
3. **Add two Actions secrets** (Settings → Secrets and variables → Actions):
   `CALLMEBOT_PHONE` and `CALLMEBOT_APIKEY`.
4. **Push:**
   ```
   git remote add origin https://github.com/<username>/tech-news-whatsapp.git
   git push -u origin main
   ```
5. **Test:** Actions tab → "Tech News Digest" → "Run workflow". A digest should arrive
   within a minute, and the run should commit `state/sent.json`.

## Known gaps

- **Repeated big stories:** when one event dominates the news, two or three differently
  worded takes on it can land in the same digest. The similarity check only catches
  headlines that share words.
- **Two feeds failed from the first laptop:** VentureBeat (HTTP 429 rate limit) and
  Engadget (SSL handshake failure on Python 3.9). Failed feeds are skipped, not fatal.
  Whether they work from GitHub's runners is unknown.
- **CallMeBot message length limit is unknown.** If digests arrive cut off, lower
  `ITEMS_PER_MESSAGE` from 4 to 2.
- **CallMeBot is a third party:** free, personal use only, text only, can have downtime.
- **OpenAI and Hugging Face feeds return ~1,000 items each;** harmless because of the
  36-hour filter, but they dominate the "stories fetched" count in the log.

## If switching to Telegram (fallback)

Only delivery changes: replace `send_whatsapp` and the two env vars / secrets with a bot
token and chat id, calling `https://api.telegram.org/bot<token>/sendMessage`. Telegram's
markdown differs from WhatsApp's, so `build_messages` formatting needs adjusting too.
Ask the owner before switching — they asked for WhatsApp.

## Working rules for this repo

- Keep everything zero-cost and standard-library-only unless the owner says otherwise.
- Never commit the phone number or API key; they live only in GitHub secrets (or a local
  `.env`, which is git-ignored).
- Commit messages: plain, no Co-Authored-By or "Generated with" lines.
- Keep delivery in one swappable function so the channel can change without touching
  fetching or selection.
