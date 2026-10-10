#!/usr/bin/env python3
"""Fetch official Nichirin Battle Slash news into data/news.json."""

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news.json"

URL = "https://p.eagate.573.jp/game/kimetsu/bslash/news/index.html"
UA = "Mozilla/5.0 (compatible; NichirinCollectionNewsUpdater/1.0)"

DATE_RE = re.compile(r"(20\d{2})[./年](\d{2})[./月](\d{2})")

class NewsParser(HTMLParser):
    """Collect links to individual official news articles."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current_href = None
        self.current_text = []
    
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.current_href = dict(attrs).get("href")
            self.current_text = []
    
    def handle_data(self, data):
        if self.current_href is not None:
            self.current_text.append(data)
    
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current_href is not None:
            self.links.append(
                (self.current_href, " ".join(self.current_text))
            )
            self.current_href = None
            self.current_text = []

def fetch():
    request = Request(
    URL,
    headers={
    "User-Agent": UA,
    "Accept-Language": "ja,en;q=0.8",
    },
    )
with urlopen(request, timeout=30) as response:
    return response.read().decode("utf-8", "replace")

def main():
    html = fetch()
    parser = NewsParser()
    parser.feed(html)

items = []
seen = set()

for href, raw_title in parser.links:
    article_url = urljoin(URL, href)

    if not re.search(
        r"/news/\d{4}/[^/?#]+\.html(?:[?#].*)?$",
        article_url,
        re.I,
    ):
        continue

    match = DATE_RE.search(raw_title)
    if not match:
        continue

    year, month, day = match.groups()
    date = f"{year}.{month}.{day}"

    try:
        time.strptime(date, "%Y.%m.%d")
    except ValueError:
        continue

    title = re.sub(DATE_RE, "", raw_title, count=1)
    title = re.sub(r"\s+", " ", title).strip()

    if not title:
        continue

    item = {
        "date": date,
        "title": title,
        "url": article_url,
    }

    key = (date, article_url)
    if key not in seen:
        seen.add(key)
        items.append(item)

items.sort(key=lambda item: item["date"], reverse=True)

# Fail safely instead of replacing valid data with an empty list.
if not items:
    raise RuntimeError(
        "No news articles were extracted. "
        "The official page structure may have changed. "
        "Existing news.json was not modified."
    )

newest = items[0]
print(f"News extracted: {len(items)}")
print(f"Newest article: {newest['date']} - {newest['title']}")
print(f"Article URL: {newest['url']}")

data = {
    "updated": time.strftime("%Y-%m-%d", time.gmtime()),
    "source": URL,
    "items": items[:20],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print(f"Saved {min(len(items), 20)} articles to {OUT}")

if __name__ == "__main__":
    main()
