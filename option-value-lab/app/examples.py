"""User-provided examples. Never manufacture raw quotes for the seven-value example."""
from .models import StudyInput
from .engine import VERSION,stats

def goog():
    rows=[]
    for k,c,p in [(275,7.59,3.67),(277.5,5.85,4.72),(280,4.66,6.13)]:
        rows.append(dict(strike=k,call=dict(last=c),put=dict(last=p)))
    return StudyInput(symbol='GOOG',spot=None,expiration='2026-11-06',valuationAt='2026-10-30T16:00:00-04:00',
                      rate=.05,timeRule='days',remainingDays=7,rows=rows)

def seven():
    strikes=['123','124.5','127','129.5','133','134','135']
    values=[129.33,130.11,131.06,129.18,128.98,130.51,131.02]
    rows=[dict(strike=k,call=None,put=None,implied=v,qualityPassed=False,issues=['需求中的示意理论价，无原始期权报价'],callQuote=None,putQuote=None,discountedStrike=None) for k,v in zip(strikes,values)]
    summary=stats(values)|dict(qualityCount=0,gapBps=None)
    return dict(modelVersion=VERSION,symbol='X',spot=130.89,expiration=None,valuationAt=None,pricingAt=None,rate=None,T=None,
                center='129.5',selectedStrikes=strikes,candidateCount=7,timeNote='此例只验证选档与中位数，未提供期限或原始期权报价。',
                modes={'LAST':dict(summary=summary,rows=rows),'MID':dict(summary=stats([])|dict(qualityCount=0,gapBps=None),rows=[])},
                notes=['七个理论价直接来自用户需求示例，不是实时行情，也不是程序补造的期权报价。',
                       '没有原始 Call / Put，不能重算 MID，也不能进行三档 Call 相对价格比较。'])
