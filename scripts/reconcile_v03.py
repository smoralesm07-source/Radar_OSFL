from pathlib import Path
import json,pandas as pd
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'docs/data'
def rd(n):
 p=D/n; return pd.read_csv(p,dtype=str).fillna('') if p.exists() else pd.DataFrame()
def main():
 c=rd('osfl_universe_core.csv'); l=rd('law21440_registry.csv'); r=rd('registro19862_osfl_like.csv')
 if c.empty: raise SystemExit('falta core')
 a=l[(l.entity_id!='')&(l.registry_status.str.upper()=='INSCRITA')] if not l.empty else pd.DataFrame(columns=['entity_id','rut','external_name'])
 rr=r[r.entity_id!=''] if not r.empty else pd.DataFrame(columns=['entity_id'])
 li=set(a.entity_id); ri=set(rr.entity_id); ci=set(c.entity_id)
 o=c.copy(); o['origin_v03']='SII_CORE'; o['law21440_active']=o.entity_id.isin(li); o['registro19862']=o.entity_id.isin(ri); o['confirmation_status_v03']='CORE_SII_ONLY'; o.loc[o.registro19862,'confirmation_status_v03']='CORROBORATED_MULTI_SOURCE'; o.loc[o.law21440_active,'confirmation_status_v03']='LEGALLY_CORROBORATED_MULTI_SOURCE'
 n=a[~a.entity_id.isin(ci)].drop_duplicates('entity_id')
 if not n.empty:
  x=pd.DataFrame({'entity_id':n.entity_id,'rut':n.rut,'legal_name':n.external_name,'origin_v03':'EXTERNAL_REGISTRY','law21440_active':True,'registro19862':n.entity_id.isin(ri),'confirmation_status_v03':'EXTERNAL_CONFIRMED_LAW21440'}); o=pd.concat([o,x],ignore_index=True,sort=False).fillna(''); n.to_csv(D/'external_new_entities.csv',index=False)
 o.to_csv(D/'osfl_universe_v03.csv',index=False)
 s={'version':'0.3.0','status':'MULTI_REGISTRY_RECONCILED','sii_core_entities':int(c.entity_id.nunique()),'expanded_universe_entities':int(o.entity_id.nunique()),'new_external_entities':int(len(n)),'core_law21440':int((c.entity_id.isin(li)).sum()),'core_registro19862':int((c.entity_id.isin(ri)).sum()),'catastro_oip':'PENDING_DYNAMIC_ADAPTER','mdsf19885':'PENDING_NAME_ONLY_ADAPTER','failure_is_zero':False}
 (D/'reconciliation_summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8'); print(s)
if __name__=='__main__': main()
