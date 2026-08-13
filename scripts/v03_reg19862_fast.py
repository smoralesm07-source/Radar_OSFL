from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re,json,sys
import pandas as pd, requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from radar_osfl.engine import normalize_rut,normalize_name,entity_id,utc_now
T={'2':'Asociaciones','8':'Corporaciones','9':'Fundaciones','12':'ONG','10':'Iglesias','3':'Bomberos'}; H={'User-Agent':'Radar-OSFL/0.3'}
def html(u):
 r=requests.get(u,headers=H,timeout=30); r.raise_for_status(); return BeautifulSoup(r.text,'lxml')
def total(t):
 s=' '.join(html(f'https://registros19862.gob.cl/buscar/0//{t}').stripped_strings); m=re.search(r'(\d[\d\.\s]*)\s+instituciones',s,re.I); return int(re.sub(r'\D','',m.group(1))) if m else 100
def page(job):
 t,label,o=job; s=html(f'https://registros19862.gob.cl/buscar/0//{t}///{o}'); out=[]
 for a in s.find_all('a',href=True):
  m=re.search(r'/institucion/(\d{7,9})/ficha',a['href']); name=a.get_text(' ',strip=True)
  if not m or not name: continue
  card=a.find_parent('li') or a.find_parent('div') or a.parent
  rm=re.search(r'\b(\d{1,2}(?:\.\d{3}){2}-[0-9Kk]|\d{7,8}-[0-9Kk])\b',card.get_text(' ',strip=True) if card else '')
  rut=normalize_rut(rm.group(1)) if rm else None
  if rut: out.append({'source_id':'registro_19862','entity_id':entity_id(rut),'rut':rut,'external_name':name,'external_name_norm':normalize_name(name),'external_type':label,'registry_status':'REGISTERED','match_method':'RUT_DIRECT'})
 return out
def main():
 D=ROOT/'docs/data'; D.mkdir(parents=True,exist_ok=True); jobs=[]; totals={}
 for t,l in T.items():
  n=total(t); totals[l]=n; jobs += [(t,l,o) for o in range(0,n,100)]
 rows=[]
 with ThreadPoolExecutor(max_workers=12) as ex:
  for part in ex.map(page,jobs): rows.extend(part)
 df=pd.DataFrame(rows).drop_duplicates(['entity_id','external_type']); df.to_csv(D/'registro19862_osfl_like.csv',index=False)
 s={'source_id':'registro_19862','status':'CURRENT','rows':len(df),'entities':int(df.entity_id.nunique()),'type_totals':totals,'pages':len(jobs),'generated_at':utc_now(),'failure_is_zero':False}; (D/'registro19862_summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8'); print(s)
if __name__=='__main__': main()
