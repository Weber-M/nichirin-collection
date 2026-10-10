#!/usr/bin/env python3
"""Daily price updater for Nichirin Battle Slash V6.

Price policy
------------
* Uses multiple public shop sources when they are reachable.
* A source that cannot be fetched is NOT treated as ¥0.
* The current market estimate is the median of the distinct source prices
  successfully fetched for that card on this run.
* Previous offers are retained as last-known values when a source temporarily
  fails, but today's history point uses ONLY prices fetched successfully today.
* One history snapshot is stored per calendar day. Existing history is never
  truncated, so the project can accumulate a multi-year price history.
* Mercari gets a direct search link, but this updater does not scrape Mercari.
  Mercari's current platform terms prohibit automated scraping/crawling, so an
  unavailable Mercari value remains unavailable rather than becoming ¥0.
"""
import json
import re
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/catalog.json'
OUT = ROOT / 'data/prices.json'
TODAY = time.strftime('%Y-%m-%d')
UA = 'Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/3.0)'

# These are public category/collection pages. More than one source is used so
# one shop does not determine the whole market estimate.
SOURCES = [
    {
        'name': 'FullAhead',
        'base': 'https://pt-fullahead.com/shopbrand/kynbs/',
        'pages': range(1, 6),
        'kind': 'fullahead',
    },
    {
        'name': 'FullAhead Rakuten',
        'base': 'https://item.rakuten.co.jp/fullahead/c/0000012537/',
        'pages': range(1, 3),
        'kind': 'rakuten',
    },
    {
        'name': 'TCG Library A01',
        'base': 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01',
        'pages': range(1, 3),
        'kind': 'tcg',
    },
    {
        'name': 'TCG Library all',
        'base': 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash',
        'pages': range(1, 4),
        'kind': 'tcg',
    },
    {
        'name': 'カードショップ カリントウ A01',
        'base': 'https://item.rakuten.co.jp/karintou10/c/0000006149/',
        'pages': range(1, 2),
        'kind': 'rakuten',
    },
    {
        'name': 'カードショップ カリントウ A02',
        'base': 'https://item.rakuten.co.jp/karintou10/c/0000006150/',
        'pages': range(1, 2),
        'kind': 'rakuten',
    },
]

SHOP_LINKS = {
    'FullAhead': 'https://pt-fullahead.com/shopbrand/kynbs/',
    'FullAhead Rakuten': 'https://item.rakuten.co.jp/fullahead/c/0000012537/',
    'TCG Library': 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash',
    'カードショップ カリントウ': 'https://item.rakuten.co.jp/karintou10/c/0000006149/',
}


def get(url):
    req = Request(url, headers={
        'User-Agent': UA,
        'Accept-Language': 'ja,en;q=0.8',
    })
    with urlopen(req, timeout=25) as r:
        return r.read().decode('utf-8', 'ignore')


def clean(html):
    html = re.sub(r'<script[\s\S]*?</script>', ' ', html, flags=re.I)
    html = re.sub(r'<style[\s\S]*?</style>', ' ', html, flags=re.I)
    html = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', html)


def parse_shop(html):
    """Extract card-id -> price from common Japanese shop/Rakuten text."""
    text = clean(html)
    out = {}
    # Card IDs used by the project: Axx/Pxx/Txx-xxx.
    pat = re.compile(
        r'((?:A|P|T)\d{2}-\d{3})'
        r'.{0,320}?'
        r'(?:¥\s*)?([0-9][0-9,]*)\s*円',
        flags=re.S,
    )
    for m in pat.finditer(text):
        cid = m.group(1)
        price = int(m.group(2).replace(',', ''))
        if 1 <= price <= 1_000_000:
            # Keep the first matching price for a card on a source page.
            out.setdefault(cid, price)
    return out


def source_url(src, cid):
    return src['base']


def mercari_url(card):
    q = ' '.join(
        x for x in [
            card['id'],
            card.get('name', ''),
            str(card.get('rarity', '')).replace('*', ''),
        ] if x
    )
    return 'https://jp.mercari.com/search?' + urlencode({'keyword': q})


def median(values):
    values = sorted(set(int(v) for v in values if isinstance(v, (int, float)) and v > 0))
    if not values:
        return None
    return int(round(statistics.median(values)))


def distinct_offers(offers):
    """One value per shop, preferring the newest/fetched value."""
    by_shop = {}
    for offer in offers:
        shop = offer.get('shop')
        price = offer.get('price')
        if shop and isinstance(price, (int, float)) and price > 0:
            by_shop[shop] = offer
    return list(by_shop.values())


def make_history_point(values):
    """Build one honest daily snapshot from today's successfully fetched values."""
    vals = [int(v) for v in values if isinstance(v, (int, float)) and v > 0]
    if not vals:
        return None
    return {
        'date': TODAY,
        'market': median(vals),
        'average': int(round(sum(vals) / len(vals))),
        'cheapest': min(vals),
    }


def main():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    old = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {'cards': {}}
    oldcards = old.get('cards', {})

    cards = {
        c['id']: dict(oldcards.get(c['id'], {}))
        for c in catalog['cards']
    }
    card_by_id = {c['id']: c for c in catalog['cards']}

    # Values fetched successfully during THIS run, by card and source.
    fetched = {cid: {} for cid in cards}
    successful_sources = set()

    for src in SOURCES:
        for page in src['pages']:
            if src['kind'] == 'fullahead':
                url = src['base'] if page == 1 else f"{src['base']}page{page}/order/"
            elif src['kind'] == 'tcg':
                url = src['base'] if page == 1 else f"{src['base']}?page={page}"
            else:
                # Rakuten category pages use ?p=2 etc. Some categories expose
                # pagination differently; page 1 is always the base URL.
                url = src['base'] if page == 1 else f"{src['base']}?p={page}"

            try:
                # prices = parse_shop(get(url))
                # print(
                #     f"PRICE DEBUG | {src['name']} | page {page} "
                #     f"| extracted {len(prices)} card prices"
                # )
                # successful_sources.add(src['name'])
                html = get(url)
                prices = parse_shop(html)
                
                if not prices:
                    text = clean(html)
                    card_id_count = len(
                        re.findall(r"\b(?:A|P|T)\d{2}-\d{3}\b", text)
                    )
                    yen_count = len(
                        re.findall(r"(?:¥|￥)\s*[0-9,]+|[0-9,]+\s*円", text)
                    )
                    page_title = re.search(
                        r"<title[^>]*>(.*?)</title>",
                        html,
                        re.I | re.S,
                    )
                    title = (
                        re.sub(r"\s+", " ", page_title.group(1)).strip()
                        if page_title else "(no title)"
                    )
                    print(
                        f"FETCH DEBUG | {src['name']} | page {page} "
                        f"| chars={len(html)} | card_ids={card_id_count} "
                        f"| price_tokens={yen_count} | title={title[:120]}"
                    )
                
                print(
                    f"PRICE DEBUG | {src['name']} | page {page} "
                    f"| extracted {len(prices)} card prices"
                )
                
                if prices:
                    successful_sources.add(src["name"])
            except Exception as e:
                print('WARN', src['name'], page, e)
                continue

            for cid, price in prices.items():
                if cid in cards:
                    fetched[cid][src['name']] = {
                        'shop': src['name'],
                        'price': price,
                        'url': source_url(src, cid),
                        'updated': TODAY,
                    }
            time.sleep(0.5)

    for cid, card in card_by_id.items():
        prev = cards[cid]
        today_offers = list(fetched[cid].values())

        # Keep the last known value for a source if that source was temporarily
        # unreachable today. Mark it as stale so it cannot silently look fresh.
        previous_offers = {
            o.get('shop'): o for o in prev.get('offers', [])
            if isinstance(o, dict) and o.get('shop') and o.get('price')
        }
        combined = []
        seen = set()
        for offer in today_offers:
            combined.append(offer)
            seen.add(offer['shop'])
        for shop, offer in previous_offers.items():
            if shop not in seen:
                stale = dict(offer)
                stale['stale'] = True
                combined.append(stale)

        combined = distinct_offers(combined)
        prev['offers'] = sorted(combined, key=lambda x: (str(x.get('shop')), float(x.get('price', 0))))

        # Current displayed values use all known positive offers, while the
        # history point below uses only today's fresh values.
        known_values = [o['price'] for o in combined if o.get('price', 0) > 0]
        fresh_values = [o['price'] for o in today_offers if o.get('price', 0) > 0]

        if known_values:
            prev['price'] = min(known_values)
            prev['marketValue'] = median(known_values)
            prev['average'] = int(round(sum(known_values) / len(known_values)))
            prev['updated'] = TODAY if fresh_values else prev.get('updated', TODAY)
        else:
            prev.setdefault('price', None)
            prev.setdefault('marketValue', None)
            prev.setdefault('average', None)

        prev['shopLinks'] = [{'shop': k, 'url': v} for k, v in SHOP_LINKS.items()]
        prev['shopLinks'].append({'shop': 'メルカリ', 'url': mercari_url(card)})

        # Explicitly represent Mercari as unavailable rather than zero.
        # We intentionally do not scrape Mercari automatically.
        prev['mercari'] = {
            'status': 'manual-search',
            'median': None,
            'samples': [],
            'url': mercari_url(card),
            'updated': TODAY,
        }

        # Preserve every existing history point. Replace today's point on a
        # rerun, but NEVER truncate old dates.
        hist = prev.get('history') if isinstance(prev.get('history'), list) else []
        hist = [x for x in hist if isinstance(x, dict) and x.get('date') != TODAY]
        point = make_history_point(fresh_values)
        if point is not None:
            hist.append(point)
        hist.sort(key=lambda x: str(x.get('date', '')))
        prev['history'] = hist
        cards[cid] = prev

    out = {
        'updated': TODAY,
        'cards': cards,
        'version': 6,
        'v6': 2,
        'historyPolicy': 'One dated snapshot per successful daily updater run; all historical dates are retained permanently. Missing days remain missing; no historical values are invented.',
        'pricePolicy': 'Market value is the median of distinct positive source prices. Unavailable sources are not treated as ¥0. Historical daily snapshots use only prices successfully fetched on that date.',
        'sourcesFetchedToday': sorted(successful_sources),
        'mercariNote': 'Mercari is kept as a direct search/reference link. Automated scraping is disabled; an unavailable Mercari value is never treated as ¥0.',
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Updated', OUT, 'cards=', len(cards), 'sources=', ', '.join(sorted(successful_sources)))


if __name__ == '__main__':
    main()
