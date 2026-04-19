# Daily YouTube Digest

A small Python workflow that watches a list of YouTube channels, summarises any new videos with Claude, and emails the result as a single executive-style digest each morning.

Built originally as a personal tool — I wanted to keep up with strategically useful YouTube channels without losing 30 minutes a day to skimming. Now it lands in my inbox before I start work, formatted so I can decide in 60 seconds whether each video is worth a deeper watch.

## Why this exists

The same problem most knowledge workers have: too many great channels, not enough time, and a YouTube homepage optimised for engagement rather than relevance.

This script flips the model:

- **You pick the channels.** No algorithmic feed.
- **Claude summarises through *your* lens.** A configurable persona steers the framing — the default is an aspiring innovation/transformation director, but it can be a software engineer, PM, founder, researcher, anything (see [Customising the persona](#customising-the-persona)).
- **The email is built for skimmers.** TL;DR up top, "why it matters" bullets in the middle, deeper context below, one click to the original video.
- **State is persisted.** Already-summarised videos never get summarised twice.

## What you get

Each morning, one email per batch of new videos. Per video:

- Channel + publish time
- Title + a red "Watch on YouTube" button
- **TL;DR** — one sentence
- **Why it matters** — 2-3 bullets framed for your persona
- **Deep dive** — 4-6 bullets covering content, examples, frameworks

If a video has no transcript (rare, mostly applies to music or some shorts), it's listed with a "no transcript available" note instead of being skipped silently.

## Requirements

- Python 3.11+
- A Google account (for the YouTube Data API v3 key)
- An Anthropic account (for the Claude API key)
- A Gmail account with 2-Step Verification enabled (for the App Password)

## Local setup

```bash
git clone https://github.com/markteojunyi/youtube-digest.git
cd youtube-digest

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env with your real keys (see below)
```

### Getting the keys

| Variable | Where to get it |
|---|---|
| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com/) → create a project → enable **YouTube Data API v3** → Credentials → Create API key. Choose "public data" — no OAuth needed. |
| `YOUTUBE_CHANNEL_IDS` | On any YouTube channel page, View Source → search for `"channelId":"UC...` — copy the `UC...` string. Multiple channels: comma-separated. |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API keys → Create. |
| `GMAIL_ADDRESS` | The Gmail you want to send *from*. |
| `GMAIL_APP_PASSWORD` | Google account → Security → 2-Step Verification → App passwords. **Not** your regular Gmail password — the 16-char App Password. |
| `EMAIL_TO` | Where the digest goes. Comma-separated for multiple recipients. |
| `LOOKBACK_HOURS` | How many hours back to look for new videos (default `24`). |
| `VIEWER_PROFILE` | The persona Claude writes for. Optional — defaults to the innovation-director one. |
| `CLAUDE_MODEL` | Which Claude model to use. Optional — defaults to `claude-sonnet-4-6`. See [Choosing a Claude model](#choosing-a-claude-model). |

### Run it

```bash
python main.py
```

You should get the digest in your inbox within a minute. Run again immediately and it'll do nothing — videos already summarised are tracked in [processed_videos.json](processed_videos.json).

To force a re-run for testing, just empty that file: `echo "[]" > processed_videos.json`.

## Deploy as a daily cron (GitHub Actions)

The included workflow at [.github/workflows/daily-digest.yml](.github/workflows/daily-digest.yml) runs every day at **05:00 Singapore time** (21:00 UTC the day before). To schedule for a different timezone, edit the cron line — it uses standard UTC cron syntax.

In your fork: **Settings → Secrets and variables → Actions**. The workflow expects two groups:

### 1. Secrets tab — sensitive credentials

Things you don't want exposed in workflow logs. Once saved, the value can't be read back, only re-entered.

| Name | What it is |
|---|---|
| `YOUTUBE_API_KEY` | Your Google Cloud API key. |
| `ANTHROPIC_API_KEY` | Your Claude API key. |
| `GMAIL_ADDRESS` | The Gmail you send from. |
| `GMAIL_APP_PASSWORD` | The 16-char Gmail App Password. |
| `VIEWER_PROFILE` | The persona Claude writes for. *(Optional — defaults to the innovation-director one. Stored as a Secret here purely as a personal preference; nothing about it is sensitive, so you could equally put it under Variables.)* |

### 2. Variables tab — non-sensitive config

Plain-text values you'll want to read and edit later without re-typing. Each has a sensible default in the code if unset.

| Name | What it is |
|---|---|
| `YOUTUBE_CHANNEL_IDS` | Comma-separated channel IDs (`UC...,UC...`). |
| `EMAIL_TO` | Comma-separated recipient email(s). |
| `LOOKBACK_HOURS` | How many hours back to scan (default `24`). |
| `CLAUDE_MODEL` | Which Claude model to use (default `claude-sonnet-4-6`). See [Choosing a Claude model](#choosing-a-claude-model). |

### 3. Test the workflow manually

**Actions** tab → *Daily YouTube Digest* → *Run workflow*. If the secrets and variables are right, the digest lands in your inbox within a minute or two. After that, the cron takes over.

The workflow commits the updated [processed_videos.json](processed_videos.json) back to the repo each run — that's why videos never get summarised twice across runs.

## Customising the persona

The single most useful customisation. The persona changes the entire tone of the "TL;DR" and "Why it matters" sections.

**Locally:** edit `VIEWER_PROFILE` in your `.env`.

**In GitHub Actions:** edit the `VIEWER_PROFILE` repo Variable — no code change, no redeploy.

Examples:

```
VIEWER_PROFILE="a software engineer evaluating new tools and frameworks for their team"
VIEWER_PROFILE="a product manager hunting for user-research signals and growth patterns"
VIEWER_PROFILE="a founder scanning for fundraising, GTM, and competitive moves"
VIEWER_PROFILE="a high school teacher looking for classroom-ready ideas and analogies"
```

The more specific the persona, the sharper the framing.

## Choosing a Claude model

Set the `CLAUDE_MODEL` variable to pick the model. All three use the same Anthropic API key — only the cost, speed, and quality change.

| Model ID | When to use |
|---|---|
| `claude-sonnet-4-6` | **Default.** Balanced quality and cost. Good for most channels. |
| `claude-haiku-4-5-20251001` | Cheapest and fastest. Fine for short videos or large channel lists where you want to keep costs minimal. |
| `claude-opus-4-7` | Highest quality. Use if you want maximum nuance in the "why it matters" framing — at noticeably higher cost. |

Want to plug in a non-Claude model (OpenAI, Gemini, local)? That requires editing `summarize()` in [main.py](main.py#L91) directly — different SDK, different message format, different response shape. Not a one-line change.

## How it works (quick tour)

[main.py](main.py) does roughly this, in order:

1. Load processed video IDs from [processed_videos.json](processed_videos.json).
2. For each channel, hit the YouTube Data API for videos published in the last `LOOKBACK_HOURS`.
3. Filter out anything already processed.
4. For each new video: fetch the transcript via `youtube-transcript-api`.
5. Send the transcript to Claude Sonnet with a structured prompt (persona-driven), parse the JSON response.
6. Render an HTML email (with plain-text fallback) and send via Gmail SMTP.
7. Persist the new IDs back to [processed_videos.json](processed_videos.json).

## Notes & limits

- The YouTube Data API has a default quota of 10,000 units/day. Each `search.list` call costs 100 units, so you can comfortably monitor dozens of channels per day.
- Videos without captions are skipped (the email will note this rather than guess).
- The free Anthropic tier covers small personal use; large channel lists may need a paid key.
- Gmail SMTP is fine for a personal digest. For high-volume, swap in a transactional provider (SendGrid, Postmark, Resend) — only `send_email()` would need to change.

## License

MIT. Fork it, customise it, ship it. If you build something interesting on top, let me know.
