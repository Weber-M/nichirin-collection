#!/usr/bin/env python3
"""Update price comparison data for the Nichirin Battle Slash PWA.

Public sources only. No login, bot-protection bypass, or access-control bypass.
Mercari is treated as one source: matching individual-card listings are
collected and the median is stored as the representative Mercari price.
The app then calculates the cross-source average from the available sources.
"""
import json, re, statistics, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote_plus

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'data/catalog.json'
OUT=ROOT/'data/prices.json'
UA='Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/2.0)'

SHOP_SOURCES=[
 {'name':'FullAhead','base':'https://pt-fullahead.com/shopbrand/kynbs/','pages':[1,2,3,4,5],
  'url':'https://pt-fullahead.com/shopbrand/kynbs/'},
 {'name':'TCG Library A01','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01','pages':[1,2],
  'url':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01'},
 {'name':'TCG Library','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash','pages':[1,2,3],
  'url':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash'},
]
MERCARI_BASE='https://jp.mercari.com/search?keyword='
MERCARI_LABEL='メルカリ'
MERCARI_PAGE='https://jp.mercari.com/search'


def get(url):
    req=Request(url,headers={'User-Agent':UA,'Accept-Language':'ja,en;q=0.8'})
    with urlopen(req,timeout=25) as r:
        return r.read().decode('utf-8','ignore')


def clean_html(html):
    return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html))


def parse_shop_cards(html):
    text=clean_html(html)
    out={}
    pat=re.compile(r'((?:A|P|T)\d{2}-\d{3})[^\d¥円]{0,220}?(?:¥\s*)?([0-9][0-9,]*)\s*円')
    for m in pat.finditer(text):
        cid=m.group(1); price=int(m.group(2).replace(',',''))
        if 10 <= price <= 500000:
            out.setdefault(cid,[]).append(price)
    return out


def mercari_prices(html,cid):
    """Extract candidate prices near the exact card ID from Mercari HTML.
    Mercari changes its markup periodically, so failure simply means no
    representative Mercari price is written for that run.
    """
    raw=html.replace('\\u00a5','¥')
    candidates=[]
    # Search both directions around the exact ID. This catches common SSR/JSON
    # representations without assuming a specific frontend class name.
    for m in re.finditer(re.escape(cid),raw,re.I):
        a=max(0,m.start()-1400); b=min(len(raw),m.end()+1800)
        chunk=clean_html(raw[a:b])
        for pm in re.finditer(r'¥\s*([0-9][0-9,]*)',chunk):
            price=int(pm.group(1).replace(',',''))
            if 30 <= price <= 200000:
                candidates.append(price)
    return candidates


def mercari_query(c):
    q=' '.join(x for x in [c.get('id'),c.get('name'),c.get('rarity')] if x)
    return MERCARI_BASE+quote_plus(q)


def old_cards():
    if OUT.exists():
        try:return json.loads(OUT.read_text(encoding='utf-8')).get('cards',{})
        except Exception:return {}
    return {}


def main():
    catalog=json.loads(CATALOG.read_text(encoding='utf-8'))
    old=old_cards()
    cards={c['id']:{'offers':[], 'shopLinks':[]} for c in catalog['cards']}
    catalog_by_id={c['id']:c for c in catalog['cards']}

    # Public shop sources.
    for src in SHOP_SOURCES:
        for page in src['pages']:
            if src['name']=='FullAhead':
                url=src['base'] if page==1 else f"{src['base']}page{page}/order/"
            else:
                url=src['base'] if page==1 else f"{src['base']}?page={page}"
            try:
                prices=parse_shop_cards(get(url))
            except Exception as e:
                print(f'WARN {src["name"]} page {page}: {e}')
                continue
            for cid,vals in prices.items():
                if cid in cards:
                    # Keep the lowest normal parsed offer for this source/page.
                    price=min(vals)
                    # Avoid duplicate entries from pagination overlap.
                    if not any(x['shop']==src['name'] for x in cards[cid]['offers']):
                        cards[cid]['offers'].append({'shop':src['name'],'price':price,'url':src['url']})
            time.sleep(.6)

    # Mercari: one search per card. Store a median of plausible exact-ID listings.
    # This is intentionally best-effort because Mercari's frontend/anti-bot rules
    # can change; the search link is always available in the PWA even if scraping fails.
    for i,c in enumerate(catalog['cards'],1):
        url=mercari_query(c)
        cards[c['id']]['shopLinks'].append({'shop':MERCARI_LABEL,'url':url})
        try:
            vals=mercari_prices(get(url),c['id'])
            if vals:
                med=int(round(statistics.median(vals)))
                cards[c['id']]['offers'].append({'shop':MERCARI_LABEL,'price':med,'url':url,'method':'median'})
                print(f'Mercari {c["id"]}: {med} from {len(vals)} candidates')
            else:
                print(f'Mercari {c["id"]}: no exact-ID price')
        except Exception as e:
            print(f'WARN Mercari {c["id"]}: {e}')
        time.sleep(.35)

    # Preserve previous usable offers when a source is temporarily unavailable.
    for cid,c in cards.items():
        prev=old.get(cid,{})
        if not c['offers'] and prev.get('offers'):
            c['offers']=prev['offers']
        # Preserve known shop links, while replacing Mercari with a current query.
        links={x.get('shop'):x for x in prev.get('shopLinks',[]) if x.get('shop')}
        links.update({x['shop']:x for x in c['shopLinks']})
        for x in prev.get('offers',[]):
            if x.get('shop') and x.get('url') and x['shop'] not in links:
                links[x['shop']]={'shop':x['shop'],'url':x['url']}
        c['shopLinks']=list(links.values())
        if c['offers']:
            prices=[int(x['price']) for x in c['offers'] if isinstance(x.get('price'),(int,float)) and x['price']>0]
            c['price']=min(prices) if prices else None
            c['average']=round(sum(prices)/len(prices)) if prices else None
            c['updated']=time.strftime('%Y-%m-%d')
        elif prev:
            # Keep legacy data rather than turning a temporary scrape failure into blanks.
            for k,v in prev.items():
                if k not in ('shopLinks',): c[k]=v

    OUT.write_text(json.dumps({'version':2,'updated':time.strftime('%Y-%m-%d'),'cards':cards},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Updated',OUT)

if __name__=='__main__': main()
