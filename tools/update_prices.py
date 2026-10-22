#!/usr/bin/env python3
# See the accompanying response for the intended pricing policy.
# This file is a safe updater foundation; all current observations are asking prices.
import csv, html, json, os, re, statistics, time
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'data/catalog.json'
OUT=ROOT/'data/prices.json'
AUDIT=ROOT/'data/price_audit.csv'
TODAY=date.today().isoformat()
UA='Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/4.0)'
# Optional authorized data-provider adapter. This is NOT a Mercari website scraper.
# The provider must permit automated API access and return the JSON contract documented
# in README / the accompanying setup notes. Leave unset until an authorized provider
# and its endpoint/schema have been confirmed.
MERCARI_API_URL=os.environ.get('MERCARI_AUTHORIZED_API_URL','').strip()
MERCARI_API_TOKEN=os.environ.get('MERCARI_AUTHORIZED_API_TOKEN','').strip()
MERCARI_API_LIMIT=max(1,min(int(os.environ.get('MERCARI_API_LIMIT','20')),100))
SOURCES=[
 {'name':'FullAhead','shop':'FullAhead','base':'https://pt-fullahead.com/shopbrand/kynbs/','pages':range(1,6),'kind':'fullahead'},
 {'name':'FullAhead Rakuten','shop':'FullAhead Rakuten','base':'https://item.rakuten.co.jp/fullahead/c/0000012537/','pages':range(1,3),'kind':'rakuten'},
 {'name':'TCG Library A01','shop':'TCG Library','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01','pages':range(1,3),'kind':'tcg'},
 {'name':'TCG Library all','shop':'TCG Library','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash','pages':range(1,4),'kind':'tcg'},
 {'name':'カードショップ カリントウ A01','shop':'カードショップ カリントウ','base':'https://item.rakuten.co.jp/karintou10/c/0000006149/','pages':range(1,2),'kind':'rakuten'},
 {'name':'カードショップ カリントウ A02','shop':'カードショップ カリントウ','base':'https://item.rakuten.co.jp/karintou10/c/0000006150/','pages':range(1,2),'kind':'rakuten'},
]
SHOP_LINKS={
 'FullAhead':'https://pt-fullahead.com/shopbrand/kynbs/',
 'FullAhead Rakuten':'https://item.rakuten.co.jp/fullahead/c/0000012537/',
 'TCG Library':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash',
 'カードショップ カリントウ':'https://item.rakuten.co.jp/karintou10/c/0000006149/',
}
ID_RE=re.compile(r'\b((?:A|P|T)\d{2}-\d{3})\b')
PRICE_RE=re.compile(r'(?:¥|￥)\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*円')
EXCLUDED=('キズ格安','傷あり','ジャンク','まとめ売り','セット販売','売り切れ','SOLD OUT')

class TextParser(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]
 def handle_data(self,data):
  if data.strip(): self.parts.append(data.strip())

def get(url):
 req=Request(url,headers={'User-Agent':UA,'Accept-Language':'ja,en;q=0.8'})
 with urlopen(req,timeout=25) as r:
  raw=r.read(); enc=r.headers.get_content_charset() or 'utf-8'
 for e in (enc,'utf-8','cp932','shift_jis','euc_jp'):
  try:return raw.decode(e)
  except (UnicodeDecodeError,LookupError):pass
 return raw.decode('utf-8','replace')

def visible_text(page):
 page=re.sub(r'<script\b[^>]*>[\s\S]*?</script>',' ',page,flags=re.I)
 page=re.sub(r'<style\b[^>]*>[\s\S]*?</style>',' ',page,flags=re.I)
 p=TextParser();p.feed(page)
 return re.sub(r'\s+',' ',html.unescape(' '.join(p.parts)))

def page_url(src,page):
 if src['kind']=='fullahead':return src['base'] if page==1 else f"{src['base']}page{page}/order/"
 if src['kind']=='tcg':return src['base'] if page==1 else f"{src['base']}?page={page}"
 return src['base'] if page==1 else f"{src['base']}?p={page}"


def parse_listings(page_html, src, url, known_ids):
    found = []

    # TCG Library uses Shopify product-card HTML. Parse each product card
    # independently so that prices cannot accidentally be assigned to
    # neighboring cards.
    if src.get('kind') == 'tcg':
        from html.parser import HTMLParser
        from html import unescape
        from urllib.parse import urljoin
        import re

        class ProductCardParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.cards = []
                self.card = None
                self.depth = 0
                self.capture_title = False
                self.title_depth = 0
                self.capture_price = False
                self.price_depth = 0
                self.title_parts = []
                self.price_parts = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                classes = attrs.get('class', '').split()

                # The product-card link commonly contains the product URL
                # and image/title for one listing.
                if tag == 'a' and 'scard__link' in classes:
                    self.card = {
                        'url': urljoin('https://tcg-library.com', attrs.get('href', '')),
                        'title': '',
                        'prices': [],
                    }
                    self.depth = 1
                    self.capture_title = False
                    self.capture_price = False
                    self.title_parts = []
                    self.price_parts = []
                    return

                if self.card is None:
                    return

                self.depth += 1

                if tag in ('h2', 'h3', 'h4'):
                    self.capture_title = True
                    self.title_depth = self.depth
                    self.title_parts = []

                if tag == 'span' and 'price-block__price' in classes:
                    self.capture_price = True
                    self.price_depth = self.depth
                    self.price_parts = []

            def handle_data(self, data):
                if self.card is None:
                    return
                if self.capture_title:
                    self.title_parts.append(data)
                if self.capture_price:
                    self.price_parts.append(data)

            def handle_endtag(self, tag):
                if self.card is None:
                    return

                if self.capture_title and self.depth == self.title_depth:
                    self.card['title'] = ' '.join(self.title_parts).strip()
                    self.capture_title = False

                if self.capture_price and self.depth == self.price_depth:
                    raw_price = ''.join(self.price_parts).strip()
                    match = re.search(r'[¥￥]\s*([0-9][0-9,]*)', raw_price)
                    if match:
                        try:
                            price = int(match.group(1).replace(',', ''))
                            if 1 <= price <= 1_000_000:
                                self.card['prices'].append(price)
                        except ValueError:
                            pass
                    self.capture_price = False

                self.depth -= 1

                if tag == 'a' and self.depth == 0:
                    self.card['title'] = self.card['title'] or ''
                    self.cards.append(self.card)
                    self.card = None
                    self.capture_title = False
                    self.capture_price = False

        parser = ProductCardParser()
        parser.feed(page_html)

        # Shopify collection-event data may contain the complete product
        # title, URL, and numeric price even when card markup differs.
        # Use it as a fallback, matching each event product independently.
        for card in parser.cards:
            combined = unescape(card['title'] + ' ' + card['url'])
            id_matches = list(ID_RE.finditer(combined))
            card_ids = list(dict.fromkeys(
                m.group(1) for m in id_matches if m.group(1) in known_ids
            ))

            prices = list(dict.fromkeys(card['prices']))

            if len(card_ids) != 1 or len(prices) != 1:
                continue

            cid = card_ids[0]
            exclusion_text = combined.lower()
            if any(term.lower() in exclusion_text for term in EXCLUDED):
                continue

            price = prices[0]
            found.append({
                'card_id': cid,
                'shop': src['shop'],
                'source': src['name'],
                'price': price,
                'url': card['url'] or url,
                'date': TODAY,
                'type': 'asking',
                'listingKey': (
                    f"{src['shop']}|{cid}|{price}|{card['url'] or url}"
                ),
            })

        # If the product-card markup did not match, try Shopify's
        # collection event data, where prices are numeric JPY amounts.
        if not found:
            import json

            event_pattern = re.compile(
                r'"collection_viewed"\\s*,\\s*\\{.*?"collection"\\s*:\\s*'
                r'(\\{.*?\\})\\s*\\}\\s*\\]'
            )

            for event_match in event_pattern.finditer(page_html):
                try:
                    event = json.loads(event_match.group(1))
                except (json.JSONDecodeError, ValueError):
                    continue

                for variant in event.get('productVariants', []):
                    product = variant.get('product') or {}
                    title = str(product.get('title') or '')
                    product_url = urljoin(
                        'https://tcg-library.com',
                        str(product.get('url') or '')
                    )
                    combined = title + ' ' + product_url

                    ids = list(dict.fromkeys(
                        m.group(1) for m in ID_RE.finditer(combined)
                        if m.group(1) in known_ids
                    ))
                    try:
                        price = int(float(variant.get('price', {}).get('amount')))
                    except (TypeError, ValueError):
                        continue

                    if len(ids) != 1 or not (1 <= price <= 1_000_000):
                        continue
                    if any(term.lower() in combined.lower() for term in EXCLUDED):
                        continue

                    cid = ids[0]
                    found.append({
                        'card_id': cid,
                        'shop': src['shop'],
                        'source': src['name'],
                        'price': price,
                        'url': product_url or url,
                        'date': TODAY,
                        'type': 'asking',
                        'listingKey': f"{src['shop']}|{cid}|{price}|{product_url}",
                    })

        # De-duplicate cards that appear more than once on a page.
        out = []
        seen = set()
        for item in found:
            key = (
                item['card_id'], item['shop'], item['price'],
                item['url'], item['listingKey']
            )
            if key not in seen:
                seen.add(key)
                out.append(item)

        return out

    # Existing parser for non-TCG sources, including FullAhead.
    text = visible_text(page_html)
    ids = list(ID_RE.finditer(text))
    found = []

    for i, m in enumerate(ids):
        cid = m.group(1)
        if cid not in known_ids:
            continue

        next_start = (
            ids[i + 1].start()
            if i + 1 < len(ids)
            else min(len(text), m.end() + 320)
        )
        segment = text[m.end():next_start]
        vals = []

        for pm in PRICE_RE.finditer(segment):
            raw = pm.group(1) or pm.group(2)
            try:
                value = int(raw.replace(',', ''))
            except (ValueError, AttributeError):
                continue
            if 1 <= value <= 1_000_000:
                vals.append(value)

        if not vals:
            prev_end = (
                ids[i - 1].end()
                if i > 0
                else max(0, m.start() - 180)
            )
            segment = text[prev_end:m.start()]

            for pm in PRICE_RE.finditer(segment):
                raw = pm.group(1) or pm.group(2)
                try:
                    value = int(raw.replace(',', ''))
                except (ValueError, AttributeError):
                    continue
                if 1 <= value <= 1_000_000:
                    vals.append(value)

        segment_for_exclusions = text[
            max(0, m.start() - 120):min(len(text), next_start + 120)
        ]
        if any(
            term.lower() in segment_for_exclusions.lower()
            for term in EXCLUDED
        ):
            continue

        vals = list(dict.fromkeys(vals))
        if len(vals) != 1:
            continue

        found.append({
            'card_id': cid,
            'shop': src['shop'],
            'source': src['name'],
            'price': vals[0],
            'url': url,
            'date': TODAY,
            'type': 'asking',
            'listingKey': (
                f"{src['shop']}|{cid}|{vals[0]}|{url}|{i}"
            ),
        })

    out = []
    seen = set()
    for item in found:
        key = (
            item['card_id'], item['shop'], item['price'],
            item['url'], item['listingKey']
        )
        if key not in seen:
            seen.add(key)
            out.append(item)

    return out

def mercari_url(card):
 q=' '.join(x for x in [card['id'],card.get('name',''),str(card.get('rarity','')).replace('*','')] if x)
 return 'https://jp.mercari.com/search?'+urlencode({'keyword':q})

def mercari_api_configured():
 if not MERCARI_API_URL and not MERCARI_API_TOKEN:
  return False
 if not MERCARI_API_URL or not MERCARI_API_TOKEN:
  print('WARN Mercari API disabled: set both MERCARI_AUTHORIZED_API_URL and MERCARI_AUTHORIZED_API_TOKEN')
  return False
 parsed=urlparse(MERCARI_API_URL)
 if parsed.scheme!='https' or not parsed.netloc:
  print('WARN Mercari API disabled: endpoint must be an HTTPS URL')
  return False
 host=(parsed.hostname or '').lower()
 if host=='mercari.com' or host.endswith('.mercari.com') or host=='mercari.jp' or host.endswith('.mercari.jp'):
  print('WARN Mercari API disabled: do not point this adapter at Mercari website/undocumented endpoints; use a separately authorized provider API')
  return False
 return True

def fetch_authorized_mercari(card):
 """Fetch normalized active/sold observations from a separately authorized API.

 Expected JSON: {"results":[{"card_id":"A01-001", "status":"active|sold",
 "price":1200, "url":"https://...", "listing_id":"provider-id",
 "title":"...", "observed_at":"YYYY-MM-DD"}]}
 Query parameters sent: card_id, q, statuses=active,sold, limit.
 The provider must explicitly label sold/completed transactions as status=sold.
 """
 q=' '.join(x for x in [card['id'],card.get('name',''),str(card.get('rarity','')).replace('*','')] if x)
 params={'card_id':card['id'],'q':q,'statuses':'active,sold','limit':MERCARI_API_LIMIT}
 sep='&' if '?' in MERCARI_API_URL else '?'
 url=MERCARI_API_URL+sep+urlencode(params)
 req=Request(url,headers={'Authorization':f'Bearer {MERCARI_API_TOKEN}',
  'Accept':'application/json','User-Agent':UA})
 with urlopen(req,timeout=25) as response:
  if response.status<200 or response.status>=300:
   raise RuntimeError(f'authorized provider returned HTTP {response.status}')
  payload=json.loads(response.read().decode('utf-8'))
 rows=payload.get('results',[]) if isinstance(payload,dict) else None
 if not isinstance(rows,list):
  raise ValueError('provider response must be a JSON object containing a results array')
 normalized=[]
 for row in rows:
  if not isinstance(row,dict): continue
  cid=str(row.get('card_id') or card['id']).strip().upper()
  if cid!=card['id'].upper(): continue
  status=str(row.get('status','')).strip().lower().replace('-','_').replace(' ','_')
  if status in {'active','listed','on_sale','onsale'}: kind='asking'
  elif status in {'sold','completed','transaction_completed'}: kind='sold'
  else: continue  # Never infer a completed sale from a generic unavailable/closed state.
  try: price=int(float(row.get('price')))
  except (TypeError,ValueError,OverflowError): continue
  if not 1<=price<=10000000: continue
  item_url=str(row.get('url') or '').strip()
  if item_url and urlparse(item_url).scheme!='https': item_url=''
  listing_id=str(row.get('listing_id') or row.get('id') or item_url or '').strip()
  if not listing_id: continue
  observed=str(row.get('observed_at') or row.get('sold_at') or TODAY)[:10]
  if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',observed): observed=TODAY
  normalized.append({'kind':kind,'price':price,'url':item_url,'listingId':listing_id,
   'title':str(row.get('title') or '')[:300],'observedDate':observed,'card_id':card['id'],
   'source':'Mercari authorized API'})
 # Same provider listing appearing twice in one response is only counted once.
 unique=[];seen=set()
 for row in normalized:
  key=(row['kind'],row['listingId'],row['price'])
  if key not in seen: seen.add(key); unique.append(row)
 return unique

def update_mercari_record(prev, card, rows, api_success):
 """Store asking and sold observations separately; retain previous observations/history."""
 m=prev.get('mercari')
 m=dict(m) if isinstance(m,dict) else {}
 m['url']=mercari_url(card)
 asking=m.get('asking') if isinstance(m.get('asking'),dict) else {}
 sold=m.get('sold') if isinstance(m.get('sold'),dict) else {}
 # Migrate any previous top-level Mercari sample shape into asking data only.
 old_asking=asking.get('samples')
 if not isinstance(old_asking,list):
  old_asking=m.get('samples') if isinstance(m.get('samples'),list) else []
 asking_history=asking.get('history') if isinstance(asking.get('history'),list) else []
 asking_observations=asking.get('observations') if isinstance(asking.get('observations'),list) else []
 sold_samples=sold.get('samples') if isinstance(sold.get('samples'),list) else []
 sold_history=sold.get('history') if isinstance(sold.get('history'),list) else []
 if api_success:
  active=[r for r in rows if r['kind']=='asking']
  newly_sold=[r for r in rows if r['kind']=='sold']
  # Current asking samples reflect only the provider's latest active result set.
  asking_samples=[{'price':r['price'],'url':r['url'],'listingId':r['listingId'],
   'title':r['title'],'observedDate':r['observedDate'],'status':'active','source':r['source']}
   for r in active]
  # Preserve each date/listing/price observation rather than replacing history.
  seen_obs={(str(x.get('listingId','')),str(x.get('observedDate','')),int(x.get('price',0) or 0))
   for x in asking_observations if isinstance(x,dict)}
  for sample in asking_samples:
   key=(sample['listingId'],sample['observedDate'],sample['price'])
   if key not in seen_obs: asking_observations.append(sample); seen_obs.add(key)
  if active:
   vals=[r['price'] for r in active]
   asking_history=[x for x in asking_history if not (isinstance(x,dict) and x.get('date')==TODAY)]
   asking_history.append({'date':TODAY,'median':int(round(statistics.median(vals))),
    'average':int(round(sum(vals)/len(vals))),'cheapest':min(vals),'listingCount':len(vals)})
  # Completed transactions are immutable observations; retain and de-duplicate them.
  seen_sold={(str(x.get('listingId','')),int(x.get('price',0) or 0))
   for x in sold_samples if isinstance(x,dict)}
  for r in newly_sold:
   sample={'price':r['price'],'url':r['url'],'listingId':r['listingId'],'title':r['title'],
    'observedDate':r['observedDate'],'status':'sold','source':r['source']}
   key=(sample['listingId'],sample['price'])
   if key not in seen_sold: sold_samples.append(sample); seen_sold.add(key)
  if newly_sold:
   vals=[r['price'] for r in newly_sold]
   sold_history=[x for x in sold_history if not (isinstance(x,dict) and x.get('date')==TODAY)]
   sold_history.append({'date':TODAY,'median':int(round(statistics.median(vals))),
    'average':int(round(sum(vals)/len(vals))),'saleCount':len(vals)})
  asking['samples']=asking_samples
  asking['observations']=asking_observations
  asking['history']=sorted(asking_history,key=lambda x:str(x.get('date','')))
  asking['median']=int(round(statistics.median([x['price'] for x in asking_samples]))) if asking_samples else None
  asking['updated']=TODAY if active else asking.get('updated')
  sold['samples']=sold_samples
  sold['history']=sorted(sold_history,key=lambda x:str(x.get('date','')))
  sold['median']=int(round(statistics.median([x['price'] for x in sold_samples]))) if sold_samples else None
  sold['updated']=TODAY if newly_sold else sold.get('updated')
  m['status']='api-ok'
  m['updated']=TODAY
  m['lastFetch']={'date':TODAY,'askingCount':len(active),'newSoldCount':len(newly_sold),
   'status':'ok'}
 else:
  m.setdefault('status','not-configured')
  # Explicitly preserve last good samples and medians on provider errors/unavailability.
 m['asking']=asking
 m['sold']=sold
 # Compatibility with older UI/data readers: legacy Mercari price means active asking price only.
 m['median']=asking.get('median')
 m['samples']=asking.get('samples',old_asking)
 prev['mercari']=m
 return m

def weighted_median(pairs):
 pairs=sorted((float(v),float(w)) for v,w in pairs if v>0 and w>0)
 if not pairs:return None
 halfway=sum(w for _,w in pairs)/2;acc=0
 for v,w in pairs:
  acc+=w
  if acc>=halfway:return int(round(v))
 return int(round(pairs[-1][0]))

def recency_weight(o):
 try:age=max(0,(date.fromisoformat(TODAY)-date.fromisoformat(o.get('observedDate',o.get('updated',TODAY)))).days)
 except (ValueError,TypeError):age=36500
 return 0.5**(age/30)

def estimate(offers):
 fresh=[o for o in offers if not o.get('stale') and isinstance(o.get('price'),(int,float)) and o['price']>0]
 if not fresh:return None
 by_shop={}
 for o in fresh:by_shop.setdefault(o.get('shop','Unknown'),[]).append(o)
 pairs=[]
 for shop,items in by_shop.items():
  # Equal total weight per shop; multiple listings improve the shop's median
  # but do not let a large catalogue dominate the whole market.
  for o in items:pairs.append((o['price'],recency_weight(o)/len(items)))
 med=weighted_median(pairs); total=sum(w for _,w in pairs)
 avg=int(round(sum(v*w for v,w in pairs)/total)) if total else None
 n=len(fresh); ns=len(by_shop)
 confidence='high' if ns>=3 and n>=8 else 'medium' if ns>=2 and n>=3 else 'low'
 return {'market':med,'weightedAverage':avg,'cheapest':min(int(o['price']) for o in fresh),
         'listingCount':n,'shopCount':ns,'confidence':confidence}

def main():
 catalog=json.loads(CATALOG.read_text(encoding='utf-8'))
 old=json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {'cards':{}}
 oldcards=old.get('cards',{}); card_by_id={c['id']:c for c in catalog['cards']}
 cards={cid:dict(oldcards.get(cid,{})) for cid in card_by_id}
 fetched={cid:[] for cid in cards};audit=[];successful=set()
 mercari_enabled=mercari_api_configured()
 if mercari_enabled: print('Mercari authorized API enabled; asking and sold records will be stored separately.')
 else: print('Mercari API not configured; existing Mercari observations/history will be preserved.')
 for src in SOURCES:
  for page in src['pages']:
   url=page_url(src,page)
   try:
    page_html = get(url)
    page_text = visible_text(page_html)

    if src['kind'] == 'tcg' and page == 1:
        id_match = ID_RE.search(page_html)
    
        if id_match:
            start = max(0, id_match.start() - 1500)
            end = min(len(page_html), id_match.end() + 3000)
            print("TCG HTML AROUND FIRST CARD ID:", page_html[start:end])
        else:
            print("TCG HTML: no card ID found in raw HTML")
    
        price_matches = list(PRICE_RE.finditer(page_html))
        print(
            "TCG RAW PRICE MATCHES:",
            [
                page_html[max(0, m.start() - 100):min(len(page_html), m.end() + 100)]
                for m in price_matches[:15]
            ]
        )

    if len(page_text) < 100:
        print(
            f"SHORT RESPONSE | {src['name']} | "
            f"{url} | body={page_text!r}"
        )
    
    id_matches = list(ID_RE.finditer(page_text))
    known_matches = sum(
        1 for m in id_matches if m.group(1) in card_by_id
    )
    
    print(
        f"PAGE DIAGNOSTIC | {src['name']} | page {page} "
        f"| chars={len(page_text)} "
        f"| card_ids={len(id_matches)} "
        f"| known_card_ids={known_matches}"
    )
    
    obs = parse_listings(page_html, src, url, card_by_id)
    
    print(
        f"PRICE DEBUG | {src['name']} | page {page} "
        f"| extracted {len(obs)} listing observations"
    )
    if obs:successful.add(src['name'])
    for o in obs:
     fetched[o['card_id']].append(o)
     audit.append({'date':TODAY,'card_id':o['card_id'],'card_name':card_by_id[o['card_id']].get('name',''),
      'shop':o['shop'],'source_page':o['source'],'page':page,'price_yen':o['price'],'decision':'OBSERVED',
      'reason':'Current asking-price observation; not a completed sale','url':url,'listing_key':o['listingKey']})
   except Exception as e:
    print('WARN',src['name'],page,str(e)[:250])
    audit.append({'date':TODAY,'card_id':'','card_name':'','shop':src['shop'],'source_page':src['name'],
      'page':page,'price_yen':'','decision':'FETCH_FAILED','reason':str(e)[:300],'url':url,'listing_key':''})
   time.sleep(.35)
 for cid,card in card_by_id.items():
  prev=cards[cid]; dedup=[];seen=set()
  for o in fetched[cid]:
   k=(o['shop'],o['price'],o['url'])
   if k in seen:
    audit.append({'date':TODAY,'card_id':cid,'card_name':card.get('name',''),'shop':o['shop'],
      'source_page':o['source'],'page':'','price_yen':o['price'],'decision':'DUPLICATE',
      'reason':'Same shop, price and URL repeated on another collection page','url':o['url'],'listing_key':o['listingKey']})
   else:seen.add(k);dedup.append(o)
  prior=prev.get('offers',[]); old_by_key={}
  for o in prior:
   if isinstance(o,dict) and o.get('price'):
    old_by_key[(str(o.get('shop','')),int(o.get('price',0) or 0),str(o.get('url','')))]=o
  newkeys={(o['shop'],int(o['price']),o['url']) for o in dedup}
  combined=[{'shop':o['shop'],'source':o['source'],'price':int(o['price']),'url':o['url'],
    'updated':TODAY,'observedDate':TODAY,'stale':False,'type':'asking','listingKey':o['listingKey']} for o in dedup]
  for k,o in old_by_key.items():
   if k not in newkeys:
    stale=dict(o);stale['stale']=True;combined.append(stale)
  prev['offers']=combined
  # Save one dated median/average/lowest/count for each shop observed today.
  shop_history=prev.get('shopHistory',[])
  if not isinstance(shop_history,list):shop_history=[]
  todays_shops={o['shop'] for o in dedup}
  shop_history=[x for x in shop_history if not (isinstance(x,dict) and x.get('date')==TODAY and x.get('shop') in todays_shops)]
  by_shop={}
  for o in dedup:by_shop.setdefault(o['shop'],[]).append(int(o['price']))
  for shop,vals in sorted(by_shop.items()):
   med=int(round(statistics.median(vals)))
   shop_history.append({'date':TODAY,'shop':shop,'price':med,'median':med,
    'average':int(round(sum(vals)/len(vals))),'cheapest':min(vals),'listingCount':len(vals),'type':'asking'})
  shop_history.sort(key=lambda x:(str(x.get('date','')),str(x.get('shop',''))))
  prev['shopHistory']=shop_history
  est=estimate(dedup)
  if est:
   prev.update({'price':est['cheapest'],'marketValue':est['market'],'average':est['weightedAverage'],
    'weightedAverage':est['weightedAverage'],'confidence':est['confidence'],'listingCount':est['listingCount'],
    'shopCount':est['shopCount'],'updated':TODAY})
  prev['shopLinks']=[{'shop':k,'url':v} for k,v in SHOP_LINKS.items()]
  prev['shopLinks'].append({'shop':'メルカリ','url':mercari_url(card)})
  mercari_rows=[]; mercari_ok=False
  if mercari_enabled:
   try:
    mercari_rows=fetch_authorized_mercari(card); mercari_ok=True
    print(f"MERCARI API | {cid} | active={sum(x['kind']=='asking' for x in mercari_rows)} | sold={sum(x['kind']=='sold' for x in mercari_rows)}")
    for row in mercari_rows:
     audit.append({'date':TODAY,'card_id':cid,'card_name':card.get('name',''),
      'shop':'Mercari authorized API','source_page':'Mercari API','page':'',
      'price_yen':row['price'],'decision':'MERCARI_ASKING' if row['kind']=='asking' else 'MERCARI_SOLD',
      'reason':'Provider explicitly classified record as active asking price' if row['kind']=='asking' else 'Provider explicitly classified record as completed sale',
      'url':row['url'],'listing_key':row['listingId']})
    successful.add('Mercari authorized API')
   except Exception as e:
    print('WARN Mercari authorized API',cid,str(e)[:250])
    audit.append({'date':TODAY,'card_id':cid,'card_name':card.get('name',''),
     'shop':'Mercari authorized API','source_page':'Mercari API','page':'','price_yen':'',
     'decision':'MERCARI_FETCH_FAILED','reason':str(e)[:300],'url':mercari_url(card),'listing_key':''})
   time.sleep(.15)
  update_mercari_record(prev,card,mercari_rows,mercari_ok)
  hist=prev.get('history',[])
  if not isinstance(hist,list):hist=[]
  hist=[x for x in hist if not (isinstance(x,dict) and x.get('date')==TODAY)]
  if est:hist.append({'date':TODAY,'market':est['market'],'average':est['weightedAverage'],
    'cheapest':est['cheapest'],'listingCount':est['listingCount'],'shopCount':est['shopCount'],
    'confidence':est['confidence']})
  hist.sort(key=lambda x:str(x.get('date','')));prev['history']=hist;cards[cid]=prev
 fields=['date','card_id','card_name','shop','source_page','page','price_yen','decision','reason','url','listing_key']
 with AUDIT.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(audit)
 out={**{k:v for k,v in old.items() if k not in {'cards','updated','sourcesFetchedToday'}},
  'updated':TODAY,'cards':cards,'version':6,'v6':2,
  'historyPolicy':'One daily market snapshot per observed day; per-shop daily medians are stored in each card shopHistory. Missing days remain missing.',
  'pricePolicy':'Market value is a recency-weighted median of current asking-price observations from existing shops; Mercari completed-sale prices are stored separately and never mixed into asking-price estimates.',
  'sourcesFetchedToday':sorted(successful),
  'mercariNote':'Mercari data is fetched only from MERCARI_AUTHORIZED_API_URL when a separately authorized HTTPS API and token are configured. Active asking prices and explicitly completed sales are stored separately. No Mercari website scraping or access-control bypass is performed.'}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print('Updated',OUT,'cards=',len(cards),'observations=',sum(len(v) for v in fetched.values()))
 print('Sources with observations:',', '.join(sorted(successful)) or '(none)')
 print('Audit:',AUDIT,dict(Counter(x['decision'] for x in audit)))

if __name__=='__main__':main()
