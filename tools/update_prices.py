#!/usr/bin/env python3
# See the accompanying response for the intended pricing policy.
# This file is a safe updater foundation; all current observations are asking prices.
import csv, html, json, re, statistics, time
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'data/catalog.json'
OUT=ROOT/'data/prices.json'
AUDIT=ROOT/'data/price_audit.csv'
TODAY=date.today().isoformat()
UA='Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/4.0)'
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

def parse_listings(page_html,src,url,known_ids):
 text=visible_text(page_html); ids=list(ID_RE.finditer(text)); found=[]
 for i,m in enumerate(ids):
  cid=m.group(1)
  if cid not in known_ids:continue
  # First inspect the text following this product ID up to the next ID. If the
  # site places price before the title, inspect the immediately preceding
  # product segment instead. Never let the segment cross another card ID.
  next_start=ids[i+1].start() if i+1<len(ids) else min(len(text),m.end()+320)
  segment=text[m.end():next_start]
  vals=[]
  for pm in PRICE_RE.finditer(segment):
   raw=pm.group(1) or pm.group(2)
   try:v=int(raw.replace(',',''))
   except (ValueError,AttributeError):continue
   if 1<=v<=1000000:vals.append(v)
  if not vals:
   prev_end=ids[i-1].end() if i>0 else max(0,m.start()-180)
   segment=text[prev_end:m.start()]
   for pm in PRICE_RE.finditer(segment):
    raw=pm.group(1) or pm.group(2)
    try:v=int(raw.replace(',',''))
    except (ValueError,AttributeError):continue
    if 1<=v<=1000000:vals.append(v)
  segment_for_exclusions=text[max(0,m.start()-120):min(len(text),next_start+120)]
  if any(x.lower() in segment_for_exclusions.lower() for x in EXCLUDED):continue
  vals=list(dict.fromkeys(vals))
  # Ambiguous segments are omitted rather than assigned a possibly unrelated price.
  if len(vals)!=1:continue
  found.append({'card_id':cid,'shop':src['shop'],'source':src['name'],'price':vals[0],
                'url':url,'date':TODAY,'type':'asking',
                'listingKey':f"{src['shop']}|{cid}|{vals[0]}|{url}|{i}"})
 # De-duplicate repeated occurrences in the same fetched page.
 out=[];seen=set()
 for o in found:
  k=(o['card_id'],o['shop'],o['price'],o['url'],o['listingKey'])
  if k not in seen:seen.add(k);out.append(o)
 return out

def mercari_url(card):
 q=' '.join(x for x in [card['id'],card.get('name',''),str(card.get('rarity','')).replace('*','')] if x)
 return 'https://jp.mercari.com/search?'+urlencode({'keyword':q})

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
 for src in SOURCES:
  for page in src['pages']:
   url=page_url(src,page)
   try:
    obs=parse_listings(get(url),src,url,card_by_id)
    print(f"PRICE DEBUG | {src['name']} | page {page} | extracted {len(obs)} listing observations")
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
  prev['mercari']={'status':'manual-search','median':None,'samples':[],'url':mercari_url(card),'updated':TODAY}
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
  'pricePolicy':'Market value is a recency-weighted median of current asking-price observations; total influence is balanced by shop. No completed-sale prices are invented.',
  'sourcesFetchedToday':sorted(successful),
  'mercariNote':'Mercari is a manual search link only; no automated scraping or invented sold prices.'}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print('Updated',OUT,'cards=',len(cards),'observations=',sum(len(v) for v in fetched.values()))
 print('Sources with observations:',', '.join(sorted(successful)) or '(none)')
 print('Audit:',AUDIT,dict(Counter(x['decision'] for x in audit)))

if __name__=='__main__':main()
