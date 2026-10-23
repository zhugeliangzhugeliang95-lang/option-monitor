import csv, io, json
from datetime import datetime
from .models import Snapshot, Quote, Contract, Leg

def parse_import(content,fmt,monitor_id,cfg,metadata):
    if fmt=='json':
        obj=json.loads(content); rows=obj if isinstance(obj,list) else [obj]
        return [Snapshot.model_validate({**r,'monitorId':monitor_id,'provider':'manual','historical':True}) for r in rows]
    reader=csv.DictReader(io.StringIO(content),delimiter='\t' if fmt=='tsv' else ',')
    rows=list(reader)
    if not rows: raise ValueError('导入文件没有报价行')
    at=metadata.get('valuationAt'); expiry=metadata.get('expiration') or cfg.expiration
    if not at or not expiry: raise ValueError('CSV/TSV 需要估值时间和到期日元数据')
    def quote(prefix,row):
        values={k:float(row[prefix+k]) if row.get(prefix+k,'').strip() else None for k in ('bid','ask','last','reportedMid')}
        times={k+'At':row.get(prefix+k+'At') or None for k in ('bid','ask','last','reportedMid')}
        return Quote(**values,**times,source='manual-import',feedType='MANUAL')
    legs=[]; contracts=[]
    for row in rows:
        strike=row.get('strike')
        if not strike: raise ValueError('缺少 strike 列')
        for right,prefix in [('C','call_'),('P','put_')]:
            c=Contract(id=f'{cfg.underlyingId}:{expiry}:{strike}:{right}',underlyingId=cfg.underlyingId,expiration=expiry,strike=strike,right=right,currency=cfg.currency,
                pricingAt=metadata.get('pricingAt'),lastTradingAt=metadata.get('lastTradingAt'),exerciseCutoffAt=metadata.get('exerciseCutoffAt'))
            contracts.append(c); legs.append(Leg(contract=c,quote=quote(prefix,row)))
    return [Snapshot(monitorId=monitor_id,valuationAt=at,provider='manual',underlying=Quote.model_validate(metadata['underlying']),contracts=contracts,legs=legs,historical=True,marketReason='MANUAL_HISTORY',marketOpen=metadata.get('marketOpen',False),optionSessionOpen=metadata.get('optionSessionOpen',False))]
