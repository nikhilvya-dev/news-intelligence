#!/usr/bin/env python3
"""Collect RSS/Atom entries and publish auditable, date-scoped reports."""
from __future__ import annotations

import calendar
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser

ROOT = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
USER_AGENT = "news-intelligence-rss-collector/1.0 (+https://github.com/nikhilvya-dev/news-intelligence)"
TIMEOUT_SECONDS = 25
MAX_BYTES = 8 * 1024 * 1024
TOPICS = {
    "ai-research": (1, 25, "AI research"),
    "cloud-industry": (26, 45, "Cloud and industry"),
    "automation": (46, 68, "Automation and infrastructure"),
    "product-strategy": (69, 80, "Business and product strategy"),
}
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(x) for x in value)
    text = html.unescape(TAG_RE.sub(" ", str(value)))
    return SPACE_RE.sub(" ", text).strip()


def entry_timestamp(entry: dict) -> tuple[datetime | None, str | None]:
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                stamp = calendar.timegm(parsed)
                return datetime.fromtimestamp(stamp, UTC), field.removesuffix("_parsed")
            except (OverflowError, OSError, TypeError, ValueError):
                pass
    for field in ("published", "updated", "created", "date"):
        value = entry.get(field)
        if not value:
            continue
        try:
            parsed = email.utils.parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC), field
        except (TypeError, ValueError, OverflowError):
            continue
    return None, None


def canonical_url(raw: str) -> str:
    if not raw:
        return ""
    try:
        p = urllib.parse.urlsplit(raw.strip())
        host = (p.hostname or "").lower()
        if not host:
            return raw.strip()
        port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
        path = re.sub(r"/+$", "", p.path) or "/"
        query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
        return urllib.parse.urlunsplit((p.scheme.lower() or "https", host + port, path, urllib.parse.urlencode(query), ""))
    except (ValueError, UnicodeError):
        return raw.strip()


def get_feed(url: str) -> tuple[bytes, int | None]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError(f"Feed exceeded {MAX_BYTES} byte size limit")
        return body, getattr(response, "status", 200)


def classify_feed(url: str, index: int) -> tuple[str, str]:
    for topic, (first, last, label) in TOPICS.items():
        if first <= index <= last:
            return topic, label
    return "other", "Other"


def parse_feed(url: str, index: int, start: datetime, now: datetime) -> tuple[dict, list[dict]]:
    topic, topic_label = classify_feed(url, index)
    checked_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    status = {
        "index": index, "url": url, "topic": topic, "topic_label": topic_label,
        "checked_at": checked_at, "http_status": None, "feed_title": "",
        "status": "failed", "error": None, "warning": None,
        "entries_seen": 0, "items_in_window": 0, "items_undated": 0,
    }
    try:
        payload, http_status = get_feed(url)
        status["http_status"] = http_status
        parsed = feedparser.parse(payload)
        feed = parsed.get("feed", {})
        status["feed_title"] = clean_text(feed.get("title")) or url
        entries = parsed.get("entries", [])
        status["entries_seen"] = len(entries)
        if parsed.get("bozo"):
            status["warning"] = clean_text(str(parsed.get("bozo_exception", "Feed has parse warnings")))[:500]
        articles = []
        for entry in entries:
            stamp, date_source = entry_timestamp(entry)
            if stamp and not (start <= stamp <= now):
                continue
            link = canonical_url(str(entry.get("link", "")))
            title = clean_text(entry.get("title")) or "(untitled entry)"
            # RSS summary/description and Atom content commonly carry the arXiv abstract.
            description = clean_text(entry.get("summary") or entry.get("description"))
            if not description and entry.get("content"):
                description = clean_text(" ".join(str(part.get("value", "")) for part in entry.get("content", [])))
            if not description:
                description = clean_text(entry.get("subtitle"))
            article = {
                "title": title, "description": description, "abstract": description if "arxiv.org" in url.lower() else "",
                "timestamp": stamp.isoformat(timespec="seconds").replace("+00:00", "Z") if stamp else None,
                "date_status": "dated" if stamp else "undated",
                "date_source": date_source, "url": link or url, "source": status["feed_title"],
                "feed_url": url, "feed_index": index, "topic": topic,
            }
            articles.append(article)
            if stamp is None:
                status["items_undated"] += 1
            else:
                status["items_in_window"] += 1
        status["status"] = "success_with_warnings" if status["warning"] else "success"
        return status, articles
    except urllib.error.HTTPError as exc:
        status["http_status"] = exc.code
        status["error"] = f"HTTP {exc.code}: {exc.reason}"[:500]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"[:500]
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return status, []


def article_key(article: dict) -> str:
    link = article.get("url", "")
    if link:
        return canonical_url(link).lower()
    return (clean_text(article.get("title")).lower(), article.get("timestamp"), article.get("source"))


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def article_markdown(article: dict) -> str:
    title = article["title"].replace("\n", " ")
    url = article["url"]
    date = article["timestamp"] or "Undated (feed supplied no usable timestamp)"
    desc = article["description"] or "No description supplied."
    return f"### [{title}]({url})\n\n- **Source:** {article['source']}\n- **Published:** {date}\n- **Feed:** {article['feed_url']}\n\n{desc}\n"


def make_topic_page(topic: str, label: str, date_label: str, items: list[dict]) -> str:
    dated = sum(1 for a in items if a["timestamp"])
    pieces = [f"# {label} — {date_label}", "", f"**{len(items)} items** ({dated} dated, {len(items)-dated} undated). Undated items are included because the feed supplied no usable publication timestamp.", ""]
    pieces.extend(article_markdown(a) for a in items)
    return "\n".join(pieces).strip() + "\n"


def main() -> None:
    now = datetime.now(UTC)
    local_today = now.astimezone(ET).date()
    start_local = datetime.combine(local_today - timedelta(days=1), datetime.min.time(), tzinfo=ET)
    start = start_local.astimezone(UTC)
    end_label = now.astimezone(ET).date().isoformat()
    date_key = start_local.date().isoformat()
    feeds_path = ROOT / "feeds.txt"
    feeds = [line.strip() for line in feeds_path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    all_articles: list[dict] = []
    coverage: list[dict] = []
    for index, url in enumerate(feeds, start=1):
        result, articles = parse_feed(url, index, start, now)
        coverage.append(result)
        all_articles.extend(articles)
        print(f"[{index:02d}/{len(feeds)}] {result['status']}: {url}", flush=True)

    unique = {}
    for article in all_articles:
        key = article_key(article)
        if key not in unique:
            unique[key] = article
    articles = list(unique.values())
    articles.sort(key=lambda a: (a["timestamp"] or "", a["title"].lower()), reverse=True)
    for article in articles:
        if article["timestamp"]:
            try:
                dt = datetime.fromisoformat(article["timestamp"].replace("Z", "+00:00"))
                article["display_time_et"] = dt.astimezone(ET).strftime("%Y-%m-%d %I:%M %p %Z")
            except ValueError:
                article["display_time_et"] = article["timestamp"]
        else:
            article["display_time_et"] = "Undated"

    OUT.mkdir(parents=True, exist_ok=True)
    report_dir = OUT / "reports" / date_key
    topic_counts = {}
    for topic, (_, _, label) in TOPICS.items():
        items = [a for a in articles if a["topic"] == topic]
        topic_counts[topic] = len(items)
        write_text(report_dir / f"{topic}.md", make_topic_page(topic, label, date_key, items))

    ok = sum(1 for f in coverage if f["status"].startswith("success"))
    failed = len(coverage) - ok
    undated = sum(f["items_undated"] for f in coverage)
    summary = {
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "window_start": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "window_start_local": start_local.isoformat(timespec="seconds"),
        "window_end": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timezone": "America/New_York", "feeds_total": len(feeds),
        "feeds_succeeded": ok, "feeds_failed": failed,
        "entries_after_deduplication": len(articles), "undated_items": sum(1 for a in articles if not a["timestamp"]),
        "topic_counts": topic_counts, "coverage": coverage,
    }
    write_text(OUT / "coverage.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    write_text(report_dir / "articles.json", json.dumps(articles, ensure_ascii=False, indent=2) + "\n")

    status_rows = ["| # | Feed | Topic | Status | HTTP | Entries seen | In window | Undated | Error / warning |",
                   "|---:|---|---|---|---:|---:|---:|---:|---|"]
    for f in coverage:
        issue = f["error"] or f["warning"] or ""
        status_rows.append(f"| {f['index']} | [{esc(f['feed_title'] or f['url'])}]({f['url']}) | {f['topic_label']} | {f['status']} | {f['http_status'] or ''} | {f['entries_seen']} | {f['items_in_window']} | {f['items_undated']} | {issue.replace('|', '/') } |")
    links = "\n".join(f"- [{TOPICS[t][2]} ({topic_counts[t]})](reports/{date_key}/{t}.md)" for t in TOPICS)
    coverage_md = f"""# Feed coverage — {date_key}

Generated: {summary['generated_at']}  
Collection window starts: {summary['window_start_local']} (America/New_York)  
Feeds attempted: {len(feeds)} · succeeded: {ok} · failed: {failed}  
Unique collected entries: {len(articles)} · undated: {summary['undated_items']}

## Topic pages

{links}

## Per-feed audit

{chr(10).join(status_rows)}
"""
    write_text(OUT / "coverage.md", coverage_md)
    index_md = f"""# News intelligence — {date_key}

Generated: {summary['generated_at']}  
Collection window: since {summary['window_start_local']} (America/New_York)

{links}

- [Auditable feed coverage](coverage.md)
- [Coverage data (JSON)](coverage.json)
- [All unique articles (JSON)](reports/{date_key}/articles.json)
"""
    write_text(OUT / "index.md", index_md)
    # Small HTML landing page makes navigation clear in a browser; Markdown/JSON remain easy for retrieval.
    topic_links = "".join(f'<li><a href="reports/{date_key}/{t}.md">{esc(TOPICS[t][2])}</a> ({topic_counts[t]} items)</li>' for t in TOPICS)
    write_text(OUT / "index.html", f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>News intelligence — {date_key}</title><main><h1>News intelligence — {date_key}</h1><p>Generated {esc(summary['generated_at'])}. Window starts {esc(summary['window_start_local'])} (America/New_York).</p><ul>{topic_links}</ul><p><a href="coverage.md">Feed coverage audit</a> · <a href="coverage.json">Coverage JSON</a> · <a href="reports/{date_key}/articles.json">Articles JSON</a></p><p>{len(feeds)} feeds attempted; {ok} succeeded and {failed} failed; {len(articles)} unique entries ({undated} undated).</p></main></html>""")
    write_text(OUT / ".nojekyll", "")
    print(f"Finished: {len(feeds)} feeds; {ok} succeeded; {failed} failed; {len(articles)} unique items.", flush=True)


if __name__ == "__main__":
    main()
