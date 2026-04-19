"""Daily YouTube channel monitor: fetches new videos, summarizes transcripts with Claude, emails the digest."""

import html
import json
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable

load_dotenv()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
YOUTUBE_CHANNEL_IDS = _split_csv(os.environ["YOUTUBE_CHANNEL_IDS"])
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
EMAIL_TO = _split_csv(os.environ["EMAIL_TO"])
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))

PROCESSED_FILE = Path(__file__).parent / "processed_videos.json"
CLAUDE_MODEL = "claude-sonnet-4-6"

VIEWER_PROFILE = (
    "an aspiring innovation/transformation director who scans these to brief "
    "leadership and spot strategic signals worth acting on"
)


def load_processed_ids() -> set[str]:
    if not PROCESSED_FILE.exists():
        return set()
    return set(json.loads(PROCESSED_FILE.read_text()))


def save_processed_ids(ids: set[str]) -> None:
    PROCESSED_FILE.write_text(json.dumps(sorted(ids), indent=2))


def fetch_recent_videos(youtube, channel_id: str, since: datetime) -> list[dict]:
    """Return videos uploaded to channel since the given UTC datetime."""
    response = (
        youtube.search()
        .list(
            part="snippet",
            channelId=channel_id,
            order="date",
            type="video",
            publishedAfter=since.isoformat().replace("+00:00", "Z"),
            maxResults=25,
        )
        .execute()
    )
    return [
        {
            "video_id": item["id"]["videoId"],
            "title": html.unescape(item["snippet"]["title"]),
            "channel_title": html.unescape(item["snippet"]["channelTitle"]),
            "published_at": item["snippet"]["publishedAt"],
            "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
        }
        for item in response.get("items", [])
    ]


def fetch_transcript(video_id: str) -> str | None:
    try:
        api = YouTubeTranscriptApi()
        snippets = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        return " ".join(snippet.text for snippet in snippets)
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
        return None
    except Exception as exc:
        print(f"  transcript error for {video_id}: {exc}")
        return None


def summarize(client: Anthropic, title: str, transcript: str) -> dict:
    """Return a structured summary: {tldr, why_it_matters: [...], deep_dive: [...]}."""
    prompt = (
        f'You are summarising a YouTube video for {VIEWER_PROFILE}.\n\n'
        f'Video title: "{title}"\n\n'
        f"Produce a JSON object with exactly these keys:\n"
        f'- "tldr": one punchy sentence (max 30 words) capturing the core message — the "so what".\n'
        f'- "why_it_matters": array of 2-3 short bullets framed for an innovation/transformation leader '
        f"(strategic signal, what to brief leadership on, implication for how the org should think or act).\n"
        f'- "deep_dive": array of 4-6 substantive bullets covering the key content, examples, frameworks, '
        f"or specifics from the video.\n\n"
        f"Each bullet should be a complete sentence, no leading dashes. "
        f"Return ONLY the JSON object, no preamble or markdown fences.\n\n"
        f"Transcript:\n{transcript[:60000]}"
    )
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    return _parse_summary_json(raw)


def _parse_summary_json(raw: str) -> dict:
    """Extract a JSON object from the model's response, tolerating stray fences."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"tldr": raw, "why_it_matters": [], "deep_dive": []}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"tldr": raw, "why_it_matters": [], "deep_dive": []}
    return {
        "tldr": data.get("tldr", "").strip(),
        "why_it_matters": [b.strip() for b in data.get("why_it_matters", []) if b.strip()],
        "deep_dive": [b.strip() for b in data.get("deep_dive", []) if b.strip()],
    }


def _format_published(published_at: str) -> str:
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return published_at


def _render_card(s: dict) -> str:
    title = html.escape(s["title"])
    channel = html.escape(s["channel_title"])
    when = html.escape(_format_published(s["published_at"]))
    url = html.escape(s["url"])
    summary = s["summary"]

    if isinstance(summary, str):
        body_html = f'<p style="margin:0;color:#555;">{html.escape(summary)}</p>'
    else:
        tldr = html.escape(summary.get("tldr", ""))
        why = "".join(f"<li>{html.escape(b)}</li>" for b in summary.get("why_it_matters", []))
        deep = "".join(f"<li>{html.escape(b)}</li>" for b in summary.get("deep_dive", []))

        body_html = ""
        if tldr:
            body_html += (
                f'<p style="margin:0 0 20px 0;padding:14px 18px;background:#f5f7fb;'
                f'border-left:4px solid #2754C5;border-radius:4px;font-size:16px;'
                f'line-height:1.5;color:#1a1a1a;"><strong style="color:#2754C5;">TL;DR — </strong>{tldr}</p>'
            )
        if why:
            body_html += (
                f'<p style="margin:18px 0 8px 0;font-size:11px;letter-spacing:1.2px;'
                f'text-transform:uppercase;color:#2754C5;font-weight:700;">Why it matters</p>'
                f'<ul style="margin:0 0 16px 0;padding-left:20px;color:#1a1a1a;line-height:1.55;">{why}</ul>'
            )
        if deep:
            body_html += (
                f'<p style="margin:18px 0 8px 0;font-size:11px;letter-spacing:1.2px;'
                f'text-transform:uppercase;color:#666;font-weight:700;">Deep dive</p>'
                f'<ul style="margin:0;padding-left:20px;color:#333;line-height:1.55;">{deep}</ul>'
            )

    return f"""
<div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
  <p style="margin:0 0 6px 0;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">
    {channel} &middot; {when}
  </p>
  <h2 style="margin:0 0 14px 0;font-size:20px;line-height:1.3;color:#111827;">{title}</h2>
  <a href="{url}" style="display:inline-block;padding:8px 16px;background:#FF0000;color:#ffffff;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;margin-bottom:18px;">
    &#9654;&nbsp; Watch on YouTube
  </a>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:0 0 18px 0;">
  {body_html}
</div>
"""


def build_email_html(summaries: list[dict]) -> str:
    cards = "".join(_render_card(s) for s in summaries)
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:680px;margin:0 auto;padding:32px 20px;">
    <div style="margin-bottom:24px;">
      <h1 style="margin:0;font-size:24px;color:#111827;">Daily YouTube digest</h1>
      <p style="margin:4px 0 0 0;color:#6b7280;font-size:14px;">{today} &middot; {len(summaries)} new video(s)</p>
    </div>
    {cards}
    <p style="text-align:center;color:#9ca3af;font-size:12px;margin-top:24px;">
      Auto-generated summary &middot; click any title to watch the full video
    </p>
  </div>
</body></html>"""


def build_email_text(summaries: list[dict]) -> str:
    """Plain-text fallback for email clients that block HTML."""
    parts = [f"Daily YouTube digest — {len(summaries)} new video(s)\n"]
    for s in summaries:
        summary = s["summary"]
        parts.append(f"\n{'=' * 70}\n[{s['channel_title']}] {s['title']}\n{s['url']}\n")
        if isinstance(summary, dict):
            if summary.get("tldr"):
                parts.append(f"\nTL;DR: {summary['tldr']}\n")
            if summary.get("why_it_matters"):
                parts.append("\nWHY IT MATTERS\n" + "\n".join(f"- {b}" for b in summary["why_it_matters"]))
            if summary.get("deep_dive"):
                parts.append("\n\nDEEP DIVE\n" + "\n".join(f"- {b}" for b in summary["deep_dive"]))
        else:
            parts.append(f"\n{summary}")
        parts.append("\n")
    return "\n".join(parts)


def send_email(subject: str, html_body: str, text_body: str, recipients: list[str]) -> None:
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
        server.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())


def build_subject(summaries: list[dict]) -> str:
    n = len(summaries)
    first_title = summaries[0]["title"]
    teaser = first_title if len(first_title) <= 60 else first_title[:57] + "..."
    if n == 1:
        return f"YT digest: {teaser}"
    return f"YT digest ({n}): {teaser} +{n - 1} more"


def main() -> None:
    processed = load_processed_ids()
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    claude = Anthropic(api_key=ANTHROPIC_API_KEY)

    all_new_videos = []
    for channel_id in YOUTUBE_CHANNEL_IDS:
        print(f"Checking channel {channel_id} for videos since {since.isoformat()}")
        videos = fetch_recent_videos(youtube, channel_id, since)
        new_videos = [v for v in videos if v["video_id"] not in processed]
        print(f"  found {len(videos)} recent, {len(new_videos)} new.")
        all_new_videos.extend(new_videos)

    if not all_new_videos:
        print("Nothing new to summarize. Done.")
        return

    summaries = []
    for video in all_new_videos:
        print(f"- [{video['channel_title']}] {video['title']}")
        transcript = fetch_transcript(video["video_id"])
        if not transcript:
            summary = {"tldr": "No transcript available for this video.", "why_it_matters": [], "deep_dive": []}
        else:
            summary = summarize(claude, video["title"], transcript)
        summaries.append({**video, "summary": summary})
        processed.add(video["video_id"])

    html_body = build_email_html(summaries)
    text_body = build_email_text(summaries)
    subject = build_subject(summaries)
    send_email(subject, html_body, text_body, EMAIL_TO)
    save_processed_ids(processed)
    print(f"Emailed digest to {', '.join(EMAIL_TO)}.")


if __name__ == "__main__":
    main()
