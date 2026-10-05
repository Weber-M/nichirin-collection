#!/usr/bin/env python3
"""Fetch the official Nichirin Battle Slash news index into data/news.json."""
import json,re,time
from pathlib import Path
from urllib.request import Request,urlopen
from html import unescape
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/news.json'
URL='https://p.eagate.573.jp/game/kimetsu/bslash/news/index.html'
UA='Mozilla/5.0 (compatible; NichirinCollectionNewsUpdater/1.0)'
def get():
 r=Request(URL,headers={'User-Agent':UA,'Accept-Language':'ja,en;q=0.8'})
 with urlopen(r,timeout=25) as x:return x.read().decode('utf-8','ignore')
def clean(s):
 s=re.sub(r'<script[\\s\\S]*?</script>',' ',s,flags=re.I)
 s=re.sub(r'<style[\\s\\S]*?</style>',' ',s,flags=re.I)
 return re.sub(r'\\s+',' ',re.sub(r'<[^>]+>',' ',s)).strip()
def main():
 html=get()
 items=[]
 # Official list uses dated news links. Capture href/title around dates.
 pat=re.compile(r'href=["\']([^"\']*?/news/2026/[^"\']+\.html)["\'][^>]*>(.*?)</a>',re.I|re.S)
 seen=set()
 for href,title in pat.findall(html):
  title=clean(unescape(title))
  m=re.search(r'(2026\.\d{2}\.\d{2})',title)
  if not m: continue
  date=m.group(1)
  title=re.sub(r'^2026\.\d{2}\.\d{2}\s*','',title).strip()
  if href.startswith('/'): href='https://p.eagate.573.jp'+href
  if href.startswith('./'): href='https://p.eagate.573.jp/game/kimetsu/bslash/news/'+href[2:]
  key=(date,href,title)
  if key not in seen:
   seen.add(key); items.append({'date':date,'title':title,'url':href})
 items.sort(key=lambda x:x['date'],reverse=True)
 OUT.write_text(json.dumps({'updated':time.strftime('%Y-%m-%d'),'source':URL,'items':items[:20]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print('News updated:',len(items))
if __name__=='__main__':main()
