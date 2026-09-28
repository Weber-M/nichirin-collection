#!/usr/bin/env python3
"""Update price data for the Nichirin Battle Slash PWA.

Sources are public shop collection pages. The script stores a reference price
and source URL per card; it does not attempt to bypass login, bot protection,
or other access controls.
"""
import json,re,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urljoin

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'data/catalog.json'
OUT=ROOT/'data/prices.json'
UA='Mozilla/5.0 (compatible; NichirinCollectionPriceUpdater/1.0)'

SOURCES=[
 {'name':'FullAhead','base':'https://pt-fullahead.com/shopbrand/kynbs/','pages':[1,2,3,4,5]},
 {'name':'TCG Library A01','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01','pages':[1,2]},
 {'name':'TCG Library all','base':'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash','pages':[1,2,3]},
]

def get(url):
    req=Request(url,headers={'User-Agent':UA,'Accept-Language':'ja,en;q=0.8'})
    with urlopen(req,timeout=20) as r:
        return r.read().decode('utf-8','ignore')

def parse_cards(html):
    # Works with common shop HTML/text representations: A01-001 ... 1,280円
    out={}
    pat=re.compile(r'((?:A|P|T)\d{2}-\d{3})[^\d¥円]{0,180}?(?:¥|)([0-9][0-9,]*)\s*円')
    for m in pat.finditer(re.sub(r'<[^>]+>',' ',html)):
        cid=m.group(1); price=int(m.group(2).replace(',',''))
        out.setdefault(cid,price)
    return out

def shop_url(source,cid):
    if source=='FullAhead': return 'https://pt-fullahead.com/shopbrand/kynbs/'
    if source=='TCG Library A01': return 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash-a01'
    return 'https://tcg-library.com/collections/kimetsu-nichirin-battle-slash'

def main():
    catalog=json.loads(CATALOG.read_text(encoding='utf-8'))
    cards={c['id']:{} for c in catalog['cards']}
    for src in SOURCES:
        for page in src['pages']:
            if src['name']=='FullAhead':
                url=src['base'] if page==1 else f"{src['base']}page{page}/order/"
            else:
                url=src['base'] if page==1 else f"{src['base']}?page={page}"
            try:
                prices=parse_cards(get(url))
            except Exception as e:
                print(f'WARN {src["name"]} page {page}: {e}')
                continue
            for cid,price in prices.items():
                if cid in cards:
                    cards[cid].setdefault('offers',[]).append({'shop':src['name'],'price':price,'url':shop_url(src['name'],cid)})
            time.sleep(0.8)
    # Preserve existing prices where the source is temporarily unavailable.
    if OUT.exists():
        old=json.loads(OUT.read_text(encoding='utf-8')).get('cards',{})
    else: old={}
    for cid in cards:
        offers=cards[cid].get('offers',[])
        if not offers and cid in old:
            cards[cid]=old[cid]
        elif offers:
            cards[cid]['price']=min(x['price'] for x in offers)
            cards[cid]['updated']=time.strftime('%Y-%m-%d')
            cards[cid]['sources']=offers
    OUT.write_text(json.dumps({'updated':time.strftime('%Y-%m-%d'),'cards':cards},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Updated',OUT)

if __name__=='__main__': main()
