"""Read-only Futu adapter executed in a bounded subprocess; no trading context."""
import contextlib,json,math,os,socket,sys
from datetime import datetime,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from .engine import close_at,select_strikes
from .models import StudyInput

class GatewayError(Exception):
    def __init__(self,code,message):self.code=code;self.message=message

def number(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>=0 else None
    except (TypeError,ValueError):return None

def update_time(value):
    try:
        dt=datetime.fromisoformat(str(value))
        if dt.tzinfo is None:dt=dt.replace(tzinfo=ZoneInfo('America/New_York'))
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError):return None

def next_expiry(dates,at):
    for day in sorted(set(str(d) for d in dates)):
        if day<at.astimezone(ZoneInfo('America/New_York')).date().isoformat():continue
        close=close_at(day)
        if close and close>at:return day
    return None

def unwrap(result,operation):
    ret,data=result
    if ret!=0:
        error=str(data).lower()
        if any(v in error for v in ('permission','权限','right')):
            raise GatewayError('QUOTE_PERMISSION',f'富途未授权{operation}。请检查 OpenD 的美股/期权 API 行情权限；App 行情权限可能不同。')
        if any(v in error for v in ('频率','频繁','frequency','too frequent','rate limit')):
            raise GatewayError('RATE_LIMIT',f'{operation}请求达到富途限频，请稍后重试。')
        raise GatewayError('FUTU_REJECTED',f'富途未返回{operation}，请检查 OpenD 登录、该市场行情权限或接口限频。')
    return data.to_dict('records')

def map_leg(meta,quote,symbol,expiry):
    if quote is None:return None
    last=number(quote.get('last_price'));bid=number(quote.get('bid_price'));ask=number(quote.get('ask_price'))
    if last==0:last=None
    if number(quote.get('bid_vol')) in (None,0):bid=None
    if number(quote.get('ask_vol')) in (None,0):ask=None
    size=number(quote.get('option_contract_size')) or number(meta.get('lot_size'))
    if not size:return None
    return dict(code=meta['code'],last=last,bid=bid,ask=ask,updatedAt=update_time(quote.get('update_time')),
                # Snapshot update_time is NOT an independent last-trade or book event timestamp.
                lastAt=None,bookAt=None,underlying=meta.get('stock_owner') or symbol,
                expiration=str(meta.get('strike_time',expiry)),currency='USD',multiplier=size,exercise='UNKNOWN',
                adjusted=str(meta.get('option_standard_type','UNKNOWN'))!='STANDARD')

def open_context():
    host=os.environ.get('FUTU_OPEND_HOST','127.0.0.1');port=int(os.environ.get('FUTU_OPEND_PORT','11111'))
    if host not in ('127.0.0.1','localhost','::1'):raise GatewayError('LOCAL_ONLY','此版本仅连接本机 OpenD。')
    try:
        with socket.create_connection((host,port),timeout=.5):pass
    except OSError:raise GatewayError('OPEND_OFFLINE','未连接到本机 OpenD。请启动并登录 OpenD，再获取报价。')
    try:from futu import OpenQuoteContext
    except ImportError:raise GatewayError('SDK_MISSING','尚未安装富途 SDK；请运行安装富途支持脚本。')
    return OpenQuoteContext(host=host,port=port)

def connection_info(ctx):
    ret,raw=ctx.get_global_state()
    if ret!=0 or not isinstance(raw,dict):raise GatewayError('OPEND_STATUS','OpenD 未返回行情连接状态，请检查登录。')
    info=dict(connected=True,quoteLoggedIn=bool(raw.get('qot_logined')),marketUS=str(raw.get('market_us','UNKNOWN')),
              serverVersion=str(raw.get('server_ver','UNKNOWN')),checkedAt=datetime.now(timezone.utc).isoformat(),
              stockRight='UNKNOWN',optionRight='UNKNOWN')
    if info['quoteLoggedIn']:
        try:
            # Request only quote rights. Never return account/profile/credential fields.
            ret,rights=ctx.get_user_info(info_field=[4])
            if ret==0 and isinstance(rights,dict):
                info['stockRight']=str(rights.get('us_qot_right','UNKNOWN'))
                info['optionRight']=str(rights.get('us_option_qot_right','UNKNOWN'))
        except Exception:pass
    return info

def diagnose():
    ctx=open_context()
    try:return connection_info(ctx)
    finally:ctx.close()

def stock_anchor(stock,market):
    # Snapshot Last may remain the regular-session close outside regular hours.
    if market in ('PRE_MARKET_BEGIN','PRE_MARKET_END'):
        field,label='pre_price','盘前成交价'
    elif market in ('AFTER_HOURS_BEGIN','AFTER_HOURS_END'):
        field,label='after_price','盘后成交价'
    elif market in ('NIGHT_OPEN','NIGHT_END'):
        field,label='overnight_price','夜盘成交价'
    else:
        field,label='last_price',('常规成交价' if market in ('MORNING','AFTERNOON') else '最近常规成交价')
    price=number(stock.get(field))
    if not price:raise GatewayError('NO_ANCHOR',f'缺少有效正股{label}，无法选档；未以其他时段价格替代。')
    return price,dict(field=field,label=label,price=price,regularLast=number(stock.get('last_price')))

def collect(symbol):
    code='US.'+symbol.removeprefix('US.').upper()
    ctx=open_context()
    try:
        connection=connection_info(ctx)
        if not connection['quoteLoggedIn']:raise GatewayError('OPEND_LOGIN','OpenD 已运行，但行情未登录。请在 OpenD 完成登录。')
        stock_rows=unwrap(ctx.get_market_snapshot([code]),'正股快照')
        if len(stock_rows)!=1:raise GatewayError('NO_STOCK','正股报价缺失。')
        stock=stock_rows[0];spot,anchor=stock_anchor(stock,connection['marketUS'])
        at=datetime.now(timezone.utc)
        dates=unwrap(ctx.get_option_expiration_date(code=code),'期权到期日')
        expiry=next_expiry([r['strike_time'] for r in dates],at)
        if not expiry:raise GatewayError('NO_EXPIRY','没有最近有效到期日。')
        catalog=unwrap(ctx.get_option_chain(code=code,start=expiry,end=expiry),'期权链')
        catalog=[r for r in catalog if str(r.get('strike_time'))==expiry and r.get('stock_owner')==code and r.get('option_type') in ('CALL','PUT') and number(r.get('strike_price')) not in (None,0)]
        keys,center=select_strikes([r['strike_price'] for r in catalog],spot)
        chosen=[r for r in catalog if Decimal(str(r['strike_price'])) in keys]
        if not chosen:raise GatewayError('EMPTY_CHAIN','该到期日期权链为空。')
        quotes={r['code']:r for r in unwrap(ctx.get_market_snapshot(list({r['code'] for r in chosen})),'期权快照')}
        rows=[]
        for k in keys:
            row=dict(strike=str(k),call=None,put=None,issues=[])
            for right,key in [('CALL','call'),('PUT','put')]:
                matches=[r for r in chosen if Decimal(str(r['strike_price']))==k and r['option_type']==right]
                if len(matches)!=1:row['issues'].append('合约缺失或存在多个同档合约');continue
                meta=matches[0];quote=quotes.get(meta['code'])
                if quote:
                    if not quote.get('option_valid') or str(quote.get('strike_time'))!=expiry or number(quote.get('option_strike_price'))!=float(k) or quote.get('option_type')!=right:
                        row['issues'].append('期权快照合约元数据不匹配');continue
                row[key]=map_leg(meta,quote,code,expiry)
            rows.append(row)
        # Original stock anchor retained if spot moves during collection; never silently reselect using a new price.
        study=StudyInput(symbol=code.removeprefix('US.'),spot=spot,expiration=expiry,valuationAt=datetime.now(timezone.utc),
                         timeRule='calendar',rate=.05,rows=rows)
        return dict(input=study.model_dump(mode='json'),connection=connection,stockAnchor=anchor,stockUpdatedAt=update_time(stock.get('update_time')),
                    receivedAt=datetime.now(timezone.utc).isoformat(),notes=['富途 API 快照；权限状态随本次读取记录，权限不等于每笔报价新鲜或同步。',
                    f'正股锚点采用本轮首次快照的{anchor["label"]}。期权快照更新时刻不是独立成交/盘口事件时间，无法证明同步。',
                    '最近到期日按 XNYS 常规收盘筛选，作为研究约定，不等同期权实际截止时刻。'])
    finally:ctx.close()

if __name__=='__main__':
    try:
        with contextlib.redirect_stdout(sys.stderr):result=diagnose() if sys.argv[1]=='--status' else collect(sys.argv[1])
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except GatewayError as e:print(json.dumps(dict(error=e.code,detail=e.message),ensure_ascii=False))
    except Exception:print(json.dumps(dict(error='FUTU_UNAVAILABLE',detail='富途行情读取失败，请检查本机 OpenD 和 SDK。'),ensure_ascii=False))
