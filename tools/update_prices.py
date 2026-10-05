#!/usr/bin/env python3
"""Update current card prices and append an online daily price history.

Sources are public retail/marketplace pages. Mercari is best-effort because its
search pages can change or block automated requests. No login, bot protection,
or access control is bypassed.
"""
import json, re, time, statistics, subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/catalog.json'
OUT = ROOT / 'data/prices.json'
HISTORY = ROOT / 'data/price-history.json'
UA = 'Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/2.0)'

SOURCES = [
    {'name':'FullAhead', 'base':'https://pt-fullahead.com/shopbrand/kynbs/', 'pages':[1,2,3]},
    {'name':'TCG Library', 'base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash', 'pages':[1,2,3]},
    {'name':'TCG Library A01', 'base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01', 'pages':[1,2]},
    {'name':'カードショップ カリントウ A01', 'base':'https://item.rakuten.co.jp/karintou10/c/0000006149/', 'pages':[1]},
    {'name':'カードショップ カリントウ A02', 'base':'https://item.rakuten.co.jp/karintou10/c/0000006150/', 'pages':[1,2]},
    {'name':'FullAhead PR', 'base':'https://item.rakuten.co.jp/fullahead/c/0000012539/', 'pages':[1]},
]


def get(url):
    req = Request(url, headers={'User-Agent': UA, 'Accept-Language':'ja,en;q=0.8'})
    with urlopen(req, timeout=25) as r:
        return r.read().decode('utf-8', 'ignore')


def strip_html(html):
    return re.sub(r'<[^>]+>', ' ', html)


def parse_cards(html):
    text = re.sub(r'\s+', ' ', strip_html(html))
    out = {}
    # A/P/T cards followed reasonably closely by a yen price.
    pat = re.compile(r'((?:A|P|T)\d{2}-\d{3})(?:(?!((?:A|P|T)\d{2}-\d{3})).){0,260}?(?:¥\s*)?([0-9][0-9,]*)\s*円')
    for m in pat.finditer(text):
        cid = m.group(1)
        price = int(m.group(3).replace(',', ''))
        if price > 0:
            out.setdefault(cid, price)
    return out


def shop_url(source, cid):
    if source == 'FullAhead': return 'https://pt-fullahead.com/shopbrand/kynbs/'
    if source == 'FullAhead PR': return 'https://item.rakuten.co.jp/fullahead/c/0000012539/'
    if source == 'TCG Library A01': return 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01'
    if source == 'TCG Library': return 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash'
    if source.endswith('A01'): return 'https://item.rakuten.co.jp/karintou10/c/0000006149/'
    return 'https://item.rakuten.co.jp/karintou10/c/0000006150/'


def mercari_url(card):
    q = ' '.join(x for x in [card.get('id'), card.get('name'), card.get('rarity')] if x)
    return 'https://jp.mercari.com/search?keyword=' + quote(q)


def parse_mercari_prices(html, cid):
    """Best-effort extraction of yen amounts near the card ID.

    Mercari changes its HTML frequently. We deliberately keep this conservative:
    if we cannot identify enough plausible prices, the marketplace contributes
    no numeric value rather than polluting the estimate.
    """
    text = re.sub(r'\s+', ' ', strip_html(html))
    if cid not in text:
        return []
    # Search windows around occurrences of the exact card ID and collect nearby yen prices.
    vals = []
    for m in re.finditer(re.escape(cid), text):
        window = text[max(0, m.start()-250):m.end()+500]
        for pm in re.finditer(r'¥\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*円', window):
            raw = pm.group(1) or pm.group(2)
            try:
                v = int(raw.replace(',', ''))
            except ValueError:
                continue
            if 30 <= v <= 300000:
                vals.append(v)
    # De-duplicate while preserving order and cap the sample.
    seen = set(); clean = []
    for v in vals:
        if v not in seen:
            seen.add(v); clean.append(v)
    return clean[:10]


def median_or_none(values):
    values = [int(v) for v in values if isinstance(v, (int,float)) and v > 0]
    return int(round(statistics.median(values))) if values else None


def build_history_from_git(history):
    """Recover previous prices.json snapshots already committed to this repo."""
    try:
        log = subprocess.check_output(
            ['git','log','--format=%H %cs','--','data/prices.json'],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).splitlines()
    except Exception:
        return history
    # Keep the latest commit per calendar day.
    by_day = {}
    for line in log:
        parts = line.split(' ', 1)
        if len(parts) == 2:
            by_day.setdefault(parts[1], parts[0])
    for day, commit in reversed(list(by_day.items())):
        try:
            raw = subprocess.check_output(['git','show',f'{commit}:data/prices.json'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
            old = json.loads(raw)
        except Exception:
            continue
        snap = history.setdefault('days', {}).setdefault(day, {})
        for cid, p in old.get('cards', {}).items():
            offers = p.get('offers') or []
            nums = [int(x.get('price')) for x in offers if str(x.get('price','')).isdigit() and int(x.get('price')) > 0]
            avg = p.get('average') if isinstance(p.get('average'), (int,float)) else (int(round(sum(nums)/len(nums))) if nums else None)
            market = p.get('market') if isinstance(p.get('market'), (int,float)) else (int(round(statistics.median(nums))) if nums else None)
            cheapest = p.get('cheapest') if isinstance(p.get('cheapest'), (int,float)) else (min(nums) if nums else None)
            if market is not None or avg is not None or cheapest is not None:
                snap[cid] = {'market': market, 'average': avg, 'cheapest': cheapest}
    return history


def main():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    cards = {c['id']: {'card': c, 'offers': []} for c in catalog['cards']}

    for src in SOURCES:
        for page in src['pages']:
            if src['name'].startswith('FullAhead'):
                url = src['base'] if page == 1 else f"{src['base']}page{page}/order/"
            elif 'rakuten.co.jp' in src['base']:
                url = src['base']
            else:
                url = src['base'] if page == 1 else f"{src['base']}?page={page}"
            try:
                prices = parse_cards(get(url))
            except Exception as e:
                print(f'WARN {src["name"]} page {page}: {e}')
                continue
            for cid, price in prices.items():
                if cid in cards:
                    cards[cid]['offers'].append({'shop':src['name'], 'price':price, 'url':shop_url(src['name'],cid)})
            time.sleep(0.5)

    # Mercari: best effort. Search links are always stored, numeric data only when safely extracted.
    for cid, entry in cards.items():
        try:
            html = get(mercari_url(entry['card']))
            samples = parse_mercari_prices(html, cid)
            if len(samples) >= 2:
                entry['offers'].append({
                    'shop':'メルカリ',
                    'price':median_or_none(samples),
                    'sampleCount':len(samples),
                    'samples':samples,
                    'url':mercari_url(entry['card'])
                })
        except Exception as e:
            print(f'INFO Mercari {cid}: unavailable ({e})')
        time.sleep(0.2)

    old = json.loads(OUT.read_text(encoding='utf-8')).get('cards', {}) if OUT.exists() else {}
    cards_out = {}
    for cid, entry in cards.items():
        offers = entry['offers']
        # One shop can appear more than once because a source has multiple pages; retain cheapest listing per shop.
        by_shop = {}
        for x in offers:
            shop = x['shop']
            if shop not in by_shop or int(x['price']) < int(by_shop[shop]['price']):
                by_shop[shop] = x
        offers = list(by_shop.values())
        nums = [int(x['price']) for x in offers if int(x.get('price',0)) > 0]
        if not offers and cid in old:
            cards_out[cid] = old[cid]
            continue
        p = old.get(cid, {}).copy()
        p['offers'] = offers
        p['price'] = min(nums) if nums else p.get('price')
        p['cheapest'] = min(nums) if nums else None
        p['average'] = int(round(sum(nums)/len(nums))) if nums else None
        p['market'] = median_or_none(nums)
        p['updated'] = time.strftime('%Y-%m-%d')
        p['shopLinks'] = [
            {'shop':'FullAhead','url':'https://pt-fullahead.com/shopbrand/kynbs/'},
            {'shop':'TCG Library','url':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash'},
            {'shop':'カードショップ カリントウ','url':'https://item.rakuten.co.jp/karintou10/c/0000006148/'},
            {'shop':'メルカリ','url':mercari_url(entry['card'])},
        ]
        p['sources'] = [{'name':x['shop'], 'url':x['url']} for x in offers]
        cards_out[cid] = p

    today = time.strftime('%Y-%m-%d')
    history = json.loads(HISTORY.read_text(encoding='utf-8')) if HISTORY.exists() else {'version':1,'days':{}}
    history = build_history_from_git(history)
    day = history.setdefault('days', {}).setdefault(today, {})
    for cid, p in cards_out.items():
        if p.get('market') is not None or p.get('average') is not None:
            day[cid] = {
                'market': p.get('market'),
                'average': p.get('average'),
                'cheapest': p.get('cheapest'),
                'sources': {x['shop']: x['price'] for x in p.get('offers', [])}
            }

    OUT.write_text(json.dumps({'version':2,'updated':today,'cards':cards_out},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    HISTORY.write_text(json.dumps(history,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    print('Updated', OUT, 'and', HISTORY)


if __name__ == '__main__':
    main()
