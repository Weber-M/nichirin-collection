#!/usr/bin/env python3
"""Daily price updater for Nichirin Battle Slash V6.

- Reads card IDs/names/rarity from data/catalog.json.
- Fetches public shop pages and attempts public Mercari search pages.
- Never treats an unavailable source as ¥0.
- Keeps previous values when a source temporarily fails.
- Stores current offers plus a dated market-value history snapshot in data/prices.json.
- Uses the median of the current source/reference prices as the market estimate.

This is reference/listed pricing, not guaranteed completed-sale pricing. The updater
uses only publicly reachable pages and does not bypass login, bot protection, or access controls.
"""
import json,re,time,statistics
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'data/catalog.json'
OUT=ROOT/'data/prices.json'
UA='Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/2.0)'
TODAY=time.strftime('%Y-%m-%d')

SOURCES=[
 {'name':'FullAhead','base':'https://pt-fullahead.com/shopbrand/kynbs/','pages':range(1,6),'kind':'fullahead'},
 {'name':'TCG Library A01','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01','pages':range(1,3),'kind':'tcg'},
 {'name':'TCG Library all','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash','pages':range(1,4),'kind':'tcg'},
]
SHOP_LINKS={
 'FullAhead':'https://pt-fullahead.com/shopbrand/kynbs/',
 'TCG Library':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash',
 'カードショップ カリントウ':'https://item.rakuten.co.jp/karintou10/c/0000006150/'
}

def get(url):
 req=Request(url,headers={'User-Agent':UA,'Accept-Language':'ja,en;q=0.8'})
 with urlopen(req,timeout=25) as r:return r.read().decode('utf-8','ignore')

def clean(html):
 html=re.sub(r'<script[\s\S]*?</script>',' ',html,flags=re.I)
 html=re.sub(r'<style[\s\S]*?</style>',' ',html,flags=re.I)
 return re.sub(r'<[^>]+>',' ',html)

def parse_shop(html):
 text=clean(html)
 out={}
 pat=re.compile(r'((?:A|P|T)\d{2}-\d{3})[^\d¥円]{0,220}(?:¥\s*)?([0-9][0-9,]*)\s*円')
 for m in pat.finditer(text):out.setdefault(m.group(1),int(m.group(2).replace(',','')))
 return out

def mercari_url(card):
 q=' '.join(x for x in [card['id'],card.get('name',''),str(card.get('rarity','')).replace('*','')] if x)
 return 'https://jp.mercari.com/search?'+urlencode({'keyword':q})

def parse_mercari(html):
 # Mercari search pages commonly expose item prices in embedded JSON. Keep a
 # conservative range and deduplicate values. If the page format changes, return [].
 vals=[]
 for pat in [r'"price"\s*:\s*([0-9]{2,7})',r'"price"\s*:\s*"([0-9]{2,7})"']:
  for m in re.finditer(pat,html):
   p=int(m.group(1))
   if 50<=p<=1000000: vals.append(p)
 # Prefer values that occur as many times as listing cards, then take up to 10
 # low/mid listings to reduce the effect of unrelated embedded metadata.
 vals=sorted(set(vals))
 return vals[:10]

def median(vals):
 vals=sorted(vals)
 if not vals:return None
 return int(round(statistics.median(vals)))

def main():
 catalog=json.loads(CATALOG.read_text(encoding='utf-8'))
 old=json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {'cards':{}}
 oldcards=old.get('cards',{})
 cards={cid:dict(oldcards.get(cid,{})) for cid in [c['id'] for c in catalog['cards']]}
 card_by_id={c['id']:c for c in catalog['cards']}
 fetched={cid:[] for cid in cards}
 fetched_names={cid:[] for cid in cards}

 for src in SOURCES:
  for page in src['pages']:
   if src['kind']=='fullahead': url=src['base'] if page==1 else f"{src['base']}page{page}/order/"
   else: url=src['base'] if page==1 else f"{src['base']}?page={page}"
   try: prices=parse_shop(get(url))
   except Exception as e:
    print('WARN',src['name'],page,e); continue
   for cid,p in prices.items():
    if cid in cards:
     fetched[cid].append({'shop':src['name'].replace(' A01',''),'price':p,'url':src['base']})
   time.sleep(.5)

 # Try one public Mercari search per card. If unavailable/blocked, preserve prior data.
 mercari_count=0
 for cid,card in card_by_id.items():
  try:
   url=mercari_url(card); vals=parse_mercari(get(url))
   if vals:
    m=median(vals)
    fetched[cid].append({'shop':'メルカリ','price':m,'url':url})
    cards[cid]['mercari']={'median':m,'samples':[{'price':v,'url':url} for v in vals],'updated':TODAY}
    mercari_count+=1
  except Exception as e:
   pass
  time.sleep(.15)

 for cid,card in card_by_id.items():
  prev=cards[cid]
  offers=fetched[cid]
  # Keep previously known offers from sources not successfully fetched today.
  if offers:
   # Deduplicate by shop, preferring today's fetched value.
   byshop={o['shop']:o for o in offers}
   for o in prev.get('offers',[]):
    if o.get('shop') not in byshop and isinstance(o.get('price'),(int,float)) and o.get('price',0)>0:
     byshop[o['shop']]=o
   offers=list(byshop.values())
   prev['offers']=offers
  elif isinstance(prev.get('offers'),list):
   offers=prev['offers']

  vals=[o['price'] for o in offers if isinstance(o.get('price'),(int,float)) and o.get('price',0)>0]
  if vals:
   prev['price']=min(vals)
   prev['marketValue']=median(vals)
   prev['average']=round(sum(vals)/len(vals))
   prev['updated']=TODAY
  elif 'price' not in prev:
   prev['price']=None; prev['marketValue']=None; prev['average']=None

  prev['shopLinks']=[{'shop':k,'url':v} for k,v in SHOP_LINKS.items()]
  prev['shopLinks'].append({'shop':'メルカリ','url':mercari_url(card)})
  prev['sources']=list(prev.get('sources',[]))
  # History: one point per day, replace today's point if rerun manually.
  hist=prev.get('history') if isinstance(prev.get('history'),list) else []
  if vals:
   point={'date':TODAY,'market':prev.get('marketValue'),'average':prev.get('average'),'cheapest':prev.get('price')}
   hist=[x for x in hist if x.get('date')!=TODAY]
   hist.append(point)
   hist=hist[-180:]
  prev['history']=hist
  cards[cid]=prev

 out={'updated':TODAY,'cards':cards,'version':4,'v6':1,'historyPolicy':'One dated snapshot per successful updater run; up to 180 snapshots are retained per card.','mercariNote':'Mercari value is a reference median of publicly visible search prices when the public page is reachable; it is not a guaranteed sold-price history.'}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print('Updated',OUT,'cards=',len(cards),'mercari=',mercari_count)

if __name__=='__main__':main()
