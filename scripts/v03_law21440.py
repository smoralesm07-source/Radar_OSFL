from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re, sys
import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from radar_osfl.engine import normalize_rut,entity_id,normalize_name,utc_now

URL='https://donacionesley21440.gob.cl/registro-publico'
H={'User-Agent':'Radar-OSFL/0.3 public OSINT Chile'}
RUT_RE=re.compile(r'\b(\d{1,2}(?:\.\d{3}){2}-[0-9Kk]|\d{7,8}-[0-9Kk])\b')
DETAIL_TIMEOUT=3
DETAIL_WORKERS=20

def fetch_detail(item):
    try:
        r=requests.get(item['source_url'],headers=H,timeout=DETAIL_TIMEOUT); r.raise_for_status()
        text=' '.join(BeautifulSoup(r.text,'lxml').stripped_strings)
        m=RUT_RE.search(text); rut=normalize_rut(m.group(1)) if m else None
        item['rut']=rut; item['entity_id']=entity_id(rut) if rut else None
        if not rut: item['error']='RUT_NOT_FOUND_OR_INVALID'
        return item
    except Exception as e:
        item['error']=f'{type(e).__name__}: {e}'; item['rut']=None; item['entity_id']=None; return item

def main():
    r=requests.get(URL,headers=H,timeout=20); r.raise_for_status(); soup=BeautifulSoup(r.text,'lxml')
    items=[]; seen=set()
    for a in soup.find_all('a',href=True):
        m=re.search(r'registro-publico\?n=(\d+)',a['href'])
        if not m or m.group(1) in seen: continue
        seen.add(m.group(1)); tr=a.find_parent('tr'); txt=tr.get_text(' ',strip=True) if tr else ''
        state='Eliminada' if 'ELIMINADA' in normalize_name(txt) else 'Inscrita' if 'INSCRITA' in normalize_name(txt) else ''
        items.append({'source_id':'donatarias_21440','external_id':m.group(1),'external_name':a.get_text(' ',strip=True),'external_name_norm':normalize_name(a.get_text(' ',strip=True)),'registry_status':state,'source_url':f'{URL}?n={m.group(1)}','match_method':'RUT_DIRECT','evidence_strength':'STRONG_LEGAL_REGISTRY'})
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as ex: rows=list(ex.map(fetch_detail,items))
    df=pd.DataFrame(rows); out=ROOT/'docs/data'; out.mkdir(parents=True,exist_ok=True)
    df.to_csv(out/'law21440_registry.csv',index=False)
    valid=df[df.entity_id.notna()]
    summary={'source_id':'donatarias_21440','status':'CURRENT' if len(valid) else 'FAILED','listed':len(df),'valid_rut':len(valid),'active_valid_rut':int((valid.registry_status.str.lower()=='inscrita').sum()),'failed_details':int(df.entity_id.isna().sum()),'detail_timeout_seconds':DETAIL_TIMEOUT,'detail_workers':DETAIL_WORKERS,'generated_at':utc_now(),'failure_is_zero':False}
    (out/'law21440_summary.json').write_text(__import__('json').dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if valid.empty: raise SystemExit('Ley 21.440 no produjo RUT válidos; no se publica cero')
    print(summary)
if __name__=='__main__': main()
