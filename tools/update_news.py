#!/usr/bin/env python3
"""Fetch the official Nichirin Battle Slash news index into data/news.json."""
import json, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/news.json'
URL = 'https://p.eagate.573.jp/game/kimetsu/bslash/news/index.html'
UA = 'Mozilla/5.0 (compatible; NichirinCollectionNewsUpdater/1.0)'


def main():
    req = Request(URL, headers={'User-Agent': UA, 'Accept-Language':'ja,en;q=0.8'})
    with urlopen(req, timeout=25) as r:
        html = r.read().decode('utf-8','ignore')
    items = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I|re.S):
        href, raw = m.group(1), m.group(2)
        title = re.sub(r'<[^>]+>', ' ', raw)
        title = re.sub(r'\s+', ' ', title).strip()
        if not title or 'news/' not in href or href.endswith('index.html'):
            continue
        full = urljoin(URL, href)
        dm = re.search(r'/news/(\d{4})/(\d{4})\.html', full)
        date = f'{dm.group(1)}-{dm.group(2)[:2]}-{dm.group(2)[2:]}' if dm else ''
        key = (full, title)
        if not any((x['url'], x['title']) == key for x in items):
            items.append({'date':date,'title':title,'url':full})
    items.sort(key=lambda x: (x['date'], x['title']), reverse=True)
    old = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    if items:
        OUT.write_text(json.dumps({'updated':time.strftime('%Y-%m-%d'),'source':URL,'items':items[:30]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    elif old:
        print('No news items parsed; keeping existing data/news.json')
    else:
        OUT.write_text(json.dumps({'updated':time.strftime('%Y-%m-%d'),'source':URL,'items':[]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__': main()
