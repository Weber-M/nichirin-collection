#!/usr/bin/env python3
"""Update Nichirin Battle Slash prices from public shop listings.

Only live asking prices are used in the market estimate. This script does not
invent sold prices. Sources that block automated access are reported as blocked
and do not cause previously observed offers to be marked stale.
"""
import csv, html, json, re, statistics, time
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/catalog.json'
OUT = ROOT / 'data/prices.json'
AUDIT = ROOT / 'data/price_audit.csv'
TODAY = date.today().isoformat()
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
ID_RE = re.compile(r'(?<![A-Z0-9])((?:A|P|T)\d{2}-\d{3})(?!\d)', re.I)
PRICE_RE = re.compile(r'(?:¥|￥)\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*円')
BAD_TITLE = ('キズ格安', '傷あり', 'キズあり', 'ジャンク', 'まとめ売り', 'セット販売', 'オリパ')

# FullAhead has a public shop catalogue. TCG Library is Shopify, so its public
# collection products.json endpoint is preferred over ambiguous text scraping.
# Rakuten URLs are attempted, but are explicitly reported blocked if the runner
# receives Rakuten's anti-bot Reference page.
SOURCES = [
    {'name':'FullAhead','shop':'FullAhead','kind':'fullahead',
     'urls':['https://pt-fullahead.com/shopbrand/kynbs/',
             'https://pt-fullahead.com/shopbrand/kynbs/page2/order/',
             'https://pt-fullahead.com/shopbrand/kynbs/page3/order/']},
    {'name':'FullAhead Rakuten','shop':'FullAhead Rakuten','kind':'rakuten',
     'urls':['https://item.rakuten.co.jp/fullahead/c/0000012537/']},
    {'name':'TCG Library A01','shop':'TCG Library','kind':'shopify',
     'urls':['https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01/products.json?limit=250']},
    {'name':'TCG Library all','shop':'TCG Library','kind':'shopify',
     'urls':['https://tcg-library.com/collections/kimetsu-nichirin-battle-slash/products.json?limit=250']},
    {'name':'カードショップ カリントウ A01','shop':'カードショップ カリントウ','kind':'rakuten',
     'urls':['https://item.rakuten.co.jp/karintou10/c/0000006149/']},
    {'name':'カードショップ カリントウ A02','shop':'カードショップ カリントウ','kind':'rakuten',
     'urls':['https://item.rakuten.co.jp/karintou10/c/0000006150/']},
]
SHOP_LINKS = {
 'FullAhead':'https://pt-fullahead.com/shopbrand/kynbs/',
 'FullAhead Rakuten':'https://item.rakuten.co.jp/fullahead/c/0000012537/',
 'TCG Library':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash',
 'カードショップ カリントウ':'https://item.rakuten.co.jp/karintou10/c/0000006149/',
}

class TextParser(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]
    def handle_data(self, data):
        if data.strip(): self.parts.append(data.strip())

def get_bytes(url):
    req = Request(url, headers={
        'User-Agent': UA, 'Accept-Language':'ja-JP,ja;q=0.9,en-US;q=0.7,en;q=0.6',
        'Accept':'text/html,application/json,application/xhtml+xml,*/*;q=0.8',
        'Cache-Control':'no-cache'
    })
    with urlopen(req, timeout=30) as response:
        return response.read(), response.headers.get_content_charset() or 'utf-8'

def get_text(url):
    raw, enc = get_bytes(url)
    for e in (enc, 'utf-8', 'cp932', 'shift_jis', 'euc_jp'):
        try: return raw.decode(e)
        except (UnicodeDecodeError, LookupError): pass
    return raw.decode('utf-8', 'replace')

def visible_text(page):
    page = re.sub(r'<(script|style|noscript)\b[^>]*>[\s\S]*?</\1>', ' ', page, flags=re.I)
    p = TextParser(); p.feed(page)
    return re.sub(r'\s+', ' ', html.unescape(' '.join(p.parts)))

def card_ids_in(text, known_ids):
    return [m.group(1).upper() for m in ID_RE.finditer(text) if m.group(1).upper() in known_ids]

def price_values(text):
    vals=[]
    for m in PRICE_RE.finditer(text):
        raw=m.group(1) or m.group(2)
        try: value=int(raw.replace(',', ''))
        except (ValueError, AttributeError): continue
        if 1 <= value <= 1_000_000: vals.append(value)
    return list(dict.fromkeys(vals))

def build_obs(cid, shop, source, price, url, index):
    return {'card_id':cid, 'shop':shop, 'source':source, 'price':int(price), 'url':url,
            'sourceUrl':url, 'date':TODAY, 'type':'asking',
            'listingKey':f'{shop}|{cid}|{price}|{url}|{index}'}

def parse_shopify(url, src, known_ids):
    raw, _ = get_bytes(url)
    try: data=json.loads(raw.decode('utf-8'))
    except Exception as exc: raise RuntimeError(f'Shopify products.json was not valid JSON: {exc}')
    products=data.get('products')
    if not isinstance(products, list): raise RuntimeError('Shopify JSON did not contain a products list')
    found=[]; rejected=0
    for product in products:
        title=str(product.get('title',''))
        handle=str(product.get('handle',''))
        identity=title+' '+handle
        ids=card_ids_in(identity, known_ids)
        if not ids: continue
        # A product title should identify one card. Ambiguous multi-card bundles
        # are excluded rather than spreading one bundle price across card IDs.
        if len(set(ids)) != 1 or any(word.lower() in identity.lower() for word in BAD_TITLE):
            rejected += 1; continue
        cid=ids[0]
        variants=product.get('variants') or []
        available=[]
        for variant in variants:
            if not variant.get('available', False): continue
            try: price=int(round(float(variant.get('price'))))
            except (TypeError, ValueError): continue
            if 1 <= price <= 1_000_000: available.append(price)
        # Product is current only if Shopify exposes an available variant.
        for i, price in enumerate(sorted(set(available))):
            offer=build_obs(cid, src['shop'], src['name'], price,
                'https://tcg-library.com/products/'+handle, i)
            offer['sourceUrl']=url
            found.append(offer)
    return found, len(products), rejected

def parse_fullahead(page_html, src, url, known_ids):
    # Product ID and price must occur in a short local text window. Listings with
    # damaged-card markers, bundles, sold-out labels or ambiguous prices are skipped.
    text=visible_text(page_html)
    matches=list(ID_RE.finditer(text)); found=[]
    for i, match in enumerate(matches):
        cid=match.group(1).upper()
        if cid not in known_ids: continue
        start=max(0, match.start()-150)
        end=min(len(text), matches[i+1].start() if i+1<len(matches) else match.end()+240)
        segment=text[start:end]
        if any(word.lower() in segment.lower() for word in BAD_TITLE) or any(w in segment.lower() for w in ('売り切れ','在庫切れ','sold out')):
            continue
        vals=price_values(segment)
        # If several prices occur in the local segment, the match is ambiguous.
        if len(vals) != 1: continue
        found.append(build_obs(cid, src['shop'], src['name'], vals[0], url, i))
    # Deduplicate repeated ID/price pairs within a page.
    out=[]; seen=set()
    for item in found:
        key=(item['card_id'],item['price'])
        if key not in seen: seen.add(key); out.append(item)
    return out, len(matches), 0

def parse_rakuten(page_html, src, url, known_ids):
    if 'Reference #' in page_html or len(page_html.strip()) < 300:
        raise RuntimeError('Rakuten blocked this GitHub Actions request (anti-bot Reference page); no prices counted')
    # Conservative fallback parser: match only locally associated card IDs and
    # prices. Do not mark old offers stale unless this page was truly readable.
    return parse_fullahead(page_html, src, url, known_ids)

def mercari_url(card):
    q=' '.join(x for x in [card['id'],card.get('name',''),str(card.get('rarity','')).replace('*','')] if x)
    return 'https://jp.mercari.com/search?'+urlencode({'keyword':q})

def weighted_median(pairs):
    pairs=sorted((float(v),float(w)) for v,w in pairs if v>0 and w>0)
    if not pairs:return None
    halfway=sum(w for _,w in pairs)/2; acc=0
    for value,weight in pairs:
        acc+=weight
        if acc>=halfway:return int(round(value))
    return int(round(pairs[-1][0]))

def recency_weight(observation):
    try: age=max(0,(date.fromisoformat(TODAY)-date.fromisoformat(observation.get('observedDate',observation.get('updated',TODAY)))).days)
    except (ValueError,TypeError): age=36500
    return 0.5**(age/30)

def estimate(offers):
    # Reverified offers count fully; older offers remain useful but decay with
    # time, and an offer not seen on today's successful scrape gets an extra
    # penalty rather than being treated as a confirmed current listing.
    usable=[o for o in offers if isinstance(o.get('price'),(int,float)) and o['price']>0]
    if not usable:return None
    by_shop={}
    for o in usable: by_shop.setdefault(o.get('shop','Unknown'),[]).append(o)
    pairs=[]
    for items in by_shop.values():
        for o in items:
            weight=recency_weight(o)
            if o.get('stale'): weight *= 0.25
            pairs.append((o['price'],weight/len(items)))
    med=weighted_median(pairs); total=sum(w for _,w in pairs)
    avg=int(round(sum(v*w for v,w in pairs)/total)) if total else None
    current=[o for o in usable if not o.get('stale')]
    n=len(current); ns=len({o.get('shop','Unknown') for o in current})
    confidence='high' if ns>=3 and n>=8 else 'medium' if ns>=2 and n>=3 else 'low'
    return {'market':med,'weightedAverage':avg,
            'cheapest':min((int(o['price']) for o in current), default=min(int(o['price']) for o in usable)),
            'listingCount':n,'shopCount':ns,'confidence':confidence}

def main():
    catalog=json.loads(CATALOG.read_text(encoding='utf-8-sig'))
    if not isinstance(catalog.get('cards'),list): raise RuntimeError('catalog.json does not contain cards[]')
    old=json.loads(OUT.read_text(encoding='utf-8-sig')) if OUT.exists() else {'cards':{}}
    oldcards=old.get('cards',{}) if isinstance(old.get('cards',{}),dict) else {}
    card_by_id={str(c['id']).upper():c for c in catalog['cards'] if c.get('id')}
    cards={cid:dict(oldcards.get(cid,{})) for cid in card_by_id}
    fetched={cid:[] for cid in cards}; audit=[]; successful=[]; successful_urls=set(); failed=[]

    for src in SOURCES:
        source_obs=0
        for url in src['urls']:
            try:
                if src['kind']=='shopify':
                    obs, item_count, rejected=parse_shopify(url,src,card_by_id)
                    print(f"PRICE SOURCE | {src['name']} | Shopify products={item_count} | matched offers={len(obs)} | rejected={rejected}")
                else:
                    page=get_text(url)
                    if src['kind']=='rakuten': obs,item_count,rejected=parse_rakuten(page,src,url,card_by_id)
                    else: obs,item_count,rejected=parse_fullahead(page,src,url,card_by_id)
                    print(f"PRICE SOURCE | {src['name']} | page text chars={len(page)} | matched offers={len(obs)}")
                if obs: successful_urls.add(url)
                source_obs += len(obs)
                for o in obs:
                    fetched[o['card_id']].append(o)
                    audit.append({'date':TODAY,'card_id':o['card_id'],'card_name':card_by_id[o['card_id']].get('name',''),
                      'shop':o['shop'],'source_page':o['source'],'page':url,'price_yen':o['price'],
                      'decision':'OBSERVED','reason':'Available current asking price; not a completed sale',
                      'url':o['url'],'listing_key':o['listingKey']})
            except Exception as exc:
                message=str(exc)[:350]; failed.append((src['name'],url,message))
                print(f"PRICE SOURCE FAILED | {src['name']} | {url} | {message}")
                audit.append({'date':TODAY,'card_id':'','card_name':'','shop':src['shop'],'source_page':src['name'],
                  'page':url,'price_yen':'','decision':'FETCH_FAILED','reason':message,'url':url,'listing_key':''})
            time.sleep(.4)
        if source_obs: successful.append(src['name'])
        elif not any(name==src['name'] for name,_,_ in failed):
            print(f"PRICE SOURCE WARNING | {src['name']} fetched but extracted zero offers; check parser/source layout")

    # Keep old observations unless the exact page that supplied them was fetched
    # successfully today. A blocked shop must never make all its old prices stale.
    for cid, card in card_by_id.items():
        prev=cards[cid]
        dedup=[]; seen=set()
        for o in fetched[cid]:
            key=(o['shop'],int(o['price']),o['url'])
            if key in seen:
                audit.append({'date':TODAY,'card_id':cid,'card_name':card.get('name',''),'shop':o['shop'],
                  'source_page':o['source'],'page':'','price_yen':o['price'],'decision':'DUPLICATE',
                  'reason':'Same shop, price and listing URL repeated','url':o['url'],'listing_key':o['listingKey']})
            else: seen.add(key); dedup.append(o)
        old_offers=prev.get('offers',[]) if isinstance(prev.get('offers',[]),list) else []
        old_by_key={}
        for o in old_offers:
            if isinstance(o,dict) and o.get('price'):
                try: k=(str(o.get('shop','')),int(o.get('price',0)),str(o.get('url','')))
                except (TypeError,ValueError): continue
                old_by_key[k]=o
        newkeys={(o['shop'],int(o['price']),o['url']) for o in dedup}
        combined=[{'shop':o['shop'],'source':o['source'],'price':int(o['price']),'url':o['url'],
          'updated':TODAY,'observedDate':TODAY,'stale':False,'type':'asking','listingKey':o['listingKey'],
          'sourceUrl':o.get('sourceUrl',o['url'])} for o in dedup]
        for key, old_offer in old_by_key.items():
            if key in newkeys: continue
            old_url=str(old_offer.get('sourceUrl',old_offer.get('url','')))
            if old_url in successful_urls:
                stale=dict(old_offer); stale['stale']=True; combined.append(stale)
            else:
                # If the source was blocked or the parser found no listings, we
                # cannot conclude that the offer disappeared. Preserve its prior
                # state and let the observation-date decay reduce its influence.
                stale=dict(old_offer); combined.append(stale)
        prev['offers']=combined

        shop_history=prev.get('shopHistory',[])
        if not isinstance(shop_history,list): shop_history=[]
        todays_shops={o['shop'] for o in dedup}
        shop_history=[x for x in shop_history if not (isinstance(x,dict) and x.get('date')==TODAY and x.get('shop') in todays_shops)]
        by_shop={}
        for o in dedup: by_shop.setdefault(o['shop'],[]).append(int(o['price']))
        for shop, vals in sorted(by_shop.items()):
            med=int(round(statistics.median(vals)))
            shop_history.append({'date':TODAY,'shop':shop,'price':med,'median':med,
              'average':int(round(sum(vals)/len(vals))),'cheapest':min(vals),'listingCount':len(vals),'type':'asking'})
        shop_history.sort(key=lambda x:(str(x.get('date','')),str(x.get('shop','')))); prev['shopHistory']=shop_history

        est=estimate(combined)
        if est:
            prev.update({'price':est['cheapest'],'marketValue':est['market'],'average':est['weightedAverage'],
              'weightedAverage':est['weightedAverage'],'confidence':est['confidence'],
              'listingCount':est['listingCount'],'shopCount':est['shopCount']})
            # Only mark updated as today's estimate if there is a new observation today.
            if dedup: prev['updated']=TODAY
        prev['shopLinks']=[{'shop':k,'url':v} for k,v in SHOP_LINKS.items()]
        prev['shopLinks'].append({'shop':'メルカリ','url':mercari_url(card)})
        prev['mercari']={'status':'manual-search','median':None,'samples':[],'url':mercari_url(card),
                         'note':'No automated sold-price data; do not interpret asks as completed sales.'}
        hist=prev.get('history',[])
        if not isinstance(hist,list): hist=[]
        hist=[x for x in hist if not (isinstance(x,dict) and x.get('date')==TODAY)]
        if est and dedup:
            hist.append({'date':TODAY,'market':est['market'],'average':est['weightedAverage'],
              'cheapest':est['cheapest'],'listingCount':est['listingCount'],'shopCount':est['shopCount'],
              'confidence':est['confidence']})
        hist.sort(key=lambda x:str(x.get('date',''))); prev['history']=hist; cards[cid]=prev

    fields=['date','card_id','card_name','shop','source_page','page','price_yen','decision','reason','url','listing_key']
    with AUDIT.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader(); writer.writerows(audit)
    out={**{k:v for k,v in old.items() if k not in {'cards','updated','sourcesFetchedToday','sourceStatus'}},
      'updated':TODAY,'cards':cards,'version':6,'v6':2,
      'historyPolicy':'Daily market snapshots are added only on days with fresh observations. Per-shop daily summaries are stored in each card shopHistory; missing days are not forward-filled.',
      'pricePolicy':'Market value is a recency-weighted median of live asking-price observations, with equal total influence per shop. Asking prices are not completed sales.',
      'sourcesFetchedToday':sorted(set(successful)),
      'sourceStatus':{'successfulSources':sorted(set(successful)), 'failedFetches':len(failed),
        'successfulPages':len(successful_urls), 'observedListings':sum(len(v) for v in fetched.values()),
        'checkedOn':TODAY},
      'mercariNote':'Mercari is a manual search link only; no automated completed-sale prices are included.'}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"PRICE SUMMARY | cards={len(cards)} | fresh listing observations={sum(len(v) for v in fetched.values())} | successful pages={len(successful_urls)} | failed pages={len(failed)}")
    print('Sources with offers:', ', '.join(sorted(set(successful))) or '(none)')
    print('Audit decisions:', dict(Counter(row['decision'] for row in audit)))
    print('Audit file:', AUDIT)

if __name__=='__main__': main()
