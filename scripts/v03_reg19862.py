from __future__ import annotations
from pathlib import Path
from urllib.parse import urljoin,urlparse
import re,sys,time,json
import pandas as pd
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from radar_osfl.engine import normalize_name,normalize_rut,rut_dv,entity_id,utc_now
H={'User-Agent':'Radar-OSFL/0.3 public OSINT Chile'}; BASE='https://registros19862.gob.cl/buscar/0/5'
TARGET=('FUNDACION','CORPORACION','ASOCIACION','AGRUPACION','ONG','CLUB','UNION COMUNAL','CENTRO','BOMBER','JUNTA DE VECINOS','IGLESIA','TALLER','COMITE')

def get(url):
 r=requests.get(url,headers=H,timeout=60); r.raise_for_status(); return r

def parse(url,label):
 soup=BeautifulSoup(get(url).text,'lxml'); rows={}; pages=[]
 for a in soup.find_all('a',href=True):
  href=urljoin(url,a['href']); txt=a.get_text(' ',strip=True)
  m=re.search(r'/institucion/(\d{7,9})(?:/|$)',href)
  if m and txt:
   body=m.group(1).lstrip('0'); rut=normalize_rut(f'{body}-{rut_dv(body)}') if body else None
   if rut: rows[entity_id(rut)]={'source_id':'registro_19862','entity_id':entity_id(rut),'rut':rut,'external_name':txt,'external_name_norm':normalize_name(txt),'external_type':label,'registry_status':'REGISTERED','source_url':f'https://registros19862.gob.cl/institucion/{body}/ficha','match_method':'RUT_DIRECT','evidence_strength':'PUBLIC_REGISTRY_CORROBORATION'}
  if txt.isdigit() and '/buscar/' in href: pages.append(href)
 return list(rows.values()),pages

def main():
 try:
  soup=BeautifulSoup(get(BASE).text,'lxml'); starts={}
  for a in soup.find_all('a',href=True):
   txt=a.get_text(' ',strip=True); norm=normalize_name(txt); href=urljoin(BASE,a['href'])
   if '/buscar/0/5/' in href and any(x in norm for x in TARGET): starts[href]=txt
  if not starts: starts={f'{BASE}/9':'Fundaciones',f'{BASE}/8':'Corporaciones',f'{BASE}/1':'Agrupaciones'}
  allrows={}; total_pages=0
  for start,label in starts.items():
   q=[start]; seen=set(); prefix='/'.join(urlparse(start).path.split('/')[:5])
   while q and len(seen)<120:
    u=q.pop(0)
    if u in seen: continue
    seen.add(u)
    try: rows,pages=parse(u,label)
    except Exception: continue
    total_pages+=1
    for x in rows: allrows[(x['entity_id'],label)]=x
    for p in pages:
     if urlparse(p).path.startswith(prefix) and p not in seen and p not in q: q.append(p)
    time.sleep(.02)
  df=pd.DataFrame(allrows.values()); out=ROOT/'docs/data'; out.mkdir(parents=True,exist_ok=True)
  df.to_csv(out/'registro19862_osfl_like.csv',index=False)
  summary={'source_id':'registro_19862','status':'CURRENT' if len(df) else 'FAILED','rows':len(df),'entities':int(df.entity_id.nunique()) if len(df) else 0,'types':len(starts),'pages':total_pages,'generated_at':utc_now(),'failure_is_zero':False}
  (out/'registro19862_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); print(summary)
 except Exception as e:
  summary={'source_id':'registro_19862','status':'FAILED','rows':0,'error':f'{type(e).__name__}: {e}','generated_at':utc_now(),'failure_is_zero':False}; (ROOT/'docs/data/registro19862_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); print(summary)
if __name__=='__main__': main()
